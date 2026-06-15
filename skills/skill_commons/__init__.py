"""skill_commons — 多 skill 共享的基础设施层。

公开 API（典型用法）::

    from skill_commons import (
        ensure_env_loaded, require_env, get_env,
        load_host_profiles, get_profile, get_client, all_profiles,
        HostProfile,
        build_lumi_client,
        load_redaction_profiles,
    )

详见 README.md。
"""
from .env import ensure_env_loaded, get as get_env, require as require_env
from .hosts import (
    HostProfile,
    all_profiles,
    get_client,
    get_profile,
    load_host_profiles,
)
from .lumi_client import build_client as build_lumi_client
from .redaction import load_redaction_profiles

__all__ = [
    "ensure_env_loaded",
    "require_env",
    "get_env",
    "HostProfile",
    "load_host_profiles",
    "get_profile",
    "get_client",
    "all_profiles",
    "build_lumi_client",
    "load_redaction_profiles",
]
