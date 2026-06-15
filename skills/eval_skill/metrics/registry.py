"""指标注册表。"""
from __future__ import annotations

from typing import Dict, List, Type

from ..core.config import MetricSpec
from .base import Metric
from .objective import (
    Accuracy, ArrayF1, ArrayRecall, Contains, ExactMatch, NumericMatch,
)
from .rvec import RVECJudge
from .subjective import (
    CustomJudge, FactualityScore, ReasoningQuality, RubricJudge,
)

_REGISTRY: Dict[str, Type[Metric]] = {
    Accuracy.name: Accuracy,
    ExactMatch.name: ExactMatch,
    Contains.name: Contains,
    ArrayRecall.name: ArrayRecall,
    ArrayF1.name: ArrayF1,
    NumericMatch.name: NumericMatch,
    ReasoningQuality.name: ReasoningQuality,
    FactualityScore.name: FactualityScore,
    RubricJudge.name: RubricJudge,
    CustomJudge.name: CustomJudge,
    RVECJudge.name: RVECJudge,
}


def build_metrics(specs: List[MetricSpec]) -> List[Metric]:
    out: List[Metric] = []
    seen_columns = set()
    for s in specs:
        if s.name not in _REGISTRY:
            raise KeyError(f"unknown metric: {s.name}; known={list(_REGISTRY)}")
        col = s.column_name
        if col in seen_columns:
            raise ValueError(
                f"重复的指标列 '{col}'；同 name 多变体共存时请用 alias 区分"
            )
        seen_columns.add(col)
        out.append(_REGISTRY[s.name](s))
    return out


def register(metric_cls: Type[Metric]) -> None:
    _REGISTRY[metric_cls.name] = metric_cls


def list_known() -> List[str]:
    return sorted(_REGISTRY)
