"""
 Copyright (c) 2019-2022 Intel Corporation
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
import itertools
import re
from collections import namedtuple
from functools import partial
from typing import List
from typing import Tuple

import pytest
import torch

from torch import nn
import torch.utils.data
from pytest import approx
from torch.utils.data import DataLoader
from torchvision.models import squeezenet1_1

from nncf.common.graph import NNCFNodeName
from nncf.common.quantization.initialization.range import PerLayerRangeInitConfig
from nncf.common.quantization.initialization.range import RangeInitConfig
from nncf.common.quantization.quantizer_setup import ActivationQuantizationInsertionPoint
from nncf.common.quantization.quantizer_setup import SingleConfigQuantizationPoint
from nncf.common.quantization.quantizer_setup import WeightQuantizationInsertionPoint
from nncf.common.quantization.structs import QuantizationMode
from nncf.common.quantization.structs import QuantizerConfig
from nncf.common.quantization.structs import QuantizerGroup
from nncf.config import NNCFConfig
from nncf.config.structures import QuantizationRangeInitArgs
from nncf.torch import utils
from nncf.torch.checkpoint_loading import load_state
from nncf.torch.initialization import DefaultInitializingDataLoader
from nncf.torch.initialization import wrap_dataloader_for_init
from nncf.torch.nncf_network import EXTERNAL_QUANTIZERS_STORAGE_NAME
from nncf.torch.quantization.init_range import PTRangeInitParams
from nncf.torch.quantization.init_range import PTRangeInitCollectorParams
from nncf.torch.quantization.init_range import StatCollectorGenerator
from nncf.torch.quantization.layers import AsymmetricQuantizer
from nncf.torch.quantization.layers import BaseQuantizer
from nncf.torch.quantization.layers import PTQuantizerSpec
from nncf.torch.quantization.layers import QUANTIZATION_MODULES
from nncf.torch.quantization.layers import SymmetricQuantizer
from nncf.torch.tensor_statistics.collectors import PTMeanMinMaxStatisticCollector
from nncf.torch.tensor_statistics.collectors import PTMedianMADStatisticCollector
from nncf.torch.tensor_statistics.collectors import PTMinMaxStatisticCollector
from nncf.torch.tensor_statistics.statistics import pt_convert_stat_to_min_max_tensor_stat
from nncf.torch.utils import get_all_modules_by_type
from nncf.torch.utils import safe_thread_call
from tests.torch.helpers import TwoConvTestModel
from tests.torch.helpers import create_compressed_model_and_algo_for_test
from tests.torch.helpers import create_ones_mock_dataloader
from tests.torch.helpers import get_empty_config
from tests.torch.helpers import register_bn_adaptation_init_args
from tests.torch.quantization.quantization_helpers import compare_multi_gpu_dump
from tests.torch.quantization.quantization_helpers import create_rank_dataloader
from tests.torch.quantization.quantization_helpers import distributed_init_test_default
from tests.torch.quantization.quantization_helpers import get_squeezenet_quantization_config
from tests.torch.quantization.quantization_helpers import post_compression_test_distr_init

# pylint:disable=unused-import


def scale_signed_dumping_worker(gpu, ngpus_per_node, config, tmp_path):
    distributed_init_test_default(gpu, ngpus_per_node, config)
    data_loader = create_rank_dataloader(config, gpu)
    model = safe_thread_call(partial(squeezenet1_1, pretrained=True))

    config.register_extra_structs([QuantizationRangeInitArgs(wrap_dataloader_for_init(data_loader))])
    quant_model, compression_ctrl = create_compressed_model_and_algo_for_test(model, config)
    compression_scheduler = compression_ctrl.scheduler

    quant_model = post_compression_test_distr_init(compression_ctrl, config, ngpus_per_node, quant_model)

    criterion = torch.nn.MSELoss().cuda(config.gpu)
    optimizer = torch.optim.Adam(quant_model.parameters(), lr=0.01)

    torch.backends.cudnn.benchmark = True

    # just to reproduce the same scale values without Dropout
    quant_model.eval()

    act_sum = 0
    for layer in get_all_modules_by_type(quant_model, "SymmetricQuantizer").values():
        act_sum += layer.scale.sum()
    ref_sum = 3720.864
    assert act_sum.item() == approx(ref_sum, 0.01), \
        'sum of scales is not expected {} vs {} rank {}'.format(act_sum.item(), ref_sum, config.rank)

    out_file_path = get_path_after_broadcast(tmp_path, config.rank)
    save_params(quant_model, out_file_path)
    compression_scheduler.step()
    for i, (input_, _) in enumerate(data_loader):
        if i > 5:
            break
        output = quant_model(input_)
        optimizer.zero_grad()
        dummy_target = torch.randn(1000).cuda(config.gpu, non_blocking=True)
        loss = criterion(output, dummy_target)
        compression_scheduler.step()
        loss.backward()
        optimizer.step()
        compression_scheduler.step()

    out_file_path = get_path_path_after_train_iters(tmp_path, config.rank)
    save_params(quant_model, out_file_path)


def get_path_path_after_train_iters(tmp_path, rank):
    out_file_path = tmp_path / 'scale_signed_after_1_train_iter_gpu{}.pt'.format(rank)
    return out_file_path


def get_path_after_broadcast(tmp_path, rank):
    out_file_path = tmp_path / 'scale_signed_after_broadcast_gpu{}.pt'.format(rank)
    return out_file_path


def save_params(model, out_file_path):
    gpu_scale_signed_params = []
    for _, layer in utils.get_all_modules_by_type(model, 'SymmetricQuantizer').items():
        gpu_scale_signed_params.append((layer.scale.to(torch.device('cpu')),
                                        layer.signed_tensor.to(torch.device('cpu'))))
    with out_file_path.open('wb') as out_file:
        torch.save(gpu_scale_signed_params, out_file)


def test_multiprocessing_distributed_shares_init_scales_signedness_across_gpus(tmp_path, runs_subprocess_in_precommit):
    if not torch.cuda.is_available():
        pytest.skip("Skipping CUDA test cases for CPU only setups")
    num_init_samples = 10

    config = get_squeezenet_quantization_config()
    config['compression']['initializer'] = {'range': {'num_init_samples': num_init_samples}}

    ngpus_per_node = torch.cuda.device_count()
    config.world_size = ngpus_per_node
    register_bn_adaptation_init_args(config)
    torch.multiprocessing.spawn(scale_signed_dumping_worker,
                                nprocs=ngpus_per_node,
                                args=(ngpus_per_node, config, tmp_path),
                                join=True)

    assert not compare_multi_gpu_dump(config, tmp_path, get_path_after_broadcast)
    assert not compare_multi_gpu_dump(config, tmp_path, get_path_path_after_train_iters)


def create_empty_config_without_init_section():
    config = get_empty_config()
    config['compression'] = {'algorithm': 'quantization'}
    register_bn_adaptation_init_args(config)
    return config


def create_config():
    config = get_empty_config()
    config['compression'] = {'algorithm': 'quantization', 'initializer': {'range': {'num_init_samples': 1}}}
    register_bn_adaptation_init_args(config)
    return config


def generate_qp(node_name: NNCFNodeName,
                target: QuantizerGroup,
                input_port_id: int = None) -> SingleConfigQuantizationPoint:
    if target is QuantizerGroup.WEIGHTS:
        qip = WeightQuantizationInsertionPoint(target_node_name=node_name)
    elif target is QuantizerGroup.ACTIVATIONS:
        qip = ActivationQuantizationInsertionPoint(target_node_name=node_name, input_port_id=input_port_id)
    else:
        raise RuntimeError()
    return SingleConfigQuantizationPoint(qip, QuantizerConfig(), [node_name])


@pytest.mark.parametrize("wrap_dataloader",
                         [True],
                         ids=['wrapped_dataloader'])
class TestRangeInit:
    @staticmethod
    def create_algo_and_compressed_model(config):
        model = TwoConvTestModel()
        compressed_model, algo = create_compressed_model_and_algo_for_test(model, config)
        return algo, compressed_model

    @staticmethod
    def create_dataloader(wrap_dataloader, config, num_samples=1) -> DataLoader:
        data_loader = create_ones_mock_dataloader(config, num_samples)
        if wrap_dataloader:
            data_loader = DefaultInitializingDataLoader(data_loader)
        return data_loader

    @staticmethod
    def check_sign_and_scale(model, ref_table):
        model_conv = get_all_modules_by_type(model, 'SymmetricQuantizer')
        for scope, module in model_conv.items():
            for pattern, ref_values in ref_table.items():
                match = re.search(pattern, str(scope))
                if match:
                    assert isinstance(module, SymmetricQuantizer)
                    assert module.signed == ref_values[0], 'sign is not matched for {}'.format(str(scope))
                    assert all(module.scale == ref_values[1]), 'scale is not matched for {}'.format(str(scope))

    @pytest.mark.parametrize("config_creator", (create_config, create_empty_config_without_init_section))
    def test_scale_and_sign_init_for_quant_algo__without_init_section(self, wrap_dataloader, config_creator):
        config = config_creator()
        data_loader = self.create_dataloader(wrap_dataloader, config)
        config.register_extra_structs([QuantizationRangeInitArgs(data_loader)])
        _, compressed_model = self.create_algo_and_compressed_model(config)

        self.check_sign_and_scale(compressed_model, {
            '.*Sequential\\[0\\].*UpdateWeight.*': (True, torch.ones(2, 1, 1, 1)),
            '.*Sequential\\[1\\].*UpdateWeight. *': (True, 1),
            '.*activation_quantizers.*Sequential\\[0\\].*': (True, 4),
            '.*activation_quantizers.*nncf_model_input*': (False, 1)
        })

    def test_scale_and_sign_init_for_quant_algo__with_zero_init_steps(self, wrap_dataloader):
        config = create_config()
        config['compression']['initializer']['range']['num_init_samples'] = 0

        data_loader = self.create_dataloader(wrap_dataloader, config)
        config.register_extra_structs([QuantizationRangeInitArgs(data_loader)])
        _, compressed_model = self.create_algo_and_compressed_model(config)

        self.check_sign_and_scale(compressed_model, {
            '.*Sequential\\[0\\].*UpdateWeight.*': (True, torch.ones(2, 1, 1, 1)),
            '.*Sequential\\[1\\].*UpdateWeight. *': (True, 1),
            '.*activation_quantizers.*Sequential\\[0\\].*': (False, 1),
            '.*activation_quantizers.*nncf_model_input*': (False, 1)
        })

    def test_scale_and_sign_init_for_quant_algo__after_load_state(self, wrap_dataloader):
        config = create_config()
        data_loader = self.create_dataloader(wrap_dataloader, config)
        config.register_extra_structs([QuantizationRangeInitArgs(data_loader)])
        _, compressed_model = self.create_algo_and_compressed_model(config)
        ref_loaded_scale_val = torch.ones((1, 1, 1, 1)) * 100
        load_state(compressed_model, {
            'module.features.0.0.pre_ops.0.op.signed_tensor': torch.tensor([0.]),  # quantizer of 1st conv's weights
            'module.features.1.0.pre_ops.0.op.scale': ref_loaded_scale_val  # quantizer of 2nd conv's weights
        })

        self.check_sign_and_scale(compressed_model, {
            '.*Sequential\\[0\\].*UpdateWeight.*': (False, torch.ones(2, 1, 1, 1)),
            '.*Sequential\\[1\\].*UpdateWeight. *': (True, ref_loaded_scale_val),
            '.*activation_quantizers.*Sequential\\[0\\].*': (True, 4),
            '.*activation_quantizers.*nncf_model_input*': (False, 1)
        })

    def test_scope_overrides(self, wrap_dataloader):
        config = create_config()
        config['target_device'] = 'TRIAL'
        config["compression"]["scope_overrides"] = {
            "weights": {
                r"{re}NNCFConv2d\[[0-9]*\]/conv2d_0": {
                    "bits": 7,
                    "mode": "asymmetric",
                },
            },
            "activations": {
                r"{re}NNCFConv2d\[[0-9]*\]/conv2d_0": {
                    "bits": 7,
                    "signed": False,
                }
            }
        }
        data_loader = self.create_dataloader(wrap_dataloader, config)
        config.register_extra_structs([QuantizationRangeInitArgs(data_loader)])
        _, compressed_model = self.create_algo_and_compressed_model(config)

        quantizers = get_all_modules_by_type(compressed_model, ['SymmetricQuantizer',
                                                                'AsymmetricQuantizer'])
        quantizer_str_dict = {str(k): v for k, v in quantizers.items()}
        group_1 = [quantizer_str_dict["NNCFNetwork/TwoConvTestModel[nncf_module]/Sequential[features]/"
                                      "Sequential[0]/NNCFConv2d[0]/ModuleDict[pre_ops]/UpdateWeight[0]/"
                                      "AsymmetricQuantizer[op]"],
                   quantizer_str_dict["NNCFNetwork/TwoConvTestModel[nncf_module]/Sequential[features]/"
                                      "Sequential[1]/NNCFConv2d[0]/ModuleDict[pre_ops]/UpdateWeight[0]/"
                                      "AsymmetricQuantizer[op]"]
                   ]
        group_2 = [quantizer_str_dict[f"NNCFNetwork/ModuleDict[{EXTERNAL_QUANTIZERS_STORAGE_NAME}]/"
                                      "SymmetricQuantizer[TwoConvTestModel/Sequential[features]"
                                      "/Sequential[0]/NNCFConv2d[0]/conv2d_0|OUTPUT]"],
                   quantizer_str_dict[f"NNCFNetwork/ModuleDict[{EXTERNAL_QUANTIZERS_STORAGE_NAME}]/SymmetricQuantizer"
                                      "[/nncf_model_input_0|OUTPUT]"],
                   ]

        for quantizer in group_1:
            assert isinstance(quantizer, AsymmetricQuantizer)
            assert quantizer.levels == 2 ** 7
        for quantizer in group_2:
            assert isinstance(quantizer, SymmetricQuantizer)
            assert not quantizer.signed

    PerLayerRangeInitTestStruct = namedtuple('PerLayerRangeInitTestStruct',
                                             ('range_init_config',
                                              'qps_vs_expected_init_config'))


    PER_LAYER_RANGE_INIT_TEST_CASES = [
        PerLayerRangeInitTestStruct(
            range_init_config=[{
                "type": "min_max",
                "num_init_samples": 1,
                "target_scopes": ["{re}.*"]
            }],
            qps_vs_expected_init_config=[
                (
                    generate_qp("/nncf_model_input_0",
                                QuantizerGroup.ACTIVATIONS,
                                ),
                    RangeInitConfig(init_type="min_max", num_init_samples=1)
                ),
                (
                    generate_qp("TwoConvTestModel/Sequential[features]/Sequential[0]/NNCFConv2d[0]/conv2d_0",
                                QuantizerGroup.ACTIVATIONS),
                    RangeInitConfig(init_type="min_max", num_init_samples=1)
                ),
                (
                    generate_qp("TwoConvTestModel/Sequential[features]/Sequential[1]/NNCFConv2d[0]/conv2d_0",
                                QuantizerGroup.WEIGHTS),
                    RangeInitConfig(init_type="min_max", num_init_samples=1),
                )]
        ),
        PerLayerRangeInitTestStruct(
            range_init_config=[{
                "type": "min_max",
                "num_init_samples": 1,
                "target_scopes": ["{re}TwoConvTestModel/Sequential\\[features\\]/.*"]
            }, {
                "type": "mean_min_max",
                "num_init_samples": 2,
                "ignored_scopes": ["{re}TwoConvTestModel/Sequential\\[features\\]/.*"]
            }],
            qps_vs_expected_init_config=[
                (
                    generate_qp("/nncf_model_input_0", QuantizerGroup.ACTIVATIONS),
                    RangeInitConfig(init_type="mean_min_max", num_init_samples=2)
                ),
                (
                    generate_qp("TwoConvTestModel/"
                                "Sequential[features]/Sequential[0]/NNCFConv2d[0]/conv2d_0",
                                QuantizerGroup.ACTIVATIONS),
                    RangeInitConfig(init_type="min_max", num_init_samples=1)
                ),
                (
                    generate_qp("TwoConvTestModel/"
                                "Sequential[features]/Sequential[0]/NNCFConv2d[0]/conv2d_0",
                                QuantizerGroup.WEIGHTS),
                    RangeInitConfig(init_type="min_max", num_init_samples=1)
                ),
                (
                    generate_qp("TwoConvTestModel/"
                                "Sequential[features]/Sequential[1]/NNCFConv2d[0]/conv2d_0",
                                QuantizerGroup.ACTIVATIONS),
                    RangeInitConfig(init_type="min_max", num_init_samples=1)
                ),
            ]),
        PerLayerRangeInitTestStruct(
            range_init_config=[
                {
                    "type": "min_max",
                    "num_init_samples": 1,
                    "target_quantizer_group": "weights",
                    "target_scopes": ["{re}TwoConvTestModel/Sequential\\[features\\]/.*"]
                },
                {
                    "type": "mean_min_max",
                    "num_init_samples": 2,
                    "ignored_scopes": ["{re}TwoConvTestModel/Sequential\\[features\\]/.*",
                                       "{re}/nncf_model_input_0"]
                },
                {
                    "type": "threesigma",
                    "num_init_samples": 1,
                    "target_quantizer_group": "activations",
                    "target_scopes": ["{re}/nncf_model_input_0"]
                },
                {
                    "type": "percentile",
                    "num_init_samples": 10,
                    "params": {
                        "min_percentile": "0.1",
                        "max_percentile": "99.9"
                    },
                    "target_quantizer_group": "activations",
                    "target_scopes": [
                        "TwoConvTestModel/Sequential[features]/Sequential[1]/NNCFConv2d[0]/conv2d_0|OUTPUT"]
                }
            ],
            qps_vs_expected_init_config=[
                (
                    generate_qp("/nncf_model_input_0", QuantizerGroup.ACTIVATIONS),
                    RangeInitConfig(init_type="threesigma", num_init_samples=1)
                ),
                (
                    generate_qp("TwoConvTestModel/"
                                "Sequential[features]/Sequential[0]/NNCFConv2d[0]/conv2d_0",
                                QuantizerGroup.WEIGHTS),
                    RangeInitConfig(init_type="min_max", num_init_samples=1)
                ),
                (
                    generate_qp("TwoConvTestModel/"
                                "Sequential[features]/Sequential[1]/NNCFConv2d[0]/conv2d_0",
                                QuantizerGroup.ACTIVATIONS),
                    RangeInitConfig(init_type="percentile", num_init_samples=10,
                                    init_type_specific_params={
                                        "min_percentile": "0.1",
                                        "max_percentile": "99.9"
                                    })
                ),
            ])
    ]

    @staticmethod
    @pytest.fixture(params=PER_LAYER_RANGE_INIT_TEST_CASES)
    def per_layer_range_init_test_struct(request):
        return request.param

    def test_get_init_config_for_quantization_point(self, wrap_dataloader, per_layer_range_init_test_struct):
        per_layer_configs = []
        for sub_init_range_config_dict in per_layer_range_init_test_struct.range_init_config:
            per_layer_configs.append(PerLayerRangeInitConfig.from_dict(sub_init_range_config_dict))

        params = PTRangeInitParams(wrap_dataloader,
                                   '',
                                   global_init_config=None,
                                   per_layer_range_init_configs=per_layer_configs)

        for qp, ref_range_init_config in per_layer_range_init_test_struct.qps_vs_expected_init_config:
            assert params.get_init_config_for_quantization_point(qp) == ref_range_init_config

    @pytest.mark.parametrize('quant_type', ('symmetric', 'asymmetric'))
    def test_ad_hoc_range_init_does_not_replace_parameter_tensors(self, wrap_dataloader, quant_type):
        config = create_config()
        config["compression"].update(
            {
                "activations": {
                    "mode": quant_type
                },
                "weights": {
                    "mode": quant_type
                }
            }
        )

        data_loader = self.create_dataloader(wrap_dataloader, config)
        config.register_extra_structs([QuantizationRangeInitArgs(data_loader)])

        model = TwoConvTestModel()
        quant_model, quant_ctrl = create_compressed_model_and_algo_for_test(model, config)
        param_name_vs_id = {name: id(tnsr) for name, tnsr in quant_model.named_parameters()}

        quant_ctrl.init_range()

        for name, param in quant_model.named_parameters():
            assert param_name_vs_id[name] == id(param)


class SingleConv2dIdentityModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.conv2d = nn.Conv2d(3, 3, 1)
        self.conv2d.weight = torch.nn.Parameter(torch.ones_like(self.conv2d.weight))

    def forward(self, input_):
        return self.conv2d(input_)


class SingleConv2dSyntheticWeightModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.conv2d = nn.Conv2d(3, 3, 100)

        with torch.no_grad():
            for i in range(0, 100):
                for j in range(0, 100):
                    self.conv2d.weight[0][0][i][j] = i * 100 + j

            for i in range(0, 3):
                for j in range(0, 3):
                    if not (i == 0 and j == 0):
                        self.conv2d.weight[i][j] = self.conv2d.weight[0][0]
                        self.conv2d.weight[i][j] = self.conv2d.weight[0][0]

    def forward(self, input_):
        return self.conv2d(input_)


def init_idfn(val):
    if isinstance(val, tuple):
        return val[0]
    return val


@pytest.mark.parametrize("quantization_mode, per_channel, range_init_type_vs_ref_vals",
                         itertools.product(["symmetric", "asymmetric"],
                                           [True, False],
                                           [("min_max", 9999, 0, 9999),
                                            ("mixed_min_max", 9999, 0, 9999),
                                            ("mean_min_max", 9999, 0, 9999),
                                            ("threesigma", 16119.5, -6119.5, 22239),
                                            ("percentile", 6789, 3210, 3578)]), ids=init_idfn)
def test_init_ranges_are_set(quantization_mode: str, per_channel: bool,
                             range_init_type_vs_ref_vals: Tuple[str, float, float, float]):
    class SyntheticDataset(torch.utils.data.Dataset):
        def __init__(self):
            super().__init__()
            self._length = 1

        def __getitem__(self, idx):
            if idx >= self._length:
                raise StopIteration
            test_input_sample = torch.zeros([3, 100, 100])
            for i in range(0, 100):
                for j in range(0, 100):
                    test_input_sample[0][i][j] = i * 100 + j
            test_input_sample[1] = test_input_sample[0]
            test_input_sample[2] = test_input_sample[0]
            return test_input_sample, test_input_sample

        def __len__(self):
            return self._length

    data_loader = torch.utils.data.DataLoader(SyntheticDataset(), batch_size=1, drop_last=True)

    range_init_type = range_init_type_vs_ref_vals[0]
    config_with_init = NNCFConfig()
    config_with_init.update(
        {
            "input_info": {
                "sample_size": [1, 3, 100, 100]
            },
            "target_device": "TRIAL",
            "compression": {
                "algorithm": "quantization",
                "activations": {
                    "mode": quantization_mode,
                    "per_channel": per_channel
                },
                "weights": {
                    "mode": quantization_mode,
                    "per_channel": per_channel
                },
                "initializer": {
                    "range": {
                        "num_init_samples": 1,
                        "type": range_init_type
                    }
                }
            }
        }
    )

    if range_init_type == "percentile":
        config_with_init["compression"]["initializer"]["range"]["params"] = {
            "min_percentile": 32.10,
            "max_percentile": 67.89
        }

    # Activations init check
    id_model = SingleConv2dIdentityModel()
    config_with_init.register_extra_structs([QuantizationRangeInitArgs(wrap_dataloader_for_init(data_loader))])
    register_bn_adaptation_init_args(config_with_init)
    _, compression_ctrl = create_compressed_model_and_algo_for_test(id_model, config_with_init)

    act_quantizer_info = next(iter(compression_ctrl.non_weight_quantizers.values()))

    ref_scale = range_init_type_vs_ref_vals[1]
    ref_input_low = range_init_type_vs_ref_vals[2]
    ref_input_high = range_init_type_vs_ref_vals[3]

    def check_scales(quantizer: BaseQuantizer, per_channel: bool):
        # Absolute tolerance is 1.0 due to percentile value interpolation
        if quantization_mode == 'symmetric':
            assert torch.allclose(quantizer.scale, torch.ones_like(quantizer.scale) * ref_scale, atol=1.0)
            if per_channel:
                assert quantizer.scale.numel() == 3
            else:
                assert quantizer.scale.numel() == 1
        else:
            assert torch.allclose(quantizer.input_low, torch.ones_like(quantizer.input_low) * ref_input_low, atol=1.0)
            assert torch.allclose(quantizer.input_range, torch.ones_like(quantizer.input_low) * ref_input_high,
                                  atol=1.0)
            if per_channel:
                assert quantizer.input_low.numel() == 3
                assert quantizer.input_range.numel() == 3
            else:
                assert quantizer.input_low.numel() == 1
                assert quantizer.input_range.numel() == 1

    check_scales(act_quantizer_info.quantizer_module_ref, per_channel)
    # Weight init check
    synth_weight_model = SingleConv2dSyntheticWeightModel()
    _, compression_ctrl = create_compressed_model_and_algo_for_test(synth_weight_model,
                                                                    config_with_init)

    weight_quantizer_info = next(iter(compression_ctrl.weight_quantizers.values()))
    check_scales(weight_quantizer_info.quantizer_module_ref, per_channel)


RangeInitCallCountTestStruct = namedtuple('RangeInitCallCountTestStruct',
                                          ('range_init_config',
                                           'expected_call_count_initializer_create',
                                           'expected_call_count_register_input',))
RANGE_INIT_CALL_COUNT_TEST_CASES = [
    RangeInitCallCountTestStruct(
        range_init_config={
            "type": "min_max",
            "num_init_samples": 5
        },
        expected_call_count_initializer_create={
            'min_max': 4,
            'mean_min_max': 0,
            'three_sigma': 0
        },
        expected_call_count_register_input={
            'min_max': 12,  # 2 activation statistics for 5x inputs, 2 weight statistics for 1 input each
            'mean_min_max': 0,
            'three_sigma': 0
        }
    ),
    RangeInitCallCountTestStruct(
        range_init_config=[{
            "type": "min_max",
            "num_init_samples": 5,
            "target_quantizer_group": "weights",
            "target_scopes": ["{re}TwoConvTestModel/Sequential\\[features\\]/.*"]
        }, {
            "type": "mean_min_max",
            "num_init_samples": 2,
            "ignored_scopes": ["{re}TwoConvTestModel/Sequential\\[features\\]/.*"]
        }, {
            "type": "threesigma",
            "num_init_samples": 3,
            "target_quantizer_group": "activations",
            "target_scopes": ["{re}TwoConvTestModel/Sequential\\[features\\]/.*"]
        }],
        expected_call_count_initializer_create={
            'min_max': 2,
            'mean_min_max': 1,
            'three_sigma': 1
        },
        expected_call_count_register_input={
            'min_max': 2,  # Weights only require single input registration
            'mean_min_max': 2,
            'three_sigma': 3
        }
    )
]


@pytest.fixture(params=RANGE_INIT_CALL_COUNT_TEST_CASES)
def range_init_call_count_test_struct(request):
    return request.param


# pylint:disable=redefined-outer-name
def test_per_layer_range_init_collectors_are_called_the_required_number_of_times(range_init_call_count_test_struct,
                                                                                 mocker):
    config = create_config()
    config['compression']['initializer']['range'] = range_init_call_count_test_struct.range_init_config
    data_loader = TestRangeInit.create_dataloader(True, config, 10)
    config.register_extra_structs([QuantizationRangeInitArgs(data_loader)])

    range_minmax_init_create_spy = mocker.spy(PTMinMaxStatisticCollector, '__init__')
    range_meanminmax_init_create_spy = mocker.spy(PTMeanMinMaxStatisticCollector, '__init__')
    range_threesigma_init_create_spy = mocker.spy(PTMedianMADStatisticCollector, '__init__')

    range_minmax_init_register_input_spy = mocker.spy(PTMinMaxStatisticCollector, '_register_input')
    range_meanminmax_init_register_input_spy = mocker.spy(PTMeanMinMaxStatisticCollector, '_register_input')
    range_threesigma_init_register_input_spy = mocker.spy(PTMedianMADStatisticCollector, '_register_input')

    TestRangeInit.create_algo_and_compressed_model(config)

    assert range_minmax_init_create_spy.call_count == \
           range_init_call_count_test_struct.expected_call_count_initializer_create['min_max']
    assert range_meanminmax_init_create_spy.call_count == \
           range_init_call_count_test_struct.expected_call_count_initializer_create['mean_min_max']
    assert range_threesigma_init_create_spy.call_count == \
           range_init_call_count_test_struct.expected_call_count_initializer_create['three_sigma']

    assert range_minmax_init_register_input_spy.call_count == \
           range_init_call_count_test_struct.expected_call_count_register_input['min_max']
    assert range_meanminmax_init_register_input_spy.call_count == \
           range_init_call_count_test_struct.expected_call_count_register_input['mean_min_max']
    assert range_threesigma_init_register_input_spy.call_count == \
           range_init_call_count_test_struct.expected_call_count_register_input['three_sigma']


QUANTIZER_RANGE_INITIALIZERS = ["min_max", "threesigma", "mean_min_max", "percentile", "mixed_min_max"]


class QuantizeRangeInitScaleShapeTestStruct:
    def __init__(self, per_channel: bool, is_weights: bool,
                 input_shape: List[int], ref_scale_shape: Tuple[int, ...]):
        self.per_channel = per_channel
        self.is_weights = is_weights
        self.input_shape = input_shape
        self.ref_scale_shape = ref_scale_shape


QRISSTS = QuantizeRangeInitScaleShapeTestStruct

QUANTIZER_RANGE_INIT_TEST_CASES = [
    QRISSTS(per_channel=False,
            is_weights=False,
            input_shape=[41, 42, 43, 44],
            ref_scale_shape=(1,)),
    QRISSTS(per_channel=True,
            is_weights=False,
            input_shape=[41, 42, 43, 44],
            ref_scale_shape=(1, 42, 1, 1)),
    QRISSTS(per_channel=False,
            is_weights=True,
            input_shape=[41, 42, 43, 44],
            ref_scale_shape=(1,)),
    QRISSTS(per_channel=True,
            is_weights=True,
            input_shape=[41, 42, 43, 44],
            ref_scale_shape=(41, 1, 1, 1)),
]


def quantizer_range_init_scale_shape_idfn(fixture_value):
    test_struct = fixture_value[0]  # type: QRISSTS
    postfix = ""
    if test_struct.is_weights:
        postfix += "-W"
    else:
        postfix += "-A"

    if test_struct.per_channel:
        postfix += "-PC"
    else:
        postfix += "-PT"
    return fixture_value[1] + postfix


@pytest.fixture(params=itertools.product(QUANTIZER_RANGE_INIT_TEST_CASES, QUANTIZER_RANGE_INITIALIZERS),
                ids=quantizer_range_init_scale_shape_idfn)
def quantizer_range_init_test_struct(request):
    return request.param


def test_quantize_range_init_sets_correct_scale_shapes(quantizer_range_init_test_struct: Tuple[QRISSTS, str]):
    test_struct = quantizer_range_init_test_struct[0]
    initializer_type = quantizer_range_init_test_struct[1]
    for quantization_mode in [QuantizationMode.SYMMETRIC, QuantizationMode.ASYMMETRIC]:
        qconfig = PTQuantizerSpec(num_bits=8,
                                  mode=quantization_mode,
                                  signedness_to_force=None,
                                  scale_shape=tuple(test_struct.ref_scale_shape),
                                  narrow_range=test_struct.is_weights,
                                  half_range=False,
                                  logarithm_scale=False)
        q_cls = QUANTIZATION_MODULES.get(quantization_mode)
        quantizer = q_cls(qconfig)  # type: BaseQuantizer
        range_init_config = RangeInitConfig(init_type=initializer_type, num_init_samples=1)

        if test_struct.is_weights:
            channel_idx = 0  # channel dim for weights
        else:
            channel_idx = 1  # channel dim for activations

        collector_params = PTRangeInitCollectorParams(test_struct.is_weights,
                                                      quantization_mode,
                                                      test_struct.per_channel,
                                                      tuple(test_struct.input_shape),
                                                      channel_idx)

        collector = StatCollectorGenerator.generate_stat_collector_for_range_init_config(
            range_init_config,
            tuple(quantizer.scale_shape),
            collector_params)
        collector.register_input(torch.ones(test_struct.input_shape))
        stat = collector.get_statistics()
        minmax_values = pt_convert_stat_to_min_max_tensor_stat(stat)
        quantizer.apply_minmax_init(min_values=minmax_values.min_values,
                                    max_values=minmax_values.max_values)

        assert quantizer.scale_shape == test_struct.ref_scale_shape
        if quantization_mode == QuantizationMode.SYMMETRIC:
            assert tuple(quantizer.scale.shape) == test_struct.ref_scale_shape
        elif quantization_mode == QuantizationMode.ASYMMETRIC:
            assert tuple(quantizer.input_low.shape) == test_struct.ref_scale_shape
            assert tuple(quantizer.input_range.shape) == test_struct.ref_scale_shape
        else:
            assert False  # options above should be exhaustive


class AbsTwosDataset:
    def __init__(self):
        super().__init__()
        self._length = 1

    def __getitem__(self, idx):
        if idx >= self._length:
            raise StopIteration
        test_input_sample = torch.ones([3, 100, 100]) * 2
        return test_input_sample, test_input_sample

    def __len__(self):
        return self._length
