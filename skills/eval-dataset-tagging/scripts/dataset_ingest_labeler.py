"""评测集入库打标器（dataset_ingest 模式）

功能：对原始评测集（仅有 question / reference，无模型回答）进行：
1. 领域自动检测（金融/医学/通用）
2. 场景分类（F轴/M轴）+ 任务类型（T轴）标签
3. 结构化后上传到 Lumi Dataset

与 RVEC 打标的区别：
- 不需要 model_response（模型还没跑过）
- 不打 RVEC 标签（没有回答就没法评）
- 输出的是 "评测集元数据"，为后续 eval_skill 跑评测做准备

设计对齐 scene_labeler.py + fin_rvec_labeler.py 的字段映射。
"""

import json
import re
from typing import Dict, Any, List, Optional

from base_labeler import BaseLabeler
from llm_client import LLMClient
from field_mapping import map_field


# ── 构建分类 prompt 的 schema 文本 ──

def _build_ingest_schema_text(config: Dict, domain: str) -> str:
    """根据领域选择对应的 scene + task schema 构建 prompt"""
    lines = []

    # 场景轴
    if domain == "medical" and "med_scene_schema" in config:
        lines.append("# 场景分类（M轴：医学业务场景，选择最匹配的 1-2 个）")
        for s in config["med_scene_schema"]:
            examples = s.get("examples", "")
            suffix = f"（如：{examples}）" if examples else ""
            lines.append(f"- {s['code']} {s['name']}{suffix}")
    elif "fin_scene_schema" in config:
        lines.append("# 场景分类（F轴：金融业务场景，选择最匹配的 1-2 个）")
        for s in config["fin_scene_schema"]:
            lines.append(f"- {s['code']} {s['name']}：{s.get('description', '')}")
    else:
        lines.append("# 场景分类（通用，请用一个简短标签描述）")

    # 任务轴
    if "task_type_schema" in config:
        lines.append("\n# 任务类型（T轴，选择最匹配的 1-2 个）")
        for t in config["task_type_schema"]:
            lines.append(f"- {t['code']} {t['name']}：{t.get('description', '')}")

    return "\n".join(lines)


SYSTEM_PROMPT = """\
你是一位评测集分类专家。你需要对评测题目进行场景分类和任务类型标注，为后续模型评测做准备。

## 分类体系
{schema_text}

## 输出要求
你必须以**严格 JSON**格式输出，不要包含任何其他文字。格式如下：
{{
  "scene": "M01 内科" 或 "F04 资本市场"（选最匹配的编码+名称），
  "scene_secondary": "M05 药理学与临床药学"（可选，有明显次要场景时填），
  "task_type": "T02 疾病诊断与鉴别诊断"（选最匹配的编码+名称），
  "task_type_secondary": ""（可选），
  "difficulty": "easy" | "medium" | "hard"（基于题目复杂度判断），
  "is_multi_choice": true | false（是否选择题），
  "topic_keywords": ["关键词1", "关键词2"]（2-5个核心知识点关键词），
  "reason": "一句话分类理由"
}}
"""

USER_PROMPT = """\
请对以下评测题目进行分类：

【题目】
{question}

【参考答案】
{reference}
"""


class DatasetIngestLabeler(BaseLabeler):
    """评测集入库分类打标器"""

    def __init__(self, llm: LLMClient, config: Dict, retry_limit: int = 3,
                 max_workers: int = 5, lumi_client=None, dataset_name: str = "",
                 run_name: str = "", domain: str = "general"):
        super().__init__(llm, retry_limit, max_workers, lumi_client, dataset_name, run_name)
        self.config = config
        self.domain = domain
        schema_text = _build_ingest_schema_text(config, domain)
        self.system_prompt = SYSTEM_PROMPT.format(schema_text=schema_text)

    @property
    def label_name(self) -> str:
        return "评测集入库分类"

    def label_one(self, record: Dict[str, Any]) -> Dict[str, Any]:
        # 字段提取
        question = map_field(record, "question") or map_field(record, "context")
        reference = map_field(record, "reference")

        if not question:
            result = dict(record)
            result["ingest_error"] = "question 字段为空"
            return result

        user_msg = USER_PROMPT.format(
            question=question[:3000],
            reference=reference[:2000] if reference else "（无参考答案）",
        )

        labels = self.llm.chat_json(self.system_prompt, user_msg)

        result = dict(record)
        # 写入分类标签
        result["label_scene"] = labels.get("scene", "")
        result["label_scene_secondary"] = labels.get("scene_secondary", "")
        result["label_task_type"] = labels.get("task_type", "")
        result["label_task_type_secondary"] = labels.get("task_type_secondary", "")
        result["label_difficulty"] = labels.get("difficulty", "medium")
        result["label_is_multi_choice"] = labels.get("is_multi_choice", False)
        result["label_topic_keywords"] = labels.get("topic_keywords", [])
        result["label_ingest_reason"] = labels.get("reason", "")
        result["label_domain"] = self.domain
        return result

    def build_lumi_item(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """将分类后的记录构造为 Lumi Dataset Item 格式"""
        question = map_field(record, "question") or map_field(record, "context")
        reference = map_field(record, "reference")
        item_id = map_field(record, "id") or ""

        # 构造 input
        input_data = {"question": question}

        # 构造 expected_output
        expected = {}
        if reference:
            expected["reference"] = reference

        # 构造 metadata（分类标签 + 原始信息）
        metadata = {
            "domain": record.get("label_domain", self.domain),
            "scene": record.get("label_scene", ""),
            "scene_secondary": record.get("label_scene_secondary", ""),
            "task_type": record.get("label_task_type", ""),
            "task_type_secondary": record.get("label_task_type_secondary", ""),
            "difficulty": record.get("label_difficulty", ""),
            "is_multi_choice": record.get("label_is_multi_choice", False),
            "topic_keywords": record.get("label_topic_keywords", []),
            # schema 类型（eval_skill 需要此字段决定 metric）
            "schema": self._infer_schema(record),
        }

        return {
            "id": item_id,
            "input": input_data,
            "expected_output": expected,
            "metadata": metadata,
        }

    def _infer_schema(self, record: Dict[str, Any]) -> str:
        """根据分类结果推断评测 schema 类型"""
        is_multi = record.get("label_is_multi_choice", False)
        if is_multi:
            # 检查参考答案是否多选（>1 个字母）
            ref = map_field(record, "reference")
            import re
            letters = re.findall(r"[A-Ea-e]", ref) if ref else []
            if len(set(letters)) > 1:
                return "multi_choice"
            return "single_choice"
        # 非选择题
        ref = map_field(record, "reference")
        if ref and len(ref) > 200:
            return "open_qa_long"
        return "open_qa"
