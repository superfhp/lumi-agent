"""RVEC Pipeline Judge：跑 STEP1 → STEP2×4 → aggregate → STEP3。

DAG 硬编码（拓扑稳定，不需要 manifest）。
领域包通过 ``spec.extra.prompt_pack`` 指向 prompts/judge/rvec_general/ 这种目录。

每个 sample 跑 6 次 LLM 调用：
  1. step1_understand → step1 JSON（用户需求分析）
  2. step2_R / step2_V / step2_E（串行）→ 各自 triggered_signals
  3. step2_C → triggered_highlights
  4. aggregate（Python，无 LLM）→ bad_signals + good_signals 裁剪到 caps
  5. step3_scoring → final_score (0-4) + tag_coverage + dcg_note

任意 step 解析失败 → 重试 1 次（temperature +0.1）→ 仍失败则整个 sample fail-loud：
  score=0.0, extra.judge_failed=true, extra.failed_step=stepX。
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.config import JudgeSpec, MetricSpec
from ..core.judge import Judge, JudgeResult, call_with_override
from ..core.runner import RunOutput
from ..core.sample import Sample
from ._rvec_helpers import (
    RVECPack,
    format_reference_block,
    limit_highlights,
    limit_signals,
    load_pack,
    render_signals_section,
    resolve_caps,
)
from .base import Metric, MetricResult


# ----------------------------------------------------------------------------
# prompt 渲染：白名单替换，避免 JSON 示例里的 {"key":"val"} 被误识别
# ----------------------------------------------------------------------------
def _render(template: str, **vars: str) -> str:
    """只替换 {var} 中确定存在 vars 里的 key；其他 `{...}` 原样保留。

    比 str.format() 安全 —— prompt 文件里的 JSON 示例不会被当成占位符。
    """
    out = template
    for k, v in vars.items():
        out = out.replace("{" + k + "}", str(v))
    return out


# ----------------------------------------------------------------------------
# step 执行辅助：解析检查 + 自动重试 1 次
# ----------------------------------------------------------------------------
def _parsed_ok(result: JudgeResult, require_keys: List[str]) -> bool:
    if not result.parsed or not isinstance(result.parsed, dict):
        return False
    return all(k in result.parsed for k in require_keys)


def _bump_temperature(
    default_judge: Optional[Judge],
    override: Optional[JudgeSpec],
    delta: float,
) -> Optional[JudgeSpec]:
    """生成一个 temperature += delta 的 spec，用于重试。"""
    base: Optional[JudgeSpec] = override or (default_judge.spec if default_judge else None)
    if base is None:
        return None
    return replace(base, temperature=min(2.0, max(0.0, base.temperature + delta)))


def _run_step(
    step_name: str,
    default_judge: Optional[Judge],
    override: Optional[JudgeSpec],
    system_prompt: str,
    user_prompt: str,
    require_keys: List[str],
) -> JudgeResult:
    """跑一个 step；解析失败重试 1 次。返回的 result.parsed 必须包含 require_keys。"""
    result = call_with_override(default_judge, override, system_prompt, user_prompt)
    if _parsed_ok(result, require_keys):
        return result

    # 重试：把 temperature +0.1
    bumped = _bump_temperature(default_judge, override, +0.1)
    retry = call_with_override(default_judge, bumped, system_prompt, user_prompt)
    if _parsed_ok(retry, require_keys):
        return retry

    # 仍失败：用第二次的 result 返回（带 raw 便于排查）
    return retry


# ----------------------------------------------------------------------------
# RVECJudge metric
# ----------------------------------------------------------------------------
class RVECJudge(Metric):
    """通用 RVEC pipeline metric。

    yaml 字段（写在 metric 项下）：
        name: rvec_judge
        alias: general_rvec
        prompt_pack: prompts/judge/rvec_general    # 必填，领域包目录
        judge_override: {host_profile:..., model:..., temperature:0.1}    # 可选
        extra:
          caps:
            bad_mut: 5         # 覆盖 pack.yaml 默认
            bad_baseline: 4
            good: 3
            per_dim: {R: 2, V: 2, E: 1, C: 3}
    """
    name = "rvec_judge"
    needs_judge = True

    REQ_KEYS_STEP1 = ["main_need", "question_type"]
    REQ_KEYS_STEP2 = ["triggered_signals"]
    REQ_KEYS_STEP2C = ["triggered_highlights"]
    REQ_KEYS_STEP3 = ["final_score"]

    def __init__(self, spec: MetricSpec):
        super().__init__(spec)
        pack_ref = spec.extra.get("prompt_pack") or getattr(spec, "prompt_pack", None)
        if not pack_ref:
            raise ValueError(
                f"rvec_judge metric '{spec.column_name}' 缺少 prompt_pack；"
                f"请在 yaml 写 prompt_pack: prompts/judge/rvec_general"
            )
        self.pack: RVECPack = load_pack(pack_ref)
        if self.pack.scoring_mode != "llm":
            raise NotImplementedError(
                f"pack {self.pack.pack_dir} 的 scoring_mode={self.pack.scoring_mode}，"
                f"本期只实现 llm 模式（rule 模式留待后续）"
            )

        # 预渲染 4 个维度的 signals_section
        self._sig_R = render_signals_section(self.pack, "R")
        self._sig_V = render_signals_section(self.pack, "V")
        self._sig_E = render_signals_section(self.pack, "E")
        self._sig_C = render_signals_section(self.pack, "C")

        # 预读取 6 个 prompt 文件
        self._prompt_step1 = self._load_prompt("step1_understand.md")
        self._prompt_step2_R = self._load_prompt("step2_R.md")
        self._prompt_step2_V = self._load_prompt("step2_V.md")
        self._prompt_step2_E = self._load_prompt("step2_E.md")
        self._prompt_step2_C = self._load_prompt("step2_C.md")
        self._prompt_step3 = self._load_prompt("step3_scoring.md")

    def _load_prompt(self, file_name: str) -> str:
        p: Path = self.pack.pack_dir / file_name
        if not p.exists():
            raise FileNotFoundError(f"RVEC prompt 缺失: {p}")
        return p.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ compute
    def compute(
        self,
        sample: Sample,
        output: RunOutput,
        judge: Optional[Judge] = None,
        is_baseline: bool = False,
        **_kwargs: Any,
    ) -> MetricResult:
        # 1) 准备上下文
        # dialog（多轮）和单轮要分开拼：
        #   - 单轮：question = sample.prompt（或 fields.question），answer = output.final_text
        #   - 多轮：把 4 轮 user / assistant 全部按时间序拼成 transcript，让 judge 能
        #          看到完整对话，否则 R6 跨轮一致性 / R-CONS-1 信息遗忘 / R-CONS-2 前后矛盾
        #          这些信号永远触发不了。
        question, answer = self._build_question_answer(sample, output)
        # answer 截断（zhuguan_prompt.py 建议 5000-8000 字）
        max_answer = int(self.spec.extra.get("max_answer_chars", 8000))
        answer_truncated = answer[:max_answer]

        expected_signals = sample.metadata.get("expected_signals") or []
        expected_answer = sample.ground_truth.answer if sample.ground_truth.answer else None
        reference_block = format_reference_block(
            expected_answer, expected_signals, self.pack
        )

        # 2) caps 解析（mut/baseline + yaml 覆盖）
        caps = resolve_caps(
            self.pack, is_baseline=is_baseline,
            overrides=self.spec.extra.get("caps") or {},
        )

        override = self.spec.judge_override

        # ============================================================
        # STEP 1: 理解需求
        # ============================================================
        step1_user = _render(
            self._prompt_step1,
            question=question,
            reference_block=reference_block,
        )
        # system 用通用小段（也把整个 prompt 当 user 送也行；这里 system 留空给一句）
        step1_sys = "你是大模型评测系统的需求理解器。严格按要求输出 JSON。"
        r1 = _run_step("step1", judge, override, step1_sys, step1_user, self.REQ_KEYS_STEP1)
        if not _parsed_ok(r1, self.REQ_KEYS_STEP1):
            return self._fail("step1", r1, caps)

        step1_data: Dict[str, Any] = r1.parsed
        step1_str = json.dumps(step1_data, ensure_ascii=False)

        # ============================================================
        # STEP 2 R / V / E: 串行检测
        # ============================================================
        bad_signals: List[Dict[str, Any]] = []

        # R 维度（用 reference_block）
        step2R_user = _render(
            self._prompt_step2_R,
            question=question,
            answer=answer_truncated,
            step1=step1_str,
            reference_block=reference_block,
            signals_section=self._sig_R,
        )
        r2R = _run_step("step2_R", judge, override, "你是 RVEC 评测员（R 维度）。",
                        step2R_user, self.REQ_KEYS_STEP2)
        if not _parsed_ok(r2R, self.REQ_KEYS_STEP2):
            return self._fail("step2_R", r2R, caps)
        bad_signals.extend(r2R.parsed.get("triggered_signals") or [])

        # V 维度（用 reference_block）
        step2V_user = _render(
            self._prompt_step2_V,
            question=question,
            answer=answer_truncated,
            step1=step1_str,
            reference_block=reference_block,
            signals_section=self._sig_V,
        )
        r2V = _run_step("step2_V", judge, override, "你是 RVEC 评测员（V 维度）。",
                        step2V_user, self.REQ_KEYS_STEP2)
        if not _parsed_ok(r2V, self.REQ_KEYS_STEP2):
            return self._fail("step2_V", r2V, caps)
        bad_signals.extend(r2V.parsed.get("triggered_signals") or [])

        # E 维度（不用 reference_block）
        step2E_user = _render(
            self._prompt_step2_E,
            question=question,
            answer=answer_truncated,
            step1=step1_str,
            signals_section=self._sig_E,
        )
        r2E = _run_step("step2_E", judge, override, "你是 RVEC 评测员（E 维度）。",
                        step2E_user, self.REQ_KEYS_STEP2)
        if not _parsed_ok(r2E, self.REQ_KEYS_STEP2):
            return self._fail("step2_E", r2E, caps)
        bad_signals.extend(r2E.parsed.get("triggered_signals") or [])

        # ============================================================
        # STEP 2 C: 亮点
        # ============================================================
        step2C_user = _render(
            self._prompt_step2_C,
            question=question,
            answer=answer_truncated,
            step1=step1_str,
            signals_section=self._sig_C,
        )
        r2C = _run_step("step2_C", judge, override, "你是 RVEC 评测员（C 亮点）。",
                        step2C_user, self.REQ_KEYS_STEP2C)
        if not _parsed_ok(r2C, self.REQ_KEYS_STEP2C):
            return self._fail("step2_C", r2C, caps)
        good_signals = list(r2C.parsed.get("triggered_highlights") or [])

        # ============================================================
        # AGGREGATE: 裁剪到 caps
        # ============================================================
        bad_signals = limit_signals(
            bad_signals,
            max_total=caps["bad_total"],
            per_dim=caps["per_dim"],
            pack=self.pack,
        )
        good_signals = limit_highlights(good_signals, caps["good_total"])

        # ============================================================
        # STEP 3: 综合评分
        # ============================================================
        step3_user = _render(
            self._prompt_step3,
            question=question,
            answer=answer_truncated,
            step1=step1_str,
            reference_block=reference_block,
            bad_signals=json.dumps(bad_signals, ensure_ascii=False),
            good_signals=json.dumps(good_signals, ensure_ascii=False),
        )
        r3 = _run_step("step3", judge, override, "你是 RVEC 综合评分器。",
                       step3_user, self.REQ_KEYS_STEP3)
        if not _parsed_ok(r3, self.REQ_KEYS_STEP3):
            return self._fail("step3", r3, caps,
                              bad_signals=bad_signals, good_signals=good_signals,
                              step1_data=step1_data)

        scoring = r3.parsed
        try:
            final_raw = float(scoring.get("final_score", 0.0))
        except (TypeError, ValueError):
            final_raw = 0.0
        final_raw = max(0.0, min(4.0, final_raw))
        score = round(final_raw / 4.0, 4)

        summary = str(scoring.get("summary") or "")
        worst = str(scoring.get("worst_level") or "")
        coverage = str(scoring.get("tag_coverage") or "")
        dcg_note = str(scoring.get("dcg_note") or "")
        question_type = str(step1_data.get("question_type") or "")

        # ------------ 落 CSV 的字段 ------------
        # reporter 自动遍历 extra；list/dict 要 json 字符串才能落 CSV
        reason = (
            f"final={final_raw}/4 worst={worst} coverage={coverage} "
            f"bad={len(bad_signals)} good={len(good_signals)} | {summary[:80]}"
        )
        return MetricResult(
            self.column,
            value=score,
            reason=reason,
            extra={
                "final_score_raw": final_raw,
                "worst_level": worst,
                "tag_coverage": coverage,
                "summary": summary,
                "dcg_note": dcg_note,
                "question_type": question_type,
                "bad_signals_count": len(bad_signals),
                "good_signals_count": len(good_signals),
                "is_baseline": is_baseline,
                "bad_signals_json": json.dumps(bad_signals, ensure_ascii=False),
                "good_signals_json": json.dumps(good_signals, ensure_ascii=False),
                # 兼容旧通用评测脚本命名：Lumi/CSV 里直观看到命中的 bad_tags/good_tags
                "bad_tags_json": json.dumps(bad_signals, ensure_ascii=False),
                "good_tags_json": json.dumps(good_signals, ensure_ascii=False),
                "step1_json": json.dumps(step1_data, ensure_ascii=False),
                "step2_R_json": json.dumps(r2R.parsed, ensure_ascii=False),
                "step2_V_json": json.dumps(r2V.parsed, ensure_ascii=False),
                "step2_E_json": json.dumps(r2E.parsed, ensure_ascii=False),
                "step2_C_json": json.dumps(r2C.parsed, ensure_ascii=False),
                "step3_json": json.dumps(scoring, ensure_ascii=False),
                "judge_failed": False,
                # 给 reporter 用：在 Lumi trace 下创建 step 级 observation/span；
                # CSV writer 会忽略 list/dict，不会污染明细列。
                "_lumi_observations": [
                    {
                        "name": f"{self.column}.step1_understand",
                        "kind": "generation",
                        "input": {"question_chars": len(question), "has_reference": bool(reference_block)},
                        "output": step1_data,
                    },
                    {
                        "name": f"{self.column}.step2_R",
                        "kind": "generation",
                        "input": {"dimension": "R", "signals": "Reliability"},
                        "output": r2R.parsed,
                    },
                    {
                        "name": f"{self.column}.step2_V",
                        "kind": "generation",
                        "input": {"dimension": "V", "signals": "Value"},
                        "output": r2V.parsed,
                    },
                    {
                        "name": f"{self.column}.step2_E",
                        "kind": "generation",
                        "input": {"dimension": "E", "signals": "Experience"},
                        "output": r2E.parsed,
                    },
                    {
                        "name": f"{self.column}.step2_C",
                        "kind": "generation",
                        "input": {"dimension": "C", "signals": "Highlights"},
                        "output": r2C.parsed,
                    },
                    {
                        "name": f"{self.column}.aggregate",
                        "kind": "span",
                        "input": {"caps": caps},
                        "output": {
                            "bad_signals": bad_signals,
                            "good_signals": good_signals,
                            "bad_count": len(bad_signals),
                            "good_count": len(good_signals),
                        },
                    },
                    {
                        "name": f"{self.column}.step3_scoring",
                        "kind": "generation",
                        "input": {
                            "bad_count": len(bad_signals),
                            "good_count": len(good_signals),
                            "bad_signals": bad_signals,
                            "good_signals": good_signals,
                        },
                        "output": scoring,
                    },
                ],
            },
        )

    # ------------------------------------------------------------------ dialog 适配
    def _build_question_answer(
        self,
        sample: Sample,
        output: RunOutput,
    ) -> tuple[str, str]:
        """根据 sample 是否多轮，把 user prompt / assistant answer 拼成给 judge 看的文本。

        - 单轮：question = sample.prompt（或 fields.question），answer = output.final_text
        - 多轮（dialog）：拼成「第 N 轮」对照表，judge 能看到完整对话，
          否则 R6 跨轮一致性 / R-CONS-1 信息遗忘 / R-CONS-2 前后矛盾这些信号
          永远触发不了。
          双方轮次对齐到 min(len(sample.turns), len(output.turns))，
          第 N 轮缺一边时仅打印另一边并标注 missing。
        """
        if not sample.has_turns:
            question = sample.fields.get("question") or sample.prompt or ""
            answer = output.final_text or ""
            return question, answer

        # 多轮场景
        sample_turns = list(sample.turns or [])
        output_turns = list(output.turns or [])
        n_pairs = max(len(sample_turns), len(output_turns))

        q_lines: List[str] = []
        a_lines: List[str] = []
        for i in range(n_pairs):
            user_text = sample_turns[i].content if i < len(sample_turns) else "(本轮缺少 user 输入)"
            assistant_text = output_turns[i].content if i < len(output_turns) else "(本轮模型未输出)"
            q_lines.append(f"【第{i+1}轮 用户】\n{user_text}")
            a_lines.append(f"【第{i+1}轮 助手】\n{assistant_text}")
        return "\n\n".join(q_lines), "\n\n".join(a_lines)

    # ------------------------------------------------------------------ fail
    def _fail(
        self,
        failed_step: str,
        last_result: JudgeResult,
        caps: Dict[str, Any],
        **trail: Any,
    ) -> MetricResult:
        """任一步 LLM 解析失败 → fail-loud 返回 0 分。"""
        reason = f"RVEC pipeline 失败于 {failed_step}: {last_result.reason or 'parse error'}"
        extra: Dict[str, Any] = {
            "judge_failed": True,
            "failed_step": failed_step,
            "raw_at_failure": (last_result.raw or "")[:2000],
            "caps": json.dumps(caps, ensure_ascii=False),
        }
        # 如果走到一半失败的，把已收集到的中间结果也带上便于排查
        if "bad_signals" in trail:
            extra["bad_signals_json"] = json.dumps(trail["bad_signals"], ensure_ascii=False)
            extra["bad_tags_json"] = extra["bad_signals_json"]
        if "good_signals" in trail:
            extra["good_signals_json"] = json.dumps(trail["good_signals"], ensure_ascii=False)
            extra["good_tags_json"] = extra["good_signals_json"]
        if "step1_data" in trail:
            extra["step1_json"] = json.dumps(trail["step1_data"], ensure_ascii=False)
        return MetricResult(self.column, value=0.0, reason=reason, extra=extra)
