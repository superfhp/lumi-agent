#!/usr/bin/env python3
"""诊断脚本：按用户 / 日期 / 关键词筛选 session 和消息记录。

用法示例:
    # 列出某一天的所有 session
    python scripts/inspect_sessions.py --date 2026-06-14

    # 按 user_id 筛选
    python scripts/inspect_sessions.py --user 123456 --date 2026-06-14

    # 按来源平台筛选 (telegram / discord / cli / ...)
    python scripts/inspect_sessions.py --source telegram --date 2026-06-14

    # 查看某个 session 的完整消息记录
    python scripts/inspect_sessions.py --session 20260614_103025_abc123

    # 按关键词搜索消息内容
    python scripts/inspect_sessions.py --search "media_data" --date 2026-06-14

    # 只看 tool 调用记录
    python scripts/inspect_sessions.py --session 20260614_103025_abc123 --tools-only

    # 跟踪技能调用 — 列出所有含 skill 关键字的消息
    python scripts/inspect_sessions.py --session 20260614_103025_abc123 --skill-trace

    # 跟踪压缩事件 — 列出压缩分裂的 session 链
    python scripts/inspect_sessions.py --session 20260614_103025_abc123 --compression-chain

    # 导出某 session 的完整对话为 JSON
    python scripts/inspect_sessions.py --session 20260614_103025_abc123 --export out.json

    # 指定自定义 state.db 路径
    python scripts/inspect_sessions.py --db /path/to/state.db --date 2026-06-14

    # 指定 HERMES_HOME (用于 profile 场景)
    python scripts/inspect_sessions.py --hermes-home ~/.hermes/profiles/work --date 2026-06-14
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts_to_str(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _ts_to_local(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _truncate(s: str | None, maxlen: int = 120) -> str:
    if not s:
        return ""
    s = s.replace("\n", " ").replace("\r", "")
    return s[:maxlen] + ("…" if len(s) > maxlen else "")


def _date_to_ts_range(date_str: str) -> tuple[float, float]:
    """Convert 'YYYY-MM-DD' to (start_ts, end_ts) in local timezone."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    start = d.timestamp()
    end = (d.replace(hour=23, minute=59, second=59)).timestamp()
    return start, end


def _resolve_db_path(args) -> Path:
    if args.db:
        return Path(args.db)
    hermes_home = args.hermes_home or os.environ.get("HERMES_HOME")
    if hermes_home:
        return Path(hermes_home) / "state.db"
    return Path.home() / ".hermes" / "state.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        print(f"错误：找不到数据库文件 {db_path}", file=sys.stderr)
        print(f"提示：默认路径为 ~/.hermes/state.db，可用 --db 指定", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _sqlite_literal(value) -> str:
    """Compact display helper for arbitrary SQLite values."""
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    return _truncate(str(value), 240)


def _parse_model_config(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _list_user_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type IN ('table', 'view')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """).fetchall()
    return [r["name"] for r in rows]


def cmd_tables(conn: sqlite3.Connection, args):
    """列出所有表/视图和行数。"""
    rows = conn.execute("""
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE type IN ('table', 'view')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
    """).fetchall()

    if not rows:
        print("未找到用户表。")
        return

    print(f"\n{'='*110}")
    print("  SQLite 表/视图")
    print(f"{'='*110}\n")
    print(f"  {'类型':8s} {'名称':35s} {'行数':>12s}  说明")
    print(f"  {'-'*8} {'-'*35} {'-'*12}  {'-'*40}")

    for r in rows:
        name = r["name"]
        try:
            cnt = conn.execute(f'SELECT COUNT(*) AS cnt FROM "{name}"').fetchone()["cnt"]
        except Exception:
            cnt = "?"
        hint = ""
        if name == "sessions":
            hint = "session 元信息"
        elif name == "messages":
            hint = "对话消息 / 工具调用结果"
        elif name.startswith("messages_fts"):
            hint = "全文搜索索引"
        elif name == "state_meta":
            hint = "状态元数据"
        elif name == "schema_version":
            hint = "schema 版本"
        print(f"  {r['type']:8s} {name:35s} {str(cnt):>12s}  {hint}")
    print()


def cmd_schema(conn: sqlite3.Connection, args):
    """显示数据库 schema 或指定表 schema。"""
    if args.dump_table:
        names = [args.dump_table]
    else:
        names = _list_user_tables(conn)

    print(f"\n{'='*110}")
    print("  SQLite Schema")
    print(f"{'='*110}\n")

    for name in names:
        row = conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name = ?",
            (name,),
        ).fetchone()
        if not row:
            print(f"未找到表/视图: {name}")
            continue
        print(f"\n-- {row['type']}: {row['name']}")
        print(row["sql"] or "(no SQL)")
        try:
            cols = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            if cols:
                print("\n列:")
                for c in cols:
                    print(
                        f"  - {c['name']} {c['type'] or ''} "
                        f"{'PRIMARY KEY' if c['pk'] else ''} "
                        f"{'NOT NULL' if c['notnull'] else ''}"
                    )
        except Exception:
            pass
        print()


def cmd_dump_table(conn: sqlite3.Connection, args):
    """抽样显示指定表，或所有表。"""
    tables = _list_user_tables(conn) if args.dump_all_tables else [args.dump_table]
    limit = args.table_limit or args.limit or 20

    for table in tables:
        if not table:
            continue
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
            (table,),
        ).fetchone()
        if not exists:
            print(f"未找到表/视图: {table}")
            continue

        try:
            total = conn.execute(f'SELECT COUNT(*) AS cnt FROM "{table}"').fetchone()["cnt"]
        except Exception:
            total = "?"

        try:
            cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            col_names = [c["name"] for c in cols]
        except Exception:
            col_names = []

        order_sql = ""
        if "started_at" in col_names:
            order_sql = " ORDER BY started_at DESC"
        elif "timestamp" in col_names:
            order_sql = " ORDER BY timestamp DESC"
        elif "id" in col_names:
            order_sql = " ORDER BY id DESC"

        print(f"\n{'='*120}")
        print(f"  表: {table}  总行数: {total}  显示: {limit}")
        print(f"{'='*120}")

        try:
            rows = conn.execute(f'SELECT * FROM "{table}"{order_sql} LIMIT ?', (limit,)).fetchall()
        except Exception as exc:
            print(f"读取失败: {exc}")
            continue

        if not rows:
            print("  (空表)")
            continue

        for idx, row in enumerate(rows, 1):
            print(f"\n  ┌─ row {idx}")
            for key in row.keys():
                value = row[key]
                if key in {"started_at", "ended_at", "timestamp"} and value:
                    print(f"  │  {key}: {_sqlite_literal(value)}  ({_ts_to_local(float(value))})")
                else:
                    print(f"  │  {key}: {_sqlite_literal(value)}")
            print("  └" + "─" * 80)
        print()

def cmd_list_users(conn: sqlite3.Connection, args):
    """列出数据库中所有不同的 user_id。"""
    rows = conn.execute("""
        SELECT s.user_id, s.source, COUNT(*) as cnt,
               MIN(datetime(s.started_at, 'unixepoch', 'localtime')) as first_seen,
               MAX(datetime(s.started_at, 'unixepoch', 'localtime')) as last_seen
        FROM sessions s
        WHERE s.user_id IS NOT NULL AND s.user_id != ''
        GROUP BY s.user_id, s.source
        ORDER BY last_seen DESC
    """).fetchall()

    if not rows:
        print("\n数据库中没有任何 user_id 记录。")
        print("提示：旧版 api_server/OpenWebUI 路径没有把 OpenAI 请求里的 user/header 写入 state.db，")
        print("      所以历史记录只能按 --source api_server、--date、--search、--session 筛选。")
        print("      CLI 模式也不会记录 user_id。")
        print("      新版本修复后，客户端传 body.user 或 X-OpenWebUI-User-Id 时才会有 user_id。")
        return

    missing = conn.execute("""
        SELECT source, COUNT(*) as cnt
        FROM sessions
        WHERE user_id IS NULL OR user_id = ''
        GROUP BY source
        ORDER BY cnt DESC
    """).fetchall()

    print(f"\n{'='*90}")
    print(f"  数据库中的 user_id 列表")
    print(f"{'='*90}\n")
    print(f"  {'user_id':25s} {'平台':12s} {'session数':>10s} {'首次出现':20s} {'最后出现':20s}")
    print(f"  {'-'*25} {'-'*12} {'-'*10} {'-'*20} {'-'*20}")
    for r in rows:
        print(f"  {str(r['user_id']):25s} {r['source']:12s} {r['cnt']:>10} {r['first_seen']:20s} {r['last_seen']:20s}")
    if missing:
        print("\n  另有以下 session 没有 user_id：")
        for r in missing:
            print(f"    {r['source']:20s} {r['cnt']:>8} sessions")
    print()


def cmd_list_sessions(conn: sqlite3.Connection, args):
    """列出 session 列表。"""
    where = []
    params = []

    if args.date:
        start, end = _date_to_ts_range(args.date)
        where.append("s.started_at BETWEEN ? AND ?")
        params.extend([start, end])

    if args.user:
        where.append("s.user_id = ?")
        params.append(args.user)

    if args.source:
        where.append("s.source = ?")
        params.append(args.source)

    if getattr(args, 'model', None):
        where.append("s.model LIKE ?")
        params.append(f"%{args.model}%")

    if getattr(args, 'title', None):
        where.append("s.title LIKE ?")
        params.append(f"%{args.title}%")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    query = f"""
        SELECT s.*,
            (SELECT SUBSTR(REPLACE(REPLACE(m.content, X'0A', ' '), X'0D', ' '), 1, 120)
             FROM messages m
             WHERE m.session_id = s.id AND m.role = 'user' AND m.content IS NOT NULL
             ORDER BY m.timestamp, m.id LIMIT 1
            ) AS preview,
            (SELECT COUNT(*) FROM messages m2 WHERE m2.session_id = s.id AND m2.role = 'tool') AS tool_count_actual
        FROM sessions s
        {where_sql}
        ORDER BY s.started_at DESC
        LIMIT ?
    """
    params.append(args.limit or 50)

    rows = conn.execute(query, params).fetchall()

    if not rows:
        print("未找到匹配的 session。")
        return

    print(f"\n{'='*120}")
    print(f"  找到 {len(rows)} 个 session")
    print(f"{'='*120}\n")

    for r in rows:
        model_config = _parse_model_config(r["model_config"])
        chat_id = model_config.get("chat_id")
        chat_type = model_config.get("chat_type")
        user_name = model_config.get("user_name")
        cost = f"${r['estimated_cost_usd']:.4f}" if r['estimated_cost_usd'] else "-"
        end_reason = r['end_reason'] or "active"
        parent = f"  ← parent: {r['parent_session_id'][:20]}" if r['parent_session_id'] else ""
        title = f"  [{r['title']}]" if r['title'] else ""

        print(f"  📋 {r['id']}{title}")
        print(f"     来源: {r['source']}  用户: {r['user_id'] or '-'}  模型: {r['model'] or '-'}")
        if user_name or chat_id or chat_type:
            print(f"     OpenWebUI: user_name={user_name or '-'}  chat_id={chat_id or '-'}  chat_type={chat_type or '-'}")
        print(f"     时间: {_ts_to_local(r['started_at'])} → {_ts_to_local(r['ended_at']) if r['ended_at'] else '进行中'}")
        print(f"     消息数: {r['message_count']}  工具调用: {r['tool_count_actual']}  API调用: {r['api_call_count']}")
        print(f"     Token: in={r['input_tokens'] or 0}  out={r['output_tokens'] or 0}  "
              f"cache_r={r['cache_read_tokens'] or 0}  reasoning={r['reasoning_tokens'] or 0}")
        print(f"     费用: {cost}  结束原因: {end_reason}{parent}")
        print(f"     预览: {_truncate(r['preview'], 100)}")
        print()


def cmd_show_session(conn: sqlite3.Connection, args):
    """展示某个 session 的完整消息记录。"""
    session_id = args.session

    # 支持前缀匹配
    exact = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not exact:
        like = conn.execute(
            "SELECT id FROM sessions WHERE id LIKE ? ORDER BY started_at DESC LIMIT 5",
            (f"{session_id}%",),
        ).fetchall()
        if len(like) == 1:
            session_id = like[0]["id"]
        elif like:
            print(f"模糊匹配到多个 session：")
            for m in like:
                print(f"  - {m['id']}")
            return
        else:
            print(f"未找到 session: {session_id}")
            return

    # Session 元信息
    sess = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    print(f"\n{'='*120}")
    print(f"  Session: {sess['id']}")
    print(f"  来源: {sess['source']}  用户: {sess['user_id'] or '-'}  模型: {sess['model'] or '-'}")
    print(f"  时间: {_ts_to_local(sess['started_at'])} → {_ts_to_local(sess['ended_at']) if sess['ended_at'] else '进行中'}")
    print(f"  结束原因: {sess['end_reason'] or 'active'}")
    if sess['parent_session_id']:
        print(f"  父 session: {sess['parent_session_id']}")
    print(f"{'='*120}\n")

    # 消息列表
    msgs = conn.execute(
        "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp, id",
        (session_id,),
    ).fetchall()

    if not msgs:
        print("  (该 session 没有消息记录)")
        return

    for i, m in enumerate(msgs, 1):
        role = m["role"]
        content = m["content"] or ""
        tool_name = m["tool_name"]
        tool_calls_raw = m["tool_calls"]
        ts = _ts_to_local(m["timestamp"])

        # 过滤模式
        if args.tools_only and role not in ("tool",) and not tool_calls_raw:
            continue

        if args.skill_trace:
            is_skill = False
            if "__hermes_skill_invocation__" in content:
                is_skill = True
            if tool_name and "skill" in tool_name.lower():
                is_skill = True
            if tool_calls_raw:
                try:
                    tcs = json.loads(tool_calls_raw) if isinstance(tool_calls_raw, str) else tool_calls_raw
                    if any("skill" in (tc.get("function", {}).get("name", "")).lower() for tc in tcs):
                        is_skill = True
                except Exception:
                    pass
            if not is_skill:
                continue

        # 角色颜色标识
        role_icon = {"system": "🔧", "user": "👤", "assistant": "🤖", "tool": "⚙️"}.get(role, "❓")

        print(f"  ┌─ [{i}] {role_icon} {role.upper()}  @ {ts}  (msg_id={m['id']})")

        # 工具调用
        if tool_calls_raw:
            try:
                tcs = json.loads(tool_calls_raw) if isinstance(tool_calls_raw, str) else tool_calls_raw
                for tc in (tcs if isinstance(tcs, list) else []):
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "?")
                    fn_args = fn.get("arguments", "{}")
                    if isinstance(fn_args, str):
                        try:
                            fn_args = json.loads(fn_args)
                        except Exception:
                            pass
                    # 简化显示 arguments
                    if isinstance(fn_args, dict):
                        display_args = {}
                        for k, v in fn_args.items():
                            sv = str(v)
                            display_args[k] = sv[:100] + ("…" if len(sv) > 100 else "")
                        args_str = json.dumps(display_args, ensure_ascii=False)
                    else:
                        args_str = _truncate(str(fn_args), 200)
                    print(f"  │  📞 CALL: {fn_name}({args_str})")
            except Exception:
                print(f"  │  📞 CALL: (parse error) {_truncate(str(tool_calls_raw), 200)}")

        # 工具结果
        if role == "tool":
            print(f"  │  🔩 tool_name={tool_name}  call_id={m['tool_call_id'] or '-'}")

        # 内容
        if content:
            # 技能调用高亮
            if "__hermes_skill_invocation__" in content:
                print(f"  │  ⚡⚡⚡ SKILL INVOCATION DETECTED ⚡⚡⚡")

            # 压缩摘要高亮
            if "CONTEXT COMPACTION" in content or "CONTEXT SUMMARY" in content:
                print(f"  │  🗜️🗜️🗜️ CONTEXT COMPRESSION SUMMARY 🗜️🗜️🗜️")

            lines = content.split("\n")
            max_lines = 5 if (args.tools_only or args.skill_trace) else 30
            for line in lines[:max_lines]:
                print(f"  │  {_truncate(line, 150)}")
            if len(lines) > max_lines:
                print(f"  │  ... ({len(lines) - max_lines} more lines, {len(content)} chars total)")

        # Reasoning
        if m["reasoning"]:
            r_preview = _truncate(m["reasoning"], 200)
            print(f"  │  💭 reasoning: {r_preview}")

        print(f"  └{'─'*80}")
        print()


def cmd_compression_chain(conn: sqlite3.Connection, args):
    """追踪 session 的压缩链 — 显示 parent/child 关系。"""
    session_id = args.session

    # 向上找到 root
    root = session_id
    seen = {root}
    while True:
        row = conn.execute(
            "SELECT parent_session_id FROM sessions WHERE id = ?", (root,)
        ).fetchone()
        if not row or not row["parent_session_id"] or row["parent_session_id"] in seen:
            break
        root = row["parent_session_id"]
        seen.add(root)

    # 从 root 向下展开所有 children
    print(f"\n{'='*100}")
    print(f"  压缩链 (root → leaf)")
    print(f"{'='*100}\n")

    current = root
    depth = 0
    visited = set()
    while current and current not in visited and depth < 50:
        visited.add(current)
        sess = conn.execute("SELECT * FROM sessions WHERE id = ?", (current,)).fetchone()
        if not sess:
            break

        msg_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE session_id = ?", (current,)
        ).fetchone()["cnt"]

        tool_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE session_id = ? AND role = 'tool'",
            (current,),
        ).fetchone()["cnt"]

        # 查找压缩摘要
        has_compression = conn.execute(
            "SELECT 1 FROM messages WHERE session_id = ? AND content LIKE '%CONTEXT COMPACTION%' LIMIT 1",
            (current,),
        ).fetchone()

        marker = "📍" if current == session_id else "  "
        indent = "  " * depth
        comp_flag = " 🗜️" if has_compression else ""

        print(f"  {marker}{indent}{'└─' if depth > 0 else ''}📋 {current}")
        print(f"    {indent}   时间: {_ts_to_local(sess['started_at'])}  "
              f"结束: {sess['end_reason'] or 'active'}  "
              f"消息: {msg_count}  工具: {tool_count}{comp_flag}")

        # 找 child (压缩 continuation)
        child = conn.execute(
            "SELECT id FROM sessions WHERE parent_session_id = ? ORDER BY started_at ASC LIMIT 1",
            (current,),
        ).fetchone()
        current = child["id"] if child else None
        depth += 1

    print()


def cmd_search(conn: sqlite3.Connection, args):
    """在消息内容中搜索关键词。"""
    keyword = args.search
    where = ["m.content LIKE ?"]
    params = [f"%{keyword}%"]

    if args.date:
        start, end = _date_to_ts_range(args.date)
        where.append("m.timestamp BETWEEN ? AND ?")
        params.extend([start, end])

    if args.user:
        where.append("s.user_id = ?")
        params.append(args.user)

    if args.source:
        where.append("s.source = ?")
        params.append(args.source)

    where_sql = " AND ".join(where)
    query = f"""
        SELECT m.*, s.source, s.user_id, s.model, s.title AS session_title
        FROM messages m
        JOIN sessions s ON s.id = m.session_id
        WHERE {where_sql}
        ORDER BY m.timestamp DESC
        LIMIT ?
    """
    params.append(args.limit or 50)

    rows = conn.execute(query, params).fetchall()
    if not rows:
        print(f"未找到包含 '{keyword}' 的消息。")
        return

    print(f"\n{'='*100}")
    print(f"  搜索 '{keyword}' — 找到 {len(rows)} 条匹配")
    print(f"{'='*100}\n")

    for r in rows:
        role_icon = {"system": "🔧", "user": "👤", "assistant": "🤖", "tool": "⚙️"}.get(r["role"], "❓")
        content = r["content"] or ""

        # 高亮关键词周围的上下文
        idx = content.lower().find(keyword.lower())
        if idx >= 0:
            start = max(0, idx - 60)
            end = min(len(content), idx + len(keyword) + 60)
            snippet = ("…" if start > 0 else "") + content[start:end] + ("…" if end < len(content) else "")
            snippet = snippet.replace("\n", " ")
        else:
            snippet = _truncate(content, 150)

        print(f"  {role_icon} {r['role'].upper()} @ {_ts_to_local(r['timestamp'])}  "
              f"session={r['session_id'][:25]}  source={r['source']}")
        if r["tool_name"]:
            print(f"     tool: {r['tool_name']}")
        print(f"     {snippet}")
        print()


def cmd_export(conn: sqlite3.Connection, args):
    """导出 session 完整对话为 JSON。"""
    session_id = args.session
    sess = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not sess:
        print(f"未找到 session: {session_id}")
        return

    msgs = conn.execute(
        "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp, id",
        (session_id,),
    ).fetchall()

    data = {
        "session": {k: sess[k] for k in sess.keys()},
        "messages": [],
    }
    for m in msgs:
        msg = {k: m[k] for k in m.keys()}
        if msg.get("tool_calls") and isinstance(msg["tool_calls"], str):
            try:
                msg["tool_calls"] = json.loads(msg["tool_calls"])
            except Exception:
                pass
        data["messages"].append(msg)

    out_path = args.export
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"✅ 已导出到 {out_path} ({len(msgs)} 条消息)")


def cmd_stats(conn: sqlite3.Connection, args):
    """显示某天/某用户的统计摘要。"""
    where = []
    params = []
    if args.date:
        start, end = _date_to_ts_range(args.date)
        where.append("s.started_at BETWEEN ? AND ?")
        params.extend([start, end])
    if args.user:
        where.append("s.user_id = ?")
        params.append(args.user)
    if args.source:
        where.append("s.source = ?")
        params.append(args.source)
    if getattr(args, 'model', None):
        where.append("s.model LIKE ?")
        params.append(f"%{args.model}%")
    if getattr(args, 'title', None):
        where.append("s.title LIKE ?")
        params.append(f"%{args.title}%")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    row = conn.execute(f"""
        SELECT
            COUNT(*) as session_count,
            SUM(s.message_count) as total_messages,
            SUM(s.tool_call_count) as total_tool_calls,
            SUM(s.api_call_count) as total_api_calls,
            SUM(s.input_tokens) as total_input_tokens,
            SUM(s.output_tokens) as total_output_tokens,
            SUM(s.estimated_cost_usd) as total_cost,
            COUNT(CASE WHEN s.end_reason = 'compression' THEN 1 END) as compression_count
        FROM sessions s {where_sql}
    """, params).fetchone()

    # 按模型分组
    models = conn.execute(f"""
        SELECT s.model, COUNT(*) as cnt, SUM(s.estimated_cost_usd) as cost
        FROM sessions s {where_sql}
        GROUP BY s.model ORDER BY cnt DESC
    """, params).fetchall()

    # 按来源分组
    sources = conn.execute(f"""
        SELECT s.source, COUNT(*) as cnt
        FROM sessions s {where_sql}
        GROUP BY s.source ORDER BY cnt DESC
    """, params).fetchall()

    title_parts = []
    if args.date:
        title_parts.append(args.date)
    if args.user:
        title_parts.append(f"user={args.user}")
    if args.source:
        title_parts.append(f"source={args.source}")
    title = " / ".join(title_parts) if title_parts else "全部"

    print(f"\n{'='*80}")
    print(f"  统计摘要: {title}")
    print(f"{'='*80}\n")
    print(f"  Session 总数:     {row['session_count']}")
    print(f"  消息总数:         {row['total_messages'] or 0}")
    print(f"  工具调用总数:     {row['total_tool_calls'] or 0}")
    print(f"  API 调用总数:     {row['total_api_calls'] or 0}")
    print(f"  Input Token:      {row['total_input_tokens'] or 0:,}")
    print(f"  Output Token:     {row['total_output_tokens'] or 0:,}")
    print(f"  总费用:           ${row['total_cost'] or 0:.4f}")
    print(f"  压缩次数:         {row['compression_count']}")

    if models:
        print(f"\n  按模型:")
        for m in models:
            cost = f"${m['cost']:.4f}" if m['cost'] else "-"
            print(f"    {m['model'] or '(unknown)':30s}  {m['cnt']:>4} sessions  {cost}")

    if sources:
        print(f"\n  按来源:")
        for s in sources:
            print(f"    {s['source']:20s}  {s['cnt']:>4} sessions")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Hermes Agent session 诊断工具 — 筛选和分析 state.db 中的 session/消息记录",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            示例:
              %(prog)s --date 2026-06-14                              列出当天所有 session
              %(prog)s --source telegram --date 2026-06-14            按平台+日期筛选
              %(prog)s --user 123456 --date 2026-06-14                按用户+日期筛选
              %(prog)s --list-users                                   列出所有 user_id
              %(prog)s --tables                                       列出所有 SQLite 表和行数
              %(prog)s --schema                                       显示所有表结构
              %(prog)s --dump-table sessions --table-limit 5          抽样查看某张表
              %(prog)s --dump-all-tables --table-limit 3              每张表抽样 3 行
              %(prog)s --model claude --date 2026-06-14               按模型筛选
              %(prog)s --title "media_data"                            按标题筛选
              %(prog)s --session 20260614_103025_abc123                查看完整消息
              %(prog)s --session 20260614_103025_abc123 --tools-only   只看工具调用
              %(prog)s --session 20260614_103025_abc123 --skill-trace  跟踪技能调用
              %(prog)s --session 20260614_103025_abc123 --compression-chain  压缩链
              %(prog)s --search "media_data" --date 2026-06-14        搜索关键词
              %(prog)s --stats --date 2026-06-14                      统计摘要
              %(prog)s --session XXX --export out.json                导出 JSON
        """),
    )

    # 路径
    parser.add_argument("--db", help="state.db 路径 (默认 ~/.hermes/state.db)")
    parser.add_argument("--hermes-home", help="HERMES_HOME 目录 (用于 profile 场景)")

    # 筛选条件
    parser.add_argument("--date", "-d", help="日期筛选 (YYYY-MM-DD)")
    parser.add_argument("--user", "-u", help="按 user_id 筛选")
    parser.add_argument("--source", "-s", help="按来源平台筛选 (cli/telegram/discord/...)")
    parser.add_argument("--session", help="查看指定 session 的详细消息 (支持前缀匹配)")
    parser.add_argument("--search", help="在消息内容中搜索关键词")
    parser.add_argument("--limit", "-n", type=int, default=50, help="最大返回条数 (默认 50)")

    # 额外筛选
    parser.add_argument("--model", "-m", help="按模型名筛选 (模糊匹配)")
    parser.add_argument("--title", help="按 session 标题筛选 (模糊匹配)")

    # 显示模式
    parser.add_argument("--tables", action="store_true", help="列出 state.db 中所有表/视图及行数")
    parser.add_argument("--schema", action="store_true", help="显示所有表结构；配合 --dump-table 可只看某张表")
    parser.add_argument("--dump-table", help="抽样显示指定表/视图内容")
    parser.add_argument("--dump-all-tables", action="store_true", help="抽样显示所有表/视图内容")
    parser.add_argument("--table-limit", type=int, default=20, help="dump 表时每张表最多显示多少行 (默认 20)")
    parser.add_argument("--tools-only", action="store_true", help="只显示工具调用相关消息")
    parser.add_argument("--skill-trace", action="store_true", help="只显示技能相关消息")
    parser.add_argument("--compression-chain", action="store_true", help="追踪 session 压缩链")
    parser.add_argument("--stats", action="store_true", help="显示统计摘要")
    parser.add_argument("--list-users", action="store_true", help="列出数据库中所有不同的 user_id（仅 gateway 平台会写入）")
    parser.add_argument("--export", help="导出 session 对话为 JSON 文件")

    args = parser.parse_args()

    db_path = _resolve_db_path(args)
    print(f"📂 数据库: {db_path}")
    conn = _connect(db_path)

    try:
        if args.tables:
            cmd_tables(conn, args)
        elif args.schema:
            cmd_schema(conn, args)
        elif args.dump_table or args.dump_all_tables:
            cmd_dump_table(conn, args)
        elif args.list_users:
            cmd_list_users(conn, args)
        elif args.session and args.compression_chain:
            cmd_compression_chain(conn, args)
        elif args.session and args.export:
            cmd_export(conn, args)
        elif args.session:
            cmd_show_session(conn, args)
        elif args.search:
            cmd_search(conn, args)
        elif args.stats:
            cmd_stats(conn, args)
        else:
            cmd_list_sessions(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
