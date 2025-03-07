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

from typing import List, Type, Optional
from dataclasses import dataclass

from nncf.common.graph.operator_metatypes import OperatorMetatype
from nncf.common.graph.operator_metatypes import OperatorMetatypeRegistry
from nncf.common.hardware.opset import HWConfigOpName

ONNX_OPERATION_METATYPES = OperatorMetatypeRegistry('onnx_operator_metatypes')


class ONNXOpMetatype(OperatorMetatype):
    op_names = []  # type: List[str]

    @classmethod
    def get_all_aliases(cls) -> List[str]:
        return cls.op_names


@dataclass
class OpWeightDef:
    """
    Contains the information about the weight and bias of the operation.

    :param weight_channel_axis: Axis for weight per-channel quantization, meaning the number of output filters.
    :param weight_port_id: Input port of the node's weight.
    If the value is None the weight_port_id should be determined dynamically.
    :param bias_port_id: Input port of the node's bias.
    If the value is None it means that the Metatype does not have bias.
    """
    weight_channel_axis: int
    weight_port_id: Optional[int] = None
    bias_port_id: Optional[int] = None


class ONNXOpWithWeightsMetatype(ONNXOpMetatype):
    weight_definition = None  # type: OpWeightDef


@ONNX_OPERATION_METATYPES.register()
class ONNXConvolutionMetatype(ONNXOpWithWeightsMetatype):
    name = 'ConvOp'
    op_names = ['Conv']
    hw_config_names = [HWConfigOpName.CONVOLUTION]
    weight_definitions = OpWeightDef(weight_channel_axis=0, weight_port_id=1, bias_port_id=2)


@ONNX_OPERATION_METATYPES.register()
class ONNXConvolutionTransposeMetatype(ONNXOpWithWeightsMetatype):
    name = 'ConvTransposeOp'
    op_names = ['ConvTranspose']
    hw_config_names = [HWConfigOpName.CONVOLUTION]
    weight_definitions = OpWeightDef(weight_channel_axis=1, weight_port_id=1, bias_port_id=2)


@ONNX_OPERATION_METATYPES.register()
class ONNXLinearMetatype(ONNXOpWithWeightsMetatype):
    name = 'LinearOp'
    op_names = ['Gemm']
    hw_config_names = [HWConfigOpName.MATMUL]
    # TODO(kshpv): Update weight_port_id to None and detects it dynamically
    weight_definitions = OpWeightDef(weight_channel_axis=0, weight_port_id=1, bias_port_id=2)


@ONNX_OPERATION_METATYPES.register()
class ONNXReluMetatype(ONNXOpMetatype):
    name = 'ReluOp'
    op_names = ['Relu', 'Clip']


@ONNX_OPERATION_METATYPES.register()
class ONNXLeakyReluMetatype(ONNXOpMetatype):
    name = 'LeakyReluOp'
    op_names = ['LeakyRelu']


@ONNX_OPERATION_METATYPES.register()
class ONNXThresholdedReluMetatype(ONNXOpMetatype):
    name = 'ThresholdedReluOp'
    op_names = ['ThresholdedRelu']


@ONNX_OPERATION_METATYPES.register()
class ONNXEluMetatype(ONNXOpMetatype):
    name = 'EluOp'
    op_names = ['Elu']


@ONNX_OPERATION_METATYPES.register()
class ONNXPReluMetatype(ONNXOpMetatype):
    name = 'PReluOp'
    op_names = ['PRelu']


@ONNX_OPERATION_METATYPES.register()
class ONNXSigmoidMetatype(ONNXOpMetatype):
    name = 'SigmoidOp'
    op_names = ['Sigmoid']


@ONNX_OPERATION_METATYPES.register()
class ONNXHardSigmoidMetatype(ONNXOpMetatype):
    name = 'HardSigmoidOp'
    op_names = ['HardSigmoid']


@ONNX_OPERATION_METATYPES.register()
class ONNXHardSwishMetatype(ONNXOpMetatype):
    name = 'HardSwishOp'
    op_names = ['HardSwish']


@ONNX_OPERATION_METATYPES.register()
class ONNXGlobalAveragePoolMetatype(ONNXOpMetatype):
    name = 'GlobalAveragePoolOp'
    op_names = ['GlobalAveragePool']
    hw_config_names = [HWConfigOpName.AVGPOOL]


@ONNX_OPERATION_METATYPES.register()
class ONNXAveragePoolMetatype(ONNXOpMetatype):
    name = 'AveragePoolOp'
    op_names = ['AveragePool']
    hw_config_names = [HWConfigOpName.AVGPOOL]


@ONNX_OPERATION_METATYPES.register()
class ONNXMaxPoolMetatype(ONNXOpMetatype):
    name = 'MaxPoolOp'
    op_names = ['MaxPool']
    hw_config_names = [HWConfigOpName.MAXPOOL]


@ONNX_OPERATION_METATYPES.register()
class ONNXConstantMetatype(ONNXOpMetatype):
    name = 'ConstantOp'
    op_names = ['Constant']


@ONNX_OPERATION_METATYPES.register()
class ONNXAddLayerMetatype(ONNXOpMetatype):
    name = 'AddOp'
    op_names = ['Add']
    hw_config_names = [HWConfigOpName.ADD]


@ONNX_OPERATION_METATYPES.register()
class ONNXSubMetatype(ONNXOpMetatype):
    name = 'SubOp'
    op_names = ['Sub']
    hw_config_names = [HWConfigOpName.SUBTRACT]


@ONNX_OPERATION_METATYPES.register()
class ONNXMulLayerMetatype(ONNXOpMetatype):
    name = 'MulOp'
    op_names = ['Mul']
    hw_config_names = [HWConfigOpName.MULTIPLY]


@ONNX_OPERATION_METATYPES.register()
class ONNXDivLayerMetatype(ONNXOpMetatype):
    name = 'DivOp'
    op_names = ['Div']
    hw_config_names = [HWConfigOpName.DIVIDE]


@ONNX_OPERATION_METATYPES.register()
class ONNXSumMetatype(ONNXOpMetatype):
    name = 'SumOp'
    op_names = ['Sum']
    hw_config_names = [HWConfigOpName.REDUCESUM]


@ONNX_OPERATION_METATYPES.register()
class ONNXConcatLayerMetatype(ONNXOpMetatype):
    name = 'ConcatOp'
    op_names = ['Concat']
    hw_config_names = [HWConfigOpName.CONCAT]


@ONNX_OPERATION_METATYPES.register()
class ONNXBatchNormMetatype(ONNXOpMetatype):
    name = 'BatchNormalizationOp'
    op_names = ['BatchNormalization']


@ONNX_OPERATION_METATYPES.register()
class ONNXResizeMetatype(ONNXOpMetatype):
    name = 'ResizeOp'
    op_names = ['Resize']
    hw_config_names = [HWConfigOpName.INTERPOLATE]


@ONNX_OPERATION_METATYPES.register()
class ONNXReshapeMetatype(ONNXOpMetatype):
    name = 'ReshapeOp'
    op_names = ['Reshape']
    hw_config_names = [HWConfigOpName.RESHAPE]


@ONNX_OPERATION_METATYPES.register()
class ONNXUpsampleMetatype(ONNXOpMetatype):
    name = 'UpsampleOp'
    op_names = ['Upsample']


@ONNX_OPERATION_METATYPES.register()
class ONNXConstantOfShapeMetatype(ONNXOpMetatype):
    name = 'ConstantOfShapeOp'
    op_names = ['ConstantOfShape']


@ONNX_OPERATION_METATYPES.register()
class ONNXShapeMetatype(ONNXOpMetatype):
    name = 'ShapeOp'
    op_names = ['Shape']


@ONNX_OPERATION_METATYPES.register()
class ONNXExpandMetatype(ONNXOpMetatype):
    # TODO (kshpv): hw_config_names?
    name = 'ExpandOp'
    op_names = ['Expand']


@ONNX_OPERATION_METATYPES.register()
class ONNXNonZeroMetatype(ONNXOpMetatype):
    name = 'NonZeroOp'
    op_names = ['NonZero']


@ONNX_OPERATION_METATYPES.register()
class ONNXSplitMetatype(ONNXOpMetatype):
    name = 'SplitOp'
    op_names = ['Split']
    hw_config_names = [HWConfigOpName.SPLIT]


@ONNX_OPERATION_METATYPES.register()
class ONNXLessMetatype(ONNXOpMetatype):
    name = 'LessOp'
    op_names = ['Less']
    hw_config_names = [HWConfigOpName.LESS]


@ONNX_OPERATION_METATYPES.register()
class ONNXGreaterMetatype(ONNXOpMetatype):
    name = 'GreaterOp'
    op_names = ['Greater']
    hw_config_names = [HWConfigOpName.GREATER]


@ONNX_OPERATION_METATYPES.register()
class ONNXEqualMetatype(ONNXOpMetatype):
    name = 'EqualOp'
    op_names = ['Equal']
    hw_config_names = [HWConfigOpName.EQUAL]


@ONNX_OPERATION_METATYPES.register()
class ONNXNotMetatype(ONNXOpMetatype):
    name = 'NotOp'
    op_names = ['Not']
    hw_config_names = [HWConfigOpName.LOGICALNOT]


@ONNX_OPERATION_METATYPES.register()
class ONNXAndMetatype(ONNXOpMetatype):
    name = 'AndOp'
    op_names = ['And']
    hw_config_names = [HWConfigOpName.LOGICALAND]


@ONNX_OPERATION_METATYPES.register()
class ONNXOrMetatype(ONNXOpMetatype):
    name = 'OrOp'
    op_names = ['Or']
    hw_config_names = [HWConfigOpName.LOGICALOR]


@ONNX_OPERATION_METATYPES.register()
class ONNXFloorMetatype(ONNXOpMetatype):
    name = 'FloorOp'
    op_names = ['Floor']
    hw_config_names = [HWConfigOpName.FLOORMOD]


@ONNX_OPERATION_METATYPES.register()
class ONNXSqrtMetatype(ONNXOpMetatype):
    name = 'SqrtOp'
    op_names = ['Sqrt']
    hw_config_names = [HWConfigOpName.POWER]


@ONNX_OPERATION_METATYPES.register()
class ONNXLogMetatype(ONNXOpMetatype):
    name = 'LogOp'
    op_names = ['Log']


@ONNX_OPERATION_METATYPES.register()
class ONNXScatterElementslMetatype(ONNXOpMetatype):
    name = 'ScatterElementsOp'
    op_names = ['ScatterElements']


@ONNX_OPERATION_METATYPES.register()
class ONNXRoiAlignMetatype(ONNXOpMetatype):
    name = 'RoiAlignOp'
    op_names = ['RoiAlign']


@ONNX_OPERATION_METATYPES.register()
class ONNXMatMulMetatype(ONNXOpMetatype):
    name = 'MatMulOp'
    op_names = ['MatMul']
    hw_config_names = [HWConfigOpName.MATMUL]


@ONNX_OPERATION_METATYPES.register()
class ONNXGatherMetatype(ONNXOpMetatype):
    name = 'GatherOp'
    op_names = ['Gather']


@ONNX_OPERATION_METATYPES.register()
class ONNXUnsqueezeMetatype(ONNXOpMetatype):
    name = 'UnsqueezeOp'
    op_names = ['Unsqueeze']
    hw_config_names = [HWConfigOpName.UNSQUEEZE]


@ONNX_OPERATION_METATYPES.register()
class ONNXSqueezeMetatype(ONNXOpMetatype):
    name = 'SqueezeOp'
    op_names = ['Squeeze']
    hw_config_names = [HWConfigOpName.SQUEEZE]


@ONNX_OPERATION_METATYPES.register()
class ONNXNonMaxSuppressionMetatype(ONNXOpMetatype):
    name = 'NonMaxSuppressionOp'
    op_names = ['NonMaxSuppression']


@ONNX_OPERATION_METATYPES.register()
class ONNXCastMetatype(ONNXOpMetatype):
    name = 'CastOp'
    op_names = ['Cast']
    hw_config_names = [HWConfigOpName.SQUEEZE]


@ONNX_OPERATION_METATYPES.register()
class ONNXReduceMinMetatype(ONNXOpMetatype):
    name = 'ReduceMinOp'
    op_names = ['ReduceMin']


@ONNX_OPERATION_METATYPES.register()
class ONNXReduceMeanMetatype(ONNXOpMetatype):
    name = 'ReduceMeanOp'
    op_names = ['ReduceMean']
    hw_config_names = [HWConfigOpName.REDUCEMEAN]


@ONNX_OPERATION_METATYPES.register()
class ONNXTopKMetatype(ONNXOpMetatype):
    name = 'TopKOp'
    op_names = ['TopK']


@ONNX_OPERATION_METATYPES.register()
class ONNXSliceMetatype(ONNXOpMetatype):
    name = 'SliceOp'
    op_names = ['Slice']


@ONNX_OPERATION_METATYPES.register()
class ONNXExpMetatype(ONNXOpMetatype):
    name = 'ExpOp'
    op_names = ['Exp']


@ONNX_OPERATION_METATYPES.register()
class ONNXTransposeMetatype(ONNXOpMetatype):
    name = 'TransposeOp'
    op_names = ['Transpose']
    hw_config_names = [HWConfigOpName.TRANSPOSE]


@ONNX_OPERATION_METATYPES.register()
class ONNXFlattenMetatype(ONNXOpMetatype):
    name = 'FlattenOp'
    op_names = ['Flatten']
    hw_config_names = [HWConfigOpName.FLATTEN]


@ONNX_OPERATION_METATYPES.register()
class ONNXSoftmaxMetatype(ONNXOpMetatype):
    name = 'SoftmaxOp'
    op_names = ['Softmax']


@ONNX_OPERATION_METATYPES.register()
class ONNXPadMetatype(ONNXOpMetatype):
    name = 'PadOp'
    op_names = ['Pad']


@ONNX_OPERATION_METATYPES.register()
class ONNXIdentityMetatype(ONNXOpMetatype):
    name = 'IdentityOp'
    op_names = ['Identity']


@ONNX_OPERATION_METATYPES.register()
class ONNXQuantizeLinearMetatype(ONNXOpMetatype):
    name = 'QuantizeLinearOp'
    op_names = ['QuantizeLinear']


@ONNX_OPERATION_METATYPES.register()
class ONNXDequantizeLinearMetatype(ONNXOpMetatype):
    name = 'DequantizeLinearOp'
    op_names = ['DequantizeLinear']


WEIGHT_LAYER_METATYPES = [ONNXConvolutionMetatype,
                          ONNXConvolutionTransposeMetatype,
                          ONNXLinearMetatype]

LAYERS_WITH_BIAS_METATYPES = [ONNXConvolutionMetatype,
                              ONNXConvolutionTransposeMetatype]


def get_operator_metatypes() -> List[Type[OperatorMetatype]]:
    """
    Returns a list of the operator metatypes.

    :return: List of operator metatypes .
    """
    return list(ONNX_OPERATION_METATYPES.registry_dict.values())
