"""PromptBuilder：Sample × PromptStrategy → List[messages]。

渲染优先级：
1. sample.has_turns → 逐 turn 作为 user
2. sample.has_prompt → 单条 user
3. 否则用 user_template 渲染 sample.fields

任何模式下，sample.attachments_md 内容（PDF 抽取结果）都会以前置 user 注入。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .config import PromptStrategy
from .sample import Sample


Message = Dict[str, str]


def _format_options(opts) -> str:
    if isinstance(opts, dict):
        return "\n".join(f"{k}: {v}" for k, v in opts.items())
    if isinstance(opts, list):
        return "\n".join(str(x) for x in opts)
    return str(opts) if opts is not None else ""


class PromptBuilder:
    def __init__(self, strategy: PromptStrategy, system_prompt: Optional[str],
                 user_template: Optional[str]):
        self.strategy = strategy
        self.system_prompt = system_prompt
        self.user_template = user_template

    def build_initial(self, sample: Sample) -> List[Message]:
        """构造首次发起请求时的 messages（不包含后续轮次的 turn[i>=1]）。"""
        messages: List[Message] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})

        # 前置 PDF 注入
        for label, md in sample.attachments_md.items():
            messages.append({"role": "user", "content": f"# {label}\n\n{md}"})

        first_user = self._first_user(sample)
        if first_user:
            messages.append({"role": "user", "content": first_user})
        return messages

    def next_user_message(self, sample: Sample, turn_idx: int) -> Optional[str]:
        """多轮：返回第 turn_idx 轮的 user 文本（turn_idx 从 1 起表示第 2 轮）。"""
        if not sample.has_turns:
            return None
        if turn_idx >= len(sample.turns):
            return None
        return self._render_turn(sample.turns[turn_idx].content)

    # ----------------------------------------------------------------------
    def _first_user(self, sample: Sample) -> str:
        if sample.has_turns:
            return self._render_turn(sample.turns[0].content)
        if sample.has_prompt:
            return sample.prompt or ""
        return self._render_template(sample)

    def _render_turn(self, content: str) -> str:
        prefix = self.strategy.per_turn_prefix or ""
        return f"{prefix}{content}" if prefix else content

    def _render_template(self, sample: Sample) -> str:
        tmpl = self.user_template
        if not tmpl:
            # 兜底：把 fields 里的 question 拼出来
            return str(sample.fields.get("question") or sample.fields)
        ctx = dict(sample.fields)
        if "options" in ctx:
            ctx["options"] = _format_options(ctx["options"])
        # 安全 format：缺字段补空
        try:
            return tmpl.format_map(_SafeMap(ctx))
        except Exception as e:
            raise ValueError(f"render user_template failed: {e}; sample={sample.sample_id}") from e


class _SafeMap(dict):
    def __missing__(self, key):
        return ""
