"""Lumi 上 dataset 清单查询：列出 + 按 metadata.domain 过滤。

约定：
  dataset 级 metadata（lumi/langfuse 一等公民字段）必须有一个 domain 字段
  来声明它属于哪个评测领域，例如 metadata={"domain": "common"}。
  没有 metadata 或没有 domain 的 dataset 视为 **未挂载到 SKILL 流程**，
  list_datasets 默认不返回。这条约定被 SKILL.md「评测前 5 步」流程依赖：
  agent 询问"有哪些 X 领域评测集"时，这里是唯一权威来源。

底层依赖：lumi SDK 的 client.client.datasets.list(page, limit)。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from skill_commons import build_lumi_client
except ModuleNotFoundError:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from skill_commons import build_lumi_client


@dataclass
class DatasetInfo:
    """一个 dataset 的轻量摘要（不拉 items）。"""
    name: str
    description: str
    metadata: Dict[str, Any]
    item_count: Optional[int] = None        # 如果 SDK 返回了就填，没有就 None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def domain(self) -> Optional[str]:
        """从 metadata.domain 取领域；空 metadata 或缺字段 → None。"""
        if not isinstance(self.metadata, dict):
            return None
        v = self.metadata.get("domain")
        return str(v) if v else None

    @property
    def is_active(self) -> bool:
        """有 domain 的视为活跃 dataset；其余视为废弃 / 未挂载。"""
        return self.domain is not None


def _to_dict(obj: Any) -> Dict[str, Any]:
    """把 SDK 返回的 pydantic-like object 转 dict（兼容 dict）。"""
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {}


def _normalize(raw: Any) -> DatasetInfo:
    d = _to_dict(raw)
    meta = d.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    items = d.get("items")
    item_count = len(items) if isinstance(items, list) else d.get("item_count")
    return DatasetInfo(
        name=str(d.get("name", "")),
        description=str(d.get("description") or ""),
        metadata=meta,
        item_count=int(item_count) if isinstance(item_count, int) else None,
        created_at=str(d.get("created_at") or "") or None,
        updated_at=str(d.get("updated_at") or "") or None,
    )


def fetch_all(client: Any, page_limit: int = 50) -> List[DatasetInfo]:
    """分页拉光所有 dataset（不拉 items，轻量）。"""
    out: List[DatasetInfo] = []
    page = 1
    while True:
        resp = client.client.datasets.list(page=page, limit=page_limit)
        # PaginatedDatasets 通常带 .data 和 .meta
        data = getattr(resp, "data", None) or _to_dict(resp).get("data") or []
        if not data:
            break
        for raw in data:
            out.append(_normalize(raw))
        meta = getattr(resp, "meta", None) or _to_dict(resp).get("meta") or {}
        meta_d = _to_dict(meta) if not isinstance(meta, dict) else meta
        total_pages = int(meta_d.get("totalPages") or meta_d.get("total_pages") or 1)
        if page >= total_pages:
            break
        page += 1
    return out


def filter_by_domain(
    items: Iterable[DatasetInfo],
    domain: Optional[str] = None,
    include_inactive: bool = False,
) -> List[DatasetInfo]:
    """按 domain 过滤。

    - domain=None + include_inactive=False → 列出所有 active（有 metadata.domain 的）
    - domain="common" + include_inactive=False → 只列 metadata.domain == "common"
    - include_inactive=True → 把没 metadata.domain 的废弃 dataset 也带上
    """
    out: List[DatasetInfo] = []
    for it in items:
        if not it.is_active and not include_inactive:
            continue
        if domain is not None and it.domain != domain:
            continue
        out.append(it)
    # 排序：active 在前，按 domain → name 排
    out.sort(key=lambda d: (d.domain or "~", d.name))
    return out


def render_table(items: List[DatasetInfo], show_inactive: bool = False) -> str:
    """渲染成终端表格。"""
    if not items:
        return "(无符合条件的 dataset)"

    # 列：domain / name / count / description
    rows = [
        (
            it.domain or "(未挂载)",
            it.name,
            str(it.item_count) if it.item_count is not None else "?",
            (it.description or "").replace("\n", " ").strip()[:60],
        )
        for it in items
    ]
    headers = ("domain", "dataset_name", "items", "description")
    cols = list(zip(*([headers] + rows)))
    widths = [max(len(c) for c in col) for col in cols]

    def fmt(row):
        return "  ".join(c.ljust(w) for c, w in zip(row, widths))

    lines = [fmt(headers), fmt(tuple("-" * w for w in widths))]
    lines.extend(fmt(r) for r in rows)
    return "\n".join(lines)


def cli_main(domain: Optional[str], include_inactive: bool, page_limit: int = 50) -> None:
    """供 cli 调用：连 lumi → 拉 → 过滤 → 渲染。"""
    client = build_lumi_client()
    print(f"[list-datasets] 连接 lumi 中…")
    all_items = fetch_all(client, page_limit=page_limit)
    print(f"[list-datasets] lumi 上共 {len(all_items)} 个 dataset")

    active_n = sum(1 for d in all_items if d.is_active)
    inactive_n = len(all_items) - active_n
    if inactive_n:
        print(f"[list-datasets] 其中 {active_n} 个已挂载（有 metadata.domain），"
              f"{inactive_n} 个未挂载/废弃")

    filtered = filter_by_domain(all_items, domain=domain, include_inactive=include_inactive)
    if domain:
        print(f"[list-datasets] 过滤 domain={domain!r}：剩 {len(filtered)} 条")
    elif include_inactive:
        print(f"[list-datasets] 全部展示（含未挂载）：{len(filtered)} 条")
    else:
        print(f"[list-datasets] 已挂载 dataset：{len(filtered)} 条")

    print()
    print(render_table(filtered, show_inactive=include_inactive))

    if not domain and not include_inactive and inactive_n:
        print()
        print(f"💡 看废弃/未挂载 dataset：加 --include-inactive")
    if not domain and active_n:
        domains = sorted({d.domain for d in all_items if d.domain})
        print(f"💡 已知 domain：{', '.join(domains)}")
        print(f"   按 domain 过滤：--domain <domain>")
