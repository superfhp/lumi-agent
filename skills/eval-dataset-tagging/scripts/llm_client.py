"""LLM 客户端封装，支持 OpenAI 兼容接口 + 多端点自动探测

参考 eval_general.py 中 Judge LLM 的探测逻辑：DNS预检 → chat ping → 正式连接
"""

import json
import logging
import os
import socket
from urllib.parse import urlparse
from typing import Optional, Dict

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINTS = {
    "iquest": {
        "base_url": "http://iqeust-litellm.danbo-agidata-inner.com/v1",
        "api_key": "sk-kOp807j6jMPRirtnj9bAJg",
        "model": "gpt-5.4",
    },
    "zerail": {
        "base_url": "https://gateway.zerail.com/v1",
        "api_key": "sk-eModH1YZpV9YVdvc1WLA5mEvwFYbEkrKbSJq0TUbxWKe2y1K",
        "model": "gpt-5.2",
    },
}


class LLMClient:
    """LLM 调用客户端"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "gpt-4o",
        temperature: float = 0.0,
        max_retries: int = 3,
        endpoint_name: Optional[str] = None,
        endpoints: Optional[Dict] = None,
    ):
        self.temperature = temperature
        self.max_retries = max_retries

        if endpoint_name:
            ep_map = endpoints or DEFAULT_ENDPOINTS
            if endpoint_name in ep_map:
                cfg = ep_map[endpoint_name]
                api_key = cfg["api_key"]
                base_url = cfg["base_url"]
                model = cfg.get("model", model)
            else:
                logger.warning(f"未知端点 {endpoint_name}，使用默认参数")

        self.model = model
        self.client = OpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            base_url=base_url or os.getenv("OPENAI_BASE_URL"),
            timeout=300,
        )

    @classmethod
    def auto_detect(cls, endpoints: Dict = None, temperature: float = 0.0) -> "LLMClient":
        """自动探测可用端点（参考 eval_general.py 的 _auto_detect_judge 逻辑）
        
        探测步骤：DNS预检 → chat.completions ping → 正式连接
        """
        ep_map = endpoints or DEFAULT_ENDPOINTS
        for name, cfg in ep_map.items():
            try:
                # 1. DNS 预检，避免长时间挂起
                host = urlparse(cfg["base_url"]).hostname
                old_timeout = socket.getdefaulttimeout()
                socket.setdefaulttimeout(5)
                try:
                    socket.getaddrinfo(host, 80)
                finally:
                    socket.setdefaulttimeout(old_timeout)
                
                # 2. 实际 chat ping（比 models.list 更可靠）
                test_client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=10)
                test_client.chat.completions.create(
                    model=cfg.get("model", "gpt-4o"),
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=3,
                )
                logger.info(f"✅ 端点 [{name}] 可用，模型: {cfg.get('model', 'default')} @ {host}")
                return cls(api_key=cfg["api_key"], base_url=cfg["base_url"],
                           model=cfg.get("model", "gpt-4o"), temperature=temperature)
            except Exception as e:
                logger.warning(f"端点 [{name}] 不可用: {str(e)[:120]}")
        raise RuntimeError("所有 LLM 端点均不可达")

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        return self._call_with_retry(system_prompt, user_prompt)

    def chat_json(self, system_prompt: str, user_prompt: str) -> dict:
        raw = self._call_with_retry(system_prompt, user_prompt)
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0]
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0]
        return json.loads(raw.strip())

    @retry(wait=wait_exponential(multiplier=1, min=2, max=30),
           stop=stop_after_attempt(3), reraise=True)
    def _call_with_retry(self, system_prompt: str, user_prompt: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return resp.choices[0].message.content
