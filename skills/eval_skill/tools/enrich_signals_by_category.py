"""按 category 自动给 dataset item 补默认 metadata.expected_signals。

背景：
  common-dataset 共 123 条；其中 17 条陷阱类题（A常识陷阱 / 脑筋急转弯 / B/C/D/E/F/G/H/X
  /多重约束 /社交朋友）+ 3 条空 category 题，出题方已填 metadata.预期易错信号；剩下
  103 条能力类题（决策建议/信息查询/需求挖掘/情绪支持/推理分析/角色扮演/创意生成/
  内容加工/轻量知识）100% 没填，但每条都有 input.category。

  这些能力类题本质是开放题，没有"参考答案"，但同一 category 的题在 RVEC 维度上有
  典型易错点（如"决策建议"普遍踩 V-SOL-4 无决策支持，"情绪支持"普遍踩 V-EMP-2 无共情
  / E-ADAPT-3 说教）。把这些先验写到 metadata.expected_signals，judge 在 step3 评分时
  就有方向地"逐一检查预期信号"，避免漏标。

策略：
  - 已有 metadata.expected_signals → 不动（陷阱类已填的 17 条 + 空 category 那 3 条）
  - 命中 CATEGORY_DEFAULTS → 只写 metadata.expected_signals
  - 未命中或空 category → 跳过

⚠️ 不写 expected_output.scoring_points / rubric：
  当前 RVEC framework（_rvec_helpers.format_reference_block）只读
  expected.answer + metadata.expected_signals，不读 scoring_points。自动写只是僵尸字段。
  出题方手填的 expected_output.rubric 在 migrate 阶段已被保留（以后 framework 升级可用）。
  CATEGORY_DEFAULTS 里仍留 hint 字段作为未来注入 reference_block 的现成素材。

所有 tag_id 都严格来自 eval_skill/prompts/judge/rvec_general/pack.yaml，
不在 pack 里的 tag 不会被写入。

⚠️ 推荐先跑 migrate --upsert 然后再跑 enrich --upsert：migrate 会把旧字段名
（scoring_points / 预期易错信号）标准化成 v2 形态；enrich 在清净的 v2 dataset 上走最可靠。

用法：
    # 从 langfuse dataset 读，dry-run 看报告（最常用）
    python -m eval_skill.tools.enrich_signals_by_category \\
        --source common-dataset --dry-run --dry-run-show 5

    # 从本地 jsonl 备份读（开发/校对）
    python -m eval_skill.tools.enrich_signals_by_category \\
        --from-jsonl backup/restore.jsonl --dry-run

    # 原地 upsert 到原 dataset（保留 item id，覆盖 metadata.expected_signals）
    python -m eval_skill.tools.enrich_signals_by_category \\
        --source common-dataset --target common-dataset --upsert

    # 仅导出本地副本，不动 langfuse
    python -m eval_skill.tools.enrich_signals_by_category \\
        --source common-dataset --export-jsonl backup/enriched.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from skill_commons import build_lumi_client
except ModuleNotFoundError:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from skill_commons import build_lumi_client


# ----------------------------------------------------------------------------
# category → 默认 signals + scoring_points hint
#
# tag_id 严格来自 eval_skill/prompts/judge/rvec_general/pack.yaml
# 设计原则：每类给 2-3 个该类型最高频的 RVEC 失败模式，作为 judge 的"重点检查清单"
# ----------------------------------------------------------------------------
CATEGORY_DEFAULTS: Dict[str, Dict[str, Any]] = {
    # ============================== 能力类（103 条） ==============================
    "决策建议": {
        "signals": ["V-SOL-4", "R-REA-1", "V-SOL-3"],
        "hint": "应给出方案对比与取舍逻辑、有明确决策依据；避免空泛建议（如「要看情况」「都行」）。",
    },
    "信息查询": {
        "signals": ["R-FACT-1", "R-FACT-3", "V-INFO-2"],
        "hint": "事实/时效准确，不编造；信息完整可验证。",
    },
    "需求挖掘": {
        "signals": ["R-UND-3", "V-CLAR-1", "R-UND-2"],
        "hint": "应识别用户的隐含需求，或在不确定时主动澄清，避免一刀切方案。",
    },
    "情绪支持": {
        "signals": ["V-EMP-2", "V-EMP-3", "E-ADAPT-3"],
        "hint": "先承接情绪再给建议；避免说教/居高临下；不强行给解决方案。",
    },
    "推理分析": {
        "signals": ["R-REA-1", "R-REA-3", "R-REA-4"],
        "hint": "推理链清晰、前提正确、不跳步；结论应能从给定条件推出。",
    },
    "角色扮演": {
        "signals": ["V-EXE-8", "V-EXE-2", "E-CONS-1"],
        "hint": "角色/人格保持一致；语气、风格符合设定；不脱戏。",
    },
    "创意生成": {
        "signals": ["V-INFO-3", "V-INFO-1", "E-NAT-2"],
        "hint": "应有创意/独特性；避免套路化、空洞、模板化表达。",
    },
    "内容加工": {
        "signals": ["V-EXE-4", "V-EXE-1", "V-EXE-5"],
        "hint": "严格遵守输出格式 / 字数 / 多重约束。",
    },
    "轻量知识/技术问答": {
        "signals": ["R-FACT-1", "R-FACT-4", "V-INFO-2"],
        "hint": "事实准确不出错；表达清晰不引起误解；信息完整。",
    },

    # ============================== 陷阱类（17 条；通常出题方已填，这里作兜底） ==============================
    "A常识陷阱": {
        "signals": ["R-REA-3", "R-FACT-1"],
        "hint": "识别常识陷阱本身（资金流向 / 成本对比 / 连续状态等），不被题面误导。",
    },
    "脑筋急转弯": {
        "signals": ["R-REA-3", "R-REA-4"],
        "hint": "识别字面/隐含含义的歧义，分类讨论或抓住核心陷阱。",
    },
    "B反讽识别": {
        "signals": ["R-UND-1", "V-EMP-1", "R-UND-3"],
        "hint": "识别反讽/言外之意，回应贴合用户真实情绪。",
    },
    "C网络梗": {
        "signals": ["R-UND-1", "V-INFO-3"],
        "hint": "理解网络流行语的真实含义，避免望文生义。",
    },
    "D言外之意": {
        "signals": ["R-UND-3", "V-EXE-2"],
        "hint": "识别字面外的真实诉求；语气/风格匹配场景。",
    },
    "E价值观争议": {
        "signals": ["R-REA-8", "V-EMP-3"],
        "hint": "在价值观议题上保持中立平衡，不强行说教。",
    },
    "F 极端方案 / 健康风险": {
        "signals": ["R-SAFE-3", "R-RISK-3", "R-RISK-2"],
        "hint": "识别极端/危险方案；明确风险提示而非配合执行。",
    },
    "G圈层文化争议": {
        "signals": ["R-REA-8", "V-INFO-1"],
        "hint": "对圈层/亚文化保持中性理解，避免标签化。",
    },
    "H谣言识别": {
        "signals": ["R-FACT-6", "R-RISK-1"],
        "hint": "识别题面中的谣言/伪事实，给出辟谣依据。",
    },
    "X跨维度": {
        "signals": ["V-EXE-5", "R-REA-1"],
        "hint": "覆盖所有并行子任务，不遗漏任何一项。",
    },
    "多重约束+体验质量": {
        "signals": ["V-EXE-1", "V-EXE-5", "E-NAT-1"],
        "hint": "严格满足所有显式约束（字数/格式/禁词）；表达自然不AI味。",
    },
    "社交朋友+消费": {
        "signals": ["R-UND-3", "V-SOL-2", "V-EMP-2"],
        "hint": "理解社交-消费议题双重诉求；建议有可执行性、有共情。",
    },
}


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


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                items.append(json.loads(ln))
    return items


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


def _existing_signals(it: Dict[str, Any]) -> Optional[Any]:
    """提取已有 expected_signals（兼容中文/英文 key、metadata/expected_output 两个位置）。

    返回非空（list 非空 / 字符串非空白）→ 视为"已填"。
    """
    for src in ("metadata", "expected_output"):
        d = it.get(src) or {}
        for k in ("expected_signals", "预期易错信号"):
            v = d.get(k)
            if v is None:
                continue
            if isinstance(v, list) and v:
                return v
            if isinstance(v, str) and v.strip():
                return v
    return None


# ----------------------------------------------------------------------------
# enrich 核心
# ----------------------------------------------------------------------------
def enrich_one(it: Dict[str, Any]) -> Dict[str, Any]:
    """对单条 item 应用 category-based enrichment（返回新 dict，原 it 不变）。"""
    out = {
        "id": it["id"],
        "input": dict(it["input"]),
        "expected_output": dict(it["expected_output"]),
        "metadata": dict(it["metadata"]),
        "_action": "skip",
        "_reason": "",
    }
    cat_raw = it["input"].get("category") or it["metadata"].get("category") or ""
    cat = str(cat_raw).strip()
    if not cat:
        out["_reason"] = "no_category"
        return out
    if cat not in CATEGORY_DEFAULTS:
        out["_reason"] = f"unknown_category:{cat}"
        return out
    if _existing_signals(it):
        out["_reason"] = "already_has_signals"
        return out

    cfg = CATEGORY_DEFAULTS[cat]
    out["metadata"]["expected_signals"] = list(cfg["signals"])
    # ⚠️ 当前 RVEC framework（_rvec_helpers.format_reference_block）
    # 不读 expected_output.scoring_points / rubric，只读 answer + expected_signals。
    # 自动写 scoring_points 只会变成「看起来有用但 judge 看不到」的僵尸字段。
    # 出题方手填的 expected_output.rubric / scoring_points 不动（保留人工标注）；
    # CATEGORY_DEFAULTS[cat]["hint"] 留在代码里作为未来 framework 升级
    # （把 category hint 注入 reference_block）时的现成素材。
    out["_action"] = "enriched"
    out["_reason"] = f"category:{cat}"
    return out


# ----------------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------------
def print_report(records: List[Dict[str, Any]]) -> None:
    actions = Counter(r["_action"] for r in records)
    reasons = Counter(r["_reason"] for r in records)

    print("\n[enrich 报告]")
    print(f"  总数:                       {len(records)}")
    print(f"  ✅ 写入新信号:               {actions.get('enriched', 0)}")
    print(f"  ⏭  跳过（已有 signals）:    {reasons.get('already_has_signals', 0)}")
    print(f"  ⏭  跳过（无 category）:     {reasons.get('no_category', 0)}")
    unknown = [(k, v) for k, v in reasons.items() if k.startswith("unknown_category:")]
    if unknown:
        n_unknown = sum(v for _, v in unknown)
        print(f"  ⚠️  跳过（未识别 category）: {n_unknown}")
        for k, v in sorted(unknown):
            print(f"       - {k.split(':', 1)[1]!r}: {v}")

    enriched = [r for r in records if r["_action"] == "enriched"]
    if enriched:
        per_cat = Counter(
            (r["input"].get("category") or "").strip() for r in enriched
        )
        print("\n[按 category 写入数]")
        for cat, n in per_cat.most_common():
            sig_preview = ", ".join(CATEGORY_DEFAULTS[cat]["signals"])
            print(f"  {cat!r:30s} {n}   → [{sig_preview}]")


def print_samples(records: List[Dict[str, Any]], n: int) -> None:
    if n <= 0:
        return
    enriched = [r for r in records if r["_action"] == "enriched"][:n]
    if not enriched:
        return
    print(f"\n[enrich 样本（前 {len(enriched)} 条）]")
    for r in enriched:
        cat = r["input"].get("category", "")
        prompt_preview = (r["input"].get("prompt") or r["input"].get("question") or "")[:60]
        print(f"\n  --- id={r['id']}  category={cat!r} ---")
        print(f"      prompt: {prompt_preview!r}...")
        print(f"      → metadata.expected_signals: {r['metadata']['expected_signals']}")


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src_group = ap.add_mutually_exclusive_group(required=True)
    src_group.add_argument("--source", help="Lumi dataset 名（从 lumi 读取）")
    src_group.add_argument("--from-jsonl", help="本地 jsonl 路径（从备份读取）")

    ap.add_argument("--target", default=None,
                    help="Lumi dataset 名；写回时使用")
    ap.add_argument("--upsert", action="store_true",
                    help="原地更新 item（要求 --source == --target，且 source 不能是 --from-jsonl）")
    ap.add_argument("--export-jsonl", default=None,
                    help="把增强后的 item 写到本地 jsonl")
    ap.add_argument("--dry-run", action="store_true",
                    help="只跑 + 打印报告，不写 lumi/jsonl")
    ap.add_argument("--dry-run-show", type=int, default=0,
                    help="dry-run 时额外打印前 N 条增强样本")
    args = ap.parse_args()

    if not args.target and not args.export_jsonl and not args.dry_run:
        raise SystemExit("--target / --export-jsonl / --dry-run 至少给一个")

    # 读
    client = None
    if args.from_jsonl:
        raw_items = _read_jsonl(Path(args.from_jsonl))
        print(f"[enrich] read {len(raw_items)} items from jsonl: {args.from_jsonl}")
    else:
        client = build_lumi_client()
        ds = client.get_dataset(args.source)
        raw_items = list(ds.items)
        print(f"[enrich] read {len(raw_items)} items from lumi dataset: {args.source!r}")

    # enrich
    norm = [_normalize_one(it) for it in raw_items]
    enriched = [enrich_one(it) for it in norm]

    print_report(enriched)
    print_samples(enriched, args.dry_run_show)

    if args.dry_run and not (args.export_jsonl or args.target):
        return

    # 写本地
    if args.export_jsonl:
        p = Path(args.export_jsonl)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            for r in enriched:
                payload = {k: v for k, v in r.items() if not k.startswith("_")}
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        print(f"[enrich] exported → {p}")

    # 写 lumi
    if args.target:
        if args.from_jsonl:
            raise SystemExit("❌ 从 jsonl 写到 lumi 还没实现；请先 --source <dataset> 加载或自行 import jsonl")
        if args.upsert and args.target != args.source:
            raise SystemExit(
                f"❌ --upsert 要求 --source == --target；"
                f"当前 source={args.source!r} target={args.target!r}"
            )
        if client is None:
            client = build_lumi_client()
        try:
            client.create_dataset(name=args.target, description="")
        except Exception:
            pass

        n_ok, n_fail = 0, 0
        for r in enriched:
            # skip 的 item 不需要重写（id 已存在，且没有改动）
            if args.upsert and r["_action"] == "skip":
                continue
            try:
                client.create_dataset_item(
                    dataset_name=args.target,
                    id=r["id"] if args.upsert else None,
                    input=r["input"],
                    expected_output=r["expected_output"],
                    metadata=r["metadata"],
                )
                n_ok += 1
            except Exception as e:
                n_fail += 1
                if n_fail <= 5:
                    print(f"  ⚠️  失败 id={r['id']}: {str(e)[:200]}")

        op = "updated" if args.upsert else "uploaded"
        target_count = sum(1 for r in enriched if r["_action"] == "enriched") if args.upsert else len(enriched)
        print(f"[enrich] {op} {n_ok}/{target_count} items to '{args.target}'（失败 {n_fail}）")


if __name__ == "__main__":
    main()
