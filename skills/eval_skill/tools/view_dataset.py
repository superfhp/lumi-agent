"""dataset 评测前预览工具：把 RVEC tag_id 反查中文名，让人能看懂 dataset。

背景：
  metadata.expected_signals 存的是 tag_id（V-EMP-2 这种）—— pipeline 友好但人不友好。
  评测执行前，常需要快速预览：
    - 这个 dataset 有多少题？分类分布？
    - 题目 prompt 长啥样？expected_signals 中文化后是什么？
    - 哪些题完全没填 answer/rubric？

  这工具不改 dataset，只读 + 渲染。三种输出：
    - 终端默认：紧凑文本预览（默认前 5 条 + 全局统计）
    - --md FILE：导出 markdown 报告（适合贴 wiki / 同事 review）
    - --csv FILE：导出 csv（excel 打开筛选）

用法：
    # 看 lumi dataset 前 5 条 + 整体统计（最常用）
    python -m eval_skill.tools.view_dataset --source common-dataset

    # 看更多条
    python -m eval_skill.tools.view_dataset --source common-dataset --limit 20

    # 按 category 过滤
    python -m eval_skill.tools.view_dataset --source common-dataset --category 推理分析

    # 只看 expected_output 完全为空的
    python -m eval_skill.tools.view_dataset --source common-dataset --only-empty

    # 导出 markdown 给同事 review
    python -m eval_skill.tools.view_dataset --source common-dataset \\
        --md backup/common-dataset-preview.md

    # 导出 csv（Excel 筛选用）
    python -m eval_skill.tools.view_dataset --source common-dataset \\
        --csv backup/common-dataset-preview.csv

    # 离线看本地 jsonl 备份
    python -m eval_skill.tools.view_dataset --from-jsonl backup/restore.jsonl
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]  # 并非硬依赖，pack 加载失败会降级到 tag_id 原样显示

try:
    from skill_commons import build_lumi_client
except ModuleNotFoundError:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from skill_commons import build_lumi_client


DEFAULT_PACK = "prompts/judge/rvec_general"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


# v2.1 spec：metadata.schema 拆 turn_kind + scoring_mode；老数据没显式标 → 推断
try:
    from eval_skill.core.sample import TURN_KIND_BY_SCHEMA, SCORING_MODE_BY_SCHEMA
except Exception:
    # 极端情况下（脱机/路径问题）退化为空表，下面 _resolve_kind_mode 自然降级
    TURN_KIND_BY_SCHEMA = {}  # type: ignore[assignment]
    SCORING_MODE_BY_SCHEMA = {}  # type: ignore[assignment]


def _resolve_kind_mode(meta: Dict[str, Any], inp: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """从 item 解出 (turn_kind, scoring_mode)。

    注意：历史 common-dataset-v3 里曾把所有带 ``turns`` 的样本都标成
    ``schema=dialog / turn_kind=multi``，其中大量其实只有 1 轮。预览工具面向
    人审，应该按真实输入形态展示：``len(input.turns) >= 2`` 才算 multi；1 轮
    turns 只是单轮样本的兼容容器。
    """
    schema = str(meta.get("schema") or "")
    tk = meta.get("turn_kind") or TURN_KIND_BY_SCHEMA.get(schema, "?")
    if inp is not None:
        turns = _extract_turns(inp)
        if turns:
            tk = "multi" if len(turns) >= 2 else "single"
    sm = meta.get("scoring_mode") or SCORING_MODE_BY_SCHEMA.get(schema, "?")
    return str(tk), str(sm)


# ----------------------------------------------------------------------------
# pack.yaml 轻量读取：只提 tag_id → name，不拉 metrics/_rvec_helpers 重依赖。
# ----------------------------------------------------------------------------
def _load_tag_names(pack_ref: str) -> Tuple[Dict[str, str], str, str]:
    """从领域包里抽取 tag_id → 中文名。

    返回 (tag_to_name, domain, version)。加载失败起 RuntimeError，main 里以警告降级。
    """
    if yaml is None:
        raise RuntimeError("未安装 pyyaml，请 pip install pyyaml")
    pack_dir = Path(pack_ref)
    if not pack_dir.is_absolute():
        pack_dir = PROJECT_ROOT / pack_dir
    if pack_dir.is_file():
        pack_dir = pack_dir.parent
    yaml_path = pack_dir / "pack.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"pack.yaml 不存在：{yaml_path}")
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    tag_to_name: Dict[str, str] = {}
    for s in (data.get("signals") or []):
        tag = str(s.get("tag_id", "")).strip()
        if tag:
            tag_to_name[tag] = str(s.get("name", ""))
    for h in (data.get("highlights") or []):
        tag = str(h.get("tag_id", "")).strip()
        if tag:
            tag_to_name[tag] = str(h.get("name", ""))
    return tag_to_name, str(data.get("domain", "unknown")), str(data.get("version", "v0"))


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def _pick(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {}


def _normalize_one(raw: Any) -> Dict[str, Any]:
    """统一两种来源（langfuse SDK item / langfuse 原生 jsonl）的字段名。"""
    iid = _pick(raw, "id") or _pick(raw, "item_id")
    inp = _as_dict(_pick(raw, "input"))
    eo = _as_dict(
        _pick(raw, "expected_output")
        or _pick(raw, "expectedOutput")
        or _pick(raw, "output")
    )
    meta = _as_dict(_pick(raw, "metadata"))
    return {"id": iid, "input": inp, "expected_output": eo, "metadata": meta}


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                items.append(json.loads(ln))
    return items


def _trunc(s: str, n: int = 80) -> str:
    if not s:
        return ""
    s = s.replace("\n", " ").replace("\r", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _humanize_signals(signals: Any, tag_to_name: Dict[str, str]) -> List[str]:
    """expected_signals: list[tag_id] 或字符串 → ['V-EMP-2 无共情', ...]。

    pack 里查不到的 tag 显示为 'TAG (未知)'，方便发现 pack 升级遗漏。
    """
    if not signals:
        return []
    if isinstance(signals, str):
        # 兜底：还没 migrate 的老字符串形态，按常见分隔符简单切
        import re
        signals = [s.strip() for s in re.split(r"[,/、\s]+", signals) if s.strip()]

    out: List[str] = []
    for tag in signals:
        tag = str(tag).strip()
        name = tag_to_name.get(tag)
        if name:
            out.append(f"{tag} {name}")
        else:
            out.append(f"{tag} (未知)")
    return out


def _is_empty_expected(eo: Dict[str, Any]) -> bool:
    """判断 expected_output 是不是完全空（出题方未填）。"""
    if not eo:
        return True
    for k in ("answer", "rubric", "explanation", "reasoning_ref", "expected_md"):
        v = eo.get(k)
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if isinstance(v, (list, dict)) and not v:
            continue
        return False
    return True


# ----------------------------------------------------------------------------
# 渲染：单条
# ----------------------------------------------------------------------------
def _extract_turns(inp: Dict[str, Any]) -> List[str]:
    """从 input.turns 抽出每轮 content 文本（兼容 string / {content,meta} 两种格式）。"""
    raw = inp.get("turns")
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for t in raw:
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, dict):
            out.append(str(t.get("content", "")))
    return out


def _input_oneline(inp: Dict[str, Any]) -> str:
    """把 input 拍平成一行文本（CSV / 单行预览用）。

    优先级：prompt > question > turns 拼接
    """
    p = inp.get("prompt") or inp.get("question")
    if p:
        return str(p)
    turns = _extract_turns(inp)
    if turns:
        return " | ".join(f"T{i+1}: {t}" for i, t in enumerate(turns))
    return ""


def render_one(it: Dict[str, Any], idx: int, total: int,
               tag_to_name: Dict[str, str]) -> str:
    """把单条 item 渲染成可读文本块。"""
    inp = it["input"]
    eo = it["expected_output"]
    meta = it["metadata"]

    cat = inp.get("category") or meta.get("category") or "(无 category)"
    prompt = inp.get("prompt") or inp.get("question") or ""
    turns = _extract_turns(inp)
    schema = meta.get("schema", "?")
    turn_kind, scoring_mode = _resolve_kind_mode(meta, inp)
    level = inp.get("level") or meta.get("level") or ""
    turn = inp.get("turn") or meta.get("turn") or ""
    sig_human = _humanize_signals(meta.get("expected_signals"), tag_to_name)

    lines = []
    lines.append(f"┌─ [{idx}/{total}] id={it['id']}")
    head = f"│  category={cat}  turn_kind={turn_kind}  scoring={scoring_mode}  legacy_schema={schema}"
    if level:
        head += f"  level={level}"
    if turn:
        head += f"  legacy_turn={turn}"
    lines.append(head)
    if turns:
        lines.append(f"│  prompt ({len(turns)} turns):")
        for i, t in enumerate(turns, 1):
            lines.append(f"│       T{i}: {_trunc(t, 150)}")
    else:
        lines.append(f"│  prompt: {_trunc(prompt, 200)}")

    if sig_human:
        lines.append(f"│  📍 expected_signals:")
        for s in sig_human:
            lines.append(f"│       • {s}")
    else:
        lines.append(f"│  📍 expected_signals: (空)")

    ans = eo.get("answer")
    if ans:
        if isinstance(ans, str):
            lines.append(f"│  📝 answer: {_trunc(ans, 200)}")
        else:
            lines.append(f"│  📝 answer: {ans!r}")

    rub = eo.get("rubric")
    if rub:
        rub_str = rub if isinstance(rub, str) else json.dumps(rub, ensure_ascii=False)
        lines.append(f"│  📋 rubric: {_trunc(rub_str, 150)}")

    if _is_empty_expected(eo):
        if meta.get("expected_signals"):
            lines.append(f"│  ℹ️  expected_output 空；metadata.expected_signals 已填，可用于 RVEC 预期信号对照")
        else:
            lines.append(f"│  ⚠️  expected_output 完全为空（出题方还没填）")

    lines.append("└" + "─" * 68)
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# 渲染：整体统计
# ----------------------------------------------------------------------------
def render_summary(items: List[Dict[str, Any]], tag_to_name: Dict[str, str]) -> str:
    cat_cnt = Counter((it["input"].get("category") or it["metadata"].get("category") or "(无)")
                      for it in items)
    schema_cnt = Counter(it["metadata"].get("schema", "?") for it in items)
    turn_cnt = Counter(_resolve_kind_mode(it["metadata"], it["input"])[0] for it in items)
    score_cnt = Counter(_resolve_kind_mode(it["metadata"], it["input"])[1] for it in items)

    has_signals = sum(1 for it in items if it["metadata"].get("expected_signals"))
    has_answer = sum(1 for it in items if it["expected_output"].get("answer"))
    has_rubric = sum(1 for it in items if it["expected_output"].get("rubric"))
    empty_eo = sum(1 for it in items if _is_empty_expected(it["expected_output"]))
    empty_eo_with_signals = sum(
        1 for it in items
        if _is_empty_expected(it["expected_output"]) and it["metadata"].get("expected_signals")
    )

    # 信号 top 10（中文化）
    sig_freq: Counter = Counter()
    unknown_tags: Counter = Counter()
    for it in items:
        sigs = it["metadata"].get("expected_signals") or []
        if isinstance(sigs, str):
            import re
            sigs = [s.strip() for s in re.split(r"[,/、\s]+", sigs) if s.strip()]
        for tag in sigs:
            tag = str(tag).strip()
            if tag in tag_to_name:
                sig_freq[f"{tag} {tag_to_name[tag]}"] += 1
            else:
                unknown_tags[tag] += 1

    lines = []
    lines.append("=" * 70)
    lines.append("DATASET 概览")
    lines.append("=" * 70)
    lines.append(f"总 item 数: {len(items)}")

    lines.append(f"\n[turn_kind 分布]")
    for k, v in turn_cnt.most_common():
        lines.append(f"  {k!r:20s} {v:4d}")

    lines.append(f"\n[scoring_mode 分布]")
    for k, v in score_cnt.most_common():
        lines.append(f"  {k!r:20s} {v:4d}")

    lines.append(f"\n[schema 分布（向后兼容）]")
    for k, v in schema_cnt.most_common():
        lines.append(f"  {k!r:20s} {v:4d}")

    lines.append(f"\n[category 分布]")
    for k, v in cat_cnt.most_common():
        lines.append(f"  {k!r:30s} {v:4d}")

    lines.append(f"\n[字段填充率]")
    n = len(items)
    lines.append(f"  metadata.expected_signals: {has_signals}/{n}")
    lines.append(f"  expected_output.answer:    {has_answer}/{n}")
    lines.append(f"  expected_output.rubric:    {has_rubric}/{n}")
    if empty_eo:
        lines.append(f"  expected_output 完全为空: {empty_eo}/{n}")
        if empty_eo_with_signals:
            lines.append(f"    其中 metadata.expected_signals 已填: {empty_eo_with_signals}/{empty_eo}（RVEC 可继续用于预期信号对照）")

    if sig_freq:
        lines.append(f"\n[预期信号 top 10（中文化）]")
        for sig, n in sig_freq.most_common(10):
            lines.append(f"  {sig:40s} {n}")

    if unknown_tags:
        lines.append(f"\n⚠️  以下 {len(unknown_tags)} 个 tag_id 在 pack 里查不到（pack 升级或拼写错误）：")
        for tag, n in unknown_tags.most_common():
            lines.append(f"  {tag} (出现 {n} 次)")

    return "\n".join(lines)


# ----------------------------------------------------------------------------
# CSV / Markdown 导出
# ----------------------------------------------------------------------------
def export_csv(items: List[Dict[str, Any]], path: Path,
               tag_to_name: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "id", "category", "turn_kind", "scoring_mode", "schema", "level", "turn",
            "prompt", "answer", "rubric",
            "expected_signals_中文",
            "expected_output_为空",
        ])
        for it in items:
            inp = it["input"]; eo = it["expected_output"]; meta = it["metadata"]
            sigs_human = _humanize_signals(meta.get("expected_signals"), tag_to_name)
            tk, sm = _resolve_kind_mode(meta, inp)
            w.writerow([
                it["id"],
                inp.get("category") or meta.get("category") or "",
                tk, sm,
                meta.get("schema", ""),
                inp.get("level") or meta.get("level") or "",
                inp.get("turn") or meta.get("turn") or "",
                _input_oneline(inp),
                eo.get("answer") or "",
                eo.get("rubric") or "",
                " | ".join(sigs_human),
                "是" if _is_empty_expected(eo) else "",
            ])


def export_markdown(items: List[Dict[str, Any]], path: Path,
                    tag_to_name: Dict[str, str], summary: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append(f"# Dataset 预览\n")
    lines.append("```")
    lines.append(summary)
    lines.append("```\n")
    lines.append(f"## 详细列表（共 {len(items)} 条）\n")

    for idx, it in enumerate(items, 1):
        inp = it["input"]; eo = it["expected_output"]; meta = it["metadata"]
        cat = inp.get("category") or meta.get("category") or "(无)"
        prompt = inp.get("prompt") or inp.get("question") or ""
        turns = _extract_turns(inp)
        sigs_human = _humanize_signals(meta.get("expected_signals"), tag_to_name)

        lines.append(f"### {idx}. `{it['id']}` — {cat}\n")
        tk, sm = _resolve_kind_mode(meta, inp)
        lines.append(f"- **turn_kind / scoring_mode**: `{tk}` / `{sm}`")
        lines.append(f"- **schema** (legacy): `{meta.get('schema', '?')}`")
        if inp.get("level") or meta.get("level"):
            lines.append(f"- **level**: {inp.get('level') or meta.get('level')}")
        if inp.get("turn") or meta.get("turn"):
            lines.append(f"- **turn**: {inp.get('turn') or meta.get('turn')}")
        if turns:
            lines.append(f"- **prompt** ({len(turns)} turns):")
            for i, t in enumerate(turns, 1):
                lines.append(f"  - **T{i}**: {t[:400]}")
        else:
            lines.append(f"- **prompt**:")
            lines.append(f"  > {prompt[:500]}")
        if sigs_human:
            lines.append(f"- **expected_signals**:")
            for s in sigs_human:
                lines.append(f"  - {s}")
        if eo.get("answer"):
            ans = eo['answer']
            ans_s = ans if isinstance(ans, str) else json.dumps(ans, ensure_ascii=False)
            lines.append(f"- **answer**: {ans_s[:500]}")
        if eo.get("rubric"):
            rub = eo['rubric']
            rub_s = rub if isinstance(rub, str) else json.dumps(rub, ensure_ascii=False)
            lines.append(f"- **rubric**: {rub_s[:300]}")
        if _is_empty_expected(eo):
            if meta.get("expected_signals"):
                lines.append(f"- ℹ️ **expected_output 空；metadata.expected_signals 已填，可用于 RVEC 预期信号对照**")
            else:
                lines.append(f"- ⚠️ **expected_output 完全为空**")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src_group = ap.add_mutually_exclusive_group(required=True)
    src_group.add_argument("--source", help="Lumi dataset 名（从 lumi 读取）")
    src_group.add_argument("--from-jsonl", help="本地 jsonl 路径")

    ap.add_argument("--pack", default=DEFAULT_PACK,
                    help=f"RVEC pack 路径，用于 tag_id → 中文 lookup（默认 {DEFAULT_PACK}）")
    ap.add_argument("--limit", type=int, default=5,
                    help="终端最多打印多少条详细预览（默认 5；设 0 则只看统计）")
    ap.add_argument("--category", default=None,
                    help="只看指定 category 的题")
    ap.add_argument("--only-empty", action="store_true",
                    help="只看 expected_output 完全为空的题（找出题方该补什么）")
    ap.add_argument("--csv", default=None,
                    help="导出 csv 路径")
    ap.add_argument("--md", default=None,
                    help="导出 markdown 路径")
    args = ap.parse_args()

    # 加载 pack 用于 tag_id → 中文
    try:
        tag_to_name, domain, version = _load_tag_names(args.pack)
        # version 通常自带 'v' 前缀（如 'v3.0'），不再补 'v' 避免变成 'vv3.0'
        ver = version if version.startswith(("v", "V")) else f"v{version}"
        print(f"[view] 加载 pack: {domain} {ver}（{len(tag_to_name)} 个 tag）")
    except Exception as e:
        print(f"[view] ⚠️ pack 加载失败：{e}；signals 将以 tag_id 原样显示")
        tag_to_name = {}

    # 读 dataset
    if args.from_jsonl:
        raw = _read_jsonl(Path(args.from_jsonl))
        print(f"[view] 读取 jsonl: {args.from_jsonl}（{len(raw)} 条）")
    else:
        client = build_lumi_client()
        ds = client.get_dataset(args.source)
        raw = list(ds.items)
        print(f"[view] 读取 lumi dataset: {args.source!r}（{len(raw)} 条）")

    items = [_normalize_one(it) for it in raw]

    # 过滤
    filtered = items
    if args.category:
        filtered = [
            it for it in filtered
            if (it["input"].get("category") or it["metadata"].get("category") or "") == args.category
        ]
        print(f"[view] --category={args.category!r} 过滤后剩 {len(filtered)} 条")
    if args.only_empty:
        filtered = [it for it in filtered if _is_empty_expected(it["expected_output"])]
        print(f"[view] --only-empty 过滤后剩 {len(filtered)} 条")

    if not filtered:
        print("[view] 无匹配 item")
        return

    # 统计（基于过滤后）
    summary = render_summary(filtered, tag_to_name)
    print()
    print(summary)

    # 终端详细预览
    if args.limit > 0:
        print(f"\n{'=' * 70}")
        print(f"详细预览（前 {min(args.limit, len(filtered))} 条 / 共 {len(filtered)} 条）")
        print("=" * 70)
        for idx, it in enumerate(filtered[: args.limit], 1):
            print()
            print(render_one(it, idx, len(filtered), tag_to_name))

    # 导出
    if args.csv:
        export_csv(filtered, Path(args.csv), tag_to_name)
        print(f"\n[view] 导出 csv → {args.csv}（用 Excel 打开）")

    if args.md:
        export_markdown(filtered, Path(args.md), tag_to_name, summary)
        print(f"[view] 导出 markdown → {args.md}")


if __name__ == "__main__":
    main()
