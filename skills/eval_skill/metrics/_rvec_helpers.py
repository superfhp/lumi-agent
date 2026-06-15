"""RVEC pipeline helpers：领域包加载、信号清单渲染、信号裁剪、reference_block 组装。

这里都是纯数据处理逻辑，不接触 LLM。RVECJudge metric 使用这里的函数。
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------------
@dataclass
class RVECPack:
    """从 pack.yaml 加载的 RVEC 领域包。"""
    domain: str
    version: str
    description: str
    scoring_mode: str                                 # llm | rule
    caps: Dict[str, Any]
    signals: List[Dict[str, Any]]                    # [{tag_id, name, dim, levels}]
    highlights: List[Dict[str, Any]]                 # [{tag_id, name, dim}]
    pack_dir: Path

    # 索引
    signals_by_dim: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    highlights_by_dim: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    tag_to_signal: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self):
        # 按子维度（R1/R2/...）分组保留输入顺序
        sd = OrderedDict()
        for s in self.signals:
            sd.setdefault(s["dim"], []).append(s)
        self.signals_by_dim = dict(sd)

        hd = OrderedDict()
        for h in self.highlights:
            hd.setdefault(h["dim"], []).append(h)
        self.highlights_by_dim = dict(hd)

        for s in self.signals:
            self.tag_to_signal[s["tag_id"]] = s
        for h in self.highlights:
            self.tag_to_signal[h["tag_id"]] = h


# ----------------------------------------------------------------------------
# 加载 pack
# ----------------------------------------------------------------------------
def load_pack(pack_ref: str | Path) -> RVECPack:
    """加载领域包目录下的 pack.yaml。

    pack_ref 可以是相对 eval_skill/ 的相对路径或绝对路径。
    """
    pack_dir = Path(pack_ref)
    if not pack_dir.is_absolute():
        pack_dir = PROJECT_ROOT / pack_dir
    if pack_dir.is_file():
        pack_dir = pack_dir.parent
    if not pack_dir.exists():
        raise FileNotFoundError(f"RVEC pack 目录不存在: {pack_dir}")
    yaml_path = pack_dir / "pack.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"RVEC pack.yaml 不存在: {yaml_path}")
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    return RVECPack(
        domain=str(data.get("domain", "unknown")),
        version=str(data.get("version", "v0")),
        description=str(data.get("description", "")),
        scoring_mode=str(data.get("scoring_mode", "llm")),
        caps=dict(data.get("caps") or {}),
        signals=list(data.get("signals") or []),
        highlights=list(data.get("highlights") or []),
        pack_dir=pack_dir,
    )


# ----------------------------------------------------------------------------
# 渲染信号清单（注入 prompt 的 {signals_section}）
# ----------------------------------------------------------------------------
def render_signals_section(pack: RVECPack, dim_prefix: str) -> str:
    """生成 step2 prompt 里 "逐一检查以下信号" 那一段。

    dim_prefix:
      - "R" → 取 R1/R2/R3/R4/R5/R6 全部
      - "V" → 取 V1/V2/V3/V4 全部
      - "E" → 取 E1/E2/E3/E4 全部
      - "C" → 取亮点（C-R/C-V/C-E）

    格式（决议方案 A，只渲染 tag_id + 中文名）：
        R1 安全合规: R-SAFE-1 政治敏感与国家安全, R-SAFE-2 仇恨/歧视/攻击性, ...
        R2 意图理解: R-UND-1 完全误解用户意图, ...
    """
    if dim_prefix == "C":
        groups = pack.highlights_by_dim
        dim_names = {"C-R": "可信性亮点", "C-V": "有用性亮点", "C-E": "体验亮点"}
    else:
        groups = pack.signals_by_dim
        dim_names = _DIM_NAMES

    lines: List[str] = []
    for dim, items in groups.items():
        if not dim.startswith(dim_prefix):
            continue
        head = f"{dim} {dim_names.get(dim, '')}".strip()
        body = ", ".join(f"{s['tag_id']} {s['name']}" for s in items)
        lines.append(f"- {head}: {body}" if items else f"- {head}: （无）")
    return "\n".join(lines)


_DIM_NAMES = {
    "R1": "安全合规",
    "R2": "意图理解",
    "R3": "事实",
    "R4": "推理",
    "R5": "风险处理",
    "R6": "跨轮一致性",
    "V1": "信息有效性",
    "V2": "方案解决",
    "V3": "执行质量",
    "V4": "情感智能",
    "E1": "信息结构",
    "E2": "表达适配",
    "E3": "表达自然性",
    "E4": "表达一致性",
}


# ----------------------------------------------------------------------------
# 信号裁剪：按 P0 > P1 > P2 优先 + 维度子上限 + 总上限
# ----------------------------------------------------------------------------
_LEVEL_PRIO = {"P0": 0, "P1": 1, "P2": 2}


def limit_signals(
    signals: List[Dict[str, Any]],
    max_total: int,
    per_dim: Optional[Dict[str, int]] = None,
    pack: Optional[RVECPack] = None,
) -> List[Dict[str, Any]]:
    """按优先级裁剪信号清单。

    1. 先按 R/V/E 主维度分桶
    2. 桶内按 P0/P1/P2 排序
    3. 桶内截断到 per_dim[主维度]（如 R 维度 ≤2）
    4. 全部合并后再按优先级截断到 max_total
    """
    if not signals:
        return []

    per_dim = per_dim or {}

    # 主维度推断（R-SAFE-1 → R）
    def main_dim(s: Dict[str, Any]) -> str:
        tag = str(s.get("tag_id", ""))
        if tag and tag[0] in ("R", "V", "E"):
            return tag[0]
        # fallback: tag_name 也能提取
        return tag[:1] or "?"

    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for s in signals:
        if not isinstance(s, dict):
            continue
        buckets[main_dim(s)].append(s)

    kept: List[Dict[str, Any]] = []
    for dim_key in ("R", "V", "E"):
        bucket = buckets.get(dim_key, [])
        bucket.sort(key=lambda s: _LEVEL_PRIO.get(str(s.get("level", "")).upper(), 9))
        cap = per_dim.get(dim_key)
        kept.extend(bucket[:cap] if cap is not None else bucket)

    # 总上限
    if len(kept) > max_total:
        kept.sort(key=lambda s: _LEVEL_PRIO.get(str(s.get("level", "")).upper(), 9))
        kept = kept[:max_total]
    return kept


def limit_highlights(
    highlights: List[Dict[str, Any]],
    max_total: int,
) -> List[Dict[str, Any]]:
    """亮点裁剪：保持出现顺序截断。"""
    if not highlights:
        return []
    return [h for h in highlights if isinstance(h, dict)][:max_total]


# ----------------------------------------------------------------------------
# 组装 reference_block
# ----------------------------------------------------------------------------
def format_reference_block(
    expected_answer: Optional[str],
    expected_signals: Optional[List[str]],
    pack: Optional[RVECPack] = None,
) -> str:
    """根据 expected.answer 和 metadata.expected_signals 拼出 reference_block。

    两者都没有 → 返回空字符串（zhuguan_prompt.py 已支持）。
    """
    parts: List[str] = []
    if expected_answer:
        parts.append(f"- 参考答案：{str(expected_answer).strip()}")
    if expected_signals:
        if pack is None:
            sig_strs = list(expected_signals)
        else:
            sig_strs = []
            for tag in expected_signals:
                meta = pack.tag_to_signal.get(str(tag))
                if meta:
                    sig_strs.append(f"{tag} {meta.get('name', '')}")
                else:
                    sig_strs.append(str(tag))
        parts.append("- 预期易错信号：" + ", ".join(sig_strs))

    if not parts:
        return ""
    return "【参考基准】\n" + "\n".join(parts)


# ----------------------------------------------------------------------------
# caps 解析（带 mut/baseline 切换 + yaml 覆盖）
# ----------------------------------------------------------------------------
def resolve_caps(
    pack: RVECPack,
    is_baseline: bool,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """合并 pack 默认 caps 与 yaml extra.caps 覆盖，返回扁平结构。

    返回:
        {"bad_total": 5, "good_total": 3, "per_dim": {"R":2,"V":2,"E":1,"C":3}}
    """
    base = pack.caps or {}
    overrides = overrides or {}

    bad_block = base.get("bad_total") or {}
    if isinstance(bad_block, dict):
        bad_default = int(bad_block.get("baseline" if is_baseline else "mut", 5))
    else:
        bad_default = int(bad_block)

    good_default = int(base.get("good_total", 3))
    per_dim_default = dict(base.get("per_dim") or {})

    # yaml 覆盖（兼容多种命名）
    bad_total = int(
        overrides.get("bad_baseline" if is_baseline else "bad_mut")
        or overrides.get("bad_total")
        or bad_default
    )
    good_total = int(overrides.get("good") or overrides.get("good_total") or good_default)
    per_dim = {**per_dim_default, **(overrides.get("per_dim") or {})}

    return {
        "bad_total": bad_total,
        "good_total": good_total,
        "per_dim": per_dim,
    }
