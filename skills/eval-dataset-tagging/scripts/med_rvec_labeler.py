"""医学 RVEC 综合打标器

一次性完成：M医学场景 + T任务类型 + RVEC问题/亮点标签 + P等级 + 评分
对齐 fin_rvec_labeler.py 结构，适配医学领域配置（medical_rvec_config.yaml）。
兼容 eval_skill 输出的 samples.csv（prediction / ground_truth / accuracy_value 等字段）。
"""

import json
import re
from typing import Dict, Any, List, Optional

from base_labeler import BaseLabeler
from llm_client import LLMClient


# ── 构建标签体系文本（与 fin_rvec_labeler 同构，字段名适配） ──

def _build_schema_text(config: Dict) -> str:
    """从 medical_rvec_config 构建 prompt 可读的标签体系"""
    lines = []

    # M 场景（医学用 med_scene_schema）
    scene_key = "med_scene_schema" if "med_scene_schema" in config else "fin_scene_schema"
    scene_label = "M" if scene_key == "med_scene_schema" else "F"
    lines.append(f"# {scene_label} 轴：医学业务场景（可多选，用分号分隔）")
    for s in config.get(scene_key, []):
        desc = s.get("description", "")
        examples = s.get("examples", "")
        suffix = f"（如：{examples}）" if examples else ""
        lines.append(f"- {s['code']} {s['name']}：{desc}{suffix}")

    # T 任务
    lines.append("\n# T 轴：任务类型（可多选，用分号分隔）")
    for t in config.get("task_type_schema", []):
        lines.append(f"- {t['code']} {t['name']}：{t.get('description', '')}")

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


def _build_mt_cross_hint(config: Dict, scene_codes: List[str], task_codes: List[str]) -> str:
    """根据已识别的 M 和 T，匹配交叉校验规则，生成校验提示"""
    mt_rules = config.get("mt_cross_validation", config.get("ft_cross_validation", {})).get("rules", [])
    if not mt_rules:
        return ""
    hints = []
    for rule in mt_rules:
        scene_match = any(s in rule.get("scenes", []) for s in scene_codes)
        task_match = any(t in rule.get("tasks", []) for t in task_codes)
        if scene_match and task_match:
            must_check = ", ".join(rule.get("must_check", []))
            hints.append(f"⚠️ {rule.get('note', '')}（必检维度: {must_check}）")
    if not hints:
        return ""
    return "\n\n【M×T交叉校验提醒】\n" + "\n".join(hints)


SYSTEM_PROMPT = """\
你是一位医学领域大模型评测专家，精通 RVEC 评测标签体系。你需要对模型在医学场景下的输出进行结构化评测。

## 评测流程
1. **识别需求侧画像**：判断医学业务场景（M轴）和任务类型（T轴）
2. **打 RVEC 标签**：识别 R（硬伤）、V（价值）、E（体验）负向问题 + C（亮点）
3. **判定 P 等级**：每个负向标签标注 P0/P1/P2
4. **计算最终评分**：根据最严重负向信号给出 0-4 分

## 核心原则
- R 优先：R-P0 直接判 0 分，不因 V/E/C 好而抵消
- 医学安全：涉及误诊、漏诊、禁忌药物推荐等属于 P0 硬伤（R-RISK / R-FACT）
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
  "label_med_scene": "M01 内科（心血管、呼吸、消化、内分泌、神经内科等）",
  "label_task_type": "T02 疾病诊断与鉴别诊断；T03 治疗方案制定与药物选择",
  "label_rvec_primary": "R-FACT-1 事实错误",
  "label_rvec_all": "R-FACT-1 事实错误；R-RISK-3 用药安全风险",
  "label_severity": "P0",
  "label_score": 0,
  "label_highlights": "C-R-3 逻辑完整；C-E-2 结构友好",
  "label_evidence": "从实际回答中摘录有问题的具体句子",
  "label_reason": "简要说明为何打此标签，涵盖每个标签的判定理由"
}}

字段说明：
- label_med_scene：医学场景标签，多选用分号分隔
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
请对以下模型回答进行医学 RVEC 评测：

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


def _extract_text(record: Dict, keys: List[str], max_len: int = 3000) -> str:
    """依次尝试多个 key，返回第一个非空值（截断到 max_len）"""
    for k in keys:
        val = record.get(k)
        if val and str(val).strip():
            text = str(val).strip()
            return text[:max_len]
    return ""


def _parse_ground_truth(gt_raw: str) -> str:
    """解析 eval_skill 输出的 ground_truth JSON（如 {"kind":"single_choice","answer":"B"}）"""
    if not gt_raw:
        return ""
    gt_raw = gt_raw.strip()
    if gt_raw.startswith("{"):
        try:
            parsed = json.loads(gt_raw)
            return parsed.get("answer", "") or parsed.get("content", "") or gt_raw
        except (json.JSONDecodeError, TypeError):
            pass
    return gt_raw


class MedRvecLabeler(BaseLabeler):
    """医学 RVEC 综合打标器"""

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
        return "医学RVEC综合打标"

    def label_one(self, record: Dict[str, Any]) -> Dict[str, Any]:
        # ── 检查 error 字段：模型调用失败的记录直接标记，不调 LLM ──
        error_text = str(record.get("error", "")).strip()
        if error_text and error_text not in ("", "nan", "None"):
            return self._mark_error_record(record, error_text)

        # ── 字段提取：兼容 eval_skill 输出（prediction/ground_truth）和传统中文列名 ──
        question = _extract_text(record, [
            "题目", "input", "question", "prompt",
        ])
        expected = _extract_text(record, [
            "参考答案", "expected_output", "ground_truth", "reference",
        ], max_len=2000)
        # eval_skill 的 ground_truth 可能是 JSON
        if expected.startswith("{"):
            expected = _parse_ground_truth(expected)

        answer = _extract_text(record, [
            "实际回答", "output", "model_response", "answer_text_for_labeling",
            "prediction",  # eval_skill 输出列
        ])

        # 如果 question 和 answer 都为空，直接标记为数据缺失
        if not question and not answer:
            return self._mark_empty_record(record, expected)

        reasoning = _extract_text(record, [
            "推理过程", "reasoning",
        ])
        accuracy = _extract_text(record, [
            "Accuracy", "accuracy", "accuracy_value", "score",
        ], max_len=500)

        # 解析 JSON 字符串（如 prediction 是 trace_output JSON）
        if answer and answer.startswith("{"):
            try:
                parsed = json.loads(answer)
                answer = parsed.get("content", "") or parsed.get("choice", "") or answer
            except (json.JSONDecodeError, TypeError):
                pass
        if reasoning and reasoning.startswith("{"):
            try:
                parsed = json.loads(reasoning)
                reasoning = parsed.get("content", "") or parsed.get("reasoning", "") or reasoning
            except (json.JSONDecodeError, TypeError):
                pass

        # 如果 accuracy_reason 存在，追加到 accuracy 信息
        acc_reason = record.get("accuracy_reason", "")
        if acc_reason:
            accuracy = f"{accuracy} | 原因: {acc_reason}"

        user_msg = USER_PROMPT.format(
            question=question[:3000],
            expected_output=expected[:2000],
            answer=answer[:3000],
            reasoning=reasoning[:3000],
            accuracy=accuracy[:500],
        )

        # 第一轮：获取初步标签
        labels = self.llm.chat_json(self.system_prompt, user_msg)

        # M×T 交叉校验
        mt_hint = self._get_mt_cross_hint(labels)
        if mt_hint:
            verify_msg = (
                f"你刚才给出的标注结果如下：\n```json\n{json.dumps(labels, ensure_ascii=False, indent=2)}\n```\n"
                f"{mt_hint}\n\n"
                "请根据以上交叉校验提醒，重新审视你的标注。如果需要修正请输出修正后的完整JSON，如果无需修正请原样输出。"
            )
            try:
                labels = self.llm.chat_json(self.system_prompt, verify_msg)
            except Exception:
                pass  # 校验失败则保留初次结果

        result = dict(record)
        for key in ["label_med_scene", "label_task_type", "label_rvec_primary",
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

    def _get_mt_cross_hint(self, labels: Dict) -> str:
        """从初步标注结果中提取 M/T 编码，匹配交叉校验规则"""
        scene_str = str(labels.get("label_med_scene", ""))
        task_str = str(labels.get("label_task_type", ""))
        scene_codes = re.findall(r'M\d+', scene_str)
        task_codes = re.findall(r'T\d+', task_str)
        if not scene_codes or not task_codes:
            return ""
        return _build_mt_cross_hint(self.config, scene_codes, task_codes)

    def _mark_error_record(self, record: Dict[str, Any], error_text: str) -> Dict[str, Any]:
        """对模型调用失败（error 非空）的记录直接标记，不调 LLM"""
        result = dict(record)
        # 截取 error 前 200 字符作为 evidence
        err_snippet = error_text[:200]
        is_http_error = "404" in err_snippet or "500" in err_snippet or "<html>" in err_snippet.lower()
        result["label_med_scene"] = "M99 其他/跨科室/无法判断"
        result["label_task_type"] = "T99 其他/无法判断任务"
        result["label_rvec_primary"] = "R-UND-1 完全误解用户" if not is_http_error else "NONE"
        result["label_rvec_all"] = "NONE"
        result["label_severity"] = "P0" if is_http_error else "NONE"
        result["label_score"] = 0
        result["label_highlights"] = "NONE"
        result["label_evidence"] = f"模型调用失败: {err_snippet}"
        result["label_reason"] = f"模型端返回错误（非正常回答），无法评测。error={err_snippet}"
        result["label_status"] = "error_skipped"
        return result

    def _mark_empty_record(self, record: Dict[str, Any], expected: str) -> Dict[str, Any]:
        """对 question 和 answer 均为空的记录直接标记"""
        result = dict(record)
        result["label_med_scene"] = "M99 其他/跨科室/无法判断"
        result["label_task_type"] = "T99 其他/无法判断任务"
        result["label_rvec_primary"] = "R-UND-1 完全误解用户"
        result["label_rvec_all"] = "R-UND-1 完全误解用户：P1"
        result["label_severity"] = "P1"
        result["label_score"] = 1
        result["label_highlights"] = "NONE"
        result["label_evidence"] = "题目和模型回答均为空"
        result["label_reason"] = (
            f"数据缺失：题目和模型回答字段为空。"
            f"{'参考答案为: ' + expected[:100] if expected else '参考答案也为空。'}"
            f"无法进行有效评测。"
        )
        result["label_status"] = "data_missing"
        return result
