"""评测集打标入口脚本

用法:
  # 指定配置文件
  python run_tagging.py --config ../config/scene_schema_example.yaml --input data/eval_set.jsonl
  python run_tagging.py --config ../config/error_schema_example.yaml --input data/eval_set.jsonl
  python run_tagging.py --config ../config/fin_rvec_config.yaml --input data/labeling_answers.csv

  # 自动模式：自动检测数据领域，金融数据自动使用 fin_rvec_config
  python run_tagging.py --mode auto --input data/labeling_answers.csv
  python run_tagging.py --mode auto --lumi-dataset FinNews-Eval --workers 10
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from datetime import datetime

# 确保 scripts/ 目录在 sys.path 中（支持从任意位置调用）
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = SCRIPT_DIR.parent / "config"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from io_utils import read_data, read_from_lumi, write_data
from schema_parser import parse_config
from utils import apply_badcase_filter
from field_mapping import (
    auto_field_mapping,
    classify_family,
    count_family_dist,
    detect_data_features,
    detect_finance_domain,
    detect_medical_domain,
    recommend_mode,
)

# LLM/Lumi/Labeler 延迟导入（inspect 模式不需要 openai 等依赖）
LLMClient = None
LumiClient = None
SceneLabeler = None
ErrorLabeler = None
FinRvecLabeler = None
MedRvecLabeler = None
DatasetIngestLabeler = None


def _ensure_imports():
    """延迟导入 LLM/Lumi/Labeler 模块（仅在实际打标时加载）"""
    global LLMClient, LumiClient, SceneLabeler, ErrorLabeler, FinRvecLabeler, MedRvecLabeler, DatasetIngestLabeler
    if LLMClient is None:
        from llm_client import LLMClient as _L
        from lumi_client import LumiClient as _C
        from scene_labeler import SceneLabeler as _S
        from error_labeler import ErrorLabeler as _E
        from fin_rvec_labeler import FinRvecLabeler as _F
        from med_rvec_labeler import MedRvecLabeler as _M
        from dataset_ingest_labeler import DatasetIngestLabeler as _D
        LLMClient = _L
        LumiClient = _C
        SceneLabeler = _S
        ErrorLabeler = _E
        FinRvecLabeler = _F
        MedRvecLabeler = _M
        DatasetIngestLabeler = _D

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ── 数据探查 ──

# 字段名映射保留（向后兼容历史代码），新代码请直接使用 field_mapping 模块
_FIELD_CANDIDATES = {
    "question": ["题目", "input", "question", "prompt", "query", "问题"],
    "answer": ["实际回答", "output", "model_response", "answer", "answer_text_for_labeling", "response", "回答"],
    "reference": ["参考答案", "expected_output", "reference", "gold", "标准答案"],
    "reasoning": ["推理过程", "reasoning", "trace_output", "思维链"],
    "accuracy": ["Accuracy", "accuracy", "score", "评分"],
    "id": ["answer_id", "id", "item_id", "sample_id"],
}


def _build_field_mapping(records):
    """从 records 中自动识别字段映射，返回 {role: {"column": col, ...}}（兼容旧调用）"""
    mapping = auto_field_mapping(records)
    return {role: {"column": col} for role, col in mapping.items()}


def inspect_data(records, file_path=None):
    """分析数据结构，输出探查报告（JSON）。

    v4 Phase 2 升级：
    - 输出 norm_task_family_dist 帮助 Agent 理解题型分布
    - recommended_mode 改用 field_mapping.recommend_mode（智能判别）
    - 输出 actionable_hints（可操作建议）
    """
    import json as _json
    if not records:
        print(_json.dumps({"error": "数据为空"}, ensure_ascii=False, indent=2))
        return

    columns = list(records[0].keys())
    sample = records[0]

    # 字段自动映射（含样本与非空率）
    mapping = auto_field_mapping(records)
    field_mapping = {}
    for role, col in mapping.items():
        field_mapping[role] = {
            "column": col,
            "sample": str(sample.get(col, ""))[:200],
            "non_empty": sum(1 for r in records if str(r.get(col, "")).strip()),
        }

    # 智能判别（使用 v4 新逻辑）
    rec = recommend_mode(records)
    domain = rec["domain"]
    features = rec["features"]
    family_dist = rec["family_dist"]
    recommended_mode_val = rec["recommended_mode"]
    recommend_reason = rec["reason"]

    # 输出字段预告
    output_fields_map = {
        "fin_rvec": ["label_fin_scene", "label_task_type", "label_rvec_primary", "label_rvec_all",
                      "label_severity", "label_score", "label_highlights", "label_evidence", "label_reason",
                      "labeler", "review_status"],
        "scene_labeling": ["scene_labels (多维度L1/L2/L3)", "labeler", "review_status"],
        "error_attribution": ["error_attribution", "error_reason", "label_status", "labeler", "review_status"],
    }

    # 数据行预览（前3条，关键字段截断展示）
    preview_rows = []
    key_roles = ["id", "question", "answer", "reference", "accuracy"]
    for i, r in enumerate(records[:3]):
        row = {"_row": i + 1}
        for role in key_roles:
            if role in mapping:
                col = mapping[role]
                val = str(r.get(col, ""))
                row[f"{role}({col})"] = val[:120] + ("..." if len(val) > 120 else "")
        preview_rows.append(row)

    # 已有标注统计
    label_columns = [c for c in columns if c.startswith("label_") or c.startswith("scene_") or c.startswith("error_")]
    already_labeled_count = 0
    if label_columns:
        for r in records:
            if any(str(r.get(c, "")).strip() for c in label_columns):
                already_labeled_count += 1

    # ── v3.5 patch ②：字段映射确认（按角色分类） ──
    field_confirmation = {
        "primary": {
            "题目": mapping.get("question") or mapping.get("context") or "❌ 未识别",
            "模型回答": mapping.get("answer") or "（无 → 仅可做 scene_labeling）",
            "参考答案": mapping.get("reference") or mapping.get("ground_truth_unstructured") or "（无）",
            "推理过程": mapping.get("reasoning") or "（无）",
        },
        "auxiliary": {
            "原始评分": mapping.get("accuracy") or "（无）",
            "judge 评注": mapping.get("judge_comment") or "（无）",
            "题型族字段": mapping.get("norm_task") or "（无 → 默认 GENERIC）",
            "截断标记": mapping.get("truncated") or "（无）",
        },
        "id": {
            "ID 列": mapping.get("id") or "（无 → 用行号兜底）",
        },
    }

    # ── v3.5 patch ③：抽样建议 ──
    n = len(records)
    if n <= 20:
        sample_advice = {"recommend": "all", "reason": f"数据量仅 {n} 条，建议直接全量打标",
                         "command_hint": "直接 --auto"}
    elif n <= 100:
        sample_advice = {"recommend": "all_or_preview",
                         "reason": f"数据量 {n} 条（≤100），可直接全量；如需先验证 prompt 效果建议 --preview 3",
                         "command_hint": "建议先 --preview 3 看效果，再 --auto 全量"}
    else:
        sample_size = min(50, max(20, n // 20))
        sample_advice = {"recommend": "sample_first",
                         "reason": f"数据量 {n} 条（>100），强烈建议先抽样 {sample_size} 条试跑",
                         "command_hint": f"先 --sample-size {sample_size} --auto 抽样，确认质量后再全量"}

    # patch ①：首轮响应所需的就绪状态
    ready_for_labeling = bool(mapping.get("question") and mapping.get("answer")) \
                         or recommended_mode_val == "scene_labeling"

    # ── v3.6 加强：完整性状态总结（Agent 用这个字段决定回复是简短还是详细） ──
    primary_missing = [r for r, v in field_confirmation["primary"].items()
                       if v == "❌ 未识别"]
    optional_missing = [r for r, v in field_confirmation["primary"].items()
                        if v == "（无）"]
    aux_missing = [r for r, v in field_confirmation["auxiliary"].items()
                   if v in ("（无）", "（无 → 默认 GENERIC）")]

    if primary_missing:
        completeness_status = "BLOCKED"
        completeness_label = f"❌ 阻断：必需字段缺失 → {', '.join(primary_missing)}"
    elif optional_missing or aux_missing:
        completeness_status = "READY_WITH_GAPS"
        gaps = optional_missing + aux_missing
        completeness_label = f"⚠️  可打标但有缺口（不阻断）：{', '.join(gaps[:3])}"
    else:
        completeness_status = "FULLY_READY"
        completeness_label = "✅ 字段完整，可直接全量打标"

    report = {
        "file": str(file_path) if file_path else "",
        "total_rows": len(records),
        "total_columns": len(columns),
        "columns": columns,
        "ready_for_labeling": ready_for_labeling,
        "completeness_status": completeness_status,
        "completeness_label": completeness_label,
        "missing_optional": optional_missing + aux_missing,
        "field_mapping": field_mapping,
        "field_confirmation": field_confirmation,
        "sample_advice": sample_advice,
        "data_features": features,
        "domain": domain,
        "norm_task_family_dist": family_dist,
        "recommended_mode": recommended_mode_val,
        "recommend_reason": recommend_reason,
        "missing_required_fields": rec["missing_required_fields"],
        "actionable_hints": rec["actionable_hints"],
        "output_fields": output_fields_map.get(recommended_mode_val, []),
        "data_preview": preview_rows,
        "existing_labels": {
            "label_columns": label_columns,
            "already_labeled_count": already_labeled_count,
            "unlabeled_count": len(records) - already_labeled_count,
        },
        "available_templates": {
            "fin_rvec": {
                "description": "金融RVEC综合打标（场景+任务+RVEC标签+P等级+评分）",
                "builtin": True,
                "config": "fin_rvec_config.yaml",
                "applicable_when": "金融领域数据，有question+answer",
            },
            "med_rvec": {
                "description": "医学RVEC综合打标（M场景+任务+RVEC标签+P等级+评分）",
                "builtin": True,
                "config": "medical_rvec_config.yaml",
                "applicable_when": "医学领域数据，有question+answer",
            },
            "dataset_ingest": {
                "description": "评测集入库分类（场景+任务类型标注→上传Lumi）",
                "builtin": True,
                "config": "自动选择（医学/金融/通用）",
                "applicable_when": "原始评测集，仅有question/reference，无model_response",
            },
            "scene_labeling": {
                "description": "场景细分（多维度L1/L2/L3分类）",
                "builtin": False,
                "config": "scene_schema_example.yaml（示例，需自定义）",
                "applicable_when": "需要对数据按维度分类",
            },
            "error_attribution": {
                "description": "错误归因（错误类型+严重程度）",
                "builtin": False,
                "config": "error_schema_example.yaml（示例，需自定义）",
                "applicable_when": "需要分析模型回答的错误原因",
            },
        },
    }

    print(_json.dumps(report, ensure_ascii=False, indent=2))


# ── 领域自动检测（保留旧 API 供向后兼容；新代码请用 field_mapping.detect_finance_domain） ──

_FIN_KEYWORDS_DEFAULT = [
    "金融", "银行", "证券", "基金", "保险", "理财", "股票", "债券", "期货",
    "信贷", "贷款", "投资", "风控", "合规", "征信", "利率", "汇率", "资管",
    "财报", "审计", "融资", "信用卡", "理赔", "ETF", "IPO", "基金经理",
]


def detect_domain(records, sample_size=20, keywords=None):
    """旧 API：返回 ("finance"|"general", hit_ratio)。新代码请用 field_mapping.detect_finance_domain"""
    res = detect_finance_domain(records, sample_size=sample_size, keywords=keywords)
    return res["domain"], res["hit_ratio"]


def resolve_config_and_mode(args, records):
    """根据 --mode 和 --config 参数决定最终使用的配置和模式。

    v4 Phase 2 升级：
    1. 显式 --config → 直接使用该配置，mode 从配置文件读取
    2. --mode auto / 不指定 → 用 field_mapping.recommend_mode 智能判别
       - fin_rvec → fin_rvec_config.yaml
       - error_attribution → 提示用户提供 config，退出（无内置默认）
       - scene_labeling → 提示用户提供 scene_schema 或选用内置 fin_rvec
    3. --mode fin_rvec/scene_labeling/error_attribution → 显式指定，加载对应默认配置
    """
    if args.config:
        config = parse_config(args.config)
        mode = config.get("mode", "scene_labeling")
        return config, mode

    mode_arg = args.mode or "auto"

    if mode_arg == "auto":
        # ── v4 智能判别：综合考虑「字段特征 + 金融关键词 + 医学关键词 + 题型族」 ──
        rec = recommend_mode(records)
        recommended = rec["recommended_mode"]
        logger.info(f"🔍 智能判别: mode={recommended} | {rec['reason']}")
        if rec["family_dist"]:
            top_fams = list(rec["family_dist"].items())[:3]
            logger.info(f"   题型族 TOP3: " + ", ".join(f"{k}:{v}" for k, v in top_fams))

        if recommended == "fin_rvec":
            fin_config_path = CONFIG_DIR / "fin_rvec_config.yaml"
            if not fin_config_path.exists():
                raise FileNotFoundError(f"金融配置文件不存在: {fin_config_path}")
            config = parse_config(str(fin_config_path))
            return config, "fin_rvec"

        if recommended == "med_rvec":
            med_config_path = CONFIG_DIR / "medical_rvec_config.yaml"
            if not med_config_path.exists():
                raise FileNotFoundError(f"医学配置文件不存在: {med_config_path}")
            config = parse_config(str(med_config_path))
            return config, "med_rvec"

        if recommended == "dataset_ingest":
            # 评测集入库：根据数据领域自动选择 config
            from field_mapping import detect_medical_domain
            med_dom = detect_medical_domain(records)
            if med_dom["domain"] == "medical":
                config_path = CONFIG_DIR / "medical_rvec_config.yaml"
            else:
                config_path = CONFIG_DIR / "fin_rvec_config.yaml"
            config = parse_config(str(config_path))
            config["mode"] = "dataset_ingest"
            return config, "dataset_ingest"

        # 非 fin_rvec / med_rvec：要求用户显式提供 config
        print(f"\n{'='*70}")
        print(f"⚠️  智能判别建议模式: {recommended}")
        print(f"   原因: {rec['reason']}")
        for hint in rec.get("actionable_hints", []):
            print(f"   👉 {hint}")
        print(f"   当前未提供 --config，退出。可用如下命令指定配置：")
        print(f"     python run_tagging.py --config your_config.yaml --input {args.input}")
        print(f"   可参考以下模板创建配置：")
        print(f"     场景分类: {CONFIG_DIR / 'scene_schema_example.yaml'}")
        print(f"     错误归因: {CONFIG_DIR / 'error_schema_example.yaml'}")
        print(f"     金融RVEC: {CONFIG_DIR / 'fin_rvec_config.yaml'}")
        print(f"     医学RVEC: {CONFIG_DIR / 'medical_rvec_config.yaml'}")
        print(f"{'='*70}")
        sys.exit(1)

    elif mode_arg == "fin_rvec":
        fin_config_path = CONFIG_DIR / "fin_rvec_config.yaml"
        config = parse_config(str(fin_config_path))
        return config, "fin_rvec"

    elif mode_arg == "med_rvec":
        med_config_path = CONFIG_DIR / "medical_rvec_config.yaml"
        if not med_config_path.exists():
            raise FileNotFoundError(f"医学配置文件不存在: {med_config_path}")
        config = parse_config(str(med_config_path))
        return config, "med_rvec"

    elif mode_arg == "scene_labeling":
        scene_config_path = CONFIG_DIR / "scene_schema_example.yaml"
        config = parse_config(str(scene_config_path))
        return config, "scene_labeling"

    elif mode_arg == "dataset_ingest":
        # 评测集入库：根据数据领域选择对应的 scene/task schema
        from field_mapping import detect_finance_domain, detect_medical_domain
        fin_dom = detect_finance_domain(records)
        med_dom = detect_medical_domain(records)
        if med_dom["domain"] == "medical":
            config_path = CONFIG_DIR / "medical_rvec_config.yaml"
        elif fin_dom["domain"] == "finance":
            config_path = CONFIG_DIR / "fin_rvec_config.yaml"
        else:
            # 通用域：尝试医学→金融→兜底空配置
            config_path = CONFIG_DIR / "medical_rvec_config.yaml"
        config = parse_config(str(config_path))
        config["mode"] = "dataset_ingest"
        return config, "dataset_ingest"

    elif mode_arg == "error_attribution":
        error_config_path = CONFIG_DIR / "error_schema_example.yaml"
        config = parse_config(str(error_config_path))
        return config, "error_attribution"

    else:
        raise ValueError(f"未知模式: {mode_arg}，支持: auto / fin_rvec / med_rvec / dataset_ingest / scene_labeling / error_attribution")


def main():
    parser = argparse.ArgumentParser(description="评测集打标工具")
    # 数据源
    parser.add_argument("--config", default=None, help="打标配置文件路径(yaml/json)；不指定时配合 --mode auto 自动选择")
    parser.add_argument("--mode", default=None,
                        choices=["auto", "fin_rvec", "med_rvec", "dataset_ingest", "scene_labeling", "error_attribution"],
                        help="打标模式（默认auto自动检测领域）")
    parser.add_argument("--input", default=None, help="输入数据文件路径(jsonl/csv/xlsx)")
    parser.add_argument("--inspect", action="store_true", help="数据探查模式：分析数据结构并输出JSON报告，不执行打标")
    parser.add_argument("--preview", type=int, nargs="?", const=3, default=None,
                        help="预览模式：打标N条(默认3)并输出打标前后对比JSON，不写文件")
    parser.add_argument("--lumi-dataset", default=None, nargs="+", help="从 Lumi Dataset 拉取（支持多个）")
    parser.add_argument("--output", default="output", help="输出目录")
    parser.add_argument("--output-format", default=None, help="输出格式(jsonl/csv/xlsx)，默认取配置文件中的值")

    # LLM
    parser.add_argument("--model", default=None, help="LLM 模型名称")
    parser.add_argument("--api-key", default=None, help="API Key")
    parser.add_argument("--base-url", default=None, help="API Base URL")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--endpoint", default=None, help="预定义端点 (iquest/zerail)")
    parser.add_argument("--auto-detect", action="store_true", help="自动探测可用端点")

    # 并发 & Lumi
    parser.add_argument("--workers", type=int, default=5, help="并发线程数（默认5）")
    parser.add_argument("--no-lumi", action="store_true", help="禁用 Lumi 上报")
    parser.add_argument("--lumi-upload", action="store_true", help="打标完成后将结果回写到 Lumi Dataset metadata")
    parser.add_argument("--lumi-create-dataset", default=None, help="将本地文件上传为 Lumi Dataset（指定名称）")
    parser.add_argument("--lumi-public-key", default=None)
    parser.add_argument("--lumi-secret-key", default=None)
    parser.add_argument("--lumi-base-url", default=None)
    parser.add_argument("--sample-size", type=int, default=None, help="随机抽样数量")

    # 报告生成
    parser.add_argument("--report", action="store_true",
                        help="打标完成后自动生成 HTML 分析报告")
    parser.add_argument("--report-title", default=None, help="报告标题（默认自动生成）")

    # ── 配置校验子命令（v4 Phase 2）──
    parser.add_argument("--validate-config", default=None, nargs="+",
                        help="校验配置文件合法性（不调用 LLM 不读数据）。支持多文件，例：--validate-config a.yaml b.json")
    parser.add_argument("--strict", action="store_true",
                        help="--validate-config 时启用严格模式：有 warning 也返回非零码")

    args = parser.parse_args()

    # ── 配置校验入口（最优先，不需要 input/数据） ──
    if args.validate_config:
        import json as _json
        from config_validator import validate_file
        reports = []
        overall_ok = True
        for c in args.validate_config:
            rep = validate_file(c)
            reports.append(rep)
            if not rep["ok"]:
                overall_ok = False
            if args.strict and rep["warnings"]:
                overall_ok = False

        # 文本展示
        for rep in reports:
            print(f"\n── 📋 {rep['source']} (mode={rep['mode']}) ──")
            print(f"   {rep['summary']}")
            for e in rep["errors"]:
                print(f"   ❌ {e}")
            for w in rep["warnings"]:
                print(f"   ⚠️  {w}")
        print(f"\n{'='*70}")
        print(_json.dumps({"reports": reports, "overall_ok": overall_ok},
                          ensure_ascii=False, indent=2))
        sys.exit(0 if overall_ok else 1)

    if not args.input and not args.lumi_dataset:
        parser.error("请指定 --input 或 --lumi-dataset 至少一个数据源")
    if not args.config and not args.mode and not args.inspect and args.preview is None:
        args.mode = "auto"  # 默认自动检测

    # 1. Lumi（inspect 模式不需要 Lumi）
    lumi = None
    if not args.no_lumi and not args.inspect:
        _ensure_imports()
        lumi = LumiClient(public_key=args.lumi_public_key, secret_key=args.lumi_secret_key,
                          base_url=args.lumi_base_url, enabled=True)
        if not lumi.enabled:
            logger.warning("Lumi 连接失败，离线模式运行")
            lumi = None

    # 2. 读取数据
    if args.lumi_dataset:
        if not lumi:
            _ensure_imports()
            parser.error("使用 --lumi-dataset 需要 Lumi 连接可用")
        records = read_from_lumi(args.lumi_dataset, lumi)
    else:
        logger.info(f"读取数据: {args.input}")
        records = read_data(args.input)
    logger.info(f"共 {len(records)} 条数据")

    # 2.5 探查模式：输出数据结构分析后退出
    if args.inspect:
        inspect_data(records, args.input)
        return

    if args.sample_size and args.sample_size < len(records):
        import random
        random.seed(42)
        records = random.sample(records, args.sample_size)
        logger.info(f"随机抽样 {args.sample_size} 条")

    # 4. 解析配置（支持 auto 模式自动选择）
    config, mode = resolve_config_and_mode(args, records)
    # 输出格式：优先用户指定 > 跟随输入文件格式 > 配置文件 > jsonl
    if args.output_format:
        output_format = args.output_format
    elif args.input:
        input_ext = Path(args.input).suffix.lower().lstrip(".")
        output_format = input_ext if input_ext in ("csv", "jsonl", "xlsx", "xls") else config.get("output_format", "jsonl")
    else:
        output_format = config.get("output_format", "jsonl")
    # xlsx/xls 统一为 xlsx
    if output_format in ("xls",):
        output_format = "xlsx"
    retry_limit = config.get("retry_limit", 3)

    # 5. LLM — 优先级: 显式参数 > 环境变量 > 预置端点自动探测
    _ensure_imports()
    if args.auto_detect:
        llm = LLMClient.auto_detect(temperature=args.temperature)
    elif args.endpoint:
        llm = LLMClient(endpoint_name=args.endpoint, temperature=args.temperature)
    elif args.api_key or args.base_url or os.getenv("OPENAI_API_KEY"):
        # 用户通过参数或环境变量提供了凭据
        llm = LLMClient(api_key=args.api_key, base_url=args.base_url,
                         model=args.model or config.get("model", "gpt-4o"), temperature=args.temperature)
    else:
        # 未提供任何 LLM 配置，自动探测预置端点（iquest/zerail）
        logger.info("未检测到 LLM 凭据，自动探测预置端点...")
        try:
            llm = LLMClient.auto_detect(temperature=args.temperature)
        except RuntimeError:
            print(f"\n{'='*70}")
            print("❌ 无法连接到任何 LLM 端点！请通过以下方式之一提供 LLM 配置：")
            print("   1. 设置环境变量: OPENAI_API_KEY + OPENAI_BASE_URL")
            print("   2. 传入参数: --api-key <key> --base-url <url>")
            print("   3. 使用预置端点: --endpoint iquest 或 --endpoint zerail")
            print("   4. 自动探测: --auto-detect")
            print(f"{'='*70}")
            sys.exit(1)

    # 6. 构建 run_name
    input_name = Path(args.input).stem if args.input else (args.lumi_dataset[0] if args.lumi_dataset else "lumi")
    run_name = f"tagging_{mode}_{input_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    dataset_name = args.lumi_dataset[0] if args.lumi_dataset else ""

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    common_kwargs = dict(max_workers=args.workers, lumi_client=lumi, dataset_name=dataset_name, run_name=run_name)

    # ── 预览模式：打标少量样本，输出前后对比 JSON ──
    if args.preview is not None:
        preview_n = min(args.preview, len(records))
        preview_records = records[:preview_n]
        logger.info(f"🔍 预览模式：打标 {preview_n} 条")

        # 构建 labeler（workers=1 串行打标）
        preview_kwargs = dict(max_workers=1, lumi_client=None, dataset_name="", run_name=run_name)
        if mode == "fin_rvec":
            filter_expr = config.get("badcase_filter", "")
            if filter_expr:
                from utils import apply_badcase_filter as _abf
                preview_records, _ = _abf(preview_records, filter_expr)
                preview_records = preview_records[:preview_n]
            labeler = FinRvecLabeler(llm, config, config.get("retry_limit", 3), **preview_kwargs)
        elif mode == "med_rvec":
            filter_expr = config.get("badcase_filter", "")
            if filter_expr:
                from utils import apply_badcase_filter as _abf
                preview_records, _ = _abf(preview_records, filter_expr)
                preview_records = preview_records[:preview_n]
            labeler = MedRvecLabeler(llm, config, config.get("retry_limit", 3), **preview_kwargs)
        elif mode == "scene_labeling":
            labeler = SceneLabeler(llm, config["scene_schema"], config.get("retry_limit", 3), **preview_kwargs)
        elif mode == "error_attribution":
            filter_expr = config.get("badcase_filter", "")
            if filter_expr:
                from utils import apply_badcase_filter as _abf
                preview_records, _ = _abf(preview_records, filter_expr)
                preview_records = preview_records[:preview_n]
            labeler = ErrorLabeler(llm, config["error_schema"], config.get("severity_schema"),
                                   config.get("retry_limit", 3), **preview_kwargs)
        elif mode == "dataset_ingest":
            from field_mapping import detect_medical_domain, detect_finance_domain
            med_dom = detect_medical_domain(preview_records)
            fin_dom = detect_finance_domain(preview_records)
            domain = "medical" if med_dom["domain"] == "medical" else ("finance" if fin_dom["domain"] == "finance" else "general")
            labeler = DatasetIngestLabeler(llm, config, config.get("retry_limit", 3), domain=domain, **preview_kwargs)
        else:
            raise ValueError(f"未知模式: {mode}")

        success_prev, failed_prev = labeler.run(preview_records)

        # 构建前后对比
        import json as _json
        comparisons = []
        # 提取关键字段名
        key_roles = {role: fm["column"] for role, fm in _build_field_mapping(records).items()}
        label_keys = [k for k in (success_prev[0].keys() if success_prev else [])
                      if k.startswith("label_") or k.startswith("scene_") or k.startswith("error_")]

        for item in success_prev:
            before = {}
            for role, col in key_roles.items():
                val = str(item.get(col, ""))
                before[f"{role}"] = val[:150] + ("..." if len(val) > 150 else "")
            after = {k: str(item.get(k, ""))[:200] for k in label_keys}
            after["label_status"] = item.get("label_status", "")
            comparisons.append({"before": before, "after": after})

        preview_report = {
            "mode": mode,
            "model": llm.model,
            "preview_count": len(success_prev),
            "failed_count": len(failed_prev),
            "label_fields": label_keys,
            "comparisons": comparisons,
        }
        print(_json.dumps(preview_report, ensure_ascii=False, indent=2))
        return

    print(f"\n{'='*70}")
    print(f"🚀 打标任务: {mode}")
    print(f"📊 数据量: {len(records)} | 并发: {args.workers}")
    print(f"📌 批次: {run_name}")
    print(f"🤖 模型: {llm.model}")
    print(f"📡 Lumi: {'启用' if lumi and lumi.enabled else '离线'}")
    print(f"{'='*70}\n")

    # 6. 执行打标
    if mode == "scene_labeling":
        labeler = SceneLabeler(llm, config["scene_schema"], retry_limit, **common_kwargs)
        success, failed = labeler.run(records)
        out_file = output_dir / f"{input_name}_scene_labeled.{output_format}"
        fail_file = output_dir / f"{input_name}_scene_failed.{output_format}"

    elif mode == "error_attribution":
        filter_expr = config.get("badcase_filter", "")
        badcases, skipped = apply_badcase_filter(records, filter_expr)
        logger.info(f"Badcase 筛选: {len(badcases)} 条命中, {len(skipped)} 条跳过")

        labeler = ErrorLabeler(llm, config["error_schema"], config.get("severity_schema"),
                               retry_limit, **common_kwargs)
        success, failed = labeler.run(badcases)

        for r in skipped:
            r["label_status"] = "skipped"
        success = success + skipped

        out_file = output_dir / f"{input_name}_error_labeled.{output_format}"
        fail_file = output_dir / f"{input_name}_error_failed.{output_format}"

    elif mode == "fin_rvec":
        # 金融 RVEC 综合打标：F场景 + T任务 + RVEC标签 + P等级 + 评分
        # 可选 badcase 过滤（仅打标 badcase）
        filter_expr = config.get("badcase_filter", "")
        if filter_expr:
            badcases, skipped = apply_badcase_filter(records, filter_expr)
            logger.info(f"Badcase 筛选: {len(badcases)} 条命中, {len(skipped)} 条跳过")
        else:
            badcases, skipped = records, []

        # 识别已人工标注的记录（label_status == done 或 label_fin_scene 非空）
        already_labeled = []
        need_label = []
        for r in badcases:
            has_label = (str(r.get("label_status", "")).strip() == "done"
                         or str(r.get("label_fin_scene", "")).strip()
                         or str(r.get("label_rvec_primary", "")).strip())
            if has_label:
                r["label_status"] = r.get("label_status", "done")
                already_labeled.append(r)
            else:
                need_label.append(r)

        if already_labeled:
            logger.info(f"📌 检测到 {len(already_labeled)} 条已有标注（保留不覆盖），待打标: {len(need_label)}")

        labeler = FinRvecLabeler(llm, config, retry_limit, **common_kwargs)
        success, failed = labeler.run(need_label)

        # 合并：已标注 + 新打标 + 跳过
        for r in skipped:
            r["label_status"] = "skipped"
        success = already_labeled + success + skipped

        out_file = output_dir / f"{input_name}_rvec_labeled.{output_format}"
        fail_file = output_dir / f"{input_name}_rvec_failed.{output_format}"

    elif mode == "med_rvec":
        # 医学 RVEC 综合打标：M场景 + T任务 + RVEC标签 + P等级 + 评分
        filter_expr = config.get("badcase_filter", "")
        if filter_expr:
            badcases, skipped = apply_badcase_filter(records, filter_expr)
            logger.info(f"Badcase 筛选: {len(badcases)} 条命中, {len(skipped)} 条跳过")
        else:
            badcases, skipped = records, []

        # 识别已人工标注的记录（label_status == done 或 label_med_scene 非空）
        already_labeled = []
        need_label = []
        for r in badcases:
            has_label = (str(r.get("label_status", "")).strip() == "done"
                         or str(r.get("label_med_scene", "")).strip()
                         or str(r.get("label_rvec_primary", "")).strip())
            if has_label:
                r["label_status"] = r.get("label_status", "done")
                already_labeled.append(r)
            else:
                need_label.append(r)

        if already_labeled:
            logger.info(f"📌 检测到 {len(already_labeled)} 条已有标注（保留不覆盖），待打标: {len(need_label)}")

        labeler = MedRvecLabeler(llm, config, retry_limit, **common_kwargs)
        success, failed = labeler.run(need_label)

        # 合并：已标注 + 新打标 + 跳过
        for r in skipped:
            r["label_status"] = "skipped"
        success = already_labeled + success + skipped

        out_file = output_dir / f"{input_name}_med_rvec_labeled.{output_format}"
        fail_file = output_dir / f"{input_name}_med_rvec_failed.{output_format}"

    elif mode == "dataset_ingest":
        # 评测集入库：分类 + 上传 Lumi
        from field_mapping import detect_finance_domain, detect_medical_domain
        med_dom = detect_medical_domain(records)
        fin_dom = detect_finance_domain(records)
        if med_dom["domain"] == "medical":
            detected_domain = "medical"
        elif fin_dom["domain"] == "finance":
            detected_domain = "finance"
        else:
            detected_domain = "general"
        logger.info(f"📋 评测集入库 | 检测领域: {detected_domain} | 数据量: {len(records)}")

        labeler = DatasetIngestLabeler(llm, config, retry_limit, domain=detected_domain, **common_kwargs)
        success, failed = labeler.run(records)

        out_file = output_dir / f"{input_name}_ingest_classified.{output_format}"
        fail_file = output_dir / f"{input_name}_ingest_failed.{output_format}"

        # 自动上传到 Lumi（如果指定了 --lumi-create-dataset 或启用了 lumi）
        lumi_dataset_name = args.lumi_create_dataset or f"{detected_domain}_{input_name}"
        if lumi and lumi.enabled:
            logger.info(f"📤 分类完成，上传到 Lumi Dataset: {lumi_dataset_name}")
            uploaded = 0
            for record in success:
                item = labeler.build_lumi_item(record)
                try:
                    lumi._sdk_client.create_dataset_item(
                        dataset_name=lumi_dataset_name,
                        id=item["id"] or None,
                        input=item["input"],
                        expected_output=item["expected_output"],
                        metadata=item["metadata"],
                    )
                    uploaded += 1
                except Exception as e:
                    logger.warning(f"上传失败: {e}")
            try:
                lumi._sdk_client.flush()
            except Exception:
                pass
            logger.info(f"📤 Lumi 上传完成: {uploaded}/{len(success)} 条")

        # 打印分类统计
        scene_dist = {}
        task_dist = {}
        for r in success:
            s = str(r.get("label_scene", ""))[:3]  # 取编码前3字符如 M01
            t = str(r.get("label_task_type", ""))[:3]
            scene_dist[s] = scene_dist.get(s, 0) + 1
            task_dist[t] = task_dist.get(t, 0) + 1

        print(f"\n{'='*70}")
        print(f"📋 评测集入库分类完成!")
        print(f"   领域: {detected_domain} | 总计: {len(success)} 条")
        print(f"   场景分布 TOP5: {dict(sorted(scene_dist.items(), key=lambda x:-x[1])[:5])}")
        print(f"   任务分布 TOP5: {dict(sorted(task_dist.items(), key=lambda x:-x[1])[:5])}")
        if lumi and lumi.enabled:
            print(f"   📤 已上传 Lumi Dataset: {lumi_dataset_name}")
        print(f"   📄 本地输出: {out_file}")
        print(f"{'='*70}")

    else:
        raise ValueError(f"未知的打标模式: {mode}")

    # 7. 输出
    if success:
        write_data(success, str(out_file), output_format)
        logger.info(f"结果已写入: {out_file}")
    else:
        logger.warning(f"⚠️ 无成功记录，未写入输出文件")

    if failed:
        write_data(failed, str(fail_file), output_format)
        logger.info(f"失败条目已写入: {fail_file}")

    done_count = len([r for r in success if r.get('label_status') == 'done'])
    skip_count = len([r for r in success if r.get('label_status') == 'skipped'])
    preserved_count = len([r for r in success if r.get('label_status') == 'done'
                           and r.get('labeled_at', '') == ''])

    # 8. Lumi 回写：将打标结果写回 Dataset metadata
    lumi_wb_count = 0
    if args.lumi_upload and lumi and lumi.enabled:
        wb_dataset = dataset_name or args.lumi_create_dataset or f"tagging_{input_name}"
        logger.info(f"📤 回写标签到 Lumi Dataset: {wb_dataset}")
        lumi_wb_count = lumi.write_back_labels(success, wb_dataset)

    # 9. Lumi 创建 Dataset：将本地数据上传为 Lumi Dataset
    if args.lumi_create_dataset and lumi and lumi.enabled:
        logger.info(f"📤 上传本地数据到 Lumi Dataset: {args.lumi_create_dataset}")
        lumi.upload_dataset_items(records, args.lumi_create_dataset)

    # 10. Flush pending links
    link_ok, link_total = 0, 0
    if lumi and lumi.enabled:
        link_ok, link_total = lumi.flush_pending_links()

    print(f"\n{'='*70}")
    print(f"🎉 打标完成!")
    print(f"   ✅ 成功: {done_count}")
    if preserved_count:
        print(f"   📌 保留人工标注: {preserved_count}")
    print(f"   ⏭️  跳过: {skip_count}")
    print(f"   ❌ 失败: {len(failed)}")
    if success:
        print(f"   📄 输出: {out_file}")
    if failed:
        print(f"   📄 失败: {fail_file}")
    if lumi_wb_count:
        print(f"   📝 Lumi标签回写: {lumi_wb_count} 条")
    if link_total:
        print(f"   🔗 Lumi Run绑定: {link_ok}/{link_total}")
    # Lumi 平台链接
    if lumi and lumi.enabled:
        lumi_base = lumi.base_url.rstrip("/")
        ds_name = dataset_name or args.lumi_create_dataset or ""
        if ds_name:
            print(f"   🌐 Lumi Dataset: {lumi_base}/datasets/{ds_name}")
        print(f"   🌐 Lumi Traces: {lumi_base}/traces?tags={run_name}")
    print(f"{'='*70}")

    # 11. 生成 HTML 报告（--report 参数）
    if args.report and success and mode in ("fin_rvec", "med_rvec"):
        from report_generator import aggregate_report_data, generate_html_report
        report_data = aggregate_report_data(success)
        domain_label = "医学" if mode == "med_rvec" else "金融"
        report_title = args.report_title or f"{domain_label} RVEC 打标分析报告 — {input_name}"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = str(output_dir / f"rvec_report_{ts}.html")
        generate_html_report(report_data, report_path, report_title)
        print(f"   📊 HTML 报告: {report_path}")


if __name__ == "__main__":
    main()
