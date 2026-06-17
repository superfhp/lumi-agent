"""金融 RVEC 综合打标器

一次性完成：F金融场景 + T任务类型 + RVEC问题/亮点标签 + P等级 + 评分
对齐人工打标执行手册的完整流程。
支持 Few-shot 样例注入 + F×T 交叉校验规则。
"""

import json
import fnmatch
from typing import Dict, Any, List, Optional

from base_labeler import BaseLabeler
from llm_client import LLMClient

# ── 构建标签体系文本 ──

def _build_schema_text(config: Dict) -> str:
    """从 fin_rvec_config 构建 prompt 可读的标签体系"""
    lines = []

    # F 场景
    lines.append("# F 轴：金融业务场景（可多选，用分号分隔）")
    for f in config.get("fin_scene_schema", []):
        lines.append(f"- {f['code']} {f['name']}：{f['description']}")

    # T 任务
    lines.append("\n# T 轴：任务类型（可多选，用分号分隔）")
    for t in config.get("task_type_schema", []):
        lines.append(f"- {t['code']} {t['name']}：{t['description']}")

    # RVEC 标签
    lines.append("\n# RVEC 标签体系")
    lines.append("## R/V/E（负向问题标签，可多选，每个标签需标注 P 等级）")
    for group_key, tags in config.get("rvec_schema", {}).items():
        if group_key.startswith("C"):
            continue
        lines.append(f"\n### {group_key}")
        for tag in tags:
            lines.append(f"  - {tag['code']} {tag['name']}（{tag['type']}）")

    lines.append("\n## C（正向亮点标签，可多选，不抵消 R/V/E）")
    for group_key, tags in config.get("rvec_schema", {}).items():
        if not group_key.startswith("C"):
            continue
        lines.append(f"\n### {group_key}")
        for tag in tags:
            lines.append(f"  - {tag['code']} {tag['name']}（{tag['type']}）")

    # P 等级
    lines.append("\n# P 等级")
    sev = config.get("severity_schema", {})
    for level in sev.get("levels", []):
        lines.append(f"- {level}：{sev.get('definitions', {}).get(level, '')}")

    # 评分规则
    scoring = config.get("scoring_rules", {})
    if scoring:
        lines.append(f"\n# 评分规则（0-4 分）")
        lines.append(scoring.get("description", ""))

    return "\n".join(lines)


def _build_few_shot_text(config: Dict) -> str:
    """从 config 中的 few_shot_examples 构建示例文本"""
    examples = config.get("few_shot_examples", [])
    if not examples:
        return ""
    lines = ["\n## 参考样例（请严格对齐以下标注风格和粒度）"]
    for i, ex in enumerate(examples, 1):
        labels = ex.get("expected_labels", {})
        lines.append(f"\n### 样例 {i}")
        lines.append(f"【题目】{ex.get('question', '')[:500]}")
        lines.append(f"【参考答案】{ex.get('expected_output', '')[:500]}")
        lines.append(f"【模型回答】{ex.get('answer', '')[:500]}")
        lines.append(f"【正确标注】")
        lines.append(f"```json")
        lines.append(json.dumps(labels, ensure_ascii=False, indent=2))
        lines.append(f"```")
    return "\n".join(lines)


def _build_ft_cross_hint(config: Dict, fin_scene_codes: List[str], task_codes: List[str]) -> str:
    """根据已识别的F和T，匹配交叉校验规则，生成校验提示"""
    ft_rules = config.get("ft_cross_validation", {}).get("rules", [])
    if not ft_rules:
        return ""
    hints = []
    for rule in ft_rules:
        scene_match = any(s in rule.get("scenes", []) for s in fin_scene_codes)
        task_match = any(t in rule.get("tasks", []) for t in task_codes)
        if scene_match and task_match:
            must_check = ", ".join(rule["must_check"])
            hints.append(f"⚠️ {rule['note']}（必检维度: {must_check}）")
    if not hints:
        return ""
    return "\n\n【F×T交叉校验提醒】\n" + "\n".join(hints)


SYSTEM_PROMPT = """\
你是一位金融领域大模型评测专家，精通 RVEC 评测标签体系。你需要对模型在金融场景下的输出进行结构化评测。

## 评测流程
1. **识别需求侧画像**：判断金融业务场景（F轴）和任务类型（T轴）
2. **打 RVEC 标签**：识别 R（硬伤）、V（价值）、E（体验）负向问题 + C（亮点）
3. **判定 P 等级**：每个负向标签标注 P0/P1/P2
4. **计算最终评分**：根据最严重负向信号给出 0-4 分

## 核心原则
- R 优先：R-P0 直接判 0 分，不因 V/E/C 好而抵消
- C 不抵消：亮点不能抵消 R/V/E 的负向问题
- P2 二次判断：仅 P2 时需判断是否需要修改（需改=2分，不需改=3分）
- 单一问题最小归因：优先选最小最直接的标签，多个独立问题可多标签共现
- 先判专业标签，再选择性叠加通用细化描述

## 标签体系
{schema_text}
{few_shot_text}

## 输出要求
你必须以**严格 JSON**格式输出，不要包含任何其他文字。格式如下：
{{
  "label_fin_scene": "F08 金融风险、监管合规、反欺诈与金融犯罪防控",
  "label_task_type": "T16 题目作答、考试辅导与案例解析；T12 合规审查、风险识别与反诈防范",
  "label_rvec_primary": "R-FACT-1 事实错误",
  "label_rvec_all": "R-FACT-1 事实错误；R-REA-4 前提错误；R-REA-2 推理跳步",
  "label_severity": "P1",
  "label_score": 1,
  "label_highlights": "C-R-3 逻辑完整；C-E-2 结构友好",
  "label_evidence": "从实际回答中摘录有问题的具体句子",
  "label_reason": "简要说明为何打此标签，涵盖每个标签的判定理由"
}}

字段说明：
- label_fin_scene：金融场景标签，多选用分号分隔
- label_task_type：任务类型标签，多选用分号分隔
- label_rvec_primary：最主要的一个 RVEC 问题标签，无问题填 "NONE"
- label_rvec_all：全部 RVEC 问题标签（分号分隔），无问题填 "NONE"
- label_severity：最高严重度 P 等级，无问题填 "NONE"
- label_score：0-4 分，严格按评分规则推导
- label_highlights：C 亮点标签（分号分隔），无亮点填 "NONE"
- label_evidence：从实际回答中摘录有问题的关键句子，无问题留空
- label_reason：每个标签的判定理由
"""

USER_PROMPT = """\
请对以下模型回答进行金融 RVEC 评测：

【题目/问题】
{question}

【参考答案】
{expected_output}

【模型实际回答】
{answer}

【模型推理过程】
{reasoning}

【原始 Accuracy 评分】
{accuracy}
"""


class FinRvecLabeler(BaseLabeler):
    """金融 RVEC 综合打标器"""

    def __init__(self, llm: LLMClient, config: Dict, retry_limit: int = 3,
                 max_workers: int = 5, lumi_client=None, dataset_name: str = "", run_name: str = ""):
        super().__init__(llm, retry_limit, max_workers, lumi_client, dataset_name, run_name)
        self.config = config
        self.schema_text = _build_schema_text(config)
        self.few_shot_text = _build_few_shot_text(config)
        self.system_prompt = SYSTEM_PROMPT.format(
            schema_text=self.schema_text,
            few_shot_text=self.few_shot_text,
        )

    @property
    def label_name(self) -> str:
        return "金融RVEC综合打标"

    def label_one(self, record: Dict[str, Any]) -> Dict[str, Any]:
        # 兼容 CSV 中的各种字段名
        question = record.get("题目", "") or record.get("input", "") or record.get("question", "")
        expected = record.get("参考答案", "") or record.get("expected_output", "")
        answer = record.get("实际回答", "") or record.get("output", "") or record.get("model_response", "") or record.get("answer_text_for_labeling", "")
        reasoning = record.get("推理过程", "") or record.get("reasoning", "")
        accuracy = record.get("Accuracy", "") or record.get("accuracy", "")

        # 如果 reasoning 是 JSON 字符串（如 trace_output），提取 content
        if reasoning and reasoning.startswith("{"):
            try:
                parsed = json.loads(reasoning)
                reasoning = parsed.get("content", "") or parsed.get("reasoning", "") or reasoning
            except (json.JSONDecodeError, TypeError):
                pass
        if answer and answer.startswith("{"):
            try:
                parsed = json.loads(answer)
                answer = parsed.get("content", "") or parsed.get("choice", "") or answer
            except (json.JSONDecodeError, TypeError):
                pass

        user_msg = USER_PROMPT.format(
            question=question[:3000],
            expected_output=expected[:2000],
            answer=answer[:3000],
            reasoning=reasoning[:3000],
            accuracy=accuracy,
        )

        # 第一轮：获取初步标签
        labels = self.llm.chat_json(self.system_prompt, user_msg)

        # F×T 交叉校验：根据初步 F/T 结果注入校验提示，必要时二次修正
        ft_hint = self._get_ft_cross_hint(labels)
        if ft_hint:
            verify_msg = (
                f"你刚才给出的标注结果如下：\n```json\n{json.dumps(labels, ensure_ascii=False, indent=2)}\n```\n"
                f"{ft_hint}\n\n"
                "请根据以上交叉校验提醒，重新审视你的标注。如果需要修正请输出修正后的完整JSON，如果无需修正请原样输出。"
            )
            try:
                labels = self.llm.chat_json(self.system_prompt, verify_msg)
            except Exception:
                pass  # 校验失败则保留初次结果

        result = dict(record)
        for key in ["label_fin_scene", "label_task_type", "label_rvec_primary",
                     "label_rvec_all", "label_severity", "label_score",
                     "label_highlights", "label_evidence", "label_reason"]:
            result[key] = labels.get(key, "")

        # 校验评分规则
        result["label_score"] = self._validate_score(result)
        return result

    def _validate_score(self, record: Dict) -> int:
        """根据 P 等级规则校验/修正评分"""
        severity = str(record.get("label_severity", "NONE"))
        rvec_all = str(record.get("label_rvec_all", "NONE"))
        highlights = str(record.get("label_highlights", "NONE"))
        raw_score = record.get("label_score", 3)

        try:
            raw_score = int(raw_score)
        except (ValueError, TypeError):
            raw_score = 3

        if severity == "P0":
            return 0
        elif severity == "P1":
            return 1
        elif severity == "P2":
            return min(raw_score, 3) if raw_score >= 2 else 2
        elif rvec_all == "NONE" or not rvec_all.strip():
            if highlights != "NONE" and highlights.strip():
                return 4
            return 3
        return raw_score

    def _get_ft_cross_hint(self, labels: Dict) -> str:
        """从初步标注结果中提取F/T编码，匹配交叉校验规则"""
        import re
        scene_str = str(labels.get("label_fin_scene", ""))
        task_str = str(labels.get("label_task_type", ""))
        scene_codes = re.findall(r'F\d+', scene_str)
        task_codes = re.findall(r'T\d+', task_str)
        if not scene_codes or not task_codes:
            return ""
        return _build_ft_cross_hint(self.config, scene_codes, task_codes)
