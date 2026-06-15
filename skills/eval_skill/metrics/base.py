"""metric 基类与 result。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..core.config import MetricSpec
from ..core.judge import Judge
from ..core.runner import RunOutput
from ..core.sample import Sample


@dataclass
class MetricResult:
    name: str                                  # column 名（alias or name）
    value: float
    reason: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class Metric:
    """指标基类。所有指标都从 MetricSpec 读取参数。

    ``compute`` 接口约定：
      - sample / output / judge 是常规入参
      - is_baseline=False 表示当前模型是 model_under_test；evaluator 在评测 baseline 时
        会把它置 True；新增指标如果不关心可以忽略
      - 其他扩展参数走 ``**_kwargs``，向后兼容
    """
    name: str = "base"
    needs_judge: bool = False

    def __init__(self, spec: MetricSpec):
        self.spec = spec

    @property
    def column(self) -> str:
        return self.spec.column_name

    def compute(self, sample: Sample, output: RunOutput,
                judge: Optional[Judge] = None,
                is_baseline: bool = False,
                **_kwargs: Any) -> MetricResult:
        raise NotImplementedError
