"""错误归因打标器：为 badcase 打错误类别标签和严重程度

字段映射统一使用 field_mapping.map_field（v4 Phase 2 对齐新 schema）。
"""

from typing import Dict, Any, List, Optional

from base_labeler import BaseLabeler
from llm_client import LLMClient
from schema_parser import build_error_schema_text
from field_mapping import map_field

SYSTEM_PROMPT = """\
你是一个专业的模型评测错误分析专家。你需要对模型的错误回答进行归因分析。

{schema_text}

## 输出要求
你必须以**严格JSON**格式输出，不要包含任何其他文字。格式如下：
{{
  "error_labels": [
    {{"label": "错误类别名称", "severity": 严重程度数值}},
    ...
  ],
  "error_reason": "详细的错误归因分析说明，涵盖每个错误标签的理由和严重程度判断依据"
}}

- error_labels 可以有多个（多选），从错误分类清单中选择。
- severity 使用给定的严重程度量表。
- 如果没有严重程度量表，severity 统一填 null。
"""

USER_PROMPT = """\
请对以下模型错误回答进行归因分析：

【问题/输入】
{input}

【模型输出】
{output}

【推理过程】
{reasoning}

【参考答案】
{expected_output}
"""


class ErrorLabeler(BaseLabeler):
    """错误归因打标"""

    def __init__(self, llm: LLMClient, error_schema: List[Dict], severity_schema: Optional[Dict] = None,
                 retry_limit: int = 3, max_workers: int = 5, lumi_client=None,
                 dataset_name: str = "", run_name: str = ""):
        super().__init__(llm, retry_limit, max_workers, lumi_client, dataset_name, run_name)
        self.error_schema = error_schema
        self.severity_schema = severity_schema
        schema_text = build_error_schema_text(error_schema, severity_schema)
        self.system_prompt = SYSTEM_PROMPT.format(schema_text=schema_text)

    @property
    def label_name(self) -> str:
        return "错误归因打标"

    def label_one(self, record: Dict[str, Any]) -> Dict[str, Any]:
        # v4 Phase 2: 用 field_mapping 统一字段映射，兼容新旧 schema
        question = map_field(record, "question") or map_field(record, "context")
        answer = map_field(record, "answer")
        reasoning = map_field(record, "reasoning")
        reference = map_field(record, "reference") or map_field(record, "ground_truth_unstructured")

        user_msg = USER_PROMPT.format(
            input=question,
            output=answer,
            reasoning=reasoning,
            expected_output=reference,
        )
        labels = self.llm.chat_json(self.system_prompt, user_msg)

        result = dict(record)
        result["error_labels"] = labels.get("error_labels", [])
        result["error_reason"] = labels.get("error_reason", "")
        # v4 字段：写入 labeler 标识，方便区分多次打标
        result.setdefault("labeler", f"error_tag@{self.llm.model}")
        result.setdefault("review_status", "pending")
        return result
