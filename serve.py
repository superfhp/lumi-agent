#!/usr/bin/env python3
"""
启动本地静态 HTTP 服务器，首页展示目录下所有 HTML 文件并可点击打开。
支持文件浏览、预览（CSV/HTML/JSON/TXT）、下载等功能。

用法: python3 serve.py [目录路径]
示例: python3 serve.py /mnt/workspace/achieveFinReport

修复记录:
  - 使用 ThreadingHTTPServer 替代 HTTPServer，解决单线程阻塞导致 Empty reply
  - 大文件使用流式传输，避免 OOM 崩溃
  - 添加全局异常保护，防止未捕获异常导致连接静默关闭
  - 移除 webbrowser.open()，避免无头服务器启动阻塞
  - 增强目录浏览 UI，支持文件大小/修改时间展示和文件预览
"""

import http.server
import socketserver
import sys
import traceback
import urllib.parse
import mimetypes
import json
import csv
import io
import os
import time
import signal
import zipfile
from pathlib import Path
from datetime import datetime
from threading import Thread

PORT = int(os.environ.get("SERVE_PORT", 9200))
BASE_DIR = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path("/mnt/workspace/achieveFinReport").resolve()
REPORT_PREFIX = "/lumifinreport"
DOWNLOAD_PREFIX = "/download"
PREVIEW_PREFIX = "/preview"
API_PREFIX = "/api"

# 允许下载的目录白名单，文件必须在其中之一下才可访问
DOWNLOAD_ROOTS = [
    Path("/mnt/workspace"),
    Path("/hpfu/media_data"),
    Path("/hpfu/medical_data"),
]

# 流式传输分块大小 (64KB)
CHUNK_SIZE = 65536

# 可预览的文件扩展名
PREVIEWABLE_EXTENSIONS = {
    ".csv", ".html", ".htm", ".json", ".jsonl",
    ".txt", ".md", ".log", ".yaml", ".yml",
    ".xml", ".py", ".sh", ".conf", ".ini",
    ".toml", ".cfg",
    ".zip", ".tar", ".gz", ".tgz",  # 压缩包：展示内容列表
}

# 预览文件大小上限
PREVIEW_MAX_SIZE = 10 * 1024 * 1024       # 文本文件 10MB
ARCHIVE_PREVIEW_MAX_SIZE = 100 * 1024 * 1024 * 1024  # 压缩包 100GB（只读中央目录，不解压内容，几乎无开销）


def human_size(size_bytes: int) -> str:
    """将字节数转换为人类可读格式"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_time(timestamp: float) -> str:
    """格式化时间戳"""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def list_html_files():
    if not BASE_DIR.exists():
        return []
    return sorted(BASE_DIR.glob("*.html"))


def get_file_icon(path: Path) -> str:
    """根据文件类型返回图标"""
    if path.is_dir():
        return "📁"
    ext = path.suffix.lower()
    icons = {
        ".html": "🌐", ".htm": "🌐",
        ".csv": "📊", ".json": "📋", ".jsonl": "📋",
        ".pdf": "📕", ".doc": "📝", ".docx": "📝",
        ".xls": "📗", ".xlsx": "📗",
        ".png": "🖼️", ".jpg": "🖼️", ".jpeg": "🖼️", ".gif": "🖼️", ".svg": "🖼️",
        ".mp4": "🎬", ".avi": "🎬", ".mkv": "🎬",
        ".mp3": "🎵", ".wav": "🎵",
        ".zip": "📦", ".tar": "📦", ".gz": "📦", ".rar": "📦",
        ".py": "🐍", ".js": "📜", ".ts": "📜",
        ".sh": "⚙️", ".bash": "⚙️",
        ".md": "📖", ".txt": "📄", ".log": "📄",
        ".yaml": "⚙️", ".yml": "⚙️", ".toml": "⚙️",
    }
    return icons.get(ext, "📄")


def build_index_page():
    files = list_html_files()
    items = "".join(
        f'<li><a href="{REPORT_PREFIX}/{f.name}" target="_blank">'
        f'<span class="icon">🌐</span><span class="name">{f.name}</span></a></li>'
        for f in files
    )
    if not items:
        items = '<li class="empty">（未找到任何 HTML 报告文件）</li>'

    # 构建白名单根目录入口
    root_links = ""
    for root in DOWNLOAD_ROOTS:
        if root.exists():
            try:
                child_count = sum(1 for _ in root.iterdir())
                desc = f"{child_count} 项"
            except (PermissionError, OSError):
                desc = ""
            root_links += (
                f'<li><a href="{DOWNLOAD_PREFIX}/{root.name}/">'
                f'<span class="icon">📁</span>'
                f'<span class="name">{root}</span>'
                f'<span class="badge">{desc}</span></a></li>'
            )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lumi File Server</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: linear-gradient(135deg, #f8faf9 0%, #eef5f2 100%);
         color: #1a2e2a; min-height: 100vh;
         display: flex; flex-direction: column; align-items: center; padding: 40px 20px; }}
  .container {{ width: 100%; max-width: 860px; }}
  .header {{ text-align: center; margin-bottom: 36px; }}
  .header h1 {{ font-size: 28px; font-weight: 700;
               background: linear-gradient(135deg, #0d7a5f, #14b8a6);
               -webkit-background-clip: text; -webkit-text-fill-color: transparent;
               margin-bottom: 8px; }}
  .header p {{ color: #5f7a72; font-size: 14px; }}
  .nav {{ display: flex; gap: 10px; margin-bottom: 30px; flex-wrap: wrap; justify-content: center; }}
  .nav a {{ padding: 8px 16px; border-radius: 20px; background: #fff;
           border: 1px solid #d1e5df; color: #0d7a5f; text-decoration: none;
           font-size: 13px; transition: all 0.2s; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
  .nav a:hover {{ background: #e6f7f2; border-color: #14b8a6; transform: translateY(-1px);
                 box-shadow: 0 3px 8px rgba(13,122,95,0.1); }}
  .section {{ margin-bottom: 28px; }}
  .section h2 {{ font-size: 15px; color: #3d6b5e; margin-bottom: 12px;
                padding-left: 12px; border-left: 3px solid #14b8a6; font-weight: 600; }}
  ul {{ list-style: none; display: flex; flex-direction: column; gap: 6px; }}
  li a {{ display: flex; align-items: center; gap: 12px; padding: 12px 16px;
         border-radius: 10px; background: #fff; border: 1px solid #e2ede8;
         color: #1a2e2a; text-decoration: none; font-size: 14px; transition: all 0.2s;
         box-shadow: 0 1px 2px rgba(0,0,0,0.03); }}
  li a:hover {{ background: #f0faf6; border-color: #14b8a6; transform: translateX(3px);
              box-shadow: 0 2px 6px rgba(13,122,95,0.08); }}
  li a .icon {{ font-size: 18px; flex-shrink: 0; }}
  li a .name {{ flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #0d7a5f; }}
  li a .badge {{ font-size: 11px; color: #5f7a72; background: #e6f7f2; padding: 2px 8px;
               border-radius: 10px; flex-shrink: 0; }}
  .empty {{ color: #8aa89e; padding: 20px; text-align: center; font-style: italic; }}
  .status {{ margin-top: 32px; padding: 14px 18px; border-radius: 10px; background: #fff;
            border: 1px solid #e2ede8; font-size: 12px; color: #5f7a72;
            box-shadow: 0 1px 3px rgba(0,0,0,0.03); }}
  .status code {{ color: #0d7a5f; font-weight: 500; background: #e6f7f2; padding: 2px 6px; border-radius: 4px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>📊 Lumi Financial Evaluation</h1>
    <p>文件服务 · 报告浏览 · 数据下载</p>
  </div>
  <nav class="nav">
    <a href="/">🏠 首页</a>
    <a href="{DOWNLOAD_PREFIX}/">📂 文件浏览</a>
    <a href="/health">💚 健康检查</a>
  </nav>
  <div class="section">
    <h2>📂 数据目录</h2>
    <ul>{root_links or '<li class="empty">（无可用目录）</li>'}</ul>
  </div>
  <div class="section">
    <h2>📄 评测报告</h2>
    <ul>{items}</ul>
  </div>
  <div class="status">
    <p>📡 服务端口: <code>{PORT}</code> &nbsp;|&nbsp; 报告路径: <code>{REPORT_PREFIX}/</code> &nbsp;|&nbsp; 下载路径: <code>{DOWNLOAD_PREFIX}/</code></p>
  </div>
</div>
</body>
</html>"""


def _get_dir_size(dir_path: Path, max_depth: int = 2, max_items: int = 200) -> int:
    """递归计算目录大小（限制深度和扫描文件数避免卡顿）"""
    if max_depth <= 0:
        return 0
    total = 0
    count = 0
    try:
        for item in dir_path.iterdir():
            count += 1
            if count > max_items:
                break
            try:
                if item.is_file() and not item.is_symlink():
                    total += item.stat().st_size
                elif item.is_dir() and not item.is_symlink():
                    total += _get_dir_size(item, max_depth - 1, max_items)
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass
    return total


def build_directory_page(dir_path: Path, rel_path: str) -> str:
    """构建增强版目录浏览页面 - 明亮风格，支持文件夹大小展示"""
    try:
        items = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        items = []

    # 如果是根路径且目录为空或不存在，展示所有白名单根目录
    is_root_listing = (not rel_path or rel_path.strip("/") == "")

    # 面包屑导航
    parts = rel_path.strip("/").split("/") if rel_path.strip("/") else []
    breadcrumbs = f'<a href="{DOWNLOAD_PREFIX}/">🏠 根目录</a>'
    accumulated = ""
    for part in parts:
        accumulated += f"/{part}"
        breadcrumbs += f' <span class="sep">›</span> <a href="{DOWNLOAD_PREFIX}{accumulated}/">{part}</a>'

    # 文件列表
    rows = ""
    total_size = 0
    file_count = 0
    dir_count = 0

    # 如果是根目录，展示所有白名单根目录入口
    if is_root_listing:
        for root in DOWNLOAD_ROOTS:
            if root.exists():
                dir_count += 1
                # 根目录不计算递归大小（太慢），只显示子项数
                try:
                    child_count = sum(1 for _ in root.iterdir())
                    size_str = f"{child_count} 项"
                except (PermissionError, OSError):
                    size_str = "—"
                try:
                    mtime_str = format_time(root.stat().st_mtime)
                except OSError:
                    mtime_str = "—"
                rows += f"""<tr>
  <td class="col-icon">📁</td>
  <td class="col-name"><a href="{DOWNLOAD_PREFIX}/{root.name}/">{root.name}/ <span class="path-hint">({root})</span></a></td>
  <td class="col-size">{size_str}</td>
  <td class="col-time">{mtime_str}</td>
  <td class="col-action"></td>
</tr>"""
    else:
        for item in items:
            try:
                stat = item.stat()
            except (PermissionError, OSError):
                continue

            icon = get_file_icon(item)
            name = item.name
            is_dir = item.is_dir()
            item_rel = f"{rel_path.rstrip('/')}/{name}" if rel_path else name

            if is_dir:
                dir_count += 1
                # 只浅层计算大小（深度1，最多100个文件），避免阻塞
                dir_size = _get_dir_size(item, max_depth=1, max_items=100)
                size_str = f"~{human_size(dir_size)}" if dir_size > 0 else "—"
                total_size += dir_size
                href = f"{DOWNLOAD_PREFIX}/{item_rel}/"
                preview_btn = ""
            else:
                file_count += 1
                size_str = human_size(stat.st_size)
                total_size += stat.st_size
                href = f"{DOWNLOAD_PREFIX}/{item_rel}"
                # 可预览文件添加预览按钮
                ext_lower = item.suffix.lower()
                is_archive = ext_lower in (".zip", ".tar", ".gz", ".tgz")
                # 压缩包几乎不限大小（只读目录），文本文件限 10MB
                if is_archive:
                    can_preview = ext_lower in PREVIEWABLE_EXTENSIONS
                else:
                    can_preview = ext_lower in PREVIEWABLE_EXTENSIONS and stat.st_size <= PREVIEW_MAX_SIZE
                if can_preview:
                    btn_label = "📦" if is_archive else "👁️"
                    btn_title = "查看内容" if is_archive else "预览"
                    preview_btn = f'<a class="btn-preview" href="{PREVIEW_PREFIX}/{item_rel}" target="_blank" title="{btn_title}">{btn_label}</a>'
                else:
                    preview_btn = ""

            mtime_str = format_time(stat.st_mtime)

            rows += f"""<tr>
  <td class="col-icon">{icon}</td>
  <td class="col-name"><a href="{href}">{name}{"/" if is_dir else ""}</a></td>
  <td class="col-size">{size_str}</td>
  <td class="col-time">{mtime_str}</td>
  <td class="col-action">{preview_btn}</td>
</tr>"""

    if not rows:
        rows = '<tr><td colspan="5" class="empty">（空目录）</td></tr>'

    summary = f"{dir_count} 个文件夹, {file_count} 个文件"
    if total_size > 0:
        summary += f", 总计 {human_size(total_size)}"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>📂 /{rel_path or "."} — Lumi File Browser</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: linear-gradient(180deg, #f4faf7 0%, #eaf3ef 100%);
         color: #1a2e2a; min-height: 100vh; padding: 28px 20px; }}
  .container {{ max-width: 1100px; margin: 0 auto; }}
  .breadcrumb {{ display: flex; align-items: center; flex-wrap: wrap; gap: 4px;
                margin-bottom: 18px; font-size: 14px; }}
  .breadcrumb a {{ color: #0d7a5f; text-decoration: none; padding: 4px 8px;
                  border-radius: 6px; transition: background 0.2s; font-weight: 500; }}
  .breadcrumb a:hover {{ background: #d5f0e8; }}
  .breadcrumb .sep {{ color: #9db8ae; font-weight: 300; }}
  .toolbar {{ display: flex; gap: 12px; margin-bottom: 16px; align-items: center; flex-wrap: wrap; }}
  .search-box {{ flex: 1; min-width: 200px; padding: 10px 16px; border-radius: 10px;
                background: #fff; border: 1px solid #d1e5df; color: #1a2e2a;
                font-size: 14px; outline: none; transition: all 0.2s;
                box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
  .search-box:focus {{ border-color: #14b8a6; box-shadow: 0 0 0 3px rgba(20,184,166,0.1); }}
  .search-box::placeholder {{ color: #9db8ae; }}
  .summary {{ font-size: 12px; color: #5f7a72; padding: 6px 0 12px; }}
  table {{ width: 100%; border-collapse: separate; border-spacing: 0;
          background: #fff; border-radius: 12px; overflow: hidden;
          border: 1px solid #d1e5df; box-shadow: 0 2px 8px rgba(0,0,0,0.04); }}
  thead {{ background: linear-gradient(180deg, #f0faf6, #e6f7f2); }}
  th {{ padding: 12px 16px; text-align: left; font-size: 12px; font-weight: 600;
       color: #3d6b5e; text-transform: uppercase; letter-spacing: 0.04em;
       cursor: pointer; user-select: none; white-space: nowrap;
       border-bottom: 1px solid #d1e5df; }}
  th:hover {{ color: #0d7a5f; }}
  th .sort-icon {{ margin-left: 4px; opacity: 0.4; }}
  td {{ padding: 10px 16px; border-top: 1px solid #eef5f2; font-size: 14px;
       vertical-align: middle; }}
  tr:hover {{ background: #f0faf6; }}
  .col-icon {{ width: 40px; text-align: center; font-size: 18px; }}
  .col-name {{ min-width: 200px; }}
  .col-name a {{ color: #0d7a5f; text-decoration: none; word-break: break-all; font-weight: 500; }}
  .col-name a:hover {{ color: #059669; text-decoration: underline; }}
  .col-name .path-hint {{ color: #9db8ae; font-size: 12px; font-weight: 400; }}
  .col-size {{ width: 110px; color: #5f7a72; white-space: nowrap; text-align: right; font-size: 13px; }}
  .col-time {{ width: 170px; color: #7a9e92; white-space: nowrap;
              font-family: 'SF Mono', 'Cascadia Mono', monospace; font-size: 12px; }}
  .col-action {{ width: 50px; text-align: center; }}
  .btn-preview {{ display: inline-flex; align-items: center; justify-content: center;
                 width: 30px; height: 30px; border-radius: 8px; background: #e6f7f2;
                 text-decoration: none; font-size: 14px; transition: all 0.2s;
                 border: 1px solid #d1e5df; }}
  .btn-preview:hover {{ background: #ccf0e3; transform: scale(1.1); border-color: #14b8a6; }}
  .empty {{ text-align: center; color: #8aa89e; padding: 40px; font-style: italic; }}
  @media (max-width: 768px) {{
    .col-time {{ display: none; }}
    td, th {{ padding: 8px 10px; }}
  }}
</style>
</head>
<body>
<div class="container">
  <div class="breadcrumb">{breadcrumbs}</div>
  <div class="toolbar">
    <input type="text" class="search-box" id="searchInput"
           placeholder="🔍 搜索文件名..." oninput="filterFiles()">
  </div>
  <p class="summary">{summary}</p>
  <table id="fileTable">
    <thead>
      <tr>
        <th class="col-icon"></th>
        <th class="col-name" onclick="sortTable(1)">文件名 <span class="sort-icon">⇅</span></th>
        <th class="col-size" onclick="sortTable(2)">大小 <span class="sort-icon">⇅</span></th>
        <th class="col-time" onclick="sortTable(3)">修改时间 <span class="sort-icon">⇅</span></th>
        <th class="col-action">操作</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
</div>
<script>
function filterFiles() {{
  const q = document.getElementById('searchInput').value.toLowerCase();
  const rows = document.querySelectorAll('#fileTable tbody tr');
  rows.forEach(row => {{
    const name = row.querySelector('.col-name')?.textContent.toLowerCase() || '';
    row.style.display = name.includes(q) ? '' : 'none';
  }});
}}
let sortDir = {{}};
function sortTable(colIdx) {{
  const table = document.getElementById('fileTable');
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  sortDir[colIdx] = !sortDir[colIdx];
  rows.sort((a, b) => {{
    let aVal = a.cells[colIdx]?.textContent.trim() || '';
    let bVal = b.cells[colIdx]?.textContent.trim() || '';
    if (colIdx === 2) {{
      const parseSize = s => {{
        const m = s.match(/([\d.]+)\s*(B|KB|MB|GB)/);
        if (!m) return -1;
        const units = {{'B':1,'KB':1024,'MB':1048576,'GB':1073741824}};
        return parseFloat(m[1]) * (units[m[2]]||1);
      }};
      aVal = parseSize(aVal); bVal = parseSize(bVal);
    }}
    let cmp = typeof aVal === 'number' ? aVal - bVal : aVal.localeCompare(bVal);
    return sortDir[colIdx] ? cmp : -cmp;
  }});
  rows.forEach(r => tbody.appendChild(r));
}}
</script>
</body>
</html>"""


def build_preview_page(file_path: Path, rel_path: str) -> str:
    """构建文件预览页面"""
    ext = file_path.suffix.lower()
    filename = file_path.name
    size_str = human_size(file_path.stat().st_size)

    try:
        raw_content = file_path.read_text(encoding="utf-8", errors="replace") if ext not in (".zip", ".tar", ".gz", ".tgz") else ""
    except Exception as e:
        raw_content = f"无法读取文件: {e}"

    # 根据文件类型选择预览方式
    if ext == ".csv":
        preview_html = _render_csv_preview(raw_content)
    elif ext in (".zip", ".tar", ".gz", ".tgz"):
        preview_html = _render_archive_preview(file_path, ext)
    elif ext in (".html", ".htm"):
        # HTML 文件使用 iframe 预览
        escaped = raw_content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        preview_html = f"""
        <div class="preview-tabs">
          <button class="tab active" onclick="showTab('rendered')">渲染视图</button>
          <button class="tab" onclick="showTab('source')">源代码</button>
        </div>
        <div id="rendered" class="tab-content active">
          <iframe srcdoc="{raw_content.replace('"', '&quot;')}" class="html-frame"></iframe>
        </div>
        <div id="source" class="tab-content"><pre><code>{escaped}</code></pre></div>"""
    elif ext in (".json", ".jsonl"):
        preview_html = _render_json_preview(raw_content, ext)
    else:
        escaped = raw_content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        preview_html = f'<pre class="code-block"><code>{escaped}</code></pre>'

    download_href = f"{DOWNLOAD_PREFIX}/{rel_path}"
    # 计算父目录链接（用于返回按钮）
    parent_rel = "/".join(rel_path.strip("/").split("/")[:-1])
    back_href = f"{DOWNLOAD_PREFIX}/{parent_rel}/" if parent_rel else f"{DOWNLOAD_PREFIX}/"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>👁️ 预览 {filename}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: linear-gradient(180deg, #f4faf7 0%, #eaf3ef 100%);
         color: #1a2e2a; min-height: 100vh; padding: 20px; }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  .file-header {{ display: flex; align-items: center; justify-content: space-between;
                 padding: 16px 20px; background: #fff; border-radius: 12px;
                 border: 1px solid #d1e5df; margin-bottom: 20px; flex-wrap: wrap; gap: 12px;
                 box-shadow: 0 2px 6px rgba(0,0,0,0.04); }}
  .file-info {{ display: flex; align-items: center; gap: 12px; }}
  .file-info .icon {{ font-size: 24px; }}
  .file-info .name {{ font-size: 16px; font-weight: 600; color: #1a2e2a; }}
  .file-info .meta {{ font-size: 12px; color: #5f7a72; margin-top: 2px; }}
  .btn {{ padding: 8px 16px; border-radius: 8px; text-decoration: none; font-size: 13px;
         font-weight: 500; transition: all 0.2s; display: inline-flex; align-items: center; gap: 6px; }}
  .btn-download {{ background: #0d7a5f; color: #fff; border: none; }}
  .btn-download:hover {{ background: #059669; transform: translateY(-1px); box-shadow: 0 3px 8px rgba(13,122,95,0.2); }}
  .btn-back {{ background: #f0faf6; color: #3d6b5e; border: 1px solid #d1e5df; }}
  .btn-back:hover {{ background: #e6f7f2; color: #0d7a5f; }}
  .preview-tabs {{ display: flex; gap: 4px; margin-bottom: 12px; }}
  .tab {{ padding: 8px 16px; border-radius: 8px 8px 0 0; background: #e6f7f2;
         border: 1px solid #d1e5df; border-bottom: none; color: #5f7a72;
         cursor: pointer; font-size: 13px; transition: all 0.2s; }}
  .tab.active {{ background: #fff; color: #0d7a5f; border-color: #14b8a6; font-weight: 600; }}
  .tab-content {{ display: none; }}
  .tab-content.active {{ display: block; }}
  pre {{ background: #fff; border: 1px solid #d1e5df; border-radius: 12px;
        padding: 20px; overflow-x: auto; font-size: 13px; line-height: 1.6;
        font-family: 'SF Mono', 'Cascadia Mono', 'JetBrains Mono', monospace;
        max-height: 80vh; overflow-y: auto; color: #1a2e2a;
        box-shadow: 0 2px 6px rgba(0,0,0,0.04); }}
  .code-block {{ white-space: pre-wrap; word-break: break-all; }}
  .csv-table {{ width: 100%; border-collapse: collapse; background: #fff;
               border-radius: 12px; overflow: hidden; border: 1px solid #d1e5df;
               font-size: 13px; display: block; overflow-x: auto; }}
  .csv-table thead {{ background: linear-gradient(180deg, #f0faf6, #e6f7f2); }}
  .csv-table th {{ padding: 10px 14px; text-align: left; font-weight: 600;
                  color: #0d7a5f; white-space: nowrap; border-bottom: 2px solid #d1e5df; }}
  .csv-table td {{ padding: 8px 14px; border-top: 1px solid #eef5f2; white-space: nowrap; }}
  .csv-table tr:hover {{ background: #f0faf6; }}
  .csv-wrapper {{ max-height: 75vh; overflow: auto; border-radius: 12px;
                 border: 1px solid #d1e5df; box-shadow: 0 2px 6px rgba(0,0,0,0.04); }}
  .json-block {{ background: #fff; border: 1px solid #d1e5df; border-radius: 12px;
                padding: 20px; overflow: auto; max-height: 80vh; font-size: 13px;
                font-family: 'SF Mono', 'Cascadia Mono', monospace; line-height: 1.5;
                box-shadow: 0 2px 6px rgba(0,0,0,0.04); }}
  .html-frame {{ width: 100%; height: 70vh; border: 1px solid #d1e5df;
                border-radius: 12px; background: #fff; }}
  .truncated {{ padding: 12px 16px; background: #fef9e7; border: 1px solid #fde68a;
               border-radius: 8px; color: #92400e; font-size: 12px; margin-top: 10px; text-align: center; }}
</style>
</head>
<body>
<div class="container">
  <div class="file-header">
    <div class="file-info">
      <span class="icon">{get_file_icon(file_path)}</span>
      <div>
        <div class="name">{filename}</div>
        <div class="meta">{size_str} · {format_time(file_path.stat().st_mtime)}</div>
      </div>
    </div>
    <div style="display:flex;gap:8px;">
      <a class="btn btn-back" href="{back_href}">← 返回目录</a>
      <a class="btn btn-download" href="{download_href}">📥 下载</a>
    </div>
  </div>
  {preview_html}
</div>
<script>
function showTab(id) {{
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  event.target.classList.add('active');
}}
</script>
</body>
</html>"""


def _render_csv_preview(content: str) -> str:
    """渲染 CSV 文件为 HTML 表格"""
    lines = content.split("\n")
    max_rows = 500  # 最多预览500行
    truncated = len(lines) > max_rows

    reader = csv.reader(io.StringIO("\n".join(lines[:max_rows + 1])))
    rows = list(reader)

    if not rows:
        return '<p class="empty">CSV 文件为空</p>'

    header = rows[0]
    th = "".join(f"<th>{h}</th>" for h in header)
    tbody = ""
    for row in rows[1:max_rows + 1]:
        cells = "".join(
            f"<td>{c.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')}</td>"
            for c in row
        )
        tbody += f"<tr>{cells}</tr>"

    truncated_note = f'<div class="truncated">⚠️ 文件较大，仅展示前 {max_rows} 行（共 {len(lines)} 行）</div>' if truncated else ""

    return f"""<div class="csv-wrapper">
<table class="csv-table"><thead><tr>{th}</tr></thead><tbody>{tbody}</tbody></table>
</div>{truncated_note}"""


def _render_json_preview(content: str, ext: str) -> str:
    """渲染 JSON/JSONL 预览"""
    try:
        if ext == ".jsonl":
            lines = [l for l in content.strip().split("\n") if l.strip()]
            max_lines = 100
            truncated = len(lines) > max_lines
            data = [json.loads(l) for l in lines[:max_lines]]
            formatted = "\n---\n".join(json.dumps(d, ensure_ascii=False, indent=2) for d in data)
            if truncated:
                formatted += f"\n\n... (共 {len(lines)} 条记录，仅展示前 {max_lines} 条)"
        else:
            data = json.loads(content)
            formatted = json.dumps(data, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, ValueError):
        formatted = content

    escaped = (formatted.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return f'<pre class="json-block"><code>{escaped}</code></pre>'


def _render_archive_preview(file_path: Path, ext: str) -> str:
    """渲染压缩包内容列表 — 支持全量扫描、PDF优先、文件类型统计"""
    entries = []
    total_uncompressed = 0
    error_msg = None
    # ZIP 中央目录是纯元数据读取，不解压内容，无需限制条目数
    # tar.gz 需要顺序读取，限制稍多
    MAX_ENTRIES_TAR = 50000

    try:
        if ext == ".zip":
            with zipfile.ZipFile(file_path, 'r') as zf:
                for info in zf.infolist():
                    entries.append({
                        "name": info.filename,
                        "size": info.file_size,
                        "compressed": info.compress_size,
                        "is_dir": info.is_dir(),
                        "date": f"{info.date_time[0]:04d}-{info.date_time[1]:02d}-{info.date_time[2]:02d} {info.date_time[3]:02d}:{info.date_time[4]:02d}",
                    })
                    total_uncompressed += info.file_size
        else:
            # tar/gz — 用 tarfile
            import tarfile
            mode = "r:gz" if ext in (".gz", ".tgz") else "r:"
            try:
                with tarfile.open(file_path, mode) as tf:
                    for i, member in enumerate(tf.getmembers()):
                        if i >= MAX_ENTRIES_TAR:
                            break
                        entries.append({
                            "name": member.name,
                            "size": member.size if member.isfile() else 0,
                            "compressed": 0,
                            "is_dir": member.isdir(),
                            "date": format_time(member.mtime) if member.mtime else "—",
                        })
                        total_uncompressed += member.size if member.isfile() else 0
            except Exception:
                mode = "r:*"
                with tarfile.open(file_path, mode) as tf:
                    for i, member in enumerate(tf.getmembers()):
                        if i >= MAX_ENTRIES_TAR:
                            break
                        entries.append({
                            "name": member.name,
                            "size": member.size if member.isfile() else 0,
                            "compressed": 0,
                            "is_dir": member.isdir(),
                            "date": format_time(member.mtime) if member.mtime else "—",
                        })
                        total_uncompressed += member.size if member.isfile() else 0
    except Exception as e:
        error_msg = f"无法读取压缩包: {e}"

    if error_msg:
        return f'<div class="truncated">⚠️ {error_msg}</div>'

    # 统计
    file_entries = [e for e in entries if not e["is_dir"]]
    dir_entries = [e for e in entries if e["is_dir"]]
    total_entries_count = len(entries)

    # 文件类型统计
    ext_counts = {}
    ext_sizes = {}
    for e in file_entries:
        fext = Path(e["name"]).suffix.lower() if "." in e["name"] else "(无扩展名)"
        ext_counts[fext] = ext_counts.get(fext, 0) + 1
        ext_sizes[fext] = ext_sizes.get(fext, 0) + e["size"]

    # 按数量排序的类型统计
    sorted_exts = sorted(ext_counts.items(), key=lambda x: -x[1])

    # 构建类型统计标签（PDF 始终排第一）
    type_tags = ""
    pdf_in_list = False
    for fext, count in sorted_exts[:20]:
        size_total = ext_sizes.get(fext, 0)
        if fext == ".pdf":
            pdf_in_list = True
            highlight = ' style="background:#fef2f2;color:#b91c1c;border-color:#fca5a5;font-weight:600;"'
        else:
            highlight = ""
        type_tags += f'<span class="type-tag"{highlight}>{fext} <b>{count}</b> ({human_size(size_total)})</span>'

    # 如果 PDF 存在但不在前 20 名，也要展示
    pdf_count = ext_counts.get(".pdf", 0)
    pdf_size = ext_sizes.get(".pdf", 0)
    if pdf_count > 0 and not pdf_in_list:
        type_tags = f'<span class="type-tag" style="background:#fef2f2;color:#b91c1c;border-color:#fca5a5;font-weight:600;">.pdf <b>{pdf_count}</b> ({human_size(pdf_size)})</span>' + type_tags

    # 排序：PDF 文件优先，其次按类型分组、文件名排序
    def sort_key(entry):
        if entry["is_dir"]:
            return (2, "", entry["name"].lower())
        ext_lower = Path(entry["name"]).suffix.lower()
        if ext_lower == ".pdf":
            return (0, ext_lower, entry["name"].lower())
        return (1, ext_lower, entry["name"].lower())

    entries_sorted = sorted(entries, key=sort_key)

    # 展示上限：PDF 全部展示，其他截断到合理数量
    MAX_DISPLAY = 1000
    pdf_entries_sorted = [e for e in entries_sorted if not e["is_dir"] and Path(e["name"]).suffix.lower() == ".pdf"]
    non_pdf_entries = [e for e in entries_sorted if e["is_dir"] or Path(e["name"]).suffix.lower() != ".pdf"]

    # 如果 PDF 超过 MAX_DISPLAY，只展示 PDF
    if len(pdf_entries_sorted) > MAX_DISPLAY:
        display_entries = pdf_entries_sorted[:MAX_DISPLAY]
    else:
        # PDF 全部 + 其他文件填充剩余
        remaining = MAX_DISPLAY - len(pdf_entries_sorted)
        display_entries = pdf_entries_sorted + non_pdf_entries[:remaining]

    # 构建表格
    rows = ""
    for entry in display_entries:
        icon = "📁" if entry["is_dir"] else get_file_icon(Path(entry["name"]))
        name = entry["name"]
        size_str = human_size(entry["size"]) if entry["size"] > 0 else "—"
        date_str = entry["date"]
        # 压缩率
        if entry["compressed"] > 0 and entry["size"] > 0:
            ratio = (1 - entry["compressed"] / entry["size"]) * 100
            ratio_str = f"{ratio:.0f}%"
        else:
            ratio_str = "—"

        # PDF 行高亮
        is_pdf = not entry["is_dir"] and Path(name).suffix.lower() == ".pdf"
        row_class = ' class="pdf-row"' if is_pdf else ""

        rows += f"""<tr{row_class}>
  <td class="col-icon">{icon}</td>
  <td class="col-name">{name}</td>
  <td class="col-size">{size_str}</td>
  <td class="col-ratio">{ratio_str}</td>
  <td class="col-time">{date_str}</td>
</tr>"""

    summary_text = f"{len(file_entries)} 个文件, {len(dir_entries)} 个目录, 解压后 {human_size(total_uncompressed)}"
    truncated_note = ""
    if total_entries_count > MAX_DISPLAY:
        truncated_note = f'<div class="truncated">📋 共 {total_entries_count} 项，当前展示 {len(display_entries)} 项（PDF {pdf_count} 个全部展示，其余按类型排列）。使用搜索框过滤查看更多。</div>'

    # PDF 摘要 — 醒目展示
    pdf_summary = ""
    if pdf_count > 0:
        pdf_summary = f"""
<div class="pdf-summary">
  <span class="pdf-icon">📕</span>
  <span>PDF 文件: <strong>{pdf_count}</strong> 个</span>
  <span class="pdf-size">（共 {human_size(pdf_size)}，平均 {human_size(pdf_size // pdf_count) if pdf_count else '0 B'}/个）</span>
</div>"""
    else:
        pdf_summary = """
<div class="pdf-summary" style="background:linear-gradient(135deg,#f5f5f5,#fafafa);border-color:#d1d5db;color:#6b7280;">
  <span class="pdf-icon">📄</span>
  <span>此压缩包中 <strong>未发现 PDF</strong> 文件</span>
</div>"""

    return f"""
<div class="archive-info">
  <p>📦 <strong>压缩包内容</strong> — {summary_text}</p>
</div>
{pdf_summary}
<div class="type-stats">
  <p class="type-label">📊 文件类型分布（共 {len(sorted_exts)} 种类型）:</p>
  <div class="type-tags">{type_tags}</div>
</div>
<div class="archive-toolbar">
  <input type="text" class="search-box" id="archiveSearch"
         placeholder="🔍 搜索文件名（如 .pdf .json 关键词等）..." oninput="filterArchive()">
  <div class="filter-hint">💡 提示: 输入 <code>.pdf</code> 可快速过滤所有 PDF 文件</div>
</div>
<div class="csv-wrapper">
<table class="csv-table" id="archiveTable">
  <thead><tr>
    <th style="width:40px"></th>
    <th>文件路径</th>
    <th style="width:90px;text-align:right">大小</th>
    <th style="width:70px;text-align:right">压缩率</th>
    <th style="width:140px">日期</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
</div>
{truncated_note}
<script>
function filterArchive() {{
  const q = document.getElementById('archiveSearch').value.toLowerCase();
  const rows = document.querySelectorAll('#archiveTable tbody tr');
  let shown = 0;
  rows.forEach(row => {{
    const name = row.querySelector('.col-name')?.textContent.toLowerCase() || '';
    const match = name.includes(q);
    row.style.display = match ? '' : 'none';
    if (match) shown++;
  }});
  // 更新过滤结果计数
  const hint = document.querySelector('.filter-hint');
  if (q) {{
    hint.textContent = '🔍 找到 ' + shown + ' 项匹配';
  }} else {{
    hint.innerHTML = '💡 提示: 输入 <code>.pdf</code> 可快速过滤所有 PDF 文件';
  }}
}}
</script>
<style>
.archive-info {{ padding: 12px 16px; background: #f0faf6; border: 1px solid #d1e5df;
               border-radius: 10px; margin-bottom: 12px; font-size: 13px; color: #3d6b5e; }}
.pdf-summary {{ display: flex; align-items: center; gap: 10px; padding: 12px 18px;
              background: #fff; border: 1px solid #e5e7eb;
              border-radius: 10px; margin-bottom: 12px; font-size: 14px; color: #374151; }}
.pdf-summary strong {{ font-size: 17px; color: #b91c1c; font-weight: 600; }}
.pdf-summary .pdf-icon {{ font-size: 18px; }}
.pdf-summary .pdf-size {{ font-size: 12px; color: #6b7280; }}
.type-stats {{ margin-bottom: 14px; }}
.type-label {{ font-size: 12px; color: #5f7a72; margin-bottom: 8px; font-weight: 500; }}
.type-tags {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.type-tag {{ padding: 4px 10px; border-radius: 12px; background: #e6f7f2; border: 1px solid #d1e5df;
           font-size: 12px; color: #3d6b5e; white-space: nowrap; }}
.type-tag b {{ color: #0d7a5f; }}
.archive-toolbar {{ margin-bottom: 14px; }}
.archive-toolbar .search-box {{ width: 100%; padding: 10px 16px; border-radius: 10px;
                               background: #fff; border: 1px solid #d1e5df; color: #1a2e2a;
                               font-size: 14px; outline: none; transition: all 0.2s;
                               box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
.archive-toolbar .search-box:focus {{ border-color: #14b8a6; box-shadow: 0 0 0 3px rgba(20,184,166,0.1); }}
.filter-hint {{ margin-top: 6px; font-size: 11px; color: #9db8ae; }}
.filter-hint code {{ background: #e6f7f2; padding: 1px 5px; border-radius: 3px; color: #0d7a5f; }}
.col-ratio {{ width: 70px; color: #5f7a72; text-align: right; font-size: 12px; }}
tr.pdf-row {{ background: #fefce8; }}
tr.pdf-row:hover {{ background: #fef9c3; }}
tr.pdf-row td {{ font-weight: 500; }}
</style>"""


class CustomHandler(http.server.SimpleHTTPRequestHandler):
    """增强版 HTTP 请求处理器，添加异常保护和流式传输"""

    # 抑制默认的连接重置错误日志
    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass
        except Exception as e:
            self.log_error("Unhandled error in handle_one_request: %s", traceback.format_exc())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self):
        try:
            self._route_request("GET")
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass
        except Exception as e:
            self.log_error("Error handling GET %s: %s", self.path, traceback.format_exc())
            try:
                self.send_error(500, f"Internal Server Error: {e}")
            except Exception:
                pass

    def do_HEAD(self):
        try:
            self._route_request("HEAD")
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass
        except Exception as e:
            self.log_error("Error handling HEAD %s: %s", self.path, traceback.format_exc())
            try:
                self.send_error(500, f"Internal Server Error: {e}")
            except Exception:
                pass

    def _route_request(self, method: str):
        """统一路由分发"""
        path = self.path.split("?")[0]
        head_only = (method == "HEAD")

        # 健康检查
        if path == "/health":
            self._serve_health(head_only)
            return

        # 首页
        if path in ("", "/", "/index", "/index.html", REPORT_PREFIX, f"{REPORT_PREFIX}/"):
            self._serve_index(head_only)
            return

        # 报告文件
        if path.startswith(f"{REPORT_PREFIX}/"):
            self._serve_prefixed_static(method)
            return

        # 文件预览
        if path.startswith(f"{PREVIEW_PREFIX}/"):
            self._serve_preview(head_only)
            return

        # API 接口
        if path.startswith(f"{API_PREFIX}/"):
            self._serve_api(head_only)
            return

        # 文件下载/浏览
        if path.startswith(f"{DOWNLOAD_PREFIX}/"):
            self._serve_download(head_only)
            return

        # 默认静态文件
        if method == "HEAD":
            super().do_HEAD()
        else:
            super().do_GET()

    def _serve_health(self, head_only: bool = False):
        """健康检查端点"""
        data = json.dumps({
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "base_dir": str(BASE_DIR),
            "port": PORT,
            "version": "2.4.1",
            "download_roots": [str(r) for r in DOWNLOAD_ROOTS if r.exists()],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def _serve_index(self, head_only: bool = False):
        content = build_index_page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        if not head_only:
            self.wfile.write(content)

    def _serve_prefixed_static(self, method: str):
        original_path = self.path
        try:
            self.path = self.path[len(REPORT_PREFIX):] or "/"
            if method == "HEAD":
                super().do_HEAD()
            else:
                super().do_GET()
        finally:
            self.path = original_path

    def _serve_preview(self, head_only: bool = False):
        """文件预览路由"""
        raw_path = self.path.split("?")[0]
        rel = raw_path[len(PREVIEW_PREFIX):]
        rel = urllib.parse.unquote(rel).lstrip("/")

        file_path = self._resolve_file_path(rel)
        if file_path is None:
            self.send_error(404, f"File not found: {rel}")
            return

        if file_path.is_dir():
            # 目录不能预览，重定向到浏览
            self.send_response(302)
            self.send_header("Location", f"{DOWNLOAD_PREFIX}/{rel}")
            self.end_headers()
            return

        if file_path.suffix.lower() not in PREVIEWABLE_EXTENSIONS:
            self.send_error(415, f"Unsupported preview type: {file_path.suffix}")
            return

        is_archive = file_path.suffix.lower() in (".zip", ".tar", ".gz", ".tgz")
        # 压缩包不限大小（只读中央目录）；文本文件限 10MB
        if not is_archive and file_path.stat().st_size > PREVIEW_MAX_SIZE:
            self.send_error(413, f"File too large for preview: {human_size(file_path.stat().st_size)}")
            return

        content = build_preview_page(file_path, rel).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(content)

    def _serve_api(self, head_only: bool = False):
        """API 端点 - 提供文件信息 JSON"""
        raw_path = self.path.split("?")[0]
        # /api/info/<path>
        if raw_path.startswith(f"{API_PREFIX}/info/"):
            rel = urllib.parse.unquote(raw_path[len(f"{API_PREFIX}/info/"):]).lstrip("/")
            file_path = self._resolve_file_path(rel)
            if file_path is None:
                self.send_error(404)
                return
            try:
                stat = file_path.stat()
                info = {
                    "name": file_path.name,
                    "path": str(file_path),
                    "is_dir": file_path.is_dir(),
                    "size": stat.st_size,
                    "size_human": human_size(stat.st_size),
                    "modified": format_time(stat.st_mtime),
                    "modified_ts": stat.st_mtime,
                }
                if file_path.is_dir():
                    info["children_count"] = len(list(file_path.iterdir()))
            except Exception as e:
                self.send_error(500, str(e))
                return

            data = json.dumps(info, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not head_only:
                self.wfile.write(data)
        else:
            self.send_error(404)

    def _serve_download(self, head_only: bool = False):
        """提供 /download/<path> 文件下载/浏览，使用流式传输"""
        raw_path = self.path.split("?")[0]
        rel = raw_path[len(DOWNLOAD_PREFIX):]
        rel = urllib.parse.unquote(rel).strip("/")

        # 根路径：展示所有白名单根目录
        if not rel:
            content = build_directory_page(Path("/"), "").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head_only:
                self.wfile.write(content)
            return

        file_path = self._resolve_file_path(rel)

        if file_path is None:
            self.send_error(404, f"File not found or not in allowed paths: {rel}")
            return

        if file_path.is_dir():
            # 目录：渲染增强版浏览页面
            content = build_directory_page(file_path, rel).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head_only:
                self.wfile.write(content)
            return

        # 文件：流式下载
        try:
            file_size = file_path.stat().st_size
        except OSError as e:
            self.send_error(500, f"Cannot stat file: {e}")
            return

        filename = file_path.name
        encoded_name = urllib.parse.quote(filename)

        # 判断 Content-Type
        content_type, _ = mimetypes.guess_type(str(file_path))
        if content_type is None:
            content_type = "application/octet-stream"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{encoded_name}")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

        if not head_only:
            try:
                with open(file_path, "rb") as f:
                    while True:
                        chunk = f.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass
            except Exception as e:
                self.log_error("Error streaming file %s: %s", file_path, e)

    def _resolve_file_path(self, rel: str) -> "Path | None":
        """解析相对路径到实际文件路径（白名单验证）
        
        支持多种路径格式：
        - 相对路径匹配: workspace/data → /mnt/workspace/data
        - 根目录名称: medical_data/xxx → /hpfu/medical_data/xxx
        - 完整路径片段: mnt/workspace/data → /mnt/workspace/data
        """
        # 1. 在白名单中查找：用 root/rel 拼接
        for root in DOWNLOAD_ROOTS:
            candidate = (root / rel).resolve()
            try:
                if str(candidate).startswith(str(root)) and candidate.exists():
                    return candidate
            except (OSError, ValueError):
                continue

        # 2. 尝试用根目录的 basename 匹配 (如 medical_data/xxx → /hpfu/medical_data/xxx)
        first_part = rel.split("/")[0] if "/" in rel else rel
        for root in DOWNLOAD_ROOTS:
            if root.name == first_part:
                # rel 以根目录名称开头，去掉首段后拼接
                sub_rel = "/".join(rel.split("/")[1:]) if "/" in rel else ""
                if sub_rel:
                    candidate = (root / sub_rel).resolve()
                else:
                    candidate = root.resolve()
                try:
                    if str(candidate).startswith(str(root)) and candidate.exists():
                        return candidate
                except (OSError, ValueError):
                    continue

        # 3. 尝试当作绝对路径（仍需在白名单内）
        try:
            abs_candidate = Path("/" + rel).resolve()
            for root in DOWNLOAD_ROOTS:
                if str(abs_candidate).startswith(str(root)) and abs_candidate.exists():
                    return abs_candidate
        except (OSError, ValueError):
            pass

        return None

    def log_message(self, format, *args):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"  [{timestamp}] [{self.address_string()}] {format % args}")


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """多线程 HTTP 服务器，防止单个请求阻塞整个服务"""
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        """覆盖默认错误处理，忽略连接重置等常见网络错误"""
        exc_type, exc_value, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return
        print(f"  [ERROR] {client_address}: {exc_type.__name__}: {exc_value}")


def main():
    # 确保 BASE_DIR 存在
    if not BASE_DIR.exists():
        print(f"⚠️  报告目录不存在，将创建: {BASE_DIR}")
        BASE_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"  📊 Lumi File Server v2.4.1")
    print(f"  {'─' * 56}")
    print(f"  📂 报告目录:   {BASE_DIR}")
    print(f"  📥 下载白名单: {[str(r) for r in DOWNLOAD_ROOTS]}")
    print(f"  🌐 监听地址:   http://0.0.0.0:{PORT}")
    print(f"  📄 报告路径:   {REPORT_PREFIX}/<report.html>")
    print(f"  📥 下载路径:   {DOWNLOAD_PREFIX}/<relative_path>")
    print(f"  👁️ 预览路径:   {PREVIEW_PREFIX}/<relative_path>")
    print(f"  💚 健康检查:   /health")
    print(f"  {'─' * 56}")
    html_files = list_html_files()
    if html_files:
        print(f"  找到 {len(html_files)} 个报告文件:")
        for f in html_files[:10]:
            print(f"    → http://localhost:{PORT}{REPORT_PREFIX}/{f.name}")
        if len(html_files) > 10:
            print(f"    ... 及其他 {len(html_files) - 10} 个文件")
    else:
        print("  ⚠️  未找到 HTML 报告文件")
    print("=" * 60)
    print("  按 Ctrl+C 停止服务\n")

    # 使用多线程服务器
    httpd = ThreadingHTTPServer(("", PORT), CustomHandler)

    # 优雅退出
    def shutdown_handler(signum, frame):
        print("\n  🛑 正在停止服务...")
        httpd.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  🛑 服务已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()