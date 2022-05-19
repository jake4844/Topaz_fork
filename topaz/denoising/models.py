import datetime
import os
import sys
from collections import OrderedDict
import time
import numpy as np

import pkg_resources
import multiprocessing as mp
import torch
import torch.functional as F
from topaz.filters import AffineDenoise
from torch import nn
from topaz.denoising.utils import set_device


class DenoiseNet(nn.Module):
    def __init__(self, base_filters):
        super(DenoiseNet, self).__init__()

        self.base_filters = base_filters
        nf = base_filters
        self.net = nn.Sequential( nn.Conv2d(1, nf, 11, padding=5)
                                , nn.LeakyReLU(0.1)
                                , nn.MaxPool2d(3, stride=1, padding=1)
                                , nn.Conv2d(nf, 2*nf, 3, padding=2, dilation=2)
                                , nn.LeakyReLU(0.1)
                                , nn.Conv2d(2*nf, 2*nf, 3, padding=4, dilation=4)
                                , nn.LeakyReLU(0.1)
                                , nn.Conv2d(2*nf, 3*nf, 3, padding=1)
                                , nn.LeakyReLU(0.1)
                                , nn.MaxPool2d(3, stride=1, padding=1)
                                , nn.Conv2d(nf, 2*nf, 3, padding=2, dilation=2)
                                , nn.LeakyReLU(0.1)
                                , nn.Conv2d(3*nf, 3*nf, 3, padding=4, dilation=4)
                                , nn.LeakyReLU(0.1)
                                , nn.Conv2d(3*nf, 1, 7, padding=3)
                                )

    def forward(self, x):
        return self.net(x)


class DenoiseNet2(nn.Module):
    def __init__(self, base_filters, width=11):
        super(DenoiseNet2, self).__init__()

        self.base_filters = base_filters
        nf = base_filters
        self.net = nn.Sequential( nn.Conv2d(1, nf, width, padding=width//2)
                                , nn.LeakyReLU(0.1)
                                , nn.Conv2d(nf, nf, width, padding=width//2)
                                , nn.LeakyReLU(0.1)
                                , nn.Conv2d(nf, 1, width, padding=width//2)
                                )

    def forward(self, x):
        return self.net(x)


class Identity(nn.Module):
    def forward(self, x):
        return x


class UDenoiseNet(nn.Module):
    # U-net from noise2noise paper
    def __init__(self, nf=48, base_width=11, top_width=3):
        super(UDenoiseNet, self).__init__()

        self.enc1 = nn.Sequential( nn.Conv2d(1, nf, base_width, padding=base_width//2)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc2 = nn.Sequential( nn.Conv2d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc3 = nn.Sequential( nn.Conv2d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc4 = nn.Sequential( nn.Conv2d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc5 = nn.Sequential( nn.Conv2d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc6 = nn.Sequential( nn.Conv2d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )

        self.dec5 = nn.Sequential( nn.Conv2d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec4 = nn.Sequential( nn.Conv2d(3*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec3 = nn.Sequential( nn.Conv2d(3*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec2 = nn.Sequential( nn.Conv2d(3*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec1 = nn.Sequential( nn.Conv2d(2*nf+1, 64, top_width, padding=top_width//2)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(64, 32, top_width, padding=top_width//2)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(32, 1, top_width, padding=top_width//2)
                                 )

    def forward(self, x):
        # downsampling
        p1 = self.enc1(x)
        p2 = self.enc2(p1)
        p3 = self.enc3(p2)
        p4 = self.enc4(p3)
        p5 = self.enc5(p4)
        h = self.enc6(p5)

        # upsampling
        n = p4.size(2)
        m = p4.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, p4], 1)

        h = self.dec5(h)

        n = p3.size(2)
        m = p3.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, p3], 1)

        h = self.dec4(h)

        n = p2.size(2)
        m = p2.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, p2], 1)

        h = self.dec3(h)

        n = p1.size(2)
        m = p1.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, p1], 1)

        h = self.dec2(h)

        n = x.size(2)
        m = x.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, x], 1)

        y = self.dec1(h)

        return y


class UDenoiseNetSmall(nn.Module):
    def __init__(self, nf=48, width=11, top_width=3):
        super(UDenoiseNetSmall, self).__init__()

        self.enc1 = nn.Sequential( nn.Conv2d(1, nf, width, padding=width//2)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc2 = nn.Sequential( nn.Conv2d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc3 = nn.Sequential( nn.Conv2d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc4 = nn.Sequential( nn.Conv2d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )

        self.dec3 = nn.Sequential( nn.Conv2d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec2 = nn.Sequential( nn.Conv2d(3*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec1 = nn.Sequential( nn.Conv2d(2*nf+1, 64, top_width, padding=top_width//2)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(64, 32, top_width, padding=top_width//2)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(32, 1, top_width, padding=top_width//2)
                                 )

    def forward(self, x):
        # downsampling
        p1 = self.enc1(x)
        p2 = self.enc2(p1)
        p3 = self.enc3(p2)
        h = self.enc4(p3)

        # upsampling with skip connections
        n = p2.size(2)
        m = p2.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, p2], 1)

        h = self.dec3(h)

        n = p1.size(2)
        m = p1.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, p1], 1)

        h = self.dec2(h)

        n = x.size(2)
        m = x.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, x], 1)

        y = self.dec1(h)

        return y


class UDenoiseNet2(nn.Module):
    # modified U-net from noise2noise paper
    def __init__(self, nf=48):
        super(UDenoiseNet2, self).__init__()

        self.enc1 = nn.Sequential( nn.Conv2d(1, nf, 7, padding=3)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc2 = nn.Sequential( nn.Conv2d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc3 = nn.Sequential( nn.Conv2d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc4 = nn.Sequential( nn.Conv2d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc5 = nn.Sequential( nn.Conv2d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc6 = nn.Sequential( nn.Conv2d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )

        self.dec5 = nn.Sequential( nn.Conv2d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec4 = nn.Sequential( nn.Conv2d(3*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec3 = nn.Sequential( nn.Conv2d(3*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec2 = nn.Sequential( nn.Conv2d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec1 = nn.Sequential( nn.Conv2d(2*nf, 64, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(64, 32, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(32, 1, 3, padding=1)
                                 )

    def forward(self, x):
        # downsampling
        p1 = self.enc1(x)
        p2 = self.enc2(p1)
        p3 = self.enc3(p2)
        p4 = self.enc4(p3)
        p5 = self.enc5(p4)
        h = self.enc6(p5)

        # upsampling
        n = p4.size(2)
        m = p4.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, p4], 1)

        h = self.dec5(h)

        n = p3.size(2)
        m = p3.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, p3], 1)

        h = self.dec4(h)

        n = p2.size(2)
        m = p2.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, p2], 1)

        h = self.dec3(h)

        n = p1.size(2)
        m = p1.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')

        h = self.dec2(h)

        n = x.size(2)
        m = x.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')

        y = self.dec1(h)

        return y


class UDenoiseNet3(nn.Module):
    def __init__(self):
        super(UDenoiseNet3, self).__init__()

        self.enc1 = nn.Sequential( nn.Conv2d(1, 48, 7, padding=3)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc2 = nn.Sequential( nn.Conv2d(48, 48, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc3 = nn.Sequential( nn.Conv2d(48, 48, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc4 = nn.Sequential( nn.Conv2d(48, 48, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc5 = nn.Sequential( nn.Conv2d(48, 48, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool2d(2)
                                 )
        self.enc6 = nn.Sequential( nn.Conv2d(48, 48, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )

        self.dec5 = nn.Sequential( nn.Conv2d(96, 96, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(96, 96, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec4 = nn.Sequential( nn.Conv2d(144, 96, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(96, 96, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec3 = nn.Sequential( nn.Conv2d(144, 96, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(96, 96, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec2 = nn.Sequential( nn.Conv2d(144, 96, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(96, 96, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec1 = nn.Sequential( nn.Conv2d(97, 64, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(64, 32, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv2d(32, 1, 3, padding=1)
                                 )

    def forward(self, x):
        # downsampling
        p1 = self.enc1(x)
        p2 = self.enc2(p1)
        p3 = self.enc3(p2)
        p4 = self.enc4(p3)
        p5 = self.enc5(p4)
        h = self.enc6(p5)

        # upsampling
        n = p4.size(2)
        m = p4.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, p4], 1)

        h = self.dec5(h)

        n = p3.size(2)
        m = p3.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, p3], 1)

        h = self.dec4(h)

        n = p2.size(2)
        m = p2.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, p2], 1)

        h = self.dec3(h)

        n = p1.size(2)
        m = p1.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, p1], 1)

        h = self.dec2(h)

        n = x.size(2)
        m = x.size(3)
        h = F.interpolate(h, size=(n,m), mode='nearest')
        h = torch.cat([h, x], 1)

        y = x - self.dec1(h) # learn only noise component

        return y


class UDenoiseNet3D(nn.Module):
    # U-net from noise2noise paper
    def __init__(self, nf=48, base_width=11, top_width=3):
        super(UDenoiseNet3D, self).__init__()

        self.enc1 = nn.Sequential( nn.Conv3d(1, nf, base_width, padding=base_width//2)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool3d(2)
                                 )
        self.enc2 = nn.Sequential( nn.Conv3d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool3d(2)
                                 )
        self.enc3 = nn.Sequential( nn.Conv3d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool3d(2)
                                 )
        self.enc4 = nn.Sequential( nn.Conv3d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool3d(2)
                                 )
        self.enc5 = nn.Sequential( nn.Conv3d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.MaxPool3d(2)
                                 )
        self.enc6 = nn.Sequential( nn.Conv3d(nf, nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )

        self.dec5 = nn.Sequential( nn.Conv3d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv3d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec4 = nn.Sequential( nn.Conv3d(3*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv3d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec3 = nn.Sequential( nn.Conv3d(3*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv3d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec2 = nn.Sequential( nn.Conv3d(3*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv3d(2*nf, 2*nf, 3, padding=1)
                                 , nn.LeakyReLU(0.1)
                                 )
        self.dec1 = nn.Sequential( nn.Conv3d(2*nf+1, 64, top_width, padding=top_width//2)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv3d(64, 32, top_width, padding=top_width//2)
                                 , nn.LeakyReLU(0.1)
                                 , nn.Conv3d(32, 1, top_width, padding=top_width//2)
                                 )

    def forward(self, x):
        # downsampling
        p1 = self.enc1(x)
        p2 = self.enc2(p1)
        p3 = self.enc3(p2)
        p4 = self.enc4(p3)
        p5 = self.enc5(p4)
        h = self.enc6(p5)

        # upsampling
        n = p4.size(2)
        m = p4.size(3)
        o = p4.size(4)
        #h = F.upsample(h, size=(n,m))
        #h = F.upsample(h, size=(n,m), mode='bilinear', align_corners=False)
        h = F.interpolate(h, size=(n,m,o), mode='nearest')
        h = torch.cat([h, p4], 1)

        h = self.dec5(h)

        n = p3.size(2)
        m = p3.size(3)
        o = p3.size(4)
        
        h = F.interpolate(h, size=(n,m,o), mode='nearest')
        h = torch.cat([h, p3], 1)

        h = self.dec4(h)

        n = p2.size(2)
        m = p2.size(3)
        o = p2.size(4)

        h = F.interpolate(h, size=(n,m,o), mode='nearest')
        h = torch.cat([h, p2], 1)

        h = self.dec3(h)

        n = p1.size(2)
        m = p1.size(3)
        o = p1.size(4)

        h = F.interpolate(h, size=(n,m,o), mode='nearest')
        h = torch.cat([h, p1], 1)

        h = self.dec2(h)

        n = x.size(2)
        m = x.size(3)
        o = x.size(4)

        h = F.interpolate(h, size=(n,m,o), mode='nearest')
        h = torch.cat([h, x], 1)

        y = self.dec1(h)

        return y


model_name_dict = {
    # 2D models
    'unet':'unet_L2_v0.2.2.sav',
    'unet-small':'unet_small_L1_v0.2.2.sav',
    'fcnn':'fcnn_L1_v0.2.2.sav',
    'affine':'affine_L1_v0.2.2.sav',
    'unet-v0.2.1':'unet_L2_v0.2.1.sav',
    # 3D models
    'unet-3d':'unet-3d-10a-v0.2.4.sav',
    'unet-3d-10a':'unet-3d-10a-v0.2.4.sav',
    'unet-3d-20a':'unet-3d-20a-v0.2.4.sav'
}

def load_model(name, base_kernel_width=11):
    ''' paths here should be ../pretrained/denoise
    '''
    log = sys.stderr
    
    # resolve model aliases 
    pretrained = (name in model_name_dict.keys())
    if pretrained:
        name = model_name_dict[name]

    # load model architecture
    if name == 'unet_L2_v0.2.1.sav':
        model = UDenoiseNet(base_width=7, top_width=3)
    elif name == 'unet_L2_v0.2.2.sav':
        model = UDenoiseNet(base_width=11, top_width=5)
    elif name == 'unet_small_L1_v0.2.2.sav':
        model = UDenoiseNetSmall(width=11, top_width=5)
    elif name == 'fcnn_L1_v0.2.2.sav':
        model = DenoiseNet2(64, width=11)
    elif name == 'affine_L1_v0.2.2.sav':
        model = AffineDenoise(max_size=31)
    elif name == 'unet-3d-10a-v0.2.4.sav': 
        model = UDenoiseNet3D(base_width=7)
    elif name == 'unet-3d-10a-v0.2.4.sav':
        model = UDenoiseNet3D(base_width=7)
    elif name == 'unet-3d-20a-v0.2.4.sav':
        model = UDenoiseNet3D(base_width=7)
    else:
        # if not set to a pretrained model, try loading path directly
        model = torch.load(name)

    # load model parameters/state
    if pretrained:
        print('# loading pretrained model:', name, file=log)
        pkg = __name__
        path = '../pretrained/denoise/' + name
        f = pkg_resources.resource_stream(pkg, path)
        state_dict = torch.load(f) # load the parameters
        model.load_state_dict(state_dict)
    elif type(model) is OrderedDict and '3d' in name:
        state = model
        model = UDenoiseNet3D(base_width=base_kernel_width)
        model.load_state_dict(state)
    
    model.eval()
    return model


def save_model(model, epoch, save_prefix, digits=3):
    if type(model) is nn.DataParallel:
        model = model.module
    path = save_prefix + ('_epoch{:0'+str(digits)+'}.sav').format(epoch) 
    #path = save_prefix + '_epoch{}.sav'.format(epoch)
    torch.save(model, path)
        

def train_epoch(iterator, model, cost_func, optim, epoch=1, num_epochs=1, N=1, use_cuda=False):   
    c = 0
    loss_accum = 0    
    model.train()

    for batch_idx, (source,target) in enumerate(iterator):    
        b = source.size(0)        
        if use_cuda:
            source = source.cuda()
            target = target.cuda()
            
        denoised_source = model(source)
        loss = cost_func(denoised_source,target)
        
        loss.backward()
        optim.step()
        optim.zero_grad()
        loss = loss.item()

        c += b
        delta = b*(loss - loss_accum)
        loss_accum += delta/c

        template = '# [{}/{}] training {:.1%}, Error={:.5f}'
        line = template.format(epoch+1, num_epochs, c/N, loss_accum)
        print(line, end='\r', file=sys.stderr)
    
    print(' '*80, end='\r', file=sys.stderr)    
    return loss_accum


# 3D
def train_model(even_path, odd_path, save_prefix, save_interval, device, base_kernel_width=11,
                cost_func='L2', weight_decay=0, learning_rate=0.001, optim='adagrad', momentum=0.8,
                minibatch_size=10, num_epochs=500, N_train=1000, N_test=200, tilesize=96, num_workers=1):
    output = sys.stdout
    log = sys.stderr

    if save_prefix is not None:
        save_dir = os.path.dirname(save_prefix)
        if len(save_dir) > 0 and not os.path.exists(save_dir):
            print('# creating save directory:', save_dir, file=log)
            os.makedirs(save_dir)

    start_time = time.time()
    now = datetime.datetime.now()
    print('# starting time: {:02d}/{:02d}/{:04d} {:02d}h:{:02d}m:{:02d}s'.format(now.month,now.day,now.year,now.hour,now.minute,now.second), file=log)

    # initialize the model
    print('# initializing model...', file=log)
    model_base = UDenoiseNet3D(base_width=base_kernel_width)
    model,use_cuda,num_devices = set_device(model_base, device)
    
    if cost_func == 'L2':
        cost_func = nn.MSELoss()
    elif cost_func == 'L1':
        cost_func = nn.L1Loss()
    else:
        cost_func = nn.MSELoss()

    wd = weight_decay
    params = [{'params': model.parameters(), 'weight_decay': wd}]
    lr = learning_rate
    if optim == 'sgd':
        optim = torch.optim.SGD(params, lr=lr, momentum=momentum)
    elif optim == 'rmsprop':
        optim = torch.optim.RMSprop(params, lr=lr)
    elif optim == 'adam':
        optim = torch.optim.Adam(params, lr=lr, betas=(0.9, 0.999), eps=1e-8, amsgrad=True)
    elif optim == 'adagrad':
        optim = torch.optim.Adagrad(params, lr=lr)
    else:
        raise Exception('Unrecognized optim: ' + optim)
        
    # Load the data
    print('# loading data...', file=log)
    if not (os.path.isdir(even_path) or os.path.isfile(even_path)):
        print('ERROR: Cannot find file or directory:', even_path, file=log)
        sys.exit(3)
    if not (os.path.isdir(odd_path) or os.path.isfile(odd_path)):
        print('ERROR: Cannot find directory:', odd_path, file=log)
        sys.exit(3)
    
    if tilesize < 1:
        print('ERROR: tilesize must be >0', file=log)
        sys.exit(4)
    if tilesize < 10:
        print('WARNING: small tilesize is not recommended', file=log)
    data = TrainingDataset3D(even_path, odd_path, tilesize, N_train, N_test)
    
    N_train = len(data)
    data.set_mode('test')
    N_test = len(data)
    data.set_mode('train')
    num_workers = min(num_workers, mp.cpu_count())
    digits = int(np.ceil(np.log10(num_epochs)))

    iterator = torch.utils.data.DataLoader(data,batch_size=minibatch_size,num_workers=num_workers,shuffle=False)
    
    ## Begin model training
    print('# training model...', file=log)
    print('\t'.join(['Epoch', 'Split', 'Error']), file=output)

    for epoch in range(num_epochs):
        data.set_mode('train')
        epoch_loss_accum = train_epoch(iterator,
                                       model,
                                       cost_func,
                                       optim,
                                       epoch=epoch,
                                       num_epochs=num_epochs,
                                       N=N_train,
                                       use_cuda=use_cuda)

        line = '\t'.join([str(epoch+1), 'train', str(epoch_loss_accum)])
        print(line, file=output)
        
        # evaluate on the test set
        data.set_mode('test')
        epoch_loss_accum = eval_model(iterator,
                                   model,
                                   cost_func,
                                   epoch=epoch,
                                   num_epochs=num_epochs,
                                   N=N_test,
                                   use_cuda=use_cuda)
    
        line = '\t'.join([str(epoch+1), 'test', str(epoch_loss_accum)])
        print(line, file=output)

        ## save the models
        if save_prefix is not None and (epoch+1)%save_interval == 0:
            model.eval().cpu()
            save_model(model, epoch+1, save_prefix, digits=digits)
            if use_cuda:
                model.cuda()

    print('# training completed!', file=log)

    end_time = time.time()
    now = datetime.datetime.now()
    print("# ending time: {:02d}/{:02d}/{:04d} {:02d}h:{:02d}m:{:02d}s".format(now.month,now.day,now.year,now.hour,now.minute,now.second), file=log)
    print("# total time:", time.strftime("%Hh:%Mm:%Ss", time.gmtime(end_time - start_time)), file=log)

    return model_base, num_devices





def eval_model(iterator, model, cost_func, epoch=1, num_epochs=1, N=1, use_cuda=False):  
    c = 0
    loss_accum = 0
    model.eval()

    with torch.no_grad():
        for batch_idx, (source,target) in enumerate(iterator):
            b = source.size(0)        
            if use_cuda:
                source = source.cuda()
                target = target.cuda()
                
            denoised_source = model(source)
            loss = cost_func(denoised_source,target)   
            loss = loss.item()
    
            c += b
            delta = b*(loss - loss_accum)
            loss_accum += delta/c
    
            template = '# [{}/{}] testing {:.1%}, Error={:.5f}'
            line = template.format(epoch+1, num_epochs, c/N, loss_accum)
            print(line, end='\r', file=sys.stderr)
             
    print(' '*80, end='\r', file=sys.stderr)    
    return loss_accum