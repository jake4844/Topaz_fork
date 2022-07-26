from __future__ import print_function, division

import numpy as np

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable

from topaz.model.utils import insize_from_outsize

class BasicConv(nn.Module):
    '''A generic convolutional neural network scaffold.'''

    def __init__(self, layers, units, unit_scaling=1, dropout=0, 
                 bn=True, pooling=None, activation=nn.PReLU, dims=2):
        super(BasicConv, self).__init__()

        if dims == 2:
            conv = nn.Conv2d
            max_pool = nn.MaxPool2d
            avg_pool = nn.AvgPool2d
            batch_norm = nn.BatchNorm2d
        elif dims == 3:
            conv = nn.Conv3d
            max_pool = nn.MaxPool3d
            avg_pool = nn.AvgPool3d
            batch_norm = nn.BatchNorm3d
        else:
            raise ValueError(f'Unsupported number of dimensions: {dims}. Try dims=2 or dims=3.')
            
        use_bias = (not bn)
        stride = 2
        if pooling == 'max':
            pooling = max_pool
            stride = 1
        elif pooling == 'avg':
            pooling = avg_pool
            stride = 1

        sizes = layers
        layers = []
        strides = []

        nin = 1
        for size in sizes[:-1]:
            layers += [ conv(nin, units, size, stride=stride, bias=use_bias) ]
            strides += [stride]
            if bn:
                layers += [ batch_norm(units) ]
                strides += [1]
            layers += [ activation() ]
            strides += [1]
            if pooling is not None:
                layers += [ pooling(3, stride=2, padding=1) ]
                strides += [2]
            if dropout > 0:
                layers += [ nn.Dropout(p=dropout) ]
            nin = units
            units *= unit_scaling

        size = sizes[-1]
        layers += [ conv(nin, units, size, bias=use_bias) ]
        strides += [1]
        if bn:
            layers += [ batch_norm(units) ]
            strides += [1]
        layers += [ activation() ]
        if dropout > 0:
            layers += [ nn.Dropout(p=dropout) ]
        strides += [1]

        self.strides = strides
        self.width = insize_from_outsize(layers, 1)
        self.filled = False
        self.features = nn.Sequential(*layers)
        self.latent_dim = units
        self.dims = dims


    def fill(self, stride=1):
        for mod,mod_stride in zip(self.features.children(), self.strides):
            if hasattr(mod, 'dilation'):
                mod.dilation = tuple([stride for _ in range(self.dims)])
            if hasattr(mod, 'stride'):
                mod.stride = tuple([1 for _ in range(self.dims)])
            stride *= mod_stride
        layers = list(self.features.modules())
        self.filled = True
        return stride


    def unfill(self):
        for mod,mod_stride in zip(self.features.children(), self.strides):
            if hasattr(mod, 'dilation'):
                mod.dilation = tuple([1 for _ in range(self.dims)])
            if hasattr(mod, 'stride'):
                mod.stride = tuple([mod_stride for _ in range(self.dims)])
        self.filled = False


    def forward(self, x):
        if len(x.size()) < self.dims + 2:
            # add channels dim, assumes batch dim is present
            x = x.unsqueeze(1)
        if self.filled: ## add (width-1)//2 zeros to edges of x
            p = self.width//2
            #before and after padding for each dim
            pads = tuple([p for _ in range(self.dims * 2)])
            x = F.pad(x, pads)
        z = self.features(x)
        return z


class BasicConv2d(BasicConv):
    '''CNN scaffold for 2D image models'''
    def __init__(self, layers, units, unit_scaling=1, dropout=0, bn=True, pooling=None, activation=nn.PReLU):
        super().__init__(layers, units, unit_scaling, dropout, bn, pooling, activation, dims=2)

class BasicConv3d(BasicConv):
    '''CNN scaffold for 3D volume models'''
    def __init__(self, layers, units, unit_scaling=1, dropout=0, bn=True, pooling=None, activation=nn.PReLU):
        super().__init__(layers, units, unit_scaling, dropout, bn, pooling, activation, dims=3)


class Conv127(BasicConv2d):
    def __init__(self, units, **kwargs):
        super(Conv127, self).__init__(units, [7, 5, 5, 5, 5], **kwargs)

class Conv63(BasicConv2d):
    def __init__(self, units, **kwargs):
        super(Conv63, self).__init__(units, [7, 5, 5, 5], **kwargs)