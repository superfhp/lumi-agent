#!/usr/bin/env python3
"""
启动本地静态 HTTP 服务器，首页展示目录下所有 HTML 文件并可点击打开。
用法: python3 serve.py [目录路径]
示例: python3 serve.py /Users/hpfu/Documents/TRADE_DESK
"""

import http.server
import sys
import webbrowser
import urllib.parse
import os
from pathlib import Path

PORT = 9200
BASE_DIR = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path("/mnt/workspace/achieveFinReport").resolve()
REPORT_PREFIX = "/lumifinreport"
DOWNLOAD_PREFIX = "/download"
# 允许下载的目录白名单，文件必须在其中之一下才可访问
DOWNLOAD_ROOTS = [
    Path("/mnt/workspace"),
    Path("/hpfu/media_data"),
]


def list_html_files():
    return sorted(BASE_DIR.glob("*.html"))


def build_index_page():
    files = list_html_files()
    items = "".join(
        f'<li><a href="{REPORT_PREFIX}/{f.name}" target="_blank">{f.name}</a></li>'
        for f in files
    )
    if not items:
        items = "<li>（未找到任何 HTML 文件）</li>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>HTML 文件列表</title>
<style>
  body {{ font-family: -apple-system, sans-serif; background: #0f1419; color: #e0e0e0;
         max-width: 600px; margin: 60px auto; padding: 0 20px; }}
  h1   {{ font-size: 22px; margin-bottom: 24px; color: #fff; }}
  ul   {{ list-style: none; padding: 0; display: flex; flex-direction: column; gap: 10px; }}
  li a {{ display: block; padding: 14px 18px; border-radius: 8px;
         background: #1a1f2e; border: 1px solid #2a3038;
         color: #71a6ff; text-decoration: none; font-size: 15px; }}
  li a:hover {{ background: #252d3d; border-color: #3b82f6; }}
</style>
</head>
<body>
<h1>Lumi LLM Financial Evaluation Report</h1>
<ul>{items}</ul>
</body>
</html>"""


class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("", "/", "/index", "/index.html", REPORT_PREFIX, f"{REPORT_PREFIX}/"):
            self._serve_index()
            return

        if path.startswith(f"{REPORT_PREFIX}/"):
            self._serve_prefixed_static("GET")
            return

        if path.startswith(f"{DOWNLOAD_PREFIX}/"):
            self._serve_download()
            return

        super().do_GET()

    def do_HEAD(self):
        path = self.path.split("?")[0]
        if path in ("", "/", "/index", "/index.html", REPORT_PREFIX, f"{REPORT_PREFIX}/"):
            self._serve_index(head_only=True)
            return
        if path.startswith(f"{REPORT_PREFIX}/"):
            self._serve_prefixed_static("HEAD")
            return
        if path.startswith(f"{DOWNLOAD_PREFIX}/"):
            self._serve_download(head_only=True)
            return
        super().do_HEAD()

    def _serve_index(self, head_only: bool = False):
        content = build_index_page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
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

    def _serve_download(self, head_only: bool = False):
        """提供 /download/<path> 文件下载，自动附加 Content-Disposition"""
        raw_path = self.path.split("?")[0]
        rel = raw_path[len(DOWNLOAD_PREFIX):]
        rel = urllib.parse.unquote(rel).lstrip("/")
        
        # 在白名单中查找匹配的根目录
        file_path = None
        for root in DOWNLOAD_ROOTS:
            candidate = (root / rel).resolve()
            if str(candidate).startswith(str(root)) and candidate.exists():
                file_path = candidate
                break
        
        # 未匹配到任何白名单目录，尝试当作绝对路径（仍需在白名单内）
        if file_path is None:
            abs_candidate = Path("/" + rel).resolve()
            for root in DOWNLOAD_ROOTS:
                if str(abs_candidate).startswith(str(root)) and abs_candidate.exists():
                    file_path = abs_candidate
                    break
        
        if file_path is None:
            self.send_error(404, f"File not found or not in allowed paths: {rel}")
            return
        
        if file_path.is_dir():
            # 目录：列出文件
            items = sorted(file_path.iterdir())
            links = "".join(
                f'<li><a href="{DOWNLOAD_PREFIX}/{rel.rstrip("/")}/{f.name}{"/" if f.is_dir() else ""}">'
                f'{"📁 " if f.is_dir() else "📄 "}{f.name}</a></li>'
                for f in items
            )
            content = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>下载目录</title>
<style>
body {{ font-family: -apple-system, sans-serif; background: #0f1419; color: #e0e0e0;
       max-width: 700px; margin: 40px auto; padding: 0 20px; }}
h2 {{ color: #fff; }} ul {{ list-style: none; padding: 0; }}
li a {{ display: block; padding: 10px 14px; margin: 4px 0; border-radius: 6px;
       background: #1a1f2e; border: 1px solid #2a3038; color: #71a6ff; text-decoration: none; }}
li a:hover {{ background: #252d3d; border-color: #3b82f6; }}
</style></head>
<body><h2>📂 /{rel or "."}</h2><ul>{links or "<li>（空目录）</li>"}</ul></body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            if not head_only:
                self.wfile.write(content)
            return
        
        # 文件：强制下载
        try:
            with open(file_path, "rb") as f:
                data = f.read()
        except Exception as e:
            self.send_error(500, str(e))
            return
        
        filename = file_path.name
        encoded_name = urllib.parse.quote(filename)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{encoded_name}")
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def log_message(self, format, *args):
        print(f"  [{self.address_string()}] {format % args}")


def main():
    print("=" * 50)
    print(f"📂 报告目录: {BASE_DIR}")
    print(f"📥 下载白名单: {[str(r) for r in DOWNLOAD_ROOTS]}")
    print(f"🌐 地址: http://localhost:{PORT}")
    print(f"📄 报告路径: {REPORT_PREFIX}/<report.html>")
    print(f"📥 下载路径: {DOWNLOAD_PREFIX}/<relative_path>")
    print("=" * 50)
    for f in list_html_files():
        print(f"   http://localhost:{PORT}{REPORT_PREFIX}/{f.name}")
    print("=" * 50)
    print("按 Ctrl+C 停止服务\n")

    webbrowser.open(f"http://localhost:{PORT}/")

    with http.server.HTTPServer(("", PORT), CustomHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n服务已停止。")


if __name__ == "__main__":
    main()