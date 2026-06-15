"""统一 env / secrets 入口（多 skill 共享）。

加载顺序（首次调用 ``ensure_env_loaded`` 时，后到不覆盖）：
1. 进程已有的环境变量（最高优先；CI / shell ``export``）
2. ``$SKILL_COMMONS_ENV_FILE`` 指向的文件（多套环境切换用）
3. ``skill_commons/.env``（默认共享配置）

故意 **不** 读取 ``<repo_root>/.env``，避免和上游 Hermes gateway 等项目的配置混在一起。
如果某些变量需要跨项目共享（例如同一个 Langfuse 实例），把它们 ``export`` 到进程环境，
或写到 ``$SKILL_COMMONS_ENV_FILE`` 指定的位置。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

_LOADED = False
_PACKAGE_DIR = Path(__file__).resolve().parent


def _candidate_env_files() -> List[Path]:
    paths: List[Path] = []
    custom = os.environ.get("SKILL_COMMONS_ENV_FILE")
    if custom:
        paths.append(Path(custom).expanduser())
    paths.append(_PACKAGE_DIR / ".env")
    return paths


def ensure_env_loaded(verbose: bool = False) -> None:
    """幂等：第一次调用时尝试 load_dotenv，之后是 no-op。"""
    global _LOADED
    if _LOADED:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        if verbose:
            print("[skill_commons.env] python-dotenv 未安装；"
                  "请 pip install python-dotenv 或手动 export 环境变量")
        _LOADED = True
        return

    seen = set()
    for p in _candidate_env_files():
        if not p or p in seen:
            continue
        seen.add(p)
        if p.is_file():
            load_dotenv(dotenv_path=p, override=False)
            if verbose:
                print(f"[skill_commons.env] loaded {p}")
    _LOADED = True


def require(key: str) -> str:
    """获取必填 env，缺失时抛出明确错误。"""
    ensure_env_loaded()
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(
            f"缺少环境变量 {key}；请在 skill_commons/.env 中设置，或 export {key}=..."
        )
    return val


def get(key: str, default: str = "") -> str:
    ensure_env_loaded()
    return os.environ.get(key, default)


def reset_for_test() -> None:
    """仅供测试使用：强制下次再走加载流程。"""
    global _LOADED
    _LOADED = False
