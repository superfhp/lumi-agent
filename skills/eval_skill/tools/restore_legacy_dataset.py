"""从 Langfuse dataset 的历史版本（dataversion）恢复被覆盖的数据。

⚠️ 关键前提
-----------
Langfuse 的版本系统只在后端开启 VERSIONED 模式时才生效（数据库层面要求 dataset_items
表有 valid_from 字段）。如果后端是 STATEFUL 模式（早期 langfuse 版本），所有 ?version=
查询会被静默忽略，永远返回最新版。

所以我们的 probe 必须做"两次对比"才能确定后端真支持版本：
  1) 拉一份当前的 item list
  2) 拉一份带 ?version=ISO_TS 的 item list
  3) 对比是否真的不同。如果完全相同 → STATEFUL 模式，恢复路径死路一条。

走的是哪个 langfuse endpoint
---------------------------
确认能用的是 list 接口（langfuse 测试用例验证过）：
    GET /api/public/dataset-items?datasetName=X&version=ISO_TS&page=1&limit=100

⚠️ 单 item GET (/api/public/dataset-items/{id}?version=...) 在 public API 里没声明
version 参数，后端可能直接忽略——别用！必须走 list。

工作流
------
    # 1) 探针（只读，验证后端是否真支持 versioned）
    python -m eval_skill.tools.restore_legacy_dataset probe \\
        --dataset 你的dataset名 \\
        --version 2026-06-04T07:43:49.484Z

    # 2) 拉历史快照（只读，全量分页）
    python -m eval_skill.tools.restore_legacy_dataset snapshot \\
        --dataset 你的dataset名 \\
        --version 2026-06-04T07:43:49.484Z \\
        --out backup/restore_$(date +%Y%m%d_%H%M%S).jsonl

    # 3) dry-run 看会写什么
    python -m eval_skill.tools.restore_legacy_dataset restore \\
        --dataset 你的dataset名 --from backup/xxx.jsonl

    # 4) 确认无误后真写（--apply）
    python -m eval_skill.tools.restore_legacy_dataset restore \\
        --dataset 你的dataset名 --from backup/xxx.jsonl --apply
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from skill_commons import build_lumi_client
except ModuleNotFoundError:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from skill_commons import build_lumi_client


# ============================================================
# 底层 HTTP 工具：绕过 SDK 直接打 langfuse REST API
# ============================================================
def _get_httpx_and_auth(client) -> Tuple[Any, str, Tuple[str, str]]:
    """从 langfuse Langfuse 实例上抽出底层 httpx + base_url + Basic Auth。"""
    httpx_client = client.httpx_client
    base_url = client.base_url.rstrip("/")
    wrapper = getattr(client.client, "_client_wrapper", None)
    if wrapper is None:
        raise RuntimeError("拿不到 langfuse 内部 client_wrapper")
    username = getattr(wrapper, "_username", None) or getattr(wrapper, "username", None)
    password = getattr(wrapper, "_password", None) or getattr(wrapper, "password", None)
    if callable(username):
        username = username()
    if callable(password):
        password = password()
    if not username or not password:
        raise RuntimeError("拿不到 langfuse public_key/secret_key")
    return httpx_client, base_url, (username, password)


def _list_dataset_items(
    client,
    dataset_name: str,
    version: Optional[str] = None,
    page: int = 1,
    limit: int = 100,
) -> Dict[str, Any]:
    """打 GET /api/public/dataset-items?datasetName=...&version=...&page=...&limit=...

    langfuse 的 public API list endpoint，确认接受 version 参数（langfuse 后端测试 case
    验证过）。返回 {data: [...items], meta: {...pagination}}.
    """
    httpx_client, base_url, auth = _get_httpx_and_auth(client)
    url = f"{base_url}/api/public/dataset-items"
    params: Dict[str, Any] = {
        "datasetName": dataset_name,
        "page": page,
        "limit": limit,
    }
    if version:
        params["version"] = version
    resp = httpx_client.get(url, params=params, auth=auth, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def _fetch_all_items(
    client, dataset_name: str, version: Optional[str], page_limit: int = 100,
    sleep_ms: int = 0,
) -> List[Dict[str, Any]]:
    """分页拉光所有 item。"""
    out: List[Dict[str, Any]] = []
    page = 1
    while True:
        body = _list_dataset_items(client, dataset_name, version=version,
                                   page=page, limit=page_limit)
        chunk = body.get("data") or []
        out.extend(chunk)
        meta = body.get("meta") or {}
        total_pages = int(meta.get("totalPages") or 1)
        if page >= total_pages or not chunk:
            break
        page += 1
        if sleep_ms:
            time.sleep(sleep_ms / 1000.0)
    return out


def _hash_item(it: Dict[str, Any]) -> str:
    """item 关键字段的稳定哈希，用于跨调用比对。"""
    snap = {
        "input": it.get("input"),
        "expectedOutput": it.get("expectedOutput"),
        "metadata": it.get("metadata"),
    }
    return json.dumps(snap, sort_keys=True, ensure_ascii=False)


# ============================================================
# probe：全量按 id 对齐对比，验证后端是否真支持 versioned
# ============================================================
def _print_sample_diff(it_old: Dict[str, Any], it_new: Dict[str, Any]) -> None:
    """打印一条 item 的历史版 vs 当前版差异。"""
    iid = it_old.get("id") or it_new.get("id")
    print(f"\n  示例 id={iid}:")
    old_input_keys = list((it_old.get("input") or {}).keys())
    new_input_keys = list((it_new.get("input") or {}).keys())
    print(f"    历史 input keys: {old_input_keys}")
    print(f"    当前 input keys: {new_input_keys}")
    print(f"    历史 input:      {json.dumps(it_old.get('input'), ensure_ascii=False)[:300]}")
    print(f"    当前 input:      {json.dumps(it_new.get('input'), ensure_ascii=False)[:300]}")
    print(f"    历史 expected:   {json.dumps(it_old.get('expectedOutput'), ensure_ascii=False)[:300]}")
    print(f"    当前 expected:   {json.dumps(it_new.get('expectedOutput'), ensure_ascii=False)[:300]}")
    print(f"    历史 metadata:   {json.dumps(it_old.get('metadata'), ensure_ascii=False)[:300]}")
    print(f"    当前 metadata:   {json.dumps(it_new.get('metadata'), ensure_ascii=False)[:300]}")


def cmd_probe(args: argparse.Namespace) -> None:
    client = build_lumi_client()
    print(f"[probe] base_url = {client.base_url}")
    print(f"[probe] dataset  = {args.dataset}")
    print(f"[probe] version  = {args.version}")

    # 注意：不能只比 page 1 的 10 条！因为历史版和当前版的排序可能完全不同，
    # 同一页的 id 集合可能 0 交集，但全量按 id 对齐后才能看出真正差异。
    print("\n[probe] 步骤 A: 全量拉当前最新版 ...")
    try:
        latest = _fetch_all_items(client, args.dataset, version=None,
                                  page_limit=args.page_limit)
    except Exception as e:
        print(f"  ❌ 连最新版列表都拉不到：{e}")
        sys.exit(1)
    print(f"  ✅ 最新版 {len(latest)} 条")
    if not latest:
        print("  ⚠️  这个 dataset 当前一条 item 也没有，没法做版本对比")
        sys.exit(1)

    print(f"\n[probe] 步骤 B: 全量拉 version={args.version} ...")
    try:
        old = _fetch_all_items(client, args.dataset, version=args.version,
                               page_limit=args.page_limit)
    except Exception as e:
        print(f"  ❌ 带 version 参数请求失败：{e}")
        print("     → 后端可能不支持版本查询，本工具无法用于恢复")
        sys.exit(2)
    print(f"  ✅ 历史版 {len(old)} 条")

    # 按 id 对齐
    latest_by_id = {it["id"]: it for it in latest}
    old_by_id = {it["id"]: it for it in old}
    common_ids = set(latest_by_id) & set(old_by_id)
    only_old = set(old_by_id) - set(latest_by_id)
    only_new = set(latest_by_id) - set(old_by_id)

    n_diff = sum(
        1 for iid in common_ids
        if _hash_item(old_by_id[iid]) != _hash_item(latest_by_id[iid])
    )
    n_same = len(common_ids) - n_diff

    print(f"\n[probe] 全量对比结果:")
    print(f"  - 共有 id 内容相同:  {n_same} 条")
    print(f"  - 共有 id 内容不同:  {n_diff} 条")
    print(f"  - 仅历史版有的 id:   {len(only_old)} 条")
    print(f"  - 仅当前版有的 id:   {len(only_new)} 条")

    has_diff = (n_diff > 0) or (len(only_old) > 0) or (len(only_new) > 0)

    if has_diff:
        print("\n✅ 后端 VERSIONED 模式生效，可以恢复数据！")
        # 优先显示"内容不同"的示例（用户最关心的覆盖场景）
        for iid in common_ids:
            if _hash_item(old_by_id[iid]) != _hash_item(latest_by_id[iid]):
                _print_sample_diff(old_by_id[iid], latest_by_id[iid])
                break
        if only_old:
            iid = next(iter(only_old))
            print(f"\n  仅历史版示例 id={iid}（说明这条已被删除，恢复会重新插入）:")
            print(f"    {json.dumps(old_by_id[iid].get('input'), ensure_ascii=False)[:300]}")
        print(f"\n下一步：")
        print(f"  python -m eval_skill.tools.restore_legacy_dataset snapshot \\")
        print(f"      --dataset {args.dataset} --version {args.version} \\")
        print(f"      --out backup/restore_$(date +%Y%m%d_%H%M%S).jsonl")
    else:
        print("\n❌ 历史版与当前版完全一致，无法恢复")
        print("   可能性 1: 后端是 STATEFUL 模式，version 参数被静默忽略")
        print("   可能性 2: 你给的时间戳已经在污染之后 → 试更早的时间戳")
        print("\n如何判断：")
        print(" - 在 UI 上随便挑一条 item，看版本下拉里有几个时间戳")
        print(" - 只有 1 个时间戳 → 后端没存版本")
        print(" - 有多个时间戳 → 改用更早的时间戳重跑 probe")


# ============================================================
# snapshot：批量拉历史版本
# ============================================================
def cmd_snapshot(args: argparse.Namespace) -> None:
    client = build_lumi_client()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[snapshot] dataset  = {args.dataset}")
    print(f"[snapshot] version  = {args.version}")
    print(f"[snapshot] out      = {out_path}")

    items = _fetch_all_items(
        client, args.dataset, version=args.version,
        page_limit=args.page_limit, sleep_ms=args.sleep_ms,
    )
    print(f"[snapshot] 拉到 {len(items)} 条 item")

    # 防呆：和当前最新版对比
    latest_items = _fetch_all_items(client, args.dataset, version=None,
                                    page_limit=args.page_limit, sleep_ms=args.sleep_ms)
    latest_by_id = {it["id"]: _hash_item(it) for it in latest_items}
    n_diff = sum(1 for it in items
                 if it["id"] in latest_by_id and _hash_item(it) != latest_by_id[it["id"]])
    n_only_old = sum(1 for it in items if it["id"] not in latest_by_id)
    n_only_new = sum(1 for it in latest_items if it["id"] not in {x["id"] for x in items})
    print(f"[snapshot] 与最新版对比: 内容不同 {n_diff} 条, "
          f"仅历史版有 {n_only_old} 条, 仅当前版有 {n_only_new} 条")

    if n_diff == 0 and n_only_old == 0:
        print("⚠️  历史版与最新版完全一致（既不删也不改），写出 jsonl 没意义。"
              "如果你确定数据被改过，请试更早的时间戳。")
        if not args.force:
            print("    （加 --force 跳过此检查继续写出）")
            sys.exit(1)

    with out_path.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    print(f"[snapshot] 已写出 {len(items)} 条到 {out_path}")
    print(f"\n下一步：dry-run 看 restore 会改什么")
    print(f"  python -m eval_skill.tools.restore_legacy_dataset restore \\")
    print(f"      --dataset {args.dataset} --from {out_path}")


# ============================================================
# restore：从 jsonl 写回
# ============================================================
def _to_upsert_payload(rec: Dict[str, Any]) -> Dict[str, Any]:
    """从 list API 返回的 item 字典提取 upsert 需要的字段。"""
    return {
        "id": rec["id"],
        "input": rec.get("input"),
        "expected_output": rec.get("expectedOutput"),
        "metadata": rec.get("metadata"),
    }


def cmd_restore(args: argparse.Namespace) -> None:
    src = Path(args.from_jsonl)
    if not src.exists():
        raise SystemExit(f"❌ 找不到 {src}")
    if not args.apply:
        print("[restore] 🔒 dry-run 模式（不会真写 Lumi）；要真写请加 --apply")
    print(f"[restore] dataset = {args.dataset}")
    print(f"[restore] from    = {src}")

    client = build_lumi_client()

    # 先拉当前版本做 diff
    print("[restore] 拉当前最新版做 diff ...")
    cur = _fetch_all_items(client, args.dataset, version=None, page_limit=100)
    cur_by_id = {it["id"]: it for it in cur}

    n_total = 0
    n_changed = 0
    n_same = 0
    n_new = 0
    n_applied = 0
    failed: List[Tuple[Any, str]] = []
    sample_diffs: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []

    with src.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            n_total += 1
            try:
                rec = json.loads(line)
                payload = _to_upsert_payload(rec)
            except Exception as e:
                failed.append((f"line {line_no}", f"parse fail: {e}"))
                continue

            cur_item = cur_by_id.get(payload["id"])
            if cur_item is None:
                n_new += 1
            else:
                cur_hash = _hash_item(cur_item)
                new_hash = _hash_item({
                    "input": payload["input"],
                    "expectedOutput": payload["expected_output"],
                    "metadata": payload["metadata"],
                })
                if cur_hash == new_hash:
                    n_same += 1
                    continue
                n_changed += 1
                if len(sample_diffs) < 3:
                    sample_diffs.append((
                        payload["id"],
                        {
                            "input": cur_item.get("input"),
                            "expected": cur_item.get("expectedOutput"),
                            "metadata": cur_item.get("metadata"),
                        },
                        {
                            "input": payload["input"],
                            "expected": payload["expected_output"],
                            "metadata": payload["metadata"],
                        },
                    ))

            if args.limit and (n_changed + n_new) > args.limit:
                continue

            if not args.apply:
                continue

            try:
                client.create_dataset_item(
                    dataset_name=args.dataset,
                    id=payload["id"],
                    input=payload["input"],
                    expected_output=payload["expected_output"],
                    metadata=payload["metadata"],
                )
                n_applied += 1
                if n_applied % 20 == 0:
                    print(f"  已 upsert {n_applied} ...")
            except Exception as e:
                failed.append((payload["id"], str(e)[:200]))

    print(f"\n[restore] 共 {n_total} 条:")
    print(f"  - 会修改 {n_changed} 条（当前与历史版不同）")
    print(f"  - 新增  {n_new} 条（当前 dataset 不存在该 id）")
    print(f"  - 跳过  {n_same} 条（已经一致）")

    if sample_diffs:
        print("\n[restore] 前 3 条变化示例:")
        for iid, before, after in sample_diffs:
            print(f"\n  id={iid}")
            for field in ("input", "expected", "metadata"):
                b = json.dumps(before.get(field), ensure_ascii=False)[:300]
                a = json.dumps(after.get(field), ensure_ascii=False)[:300]
                print(f"    {field:>9} BEFORE: {b}")
                print(f"    {field:>9} AFTER : {a}")

    if not args.apply:
        print(f"\n  🔒 dry-run，确认无误后加 --apply 真正执行")
    else:
        print(f"\n[restore] 完成 upsert: {n_applied} 条")
        if failed:
            print(f"[restore] ⚠️  {len(failed)} 条失败：")
            for iid, msg in failed[:5]:
                print(f"     - {iid}: {msg}")


# ============================================================
# CLI
# ============================================================
def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="验证后端是否真支持版本查询（全量按 id 对齐对比）")
    p.add_argument("--dataset", required=True)
    p.add_argument("--version", required=True,
                   help="ISO 时间戳，如 2026-06-04T07:43:49.484Z")
    p.add_argument("--page-limit", type=int, default=100, help="单页大小")
    p.set_defaults(func=cmd_probe)

    s = sub.add_parser("snapshot", help="拉某个 version 的全量 item 到本地 jsonl")
    s.add_argument("--dataset", required=True)
    s.add_argument("--version", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--page-limit", type=int, default=100, help="单页大小")
    s.add_argument("--sleep-ms", type=int, default=0,
                   help="每页之间睡眠 ms（防止打爆后端）")
    s.add_argument("--force", action="store_true",
                   help="即使历史版与当前版一致也写 jsonl")
    s.set_defaults(func=cmd_snapshot)

    r = sub.add_parser("restore", help="从 jsonl 写回 Lumi（默认 dry-run）")
    r.add_argument("--dataset", required=True)
    r.add_argument("--from", dest="from_jsonl", required=True)
    r.add_argument("--apply", action="store_true",
                   help="真正执行 upsert；不加这个 flag 默认 dry-run")
    r.add_argument("--limit", type=int, default=0)
    r.set_defaults(func=cmd_restore)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
