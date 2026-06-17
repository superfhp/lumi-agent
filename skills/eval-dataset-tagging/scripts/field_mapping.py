"""通用字段映射 + 题型族路由工具

供 run_tagging.py / scene_labeler.py / error_labeler.py 共享使用。

设计原则：
- 与 fin_rvec_tag.py 中的 _FIELD_CANDIDATES / classify_family 保持字段对齐
  （fin_rvec_tag.py 仍保留自己的副本以维持自包含特性，方便单文件分发）
- 提供 has_role / get_field 等高层 API，让 labeler 不再硬编码字段名
- classify_family 不强依赖 config（无 task_family_rules 时返回 GENERIC）
"""

from typing import Dict, List, Any, Optional


# ════════════════════════════════════════════════════════════
# 字段候选表（与 fin_rvec_tag._FIELD_CANDIDATES 对齐）
# ════════════════════════════════════════════════════════════

FIELD_CANDIDATES: Dict[str, List[str]] = {
    # 题目主体（去掉前置背景材料的核心提问）
    "question": ["question", "题目", "prompt", "query", "问题"],
    # 背景上下文（report 池含 PDF 摘要，QA 池可能为空）
    "context": ["context", "输入", "input", "full_context", "题干"],
    # 模型最终回答（已剥离 think/CoT）
    "answer": ["model_response", "prediction", "实际回答", "output", "answer",
               "answer_text_for_labeling", "response", "回答"],
    # 选择题答案字母
    "model_choice": ["model_choice", "accuracy__prediction"],
    # 期望选项（选择题标准答案字母）
    "expected_choice": ["expected_choice", "accuracy__expected"],
    # 结构化标准答案
    "reference": ["ground_truth_structured", "ground_truth", "参考答案", "expected_output",
                  "reference", "gold", "标准答案"],
    # 非结构化标准答案（report 池 PDF 文件名）
    "ground_truth_unstructured": ["ground_truth_unstructured"],
    # 模型推理过程
    "reasoning": ["model_reasoning", "reasoning", "推理过程", "trace_output", "思维链"],
    # 评分
    "accuracy": ["accuracy_value", "Accuracy", "accuracy", "score", "评分"],
    # 自动评测系统的评注
    "judge_comment": ["judge_comment", "accuracy_reason"],
    # 截断标记
    "truncated": ["model_response_truncated"],
    "full_length": ["model_response_full_length"],
    # 多维评分
    "factuality_score": ["factuality_score"],
    "recall_score": ["recall_score"],
    "reasoning_score": ["reasoning_score"],
    "structure_score": ["structure_score"],
    "comprehensive_score": ["comprehensive_score"],
    # 文字评注
    "accuracy_comment": ["accuracy_reason", "Accuracy_comment", "accuracy_comment"],
    "reasoning_quality_comment": ["reasoning_quality_comment"],
    # 题型族识别
    "norm_task": ["norm_task_from_filename", "dataset"],
    "meta_task_family": ["meta_task_family"],
    "meta_schema_type": ["meta_schema_type", "schema"],
    # ID
    "id": ["answer_id", "id", "item_id", "sample_id"],
    # 错误信息（eval_skill 输出的模型调用错误）
    "error": ["error"],
    # 评测配置信息
    "model_name": ["model"],
    "scoring_mode": ["scoring_mode"],
}


def map_field(record: Dict[str, Any], role: str, default: str = "") -> str:
    """按 role 提取字段值，命中第一个非空候选列即返回。

    Args:
        record: 单条数据
        role: 字段角色，对应 FIELD_CANDIDATES 的 key
        default: 全部候选都为空时返回

    Returns:
        str: 字段值（非空）或 default
    """
    for col in FIELD_CANDIDATES.get(role, []):
        val = record.get(col, "")
        if val and str(val).strip():
            return str(val)
    return default


def has_role(record: Dict[str, Any], role: str) -> bool:
    """检查 record 是否包含 role 对应的非空字段。"""
    return bool(map_field(record, role))


def auto_field_mapping(records: List[Dict[str, Any]]) -> Dict[str, str]:
    """自动识别字段映射：{role: 实际命中的列名}

    仅基于第一条数据的 keys，不验证非空率（保留所有命中的列名）。
    """
    if not records:
        return {}
    columns = list(records[0].keys())
    mapping: Dict[str, str] = {}
    for role, candidates in FIELD_CANDIDATES.items():
        for col in candidates:
            if col in columns:
                mapping[role] = col
                break
    return mapping


def detect_present_roles(records: List[Dict[str, Any]],
                          min_non_empty_ratio: float = 0.1) -> Dict[str, Dict[str, Any]]:
    """检测 records 中实际存在（非空率 >= 阈值）的 role 列表。

    返回 {role: {column, non_empty_count, non_empty_ratio}}
    """
    if not records:
        return {}
    mapping = auto_field_mapping(records)
    present: Dict[str, Dict[str, Any]] = {}
    total = len(records)
    for role, col in mapping.items():
        non_empty = sum(1 for r in records if str(r.get(col, "")).strip())
        ratio = non_empty / total if total else 0.0
        if ratio >= min_non_empty_ratio:
            present[role] = {
                "column": col,
                "non_empty_count": non_empty,
                "non_empty_ratio": round(ratio, 3),
            }
    return present


# ════════════════════════════════════════════════════════════
# 题型族路由（与 fin_rvec_tag.classify_family 对齐）
# ════════════════════════════════════════════════════════════

# 兜底匹配规则（无 config 或 config 中无 task_family_rules 时使用）
DEFAULT_FAMILY_FALLBACK_RULES: Dict[str, List[str]] = {
    "QA_CHOICE": ["Fin-Compliance", "Economics", "Investing", "Literacy", "Quantitatics"],
    "SENTIMENT": ["FinNews-Sentiment", "Anomalous-Emotion"],
    "REPORT_EVAL": ["Eval_FullReport", "NewsReport", "ResearchReport"],
    "LONG_GEN": ["10K_Analysis", "Report_Analysis"],
}


def classify_family(record: Dict[str, Any],
                     config: Optional[Dict[str, Any]] = None,
                     default: str = "GENERIC") -> str:
    """根据 record 的 norm_task 等字段识别题型族。

    优先级：
    1. config["task_family_rules"][family]["match_norm_task"] 命中
    2. DEFAULT_FAMILY_FALLBACK_RULES 命中（无 config 时）
    3. default（默认 GENERIC）

    Returns:
        family key（QA_CHOICE / SENTIMENT / REPORT_EVAL / LONG_GEN / GENERIC）
    """
    norm_task = map_field(record, "norm_task")
    if not norm_task:
        return default

    # 1. 优先用 config 中的规则
    if config:
        rules = config.get("task_family_rules", {})
        for family, cfg in rules.items():
            if family.startswith("_") or not isinstance(cfg, dict):
                continue
            match_list = cfg.get("match_norm_task", [])
            if norm_task in match_list:
                return family

    # 2. 兜底规则
    for family, match_list in DEFAULT_FAMILY_FALLBACK_RULES.items():
        if norm_task in match_list:
            return family

    return default


def count_family_dist(records: List[Dict[str, Any]],
                       config: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """统计 records 的题型族分布。

    Returns:
        {family_key: count}，按数量降序的 dict（Python 3.7+ 有序）
    """
    counts: Dict[str, int] = {}
    for r in records:
        fam = classify_family(r, config)
        counts[fam] = counts.get(fam, 0) + 1
    # 按数量降序
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


# ════════════════════════════════════════════════════════════
# 数据特征探查（用于 --auto 智能判别）
# ════════════════════════════════════════════════════════════

def detect_data_features(records: List[Dict[str, Any]],
                          min_non_empty_ratio: float = 0.3) -> Dict[str, Any]:
    """探测数据特征，返回用于 mode 路由的判别字典。

    Returns:
        {
          "has_question": bool,          # question 列存在且非空率 >= 阈值
          "has_answer": bool,            # 模型回答存在且非空率 >= 阈值
          "has_reference": bool,         # 参考答案存在
          "has_accuracy": bool,          # 评分存在
          "has_judge_comment": bool,     # 自动评测线索存在
          "has_norm_task": bool,         # 题型族字段存在
          "answer_non_empty_ratio": float,
          "norm_task_non_empty_ratio": float,
        }
    """
    if not records:
        return {
            "has_question": False, "has_answer": False, "has_reference": False,
            "has_accuracy": False, "has_judge_comment": False, "has_norm_task": False,
            "answer_non_empty_ratio": 0.0, "norm_task_non_empty_ratio": 0.0,
        }

    present = detect_present_roles(records, min_non_empty_ratio=min_non_empty_ratio)
    total = len(records)

    def _ratio(role: str) -> float:
        info = present.get(role)
        return info["non_empty_ratio"] if info else 0.0

    return {
        "has_question": "question" in present,
        "has_answer": "answer" in present,
        "has_reference": "reference" in present,
        "has_accuracy": "accuracy" in present,
        "has_judge_comment": "judge_comment" in present,
        "has_norm_task": "norm_task" in present,
        "answer_non_empty_ratio": _ratio("answer"),
        "norm_task_non_empty_ratio": _ratio("norm_task"),
        "total_rows": total,
    }


# ════════════════════════════════════════════════════════════
# 金融领域关键词（与 run_tagging.py 中的关键词保持同步）
# ════════════════════════════════════════════════════════════

FIN_KEYWORDS_DEFAULT = [
    "金融", "银行", "证券", "基金", "保险", "理财", "股票", "债券", "期货",
    "信贷", "贷款", "投资", "风控", "合规", "征信", "利率", "汇率", "资管",
    "财报", "审计", "融资", "信用卡", "理赔", "ETF", "IPO", "基金经理",
]

MED_KEYWORDS_DEFAULT = [
    "医学", "医疗", "临床", "诊断", "治疗", "药物", "疾病", "患者", "症状",
    "检验", "检查", "指标", "手术", "用药", "处方", "病理", "生理", "解剖",
    "中医", "中药", "护理", "急救", "预后", "并发症", "禁忌", "适应症",
    "血常规", "肝功能", "肾功能", "CT", "MRI", "心电图", "抗生素",
]


def _detect_domain(records: List[Dict[str, Any]],
                   keywords: List[str],
                   domain_name: str,
                   sample_size: int = 20,
                   threshold: float = 0.3) -> Dict[str, Any]:
    """通用领域检测函数。

    Returns:
        {"domain": domain_name|"general", "hit_ratio": float, "hit_count": int, "sample_size": int}
    """
    sample = records[:min(sample_size, len(records))]
    hit = 0
    for r in sample:
        text = " ".join(str(v) for v in r.values())[:2000]
        if any(kw in text for kw in keywords):
            hit += 1
    n = max(len(sample), 1)
    ratio = hit / n
    return {
        "domain": domain_name if ratio >= threshold else "general",
        "hit_ratio": round(ratio, 3),
        "hit_count": hit,
        "sample_size": len(sample),
    }


def detect_finance_domain(records: List[Dict[str, Any]],
                            sample_size: int = 20,
                            keywords: Optional[List[str]] = None) -> Dict[str, Any]:
    """检测是否属于金融领域。"""
    return _detect_domain(records, keywords or FIN_KEYWORDS_DEFAULT,
                          "finance", sample_size=sample_size)


def detect_medical_domain(records: List[Dict[str, Any]],
                            sample_size: int = 20,
                            keywords: Optional[List[str]] = None) -> Dict[str, Any]:
    """检测是否属于医学领域。"""
    return _detect_domain(records, keywords or MED_KEYWORDS_DEFAULT,
                          "medical", sample_size=sample_size)


# ════════════════════════════════════════════════════════════
# 智能模式路由（v4 Phase 2 核心）
# ════════════════════════════════════════════════════════════

def recommend_mode(records: List[Dict[str, Any]],
                    config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """根据数据特征推荐打标模式。

    判别决策树：
    1. has_answer + has_accuracy + 金融域      → fin_rvec
    2. has_answer + has_accuracy + 医学域      → med_rvec
    3. has_answer + has_accuracy + 非金融非医学 → error_attribution（提示用户）
    4. has_answer + has_judge_comment           → fin_rvec（含 judge 线索的 RVEC 数据）
    5. has_answer + 无 accuracy/judge           → error_attribution（无评分的错误归因）
    6. has_question + 无 answer                 → scene_labeling
    7. 数据不完整                              → 报错

    Returns:
        {
          "recommended_mode": "fin_rvec"|"med_rvec"|"scene_labeling"|"error_attribution"|"unknown",
          "reason": str,
          "features": {...},          # detect_data_features 结果
          "domain": {...},            # detect_finance_domain 结果
          "medical_domain": {...},    # detect_medical_domain 结果
          "family_dist": {...},       # count_family_dist 结果
          "missing_required_fields": [],
          "actionable_hints": [str],
        }
    """
    features = detect_data_features(records)
    domain = detect_finance_domain(records)
    med_domain = detect_medical_domain(records)
    family_dist = count_family_dist(records, config)

    has_q = features["has_question"]
    has_a = features["has_answer"]
    has_acc = features["has_accuracy"]
    has_jc = features["has_judge_comment"]
    is_finance = domain["domain"] == "finance"
    is_medical = med_domain["domain"] == "medical"

    missing: List[str] = []
    hints: List[str] = []

    if not has_q:
        missing.append("question/题目（必填）")

    if has_q and has_a and (has_acc or has_jc) and is_finance:
        mode = "fin_rvec"
        reason = (f"数据含 question + answer + 评分线索 + 金融关键词命中率 {domain['hit_ratio']:.0%}，"
                  "推荐金融 RVEC 综合打标")
        hints.append("可用 fin_rvec_tag.py --auto --input X 一键完成")
    elif has_q and has_a and (has_acc or has_jc) and is_medical:
        mode = "med_rvec"
        reason = (f"数据含 question + answer + 评分线索 + 医学关键词命中率 {med_domain['hit_ratio']:.0%}，"
                  "推荐医学 RVEC 综合打标")
        hints.append("将使用 config/medical_rvec_config.yaml 医学评测体系")
        hints.append("如需用金融 RVEC 体系打标，可用 --mode fin_rvec 强制启用")
    elif has_q and has_a and (has_acc or has_jc) and not is_finance and not is_medical:
        mode = "error_attribution"
        reason = (f"数据含 question + answer + 评分线索，但金融命中率仅 {domain['hit_ratio']:.0%} / "
                  f"医学命中率仅 {med_domain['hit_ratio']:.0%}（非特定领域），推荐通用错误归因")
        hints.append("如需用金融 RVEC 体系打标，可用 --mode fin_rvec 强制启用")
        hints.append("如需用医学 RVEC 体系打标，可用 --mode med_rvec 强制启用")
        hints.append("或提供 --config 指定自定义错误归因配置文件")
    elif has_q and has_a:
        mode = "error_attribution"
        reason = "数据含 question + answer 但缺少 accuracy/judge_comment，按错误归因模式处理"
        hints.append("如有评分字段未识别，请检查列名是否为 Accuracy/accuracy/score/评分 之一")
    elif has_q and not has_a:
        mode = "dataset_ingest"
        reason = "数据仅含 question 无模型回答，推荐评测集入库分类（自动标注场景+任务类型后上传 Lumi）"
        hints.append("将对每条题目进行场景+任务类型分类")
        hints.append("可通过 --lumi-create-dataset <名称> 同步上传 Lumi 平台")
        hints.append("如只需做场景细分（L1/L2/L3），可用 --mode scene_labeling --config scene_schema.yaml")
    else:
        mode = "unknown"
        reason = "数据特征不足，无法自动判定打标模式"

    return {
        "recommended_mode": mode,
        "reason": reason,
        "features": features,
        "domain": domain,
        "medical_domain": med_domain,
        "family_dist": family_dist,
        "missing_required_fields": missing,
        "actionable_hints": hints,
    }
