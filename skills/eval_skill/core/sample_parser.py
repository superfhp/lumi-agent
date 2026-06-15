"""sample_parser：raw item (Langfuse / dict) → Sample；只认 v2 字段名。"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from .sample import GroundTruth, PdfRef, Sample, TurnInput


def _build_ground_truth(expected: Mapping[str, Any], schema: str) -> GroundTruth:
    expected = dict(expected or {})
    answer = expected.get("answer")

    kind: str
    if schema in ("single_choice", "array", "string", "number",
                  "open_ended", "dialog", "report_pair"):
        kind = schema
    else:
        kind = "none"

    # number schema 下的字符串答案运行时补一下类型规范化（以防老 dataset 没走过迁移脚本）
    if kind == "number" and isinstance(answer, str):
        try:
            answer = float(answer.replace(",", "").replace("，", ""))
        except ValueError:
            pass

    return GroundTruth(
        kind=kind,                                       # type: ignore[arg-type]
        answer=answer,
        reasoning_ref=expected.get("reasoning_ref"),
        explanation=expected.get("explanation"),
        rubric=expected.get("rubric"),
        expected_md=expected.get("expected_md"),
        raw=expected,
    )


def _parse_turns(raw_turns: Iterable[Any]) -> List[TurnInput]:
    out: List[TurnInput] = []
    for t in raw_turns or []:
        if isinstance(t, str):
            out.append(TurnInput(content=t))
        elif isinstance(t, Mapping):
            out.append(TurnInput(content=str(t.get("content", "")), meta=dict(t.get("meta", {}))))
        else:
            raise TypeError(f"unknown turn type: {type(t)}")
    return out


def _parse_pdf_refs(raw_refs: Iterable[Any]) -> List[PdfRef]:
    out: List[PdfRef] = []
    for r in raw_refs or []:
        if not isinstance(r, Mapping):
            continue
        label = str(r.get("label") or r.get("year") or r.get("path") or f"pdf_{len(out)}")
        path = str(r.get("path") or "")
        extra = {k: v for k, v in r.items() if k not in ("label", "path")}
        out.append(PdfRef(label=label, path=path, extra=extra))
    return out


def parse_sample(raw_item: Any, fallback_id: str = "") -> Sample:
    """raw_item 兼容两种来源：
    - Langfuse dataset item（有 .input / .expected_output / .metadata 属性）
    - 普通 dict（来自本地 jsonl）
    """
    # 兼容两种结构
    if isinstance(raw_item, Mapping):
        input_data = dict(raw_item.get("input") or {})
        expected = dict(raw_item.get("expected_output") or raw_item.get("expected") or {})
        metadata = dict(raw_item.get("metadata") or {})
        item_id = raw_item.get("id")
    else:
        input_data = dict(getattr(raw_item, "input", {}) or {})
        expected = dict(getattr(raw_item, "expected_output", {}) or {})
        metadata = dict(getattr(raw_item, "metadata", {}) or {})
        item_id = getattr(raw_item, "id", None)

    schema = str(metadata.get("schema") or "string")
    sample_id = str(item_id or metadata.get("sample_id") or fallback_id or "")

    # 三种模式：互斥但可与 pdf_refs 叠加
    prompt: Optional[str] = input_data.pop("prompt", None) if isinstance(input_data.get("prompt"), str) else None
    raw_turns = input_data.pop("turns", None)
    raw_pdf_refs = input_data.pop("pdf_refs", None)

    turns = _parse_turns(raw_turns) if raw_turns else []
    pdf_refs = _parse_pdf_refs(raw_pdf_refs) if raw_pdf_refs else []

    ground_truth = _build_ground_truth(expected, schema)

    return Sample(
        sample_id=sample_id,
        schema=schema,
        prompt=prompt,
        turns=turns,
        fields=input_data,                 # 剩余即结构化字段
        pdf_refs=pdf_refs,
        ground_truth=ground_truth,
        metadata=metadata,
        raw=raw_item,
    )
