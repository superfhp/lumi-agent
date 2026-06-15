"""脱敏 profile 加载（多 skill 共享）。

profile_name → {keyword: replacement}。在 PDF / 长文本 redaction 时使用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import yaml

DEFAULT_REDACTION_YAML = Path(__file__).resolve().parent / "registry" / "redaction_profiles.yaml"


def load_redaction_profiles(yaml_path: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    p = Path(yaml_path) if yaml_path else DEFAULT_REDACTION_YAML
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
