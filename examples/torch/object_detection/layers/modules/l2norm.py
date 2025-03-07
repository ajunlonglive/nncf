"""
 Copyright (c) 2022 Intel Corporation
 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at
      http://www.apache.org/licenses/LICENSE-2.0
 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
"""
from typing import Any

import torch
from torch import nn
from torch.nn import init

from nncf.torch.utils import add_domain
from nncf.torch import register_module


@register_module(
    ignored_algorithms=['quantization', 'binarization', 'const_sparsity', 'magnitude_sparsity', 'rb_sparsity']
)
class L2Norm(nn.Module):
    def __init__(self, n_channels, scale, eps, across_spatial=0, channel_shared=0):
        super().__init__()
        self.n_channels = n_channels
        self.scale = scale or None
        self.eps = eps
        self.across_spatial = across_spatial
        self.channel_shared = channel_shared
        self.weight = nn.Parameter(torch.Tensor(self.n_channels))
        self.reset_parameters()

    def reset_parameters(self):
        init.constant_(self.weight, self.scale)

    def forward(self, x):
        if self.training:
            norm = x.pow(2).sum(dim=1, keepdim=True).sqrt() + self.eps
            x = torch.div(x, norm)
            out = self.weight.unsqueeze(0).unsqueeze(2).unsqueeze(3).expand_as(x) * x
            return out
        return L2NormFunction.apply(x, self.weight, self)


# pylint:disable=abstract-method
class L2NormFunction(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x, weight, l2NormParams):
        norm = x.pow(2).sum(dim=1, keepdim=True).sqrt() + l2NormParams.eps
        x = torch.div(x, norm)
        out = weight.unsqueeze(0).unsqueeze(2).unsqueeze(3).expand_as(x) * x
        return out

    @staticmethod
    def backward(ctx: Any, *grad_outputs: Any) -> Any:
        return grad_outputs[0]

    @staticmethod
    def symbolic(g, x, weight, l2NormParams):
        return g.op(add_domain("Normalize"), x, weight, eps_f=l2NormParams.eps,
                    across_spatial_i=l2NormParams.across_spatial, channel_shared_i=l2NormParams.channel_shared)
