"""HostProfile / host_profiles.yaml 加载 / OpenAI client 缓存。

字段值支持 ``${ENV_VAR}`` 与 ``${ENV_VAR:default}`` 占位，首次加载时通过
``env.ensure_env_loaded()`` 拉取 .env。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from openai import OpenAI

from .env import ensure_env_loaded

DEFAULT_HOST_PROFILES_YAML = Path(__file__).resolve().parent / "registry" / "host_profiles.yaml"

_PROFILES: Dict[str, "HostProfile"] = {}
_CLIENTS: Dict[str, OpenAI] = {}
_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::([^}]*))?\}")


@dataclass
class HostProfile:
    name: str
    api_key: str
    base_url: str
    timeout: int = 300


def _expand(value: Any) -> Any:
    """递归展开 ${VAR} / ${VAR:default} 占位。"""
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            var, default = m.group(1), m.group(2) or ""
            return os.environ.get(var, default)
        return _PLACEHOLDER_RE.sub(repl, value)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def load_host_profiles(yaml_path: Optional[Path] = None) -> None:
    """从 yaml 加载 host 注册表；不传则用 skill_commons 自带的默认表。"""
    global _PROFILES
    ensure_env_loaded()
    p = Path(yaml_path) if yaml_path else DEFAULT_HOST_PROFILES_YAML
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw = _expand(raw)
    _PROFILES = {name: HostProfile(name=name, **cfg) for name, cfg in raw.items()}


def _ensure_loaded() -> None:
    if not _PROFILES:
        load_host_profiles()


def get_profile(name: str) -> HostProfile:
    _ensure_loaded()
    if name not in _PROFILES:
        raise KeyError(
            f"host_profile '{name}' 未注册；请在 skill_commons/registry/host_profiles.yaml 中添加，"
            f"或自行 load_host_profiles(<path>) 指向你自己的 yaml"
        )
    return _PROFILES[name]


def get_client(profile_name: str) -> OpenAI:
    if profile_name in _CLIENTS:
        return _CLIENTS[profile_name]
    p = get_profile(profile_name)
    client = OpenAI(api_key=p.api_key, base_url=p.base_url, timeout=p.timeout)
    _CLIENTS[profile_name] = client
    return client


def all_profiles() -> Dict[str, HostProfile]:
    _ensure_loaded()
    return dict(_PROFILES)
