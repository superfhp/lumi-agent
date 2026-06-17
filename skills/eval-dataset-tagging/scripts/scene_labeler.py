"""场景细分打标器：为每条数据打 L1/L2/L3 场景标签

字段映射统一使用 field_mapping.map_field（v4 Phase 2 对齐新 schema）：
- input  ← question / 题目 / prompt / context / 输入
- output ← model_response / 实际回答 / answer / output
- reasoning ← model_reasoning / 推理过程 / reasoning
- expected_output ← ground_truth_structured / 参考答案 / expected_output
"""

from typing import Dict, Any, List

from base_labeler import BaseLabeler
from llm_client import LLMClient
from schema_parser import build_scene_tree_text
from field_mapping import map_field

SYSTEM_PROMPT = """\
你是一个专业的评测数据分类专家。你需要根据给定的标签体系，对输入的评测样本进行场景分类。

## 标签体系
{schema_text}

## 输出要求
你必须以**严格JSON**格式输出，不要包含任何其他文字。格式如下：
{{
{output_fields}
}}

其中每个维度的值是一个对象，包含 l1, l2, l3, reason 四个字段。
- l1/l2/l3 必须是标签体系中存在的值，从上到下逐级选择最匹配的标签。
- 如果没有合适的 l3，可以设为 null。如果没有合适的 l2，l2 和 l3 都设为 null。
- reason 用一句话说明分类理由。
"""

USER_PROMPT = """\
请对以下评测样本进行场景分类：

【问题/输入】
{input}

【模型输出】
{output}

【推理过程】
{reasoning}

【参考答案】
{expected_output}
"""


class SceneLabeler(BaseLabeler):
    """场景细分打标"""

    def __init__(self, llm: LLMClient, scene_schema: List[Dict], retry_limit: int = 3,
                 max_workers: int = 5, lumi_client=None, dataset_name: str = "", run_name: str = ""):
        super().__init__(llm, retry_limit, max_workers, lumi_client, dataset_name, run_name)
        self.scene_schema = scene_schema
        self.schema_text = build_scene_tree_text(scene_schema)
        self.dimensions = [d["dimension"] for d in scene_schema]
        fields = [f'  "{dim}": {{"l1": "...", "l2": "...", "l3": "...", "reason": "..."}}' for dim in self.dimensions]
        self.output_fields = ",\n".join(fields)
        self.system_prompt = SYSTEM_PROMPT.format(schema_text=self.schema_text, output_fields=self.output_fields)

    @property
    def label_name(self) -> str:
        return "场景细分打标"

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
        result["scene_labels"] = {}
        for dim in self.dimensions:
            if dim in labels:
                result["scene_labels"][dim] = labels[dim]
            else:
                result["scene_labels"][dim] = {"l1": None, "l2": None, "l3": None, "reason": "未匹配"}
        # v4 字段：写入 labeler 标识，方便区分多次打标
        result.setdefault("labeler", f"scene_tag@{self.llm.model}")
        result.setdefault("review_status", "pending")
        return result
