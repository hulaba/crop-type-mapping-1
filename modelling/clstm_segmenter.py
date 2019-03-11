import torch
import torch.nn as nn
from modelling.util import initialize_weights
from modelling.clstm import CLSTM
from modelling.attention import ApplyAtt

<<<<<<< e50c4cc3acfec5d4d79e80ce7e60d24b082245c2
=======
class VectorAtt(nn.Module):
    
    def __init__(self, hidden_dim_size):
        """
            Assumes input will be in the form (batch, time_steps, hidden_dim_size, height, width)
            Returns reweighted hidden states.
        """
        super(VectorAtt, self).__init__()
        self.linear = nn.Linear(hidden_dim_size, 1, bias=False)
        nn.init.constant_(self.linear.weight, 1)
        self.softmax = nn.Softmax(dim=1)
        
    def forward(self, hidden_states, lengths=None):
        hidden_states = hidden_states.permute(0, 1, 3, 4, 2).contiguous() # puts channels last
        weights = self.softmax(self.linear(hidden_states))
        b, t, c, h, w = weights.shape
        for i, length in enumerate(lengths):
            weights[i, t:] *= 0
        reweighted = weights * hidden_states
        return reweighted.permute(0, 1, 4, 2, 3).contiguous()
    
>>>>>>> updates to mi model for var length
class CLSTMSegmenter(nn.Module):
    """ CLSTM followed by conv for segmentation output
    """

    def __init__(self, input_size, hidden_dims, lstm_kernel_sizes, conv_kernel_size, 
                 lstm_num_layers, num_outputs, bidirectional, with_pred=False, 
                 avg_hidden_states=None, attn_type=None, d=None, r=None, dk=None, dv=None, var_length=False): 

        super(CLSTMSegmenter, self).__init__()
        self.input_size = input_size
        self.hidden_dims = hidden_dims
        self.with_pred = with_pred        

        if self.with_pred:
            self.avg_hidden_states = avg_hidden_states
            self.attention = ApplyAtt(attn_type, hidden_dims, d=d, r=r, dk=dk, dv=dv) 
            self.final_conv = nn.Conv2d(in_channels=hidden_dims, 
                                        out_channels=num_outputs, 
                                        kernel_size=conv_kernel_size, 
                                        padding=int((conv_kernel_size-1)/2)) 
            self.logsoftmax = nn.LogSoftmax(dim=1)
        
        if not isinstance(hidden_dims, list):
            hidden_dims = [hidden_dims]        

        self.clstm = CLSTM(input_size, hidden_dims, lstm_kernel_sizes, lstm_num_layers, var_length=var_length)
        
        self.var_length = var_length
        self.bidirectional = bidirectional
        if self.bidirectional:
            self.clstm_rev = CLSTM(input_size, hidden_dims, lstm_kernel_sizes, lstm_num_layers, var_length=var_length)
            self.att_rev = VectorAtt(hidden_dims[-1])
        self.avg_hidden_states = avg_hidden_states
        
        in_channels = hidden_dims[-1] if not self.bidirectional else hidden_dims[-1] * 2
        initialize_weights(self)
       
    def forward(self, inputs, lengths=None):
        layer_outputs, last_states = self.clstm(inputs)
        b, t, c, h, w = layer_outputs.shape
        # layer outputs is size (b, t, c, h, w)
        if self.avg_hidden_states:
            final_states = [torch.mean(layer_outputs[i], dim=0) for i, length in enumerate(lengths)]
            final_state = torch.stack(final_states)
        else:
            final_state = torch.sum(self.att1(layer_outputs, lengths), dim=1)
        
        rev_layer_outputs = None     
        if self.bidirectional:
            rev_inputs = torch.flip(inputs, dims=[1])
            rev_layer_outputs, rev_last_states = self.clstm_rev(rev_inputs)
            #final_state_rev = torch.sum(self.att_rev(rev_layer_outputs, lengths), dim=1)
            #final_state = torch.cat([final_state, final_state_rev], dim=1)
            
        output = torch.cat([layer_outputs, rev_layer_outputs], dim=1) if rev_layer_outputs is not None else layer_outputs       
        #scores = self.conv(final_state)
        #preds = self.softmax(scores)
        #preds = torch.log(preds)

        if self.with_pred:
            # Apply attention
            if self.attention(output) is None:
                 if not self.avg_hidden_states:
                     last_fwd_feat = output[:, timestamps-1, :, :, :]
                     last_rev_feat = output[:, -1, :, :, :] if self.bidirectional else None
                     reweighted = torch.concat([last_fwd_feat, last_rev_feat], dim=1) if bidirectional else last_fwd_feat
                     reweighted = torch.mean(reweighted, dim=1) #, torch.sum(self.att2(layer_outputs), dim=1), dim=1) 
                 else:
                     reweighted = torch.mean(output, dim=1)
            else:
                reweighted = self.attention(output)
                reweighted = torch.sum(reweighted, dim=1) #, torch.sum(self.att2(layer_outputs), dim=1), dim=1) 

            # Apply final conv
            scores = self.final_conv(reweighted)
            output = self.logsoftmax(scores)

        return output
