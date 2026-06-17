#!/usr/bin/env python3
"""Run MediaCrawler — hybrid mode.

CDP platforms  (xhs, dy, ks):  real Chrome + fixed profile + CDP remote connect
Playwright platforms (wb, bili, tieba, zhihu): standard Playwright + cookie login
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


SUPPORTED_PLATFORMS = {"xhs", "wb", "bili", "dy", "ks", "tieba", "zhihu"}

# 真实 Chrome + CDP 模式的平台（反爬严格，需要真实浏览器指纹）
CDP_PLATFORMS = {"xhs", "dy", "ks"}

# Playwright + cookie 模式的平台
PLAYWRIGHT_PLATFORMS = SUPPORTED_PLATFORMS - CDP_PLATFORMS

MAX_COUNTS = {
    "xhs": 100, "wb": 200, "bili": 100, "dy": 100,
    "ks": 100, "tieba": 200, "zhihu": 100,
}

COOKIE_ENV = {
    "wb": "MEDIACRAWLER_WB_COOKIE",
    "bili": "MEDIACRAWLER_BILI_COOKIE",
    "tieba": "MEDIACRAWLER_TIEBA_COOKIE",
    "zhihu": "MEDIACRAWLER_ZHIHU_COOKIE",
}

DEFAULT_PROFILE_CONFIG = Path(__file__).parent / "profiles.json"
DEFAULT_COOKIE_CONFIG = Path(__file__).parent / "cookies.json"

# platform 级文件锁目录
LOCK_DIR = Path(__file__).parent / ".locks"

# 关键词间随机间隔默认区间（秒）——防风控。仅在 keywords 多于 1 个时生效。
DEFAULT_SLEEP_MIN = 300   # 5 min
DEFAULT_SLEEP_MAX = 900   # 15 min

# heartbeat 刷新间隔
HEARTBEAT_SEC = 30


# ---------------------------------------------------------------------------
# CDP helpers (xhs, dy, ks)
# ---------------------------------------------------------------------------

def load_profile_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or DEFAULT_PROFILE_CONFIG
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[run_crawl] WARNING: failed to read profile config {path}: {exc}", flush=True)
        return {}


def get_platform_profile(config: dict[str, Any], platform: str) -> dict[str, Any]:
    chrome_path = config.get("chrome_path", "/usr/bin/google-chrome")
    profile_base = Path(config.get("profile_base", "/mnt/workspace/chrome-profiles"))
    plat = config.get("platforms", {}).get(platform, {})
    return {
        "chrome_path": chrome_path,
        "cdp_port": plat.get("cdp_port", 9222),
        "profile_dir": str(profile_base / plat.get("profile_dir", platform)),
        "display": plat.get("display", ":99"),
    }


def is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        return s.connect_ex((host, port)) == 0


def find_chrome(preferred: str = "") -> str:
    candidates = [
        preferred,
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in candidates:
        if c and (shutil.which(c) or Path(c).is_file()):
            return c
    return "google-chrome"


def ensure_chrome_running(
    chrome_path: str,
    cdp_port: int,
    profile_dir: str,
    display: str = ":99",
    headless: bool = False,
) -> tuple[bool, str]:
    if is_port_open(cdp_port):
        return True, f"Chrome already running on CDP port {cdp_port}"

    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    chrome_bin = find_chrome(chrome_path)
    cmd = [
        chrome_bin,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-client-side-phishing-detection",
        "--disable-default-apps",
        "--disable-hang-monitor",
        "--disable-popup-blocking",
        "--disable-sync",
        "--metrics-recording-only",
        "--no-service-autorun",
    ]
    if headless:
        cmd.append("--headless=new")

    env = os.environ.copy()
    if display:
        env["DISPLAY"] = display

    print(f"[run_crawl] Launching Chrome: port={cdp_port} profile={profile_dir} headless={headless}", flush=True)
    try:
        subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except FileNotFoundError:
        return False, f"Chrome not found at {chrome_bin}. Install or set chrome_path in profiles.json."

    for i in range(30):
        time.sleep(1)
        if is_port_open(cdp_port):
            print(f"[run_crawl] Chrome ready on port {cdp_port} ({i+1}s)", flush=True)
            return True, f"Chrome launched on CDP port {cdp_port}"
    return False, f"Chrome launched but CDP port {cdp_port} not reachable after 30s"


# ---------------------------------------------------------------------------
# Cookie helpers (wb, bili, tieba, zhihu)
# ---------------------------------------------------------------------------

def load_cookie_from_config(platform: str, config_path: Path | None = None) -> str:
    path = config_path or DEFAULT_COOKIE_CONFIG
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get(platform, "")
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[run_crawl] WARNING: failed to read cookie config {path}: {exc}", flush=True)
        return ""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MediaCrawler (hybrid CDP / Playwright mode).")
    parser.add_argument("--mediacrawler-root",
                        default=os.environ.get("MEDIACRAWLER_ROOT", "/mnt/workspace/MediaCrawler"))
    parser.add_argument("--platform", required=True, choices=sorted(SUPPORTED_PLATFORMS))
    parser.add_argument("--keywords", required=True, help="Comma-separated search keywords.")
    parser.add_argument("--max-notes-count", required=True, type=int)
    parser.add_argument("--with-comments", action="store_true")
    parser.add_argument("--with-sub-comments", action="store_true")
    parser.add_argument("--save-data-option", default="jsonl",
                        choices=["jsonl", "json", "csv", "excel", "sqlite", "db", "postgres", "mongodb"])
    parser.add_argument("--output-root", default="", help="Base output directory.")
    parser.add_argument("--max-concurrency", default=1, type=int,
                        help="MediaCrawler 内部并发。默认 1（严禁提高，防风控）。")
    # MediaCrawler 内部请求间隔是一个随机区间（CRAWLER_MIN_SLEEP_SEC / CRAWLER_MAX_SLEEP_SEC）。
    # 这里同样按区间暴露：固定间隔反而更易被识别，建议保留 min<max。
    parser.add_argument("--sleep-min-sec", default=0.5, type=float,
                        help="MediaCrawler 内部请求间隔随机下限（秒），默认 0.5。")
    parser.add_argument("--sleep-max-sec", default=1.5, type=float,
                        help="MediaCrawler 内部请求间隔随机上限（秒），默认 1.5。")
    # 向后兼容：若仍传 --sleep-sec，则同时填入 min 和 max（行为退化为固定间隔）。
    parser.add_argument("--sleep-sec", default=None, type=float,
                        help="[deprecated] 等价于 --sleep-min-sec=X --sleep-max-sec=X。仅做向后兼容。")
    parser.add_argument("--per-keyword-sleep-min", default=DEFAULT_SLEEP_MIN, type=int,
                        help="关键词之间随机间隔下限（秒），默认 300。1 个关键词时不生效。")
    parser.add_argument("--per-keyword-sleep-max", default=DEFAULT_SLEEP_MAX, type=int,
                        help="关键词之间随机间隔上限（秒），默认 900。1 个关键词时不生效。")
    parser.add_argument("--timeout", default=3600, type=int)

    # 后台任务 / 状态文件
    parser.add_argument("--background", action="store_true",
                        help="后台运行。前台只返回 task_id/log_path/status_path 后退出，实际采集在 fork 出的子进程里跑。")
    parser.add_argument("--status-file", default="",
                        help="状态文件路径。默认为 <run_output>/_task/status.json；--background 下被创建者覆盖。")
    parser.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)  # 后台子进程标记，外部不要传

    # CDP 模式参数 (xhs, dy, ks)
    parser.add_argument("--profile-config", default="",
                        help="Path to profiles.json. Default: <script_dir>/profiles.json")
    parser.add_argument("--cdp-port", type=int, default=0, help="Override CDP port.")
    parser.add_argument("--chrome-profile-dir", default="", help="Override Chrome profile dir.")
    parser.add_argument("--headless", default="", choices=["true", "false", ""],
                        help="CDP default: false; Playwright default: true. Leave empty for auto.")
    parser.add_argument("--skip-chrome-launch", action="store_true",
                        help="Skip Chrome launch check (assume already running).")

    # Playwright/cookie 模式参数 (wb, bili, tieba, zhihu)
    parser.add_argument("--cookies", default="", help="Cookie string for Playwright platforms.")
    parser.add_argument("--cookie-config", default="",
                        help="Path to cookies.json. Default: <script_dir>/cookies.json")
    parser.add_argument("--login-type", default="cookie", choices=["cookie", "qrcode", "phone"])

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def normalize_count(platform: str, requested: int) -> tuple[int, list[str]]:
    warnings: list[str] = []
    count = requested
    if platform == "xhs" and count < 20:
        count = 20
        warnings.append("xhs minimum effective page size is 20; raised to 20.")
    cap = MAX_COUNTS[platform]
    if count > cap:
        count = cap
        warnings.append(f"{platform} max_notes_count capped at {cap}.")
    return count, warnings


def sanitize_name(value: str) -> str:
    sanitized = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value.strip(), flags=re.UNICODE)
    return sanitized.strip("._")[:60] or "keyword"


def count_jsonl(path: Path) -> int:
    with path.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def count_json(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    return len(data) if isinstance(data, list) else 1


def count_csv(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig") as f:
        lines = [line for line in f if line.strip()]
    return max(0, len(lines) - 1)


def record_count(path: Path) -> int | None:
    try:
        if path.suffix == ".jsonl": return count_jsonl(path)
        if path.suffix == ".json": return count_json(path)
        if path.suffix == ".csv": return count_csv(path)
    except Exception:
        pass
    return None


def locate_outputs(platform: str, fmt: str, output_root: Path, started_at: float) -> list[dict[str, Any]]:
    platform_dir = output_root / platform / fmt
    if not platform_dir.exists():
        return []
    outputs: list[dict[str, Any]] = []
    for path in sorted(platform_dir.glob(f"search_*.{fmt}")):
        if path.stat().st_mtime + 1 < started_at:
            continue
        outputs.append({
            "type": "comments" if "comments" in path.name else "contents",
            "path": str(path.resolve()),
            "records": record_count(path),
            "bytes": path.stat().st_size,
        })
    return outputs


PREPROCESS_SCRIPT = str((Path(__file__).parent / "step1_preprocess.py").resolve())
OCR_SCRIPT = str((Path(__file__).parent / "step1_ocr.py").resolve())


def build_preprocess_cmd(platform: str, outputs: list[dict[str, Any]]) -> str | None:
    """根据 locate_outputs 的结果，拼出一条建议的 step1_preprocess.py 调用命令。

    目前 step1_preprocess.py 仅适配 xhs，其它平台返回 None。
    没有 contents 文件时返回 None；只有 contents、没有 comments 时省略 --comments。
    若同目录存在 ocr_<contents_stem>.jsonl，自动拼上 --ocr-file 参数。
    """
    if platform != "xhs":
        return None
    contents = next((o["path"] for o in outputs if o.get("type") == "contents"), None)
    if not contents:
        return None
    comments = next((o["path"] for o in outputs if o.get("type") == "comments"), None)
    cmd = f'python {PREPROCESS_SCRIPT} \\\n  --input "{contents}"'
    if comments:
        cmd += f' \\\n  --comments "{comments}"'
    contents_path = Path(contents)
    ocr_guess = contents_path.parent / f"ocr_{contents_path.stem}.jsonl"
    cmd += f' \\\n  --ocr-file "{ocr_guess}"  # 如 OCR 未跑请先执行 suggested_ocr_cmds'
    return cmd


def build_ocr_cmd(platform: str, outputs: list[dict[str, Any]]) -> str | None:
    """生成 step1_ocr.py 建议命令（仅 xhs）。没有 contents 返回 None。

    默认拼上：
    - --workers 8           （多进程，每 worker 独立 RapidOCR）
    - --max-images-per-note 8（前 8 张承载 80%+ 信息）
    - --resize-long-edge 1280（PIL 缩图，砍掉 50%+ OCR 耗时）
    - --background          （脱离 caller 600s timeout 笼子）

    如需改参数，复制命令后手动调即可。
    """
    if platform != "xhs":
        return None
    contents = next((o["path"] for o in outputs if o.get("type") == "contents"), None)
    if not contents:
        return None
    return (
        f'python {OCR_SCRIPT} \\\n'
        f'  --input "{contents}" \\\n'
        f'  --workers 8 \\\n'
        f'  --max-images-per-note 8 \\\n'
        f'  --resize-long-edge 1280 \\\n'
        f'  --background  # 后台跑，查 status_path/log_path 看进度'
    )


# ---------------------------------------------------------------------------
# Platform 级文件锁
# ---------------------------------------------------------------------------

def acquire_platform_lock(platform: str, run_id: str, keywords: list[str]) -> tuple[Any, Path, dict[str, Any] | None]:
    """取 platform 级文件锁。返回 (file_handle, lock_path, held_by | None)。

    拿不到锁时 file_handle=None、held_by 为现有使用者信息；拿到时 held_by=None。
    锁文件留到进程结束由 OS 自动释放（fcntl.flock LOCK_EX | LOCK_NB）。
    """
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = LOCK_DIR / f"{platform}.lock"
    fh = open(lock_path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno not in (errno.EAGAIN, errno.EACCES):
            fh.close()
            raise
        # 被占用——读文件里的 holder 信息
        held_by: dict[str, Any] | None = None
        try:
            fh.seek(0)
            content = fh.read().strip()
            held_by = json.loads(content) if content else None
        except Exception:
            held_by = None
        fh.close()
        return None, lock_path, held_by or {"info": "lock held but holder file empty"}

    # 拿到锁了，写入 holder 信息
    fh.seek(0)
    fh.truncate()
    holder = {
        "pid": os.getpid(),
        "run_id": run_id,
        "platform": platform,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "keywords": keywords,
    }
    fh.write(json.dumps(holder, ensure_ascii=False, indent=2))
    fh.flush()
    return fh, lock_path, None


# ---------------------------------------------------------------------------
# 后台进程 + status.json
# ---------------------------------------------------------------------------

def write_status(status_path: Path, state: dict[str, Any]) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["last_heartbeat"] = datetime.now().isoformat(timespec="seconds")
    tmp = status_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(status_path)


def spawn_background(orig_argv: list[str], run_output: Path) -> dict[str, Any]:
    """fork 出 nohup 子进程。前台返回 task 信息，供 manifest 输出后退出。

    子进程接受除 --background 外的原参数 + 额外 --_child --status-file <path>，重跑 main()。
    """
    task_dir = run_output / "_task"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_id = run_output.name  # 复用 run_id 作为 task_id
    log_path = task_dir / "run.log"
    status_path = task_dir / "status.json"

    # 过滤掉 --background，避免子进程反复 fork
    child_argv = [a for a in orig_argv if a != "--background"]
    child_cmd = [sys.executable, str(Path(__file__).resolve()), *child_argv,
                 "--_child", "--status-file", str(status_path)]

    write_status(status_path, {
        "state": "starting",
        "task_id": task_id,
        "run_id": task_id,
        "log_path": str(log_path),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "cmd": child_cmd,
    })

    log_fh = open(log_path, "a", buffering=1)
    proc = subprocess.Popen(
        child_cmd,
        stdout=log_fh, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # 脱离父会话，不随前台退出而死
    )
    return {
        "task_id": task_id,
        "pid": proc.pid,
        "log_path": str(log_path),
        "status_path": str(status_path),
    }


def emit_manifest(manifest: dict[str, Any], exit_code: int) -> None:
    print("\n[manifest]", flush=True)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    raise SystemExit(exit_code)


def build_command(
    args: argparse.Namespace,
    keyword: str,
    max_notes_count: int,
    output_dir: Path,
    cookies: str,
    headless: str,
) -> list[str]:
    cmd = [
        "uv", "run", "main.py",
        "--platform", args.platform,
        "--lt", args.login_type,
        "--type", "search",
        "--keywords", keyword,
        "--max_notes_count", str(max_notes_count),
        "--get_comment", "true" if args.with_comments else "false",
        "--get_sub_comment", "true" if args.with_sub_comments else "false",
        "--headless", headless,
        "--save_data_option", args.save_data_option,
        "--save_data_path", str(output_dir),
        "--max_concurrency_num", str(args.max_concurrency),
        # MediaCrawler CLI 接受 --crawler_min_sleep_sec / --crawler_max_sleep_sec
        # （旧名 --crawler_sleep_sec 不存在，传过去 typer 会直接 No such option 退出）。
        "--crawler_min_sleep_sec", str(args.sleep_min_sec),
        "--crawler_max_sleep_sec", str(args.sleep_max_sec),
    ]
    if cookies:
        cmd.extend(["--cookies", cookies])
    return cmd


def run_streaming(command: list[str], cwd: Path, timeout: int, env: dict | None = None) -> tuple[int, list[str]]:
    """Run a subprocess and stream its combined stdout/stderr.

    IMPORTANT: Some subprocesses may emit non-UTF8 bytes (or mixed encodings).
    We must never crash the orchestrator due to UnicodeDecodeError.
    """
    started = time.monotonic()
    log_tail: list[str] = []

    # Use bytes mode + manual decode to avoid UnicodeDecodeError.
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        text=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        env=env,
    )
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            # Decode best-effort; keep the orchestrator alive even with bad bytes.
            try:
                line = raw.decode("utf-8", errors="replace")
            except Exception:
                line = repr(raw) + "\n"

            print(line, end="", flush=True)
            log_tail.append(line.rstrip("\n"))
            if len(log_tail) > 120:
                log_tail = log_tail[-120:]
            if time.monotonic() - started > timeout:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                log_tail.append(f"[run_crawl] timed out after {timeout}s")
                return 124, log_tail
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass

    return proc.wait(), log_tail


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    root = Path(args.mediacrawler_root).resolve()
    warnings: list[str] = []
    is_cdp = args.platform in CDP_PLATFORMS

    if not root.exists() or not (root / "main.py").exists():
        emit_manifest({"status": "error", "error": f"MediaCrawler root not found: {root}"}, 2)

    # --- 并发/间隔参数校验 ---
    if args.max_concurrency != 1:
        warnings.append(
            f"max_concurrency={args.max_concurrency} ≠ 1，已强制降为 1（防风控；如确需提高请显式承担风险）。"
        )
        args.max_concurrency = 1

    # --sleep-sec 是 deprecated 的固定值入口；若用户传了，就同时覆写 min/max（行为退化为固定间隔）。
    if args.sleep_sec is not None:
        if args.sleep_sec < 0:
            emit_manifest({
                "status": "error",
                "error": f"invalid --sleep-sec: {args.sleep_sec} (must be >= 0)",
            }, 2)
        warnings.append(
            f"--sleep-sec={args.sleep_sec} 已 deprecated，已转为 --sleep-min-sec/--sleep-max-sec 同值。"
            " 建议改用区间，固定间隔反而更易被识别。"
        )
        args.sleep_min_sec = args.sleep_sec
        args.sleep_max_sec = args.sleep_sec
    if args.sleep_min_sec < 0 or args.sleep_max_sec < args.sleep_min_sec:
        emit_manifest({
            "status": "error",
            "error": (
                f"invalid request sleep range: min={args.sleep_min_sec} max={args.sleep_max_sec}"
                " (need 0 <= min <= max)"
            ),
        }, 2)

    if args.per_keyword_sleep_min < 0 or args.per_keyword_sleep_max < args.per_keyword_sleep_min:
        emit_manifest({
            "status": "error",
            "error": f"invalid sleep range: min={args.per_keyword_sleep_min} max={args.per_keyword_sleep_max}",
        }, 2)

    max_notes_count, count_warnings = normalize_count(args.platform, args.max_notes_count)
    warnings.extend(count_warnings)

    # --- Mode-specific setup ---
    run_env = os.environ.copy()
    cookies = ""
    cdp_port = 0
    profile_dir = ""

    if is_cdp:
        # ===== CDP 模式：xhs, dy, ks =====
        cfg_path = Path(args.profile_config).resolve() if args.profile_config else None
        profile_cfg = load_profile_config(cfg_path)
        plat_profile = get_platform_profile(profile_cfg, args.platform)

        cdp_port = args.cdp_port or plat_profile["cdp_port"]
        profile_dir = args.chrome_profile_dir or plat_profile["profile_dir"]
        chrome_path = plat_profile["chrome_path"]
        display = plat_profile["display"]
        headless_flag = args.headless == "true" if args.headless else False  # CDP 默认 non-headless

        if not args.skip_chrome_launch:
            ok, msg = ensure_chrome_running(chrome_path, cdp_port, profile_dir, display, headless_flag)
            print(f"[run_crawl] Chrome: {msg}", flush=True)
            if not ok:
                emit_manifest({"status": "error", "error": msg,
                               "hint": "Start Chrome manually or check profiles.json."}, 2)
        elif not is_port_open(cdp_port):
            warnings.append(f"--skip-chrome-launch but CDP port {cdp_port} not reachable.")

        run_env["BROWSER_CDP_URL"] = f"http://127.0.0.1:{cdp_port}"
        headless_str = args.headless if args.headless else "false"

        print(f"[run_crawl] mode=CDP platform={args.platform} cdp_port={cdp_port} profile={profile_dir}", flush=True)

    else:
        # ===== Playwright 模式：wb, bili, tieba, zhihu =====
        cookie_cfg = Path(args.cookie_config).resolve() if args.cookie_config else None
        cookies = (
            args.cookies
            or load_cookie_from_config(args.platform, cookie_cfg)
            or os.environ.get(COOKIE_ENV.get(args.platform, ""), "")
        )
        if args.login_type == "cookie" and not cookies:
            cfg = cookie_cfg or DEFAULT_COOKIE_CONFIG
            env_key = COOKIE_ENV.get(args.platform, f"MEDIACRAWLER_{args.platform.upper()}_COOKIE")
            warnings.append(f"No cookie. Add to {cfg} or set {env_key} or pass --cookies.")

        headless_str = args.headless if args.headless else "true"  # Playwright 默认 headless

        print(f"[run_crawl] mode=Playwright platform={args.platform} cookie={'set' if cookies else 'MISSING'}", flush=True)

    # --- Common flow ---
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    if not keywords:
        emit_manifest({"status": "error", "error": "No non-empty keywords provided."}, 2)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    base_output = Path(args.output_root).resolve() if args.output_root else root / "skill_runs"
    run_output = base_output / run_id
    run_output.mkdir(parents=True, exist_ok=True)

    # --- 后台模式：fork 子进程后前台立即返回 ---
    if args.background and not args._child:
        task_info = spawn_background(sys.argv[1:], run_output)
        manifest_fg = {
            "status": "background_started",
            "run_id": run_id,
            "platform": args.platform,
            "keywords": keywords,
            "max_notes_count": max_notes_count,
            "output_root": str(run_output.resolve()),
            "warnings": warnings,
            **task_info,
            "hint": (
                "后台任务已启动。查 status_path 文件可获取实时状态，"
                "查 log_path 文件可获取实时日志。同 platform 不可并发再起新任务。"
            ),
        }
        emit_manifest(manifest_fg, 0)

    # --- 取 platform 级文件锁（前台 / 子进程都要锁）---
    lock_handle, lock_path, held_by = acquire_platform_lock(args.platform, run_id, keywords)
    if lock_handle is None:
        emit_manifest({
            "status": "error",
            "error": "lock_held",
            "platform": args.platform,
            "lock_path": str(lock_path),
            "held_by": held_by,
            "hint": (
                f"platform={args.platform} 已有任务在跑，请等待 / 查 held_by.pid / "
                f"或确认后 rm {lock_path} 再重试。"
            ),
        }, 9)

    # --- status.json 路径（子进程模式：父进程传入；其它情况：默认到 _task/status.json）---
    status_path = Path(args.status_file).resolve() if args.status_file else run_output / "_task" / "status.json"
    status_state: dict[str, Any] = {
        "state": "running",
        "task_id": run_id,
        "run_id": run_id,
        "platform": args.platform,
        "keywords": keywords,
        "total_keywords": len(keywords),
        "current_keyword_index": 0,
        "current_keyword": None,
        "sleep_until": None,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "output_root": str(run_output.resolve()),
    }
    write_status(status_path, status_state)

    all_files: list[dict[str, Any]] = []
    keyword_results: list[dict[str, Any]] = []
    combined_log_tail: list[str] = []
    overall_success = True

    print(
        f"[run_crawl] run_id={run_id} keywords={len(keywords)} "
        f"max_notes_count={max_notes_count} "
        f"concurrency={args.max_concurrency} "
        f"sleep={args.sleep_min_sec}-{args.sleep_max_sec}s "
        f"per_kw_sleep={args.per_keyword_sleep_min}-{args.per_keyword_sleep_max}s",
        flush=True,
    )

    per_kw_timeout = max(60, int(args.timeout / len(keywords)))
    try:
        for idx, keyword in enumerate(keywords, 1):
            kw_output = run_output / f"{idx:02d}_{sanitize_name(keyword)}"
            kw_output.mkdir(parents=True, exist_ok=True)
            command = build_command(args, keyword, max_notes_count, kw_output, cookies, headless_str)
            started_at = datetime.now().timestamp()

            status_state.update({
                "current_keyword_index": idx,
                "current_keyword": keyword,
                "sleep_until": None,
            })
            write_status(status_path, status_state)

            print(f"\n[run_crawl] keyword {idx}/{len(keywords)}: {keyword}", flush=True)
            returncode, log_tail = run_streaming(command, root, per_kw_timeout, env=run_env)
            outputs = locate_outputs(args.platform, args.save_data_option, kw_output, started_at)
            all_files.extend(outputs)
            combined_log_tail.extend(log_tail)
            combined_log_tail = combined_log_tail[-120:]

            kw_ok = returncode == 0 and bool(outputs)
            overall_success = overall_success and kw_ok
            keyword_results.append({
                "keyword": keyword,
                "status": "success" if kw_ok else "error",
                "returncode": returncode,
                "output_dir": str(kw_output.resolve()),
                "files": outputs,
                "preprocess_cmd": build_preprocess_cmd(args.platform, outputs),
                "ocr_cmd": build_ocr_cmd(args.platform, outputs),
            })
            print(
                f"[run_crawl] done: {keyword} status={'ok' if kw_ok else 'error'} "
                f"files={len(outputs)} rc={returncode}",
                flush=True,
            )

            # --- 关键词间随机间隔（最后一个关键词不睡）---
            if idx < len(keywords) and args.per_keyword_sleep_max > 0:
                sleep_sec = random.randint(args.per_keyword_sleep_min, args.per_keyword_sleep_max)
                wake_at = datetime.now().timestamp() + sleep_sec
                status_state.update({
                    "current_keyword": keyword,
                    "sleep_until": datetime.fromtimestamp(wake_at).isoformat(timespec="seconds"),
                })
                write_status(status_path, status_state)
                print(
                    f"[run_crawl] 防风控：下一个关键词前等待 {sleep_sec}s "
                    f"(范围 {args.per_keyword_sleep_min}-{args.per_keyword_sleep_max}s)",
                    flush=True,
                )
                # heartbeat 分块睡，让 status.json 持续刷新
                remaining = sleep_sec
                while remaining > 0:
                    chunk = min(HEARTBEAT_SEC, remaining)
                    time.sleep(chunk)
                    remaining -= chunk
                    write_status(status_path, status_state)

        manifest: dict[str, Any] = {
            "status": "success" if overall_success else "error",
            "run_id": run_id,
            "task_id": run_id,
            "mode": "cdp" if is_cdp else "playwright",
            "platform": args.platform,
            "keywords": keywords,
            "max_notes_count": max_notes_count,
            "with_comments": args.with_comments,
            "max_concurrency": args.max_concurrency,
            "sleep_min_sec": args.sleep_min_sec,
            "sleep_max_sec": args.sleep_max_sec,
            "per_keyword_sleep_min": args.per_keyword_sleep_min,
            "per_keyword_sleep_max": args.per_keyword_sleep_max,
            "output_root": str(run_output.resolve()),
            "status_path": str(status_path),
            "files": all_files,
            "keyword_results": keyword_results,
            "suggested_ocr_cmds": [
                kr["ocr_cmd"] for kr in keyword_results if kr.get("ocr_cmd")
            ],
            "suggested_preprocess_cmds": [
                kr["preprocess_cmd"] for kr in keyword_results if kr.get("preprocess_cmd")
            ],
            "warnings": warnings,
            "log_tail": "\n".join(combined_log_tail[-80:]),
        }
        if is_cdp:
            manifest["cdp_port"] = cdp_port
            manifest["chrome_profile"] = profile_dir

        # 落盘 manifest（后台模式下前台拿不到 stdout，必须文件可读）
        manifest_path = run_output / "_task" / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["manifest_path"] = str(manifest_path)

        # 更新 status 为 done/error
        status_state.update({
            "state": "done" if overall_success else "error",
            "current_keyword": None,
            "sleep_until": None,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "manifest_path": str(manifest_path),
        })
        write_status(status_path, status_state)

        emit_manifest(manifest, 0 if overall_success else 1)
    finally:
        # 释放锁
        try:
            if lock_handle is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                lock_handle.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as exc:
        emit_manifest({"status": "error", "error": f"Not found: {exc}"}, 127)
    except Exception as exc:
        emit_manifest({"status": "error", "error": f"run_crawl.py: {type(exc).__name__}: {exc}"}, 1)
