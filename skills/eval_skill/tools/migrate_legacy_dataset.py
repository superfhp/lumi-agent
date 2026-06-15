"""把旧 Lumi dataset 迁移成 v2 spec，写入新 Lumi dataset（可选同时导出本地 jsonl 备份）。

旧字段兼容（基于 for_eval/dataset_example.md case 1-7 + DESIGN §3.5/§3.6 强制治理项）：

- input.prompt / input 直接是 string                                → input.prompt
- input.question + input.options + input.background_context         → 结构化字段透传
  其中 input.background_context                                     → input.background（强制）
- input.turn (轮次号) + 同一 dialog_group_key                        → 聚合成 input.turns[]
- expected.correct_answer / ground_truth / sentiment_id / answer    → expected.answer
- expected.reference_answer / reference                             → expected.answer（RVEC 通用领域）
- expected.official_explanation / explanation / analysis            → expected.explanation
- expected.reasoning / reasoning_ref / cot                          → expected.reasoning_ref
- expected.rubric / criteria / scoring_points                       → expected.rubric（含 RVEC 通用领域）
- expected.expected_md / reference_md                               → expected.expected_md
- expected.sentiment_id 答案                                         → 同时补 metadata.label_map
- expected.预期易错信号 (字符串)                                     → metadata.expected_signals (list[tag_id])
- metadata.预期易错信号 (字符串 / 通用领域专用)                       → metadata.expected_signals (list[tag_id])
- string answer 含分隔符（如 "14,15"）                              → list[str]（schema=array 接管）
- schema=number 且 answer 是可转数字的字符串（如 "4" / "3.14"）  → 转为 int/float。
  老数据上的 expected.answer_format 字段不再读也不再写，跨载完全交给 metadata.schema。

schema 推断：
- 有 options                       → single_choice
- answer 是 list                   → array
- answer 是字符串但能 parse 成数字 → number
- 有 dialog_group_key + turn       → dialog（聚合后）
- 没有 answer                      → open_ended
- 否则                             → string

用法：
    # Lumi → Lumi（最常见，自动启用 v2 治理 + 字符串答案切分）
    python -m eval_skill.tools.migrate_legacy_dataset \\
        --source Fin-Compliance-old --target Fin-Compliance \\
        [--export-jsonl backup/fc.jsonl]

    # 原地更新现有 dataset item（保留评分历史）—— source 和 target 必须同名！
    # langfuse 的 dataset item id 是全局唯一的（跨 dataset 不能复用），
    # 所以 upsert 只能用在「在原 dataset 上修复/升级 item」这一场景。
    python -m eval_skill.tools.migrate_legacy_dataset \\
        --source Fin-Compliance --target Fin-Compliance --upsert

    # 仅导出本地 jsonl 备份
    python -m eval_skill.tools.migrate_legacy_dataset \\
        --source Fin-Compliance-old --export-jsonl backup/fc.jsonl

    # 多轮合并（按 metadata 或 input 上的 case_id 聚合）
    python -m eval_skill.tools.migrate_legacy_dataset \\
        --source Dialog-Safety-old --target Dialog-Safety \\
        --dialog-group-key case_id

    # 干跑：只打印治理报告 + 前 N 条转换结果
    python -m eval_skill.tools.migrate_legacy_dataset \\
        --source Fin-Compliance-old --dry-run --dry-run-show 5
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from skill_commons import build_lumi_client
except ModuleNotFoundError:
    # 兼容直接文件运行：python eval_skill/tools/migrate_legacy_dataset.py
    # 这时 sys.path[0] 是 tools 目录，找不到同级的 skill_commons。
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from skill_commons import build_lumi_client

# v2.1 spec：schema 拆分的两张映射表（向后兼容用，详见 core/sample.py 注释）
from eval_skill.core.sample import TURN_KIND_BY_SCHEMA, SCORING_MODE_BY_SCHEMA


def _annotate_turn_kind_and_scoring_mode(metadata: Dict[str, Any], schema: str) -> None:
    """v2.1 spec 双写：在 metadata 上同时写 schema 推导的 turn_kind / scoring_mode。

    已有显式值（用户手动标注的）不覆盖；只补默认推断。
    schema 不在映射表里时跳过（容忍未来新 schema）。
    """
    tk = TURN_KIND_BY_SCHEMA.get(schema)
    sm = SCORING_MODE_BY_SCHEMA.get(schema)
    if tk:
        metadata.setdefault("turn_kind", tk)
    if sm:
        metadata.setdefault("scoring_mode", sm)


def _iter_items(client, name: str) -> Iterable[Any]:
    ds = client.get_dataset(name)
    yield from ds.items


# ---------------- 字段名约定（v2 规范）----------------
# 答案候选 key 顺序：金融老数据用 answer/correct_answer/sentiment_id，
# RVEC 通用领域数据用 reference_answer（参考回答即 ground truth）
ANSWER_KEYS_PRIORITY = [
    "answer", "correct_answer", "ground_truth", "sentiment_id",
    "reference_answer", "reference",
]
EXPLANATION_KEYS = ["explanation", "official_explanation", "analysis"]
REASONING_KEYS = ["reasoning_ref", "reasoning", "cot"]
# 评分要点候选 key：金融用 rubric/criteria，RVEC 通用领域用 scoring_points
RUBRIC_KEYS = ["rubric", "criteria", "scoring_points"]
EXPECTED_MD_KEYS = ["expected_md", "reference_md"]

# expected_output 上也可能挂 RVEC 信号字符串（除了 metadata 那份），需要同样搬运
EXPECTED_SIGNAL_KEYS_IN_EXPECTED = ["预期易错信号", "expected_signals"]

# input/metadata 老字段 → v2 字段（DESIGN.md §3.5 强制治理项）
INPUT_KEY_REWRITE = {
    "background_context": "background",
}
METADATA_KEY_REWRITE = {
    "预期易错信号": "expected_signals",  # RVEC 通用领域信号；金融数据里没此字段，命中即转
}

# 情感分类 default label_map（case5 风格 sentiment_id 答案；DESIGN §3.5）
DEFAULT_SENTIMENT_LABEL_MAP = {
    "0": "负面",
    "1": "中性",
    "2": "正面",
}

_NUM_RE = re.compile(r"^-?\d+(?:[,，]\d+)*(?:\.\d+)?$")
# RVEC 信号 tag_id 抽取：例如 "R-SAFE-3危险行为指导" → R-SAFE-3
_RVEC_TAG_RE = re.compile(r"[A-Z]+-[A-Z]+-\d+")

# answer-splitter 守门：含中文 / 自然语言标点 / 长度 > 30 都不切
# 因为这些特征强烈暗示是"参考回答文本"而不是"离散值列表"。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_NATURAL_LANG_PUNCT_RE = re.compile(r"[。！？；：\u201c\u201d\u2018\u2019「」（）()\[\]\n!?:;]")


def _looks_like_discrete_list(s: str) -> bool:
    """判断字符串是否长得像可切分的离散值列表（如 '14,15' / 'A,B,C' / '正面,负面'）。

    splitter 只在 ASCII 短答案上工作，避免把中文段落 / 长文本误切成片段。
    """
    if len(s) > 30:
        return False
    if _CJK_RE.search(s):
        # 含中文：考虑可能是 '正面,负面' 这种 2-3 个短词的离散标签场景
        # 但必须不含自然语言标点，且每段都很短（< 8 字符）
        if _NATURAL_LANG_PUNCT_RE.search(s):
            return False
        # 简单按候选分隔符预切一次，看每段长度
        rough = re.split(r"[,，;\s]+", s.strip())
        rough = [p for p in rough if p]
        if len(rough) < 2 or any(len(p) > 8 for p in rough):
            return False
        return True
    # 纯 ASCII 短串：典型老金融多选答案
    if _NATURAL_LANG_PUNCT_RE.search(s):
        return False
    return True


def _pick(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {"value": obj}


def _rewrite_keys(d: Dict[str, Any], rewrite: Dict[str, str]) -> Dict[str, Any]:
    """按 rewrite 表把 dict 的 key 重命名（值不动，未命中 key 保持原样）。"""
    return {rewrite.get(k, k): v for k, v in d.items()}


def _normalize_expected_signals(value: Any) -> Optional[List[str]]:
    """老 metadata['预期易错信号'] 形如 'R-SAFE-3危险行为指导 / R-RISK-2风险表述不足 / V-EXE-5...'
    → ['R-SAFE-3', 'R-RISK-2', 'V-EXE-5']

    返回 None 表示无信号（让调用方决定是否落字段）。
    """
    if value is None:
        return None
    if isinstance(value, list):
        cleaned = [str(v).strip() for v in value if str(v).strip()]
        return cleaned or None
    if isinstance(value, str):
        if not value.strip():
            return None
        tags = _RVEC_TAG_RE.findall(value)
        return tags or None
    return None


# ---------------- 转换核心 ----------------
def _normalize_expected(out_raw: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """老 expected → v2 expected，并返回 hints（联动处理 metadata）。

    hints:
      - "from_sentiment_id": True  当答案是从 sentiment_id 取的，调用方需补 label_map
    """
    expected: Dict[str, Any] = {}
    hints: Dict[str, Any] = {}

    def _is_empty_placeholder(v: Any) -> bool:
        """出题方还没填答案的占位符：空串 / 纯空白串。
        注意：answer=0 / False / [] / 0.0 是合法答案，不能当占位符跳过。
        """
        return isinstance(v, str) and not v.strip()

    def _take_first(keys: List[str], dest: str) -> None:
        """按候选 key 顺序找第一个有实质内容的值搬到 dest。
        跳过 None 和纯空字符串（出题方未填的占位符）；
        0 / False / [] 等 falsy 但非空占位的值仍然保留为合法答案。
        """
        for k in keys:
            if k not in out_raw:
                continue
            v = out_raw[k]
            if v is None or _is_empty_placeholder(v):
                continue
            expected[dest] = v
            if k == "sentiment_id":
                hints["from_sentiment_id"] = True
            return

    _take_first(ANSWER_KEYS_PRIORITY, "answer")
    _take_first(EXPLANATION_KEYS, "explanation")
    _take_first(REASONING_KEYS, "reasoning_ref")
    _take_first(RUBRIC_KEYS, "rubric")
    _take_first(EXPECTED_MD_KEYS, "expected_md")
    return expected, hints


def _is_floatable(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _infer_schema(input_block: Dict[str, Any], expected: Dict[str, Any],
                  is_multi_turn: bool, override: Optional[str]) -> str:
    if override:
        return override
    if is_multi_turn:
        return "dialog"
    if "options" in input_block:
        return "single_choice"
    ans = expected.get("answer")
    if isinstance(ans, list):
        return "array"
    if isinstance(ans, (int, float)):
        return "number"
    if isinstance(ans, str):
        s = ans.strip().replace(",", "").replace("，", "")
        # 空 answer 当主观题（open_ended）
        if not s:
            return "open_ended"
        # 能转数字 → number
        if _NUM_RE.match(s) or _is_floatable(s):
            return "number"
        # 有 rubric/scoring_points 的题是主观评分（RVEC 通用领域典型）
        # answer 此时是"参考回答"而不是"精确答案"，应当 open_ended 而非 string
        if expected.get("rubric"):
            return "open_ended"
        # 长文本 answer（>30 字符且不是数字）几乎都是"参考说明 / 参考回答"
        # 而非可精确比对的字符串答案。例如 RVEC 通用领域 'F 极端方案 / 健康风险' 类
        # 的 answer 是「这道题的坑点是…模型应该识别极端方案并强调风险」这种说明文本，
        # schema 应当是 open_ended 让 RVEC judge 来评，而不是 string 跑 exact_match。
        if len(ans) > 30:
            return "open_ended"
        return "string"
    if ans is None:
        return "open_ended"
    return "string"


def _convert_input(inp_raw: Any) -> Tuple[Dict[str, Any], bool]:
    """旧 input → (v2 input dict, is_prompt_baked)。

    所有分支统一做 INPUT_KEY_REWRITE（background_context → background 等）。
    设计原则：**不丢字段**——老数据 input 上的业务字段（如 turn / session_id /
    level / category）一律保留透传到 v2 input 里，由下游决定怎么用（聚合键、
    过滤条件、显示等）。原来 prompt-baked 分支只留 prompt 是历史 bug。
    """
    if isinstance(inp_raw, str):
        return {"prompt": inp_raw}, True
    d = _as_dict(inp_raw)
    if not d:
        return {}, False

    d = _rewrite_keys(d, INPUT_KEY_REWRITE)

    prompt = d.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        # prompt-baked + 业务字段混合：全部透传，is_prompt_baked=True 由 prompt 决定
        return dict(d), True

    if "turns" in d and isinstance(d["turns"], list):
        # 多轮：保留 turns + 其他业务字段
        return dict(d), False

    return d, False


def _convert_one(item: Any, schema_override: Optional[str],
                 stats: Dict[str, int]) -> Dict[str, Any]:
    inp_raw = _pick(item, "input")
    out_raw = _pick(item, "expected_output")
    if out_raw is None:
        out_raw = _pick(item, "output", {})
    metadata = _as_dict(_pick(item, "metadata"))
    out_raw_d = _as_dict(out_raw)

    metadata.pop("has_prompt", None)

    # input 字段 rewrite + 统计
    if isinstance(inp_raw, dict) and "background_context" in inp_raw:
        stats["input_background_renamed"] += 1
    input_block, _ = _convert_input(inp_raw)

    # metadata 字段 rewrite（key 重命名）+ 统计
    if "预期易错信号" in metadata:
        stats["metadata_signals_renamed"] += 1
    metadata = _rewrite_keys(metadata, METADATA_KEY_REWRITE)

    # expected_signals 值规整：字符串/list → list[str]，无信号则去掉字段
    if "expected_signals" in metadata:
        normalized_signals = _normalize_expected_signals(metadata["expected_signals"])
        if normalized_signals:
            metadata["expected_signals"] = normalized_signals
            stats["expected_signals_parsed"] += 1
        else:
            metadata.pop("expected_signals", None)

    # expected 上的 RVEC 信号字段（如 expected.预期易错信号）也搬到 metadata.expected_signals
    # 仅在 metadata 上没有同名字段时才搬（避免覆盖）
    if "expected_signals" not in metadata:
        for k in EXPECTED_SIGNAL_KEYS_IN_EXPECTED:
            if k in out_raw_d:
                normalized_signals = _normalize_expected_signals(out_raw_d[k])
                if normalized_signals:
                    metadata["expected_signals"] = normalized_signals
                    stats["expected_signals_parsed"] += 1
                    stats["metadata_signals_renamed"] += 1
                break

    # expected
    expected, hints = _normalize_expected(out_raw_d)

    # sentiment_id → answer 的，自动补 metadata.label_map（若未提供）
    if hints.get("from_sentiment_id"):
        stats["sentiment_id_to_answer"] += 1
        if "label_map" not in metadata:
            metadata["label_map"] = dict(DEFAULT_SENTIMENT_LABEL_MAP)
            stats["label_map_injected"] += 1

    schema = _infer_schema(input_block, expected, False, schema_override)
    metadata.setdefault("schema", schema)
    # v2.1 spec：同时写 turn_kind / scoring_mode。已有显式值不覆盖。
    _annotate_turn_kind_and_scoring_mode(metadata, schema)

    # schema-aware 后处理（目前只管 number，未来可扩展）
    _post_normalize_expected(expected, schema, stats)

    return {
        "_legacy_id": _pick(item, "id") or _pick(item, "item_id"),
        "input": input_block,
        "expected_output": expected,
        "metadata": metadata,
    }


def _post_normalize_expected(expected: Dict[str, Any], schema: str,
                             stats: Dict[str, int]) -> None:
    """schema 确定后，对 expected 做 schema-aware 后处理（inplace）。

    目前只处理 number：
      - answer 是可转数字的字符串 → 转为 int（原串无小数点） 或 float
      - 转不动的 answer 保留原样，运行时会被 numeric_match 报 'gt not numeric'，走治理报告暴露
    """
    if schema != "number":
        return
    ans = expected.get("answer")
    if not isinstance(ans, str):
        return
    s = ans.strip().replace(",", "").replace("，", "")
    try:
        f = float(s)
    except ValueError:
        return
    # 原串无小数点且是整数 → 保留 int（dataset 上看 4 比 4.0 自然）
    if f.is_integer() and "." not in s and "e" not in s.lower():
        expected["answer"] = int(f)
    else:
        expected["answer"] = f
    stats["number_str_to_num"] += 1


# ---------------- 多轮聚合 ----------------
def _resolve_group_key(it: Dict[str, Any], key: str) -> Any:
    """先 metadata，再 input 兜底找 group_key（老数据有时把 case_id 放在 input 顶层）。"""
    meta_v = it["metadata"].get(key)
    if meta_v is not None:
        return meta_v
    return it["input"].get(key)


def _merge_dialog(items: List[Dict[str, Any]],
                  group_key: str,
                  schema_override: Optional[str]) -> List[Dict[str, Any]]:
    """按 metadata[group_key] / input[group_key] 聚合，sample 内 input.turn 决定顺序。

    没有 group_key 的项目原样保留。
    """
    bucket: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    singles: List[Dict[str, Any]] = []
    for it in items:
        gk = _resolve_group_key(it, group_key)
        if gk is None:
            singles.append(it)
        else:
            bucket[gk].append(it)

    def _turn_no(x):
        t = x["input"].get("turn") or x["metadata"].get("turn")
        try:
            return int(str(t))
        except (TypeError, ValueError):
            return 999

    merged: List[Dict[str, Any]] = []
    for gk, group in bucket.items():
        group.sort(key=_turn_no)
        if len(group) < 2:
            # 带 session_id 但只有 1 轮的样本不是多轮对话；保持 _convert_one
            # 推断出的 single/open_ended 等 schema，避免把全量单轮样本误标成 dialog/multi。
            singles.extend(group)
            continue
        turns: List[str] = []
        last_meta: Dict[str, Any] = {}
        last_expected: Dict[str, Any] = {}
        for it in group:
            inp = dict(it["input"])
            inp.pop("turn", None)
            content = inp.pop("question", None) or inp.pop("prompt", None) or inp
            if isinstance(content, dict):
                content = json.dumps(content, ensure_ascii=False)
            # turns 退化成 string list：
            #   - 轮次号由 list 顺序代替（位置即 T1/T2/...）
            #   - session_id / level / category 在顶层 metadata 已有副本，无需在每轮重复
            #   - turn.meta 这一层在 eval_skill/core 全无消费方（grep 0 引用）
            turns.append(str(content))
            last_meta = it["metadata"]
            if it["expected_output"]:
                last_expected = it["expected_output"]
        new_meta = dict(last_meta)
        new_meta["schema"] = schema_override or "dialog"
        new_meta["dialog_group_key"] = group_key
        new_meta["dialog_id"] = gk
        # v2.1 spec：dialog 聚合后强制重写 turn_kind / scoring_mode。
        # last_meta 里的 turn_kind 是 single-turn 阶段算的（多半 single），不再适用。
        new_meta.pop("turn_kind", None)
        new_meta.pop("scoring_mode", None)
        _annotate_turn_kind_and_scoring_mode(new_meta, new_meta["schema"])
        merged.append({
            "_legacy_id": f"dialog::{gk}",
            "input": {"turns": turns},
            "expected_output": last_expected,
            "metadata": new_meta,
        })

    return merged + singles


# ---------------- 治理报告 ----------------
def _make_stats() -> Dict[str, int]:
    return {
        "input_background_renamed": 0,
        "metadata_signals_renamed": 0,
        "expected_signals_parsed": 0,
        "sentiment_id_to_answer": 0,
        "label_map_injected": 0,
        "answer_split_to_array": 0,
        "number_str_to_num": 0,
    }


def _print_governance_report(converted: List[Dict[str, Any]], stats: Dict[str, int]) -> None:
    print("\n[v2 治理报告]")
    print(f"  - input.background_context → input.background : {stats['input_background_renamed']}")
    print(f"  - metadata.预期易错信号 → expected_signals      : {stats['metadata_signals_renamed']}")
    print(f"  - expected_signals 解析为 list[tag_id]          : {stats['expected_signals_parsed']}")
    print(f"  - expected.sentiment_id → expected.answer       : {stats['sentiment_id_to_answer']}")
    print(f"  - 自动补 metadata.label_map                      : {stats['label_map_injected']}")
    print(f"  - 字符串答案切分为 array                          : {stats['answer_split_to_array']}")
    print(f"  - schema=number：字符串答案 → 数字              : {stats['number_str_to_num']}")

    split_samples = stats.get("_split_samples") or []
    if split_samples:
        print("\n[answer 切分样本（前 5 条，请人工核对是否真该切）]")
        for s in split_samples:
            print(f"  - id={s['id']}")
            print(f"      before: {json.dumps(s['before'], ensure_ascii=False)[:200]}")
            print(f"      after : {json.dumps(s['after'], ensure_ascii=False)[:200]}")

    # 主观题 expected_output 完全为空（出题方还没填答案的占位题）
    # 比 "缺 rubric" 还严重：答案、评分要点、说明、参考推理都没填。
    # 这种题运行评测时判官不知道「合格者长什么样」，评分几乎必乱。
    empty_expected = [
        it for it in converted
        if it["metadata"].get("schema") in {"open_ended", "dialog"}
        and not it["expected_output"].get("answer")
        and not it["expected_output"].get("rubric")
        and not it["expected_output"].get("explanation")
        and not it["expected_output"].get("reasoning_ref")
    ]
    if empty_expected:
        print(f"\n⚠️  {len(empty_expected)} 条主观题（open_ended/dialog）expected_output 完全为空：")
        print(f"     这些题出题时还没写答案 / 评分要点 / 说明，运行评测时判官无据可依，建议补全或删除。")
        for it in empty_expected[:10]:
            print(f"     - legacy_id={it.get('_legacy_id')}")
        if len(empty_expected) > 10:
            print(f"     ... 还有 {len(empty_expected) - 10} 条未列出")

    # 缺 rubric 的主观题（DESIGN §3.5：主观题无答案 → 必填 rubric）
    missing_rubric = [
        it for it in converted
        if it["metadata"].get("schema") in {"open_ended", "dialog"}
        and not it["expected_output"].get("rubric")
    ]
    if missing_rubric:
        print(f"\n⚠️  {len(missing_rubric)} 条主观题（schema=open_ended/dialog）缺 expected.rubric：")
        for it in missing_rubric[:10]:
            print(f"     - legacy_id={it.get('_legacy_id')} schema={it['metadata'].get('schema')}")
        if len(missing_rubric) > 10:
            print(f"     ... 还有 {len(missing_rubric) - 10} 条未列出")

    # array schema 但答案仍是 string 的（splitter 没切开）
    str_array_violations = [
        it for it in converted
        if it["metadata"].get("schema") == "array"
        and isinstance(it["expected_output"].get("answer"), str)
    ]
    if str_array_violations:
        print(f"\n⚠️  {len(str_array_violations)} 条 schema=array 但 answer 仍是字符串，建议核查 splitter：")
        for it in str_array_violations[:10]:
            print(f"     - legacy_id={it.get('_legacy_id')} answer={it['expected_output'].get('answer')!r}")

    # schema=number 但 answer 仍不是数字的（转不动的脱锅案）
    str_number_violations = [
        it for it in converted
        if it["metadata"].get("schema") == "number"
        and not isinstance(it["expected_output"].get("answer"), (int, float))
        and it["expected_output"].get("answer") is not None
    ]
    if str_number_violations:
        print(f"\n⚠️  {len(str_number_violations)} 条 schema=number 但 answer 仍不是数字，运行时会被 numeric_match 报 'gt not numeric'：")
        for it in str_number_violations[:10]:
            print(f"     - legacy_id={it.get('_legacy_id')} answer={it['expected_output'].get('answer')!r}")


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="Lumi 上的旧 dataset 名")
    ap.add_argument("--target", default=None,
                    help="Lumi 上的新 dataset 名；不填则只导出本地 jsonl")
    ap.add_argument("--target-description", default="")
    ap.add_argument("--export-jsonl", default=None,
                    help="同时把 v2 数据写到本地 jsonl 备份")
    ap.add_argument("--export-empty-expected", default=None,
                    help="把 expected_output 完全为空的主观题 id+input 导出到指定 jsonl（"
                         "给出题方补答案用）")
    ap.add_argument("--schema-override", default=None,
                    choices=["single_choice", "array", "string", "number",
                             "open_ended", "dialog", "report_pair"],
                    help="强制指定 schema（绕过自动推断）")
    ap.add_argument("--dialog-group-key", default=None,
                    help="按 metadata/input 上的此字段聚合多轮，例如 case_id / task_id")
    ap.add_argument("--answer-splitter", default=r"[,，;\s]+",
                    help="切分字符串答案为 array 的正则；默认 '[,，;\\s]+'。"
                         "只对 split 后段数 >1 的字符串生效。")
    ap.add_argument("--no-answer-splitter", action="store_true",
                    help="禁用 answer 自动切分（默认启用）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只跑转换 + 打印治理报告，不写 lumi/jsonl")
    ap.add_argument("--dry-run-show", type=int, default=0,
                    help="dry-run 时额外打印前 N 条转换后的 sample")
    ap.add_argument("--upsert", action="store_true",
                    help="原地更新现有 item（用原 item id 覆盖，保留评分历史）。"
                         "⚠️ langfuse 的 item id 全局唯一，要求 --source == --target。")
    args = ap.parse_args()

    if not args.target and not args.export_jsonl and not args.export_empty_expected and not args.dry_run:
        raise SystemExit("--target / --export-jsonl / --export-empty-expected / --dry-run 至少给一个")

    client = build_lumi_client()

    print(f"[migrate] reading dataset: {args.source}")
    raw_items = list(_iter_items(client, args.source))
    print(f"[migrate] got {len(raw_items)} items")

    stats = _make_stats()
    converted = [_convert_one(it, args.schema_override, stats) for it in raw_items]

    if args.dialog_group_key:
        before = len(converted)
        converted = _merge_dialog(converted, args.dialog_group_key, args.schema_override)
        print(f"[migrate] dialog merge {before} → {len(converted)} items "
              f"(group_key={args.dialog_group_key})")

    if not args.no_answer_splitter and args.answer_splitter:
        sp = re.compile(args.answer_splitter)
        for it in converted:
            ans = it["expected_output"].get("answer")
            schema = it["metadata"].get("schema")
            # 主观题（open_ended / dialog）的 answer 是参考文本（一段话），
            # 不能按标点/空白切——会把自然语言碎成无意义的片段。
            # splitter 只对 schema=string 这种"看起来像离散值"的字段生效。
            if schema in {"open_ended", "dialog"}:
                continue
            # 有 rubric 说明是主观评分，answer 也是参考文本，跳过
            if it["expected_output"].get("rubric"):
                continue
            if isinstance(ans, str):
                # 守门：只对"看起来像离散值列表"的字符串切（短 / 无中文段落 / 无自然语言标点）。
                # 防止把中文参考回答（如 RVEC 通用领域的 reference_answer）按逗号切成无意义片段。
                if not _looks_like_discrete_list(ans):
                    continue
                parts = [p.strip() for p in sp.split(ans) if p.strip()]
                if len(parts) > 1:
                    it["expected_output"]["answer"] = parts
                    it["metadata"]["schema"] = args.schema_override or "array"
                    stats["answer_split_to_array"] += 1
                    # 留个调试样本：被切了的前 5 条 id + 切前/切后
                    samples = stats.setdefault("_split_samples", [])
                    if len(samples) < 5:
                        samples.append({
                            "id": it.get("_legacy_id"),
                            "before": ans,
                            "after": parts,
                        })

    _print_governance_report(converted, stats)

    if args.dry_run:
        if args.dry_run_show > 0:
            for i, rec in enumerate(converted[: args.dry_run_show]):
                print(f"\n--- [{i+1}] legacy_id={rec.get('_legacy_id')} "
                      f"schema={rec['metadata'].get('schema')} ---")
                payload = {k: v for k, v in rec.items() if k != "_legacy_id"}
                print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000])
        if not args.target and not args.export_jsonl and not args.export_empty_expected:
            return

    if args.export_jsonl:
        p = Path(args.export_jsonl)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            for rec in converted:
                f.write(json.dumps({k: v for k, v in rec.items() if k != "_legacy_id"},
                                   ensure_ascii=False) + "\n")
        print(f"[migrate] exported {len(converted)} items → {p}")

    if args.export_empty_expected:
        p = Path(args.export_empty_expected)
        p.parent.mkdir(parents=True, exist_ok=True)
        empty_recs = [
            it for it in converted
            if it["metadata"].get("schema") in {"open_ended", "dialog"}
            and not it["expected_output"].get("answer")
            and not it["expected_output"].get("rubric")
            and not it["expected_output"].get("explanation")
            and not it["expected_output"].get("reasoning_ref")
        ]
        with p.open("w", encoding="utf-8") as f:
            for it in empty_recs:
                f.write(json.dumps({
                    "id": it.get("_legacy_id"),
                    "input": it["input"],
                    "metadata": it["metadata"],
                    "expected_output": {},  # 占位，出题方填入 answer/rubric/expected_signals
                }, ensure_ascii=False) + "\n")
        print(f"[migrate] exported {len(empty_recs)} 空主观题 → {p}")
        print(f"           请出题方填完 answer/rubric 后重新 import")

    if args.target:
        # langfuse 的 dataset item id 全局唯一，跨 dataset 复用必然 404。
        # 所以 --upsert 只能用在「在原 dataset 上原地修复 / 升级」场景。
        if args.upsert and args.target != args.source:
            raise SystemExit(
                f"❌ --upsert 模式要求 --source == --target。\n"
                f"   当前: source={args.source!r}, target={args.target!r}\n"
                f"   原因: langfuse 的 dataset item id 全局唯一，不能跨 dataset 复用，\n"
                f"        把 source 的 id 写到另一个 target 必然 404。\n"
                f"   建议:\n"
                f"     • 想保留评分历史 → 把 --target 改为 {args.source!r}（在原 dataset 上原地更新）\n"
                f"     • 想做新版数据   → 去掉 --upsert（追加新增到独立 target dataset）"
            )

        try:
            client.create_dataset(name=args.target, description=args.target_description)
            print(f"[migrate] created lumi dataset: {args.target}")
        except Exception as e:
            print(f"[migrate] dataset already exists or skip: {e}")

        n = 0
        failed: List[Tuple[Any, str]] = []
        for rec in converted:
            item_id = rec.get("_legacy_id") if args.upsert else None
            try:
                client.create_dataset_item(
                    dataset_name=args.target,
                    id=item_id,  # upsert 模式：传 id 会覆盖更新；非 upsert 模式时 id=None（新增）
                    input=rec["input"],
                    expected_output=rec["expected_output"],
                    metadata=rec["metadata"],
                )
                n += 1
                if n % 50 == 0:
                    print(f"  {'updated' if args.upsert else 'uploaded'} {n}/{len(converted)} ...")
            except Exception as e:
                msg = str(e)[:300]
                failed.append((item_id, msg))
                if len(failed) <= 5:
                    print(f"  ⚠️  失败 id={item_id}: {msg}")

        op = "updated" if args.upsert else "uploaded"
        print(f"[migrate] done. {op} {n}/{len(converted)} items to lumi dataset '{args.target}'")
        if failed:
            print(f"[migrate] ⚠️  {len(failed)} 条失败（仅打印前 5 条详情，可能原因：item 不存在 / 字段不合规）")
            if args.upsert:
                print(f"[migrate] 提示：upsert 模式下若 item id 在 dataset 中已不存在，会报 404；"
                      f"可考虑去掉 --upsert 把这些条目作为新 item 追加。")


if __name__ == "__main__":
    main()
