import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
import math
from functools import partial
import pdb

__all__ = [
    'ResNet', 'resnet10', 'resnet18', 'resnet34', 'resnet50', 'resnet101',
    'resnet152', 'resnet200'
]


def conv3x3x3(in_planes, out_planes, stride=1):
    # 3x3x3 convolution with padding
    return nn.Conv3d(
        in_planes,
        out_planes,
        kernel_size=3,
        stride=stride,
        padding=1,
        bias=False)


def downsample_basic_block(x, planes, stride):
    out = F.avg_pool3d(x, kernel_size=1, stride=stride)
    zero_pads = torch.Tensor(
        out.size(0), planes - out.size(1), out.size(2), out.size(3),
        out.size(4)).zero_()
    if isinstance(out.data, torch.cuda.FloatTensor):
        zero_pads = zero_pads.cuda()

    out = Variable(torch.cat([out.data, zero_pads], dim=1))

    return out


class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(BasicBlock, self).__init__()
        self.conv1 = conv3x3x3(inplanes, planes, stride)
        self.bn1 = nn.BatchNorm3d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = conv3x3x3(planes, planes)
        self.bn2 = nn.BatchNorm3d(planes)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


class Bottleneck(nn.Module):
    expansion = 4

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(Bottleneck, self).__init__()
        self.conv1 = nn.Conv3d(inplanes, planes, kernel_size=1, bias=False)
        self.bn1 = nn.BatchNorm3d(planes)
        self.conv2 = nn.Conv3d(
            planes, planes, kernel_size=3, stride=stride, padding=1, bias=False)
        self.bn2 = nn.BatchNorm3d(planes)
        self.conv3 = nn.Conv3d(planes, planes * 4, kernel_size=1, bias=False)
        self.bn3 = nn.BatchNorm3d(planes * 4)
        self.relu = nn.ReLU(inplace=True)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)

        out = self.conv3(out)
        out = self.bn3(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


class ResNet(nn.Module):

    def __init__(self,
                 block,
                 layers,
                 sample_size,
                 sample_duration,
                 shortcut_type='A',
                 num_classes=400,
                 is_gray=False,
                 opt=None):
        self.scaledown = opt.scaledown
        self.nomask = opt.nomask
        self.sample_duration = sample_duration
        self.residual = opt.residual
        self.num_classes = num_classes
        self.inplanes = 64
        super(ResNet, self).__init__()
        self.is_fwbw = opt.is_fwbw
        if is_gray:
            self.conv1 = nn.Conv3d(1,64,kernel_size=7, stride=(1, 2, 2), padding=(3, 3, 3), bias=False)
        else:
            self.conv1 = nn.Conv3d(3,64,kernel_size=7, stride=(1, 2, 2), padding=(3, 3, 3), bias=False)
        self.bn1 = nn.BatchNorm3d(64)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool3d(kernel_size=(3, 3, 3), stride=(1,2,2), padding=1)
        
        # ENCODER
        self.layer1 = self._make_layer(block, 64, layers[0], shortcut_type) # 64x  4x32x32
        self.layer2 = self._make_layer(block, 128, layers[1], shortcut_type, stride=(1,2,2)) # 128x  2x16x16
        self.layer3 = self._make_layer(block, 256, layers[2], shortcut_type, stride=(1,2,2)) # 256x  1x8x8
        self.layer4 = self._make_layer(block, 512, layers[3], shortcut_type, stride=(1,1,1)) # 512x  1x8x8

        # DECODER
        self.unlayer1 = nn.Sequential(
                        nn.Conv3d(512,512,kernel_size=(3,3,3), stride=(1, 1, 1), padding=(1, 1, 1), bias=False),
                        nn.BatchNorm3d(512),
                        nn.ReLU(inplace=True)
                        ) #512X2x16x16
        self.unlayer2 = nn.Sequential(
                        nn.Conv3d(512,256,kernel_size=(3,3,3), stride=(1, 1, 1), padding=(1, 1, 1), bias=False),
                        nn.BatchNorm3d(256),
                        nn.ReLU(inplace=True)
                        ) #256x4x32x32
        self.unlayer3 = nn.Sequential(
                        nn.Conv3d(256,128,kernel_size=(3,3,3), stride=(1, 1, 1), padding=(1, 1, 1), bias=False),
                        nn.BatchNorm3d(128),
                        nn.ReLU(inplace=True)
                        ) #128x8x64x64
        self.unlayer4 = nn.Sequential(
                        nn.Conv3d(128,64,kernel_size=(3,3,3), stride=(1, 1, 1), padding=(1, 1, 1), bias=False),
                        nn.BatchNorm3d(64),
                        nn.ReLU(inplace=True)
                        ) #64x16x128x128
        self.unconv1 = nn.Conv3d(64,3,kernel_size=3, stride=(1,1,1), padding=(1,1,1), bias=False) #128x128
        if not self.nomask :
            self.unconv2 = nn.Sequential(
                           nn.Conv3d(64,1,kernel_size=3, stride=(1,1,1), padding=(1,1,1), bias=False),nn.Sigmoid()
                           )

        for m in self.modules():
            if isinstance(m, nn.Conv3d):
                m.weight = nn.init.kaiming_normal(m.weight, mode='fan_out')
            elif isinstance(m, nn.ConvTranspose3d):
                m.weight = nn.init.kaiming_normal(m.weight, mode='fan_out')
            elif isinstance(m, nn.BatchNorm3d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()
            elif isinstance(m, nn.Linear):
                m.weight.data.normal_(0, 0.01)
                m.bias.data.zero_()

    def _make_layer(self, block, planes, blocks, shortcut_type, stride=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            if shortcut_type == 'A':
                downsample = partial(
                    downsample_basic_block,
                    planes=planes * block.expansion,
                    stride=stride)
            else:
                downsample = nn.Sequential(
                    nn.Conv3d(
                        self.inplanes,
                        planes * block.expansion,
                        kernel_size=1,
                        stride=stride,
                        bias=False), nn.BatchNorm3d(planes * block.expansion)) 

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample))
        self.inplanes = planes * block.expansion
        for i in range(1, blocks): 
            layers.append(block(self.inplanes, planes))

        return nn.Sequential(*layers)

    def forward(self, x):
        residual = x
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)
        # print('M',x.size())
        x = self.layer1(x)
        # print('L1',x.size())
        x = self.layer2(x)
        # print('L2',x.size())
        x = self.layer3(x)
        # print('L3',x.size())
        x = self.layer4(x)
        # print('L4',x.size())
        x = self.unlayer1(F.upsample(x, size=(self.sample_duration,16,16), mode='trilinear'))
        #x = self.unlayer1(F.upsample(x, scale_factor=2, mode='trilinear'))
        # print('U1',x.size())
        x = self.unlayer2(F.upsample(x, size=(self.sample_duration,32,32), mode='trilinear'))
        #x = self.unlayer2(F.upsample(x, scale_factor=2, mode='trilinear'))
        # print('U2',x.size())
        x = self.unlayer3(F.upsample(x, size=(self.sample_duration,64,64), mode='trilinear'))
        #x = self.unlayer3(F.upsample(x, scale_factor=2, mode='trilinear'))
        # print('U3',x.size())
        decoded = self.unlayer4(F.upsample(x, size=(self.sample_duration,128,128), mode='trilinear'))
        #x = self.unlayer4(F.upsample(x, scale_factor=2, mode='trilinear'))
        # print('U4',x.size())
        out1 = self.unconv1(decoded)
        if not self.nomask:
            out2 = self.unconv2(decoded)
        else:
            out2 =None

        #if self.residual:
        #    out1 = out1 + residual
        # return x
        if self.scaledown:
            out1 = torch.clamp(out1,min=0, max=1)
        return out1, out2



def get_fine_tuning_parameters(model, ft_begin_index):
    if ft_begin_index == 0:
        return model.parameters()

    ft_module_names = []
    for i in range(ft_begin_index, 5):
        ft_module_names.append('layer{}'.format(i))
    ft_module_names.append('fc')

    parameters = []
    for k, v in model.named_parameters():
        for ft_module in ft_module_names:
            if ft_module in k:
                parameters.append({'params': v})
                break
        else:
            parameters.append({'params': v, 'lr': 0.0})

    return parameters


def resnet10(**kwargs):
    """Constructs a ResNet-18 model.
    """
    model = ResNet(BasicBlock, [1, 1, 1, 1], **kwargs)
    return model


def resnet18(**kwargs):
    """Constructs a ResNet-18 model.
    """
    model = ResNet(BasicBlock, [2, 2, 2, 2], **kwargs)
    return model


def resnet34(**kwargs):
    """Constructs a ResNet-34 model.
    """
    model = ResNet(BasicBlock, [3, 4, 6, 3], **kwargs)
    return model


def resnet50(**kwargs):
    """Constructs a ResNet-50 model.
    """
    model = ResNet(Bottleneck, [3, 4, 6, 3], **kwargs)
    return model


def resnet101(**kwargs):
    """Constructs a ResNet-101 model.
    """
    model = ResNet(Bottleneck, [3, 4, 23, 3], **kwargs)
    return model


def resnet152(**kwargs):
    """Constructs a ResNet-101 model.
    """
    model = ResNet(Bottleneck, [3, 8, 36, 3], **kwargs)
    return model


def resnet200(**kwargs):
    """Constructs a ResNet-101 model.
    """
    model = ResNet(Bottleneck, [3, 24, 36, 3], **kwargs)
    return model