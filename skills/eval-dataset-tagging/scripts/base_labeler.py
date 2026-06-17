"""打标器基类：批量处理 / 并发 / 重试 / 断点恢复 / 实时进度条 / Lumi Trace"""

import logging
import time
import random
import threading
import concurrent.futures
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from datetime import datetime

from tqdm import tqdm

from utils import now_iso
from llm_client import LLMClient

logger = logging.getLogger(__name__)


class ProgressTracker:
    """线程安全的进度追踪器，支持实时进度条输出"""

    def __init__(self, total: int, label: str = "打标"):
        self.total = total
        self.label = label
        self.done = 0
        self.success = 0
        self.failed = 0
        self.start_time = time.time()
        self._lock = threading.Lock()
        self._bar = tqdm(
            total=total, desc=f"⚡ {label}",
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}",
        )
        self._bar.set_postfix_str("✅0 ❌0 | ...")

    def update(self, is_success: bool):
        with self._lock:
            self.done += 1
            if is_success:
                self.success += 1
            else:
                self.failed += 1

            elapsed = time.time() - self.start_time
            speed = self.done / max(elapsed, 0.01)
            remaining = (self.total - self.done) / max(speed, 0.01)

            if remaining < 60:
                eta_str = f"{remaining:.0f}s"
            else:
                eta_str = f"{remaining / 60:.1f}min"

            self._bar.set_postfix_str(
                f"✅{self.success} ❌{self.failed} | {speed:.1f}条/s | 剩余{eta_str}"
            )
            self._bar.update(1)

    def close(self):
        self._bar.close()
        elapsed = time.time() - self.start_time
        print(f"\n⏱️  总耗时: {elapsed:.1f}s | 平均: {elapsed / max(self.total, 1):.2f}s/条")


class BaseLabeler(ABC):
    """打标器基类"""

    def __init__(self, llm: LLMClient, retry_limit: int = 3, max_workers: int = 5,
                 lumi_client=None, dataset_name: str = "", run_name: str = ""):
        self.llm = llm
        self.retry_limit = retry_limit
        self.max_workers = max_workers
        self.lumi = lumi_client
        self.dataset_name = dataset_name
        self.run_name = run_name or f"tagging_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def run(self, records: List[Dict[str, Any]], done_ids: set = None) -> tuple:
        """批量打标主流程"""
        done_ids = done_ids or set()
        todo, already_done = [], []

        for record in records:
            rid = record.get("id", record.get("item_id", ""))
            if rid in done_ids:
                already_done.append(record)
            else:
                todo.append(record)

        if not todo:
            return already_done, []

        logger.info(f"⚡ {self.label_name} | 待处理: {len(todo)} | 并发: {self.max_workers}")

        success = list(already_done)
        failed = []

        # 第一轮
        if self.max_workers > 1:
            self._run_concurrent(todo, success, failed, len(todo))
        else:
            self._run_sequential(todo, success, failed, len(todo))

        # 统一重试
        for attempt in range(1, self.retry_limit + 1):
            if not failed:
                break
            logger.info(f"第 {attempt}/{self.retry_limit} 次重试，剩余失败: {len(failed)}")
            still_failed = []
            for record in tqdm(failed, desc=f"重试 {attempt}"):
                result = self._try_label_with_trace(record, 0, 0)
                if result is not None:
                    success.append(result)
                else:
                    still_failed.append(record)
            failed = still_failed

        for record in failed:
            record["label_status"] = "failed"
            record["labeled_at"] = now_iso()

        if self.lumi:
            s, t = self.lumi.flush_pending_links()
            if t > 0:
                logger.info(f"🔗 绑定完成: {s}/{t}")

        logger.info(f"✅ {self.label_name} 完成: 成功 {len(success)}, 失败 {len(failed)}")
        return success, failed

    def _run_concurrent(self, records, success, failed, total):
        tracker = ProgressTracker(total, self.label_name)
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for idx, record in enumerate(records):
                future = executor.submit(self._try_label_with_trace, record, idx + 1, total)
                futures[future] = record
            for future in concurrent.futures.as_completed(futures):
                record = futures[future]
                try:
                    result = future.result()
                    if result is not None:
                        success.append(result)
                        tracker.update(is_success=True)
                    else:
                        failed.append(record)
                        tracker.update(is_success=False)
                except Exception as e:
                    logger.warning(f"并发任务异常: {e}")
                    failed.append(record)
                    tracker.update(is_success=False)
        tracker.close()

    def _run_sequential(self, records, success, failed, total):
        for idx, record in enumerate(tqdm(records, desc=self.label_name)):
            result = self._try_label_with_trace(record, idx + 1, total)
            if result is not None:
                success.append(result)
            else:
                failed.append(record)

    def _try_label_with_trace(self, record: Dict[str, Any], idx: int, total: int) -> Optional[Dict[str, Any]]:
        time.sleep(random.uniform(0.1, 0.5))
        rid = record.get("id", record.get("item_id", ""))
        trace, gen = None, None

        if self.lumi and self.lumi.enabled:
            trace = self.lumi.trace(
                name=f"Tagging_{self.label_name}", session_id=self.run_name,
                input={"item_id": rid, "input": str(record.get("input", ""))[:500], "mode": self.label_name},
                metadata={"run_name": self.run_name, "index": idx}, tags=[self.label_name, self.run_name])
            gen = trace.generation(name=f"labeling_{self.label_name}", model=self.llm.model, input={"item_id": rid})

        try:
            start = time.time()
            result = self.label_one(record)
            result["label_status"] = "done"
            result["labeled_at"] = now_iso()
            latency = round(time.time() - start, 2)

            if trace:
                try:
                    gen.end(output={"status": "done", "latency": latency})
                    trace.score(name="label_status", value=1.0, comment="done")
                    trace.update(output={"status": "done", "latency": latency})
                    # 上报打标分数到 Lumi（对齐 eval_general.py 的 trace.score 模式）
                    self.lumi.report_tagging_scores(trace, result)
                except Exception:
                    pass
                if rid and self.dataset_name:
                    self.lumi.link_trace_to_dataset(trace.id, self.run_name, rid)

            if idx > 0 and total > 0:
                logger.info(f"[{idx}/{total}] ✅ id={rid} | {latency}s")
            return result
        except Exception as e:
            logger.warning(f"打标失败 id={rid}: {e}")
            if trace:
                try:
                    gen.end(output={"error": str(e)[:200]})
                    trace.update(output={"error": str(e)[:200]})
                except Exception:
                    pass
            return None

    @property
    @abstractmethod
    def label_name(self) -> str: ...

    @abstractmethod
    def label_one(self, record: Dict[str, Any]) -> Dict[str, Any]: ...
