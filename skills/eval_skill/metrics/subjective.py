"""主观指标：reasoning_quality / factuality_score / rubric_judge / custom_judge。

每个指标都有：
- system prompt：评分原则、维度定义、输出格式（全局通用，所有样本共用）
  → 默认从 prompts/judge/<name>.md 读；通过 spec.prompt_inline / prompt_ref 覆盖。
- user prompt 模板：拼装当前 sample + output（每条样本不同）
  → 默认用兜底模板（含背景/题目/官方解析/参考推理/...）；通过 spec.user_prompt_inline /
    user_prompt_ref 提供任意自定义模板。

通过 spec.judge_override 单独指定 judge。

注：原来的 SignalJudge 已被更完整的 ``rvec_judge`` (metrics/rvec.py) 替代。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from ..core.judge import Judge, call_with_override
from ..core.runner import RunOutput
from ..core.sample import Sample
from .base import Metric, MetricResult

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts" / "judge"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------------------
# prompt resolution
# ----------------------------------------------------------------------------
def _read_prompt_file(rel_or_abs: str) -> str:
    p = Path(rel_or_abs)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    if not p.exists():
        raise FileNotFoundError(f"prompt 文件不存在: {p}")
    return p.read_text(encoding="utf-8")


def _resolve_system_prompt(spec, default_name: str) -> str:
    if spec.prompt_inline:
        return spec.prompt_inline
    if spec.prompt_ref:
        return _read_prompt_file(spec.prompt_ref)
    default = PROMPTS_DIR / f"{default_name}.md"
    if not default.exists():
        raise FileNotFoundError(
            f"未找到 judge system prompt: {default}\n"
            f"请在 metric 上提供 prompt_inline 或 prompt_ref，"
            f"或创建 {default}"
        )
    return default.read_text(encoding="utf-8")


def _resolve_user_prompt_template(spec) -> Optional[str]:
    """返回 user_prompt 模板字符串；None 表示走兜底模板。"""
    if spec.user_prompt_inline:
        return spec.user_prompt_inline
    if spec.user_prompt_ref:
        return _read_prompt_file(spec.user_prompt_ref)
    return None


# ----------------------------------------------------------------------------
# user prompt rendering
# ----------------------------------------------------------------------------
class _SafeMap(dict):
    def __missing__(self, key: str):
        # 支持 metadata.xxx / fields.xxx 简单点路径
        if "." in key:
            head, tail = key.split(".", 1)
            sub = super().get(head)
            if isinstance(sub, dict):
                return sub.get(tail, "")
        return ""


def _render_context(sample: Sample, output: RunOutput) -> Dict[str, Any]:
    gt = sample.ground_truth
    base = {
        # 输入侧
        "question": sample.fields.get("question") or sample.prompt or "",
        "background": (sample.fields.get("background")
                       or sample.fields.get("background_context") or ""),
        "options": sample.fields.get("options", ""),
        # 模型输出侧
        "answer": output.final_text,
        "prediction": output.final_text,
        "reasoning": output.final_reasoning,
        "all_answers": output.all_text,
        # ground truth 侧
        "ground_truth": gt.answer if gt.answer is not None else "",
        "explanation": gt.explanation or "",
        "reasoning_ref": gt.reasoning_ref or "",
        "rubric": gt.rubric or "",
        "expected_md": gt.expected_md or "",
        # 整层暴露
        "fields": sample.fields,
        "metadata": sample.metadata,
    }
    # fields/metadata 顶层透传（命名不冲突时方便直接 {step1} / {custom_field}）
    for k, v in sample.fields.items():
        base.setdefault(k, v)
    for k, v in sample.metadata.items():
        base.setdefault(k, v)
    return base


def _build_user_prompt(sample: Sample, output: RunOutput, spec=None) -> str:
    template = _resolve_user_prompt_template(spec) if spec is not None else None
    if template:
        ctx = _render_context(sample, output)
        try:
            return template.format_map(_SafeMap(ctx))
        except Exception as e:
            raise ValueError(f"render judge user_prompt failed: {e}") from e

    # 兜底模板（保持老行为）
    gt = sample.ground_truth
    fields = sample.fields
    background = fields.get("background") or fields.get("background_context") or ""
    question = fields.get("question") or sample.prompt or ""
    return (
        f"【背景】\n{background}\n\n"
        f"【题目】\n{question}\n\n"
        f"【官方解析】\n{gt.explanation or ''}\n\n"
        f"【参考推理】\n{gt.reasoning_ref or ''}\n\n"
        f"【参考答案】\n{gt.answer if gt.answer is not None else ''}\n\n"
        f"【评分准则 rubric】\n{gt.rubric or ''}\n\n"
        f"【模型推理过程】\n{output.final_reasoning}\n\n"
        f"【模型最终回答】\n{output.all_text}\n"
    )


# ----------------------------------------------------------------------------
# 单值打分指标
# ----------------------------------------------------------------------------
class _JudgeMetric(Metric):
    needs_judge = True
    default_prompt = ""

    def compute(self, sample: Sample, output: RunOutput,
                judge: Optional[Judge] = None, **_kwargs) -> MetricResult:
        sys_prompt = _resolve_system_prompt(self.spec, self.default_prompt)
        usr_prompt = _build_user_prompt(sample, output, self.spec)
        result = call_with_override(judge, self.spec.judge_override, sys_prompt, usr_prompt)
        return MetricResult(
            self.column,
            value=max(0.0, min(1.0, result.score)),
            reason=result.reason,
            extra={"thinking": result.thinking, "raw": (result.raw or "")[:2000]},
        )


class ReasoningQuality(_JudgeMetric):
    name = "reasoning_quality"
    default_prompt = "reasoning_quality"


class FactualityScore(_JudgeMetric):
    name = "factuality_score"
    default_prompt = "factuality_score"


class RubricJudge(_JudgeMetric):
    name = "rubric_judge"
    default_prompt = "rubric_judge"


class CustomJudge(_JudgeMetric):
    name = "custom_judge"
    default_prompt = "custom_judge"
