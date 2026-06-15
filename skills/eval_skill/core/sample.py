"""Sample / GroundTruth：评测路径上的统一样本对象。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

GroundTruthKind = Literal[
    "single_choice", "array", "string", "number",
    "open_ended", "dialog", "report_pair", "none",
]

# ---------------------------------------------------------------------------
# v2 → v2.1 spec 拆分：metadata.schema 一个字段塞了两个正交维度
#   - 轮次（single / multi）
#   - 评分方式（exact / numeric / array / rubric / report_pair / none）
# 升级后 metadata 同时显式带上 turn_kind 与 scoring_mode；老 dataset 没显式标
# 时下面两个表负责从 schema 推断回去（保持向后兼容，老数据不丢）。
TurnKind = Literal["single", "multi"]
ScoringMode = Literal["exact", "numeric", "array", "rubric", "report_pair", "none"]

TURN_KIND_BY_SCHEMA: Dict[str, TurnKind] = {
    "dialog": "multi",
    "single_choice": "single",
    "string": "single",
    "number": "single",
    "array": "single",
    "open_ended": "single",
    "report_pair": "single",
}

SCORING_MODE_BY_SCHEMA: Dict[str, ScoringMode] = {
    "single_choice": "exact",
    "string": "exact",
    "number": "numeric",
    "array": "array",
    "open_ended": "rubric",
    "dialog": "rubric",
    "report_pair": "report_pair",
}


@dataclass
class GroundTruth:
    kind: GroundTruthKind
    answer: Any                                  # str | list | float | None
    reasoning_ref: Optional[str] = None
    explanation: Optional[str] = None
    rubric: Optional[str] = None
    expected_md: Optional[str] = None            # 基准研报 markdown（report_pair 用）

    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnInput:
    """多轮 dataset 的一轮 user 内容。"""
    content: str
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PdfRef:
    label: str
    path: str
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Sample:
    sample_id: str
    schema: str                                  # metadata.schema 直传
    # ----- 三种输入模式（互斥但可叠加 PDF）-----
    prompt: Optional[str] = None                 # prompt-baked
    turns: List[TurnInput] = field(default_factory=list)
    fields: Dict[str, Any] = field(default_factory=dict)   # 结构化字段（含 question/options/...）
    pdf_refs: List[PdfRef] = field(default_factory=list)

    ground_truth: GroundTruth = field(default_factory=lambda: GroundTruth("none", None))
    metadata: Dict[str, Any] = field(default_factory=dict)
    attachments_md: Dict[str, str] = field(default_factory=dict)   # PDF 抽取后的 markdown
    raw: Any = None                              # 原 lumi item

    # ---- 便捷判断 ----
    @property
    def has_turns(self) -> bool:
        return bool(self.turns)

    @property
    def has_prompt(self) -> bool:
        return bool(self.prompt)

    @property
    def has_pdf(self) -> bool:
        return bool(self.pdf_refs)

    # ---- v2.1 spec：turn_kind / scoring_mode 显式化 ----
    # turn_kind 面向真实输入形态：len(turns)>=2 才是 multi；len(turns)==1 是单轮样本的兼容容器。
    # scoring_mode 优先读 metadata 显式标注；缺失时从 schema 推断。
    # 评测代码（runner / metric / view / validate）都应该用这两个 property 而非 sample.schema，
    # 这样新老 dataset 都能跑，未来去掉 schema 字段也只改 property 实现。
    @property
    def turn_kind(self) -> TurnKind:
        if self.turns:
            return "multi" if len(self.turns) >= 2 else "single"
        v = self.metadata.get("turn_kind")
        if v in ("single", "multi"):
            return v  # type: ignore[return-value]
        # fallback：从 schema 推断
        if self.schema in TURN_KIND_BY_SCHEMA:
            return TURN_KIND_BY_SCHEMA[self.schema]
        # 未知 schema：有 turns 算 multi，否则 single
        return "multi" if self.has_turns else "single"

    @property
    def scoring_mode(self) -> ScoringMode:
        v = self.metadata.get("scoring_mode")
        if v in ("exact", "numeric", "array", "rubric", "report_pair", "none"):
            return v  # type: ignore[return-value]
        # fallback：从 schema 推断
        if self.schema in SCORING_MODE_BY_SCHEMA:
            return SCORING_MODE_BY_SCHEMA[self.schema]
        return "none"
