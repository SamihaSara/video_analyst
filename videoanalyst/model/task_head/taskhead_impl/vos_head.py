# -*- coding: utf-8 -*

import numpy as np

import torch
import torch.nn as nn
import torch.nn.functional as F

from videoanalyst.model.common_opr.common_block import upsample_block, projector
from videoanalyst.model.module_base import ModuleBase
from videoanalyst.model.task_head.taskhead_base import VOS_HEADS

torch.set_printoptions(precision=8)


@VOS_HEADS.register
class DecoderHead(ModuleBase):
    r"""
    DecoderHead for SAT

    Hyper-parameter
    ---------------
    output_size: int
        output size of predicted mask

    """

    default_hyper_params = dict(output_size=257, )

    def __init__(self):
        super(DecoderHead, self).__init__()
        self.output_size = self._hyper_params["output_size"]
        self.upblock1 = upsample_block(512, 512, 256)
        self.upblock2 = upsample_block(256, 256, 256)
        self.upblock3 = upsample_block(256, 128, 256)
        self.upblock4 = upsample_block(256, 64, 128)
        self.out_projector = projector(128, 1)
        self.activation = nn.Sigmoid()

    def forward(self, feature_list):
        x1, x2, x3, x4, x5 = feature_list
        f_s32 = self.upblock1(x1, x2)
        f_s16 = self.upblock2(f_s32, x3)
        f_s8 = self.upblock3(f_s16, x4)
        f_s4 = self.upblock4(f_s8, x5)

        p = self.out_projector(f_s4)
        p_resize = F.interpolate(p, (self.output_size, self.output_size),
                                 mode='bilinear',
                                 align_corners=False)
        prediction = self.activation(p_resize)
        return prediction, f_s8
