#!/usr/bin/env python3
"""
启动本地静态 HTTP 服务器，首页展示目录下所有 HTML 文件并可点击打开。
用法: python3 serve.py [目录路径]
示例: python3 serve.py /Users/hpfu/Documents/TRADE_DESK
"""

import http.server
import sys
import webbrowser
from pathlib import Path

PORT = 9200
BASE_DIR = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).parent.resolve()
REPORT_PREFIX = "/lumifinreport"


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
<h1>📄 HTML 文件列表</h1>
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

        super().do_GET()

    def do_HEAD(self):
        path = self.path.split("?")[0]
        if path in ("", "/", "/index", "/index.html", REPORT_PREFIX, f"{REPORT_PREFIX}/"):
            self._serve_index(head_only=True)
            return
        if path.startswith(f"{REPORT_PREFIX}/"):
            self._serve_prefixed_static("HEAD")
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

    def log_message(self, format, *args):
        print(f"  [{self.address_string()}] {format % args}")


def main():
    print("=" * 50)
    print(f"📂 目录: {BASE_DIR}")
    print(f"🌐 地址: http://localhost:{PORT}")
    print(f"📄 报告同源路径: {REPORT_PREFIX}/<report.html>")
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