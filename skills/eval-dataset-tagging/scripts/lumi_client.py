"""Lumi 平台集成：Trace 上报、Dataset 读写、打标结果回写

参考 eval_general.py 的 Lumi 集成模式：
  - SDK 优先（Lumi → Langfuse fallback）用于 Dataset 操作
  - REST API 用于 Trace ingestion（兼容旧版 SDK）
  - 三阶段 Dataset Run 绑定（评测完→等待→批量绑定）
"""

import os
import logging
import time
import uuid
import base64
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

DEFAULT_LUMI_CONFIG = {
    "secret_key": "sk-lf-24f65774-ec0c-4490-bd7f-6cf9635f1d4e",
    "public_key": "pk-lf-ae40d3e8-0b00-4412-9734-c90b2cd77e49",
    "base_url": "https://elliptic-implicit-tummy.ngrok-free.dev/siflow/auriga/vscs/skyinfer/xyli05/lumi/proxy/3000",
}


class LumiClient:
    """Lumi 平台封装 — 对齐 eval_general.py 的完整 Lumi 集成模式

    功能：
    1. Dataset CRUD（SDK 方式）：创建 dataset / 上传 items / 读取 items
    2. Trace 上报（SDK + REST 双通道）
    3. 打标结果回写：将 label 写入 dataset item 的 metadata
    4. Dataset Run 绑定：trace → dataset item → experiment run
    """

    def __init__(self, public_key: Optional[str] = None, secret_key: Optional[str] = None,
                 base_url: Optional[str] = None, enabled: bool = True):
        self.public_key = public_key or os.getenv("LUMI_PUBLIC_KEY", DEFAULT_LUMI_CONFIG["public_key"])
        self.secret_key = secret_key or os.getenv("LUMI_SECRET_KEY", DEFAULT_LUMI_CONFIG["secret_key"])
        self.base_url = base_url or os.getenv("LUMI_HOST", DEFAULT_LUMI_CONFIG["base_url"])
        self.enabled = enabled
        self._sdk_client = None
        self._rest_session = None
        self._pending_links: List[Dict] = []

        if self.enabled:
            self._init_sdk()
            self._init_rest()

    # ── 初始化 ──

    def _init_sdk(self):
        """SDK 初始化：Lumi → Langfuse fallback（对齐 eval_general.py）"""
        os.environ["LUMI_PUBLIC_KEY"] = self.public_key
        os.environ["LUMI_SECRET_KEY"] = self.secret_key
        os.environ["LUMI_HOST"] = self.base_url
        os.environ["LANGFUSE_PUBLIC_KEY"] = self.public_key
        os.environ["LANGFUSE_SECRET_KEY"] = self.secret_key
        os.environ["LANGFUSE_HOST"] = self.base_url

        try:
            from lumi import Lumi
            self._sdk_client = Lumi(public_key=self.public_key, secret_key=self.secret_key,
                                    host=self.base_url, timeout=120, max_retries=3)
            logger.info(f"✅ Lumi SDK 初始化成功 (type={type(self._sdk_client).__name__})")
            return
        except Exception as e:
            logger.warning(f"lumi.Lumi 初始化失败: {e}, 尝试 langfuse")

        try:
            from langfuse import Langfuse
            self._sdk_client = Langfuse(public_key=self.public_key, secret_key=self.secret_key,
                                        host=self.base_url, timeout=120)
            logger.info(f"✅ Langfuse SDK 初始化成功 (type={type(self._sdk_client).__name__})")
        except Exception as e:
            logger.warning(f"⚠️ SDK 初始化失败: {e}，Lumi 功能关闭")
            self.enabled = False

    def _init_rest(self):
        """REST API session 初始化（对齐 eval_general.py 的 _lumi_session）"""
        try:
            import requests
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            auth = base64.b64encode(f"{self.public_key}:{self.secret_key}".encode()).decode()
            self._rest_session = requests.Session()
            self._rest_session.verify = False
            self._rest_session.headers.update({
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/json",
            })
            # 配置重试
            from requests.adapters import HTTPAdapter
            adapter = HTTPAdapter(max_retries=urllib3.util.Retry(
                total=3, backoff_factor=2, status_forcelist=[502, 503, 504],
                allowed_methods=["GET", "POST"],
            ))
            self._rest_session.mount("https://", adapter)
            self._rest_session.mount("http://", adapter)
        except Exception as e:
            logger.warning(f"REST session 初始化失败: {e}")
            self._rest_session = None

    # ── Trace 上报 ──

    def trace(self, name: str, session_id: str = "", input: dict = None,
              metadata: dict = None, tags: list = None):
        if not self.enabled or not self._sdk_client:
            return _DummyTrace()
        try:
            return self._sdk_client.trace(name=name, session_id=session_id,
                                          input=input or {}, metadata=metadata or {}, tags=tags or [])
        except Exception as e:
            logger.warning(f"创建 Trace 失败: {e}")
            return _DummyTrace()

    def flush(self):
        if self._sdk_client and hasattr(self._sdk_client, 'flush'):
            try:
                self._sdk_client.flush()
            except Exception:
                pass

    # ── REST API 辅助 ──

    def _rest_post(self, url: str, payload: dict, max_retries: int = 3):
        """带重试的 REST POST（对齐 eval_general.py 的 _lumi_post）"""
        if not self._rest_session:
            return None
        for attempt in range(max_retries):
            try:
                resp = self._rest_session.post(url, json=payload, timeout=60)
                return resp
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(3 * (attempt + 1))
                else:
                    logger.warning(f"REST POST 失败({max_retries}次): {str(e)[:150]}")
                    return None

    def _rest_get(self, url: str, max_retries: int = 3):
        if not self._rest_session:
            return None
        for attempt in range(max_retries):
            try:
                return self._rest_session.get(url, timeout=30)
            except Exception:
                if attempt < max_retries - 1:
                    time.sleep(3 * (attempt + 1))
        return None

    # ── Dataset CRUD ──

    def ensure_dataset(self, dataset_name: str, description: str = "", metadata: dict = None) -> bool:
        """确保 Dataset 存在（对齐 eval_general.py 的 ensure_lumi_dataset）"""
        if not self.enabled or not self._sdk_client:
            return False
        try:
            self._sdk_client.get_dataset(dataset_name)
            logger.info(f"✅ Lumi Dataset 已存在: {dataset_name}")
            return True
        except Exception:
            pass
        try:
            self._sdk_client.create_dataset(
                name=dataset_name,
                description=description or f"评测集打标数据集: {dataset_name}",
                metadata=metadata or {"source": "eval-dataset-tagging"},
            )
            logger.info(f"✅ Lumi Dataset 已创建: {dataset_name}")
            return True
        except Exception as e:
            logger.warning(f"Dataset 创建失败: {e}")
            return False

    def upload_dataset_items(self, records: List[Dict], dataset_name: str,
                             id_field: str = "id") -> Dict[str, str]:
        """将本地记录上传为 Dataset Items（对齐 eval_general.py 的 upload_dataset_items）

        Args:
            records: 本地数据记录
            dataset_name: 目标 Dataset 名称
            id_field: 用作 item_id 的字段名

        Returns:
            {local_id: lumi_item_id} 映射
        """
        if not self.enabled or not self._sdk_client:
            return {}
        if not self.ensure_dataset(dataset_name):
            return {}

        item_map = {}
        uploaded, skipped = 0, 0

        for record in records:
            item_id = str(record.get(id_field, "") or record.get("item_id", "") or record.get("answer_id", ""))
            if not item_id:
                item_id = str(uuid.uuid4())[:12]

            # 提取 input/expected_output
            question = (record.get("题目", "") or record.get("input", "")
                        or record.get("question", ""))[:3000]
            expected = (record.get("参考答案", "") or record.get("expected_output", ""))[:3000]

            try:
                self._sdk_client.create_dataset_item(
                    dataset_name=dataset_name,
                    id=item_id,
                    input={"question": question, "raw_fields": {k: str(v)[:500] for k, v in record.items()
                                                                 if k in ("题目", "参考答案", "实际回答", "Accuracy",
                                                                          "norm_model_name", "norm_task_from_filename")}},
                    expected_output={"reference": expected},
                    metadata={k: str(v)[:200] for k, v in record.items()
                              if k in ("norm_model_name", "norm_task_from_filename", "norm_scene_from_filename",
                                       "pool_name", "Accuracy")},
                )
                uploaded += 1
            except Exception:
                skipped += 1
            item_map[item_id] = item_id

        try:
            self._sdk_client.flush()
        except Exception:
            pass

        logger.info(f"📤 Dataset Items: 上传={uploaded}, 跳过={skipped}, 总计={len(item_map)}")
        return item_map

    def fetch_dataset_items(self, dataset_name: str, max_total: int = 2000) -> List[Any]:
        """从 Lumi 拉取 Dataset Items"""
        if not self.enabled or not self._sdk_client:
            return []
        page, all_items = 1, []
        try:
            api = getattr(self._sdk_client, 'api', None) or getattr(self._sdk_client, 'client', None)
            if not api or not hasattr(api, 'dataset_items'):
                # fallback: 直接用 SDK 的 get_dataset
                try:
                    ds = self._sdk_client.get_dataset(dataset_name)
                    return ds.items if hasattr(ds, 'items') else []
                except Exception:
                    return []
            while len(all_items) < max_total:
                response = api.dataset_items.list(
                    dataset_name=dataset_name, page=page,
                    limit=min(100, max_total - len(all_items)))
                items = response.data
                if not items:
                    break
                all_items.extend(items)
                if response.meta.page >= response.meta.total_pages:
                    break
                page += 1
        except Exception as e:
            logger.warning(f"拉取 Dataset Items 中断: {e}")
        return all_items

    def dataset_items_to_records(self, items: List[Any]) -> List[Dict[str, Any]]:
        """将 Lumi Dataset Items 转为打标器可用的 record 格式"""
        records = []
        for item in items:
            inp = item.input or {} if hasattr(item, 'input') else {}
            exp = item.expected_output or {} if hasattr(item, 'expected_output') else {}
            meta = item.metadata or {} if hasattr(item, 'metadata') else {}
            item_id = item.id if hasattr(item, 'id') else str(uuid.uuid4())[:12]

            record = {
                "id": item_id,
                "input": inp.get("question", "") or inp.get("prompt", "") or inp.get("news_title", "") or str(inp),
                "output": "",
                "reasoning": "",
                "expected_output": exp.get("reference", "") or str(exp) if exp else "",
            }
            # 展开 raw_fields（如果有）
            raw = inp.get("raw_fields", {})
            if raw:
                for k, v in raw.items():
                    if k not in record:
                        record[k] = v
            # 展开 metadata
            for k, v in meta.items():
                record[f"meta_{k}"] = v

            records.append(record)
        return records

    # ── 打标结果回写 ──

    def write_back_labels(self, records: List[Dict], dataset_name: str, id_field: str = "id",
                          label_fields: List[str] = None):
        """将打标结果回写到 Lumi Dataset Items 的 metadata 中

        这是打标 skill 与 Lumi 联动的核心：打完标后，把 label 字段写回 item，
        使得 Lumi 平台上可以直接看到每条数据的标签。

        Args:
            records: 打标完成的记录（含 label_ 字段）
            dataset_name: Lumi Dataset 名称
            id_field: 记录中的 ID 字段名
            label_fields: 要回写的标签字段列表，默认自动提取 label_ 开头的字段
        """
        if not self.enabled or not self._sdk_client:
            logger.warning("Lumi 不可用，跳过标签回写")
            return 0

        if not label_fields:
            # 自动提取 label_ 开头的字段
            if records:
                label_fields = [k for k in records[0].keys()
                                if k.startswith("label_") or k in ("label_status", "labeled_at")]

        success, failed = 0, 0
        client = getattr(self._sdk_client, 'client', self._sdk_client)

        for record in records:
            item_id = str(record.get(id_field, "") or record.get("item_id", "") or record.get("answer_id", ""))
            if not item_id or record.get("label_status") != "done":
                continue

            labels_meta = {k: str(record.get(k, ""))[:500] for k in label_fields if record.get(k)}

            # 方式1: SDK update_dataset_item（如果有）
            try:
                if hasattr(client, 'dataset_items') and hasattr(client.dataset_items, 'update'):
                    client.dataset_items.update(
                        id=item_id,
                        metadata=labels_meta,
                    )
                    success += 1
                    continue
            except Exception:
                pass

            # 方式2: REST API fallback
            try:
                resp = self._rest_post(
                    f"{self.base_url}/api/public/dataset-items/{item_id}",
                    {"metadata": labels_meta},
                )
                if resp and resp.status_code in (200, 201):
                    success += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

        try:
            self._sdk_client.flush()
        except Exception:
            pass

        logger.info(f"📝 标签回写: 成功={success}, 失败={failed}")
        return success

    # ── Dataset Run 绑定 ──

    def link_trace_to_dataset(self, trace_id: str, run_name: str, dataset_item_id: str):
        """收集待绑定信息（对齐 eval_general.py 的 _pending_links 模式）"""
        self._pending_links.append({
            "item_id": dataset_item_id,
            "run_name": run_name,
            "trace_id": trace_id,
        })

    def flush_pending_links(self):
        """三阶段绑定（对齐 eval_general.py）：flush SDK → 等待 → 批量绑定"""
        if not self.enabled or not self._pending_links:
            return 0, 0

        # 阶段1: flush SDK 确保 trace 落库
        self.flush()

        # 阶段2: 等待服务器处理
        total = len(self._pending_links)
        if total > 0:
            wait = min(10, max(3, total // 10))
            logger.info(f"💤 等待 {wait}s 确保 Lumi 数据一致性...")
            time.sleep(wait)

        # 阶段3: 批量绑定
        success = 0
        client = getattr(self._sdk_client, 'client', self._sdk_client)
        for link in self._pending_links:
            try:
                client.dataset_run_items.create(request={
                    "datasetItemId": link["item_id"],
                    "runName": link["run_name"],
                    "traceId": link["trace_id"],
                })
                success += 1
            except Exception as e:
                # REST fallback（对齐 eval_general.py 的 _lumi_link_trace_to_dataset）
                try:
                    resp = self._rest_post(
                        f"{self.base_url}/api/public/dataset-run-items",
                        {"datasetItemId": link["item_id"], "runName": link["run_name"],
                         "traceId": link["trace_id"]},
                    )
                    if resp and resp.status_code in (200, 201):
                        success += 1
                except Exception:
                    pass

        self._pending_links.clear()
        logger.info(f"🔗 Dataset Run 绑定: {success}/{total}")
        return success, total

    # ── 打标结果上报为 Score ──

    def report_tagging_scores(self, trace, labels: Dict[str, Any]):
        """将打标结果作为 Score 上报到 Trace（对齐 eval_general.py 的 trace.score 模式）"""
        if not trace or isinstance(trace, _DummyTrace):
            return
        try:
            score_val = labels.get("label_score", 0)
            if isinstance(score_val, str):
                score_val = float(score_val) if score_val else 0
            trace.score(name="label_score", value=float(score_val) / 4.0,
                        comment=f"RVEC评分={score_val}/4, severity={labels.get('label_severity', 'NONE')}")
            trace.score(name="tagging_status", value=1.0 if labels.get("label_status") == "done" else 0.0,
                        comment=f"rvec_primary={labels.get('label_rvec_primary', 'NONE')}")
        except Exception as e:
            logger.warning(f"Score 上报失败: {e}")


class _DummyTrace:
    id = "dummy"
    def generation(self, *a, **kw): return _DummyGeneration()
    def score(self, *a, **kw): pass
    def update(self, *a, **kw): pass


class _DummyGeneration:
    def end(self, *a, **kw): pass
