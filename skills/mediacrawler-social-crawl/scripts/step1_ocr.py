"""
step1_ocr.py — 小红书图文帖图片 OCR 抽取（高吞吐版）

读取 MediaCrawler 落地的 contents 文件（.csv / .jsonl，含 image_list / type 字段），
按 note_id 下载 → PIL 缩图 → rapidocr 识别 → 合并，产出 ocr_<input_stem>.jsonl，
每行：

    {"note_id": "...", "image_ocr_line": "<图1文字> | <图2文字>"}

下游脚本（如 step1_preprocess.py / 自定义 finalize_csv.py）用 image_ocr_line
即可拿到该 note 全部图片的合并 OCR 文本。

设计要点
---------
- **多进程并行**：`--workers N`（默认 8）。每个 worker 独立持有一份 RapidOCR
  实例（initializer 中 lazy-init），不共享。1 = 串行兜底。
- **PIL 预缩图**：默认把图片长边压到 `--resize-long-edge 1280`px 再喂 OCR，
  可以直接砍掉 50%+ 的 det/rec 时间。设 0 关闭缩图。
- **图片采样**：`--max-images-per-note`（默认 8）只对每条 note 的前 N 张图做
  OCR——前 6-8 张通常承载图文帖 80%+ 的信息密度，再多边际效益低。
- **per-image 超时**：`--per-image-timeout`（默认 60s），下载 + 缩图 + OCR
  整体超时；任一阶段卡死该图被跳过，不影响其它图。
- **URL 缓存**：相同图片 URL 落到 `<cache_dir>/<md5>.bin`，多次 run 共享，
  跨 keyword 也省下载时间。
- **断点续跑**：输出 jsonl 已存在的 note_id 直接跳过，可以反复重跑。
- **跳过 type=video**：视频帖图片对图文打标无意义，--include-video 可关闭跳过。
- **后台模式**：`--background` fork 出子进程，前台立即返回 task_id /
  log_path / status_path，调用者只查 status.json 看进度，不再被
  caller 端的 timeout 卡死。
- **进程锁**：每个输出 jsonl 上 fcntl flock，防止两次 run 互写打架。
- **status.json**：实时刷新 processed / total / current_note / ocr_total_images
  / ocr_failed_images / elapsed_sec / state，便于调度器/Agent 查进度。

CLI 兼容
---------
旧脚本的 `--input / --output / --cache-dir / --include-video` 完全保留；新增
参数都有合理默认值，不传也能跑（行为 = 默认 8 worker + 1280 缩图 + 8 图采样）。
"""

from __future__ import annotations

import argparse
import csv
import errno
import fcntl
import hashlib
import io
import json
import multiprocessing as mp
import os
import signal
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

try:
    import urllib.request
except ImportError:  # pragma: no cover
    urllib = None  # type: ignore


# ── 默认值 ────────────────────────────────────────────────────────────────────

DEFAULT_WORKERS = 8                # 默认 8 worker，机器是 128 vCPU 占 25%
DEFAULT_MAX_IMAGES_PER_NOTE = 8    # 每帖前 8 张图，承载 80%+ 信息
DEFAULT_PER_IMAGE_TIMEOUT = 60     # 下载 + 缩图 + OCR 总超时
DEFAULT_RESIZE_LONG_EDGE = 1280    # 长边 1280，0 = 不缩
MAX_IMG_BYTES = 12 * 1024 * 1024   # 单图最大 12MB

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
OCR_JOIN_SEP = " | "               # 多图 OCR 拼接分隔符
HEARTBEAT_SEC = 5                  # status.json 最低刷新间隔


# ── OCR engine（每 worker 进程持有一份）──────────────────────────────────────

# worker 进程局部变量；主进程也可能 lazy 用（workers=1 时）
_OCR_ENGINE = None


def init_worker() -> None:
    """multiprocessing initializer：在 worker 启动时预热一次 RapidOCR。

    放到这里而不是任务函数里 lazy init，是为了让首个任务就快——否则
    每个 worker 第一条任务都会被 ~3-5s 的模型加载拖慢，吞吐曲线不平滑。
    """
    _ensure_ocr_engine()


def _ensure_ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "缺少 rapidocr-onnxruntime。请先安装：\n"
            "    pip install rapidocr-onnxruntime\n"
            f"原始错误：{exc}"
        ) from exc
    _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


# ── IO 工具 ───────────────────────────────────────────────────────────────────

def iter_rows(path: Path) -> Iterator[dict]:
    """按后缀自动选择 CSV / JSONL 读取，统一返回 dict 迭代器。"""
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    elif suffix in (".csv", ".tsv"):
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                yield row
    else:
        raise ValueError(f"Unsupported input suffix: {path.suffix} ({path})")


def url_to_cache_path(cache_dir: Path, url: str) -> Path:
    """URL md5 作为缓存文件名，避免 URL 含查询参数导致文件系统不友好。"""
    h = hashlib.md5(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{h}.bin"


def download_image(url: str, cache_path: Path, timeout: float) -> bytes | None:
    """下载图片到缓存路径。已存在则直接读，失败 / 超大返回 None。"""
    if cache_path.exists() and cache_path.stat().st_size > 0:
        try:
            return cache_path.read_bytes()
        except OSError:
            pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(MAX_IMG_BYTES + 1)
        if len(data) > MAX_IMG_BYTES or len(data) == 0:
            return None
        try:
            cache_path.write_bytes(data)
        except OSError:
            pass  # 缓存写失败不阻断 OCR
        return data
    except Exception:
        return None


def resize_image_bytes(img_bytes: bytes, long_edge: int) -> bytes:
    """用 Pillow 把图片长边压到 long_edge 像素（0 = 不缩）。
    解码失败时原样返回，OCR 引擎会再失败一次（不阻断流程）。
    """
    if long_edge <= 0 or not img_bytes:
        return img_bytes
    try:
        from PIL import Image  # 局部 import，避免主进程未装 Pillow 也能 --help
    except ImportError:
        return img_bytes
    try:
        with Image.open(io.BytesIO(img_bytes)) as im:
            im.load()
            w, h = im.size
            cur_long = max(w, h)
            if cur_long <= long_edge:
                return img_bytes  # 已经够小，不重编码省时间
            # 计算等比缩放尺寸
            if w >= h:
                new_w = long_edge
                new_h = max(1, int(h * long_edge / w))
            else:
                new_h = long_edge
                new_w = max(1, int(w * long_edge / h))
            im_resized = im.convert("RGB").resize((new_w, new_h), Image.LANCZOS)
            buf = io.BytesIO()
            im_resized.save(buf, format="JPEG", quality=85, optimize=False)
            return buf.getvalue()
    except Exception:
        return img_bytes


def ocr_bytes(img_bytes: bytes) -> str:
    """对图片字节做 OCR，返回单行字符串（rapidocr 多行用空格拼）。"""
    if not img_bytes:
        return ""
    ocr = _ensure_ocr_engine()
    try:
        result, _ = ocr(img_bytes)
    except Exception:
        return ""
    if not result:
        return ""
    lines: list[str] = []
    for item in result:
        # 兼容两种返回格式：[bbox, text, conf] / dict
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            text = str(item[1] or "").strip()
        elif isinstance(item, dict):
            text = str(item.get("text") or "").strip()
        else:
            continue
        if text:
            lines.append(text)
    return " ".join(lines).strip()


# ── worker 任务 ───────────────────────────────────────────────────────────────

def _ocr_one_image(
    url: str,
    cache_dir: Path,
    per_image_timeout: float,
    resize_long_edge: int,
) -> tuple[str, float]:
    """单图 OCR 全流程（下载 → 缩图 → OCR），返回 (text, elapsed_sec)。"""
    started = time.monotonic()
    cache_path = url_to_cache_path(cache_dir, url)

    def _work() -> str:
        data = download_image(url, cache_path, timeout=per_image_timeout)
        if not data:
            return ""
        small = resize_image_bytes(data, resize_long_edge)
        return ocr_bytes(small)

    # 在 worker 进程内再开 1 个线程跑实际工作，给整图 timeout 兜底
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_work)
        try:
            text = fut.result(timeout=per_image_timeout)
        except FutTimeout:
            text = ""
        except Exception:
            text = ""
    return text, time.monotonic() - started


def ocr_one_note(task: dict) -> dict:
    """worker 进程的任务入口。task 形如：
        {
            "note_id": "...",
            "urls": ["url1", "url2", ...],   # 已截到 max_images_per_note
            "cache_dir": "...",
            "per_image_timeout": 60,
            "resize_long_edge": 1280,
        }
    返回：
        {
            "note_id": "...",
            "image_ocr_line": "<图1> | <图2> ...",
            "ocr_total_images": N,
            "ocr_failed_images": M,
            "per_image_log": ["1/N ok 67c 6.2s", ...],   # 给主进程拼日志
            "note_elapsed_sec": float,
        }
    """
    note_id = task["note_id"]
    urls = task["urls"]
    cache_dir = Path(task["cache_dir"])
    per_image_timeout = float(task["per_image_timeout"])
    resize_long_edge = int(task["resize_long_edge"])

    cache_dir.mkdir(parents=True, exist_ok=True)
    note_started = time.monotonic()

    per_img_texts: list[str] = []
    per_image_log: list[str] = []
    failed = 0

    for i, url in enumerate(urls, 1):
        text, elapsed = _ocr_one_image(url, cache_dir, per_image_timeout, resize_long_edge)
        if text:
            per_img_texts.append(text)
        else:
            failed += 1
        per_image_log.append(
            f"img {i}/{len(urls)} ocr={'ok' if text else 'fail/empty'} "
            f"chars={len(text)} t={elapsed:.1f}s"
        )

    return {
        "note_id": note_id,
        "image_ocr_line": OCR_JOIN_SEP.join(per_img_texts),
        "ocr_total_images": len(urls),
        "ocr_failed_images": failed,
        "per_image_log": per_image_log,
        "note_elapsed_sec": time.monotonic() - note_started,
    }


# ── status.json / 进程锁 / background 分叉 ───────────────────────────────────

def write_status(status_path: Path, state: dict[str, Any]) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["last_heartbeat"] = datetime.now().isoformat(timespec="seconds")
    tmp = status_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(status_path)


def acquire_output_lock(out_jsonl: Path):
    """对输出 jsonl 上 fcntl flock，防止两次 run 互写。
    返回 (file_handle, lock_path) 或 (None, lock_path)+held_by。"""
    lock_path = out_jsonl.with_suffix(out_jsonl.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno not in (errno.EAGAIN, errno.EACCES):
            fh.close()
            raise
        held_by: dict[str, Any] | None = None
        try:
            fh.seek(0)
            content = fh.read().strip()
            held_by = json.loads(content) if content else None
        except Exception:
            held_by = None
        stale = False
        pid = held_by.get("pid") if isinstance(held_by, dict) else None
        if isinstance(pid, int):
            proc_state_path = Path('/proc') / str(pid) / 'stat'
            try:
                stat_text = proc_state_path.read_text(encoding='utf-8', errors='ignore')
                proc_state = stat_text.split()[2] if len(stat_text.split()) >= 3 else ''
                if proc_state == 'Z':
                    stale = True
            except OSError:
                stale = True
            if not stale:
                try:
                    os.kill(pid, 0)
                except OSError:
                    stale = True
        if stale:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                fh.seek(0)
                fh.truncate()
                holder = {
                    "pid": os.getpid(),
                    "started_at": datetime.now().isoformat(timespec="seconds"),
                    "out_jsonl": str(out_jsonl),
                    "recovered_from_stale_lock": pid,
                }
                fh.write(json.dumps(holder, ensure_ascii=False, indent=2))
                fh.flush()
                return fh, lock_path, None
            except OSError:
                pass
        fh.close()
        return None, lock_path, held_by or {"info": "lock held but holder file empty"}

    fh.seek(0)
    fh.truncate()
    holder = {
        "pid": os.getpid(),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "out_jsonl": str(out_jsonl),
    }
    fh.write(json.dumps(holder, ensure_ascii=False, indent=2))
    fh.flush()
    return fh, lock_path, None


def spawn_background(orig_argv: list[str], task_dir: Path, status_path: Path) -> dict[str, Any]:
    """fork 出后台子进程，前台立即返回 task 信息。"""
    task_dir.mkdir(parents=True, exist_ok=True)
    log_path = task_dir / "ocr.log"

    child_argv = [a for a in orig_argv if a != "--background"]
    child_cmd = [sys.executable, str(Path(__file__).resolve()), *child_argv,
                 "--_child", "--status-file", str(status_path)]

    write_status(status_path, {
        "state": "starting",
        "task_id": task_dir.parent.name + "/" + task_dir.name,
        "log_path": str(log_path),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "cmd": child_cmd,
    })

    log_fh = open(log_path, "a", buffering=1)
    proc = subprocess.Popen(
        child_cmd,
        stdout=log_fh, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {
        "pid": proc.pid,
        "log_path": str(log_path),
        "status_path": str(status_path),
    }


# ── 主流程 ────────────────────────────────────────────────────────────────────

def load_existing_results(out_jsonl: Path) -> set[str]:
    """断点续跑：返回已存在 note_id 的集合。"""
    seen: set[str] = set()
    if not out_jsonl.exists():
        return seen
    for row in iter_rows(out_jsonl):
        nid = str(row.get("note_id") or "").strip()
        if nid:
            seen.add(nid)
    return seen


def collect_tasks(
    input_path: Path,
    out_jsonl: Path,
    cache_dir: Path,
    skip_video: bool,
    max_images_per_note: int,
    per_image_timeout: float,
    resize_long_edge: int,
) -> tuple[list[dict], dict[str, int]]:
    """读输入，构造 worker 任务列表。"""
    existing = load_existing_results(out_jsonl)
    stats = {
        "total_input": 0,
        "skipped_video": 0,
        "skipped_existing": 0,
        "skipped_no_image": 0,
        "queued": 0,
    }
    tasks: list[dict] = []
    no_image_records: list[dict] = []  # 没图但要写空记录的 note

    for row in iter_rows(input_path):
        stats["total_input"] += 1
        note_id = str(row.get("note_id") or "").strip()
        if not note_id:
            continue
        if skip_video and str(row.get("type") or "").strip() == "video":
            stats["skipped_video"] += 1
            continue
        if note_id in existing:
            stats["skipped_existing"] += 1
            continue
        image_list_str = str(row.get("image_list") or "").strip()
        urls = [u.strip() for u in image_list_str.split(",") if u.strip()]
        if not urls:
            stats["skipped_no_image"] += 1
            no_image_records.append({"note_id": note_id, "image_ocr_line": ""})
            continue
        if max_images_per_note > 0:
            urls = urls[:max_images_per_note]
        tasks.append({
            "note_id": note_id,
            "urls": urls,
            "cache_dir": str(cache_dir),
            "per_image_timeout": per_image_timeout,
            "resize_long_edge": resize_long_edge,
        })
        stats["queued"] += 1

    # 把没图的 note 直接补到 jsonl（不进 worker，省一次进程通信）
    if no_image_records:
        out_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with open(out_jsonl, "a", encoding="utf-8") as fout:
            for rec in no_image_records:
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")

    return tasks, stats


def run_ocr_pipeline(
    tasks: list[dict],
    out_jsonl: Path,
    workers: int,
    status_path: Path,
    status_state: dict[str, Any],
    t0: float,
) -> dict[str, int]:
    """跑 multiprocessing.Pool，边收边写 jsonl，边刷 status.json。"""
    runtime = {
        "processed": 0,
        "ocr_total_images": 0,
        "ocr_failed_images": 0,
    }
    last_heartbeat = 0.0

    def _flush_status(force: bool = False) -> None:
        nonlocal last_heartbeat
        now = time.monotonic()
        if not force and now - last_heartbeat < HEARTBEAT_SEC:
            return
        last_heartbeat = now
        status_state.update({
            "processed_notes": runtime["processed"],
            "ocr_total_images": runtime["ocr_total_images"],
            "ocr_failed_images": runtime["ocr_failed_images"],
            "elapsed_sec": round(time.monotonic() - t0, 1),
        })
        write_status(status_path, status_state)

    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with open(out_jsonl, "a", encoding="utf-8") as fout:
        if workers <= 1:
            # 串行兜底：不开 Pool，避免 fork/IPC 开销
            init_worker()
            for task in tasks:
                status_state["current_note_id"] = task["note_id"]
                _flush_status()
                result = ocr_one_note(task)
                _consume_result(result, fout, runtime)
                _flush_status()
        else:
            ctx = mp.get_context("fork")
            with ctx.Pool(processes=workers, initializer=init_worker) as pool:
                # imap_unordered 提高吞吐：谁先完成谁先返回，主进程不被慢任务阻塞
                for result in pool.imap_unordered(ocr_one_note, tasks, chunksize=1):
                    status_state["current_note_id"] = result["note_id"]
                    _consume_result(result, fout, runtime)
                    _flush_status()
            _flush_status(force=True)

    return runtime


def _consume_result(result: dict, fout, runtime: dict) -> None:
    """处理一个 worker 返回结果：打日志 + 写 jsonl + 累加 stats。"""
    note_id = result["note_id"]
    print(f"[step1_ocr] note_id={note_id} images={result['ocr_total_images']} "
          f"failed={result['ocr_failed_images']} t={result['note_elapsed_sec']:.1f}s",
          flush=True)
    for line in result.get("per_image_log", []):
        print(f"  {line}", flush=True)
    fout.write(json.dumps(
        {"note_id": note_id, "image_ocr_line": result["image_ocr_line"]},
        ensure_ascii=False,
    ) + "\n")
    fout.flush()
    runtime["processed"] += 1
    runtime["ocr_total_images"] += result["ocr_total_images"]
    runtime["ocr_failed_images"] += result["ocr_failed_images"]


# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Step 1: 小红书图文帖 OCR 抽取（rapidocr，多进程，PIL 缩图）",
    )
    parser.add_argument(
        "--input", required=True,
        help="contents 文件路径，支持 .csv 或 .jsonl（含 note_id / image_list / type 字段）",
    )
    parser.add_argument(
        "--output", default=None,
        help="OCR 结果输出路径（.jsonl）。默认 <input_dir>/ocr_<input_stem>.jsonl",
    )
    parser.add_argument(
        "--cache-dir", default=None,
        help="图片下载缓存目录。默认 <run_output>/_ocr_cache/",
    )
    parser.add_argument(
        "--include-video", action="store_true",
        help="也对 type=video 的帖子做 OCR（默认跳过）",
    )
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS,
        help=f"并行 OCR worker 数，默认 {DEFAULT_WORKERS}。1 = 串行兜底。",
    )
    parser.add_argument(
        "--max-images-per-note", type=int, default=DEFAULT_MAX_IMAGES_PER_NOTE,
        help=f"每条 note 最多 OCR 前 N 张图，默认 {DEFAULT_MAX_IMAGES_PER_NOTE}。0 = 不限。",
    )
    parser.add_argument(
        "--per-image-timeout", type=float, default=DEFAULT_PER_IMAGE_TIMEOUT,
        help=f"单图（下载+缩图+OCR）总超时，默认 {DEFAULT_PER_IMAGE_TIMEOUT}s。",
    )
    parser.add_argument(
        "--resize-long-edge", type=int, default=DEFAULT_RESIZE_LONG_EDGE,
        help=f"OCR 前 PIL 长边缩放，默认 {DEFAULT_RESIZE_LONG_EDGE}px。0 = 不缩。",
    )
    parser.add_argument(
        "--background", action="store_true",
        help="后台运行。前台立即返回 status_path / log_path / pid 后退出，OCR 在 fork 出的子进程里跑。",
    )
    parser.add_argument(
        "--status-file", default="",
        help="状态文件路径。默认 <output_dir>/_task/ocr_status_<input_stem>.json。--background 子进程内部使用。",
    )
    parser.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)

    return parser.parse_args()


def emit_manifest(payload: dict[str, Any], exit_code: int) -> None:
    print("\n[ocr_manifest]", flush=True)
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    raise SystemExit(exit_code)


def main() -> None:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        emit_manifest({"status": "error", "error": f"--input not found: {input_path}"}, 2)

    if args.output:
        out_jsonl = Path(args.output).expanduser().resolve()
    else:
        out_jsonl = input_path.parent / f"ocr_{input_path.stem}.jsonl"

    if args.cache_dir:
        cache_dir = Path(args.cache_dir).expanduser().resolve()
    else:
        # 默认尝试 <run_output>/_ocr_cache（contents 文件通常向上 4 层是 run_output 根）。
        # 浅路径（如 /tmp/x.jsonl）parents 不够 4 层时直接退到 input 同级。
        parents = list(input_path.parents)
        if len(parents) >= 4 and str(parents[3]) not in ("/", ""):
            cache_dir = (parents[3] / "_ocr_cache").resolve()
        else:
            cache_dir = (input_path.parent / "_ocr_cache").resolve()
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 兜底：路径不可写 → 退到 input 同级
        cache_dir = (input_path.parent / "_ocr_cache").resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)

    # status / task 路径
    if args.status_file:
        status_path = Path(args.status_file).expanduser().resolve()
    else:
        status_path = out_jsonl.parent / "_task" / f"ocr_status_{input_path.stem}.json"
    task_dir = status_path.parent
    task_dir.mkdir(parents=True, exist_ok=True)

    # ── background 分叉 ──
    if args.background and not args._child:
        task_info = spawn_background(sys.argv[1:], task_dir, status_path)
        emit_manifest({
            "status": "background_started",
            "input": str(input_path),
            "output": str(out_jsonl),
            "cache_dir": str(cache_dir),
            "workers": args.workers,
            "max_images_per_note": args.max_images_per_note,
            "per_image_timeout": args.per_image_timeout,
            "resize_long_edge": args.resize_long_edge,
            **task_info,
            "hint": "查 status_path 看进度，state=done 后 ocr_*.jsonl 即为最终输出。",
        }, 0)

    # ── 进程锁 ──
    lock_handle, lock_path, held_by = acquire_output_lock(out_jsonl)
    if lock_handle is None:
        emit_manifest({
            "status": "error",
            "error": "lock_held",
            "out_jsonl": str(out_jsonl),
            "lock_path": str(lock_path),
            "held_by": held_by,
            "hint": f"另一个 OCR 任务正在写同一个文件，请等待 / 查 held_by.pid / 或确认后 rm {lock_path}",
        }, 9)

    print("=== Step 1 OCR ===", flush=True)
    print(f"  input              = {input_path}", flush=True)
    print(f"  output             = {out_jsonl}", flush=True)
    print(f"  cache_dir          = {cache_dir}", flush=True)
    print(f"  workers            = {args.workers}", flush=True)
    print(f"  max_images_per_note= {args.max_images_per_note}", flush=True)
    print(f"  per_image_timeout  = {args.per_image_timeout}s", flush=True)
    print(f"  resize_long_edge   = {args.resize_long_edge}px", flush=True)
    print(f"  skip_video         = {not args.include_video}", flush=True)
    print(f"  status_path        = {status_path}", flush=True)
    print()

    # ── 收集任务 ──
    tasks, collect_stats = collect_tasks(
        input_path=input_path,
        out_jsonl=out_jsonl,
        cache_dir=cache_dir,
        skip_video=not args.include_video,
        max_images_per_note=args.max_images_per_note,
        per_image_timeout=args.per_image_timeout,
        resize_long_edge=args.resize_long_edge,
    )

    # ── 初始化 status ──
    t0 = time.monotonic()
    status_state: dict[str, Any] = {
        "state": "running",
        "task_id": status_path.stem,
        "input_file": str(input_path),
        "output_file": str(out_jsonl),
        "cache_dir": str(cache_dir),
        "workers": args.workers,
        "resize_long_edge": args.resize_long_edge,
        "max_images_per_note": args.max_images_per_note,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "total_input": collect_stats["total_input"],
        "total_to_process": collect_stats["queued"],
        "skipped_video": collect_stats["skipped_video"],
        "skipped_existing": collect_stats["skipped_existing"],
        "skipped_no_image": collect_stats["skipped_no_image"],
        "processed_notes": 0,
        "current_note_id": None,
        "ocr_total_images": 0,
        "ocr_failed_images": 0,
        "elapsed_sec": 0.0,
    }
    write_status(status_path, status_state)

    print(f"[step1_ocr] queued={collect_stats['queued']} "
          f"skip_video={collect_stats['skipped_video']} "
          f"skip_existing={collect_stats['skipped_existing']} "
          f"skip_no_image={collect_stats['skipped_no_image']}", flush=True)

    try:
        if not tasks:
            print("[step1_ocr] 没有需要处理的 note，直接结束。", flush=True)
            runtime = {"processed": 0, "ocr_total_images": 0, "ocr_failed_images": 0}
        else:
            runtime = run_ocr_pipeline(
                tasks=tasks,
                out_jsonl=out_jsonl,
                workers=args.workers,
                status_path=status_path,
                status_state=status_state,
                t0=t0,
            )

        elapsed = time.monotonic() - t0
        summary = {
            "input_file": str(input_path),
            "output_file": str(out_jsonl),
            "cache_dir": str(cache_dir),
            "workers": args.workers,
            "elapsed_sec": round(elapsed, 1),
            "stats": {
                "total_input": collect_stats["total_input"],
                "skipped_video": collect_stats["skipped_video"],
                "skipped_existing": collect_stats["skipped_existing"],
                "skipped_no_image": collect_stats["skipped_no_image"],
                "queued": collect_stats["queued"],
                "processed": runtime["processed"],
                "ocr_total_images": runtime["ocr_total_images"],
                "ocr_failed_images": runtime["ocr_failed_images"],
            },
        }
        summary_path = out_jsonl.with_suffix(".summary.json")
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        # 终态 status
        status_state.update({
            "state": "done",
            "processed_notes": runtime["processed"],
            "ocr_total_images": runtime["ocr_total_images"],
            "ocr_failed_images": runtime["ocr_failed_images"],
            "elapsed_sec": round(elapsed, 1),
            "current_note_id": None,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "summary_path": str(summary_path),
        })
        write_status(status_path, status_state)

        print()
        print("── OCR 处理结果 ──────────────────────────────────────────────", flush=True)
        print(f"  输入条数:           {collect_stats['total_input']}", flush=True)
        print(f"  跳过(视频帖):       {collect_stats['skipped_video']}", flush=True)
        print(f"  跳过(已存在结果):   {collect_stats['skipped_existing']}", flush=True)
        print(f"  跳过(无图):         {collect_stats['skipped_no_image']}", flush=True)
        print(f"  本次处理:           {runtime['processed']}", flush=True)
        print(f"  图片总数:           {runtime['ocr_total_images']}", flush=True)
        print(f"  OCR 失败/空:        {runtime['ocr_failed_images']}", flush=True)
        print(f"  耗时:               {elapsed:.1f}s", flush=True)
        print(f"  输出文件:           {out_jsonl}", flush=True)
        print(f"  摘要文件:           {summary_path}", flush=True)
        print(f"  状态文件:           {status_path}", flush=True)

        emit_manifest({
            "status": "success",
            "summary": summary,
            "summary_path": str(summary_path),
            "status_path": str(status_path),
            "output_file": str(out_jsonl),
        }, 0)
    except Exception as exc:
        # 失败也要把 status 写成 error，便于调度器 / Agent 看到
        try:
            status_state.update({
                "state": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            })
            write_status(status_path, status_state)
        except Exception:
            pass
        emit_manifest({
            "status": "error",
            "error": f"step1_ocr.py: {type(exc).__name__}: {exc}",
            "status_path": str(status_path),
        }, 1)
    finally:
        try:
            if lock_handle is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                lock_handle.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        emit_manifest({"status": "error", "error": f"step1_ocr.py: {type(exc).__name__}: {exc}"}, 1)
