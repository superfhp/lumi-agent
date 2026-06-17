"""配置校验：验证 fin_rvec / scene / error_attribution 三类配置文件的合法性。

校验维度：
1. JSON/YAML 语法是否合法（由 schema_parser.parse_config 保证）
2. 必填字段是否齐全（如 mode、对应的 schema）
3. fin_rvec：F/T schema 编码格式、rvec_schema 完整性、scoring_rules 覆盖率
4. prompt 占位符（system_prompt 必须含 {schema_text}，user 模板必须含 {question}/{answer} 等）
5. task_family_rules 是否每个 family 都有 match_norm_task

设计原则：
- 输出结构化 JSON，包含 errors（致命）/ warnings（建议）/ summary
- 不依赖 fin_rvec_tag.py（避免循环依赖）
- 命令行入口：直接执行此文件即可校验
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List


# ════════════════════════════════════════════════════════════
# 核心校验逻辑
# ════════════════════════════════════════════════════════════

def validate_config(config: Dict[str, Any], source: str = "") -> Dict[str, Any]:
    """校验单份配置文件，返回结构化报告。

    Returns:
        {
          "source": 配置文件路径,
          "mode": 配置 mode,
          "errors": [str],    # 致命错误（必须修复）
          "warnings": [str],  # 警告（建议修复）
          "summary": str,
          "ok": bool,
        }
    """
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(config, dict):
        errors.append(f"配置根对象必须是 dict，实际是 {type(config).__name__}")
        return _build_report(source, "?", errors, warnings)

    mode = str(config.get("mode", "")).strip()
    if not mode:
        warnings.append("缺少 mode 字段，将由 run_tagging.py 自动判别（建议显式标注）")

    # 按 mode 路由不同的校验规则
    if mode == "med_rvec" or _looks_like_med_rvec(config):
        _validate_med_rvec(config, errors, warnings)
    elif mode == "fin_rvec" or _looks_like_fin_rvec(config):
        _validate_fin_rvec(config, errors, warnings)
    elif mode == "scene_labeling" or "scene_schema" in config:
        _validate_scene_labeling(config, errors, warnings)
    elif mode == "error_attribution" or "error_schema" in config:
        _validate_error_attribution(config, errors, warnings)
    else:
        warnings.append(f"无法识别配置类型（mode={mode}）；仅做基础校验，跳过 schema 详检")

    # 通用：retry_limit / output_format
    if "retry_limit" in config:
        rl = config["retry_limit"]
        if not isinstance(rl, int) or rl < 0 or rl > 10:
            warnings.append(f"retry_limit 建议 0-10 整数，当前 {rl}")

    return _build_report(source, mode or "unknown", errors, warnings)


def _looks_like_fin_rvec(config: Dict) -> bool:
    """看不出 mode 时，根据特征字段判断是否 fin_rvec 配置"""
    return any(k in config for k in ("fin_scene_schema", "task_type_schema", "rvec_schema"))


def _looks_like_med_rvec(config: Dict) -> bool:
    """根据特征字段判断是否 med_rvec 配置"""
    return "med_scene_schema" in config


# ── fin_rvec 配置校验 ──

_F_CODE_RE = re.compile(r"^F(\d{2}|99)$")
_T_CODE_RE = re.compile(r"^T(\d{2}|99)$")
_M_CODE_RE = re.compile(r"^M(\d{2}|99)$")
_RVEC_CODE_RE = re.compile(r"^[RVEC](-[A-Z]+)?-\d+$")


def _validate_fin_rvec(config: Dict, errors: List[str], warnings: List[str]):
    # F 场景
    fs = config.get("fin_scene_schema", [])
    if not isinstance(fs, list) or len(fs) == 0:
        errors.append("fin_scene_schema 缺失或为空")
    else:
        for i, item in enumerate(fs):
            if not isinstance(item, dict):
                errors.append(f"fin_scene_schema[{i}] 不是 dict")
                continue
            code = str(item.get("code", ""))
            if not _F_CODE_RE.match(code):
                errors.append(f"fin_scene_schema[{i}] 编码非法: {code!r}（应符合 F\\d{{2}} 或 F99）")
            if not item.get("name"):
                warnings.append(f"fin_scene_schema[{i}] {code} 缺少 name")

    # T 任务
    ts = config.get("task_type_schema", [])
    if not isinstance(ts, list) or len(ts) == 0:
        errors.append("task_type_schema 缺失或为空")
    else:
        for i, item in enumerate(ts):
            if not isinstance(item, dict):
                errors.append(f"task_type_schema[{i}] 不是 dict")
                continue
            code = str(item.get("code", ""))
            if not _T_CODE_RE.match(code):
                errors.append(f"task_type_schema[{i}] 编码非法: {code!r}（应符合 T\\d{{2}} 或 T99）")

    # RVEC schema
    rvec = config.get("rvec_schema", {})
    if not isinstance(rvec, dict) or len(rvec) == 0:
        errors.append("rvec_schema 缺失或为空")
    else:
        # 必须至少包含 R / V / E / C 四类
        prefixes = set()
        total_tags = 0
        for group_key, tags in rvec.items():
            if not isinstance(tags, list):
                warnings.append(f"rvec_schema['{group_key}'] 不是 list，已跳过")
                continue
            for tag in tags:
                if not isinstance(tag, dict):
                    continue
                code = str(tag.get("code", ""))
                total_tags += 1
                if code:
                    prefixes.add(code[0])
                    if not _RVEC_CODE_RE.match(code):
                        warnings.append(f"rvec_schema 标签编码格式存疑: {code!r}（建议 R-XXX-N / V-XXX-N 等）")
        if total_tags == 0:
            errors.append("rvec_schema 中没有任何标签")
        for needed in ("R", "V", "E", "C"):
            if needed not in prefixes:
                warnings.append(f"rvec_schema 缺少 {needed} 类标签（按 RVEC 体系应当四类齐备）")

    # 严重度
    sev = config.get("severity_schema", {})
    if not isinstance(sev, dict) or "levels" not in sev:
        errors.append("severity_schema.levels 缺失（应包含 P0/P1/P2）")
    else:
        levels = sev.get("levels", [])
        for needed in ("P0", "P1", "P2"):
            if needed not in levels:
                warnings.append(f"severity_schema.levels 建议包含 {needed}")

    # 评分规则
    if not config.get("scoring_rules"):
        warnings.append("scoring_rules 缺失（脚本会用兜底规则，建议显式定义）")

    # 题型族规则（v4 加入）
    if "task_family_rules" in config:
        rules = config["task_family_rules"]
        if not isinstance(rules, dict):
            errors.append("task_family_rules 不是 dict")
        else:
            for fam, cfg in rules.items():
                if fam.startswith("_"):
                    continue
                if not isinstance(cfg, dict):
                    warnings.append(f"task_family_rules['{fam}'] 不是 dict")
                    continue
                if "match_norm_task" not in cfg:
                    warnings.append(f"task_family_rules['{fam}'] 缺少 match_norm_task（无法路由数据）")

    # F×T 交叉规则
    if "ft_cross_validation" in config:
        ftcv = config["ft_cross_validation"]
        rules = ftcv.get("rules", [])
        if not isinstance(rules, list):
            warnings.append("ft_cross_validation.rules 不是 list")


# ── med_rvec 配置校验 ──

def _validate_med_rvec(config: Dict, errors: List[str], warnings: List[str]):
    """医学 RVEC 配置校验（与 fin_rvec 同构，场景 key 为 med_scene_schema，编码为 M\\d{2}）"""
    # M 场景
    ms = config.get("med_scene_schema", [])
    if not isinstance(ms, list) or len(ms) == 0:
        errors.append("med_scene_schema 缺失或为空")
    else:
        for i, item in enumerate(ms):
            if not isinstance(item, dict):
                errors.append(f"med_scene_schema[{i}] 不是 dict")
                continue
            code = str(item.get("code", ""))
            if not _M_CODE_RE.match(code):
                errors.append(f"med_scene_schema[{i}] 编码非法: {code!r}（应符合 M\\d{{2}} 或 M99）")
            if not item.get("name"):
                warnings.append(f"med_scene_schema[{i}] {code} 缺少 name")

    # T 任务（复用同一逻辑）
    ts = config.get("task_type_schema", [])
    if not isinstance(ts, list) or len(ts) == 0:
        errors.append("task_type_schema 缺失或为空")
    else:
        for i, item in enumerate(ts):
            if not isinstance(item, dict):
                errors.append(f"task_type_schema[{i}] 不是 dict")
                continue
            code = str(item.get("code", ""))
            if not _T_CODE_RE.match(code):
                errors.append(f"task_type_schema[{i}] 编码非法: {code!r}（应符合 T\\d{{2}} 或 T99）")

    # RVEC schema
    rvec = config.get("rvec_schema", {})
    if not isinstance(rvec, dict) or len(rvec) == 0:
        errors.append("rvec_schema 缺失或为空")
    else:
        prefixes = set()
        total_tags = 0
        for group_key, tags in rvec.items():
            if not isinstance(tags, list):
                warnings.append(f"rvec_schema['{group_key}'] 不是 list，已跳过")
                continue
            for tag in tags:
                if not isinstance(tag, dict):
                    continue
                code = str(tag.get("code", ""))
                total_tags += 1
                if code:
                    prefixes.add(code[0])
                    if not _RVEC_CODE_RE.match(code):
                        warnings.append(f"rvec_schema 标签编码格式存疑: {code!r}（建议 R-XXX-N / V-XXX-N 等）")
        if total_tags == 0:
            errors.append("rvec_schema 中没有任何标签")
        for needed in ("R", "V", "E", "C"):
            if needed not in prefixes:
                warnings.append(f"rvec_schema 缺少 {needed} 类标签（按 RVEC 体系应当四类齐备）")

    # 严重度
    sev = config.get("severity_schema", {})
    if not isinstance(sev, dict) or "levels" not in sev:
        errors.append("severity_schema.levels 缺失（应包含 P0/P1/P2）")
    else:
        levels = sev.get("levels", [])
        for needed in ("P0", "P1", "P2"):
            if needed not in levels:
                warnings.append(f"severity_schema.levels 建议包含 {needed}")

    # 评分规则
    if not config.get("scoring_rules"):
        warnings.append("scoring_rules 缺失（脚本会用兜底规则，建议显式定义）")

    # M×T 交叉规则
    if "mt_cross_validation" in config:
        mtcv = config["mt_cross_validation"]
        rules = mtcv.get("rules", [])
        if not isinstance(rules, list):
            warnings.append("mt_cross_validation.rules 不是 list")


# ── scene_labeling 配置校验 ──

def _validate_scene_labeling(config: Dict, errors: List[str], warnings: List[str]):
    schema = config.get("scene_schema", [])
    if not isinstance(schema, list) or len(schema) == 0:
        errors.append("scene_schema 缺失或为空（应是 [{dimension, tree:[...]}]）")
        return
    for i, dim in enumerate(schema):
        if not isinstance(dim, dict):
            errors.append(f"scene_schema[{i}] 不是 dict")
            continue
        if not dim.get("dimension"):
            errors.append(f"scene_schema[{i}] 缺少 dimension")
        tree = dim.get("tree", [])
        if not isinstance(tree, list) or len(tree) == 0:
            warnings.append(f"scene_schema[{i}].{dim.get('dimension', '?')} tree 为空")


# ── error_attribution 配置校验 ──

def _validate_error_attribution(config: Dict, errors: List[str], warnings: List[str]):
    es = config.get("error_schema", [])
    if not isinstance(es, list) or len(es) == 0:
        errors.append("error_schema 缺失或为空（应是 [{name, description}]）")
        return
    for i, item in enumerate(es):
        if not isinstance(item, dict) or not item.get("name"):
            errors.append(f"error_schema[{i}] 缺少 name")

    sev = config.get("severity_schema")
    if sev:
        if not isinstance(sev, dict) or "scale" not in sev:
            warnings.append("severity_schema 应包含 scale（如 [1,2,3]）")

    bf = config.get("badcase_filter", "")
    if bf and not isinstance(bf, str):
        warnings.append("badcase_filter 应是字符串表达式（如 'accuracy == 0'）")


# ── prompt 占位符校验（在 fin_rvec/rvec/prompts.yaml 加载后单独调用） ──

def validate_prompt_placeholders(prompts: Dict[str, Any]) -> Dict[str, Any]:
    """校验 prompts.yaml 加载结果的占位符完整性。

    Args:
        prompts: 形如 {"system_prompt": str, "user_prompt_header": str,
                       "family_templates": {family: str}}

    Returns:
        {"errors": [...], "warnings": [...], "ok": bool}
    """
    errors: List[str] = []
    warnings: List[str] = []

    sys_prompt = prompts.get("system_prompt", "")
    if isinstance(sys_prompt, str) and sys_prompt:
        for placeholder in ("{schema_text}",):
            if placeholder not in sys_prompt:
                errors.append(f"system_prompt 缺少必备占位符 {placeholder}")
        if "{few_shot_text}" not in sys_prompt:
            warnings.append("system_prompt 不含 {few_shot_text}，将无法注入 few-shot 样例")
    else:
        warnings.append("prompts.system_prompt 未设置，将回退到内置 SYSTEM_PROMPT_TEMPLATE")

    family_templates = prompts.get("family_templates") or {}
    needed_placeholders = ("{question}", "{answer}")
    for fam in ("QA_CHOICE", "SENTIMENT", "REPORT_EVAL", "LONG_GEN", "GENERIC"):
        tpl = family_templates.get(fam)
        if not tpl:
            warnings.append(f"family_templates['{fam}'] 未提供，将回退到内置常量")
            continue
        if not isinstance(tpl, str):
            errors.append(f"family_templates['{fam}'] 不是字符串")
            continue
        for ph in needed_placeholders:
            if ph not in tpl:
                warnings.append(f"family_templates['{fam}'] 缺少占位符 {ph}（可能影响渲染）")

    return {
        "errors": errors,
        "warnings": warnings,
        "ok": len(errors) == 0,
    }


# ════════════════════════════════════════════════════════════
# 报告组装 + CLI 入口
# ════════════════════════════════════════════════════════════

def _build_report(source: str, mode: str, errors: List[str], warnings: List[str]) -> Dict[str, Any]:
    ok = len(errors) == 0
    if ok and not warnings:
        summary = "✅ 配置合法，无警告"
    elif ok:
        summary = f"✅ 配置合法，但有 {len(warnings)} 个警告（建议关注）"
    else:
        summary = f"❌ 配置存在 {len(errors)} 个致命错误，必须修复"
    return {
        "source": source,
        "mode": mode,
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
        "ok": ok,
    }


def validate_file(path: str) -> Dict[str, Any]:
    """从文件路径加载并校验配置。"""
    p = Path(path)
    if not p.exists():
        return _build_report(path, "?", [f"文件不存在: {path}"], [])
    try:
        # 复用 schema_parser 已有的解析能力（支持 yaml/json/md）
        from schema_parser import parse_config
        config = parse_config(str(p))
    except Exception as e:
        return _build_report(path, "?", [f"配置文件解析失败: {e}"], [])
    return validate_config(config, source=str(p))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="配置文件合法性校验")
    parser.add_argument("config", nargs="+", help="一个或多个配置文件路径")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式：有 warning 也返回非零退出码")
    args = parser.parse_args()

    overall_ok = True
    reports = []
    for c in args.config:
        rep = validate_file(c)
        reports.append(rep)
        if not rep["ok"]:
            overall_ok = False
        if args.strict and rep["warnings"]:
            overall_ok = False

    print(json.dumps({"reports": reports, "overall_ok": overall_ok},
                     ensure_ascii=False, indent=2))
    raise SystemExit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
