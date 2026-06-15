"""Lumi (Langfuse) 客户端构造（多 skill 共享）。"""
from __future__ import annotations

from typing import Any

from .env import get, require


def build_client() -> Any:
    """读取 LUMI_PUBLIC_KEY / LUMI_SECRET_KEY / LUMI_HOST 构造 Lumi 客户端。

    缺关键变量会直接报错（提示去 ``skill_commons/.env`` 设置）。
    """
    from lumi import Lumi
    return Lumi(
        public_key=require("LUMI_PUBLIC_KEY"),
        secret_key=require("LUMI_SECRET_KEY"),
        host=require("LUMI_HOST"),
        timeout=int(get("LUMI_TIMEOUT", "120") or 120),
        max_retries=int(get("LUMI_MAX_RETRIES", "3") or 3),
    )
