"""

File housing all models.

Each model can be created by invoking the appropriate function
given by:

    make_MODELNAME_model(MODEL_SETTINGS)

Changes to allow this are still in progess
"""


import torch 
import torch.nn as nn
import torch.nn.functional as F
from torchvision import models

import numpy as np
import torchfcn
import fcn

from constants import *
from modelling.baselines import make_rf_model, make_logreg_model, make_1d_nn_model, make_1d_2layer_nn_model, make_1d_cnn_model
from modelling.recurrent_norm import RecurrentNorm2d
from modelling.clstm_cell import ConvLSTMCell
from modelling.clstm import CLSTM
from modelling.cgru_segmenter import CGRUSegmenter
from modelling.clstm_segmenter import CLSTMSegmenter
from modelling.util import initialize_weights, get_num_bands, get_upsampling_weight, get_num_s1_bands, get_num_s2_bands
from modelling.fcn8 import FCN8
from modelling.unet import UNet, UNet_Encode, UNet_Decode
from modelling.unet3d import UNet3D
from modelling.multi_input_clstm import MI_CLSTM


# TODO: figure out how to decompose this       
class FCN_CRNN(nn.Module):
    def __init__(self, fcn_input_size, fcn_model_name, 
                       crnn_input_size, crnn_model_name, 
                       hidden_dims, lstm_kernel_sizes, conv_kernel_size, lstm_num_layers, avg_hidden_states, 
                       num_classes, bidirectional, pretrained, early_feats,
                       use_planet, resize_planet):
        super(FCN_CRNN, self).__init__()

        self.early_feats = early_feats

        if fcn_model_name == 'simpleCNN':
            self.fcn = simple_CNN(fcn_input_size, crnn_input_size[1])
        elif fcn_model_name == 'fcn8':
            self.fcn = make_fcn_model(crnn_input_size[1], fcn_input_size[1], freeze=False)
        elif fcn_model_name == 'unet':
            if not self.early_feats:
                self.fcn = make_UNet_model(crnn_input_size[1], fcn_input_size[1], late_feats_for_fcn=True, pretrained=pretrained)
            else:
                self.fcn_enc = make_UNetEncoder_model(fcn_input_size[1], use_planet=use_planet, resize_planet=resize_planet, pretrained=pretrained)
                self.fcn_dec = make_UNetDecoder_model(num_classes, late_feats_for_fcn=False) 
        
        if crnn_model_name == "gru":
            if self.early_feats:
                self.crnn = CGRUSegmenter(crnn_input_size, hidden_dims, lstm_kernel_sizes, 
                                      conv_kernel_size, lstm_num_layers, crnn_input_size[1], bidirectional, avg_hidden_states)
            else:
                self.crnn = CGRUSegmenter(crnn_input_size, hidden_dims, lstm_kernel_sizes, 
                                      conv_kernel_size, lstm_num_layers, num_classes, bidirectional, avg_hidden_states)
        elif crnn_model_name == "clstm":
            if self.early_feats:
                self.crnn = CLSTMSegmenter(crnn_input_size, hidden_dims, lstm_kernel_sizes, 
                                       conv_kernel_size, lstm_num_layers, crnn_input_size[1], bidirectional, avg_hidden_states)
            else:
                self.crnn = CLSTMSegmenter(crnn_input_size, hidden_dims, lstm_kernel_sizes, 
                                       conv_kernel_size, lstm_num_layers, num_classes, bidirectional, avg_hidden_states)

    def forward(self, input_tensor):
        batch, timestamps, bands, rows, cols = input_tensor.size()
        fcn_input = input_tensor.view(batch * timestamps, bands, rows, cols)
        
        if self.early_feats:
            center1_feats, enc4_feats, enc3_feats = self.fcn_enc(fcn_input)

            # Reshape tensors to separate batch and timestamps
            crnn_input = center1_feats.view(batch, timestamps, -1, center1_feats.shape[-2], center1_feats.shape[-1])
            enc4_feats = enc4_feats.view(batch, timestamps, -1, enc4_feats.shape[-2], enc4_feats.shape[-1])
            enc3_feats = enc3_feats.view(batch, timestamps, -1, enc3_feats.shape[-2], enc3_feats.shape[-1])

            enc3_feats = torch.mean(enc3_feats, dim=1, keepdim=False)
            enc4_feats = torch.mean(enc4_feats, dim=1, keepdim=False)

            pred_enc = self.crnn(crnn_input)
            preds = self.fcn_dec(pred_enc, enc4_feats, enc3_feats)

        else:
            fcn_output = self.fcn(fcn_input)
            crnn_input = fcn_output.view(batch, timestamps, -1, fcn_output.shape[-2], fcn_output.shape[-1])
            preds = self.crnn(crnn_input)
        
        return preds

def make_MI_CLSTM_model(s1_input_size, s2_input_size, 
                        unet_out_channels,
                        hidden_dims, lstm_kernel_sizes, lstm_num_layers, 
                        conv_kernel_size, 
                        num_classes, bidirectional):
    model = MI_CLSTM(s1_input_size, s2_input_size,
                     unet_out_channels,
                     hidden_dims, lstm_kernel_sizes, lstm_num_layers, 
                     conv_kernel_size, num_classes, bidirectional)
    return model

def make_bidir_clstm_model(input_size, hidden_dims, lstm_kernel_sizes, conv_kernel_size, lstm_num_layers, num_classes, bidirectional):
    """ Defines a (bidirectional) CLSTM model 
    Args:
        input_size - (tuple) size of input dimensions 
        hidden_dims - (int or list) num features for hidden layers 
        lstm_kernel_sizes - (int) kernel size for lstm cells
        conv_kernel_size - (int) ketnel size for convolutional layers
        lstm_num_layers - (int) number of lstm cells to stack
        num_classes - (int) number of classes to predict
        bidirectional - (bool) if True, include reverse inputs and concatenate output features from forward and reverse models
                               if False, use only forward inputs and features
    
    Returns:
      returns the model! 
    """
    clstm_segmenter = CLSTMSegmenter(input_size, hidden_dims, lstm_kernel_sizes, conv_kernel_size, lstm_num_layers, num_classes, bidirectional)

    return clstm_segmenter


def make_fcn_model(n_class, n_channel, freeze=True):
    """ Defines a FCN8s model
    Args: 
      n_class - (int) number of classes to predict
      n_channel - (int) number of channels in input
      freeze - (bool) whether to use pre-trained weights
                TODO: unfreeze after x epochs of training

    Returns: 
      returns the model!
    """
    ## load pretrained model
    fcn8s_pretrained_model=torch.load(torchfcn.models.FCN8s.download())
    fcn8s = FCN8(n_class, n_channel)
    fcn8s.load_state_dict(fcn8s_pretrained_model,strict=False)
    
    if freeze:
        ## Freeze the parameter you do not want to tune
        for param in fcn8s.parameters():
            if torch.sum(param==0)==0:
                param.requires_grad = False
    
    return fcn8s

def make_UNet_model(n_class, n_channel, late_feats_for_fcn=False, pretrained=True, resize_planet=False):
    """ Defines a U-Net model
    Args:
      n_class - (int) number of classes to predict
      n_channel - (int) number of channels in input
      for_fcn - (bool) whether or not U-Net is to be used for FCN + CLSTM, 
                 or false if just used as a U-Net. When True, the last conv and 
                 softmax layer is removed and features are returned. When False, 
                 the softmax layer is kept and probabilities are returned. 
      pretrained - (bool) whether to use pre-trained weights

    Returns: 
      returns the model!
    """
    model = UNet(n_class, n_channel, late_feats_for_fcn)
    
    if pretrained:
        # TODO: Why are pretrained weights from vgg13? 
        # TODO: Adjust these again based on # features we decide to use
        pre_trained = models.vgg13(pretrained=True)
        pre_trained_features = list(pre_trained.features)

        if not resize_planet:
            model.unet_encode.enc2.encode[3] = pre_trained_features[2] # 64 in, 64 out
            model.unet_encode.enc3.encode[0] = pre_trained_features[5] # 64 in, 128 out

        model.unet_encode.enc3.encode[3] = pre_trained_features[7] # 128 in, 128 out
        model.unet_encode.enc4.encode[0] = pre_trained_features[10] # 128 in, 256 out
        model.unet_encode.enc4.encode[3] = pre_trained_features[12] # 256 in, 256 out
        model.unet_encode.center[0] = pre_trained_features[15] # 256 in, 512 out
        
    model = model.cuda()
    return model

def make_UNetEncoder_model(n_channel, use_planet=True, resize_planet=False, pretrained=True):
    model = UNet_Encode(n_channel, use_planet, resize_planet)
    
    if pretrained:
       # TODO: Why are pretrained weights from vgg13? 
        pre_trained = models.vgg13(pretrained=True)
        pre_trained_features = list(pre_trained.features)

        if not resize_planet:
            model.unet_encode.enc2.encode[3] = pre_trained_features[2] # 64 in, 64 out
            model.unet_encode.enc3.encode[0] = pre_trained_features[5] # 64 in, 128 out

        model.unet_encode.enc3.encode[3] = pre_trained_features[7] # 128 in, 128 out
        model.unet_encode.enc4.encode[0] = pre_trained_features[10] # 128 in, 256 out
        model.unet_encode.enc4.encode[3] = pre_trained_features[12] # 256 in, 256 out
        model.unet_encode.center[0] = pre_trained_features[15] # 256 in, 512 out

    model = model.cuda()
    return model

def make_UNetDecoder_model(n_class, late_feats_for_fcn):
    model = UNet_Decode(n_class, late_feats_for_fcn=False)
    model = model.cuda()
    return model

def make_fcn_clstm_model(country, fcn_input_size, fcn_model_name, 
                         crnn_input_size, crnn_model_name, 
                         hidden_dims, lstm_kernel_sizes, conv_kernel_size, lstm_num_layers, avg_hidden_states,
                         num_classes, bidirectional, pretrained, early_feats, use_planet, resize_planet):
    """ Defines a fully-convolutional-network + CLSTM model
    Args:
      fcn_input_size - (tuple) input dimensions for FCN model
      fcn_model_name - (str) model name used as the FCN portion of the network
      crnn_input_size - (tuple) input dimensions for CRNN model
      crnn_model_name - (str) model name used as the convolutional RNN portion of the network 
      hidden_dims - (int or list) num features for hidden layers 
      lstm_kernel_sizes - (int) kernel size for lstm cells
      conv_kernel_size - (int) ketnel size for convolutional layers
      lstm_num_layers - (int) number of lstm cells to stack
      num_classes - (int) number of classes to predict
      bidirectional - (bool) if True, include reverse inputs and concatenate output features from forward and reverse models
                               if False, use only forward inputs and features
      pretrained - (bool) whether to use pre-trained weights

    Returns: 
      returns the model!
    """
    if early_feats:
        crnn_input_size += (16, 16)
    else:
        crnn_input_size += (GRID_SIZE[country], GRID_SIZE[country]) 

    model = FCN_CRNN(fcn_input_size, fcn_model_name, 
                     crnn_input_size, crnn_model_name, hidden_dims, lstm_kernel_sizes, conv_kernel_size, lstm_num_layers, 
                     avg_hidden_states, num_classes, bidirectional, pretrained, early_feats, use_planet, resize_planet)
    model = model.cuda()

    return model

def make_UNet3D_model(n_class, n_channel, timesteps):
    """ Defined a 3d U-Net model
    Args: 
      n_class - (int) number of classes to predict
      n_channels - (int) number of input channgels

    Returns:
      returns the model!
    """

    model = UNet3D(n_channel, n_class, timesteps)
    model = model.cuda()
    return model

def get_model(model_name, **kwargs):
    """ Get appropriate model based on model_name and input arguments
    Args: 
      model_name - (str) which model to use 
      kwargs - input arguments corresponding to the model name

    Returns: 
      returns the model!
    """

    model = None

    if model_name == 'random_forest':
        # use class weights
        class_weight=None
        if kwargs.get('loss_weight'):
            class_weight = 'balanced'

        model = make_rf_model(random_state=kwargs.get('seed', None),
                              n_jobs=kwargs.get('n_jobs', None),
                              n_estimators=kwargs.get('n_estimators', 100),
                              class_weight=class_weight)

    elif model_name == 'bidir_clstm':
        num_bands = get_num_bands(kwargs)
        num_timesteps = kwargs.get('num_timesteps')

        # TODO: change the timestamps passed in to be more flexible (i.e allow specify variable length / fixed / truncuate / pad)
        # TODO: don't hardcode values
        model = make_bidir_clstm_model(input_size=(num_timesteps, num_bands, GRID_SIZE[kwargs.get('country')], GRID_SIZE[kwargs.get('country')]), 

                                       hidden_dims=kwargs.get('hidden_dims'), 
                                       lstm_kernel_sizes=(kwargs.get('crnn_kernel_sizes'), kwargs.get('crnn_kernel_sizes')), 
                                       conv_kernel_size=kwargs.get('conv_kernel_size'), 
                                       lstm_num_layers=kwargs.get('crnn_num_layers'),
                                       num_classes=NUM_CLASSES[kwargs.get('country')],
                                       bidirectional=kwargs.get('bidirectional'))
    elif model_name == 'fcn':
        num_bands = get_num_bands(kwargs)
        model = make_fcn_model(n_class=NUM_CLASSES[kwargs.get('country')], n_channel = num_bands, freeze=True)
    
    elif model_name == 'unet':
        num_bands = get_num_bands(kwargs)
        num_timesteps = kwargs.get('num_timesteps')
        
        if kwargs.get('time_slice') is None:
            model = make_UNet_model(n_class=NUM_CLASSES[kwargs.get('country')], n_channel = num_bands*num_timesteps)
        else: 
            model = make_UNet_model(n_class=NUM_CLASSES[kwargs.get('country')], n_channel = num_bands)
    
    elif model_name == 'fcn_crnn':
        num_bands = get_num_bands(kwargs) 
        num_timesteps = kwargs.get('num_timesteps')
        model = make_fcn_clstm_model(country=kwargs.get('country'),
                                     fcn_input_size=(num_timesteps, num_bands, GRID_SIZE[kwargs.get('country')], GRID_SIZE[kwargs.get('country')]), 
                                     fcn_model_name=kwargs.get('fcn_model_name'),
                                     #crnn_input_size=(num_timesteps, kwargs.get('fcn_out_feats'), GRID_SIZE[kwargs.get('country')], GRID_SIZE[kwargs.get('country')]),
                                     crnn_input_size=(num_timesteps, kwargs.get('fcn_out_feats')), #, GRID_SIZE[kwargs.get('country')], GRID_SIZE[kwargs.get('country')]),
                                     crnn_model_name=kwargs.get('crnn_model_name'),
                                     hidden_dims=kwargs.get('hidden_dims'), 
                                     lstm_kernel_sizes=(kwargs.get('crnn_kernel_sizes'), kwargs.get('crnn_kernel_sizes')), 
                                     conv_kernel_size=kwargs.get('conv_kernel_size'), 
                                     lstm_num_layers=kwargs.get('crnn_num_layers'), 
                                     avg_hidden_states=kwargs.get('avg_hidden_states'), 
                                     num_classes=NUM_CLASSES[kwargs.get('country')],
                                     bidirectional=kwargs.get('bidirectional'),                                       
                                     pretrained = kwargs.get('pretrained'),
                                     early_feats = kwargs.get('early_feats'),
                                     use_planet = kwargs.get('use_planet'),
                                     resize_planet = kwargs.get('resize_planet'))
    elif model_name == 'unet3d':
        num_bands = get_num_bands(kwargs)
        model = make_UNet3D_model(n_class = NUM_CLASSES[kwargs.get('country')], n_channel = num_bands, timesteps=kwargs.get('num_timesteps'))
    elif model_name == 'mi_clstm':
        num_s1_bands, num_s2_bands = get_num_s1_bands(kwargs), get_num_s2_bands(kwargs)
        model = make_MI_CLSTM_model(s1_input_size=(num_timesteps, num_s1_bands, GRID_SIZE[kwargs.get('country')], GRID_SIZE[kwargs.get('country')]),
                                    s2_input_size=(num_timesteps, num_s2_bands, GRID_SIZE[kwargs.get('country')], GRID_SIZE[kwargs.get('country')]),
                                    unet_out_channels=kwargs.get('fcn_out_feats'),
                                    hidden_dims=kwargs.get('hidden_dims'), 
                                    lstm_kernel_sizes=(kwargs.get('crnn_kernel_sizes'), kwargs.get('crnn_kernel_sizes')), 
                                    conv_kernel_size=kwargs.get('conv_kernel_size'), 
                                    lstm_num_layers=kwargs.get('crnn_num_layers'), 
                                    avg_hidden_states=kwargs.get('avg_hidden_states'), 
                                    num_classes=NUM_CLASSES[kwargs.get('country')],
                                    bidirectional=kwargs.get('bidirectional'))
    else:
        raise ValueError(f"Model {model_name} unsupported, check `model_name` arg") 
        

    return model

