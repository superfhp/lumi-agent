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
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit

PORT = 9200
BASE_DIR = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).parent.resolve()
REPORT_PREFIX = "/lumifinreport"
APP_UPSTREAM = "http://127.0.0.1:3000"


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
        path = self.path.split("?")[0].rstrip("/") or "/"

        if path == REPORT_PREFIX:
            self.send_response(301)
            self.send_header("Location", f"{REPORT_PREFIX}/")
            self.end_headers()
            return

        if self._is_report_path(path):
            self._handle_report_request()
            return

        self._proxy_request()

    def do_POST(self):
        self._proxy_request()

    def do_PUT(self):
        self._proxy_request()

    def do_PATCH(self):
        self._proxy_request()

    def do_DELETE(self):
        self._proxy_request()

    def do_OPTIONS(self):
        self._proxy_request()

    def do_HEAD(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if self._is_report_path(path):
            self._handle_report_request(head_only=True)
            return
        self._proxy_request()

    def _is_report_path(self, path: str) -> bool:
        return path == REPORT_PREFIX or path.startswith(f"{REPORT_PREFIX}/")

    def _strip_report_prefix(self, raw_path: str) -> str:
        if raw_path.startswith(REPORT_PREFIX):
            stripped = raw_path[len(REPORT_PREFIX):]
            return stripped if stripped else "/"
        return raw_path

    def _handle_report_request(self, head_only: bool = False):
        clean_path = self.path.split("?")[0]
        stripped = self._strip_report_prefix(clean_path)

        if stripped in ("", "/", "/index", "/index.html"):
            content = build_index_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head_only:
                self.wfile.write(content)
            return

        original_path = self.path
        try:
            self.path = self._strip_report_prefix(self.path)
            if head_only:
                super().do_HEAD()
            else:
                super().do_GET()
        finally:
            self.path = original_path

    def _proxy_request(self):
        upstream_url = urljoin(APP_UPSTREAM.rstrip("/") + "/", self.path.lstrip("/"))

        body = None
        content_length = self.headers.get("Content-Length")
        if content_length:
            try:
                length = int(content_length)
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else None

        outgoing_headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "connection", "proxy-connection", "content-length"}
        }
        outgoing_headers["Host"] = urlsplit(APP_UPSTREAM).netloc
        outgoing_headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        outgoing_headers["X-Forwarded-Proto"] = "http"

        req = urllib_request.Request(
            upstream_url,
            data=body,
            headers=outgoing_headers,
            method=self.command,
        )

        try:
            with urllib_request.urlopen(req, timeout=30) as resp:
                response_body = b"" if self.command == "HEAD" else resp.read()
                self.send_response(resp.getcode())
                for key, value in resp.headers.items():
                    lower = key.lower()
                    if lower in {"connection", "proxy-connection", "transfer-encoding", "content-length"}:
                        continue
                    if lower == "location":
                        value = self._rewrite_location(value)
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(response_body)
        except HTTPError as e:
            response_body = e.read() if e.fp else b""
            self.send_response(e.code)
            for key, value in e.headers.items():
                lower = key.lower()
                if lower in {"connection", "proxy-connection", "transfer-encoding", "content-length"}:
                    continue
                if lower == "location":
                    value = self._rewrite_location(value)
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            if self.command != "HEAD" and response_body:
                self.wfile.write(response_body)
        except URLError as e:
            self.send_error(502, f"上游服务不可达: {e}")

    def _rewrite_location(self, location: str) -> str:
        if not location:
            return location

        target = urlsplit(APP_UPSTREAM)
        incoming = urlsplit(location)
        if incoming.scheme == target.scheme and incoming.netloc == target.netloc:
            return "/" + incoming.path.lstrip("/") + (f"?{incoming.query}" if incoming.query else "")
        return location

    def log_message(self, format, *args):
        print(f"  [{self.address_string()}] {format % args}")


def main():
    print("=" * 50)
    print(f"📂 目录: {BASE_DIR}")
    print(f"🌐 地址: http://localhost:{PORT}")
    print(f"🧭 主站同源代理: /  ->  {APP_UPSTREAM}")
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