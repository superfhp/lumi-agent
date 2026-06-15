"""Runner：流式调用 + reasoning_content / <think> 抽取 + 多轮串联。"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import ModelSpec
from skill_commons import get_client
from .prompt_builder import PromptBuilder
from .sample import Sample


@dataclass
class TurnOutput:
    role: str
    prompt: str
    content: str
    reasoning: str = ""
    finish_reason: str = "stop"
    usage: Dict[str, int] = field(default_factory=dict)
    latency_sec: float = 0.0


@dataclass
class RunOutput:
    sample_id: str
    model: str
    run_prefix: str
    turns: List[TurnOutput] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def final_text(self) -> str:
        return self.turns[-1].content if self.turns else ""

    @property
    def final_reasoning(self) -> str:
        return self.turns[-1].reasoning if self.turns else ""

    @property
    def all_text(self) -> str:
        return "\n\n".join(t.content for t in self.turns)

    def total_usage(self) -> Dict[str, int]:
        agg = {"input": 0, "output": 0, "total": 0}
        for t in self.turns:
            for k, v in t.usage.items():
                agg[k] = agg.get(k, 0) + (v or 0)
        return agg


class Runner:
    def __init__(self, model: ModelSpec, builder: PromptBuilder):
        self.model = model
        self.builder = builder

    def run(self, sample: Sample) -> RunOutput:
        out = RunOutput(sample_id=sample.sample_id, model=self.model.model,
                        run_prefix=self.model.trace_label())
        try:
            messages = self.builder.build_initial(sample)
            # 第 0 轮
            content, reasoning, usage, finish, latency = self._call(messages)
            out.turns.append(TurnOutput(
                role="assistant",
                prompt=messages[-1]["content"] if messages and messages[-1]["role"] == "user" else "",
                content=content, reasoning=reasoning, usage=usage,
                finish_reason=finish, latency_sec=latency,
            ))
            messages.append({"role": "assistant", "content": content})

            # 后续多轮
            turn_idx = 1
            while True:
                next_user = self.builder.next_user_message(sample, turn_idx)
                if next_user is None:
                    break
                messages.append({"role": "user", "content": next_user})
                content, reasoning, usage, finish, latency = self._call(messages)
                out.turns.append(TurnOutput(
                    role="assistant", prompt=next_user,
                    content=content, reasoning=reasoning, usage=usage,
                    finish_reason=finish, latency_sec=latency,
                ))
                messages.append({"role": "assistant", "content": content})
                turn_idx += 1
        except Exception as e:
            out.error = str(e)
        return out

    # ----------------------------------------------------------------------
    def _call(self, messages):
        client = get_client(self.model.host_profile)
        kwargs: Dict[str, Any] = dict(
            model=self.model.model,
            messages=messages,
            temperature=self.model.temperature,
            stream=True,
            stream_options={"include_usage": True},
        )
        if self.model.max_tokens:
            kwargs["max_tokens"] = self.model.max_tokens
        if self.model.extra_body:
            kwargs["extra_body"] = self.model.extra_body

        t0 = time.time()
        stream = client.chat.completions.create(**kwargs)

        content, reasoning = "", ""
        usage = {"input": 0, "output": 0, "total": 0}
        finish_reason = "stop"
        for chunk in stream:
            if not chunk.choices:
                if getattr(chunk, "usage", None):
                    usage = {
                        "input": chunk.usage.prompt_tokens,
                        "output": chunk.usage.completion_tokens,
                        "total": chunk.usage.total_tokens,
                    }
                continue
            delta = chunk.choices[0].delta
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                reasoning += rc
            if getattr(delta, "content", None):
                content += delta.content
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason

        # <think>...</think> 抽取
        if "<think>" in content:
            m = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
            if m:
                if not reasoning:
                    reasoning = m.group(1).strip()
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

        return content, reasoning, usage, finish_reason, round(time.time() - t0, 3)
