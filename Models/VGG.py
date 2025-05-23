from unicodedata import numeric
import torch.nn as nn
import torch
from modules import MyFloor
import math
from spikingjelly.clock_driven import neuron

from torch.autograd import Function

class ScaledNeuron(nn.Module):
    def __init__(self, scale=1.):
        super(ScaledNeuron, self).__init__()
        self.scale = scale
        self.t = 0
        self.neuron = neuron.IFNode(v_reset=None)
    def forward(self, x):          
        x = x / self.scale
        if self.t == 0:
            self.neuron(torch.ones_like(x)*0.5)
        x = self.neuron(x)
        self.t += 1
        return x * self.scale
    def reset(self):
        self.t = 0
        self.neuron.reset()


class dec_to_bin(Function):
    @staticmethod
    def forward(ctx, input, t): 
        mask = 2 ** torch.arange(int(math.log2(t+1))).to(input.device)
        #return (input.int().unsqueeze(-1).bitwise_and(mask).ne(0).float())
        return (input.int().unsqueeze(-1).bitwise_and(mask).ne(0).float()).clone().detach()

    @staticmethod
    def backward(ctx, grad_output):
        #return grad_output.mean(dim=-1), None
        return (grad_output.mean(dim=-1)).clone().detach(), None

convert_to_binary = dec_to_bin.apply

cfg = {
    'VGG11': [
        [64, 'M'],
        [128, 'M'],
        [256, 256, 'M'],
        [512, 512, 'M'],
        [512, 512, 'M']
    ],
    'VGG13': [
        [64, 64, 'M'],
        [128, 128, 'M'],
        [256, 256, 'M'],
        [512, 512, 'M'],
        [512, 512, 'M']
    ],
    'VGG16': [
        [64, 64, 'M'],
        [128, 128, 'M'],
        [256, 256, 256, 'M'],
        [512, 512, 512, 'M'],
        [512, 512, 512, 'M']
    ],
    'VGG19': [
        [64, 64, 'M'],
        [128, 128, 'M'],
        [256, 256, 256, 256, 'M'],
        [512, 512, 512, 512, 'M'],
        [512, 512, 512, 512, 'M']
    ]
}


class VGG(nn.Module):
    def __init__(self, vgg_name, num_classes, dropout):
        super(VGG, self).__init__()
        self.init_channels = 3
        self.num_classes = num_classes
        self.layer1 = self._make_layers(cfg[vgg_name][0], dropout)
        self.layer2 = self._make_layers(cfg[vgg_name][1], dropout)
        self.layer3 = self._make_layers(cfg[vgg_name][2], dropout)
        self.layer4 = self._make_layers(cfg[vgg_name][3], dropout)
        self.layer5 = self._make_layers(cfg[vgg_name][4], dropout)
        self.neuron = neuron.IFNode(v_reset=None)
        if num_classes == 1000:
            self.classifier = nn.Sequential(
                nn.Flatten(),
                nn.Linear(512*7*7, 4096),
                nn.ReLU(inplace=True),
                nn.Dropout(dropout),
                nn.Linear(4096, 4096),
                nn.ReLU(inplace=True),
                nn.Dropout(dropout),
                nn.Linear(4096, num_classes)
            )
        else:
            self.classifier = nn.Sequential(
                nn.Flatten(),
                nn.Linear(512, 4096),
                nn.ReLU(inplace=True),
                nn.Dropout(dropout),
                nn.Linear(4096, 4096),
                nn.ReLU(inplace=True),
                nn.Dropout(dropout),
                nn.Linear(4096, num_classes)
            )

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, val=1)
                nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Linear):
                nn.init.zeros_(m.bias)

    def _make_layers(self, cfg, dropout):
        layers = []
        for x in cfg:
            if x == 'M':
                layers.append(nn.MaxPool2d(kernel_size=2, stride=2))
            else:
                layers.append(nn.Conv2d(self.init_channels, x, kernel_size=3, padding=1))
                layers.append(nn.BatchNorm2d(x))
                layers.append(nn.ReLU(inplace=True))
                layers.append(nn.Dropout(dropout))
                self.init_channels = x
        return nn.Sequential(*layers)

    #def convert_to_binary(self, x, bits):
    #    mask = 2 ** torch.arange(bits - 1, -1, -1).to(x.device)
    #    return x.unsqueeze(-1).bitwise_and(mask).ne(0).byte()
    
    def hoyer_loss(self, x, t):
        #return torch.sum(x)
        #x[x<0.0] = 0
        #x[x>=thr] = thr
        #print("input grad: ", x.grad)
        #self.save_input = x.clone()
        #self.save_input.retain_grad()

        x = convert_to_binary(x, t)

        #self.save_output = x.clone()
        #self.save_output.retain_grad()
        #print("output_grad: ", x.grad)
        if torch.sum(x)>0: #  and l < self.start_spike_layer
            return torch.sum(x)
            #return  ((torch.sum(x))**2 / torch.sum((x)**2))

            # if self.loss_type == 'mean':
            #     return torch.mean(torch.sum(torch.abs(x), dim=(1,2,3))**2 / torch.sum((x)**2, dim=(1,2,3)))
            # elif self.loss_type == 'sum':
            #     return  (torch.sum(torch.abs(x))**2 / torch.sum((x)**2))
            # elif self.loss_type == 'cw':
            #     hoyer_thr = torch.sum((x)**2, dim=(0,2,3)) / torch.sum(torch.abs(x), dim=(0,2,3))
            #     # 1.0 is the max thr
            #     hoyer_thr = torch.nan_to_num(hoyer_thr, nan=1.0)
            #     return torch.mean(hoyer_thr)
        return 0.0
    
    def forward(self, x, t, mode):
        act_loss = 0.0
        self.neuron_count = 0
        out = x
        batch_size, _, _, _ = x.shape
        counter = 0
        time_steps = int(math.log2(t+1))
        
        #out = self.layer1(out)

        for i, layers in enumerate([self.layer1, self.layer2, self.layer3, self.layer4, self.layer5, self.classifier]):
            for l in layers:
                out = l(out)

                if isinstance(l, MyFloor):
                    self.neuron_count += torch.numel(out) 
                    act_loss += self.hoyer_loss((out/l.up)*t, t)
                if 'ScaledNeuron' in str(l):
                    self.neuron_count += torch.numel(out)
                    act_loss += torch.count_nonzero(out)
                '''
                if isinstance(l, MyFloor):
                    #self.act_loss += self.hoyer_loss((out/l.up)*t, t)
                    if out.dim() == 4:
                        B, C, H, W = out.shape
                    else:
                        B, C = out.shape
                    counter += 1
                    if counter <= 1:
                        
                        #if counter != 1:
                        #    out = out.reshape(time_steps, int(B/time_steps), C, H, W).contiguous() if out.dim()==4 else out.reshape(time_steps, int(B/time_steps), C).contiguous()
                        #    out = out.sum(dim=0)
                        out_b = l(out) 
                        out_b = convert_to_binary((out_b/l.up)*t, t).permute(4,0,1,2,3) if out_b.dim()==4 else convert_to_binary(out_b/l.up*t, t).permute(2,0,1)
                        out_b = out_b*l.up/t
                        #print(out_b.shape)
                        #out_b = out.reshape(time_steps, int(B/time_steps), C, H, W).contiguous() if out.dim()==4 else out.reshape(time_steps, int(B/time_steps), C).contiguous()
                    else:
                        out_b = out.reshape(time_steps, int(B/time_steps), C, H, W).contiguous() if out.dim()==4 else out.reshape(time_steps, int(B/time_steps), C).contiguous()
                        for i in range(out_b.shape[0]):
                            if i == 0:
                                self.neuron(torch.ones_like(out_b[0])*0.5, 1, False)
                            #out_b[i] = l.up * self.neuron(out_b[i]/l.up, 1, True)
                            out_b[i] = (1/t) * l.up * self.neuron(out_b[i]/l.up, 1*(2**(i+1)-1)/((2**(time_steps))-1), True if i==time_steps-1 else False)
                        self.neuron.reset()
                    #print(out_b.shape)
                    #print(l.up)
                    #exit()

                    for i in range(out_b.shape[0]):
                        out_b[i] = 2**i*out_b[i]
                    #out = out_b.sum(dim=0)
                    out = out_b.flatten(0,1).contiguous()
                else:
                    out = l(out)

'''
    


        #out = convert_to_binary(out)   #(B, C, H, W) -> (T, B, C, H, W)
        #out = self.layer2(out)

    
        '''
        for i in range(2):
            out = self.layer2[i](out)
        out = out.reshape(T,B,C,H,W).contiguous()             #(B, C, H, W) -> (T, B, C, H, W)
        for i in range(2, 4):
            out = self.layer2[i](out)
        out = out.flatten(0,1)
        for i in range(4, 6):
            out = self.layer2[i](out)
        out = out.reshape(T,B,N,C).contiguous()  #(T, B, C, H, W) -> (B, T, C, H, W)
        for i in range(6, 8):
            out = self.layer2[i](out)
        out = out.flatten(0,1)
        out =self.layer2[8](out)
        '''


        #out = self.classifier(out)

        #out = out.reshape(time_steps, batch_size, self.num_classes).contiguous().sum(dim=0)
        
        return (out), act_loss


class VGG_normed(nn.Module):
    def __init__(self, vgg_name, num_classes, dropout):
        super(VGG_normed, self).__init__()
        self.num_classes = num_classes
        self.module_list = self._make_layers(cfg[vgg_name], dropout)


    def _make_layers(self, cfg, dropout):
        layers = []
        for i in range(5):
            for x in cfg[i]:
                if x == 'M':
                    layers.append(nn.MaxPool2d(kernel_size=2, stride=2))
                else:
                    layers.append(nn.Conv2d(3, x, kernel_size=3, padding=1))
                    layers.append(nn.ReLU(inplace=True))
                    layers.append(nn.Dropout(dropout))
                    self.init_channels = x
        layers.append(nn.Flatten())
        if self.num_classes == 1000:
            layers.append(nn.Linear(512*7*7, 4096))
        else:
            layers.append(nn.Linear(512, 4096))
        layers.append(nn.ReLU(inplace=True))
        layers.append(nn.Dropout(dropout))
        layers.append(nn.Linear(4096, 4096))
        layers.append(nn.ReLU(inplace=True))
        layers.append(nn.Dropout(dropout))
        layers.append(nn.Linear(4096, self.num_classes))

        return nn.Sequential(*layers)

    def forward(self, x):
        return self.module_list(x)



def vgg11(num_classes=10, dropout=0, **kargs):
    return VGG('VGG11', num_classes, dropout)


def vgg13(num_classes=10, dropout=0, **kargs):
    return VGG('VGG13', num_classes, dropout)


def vgg16(num_classes=10, dropout=0, **kargs):
    return VGG('VGG16', num_classes, dropout)


def vgg19(num_classes=10, dropout=0, **kargs):
    return VGG('VGG19', num_classes, dropout)


def vgg16_normed(num_classes=10, dropout=0, **kargs):
    return VGG_normed('VGG16', num_classes, dropout)