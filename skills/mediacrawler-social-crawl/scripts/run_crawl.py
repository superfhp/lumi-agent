#!/usr/bin/env python3
"""Run MediaCrawler with streaming logs and emit a final JSON manifest."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


SUPPORTED_PLATFORMS = {"xhs", "wb", "bili", "dy", "ks", "tieba", "zhihu"}
MAX_COUNTS = {
    "xhs": 100,
    "wb": 200,
    "bili": 100,
    "dy": 100,
    "ks": 100,
    "tieba": 200,
    "zhihu": 100,
}
COOKIE_ENV = {
    "xhs": "MEDIACRAWLER_XHS_COOKIE",
    "wb": "MEDIACRAWLER_WB_COOKIE",
    "bili": "MEDIACRAWLER_BILI_COOKIE",
    "dy": "MEDIACRAWLER_DY_COOKIE",
    "ks": "MEDIACRAWLER_KS_COOKIE",
    "tieba": "MEDIACRAWLER_TIEBA_COOKIE",
    "zhihu": "MEDIACRAWLER_ZHIHU_COOKIE",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MediaCrawler from the social crawl skill.")
    parser.add_argument("--mediacrawler-root", default=os.environ.get("MEDIACRAWLER_ROOT", "/mnt/workspace/MediaCrawler"))
    parser.add_argument("--platform", required=True, choices=sorted(SUPPORTED_PLATFORMS))
    parser.add_argument("--keywords", required=True, help="Comma-separated search keywords.")
    parser.add_argument("--max-notes-count", required=True, type=int)
    parser.add_argument("--with-comments", action="store_true")
    parser.add_argument("--with-sub-comments", action="store_true")
    parser.add_argument("--cookies", default="", help="Cookie string. Prefer env vars instead of this argument.")
    parser.add_argument("--login-type", default="cookie", choices=["cookie", "qrcode", "phone"])
    parser.add_argument("--save-data-option", default="jsonl", choices=["jsonl", "json", "csv", "excel", "sqlite", "db", "postgres", "mongodb"])
    parser.add_argument("--output-root", default="", help="Base output directory. Defaults to <MediaCrawler>/skill_runs.")
    parser.add_argument("--headless", default="true", choices=["true", "false"])
    parser.add_argument("--max-concurrency", default=2, type=int, help="Maps to MediaCrawler --max_concurrency_num.")
    parser.add_argument("--sleep-sec", default=0.5, type=float, help="Maps to MediaCrawler --crawler_sleep_sec.")
    parser.add_argument("--timeout", default=3600, type=int)
    return parser.parse_args()


def normalize_count(platform: str, requested: int) -> tuple[int, list[str]]:
    warnings: list[str] = []
    count = requested
    if platform == "xhs" and count < 20:
        count = 20
        warnings.append("xhs minimum effective page size is 20; raised max_notes_count to 20.")
    platform_max = MAX_COUNTS[platform]
    if count > platform_max:
        count = platform_max
        warnings.append(f"{platform} max_notes_count capped at {platform_max}.")
    return count, warnings


def sanitize_name(value: str) -> str:
    sanitized = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value.strip(), flags=re.UNICODE)
    return sanitized.strip("._")[:60] or "keyword"


def count_jsonl(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def count_json(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    return len(data) if isinstance(data, list) else 1


def count_csv(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig") as handle:
        lines = [line for line in handle if line.strip()]
    return max(0, len(lines) - 1)


def record_count(path: Path) -> int | None:
    try:
        if path.suffix == ".jsonl":
            return count_jsonl(path)
        if path.suffix == ".json":
            return count_json(path)
        if path.suffix == ".csv":
            return count_csv(path)
    except Exception:
        return None
    return None


def locate_outputs(platform: str, save_data_option: str, output_root: Path, started_at: float) -> list[dict[str, Any]]:
    platform_dir = output_root / platform / save_data_option
    if not platform_dir.exists():
        return []

    outputs: list[dict[str, Any]] = []
    for path in sorted(platform_dir.glob(f"search_*.{save_data_option}")):
        if path.stat().st_mtime + 1 < started_at:
            continue
        outputs.append(
            {
                "type": "comments" if "comments" in path.name else "contents",
                "path": str(path.resolve()),
                "records": record_count(path),
                "bytes": path.stat().st_size,
            }
        )
    return outputs


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
) -> list[str]:
    command = [
        "uv",
        "run",
        "main.py",
        "--platform",
        args.platform,
        "--lt",
        args.login_type,
        "--type",
        "search",
        "--keywords",
        keyword,
        "--max_notes_count",
        str(max_notes_count),
        "--get_comment",
        "true" if args.with_comments else "false",
        "--get_sub_comment",
        "true" if args.with_sub_comments else "false",
        "--headless",
        args.headless,
        "--save_data_option",
        args.save_data_option,
        "--save_data_path",
        str(output_dir),
        "--max_concurrency_num",
        str(args.max_concurrency),
        "--crawler_sleep_sec",
        str(args.sleep_sec),
    ]
    if cookies:
        command.extend(["--cookies", cookies])
    return command


def run_streaming(command: list[str], cwd: Path, timeout: int) -> tuple[int, list[str]]:
    started = time.monotonic()
    log_tail: list[str] = []
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )

    assert proc.stdout is not None
    try:
        for line in proc.stdout:
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
        if proc.stdout:
            proc.stdout.close()

    return proc.wait(), log_tail


def main() -> None:
    args = parse_args()
    root = Path(args.mediacrawler_root).resolve()
    warnings: list[str] = []

    if not root.exists() or not (root / "main.py").exists():
        emit_manifest(
            {
                "status": "error",
                "error": f"MediaCrawler root not found or missing main.py: {root}",
            },
            2,
        )

    max_notes_count, count_warnings = normalize_count(args.platform, args.max_notes_count)
    warnings.extend(count_warnings)

    cookies = args.cookies or os.environ.get(COOKIE_ENV[args.platform], "")
    if args.login_type == "cookie" and not cookies:
        warnings.append(f"No cookie provided. Set {COOKIE_ENV[args.platform]} or pass --cookies.")

    keywords = [item.strip() for item in args.keywords.split(",") if item.strip()]
    if not keywords:
        emit_manifest({"status": "error", "error": "No non-empty keywords were provided."}, 2)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    base_output = Path(args.output_root).resolve() if args.output_root else root / "skill_runs"
    run_output = base_output / run_id
    run_output.mkdir(parents=True, exist_ok=True)

    all_files: list[dict[str, Any]] = []
    keyword_results: list[dict[str, Any]] = []
    combined_log_tail: list[str] = []
    overall_success = True

    print(
        f"[run_crawl] run_id={run_id} platform={args.platform} keywords={len(keywords)} "
        f"max_notes_count={max_notes_count} comments={args.with_comments} "
        f"max_concurrency={args.max_concurrency} sleep_sec={args.sleep_sec}",
        flush=True,
    )

    per_keyword_timeout = max(60, int(args.timeout / len(keywords)))
    for index, keyword in enumerate(keywords, start=1):
        keyword_output = run_output / f"{index:02d}_{sanitize_name(keyword)}"
        keyword_output.mkdir(parents=True, exist_ok=True)
        command = build_command(args, keyword, max_notes_count, keyword_output, cookies)
        started_at = datetime.now().timestamp()

        print(f"\n[run_crawl] keyword {index}/{len(keywords)}: {keyword}", flush=True)
        returncode, log_tail = run_streaming(command, root, per_keyword_timeout)
        outputs = locate_outputs(args.platform, args.save_data_option, keyword_output, started_at)
        all_files.extend(outputs)
        combined_log_tail.extend(log_tail)
        combined_log_tail = combined_log_tail[-120:]

        keyword_success = returncode == 0 and bool(outputs)
        overall_success = overall_success and keyword_success
        keyword_results.append(
            {
                "keyword": keyword,
                "status": "success" if keyword_success else "error",
                "returncode": returncode,
                "output_dir": str(keyword_output.resolve()),
                "files": outputs,
            }
        )
        print(
            f"[run_crawl] keyword done: {keyword} status={'success' if keyword_success else 'error'} "
            f"files={len(outputs)} returncode={returncode}",
            flush=True,
        )

    manifest = {
        "status": "success" if overall_success else "error",
        "run_id": run_id,
        "platform": args.platform,
        "keywords": keywords,
        "max_notes_count": max_notes_count,
        "with_comments": args.with_comments,
        "max_concurrency": args.max_concurrency,
        "sleep_sec": args.sleep_sec,
        "output_root": str(run_output.resolve()),
        "files": all_files,
        "keyword_results": keyword_results,
        "warnings": warnings,
        "log_tail": "\n".join(combined_log_tail[-80:]),
    }
    emit_manifest(manifest, 0 if manifest["status"] == "success" else 1)


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as exc:
        emit_manifest(
            {
                "status": "error",
                "error": f"Required executable or path was not found: {exc}",
            },
            127,
        )
    except Exception as exc:
        emit_manifest(
            {
                "status": "error",
                "error": f"run_crawl.py failed before producing MediaCrawler output: {type(exc).__name__}: {exc}",
            },
            1,
        )
