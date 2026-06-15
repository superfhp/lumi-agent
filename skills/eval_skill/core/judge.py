"""Judge：单 LLM judge 客户端 + 严格 JSON 输出契约。

支持每个 metric 通过 judge_override 单独指定 judge。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from .config import JudgeSpec
from skill_commons import get_client


@dataclass
class JudgeResult:
    score: float
    reason: str
    thinking: str = ""
    raw: str = ""
    parsed: dict = None  # type: ignore[assignment]

    def to_dict(self):
        return {"score": self.score, "reason": self.reason, "thinking": self.thinking}


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class Judge:
    """全局 judge；可被 metric 自带的 judge_override 临时替换（见 evaluator）。"""

    def __init__(self, spec: JudgeSpec):
        self.spec = spec

    def call(self, system_prompt: str, user_prompt: str) -> JudgeResult:
        return _call_with_spec(self.spec, system_prompt, user_prompt)


def _call_with_spec(spec: JudgeSpec, system_prompt: str, user_prompt: str) -> JudgeResult:
    client = get_client(spec.host_profile)
    try:
        resp = client.chat.completions.create(
            model=spec.model,
            temperature=spec.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = resp.choices[0].message.content.strip()
    except Exception as e:
        return JudgeResult(0.0, f"judge call failed: {e}", "", "", {})

    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    m = _JSON_OBJECT_RE.search(cleaned)
    if not m:
        return JudgeResult(0.0, "judge output not JSON", "", raw, {})
    try:
        data = json.loads(m.group(0))
    except Exception as e:
        return JudgeResult(0.0, f"judge parse failed: {e}", "", raw, {})

    return JudgeResult(
        score=float(data.get("score", 0.0)) if isinstance(data.get("score", 0.0), (int, float, str)) and str(data.get("score", "")).strip() != "" else 0.0,
        reason=str(data.get("reason", "")),
        thinking=str(data.get("thinking", "")),
        raw=raw,
        parsed=data,
    )


def call_with_override(default: Optional[Judge], override: Optional[JudgeSpec],
                       system_prompt: str, user_prompt: str) -> JudgeResult:
    spec = override or (default.spec if default else None)
    if spec is None:
        return JudgeResult(0.0, "no judge configured", "", "", {})
    return _call_with_spec(spec, system_prompt, user_prompt)
