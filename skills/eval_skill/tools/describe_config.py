"""describe_config：把 yaml 配置 + 引用的 prompt/pack + dataset 体检渲染成"评测计划"。

供 cli describe-config 调用。是「评测前 5 步」流程的第 3 步：用户选完 dataset
预览 5 条之后，下一步必看 plan，确认评测规则后再 run。

设计原则：
  - 不修改任何文件，只读
  - 不发起 LLM 调用（不实际跑），只统计 + 估算
  - 输出对人类友好的中文文本，不是 JSON
  - dataset 体检会拉远端 lumi（耗时几秒），但不抽样跑模型
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from skill_commons import build_lumi_client
except ModuleNotFoundError:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from skill_commons import build_lumi_client

from ..core.config import ExperimentConfig, MetricSpec, ModelSpec
from ..core.sample_parser import parse_sample


# ----------------------------------------------------------------------------
# pack metadata 抽取（轻量读 pack.yaml；不依赖 metrics/_rvec_helpers）
# ----------------------------------------------------------------------------
def _read_pack_meta(pack_ref: str, project_root: Path) -> Optional[Dict[str, Any]]:
    """读 pack.yaml 取 metric 关心的几个字段。失败返回 None。"""
    try:
        import yaml as _yaml
    except ImportError:
        return None
    p = Path(pack_ref)
    if not p.is_absolute():
        p = project_root / pack_ref
    if p.is_file():
        p = p.parent
    yaml_path = p / "pack.yaml"
    if not yaml_path.exists():
        return None
    try:
        data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    return {
        "domain": str(data.get("domain", "?")),
        "version": str(data.get("version", "?")),
        "scoring_mode": str(data.get("scoring_mode", "?")),
        "signals_count": len(data.get("signals") or []),
        "highlights_count": len(data.get("highlights") or []),
        "caps": dict(data.get("caps") or {}),
        "pack_dir": str(p),
    }


# ----------------------------------------------------------------------------
# metric 描述：每个 metric 多一行「会读 dataset 哪些字段 / 会调几次 judge」
# ----------------------------------------------------------------------------
# 单条 sample 上每个 metric 估算 LLM 调用次数。已知 metric 的固定开销。
_METRIC_LLM_CALLS_PER_SAMPLE: Dict[str, int] = {
    "rvec_judge": 6,                # step1 + step2_R/V/E/C + step3
    "reasoning_quality": 1,
    "factuality_score": 1,
    "rubric_judge": 1,
    "custom_judge": 1,
    # 客观指标都是 0
    "accuracy": 0,
    "exact_match": 0,
    "contains": 0,
    "array_recall": 0,
    "array_f1": 0,
    "numeric_match": 0,
}


def _metric_one_liner(m: MetricSpec, project_root: Path) -> Tuple[str, int]:
    """渲染单 metric 描述 + 单条样本预计 LLM 调用次数。"""
    head = f"  · {m.name}" + (f" / alias={m.alias}" if m.alias else "")
    extras: List[str] = []
    cps = _METRIC_LLM_CALLS_PER_SAMPLE.get(m.name, 1 if m.name.endswith("_judge") else 0)

    # rvec：展示 pack
    pack_ref = m.extra.get("prompt_pack")
    if pack_ref:
        pm = _read_pack_meta(pack_ref, project_root)
        if pm:
            extras.append(
                f"      pack: {pack_ref}  ({pm['domain']} {pm['version']}, "
                f"signals={pm['signals_count']}, highlights={pm['highlights_count']}, "
                f"scoring_mode={pm['scoring_mode']})"
            )
            if pm.get("caps"):
                extras.append(f"      caps: {pm['caps']}")
        else:
            extras.append(f"      pack: {pack_ref}  (⚠️ pack.yaml 读取失败)")

    # custom prompts
    if m.prompt_ref:
        extras.append(f"      judge system prompt: {m.prompt_ref}")
    if m.user_prompt_ref:
        extras.append(f"      judge user template: {m.user_prompt_ref}")
    if m.prompt_inline:
        extras.append(f"      judge system prompt: <inline {len(m.prompt_inline)} chars>")
    if m.user_prompt_inline:
        extras.append(f"      judge user template: <inline {len(m.user_prompt_inline)} chars>")

    # extra caps 覆盖
    if m.extra.get("caps"):
        extras.append(f"      caps 覆盖: {m.extra['caps']}")

    # 客观指标：把关键参数显示
    if m.name == "accuracy":
        extras.append(f"      extractor={m.extractor}  normalizer={m.normalizer}")
    if m.name in ("array_recall", "array_f1"):
        extras.append(f"      splitter={m.splitter!r}")
    if m.name == "numeric_match":
        extras.append(f"      tolerance={m.tolerance}  relative={m.relative}")

    if cps > 0:
        extras.append(f"      预计每条样本调 judge {cps} 次")

    body = head + "\n" + "\n".join(extras) if extras else head
    return body, cps


# ----------------------------------------------------------------------------
# dataset 体检：拉一遍 sample，统计 schema/category/字段填充率
# ----------------------------------------------------------------------------
def _normalize_item(it: Any) -> Dict[str, Any]:
    """raw lumi item → dict（兼容 pydantic / dataclass）。"""
    if isinstance(it, dict):
        d = dict(it)
    elif hasattr(it, "model_dump"):
        try:
            d = it.model_dump()
        except Exception:
            d = {k: v for k, v in vars(it).items() if not k.startswith("_")}
    else:
        d = {k: v for k, v in vars(it).items() if not k.startswith("_")}
    d.setdefault("input", d.get("input") or {})
    d.setdefault("expected_output", d.get("expected_output") or {})
    d.setdefault("metadata", d.get("metadata") or {})
    return d


def _check_dataset(name: str) -> Dict[str, Any]:
    """拉 dataset，跑 parse_sample，统计 schema/字段/turns 等关键面。"""
    client = build_lumi_client()
    ds = client.get_dataset(name)
    raw = list(ds.items)
    items = [_normalize_item(it) for it in raw]

    schema_dist: Counter = Counter()
    cat_dist: Counter = Counter()
    has_answer = 0
    has_rubric = 0
    has_signals = 0
    multi_turn = 0
    single_turn = 0
    has_pdf = 0

    for it in items:
        meta = it["metadata"]
        inp = it["input"]
        eo = it["expected_output"]
        schema = meta.get("schema") or "?"
        schema_dist[schema] += 1
        cat = inp.get("category") or meta.get("category") or "(无)"
        cat_dist[cat] += 1
        if eo.get("answer"):
            has_answer += 1
        if eo.get("rubric"):
            has_rubric += 1
        if meta.get("expected_signals"):
            has_signals += 1
        turns = inp.get("turns")
        if isinstance(turns, list):
            if len(turns) > 1:
                multi_turn += 1
            else:
                single_turn += 1
        if inp.get("pdf_refs"):
            has_pdf += 1

    return {
        "name": name,
        "total": len(items),
        "schema_dist": dict(schema_dist),
        "category_dist": dict(cat_dist.most_common(10)),
        "has_answer": has_answer,
        "has_rubric": has_rubric,
        "has_signals": has_signals,
        "multi_turn": multi_turn,
        "single_turn": single_turn,
        "has_pdf": has_pdf,
        "dataset_metadata": _to_dict_meta(ds),
    }


def _to_dict_meta(ds: Any) -> Dict[str, Any]:
    """从 DatasetClient 上抽 metadata 字段（lumi SDK 取法可能略不同）。"""
    raw_meta = getattr(ds, "metadata", None)
    if isinstance(raw_meta, dict):
        return dict(raw_meta)
    if hasattr(raw_meta, "model_dump"):
        try:
            return raw_meta.model_dump() or {}
        except Exception:
            return {}
    return {}


# ----------------------------------------------------------------------------
# main 渲染
# ----------------------------------------------------------------------------
def _model_line(m: ModelSpec, role: str) -> str:
    return (f"  - {role}: {m.host_profile}/{m.model}  "
            f"temp={m.temperature}  run_prefix={m.run_prefix or '(空)'}"
            + (f"  max_tokens={m.max_tokens}" if m.max_tokens else ""))


def render_plan(cfg: ExperimentConfig, project_root: Path,
                check_dataset: bool = True) -> str:
    """渲染评测计划文本。"""
    lines: List[str] = []
    lines.append("=" * 70)
    lines.append(f"评测计划：{cfg.experiment_name}")
    lines.append(f"配置文件：{cfg.config_path}")
    if cfg.tags:
        lines.append(f"tags：{', '.join(cfg.tags)}")
    if cfg.description:
        lines.append(f"描述：{cfg.description}")
    lines.append("=" * 70)
    lines.append("")

    # ----- dataset & sampling -----
    lines.append(f"📚 数据集：{cfg.dataset.name}")
    s = cfg.sampling
    if s.mode == "full":
        lines.append(f"   采样：全量")
    elif s.mode == "n":
        lines.append(f"   采样：随机抽 {s.n} 条（seed={s.seed}）")
    elif s.mode == "ratio":
        lines.append(f"   采样：随机抽 {(s.ratio or 0) * 100:.0f}%（seed={s.seed}）")
    lines.append("")

    # ----- 模型 -----
    lines.append("🤖 模型：")
    lines.append(_model_line(cfg.model_under_test, "MUT"))
    for b in cfg.baselines:
        lines.append(_model_line(b, "baseline"))
    if cfg.judge:
        lines.append(f"  - judge: {cfg.judge.host_profile}/{cfg.judge.model}  temp={cfg.judge.temperature}")
    lines.append("")

    # ----- prompt strategy -----
    lines.append("✏️  prompt strategy：")
    ps = cfg.prompt_strategy
    if ps.system_prompt_ref:
        lines.append(f"   system_prompt_ref: {ps.system_prompt_ref}")
    if ps.system_prompt:
        lines.append(f"   system_prompt: <inline {len(ps.system_prompt)} chars>")
    if ps.user_template_ref:
        lines.append(f"   user_template_ref: {ps.user_template_ref}")
    if ps.user_template:
        lines.append(f"   user_template: <inline {len(ps.user_template)} chars>")
    if not any([ps.system_prompt, ps.system_prompt_ref, ps.user_template, ps.user_template_ref]):
        lines.append(f"   (无显式 prompt，走 prompt-baked / turns 直传)")
    lines.append("")

    # ----- metrics -----
    lines.append(f"📐 评测规则（{len(cfg.metrics)} 个 metric）：")
    total_calls_per_sample = 0
    for m in cfg.metrics:
        body, cps = _metric_one_liner(m, project_root)
        lines.append(body)
        total_calls_per_sample += cps
    lines.append("")

    # ----- 估算调用量 -----
    n_models = 1 + len(cfg.baselines)
    rounds = cfg.execution.rounds
    lines.append("⏱  规模估算：")
    lines.append(f"   judge 单条样本调用次数（所有 metric 合计）: {total_calls_per_sample}")
    lines.append(f"   模型数: {n_models}（1 MUT + {len(cfg.baselines)} baseline）"
                 f"  rounds: {rounds}  concurrency: {cfg.execution.concurrency}")
    if check_dataset:
        # 在 dataset 体检后能给精确数；这里先按 yaml 里的 sampling 估
        if s.mode == "n" and s.n:
            n_est = s.n
        elif s.mode == "ratio" and s.ratio:
            n_est = int(s.ratio * 100)  # 占位
        else:
            n_est = 0  # 待 dataset 体检后补
    lines.append("")

    # ----- dataset 体检 -----
    if check_dataset:
        lines.append("🔬 数据集体检（拉 lumi 中…）")
        try:
            stats = _check_dataset(cfg.dataset.name)
        except Exception as e:
            lines.append(f"   ⚠️ 体检失败: {e}")
            stats = None

        if stats:
            n_total = stats["total"]
            lines.append(f"   总数：{n_total} 条")
            ds_meta = stats["dataset_metadata"]
            if ds_meta:
                lines.append(f"   dataset metadata: {ds_meta}")
            else:
                lines.append(f"   ⚠️ dataset 没有 metadata（domain 等），按 SKILL 约定属于「未挂载」")

            sd = stats["schema_dist"]
            if sd:
                lines.append("   schema 分布:")
                for k, v in sorted(sd.items(), key=lambda x: -x[1]):
                    lines.append(f"     - {k}: {v}")

            lines.append(
                f"   字段填充: answer {stats['has_answer']}/{n_total} "
                f"| rubric {stats['has_rubric']}/{n_total} "
                f"| expected_signals {stats['has_signals']}/{n_total}"
            )
            if stats["multi_turn"] or stats["single_turn"]:
                lines.append(
                    f"   轮次: 多轮 {stats['multi_turn']}, 单轮 {stats['single_turn']}"
                )
            if stats["has_pdf"]:
                lines.append(f"   PDF 引用: {stats['has_pdf']}/{n_total}")

            # 修正调用量估算
            if s.mode == "full":
                n_est = n_total
            elif s.mode == "n" and s.n:
                n_est = min(s.n, n_total)
            elif s.mode == "ratio" and s.ratio:
                n_est = max(1, int(n_total * s.ratio))
            else:
                n_est = n_total

            total_judge_calls = n_est * total_calls_per_sample * n_models * rounds
            total_model_calls = n_est * n_models * rounds  # MUT + baseline 各调一次模型
            lines.append("")
            lines.append(f"📊 预计调用量（{n_est} 条样本）：")
            lines.append(f"   模型调用: {total_model_calls} 次（{n_est} 条 × {n_models} 模型 × {rounds} rounds）")
            if total_judge_calls > 0:
                lines.append(f"   judge 调用: {total_judge_calls} 次"
                             f"（{n_est} × {total_calls_per_sample} × {n_models} × {rounds}）")

            # 警告
            warnings: List[str] = []
            if "open_ended" in sd or "dialog" in sd:
                subj = sd.get("open_ended", 0) + sd.get("dialog", 0)
                if subj > 0 and stats["has_signals"] == 0:
                    warnings.append(
                        f"主观题 {subj} 条但 expected_signals 全空：RVEC reference_block 不会有内容"
                    )
                # rubric_judge 检查
                if any(m.name == "rubric_judge" for m in cfg.metrics):
                    no_rubric = subj - stats["has_rubric"]
                    if no_rubric > 0:
                        warnings.append(
                            f"配置了 rubric_judge，但 {no_rubric} 条主观题没填 rubric"
                        )
            if warnings:
                lines.append("")
                lines.append("⚠️ 注意：")
                for w in warnings:
                    lines.append(f"   - {w}")
        lines.append("")

    # ----- execution -----
    lines.append(f"⚙️  执行：")
    lines.append(f"   rounds={rounds}  concurrency={cfg.execution.concurrency}  "
                 f"resume={cfg.execution.resume}  reporter={cfg.execution.reporter}")
    lines.append("")
    lines.append("=" * 70)
    lines.append("如确认无误，移除 describe-config，改用 cli run -c <yaml> 执行")
    lines.append("=" * 70)

    return "\n".join(lines)


def cli_main(yaml_path: Path, project_root: Path, check_dataset: bool = True) -> None:
    """供 cli describe-config 调用。"""
    plans = ExperimentConfig.from_yaml_expanded(yaml_path)
    for i, cfg in enumerate(plans, 1):
        if len(plans) > 1:
            print(f"\n\n{'#' * 30} plan {i}/{len(plans)} {'#' * 30}\n")
        print(render_plan(cfg, project_root, check_dataset=check_dataset))
