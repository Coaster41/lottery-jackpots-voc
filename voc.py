import torch
import torch.nn as nn
import torch.optim as optim
from utils.options import args
import utils.common as utils
import os
import time
import copy
import sys
import random
import numpy as np
import heapq
from data import cifar10, cifar100
from utils.common import *
from importlib import import_module

from sklearn.metrics import average_precision_score, f1_score
from utils.conv_type import *

import models
import pdb

from models import resnet_voc
import torch
from torch import nn
from torchvision import datasets, transforms
import numpy as np
import torch
from torchvision import datasets
from xml.etree.ElementTree import Element as ET_Element
from typing import Any, Dict
import collections


visible_gpus_str = ','.join(str(i) for i in args.gpus)
os.environ['CUDA_VISIBLE_DEVICES'] = visible_gpus_str
args.gpus = [i for i in range(len(args.gpus))]
checkpoint = utils.checkpoint(args)
now = datetime.datetime.now().strftime('%Y-%m-%d-%H:%M:%S')
logger = utils.get_logger(os.path.join(args.job_dir, 'logger'+now+'.log'))
device = torch.device(f"cuda:{args.gpus[0]}") if torch.cuda.is_available() else 'cpu'


class VOCnew(datasets.VOCDetection):
    classes = ('aeroplane', 'bicycle', 'bird', 'boat',
                    'bottle', 'bus', 'car', 'cat', 'chair',
                    'cow', 'diningtable', 'dog', 'horse',
                    'motorbike', 'person', 'pottedplant',
                    'sheep', 'sofa', 'train', 'tvmonitor')
    class_to_ind = dict(zip(classes, range(len(classes))))   

    @staticmethod
    def parse_voc_xml(node: ET_Element) -> Dict[str, Any]:
        

        voc_dict: Dict[str, Any] = {}
        children = list(node)
        if children:
            def_dic: Dict[str, Any] = collections.defaultdict(list)
            for dc in map(datasets.VOCDetection.parse_voc_xml, children):
                for ind, v in dc.items():
                    def_dic[ind].append(v)
            if node.tag == "annotation":
                def_dic["object"] = [def_dic["object"]]
                objs = [def_dic["object"]]
                lbl = np.zeros(len(VOCnew.classes))
                for ix, obj in enumerate(objs[0][0]):        
                    obj_class = VOCnew.class_to_ind[obj['name']]
                    lbl[obj_class] = 1
                return lbl
            voc_dict = {node.tag: {ind: v[0] if len(v) == 1 else v for ind, v in def_dic.items()}}
        if node.text:
            text = node.text.strip()
            if not children:
                voc_dict[node.tag] = text
        return voc_dict
# if args.label_smoothing is None:
#     loss_func = nn.CrossEntropyLoss().cuda()
# else:
#     loss_func = LabelSmoothing(smoothing=args.label_smoothing)
loss_func = nn.BCEWithLogitsLoss()

# Data
print('==> Loading Data..')
class Data:
    def __init__(self):
        train_dataset = VOCnew(root=r'/tmp/public_dataset/pytorch/pascalVOC-data', image_set='train', download=False,
                        transform=transforms.Compose([
                            transforms.Resize(330),
                            transforms.Pad(30),
                            transforms.RandomCrop(300),
                            transforms.RandomHorizontalFlip(),
                            transforms.ToTensor(),
                            transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
                        ]))

        test_dataset = VOCnew(root=r'/tmp/public_dataset/pytorch/pascalVOC-data', image_set='val', download=False,
                        transform=transforms.Compose([
                            transforms.Resize(330), 
                            transforms.CenterCrop(300),
                            transforms.ToTensor(),
                            transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
                        ]))
        self.trainLoader = torch.utils.data.DataLoader(train_dataset, batch_size=256, shuffle=True, num_workers=4)
        self.testLoader = torch.utils.data.DataLoader(test_dataset, batch_size=256, shuffle=False, num_workers=4)
loader = Data()

# train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=32, shuffle=True)
# test_loader = torch.utils.data.DataLoader(val_dataset, batch_size=32, shuffle=False)
# if args.data_set == 'cifar10':
#     loader = cifar10.Data(args)
# elif args.data_set == 'cifar100':
#     loader = cifar100.Data(args)
    
def adjust_rate(epoch):
    rate = math.ceil((1-epoch/args.num_epochs)**4)
    return rate

def pop_up(model, rate):
    pop_num = []
    for n, m in model.named_modules():
        if hasattr(m, "set_prune_rate"):
            pop_num.append(m.final_pop_up(rate))
    model = model.to(device)
    if len(args.gpus) != 1:
        model = nn.DataParallel(model, device_ids=args.gpus)
    #logger.info("epoch{} iter{} pop_configuration {}".format(epoch, iter, pop_num))
    return np.array(pop_num)

def compute_mAP(labels,outputs):
    AP = []
    for i in range(labels.shape[0]):
        AP.append(average_precision_score(labels[i],outputs[i]))
    return np.mean(AP)

def compute_f1(labels, outputs):
    outputs = outputs > 0.5
    return f1_score(labels, outputs, average="samples")

def train(model, optimizer, trainLoader, args, epoch):

    model.train()
    losses = utils.AverageMeter(':.4e')
    mAP_meter = utils.AverageMeter(':6.3f')
    print_freq = len(trainLoader.dataset) // args.train_batch_size // 10
    #print_freq = 1
    #import pdb;pdb.set_trace()
    start_time = time.time()
    i = 0 
    pop_config = np.array([0] * 32)
    rate = adjust_rate(epoch)
    for batch, (inputs, targets) in enumerate(trainLoader):
        i+=1
        inputs, targets = inputs.to(device), targets.to(device)
        optimizer.zero_grad()
        output = model(inputs)
        #adjust_learning_rate(optimizer, epoch, batch, print_freq, args)
        loss = loss_func(output, targets)
        loss.backward()
        losses.update(loss.item(), inputs.size(0))
        optimizer.step()
        #if epoch > 5:
        if args.freeze_weights:
            pop_config = pop_up(model,rate)
        #print(pop_config)
        mAP = compute_mAP(targets.cpu().detach().numpy(), output.cpu().detach().numpy()), inputs.size(0)
        # prec1 = utils.accuracy(output, targets)
        print(mAP)
        mAP_meter.update(mAP, inputs.size(0))

        if batch % print_freq == 0 and batch != 0:
            current_time = time.time()
            cost_time = current_time - start_time
            logger.info(
                'Epoch[{}] ({}/{}):\t'
                'Loss {:.4f}\t'
                'mAP {:.2f}%\t\t'
                'Time {:.2f}s'.format(
                    epoch, batch * args.train_batch_size, len(trainLoader.dataset),
                    float(losses.avg), float(mAP_meter.avg), cost_time
                )
            )
            start_time = current_time
    logger.info("epoch{} pop_configuration {}".format(epoch, pop_config))

def validate(model, testLoader, device='cuda:0', loss_func=nn.BCEWithLogitsLoss()):
    global best_acc
    model.eval()

    losses = AverageMeter(':.4e')
    mAP = AverageMeter(':6.3f')
    f1 = AverageMeter(':.4e')

    start_time = time.time()

    with torch.no_grad():
        for batch_idx, (inputs, targets) in enumerate(testLoader):
            inputs, targets = inputs.to(device), targets.to(device)
            outputs = model(inputs)
            loss = loss_func(outputs, targets)

            losses.update(loss.item(), inputs.size(0))
            labels_cpu = targets.cpu().detach().numpy()
            outputs_cpu = outputs.cpu().detach().numpy()
            mAP.update(compute_mAP(labels_cpu, outputs_cpu), inputs.size(0))
            f1.update(compute_f1(labels_cpu, outputs_cpu), inputs.size(0))

        current_time = time.time()
        print(
            'Test Loss {:.4f}\tmAP {:.2f}%\tf1 score {:.2f}\tTime {:.2f}s\n'
            .format(float(losses.avg), float(mAP.avg*100), float(f1.avg), (current_time - start_time))
        )
    return mAP.avg.item()

def generate_pr_cfg(model):
    cfg_len = {
        'vgg': 17,
        'resnet32': 32,
        'resnet34': 34
    }

    pr_cfg = []
    if args.layerwise == 'l1':
        weights = []
        for name, module in model.named_modules():
            if hasattr(module, "set_prune_rate") and name != 'fc' and name != 'classifier':
                conv_weight = module.weight.data.detach().cpu()   
                weights.append(conv_weight.view(-1)) 
        all_weights = torch.cat(weights,0)
        preserve_num = int(all_weights.size(0) * (1 - args.prune_rate))
        preserve_weight, _ = torch.topk(torch.abs(all_weights), preserve_num)
        threshold = preserve_weight[preserve_num-1]

        #Based on the pruning threshold, the prune cfg of each layer is obtained
        for weight in weights:
            pr_cfg.append(torch.sum(torch.lt(torch.abs(weight),threshold)).item()/weight.size(0))
        pr_cfg.append(0)
    elif args.layerwise == 'uniform':
        pr_cfg = [args.prune_rate] * cfg_len[args.arch]
        pr_cfg[-1] = 0
    get_prune_rate(model, pr_cfg)

    return pr_cfg

def get_prune_rate(model, pr_cfg):
    all_params = 0
    prune_params = 0

    i = 0
    for name, module in model.named_modules():
        if hasattr(module, "set_prune_rate"):
            w = module.weight.data.detach().cpu()
            params = w.size(0) * w.size(1) * w.size(2) * w.size(3)
            all_params = all_params + params
            prune_params += int(params * pr_cfg[i])
            i += 1

    logger.info('Params Compress Rate: %.2f M/%.2f M(%.2f%%)' % ((all_params-prune_params)/1000000, all_params/1000000, 100. * prune_params / all_params))

def main():
    start_epoch = 0
    best_acc = 0.0

    model, pr_cfg = get_model(args,logger)

    optimizer = get_optimizer(args, model)

    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, args.num_epochs)

    if args.resume == True:
        start_epoch, best_acc = resume(args, model, optimizer)
    
    if len(args.gpus) != 1:
        model = nn.DataParallel(model, device_ids=args.gpus)

    for epoch in range(start_epoch, args.num_epochs):
        train(model, optimizer, loader.trainLoader, args, epoch)
        test_acc = validate(model, loader.testLoader)
        scheduler.step()

        is_best = best_acc < test_acc
        best_acc = max(best_acc, test_acc)

        model_state_dict = model.module.state_dict() if len(args.gpus) > 1 else model.state_dict()

        state = {
            'state_dict': model_state_dict,
            'best_acc': best_acc,
            'optimizer': optimizer.state_dict(),
            #'scheduler': scheduler.state_dict(),
            'epoch': epoch + 1,
            'cfg': pr_cfg,
        }

        checkpoint.save_model(state, epoch + 1, is_best)

    logger.info('Best accurary: {:.3f}'.format(float(best_acc)))

def resume(args, model, optimizer):
    if os.path.exists(args.job_dir+'/checkpoint/model_last.pt'):
        print(f"=> Loading checkpoint ")

        checkpoint = torch.load(args.job_dir+'/checkpoint/model_last.pt')

        start_epoch = checkpoint["epoch"]

        best_acc = checkpoint["best_acc"]

        model.load_state_dict(checkpoint["state_dict"])

        optimizer.load_state_dict(checkpoint["optimizer"])

        print(f"=> Loaded checkpoint (epoch) {checkpoint['epoch']})")

        return start_epoch, best_acc
    else:
        print(f"=> No checkpoint found at '{args.job_dir}' '/checkpoint/")

class VocModel(nn.Module):
    def __init__(self, num_classes, weights=None):
        super().__init__()
        # Use a pretrained model
        self.network = resnet_voc.resnet34(weights=weights, lottery=True)
        # Replace last layer
        self.network.fc = nn.Linear(self.network.fc.in_features, num_classes)

    def forward(self, xb):
        return self.network(xb)

def get_model(args,logger):
    pr_cfg = []
    print(device)
    print("=> Creating model '{}'".format(args.arch))
    model = VocModel(20).to(device)
    ckpt = torch.load(args.pretrained_model, map_location=device)
    #import pdb;pdb.set_trace()
    model.load_state_dict(ckpt, strict=False)
    
    #applying sparsity to the network
    pr_cfg = generate_pr_cfg(model)
    model = VocModel(20).to(device)
    set_model_prune_rate(model, pr_cfg, logger)
    
    if args.freeze_weights:
        freeze_model_weights(model)

    model = model.to(device)

    return model, pr_cfg

def get_optimizer(args, model):
    if args.optimizer == "sgd":
        parameters = list(model.named_parameters())
        bn_params = [v for n, v in parameters if ("bn" in n) and v.requires_grad]
        rest_params = [v for n, v in parameters if ("bn" not in n) and v.requires_grad]
        optimizer = torch.optim.SGD(
            [
                {
                    "params": bn_params,
                    "weight_decay": 0 if args.no_bn_decay else args.weight_decay,
                },
                {"params": rest_params, "weight_decay": args.weight_decay},
            ],
            args.lr,
            momentum=args.momentum,
            weight_decay=args.weight_decay,
            nesterov=args.nesterov,
        )
    elif args.optimizer == "adam":
        optimizer = torch.optim.Adam(
            filter(lambda p: p.requires_grad, model.parameters()), lr=args.lr
        )

    return optimizer
def adjust_learning_rate(optimizer, epoch, step, len_epoch):
    # Warmup
    if args.lr_policy == 'step':
        factor = epoch // 8
        #if epoch >= 5:
        #    factor = factor + 1
        lr = args.lr * (0.1 ** factor)
    elif args.lr_policy == 'cos':
        lr = 0.5 * args.lr * (1 + math.cos(math.pi * epoch / args.num_epochs))
    elif args.lr_policy == 'exp':
        step = 1
        decay = 0.96
        lr = args.lr * (decay ** (epoch // step))
    elif args.lr_policy == 'fixed':
        lr = args.lr
    else:
        raise NotImplementedError

    if epoch < args.warmup_length:
        lr = lr * float(1 + step + epoch * len_epoch) / (5. * len_epoch)
    if step == 0:
        print('current learning rate:{0}'.format(lr))
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr

if __name__ == '__main__':
    main()