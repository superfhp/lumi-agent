"""
从 Langfuse 关联 datasets / runs / tracing 提取全量评测数据
字段：题目、参考答案、实际回答、所属领域、难度、Accuracy、reasoning_quality、
      模型名称、模型版本、Token消耗(input/output/total)、Latency(s)、总费用

适配 langfuse==2.x SDK：
  - lf.get_dataset_run()          → dataset_run_items (含 trace_id / dataset_item_id)
  - lf.client.dataset_items.get() → 题目 / 参考答案 / metadata
  - lf.client.trace.get()         → latency / scores / metadata / observations(含GENERATION)
"""
import os
import re
import json
import argparse
import pandas as pd
from tqdm import tqdm
from langfuse import Langfuse

# ===============================================================
# 1. 配置
# ===============================================================
LANGFUSE_PUBLIC_KEY = "pk-lf-ae40d3e8-0b00-4412-9734-c90b2cd77e49"
LANGFUSE_SECRET_KEY = "sk-lf-24f65774-ec0c-4490-bd7f-6cf9635f1d4e"
LANGFUSE_HOST       = "https://elliptic-implicit-tummy.ngrok-free.dev/siflow/auriga/vscs/skyinfer/xyli05/lumi/proxy/3000"
lf = Langfuse(
    public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", LANGFUSE_PUBLIC_KEY),
    secret_key=os.environ.get("LANGFUSE_SECRET_KEY", LANGFUSE_SECRET_KEY),
    host=os.environ.get("LANGFUSE_HOST", LANGFUSE_HOST)
)

# ── 要提取的 dataset → run 列表，支持同一模型多个版本 ────────────
# 留空列表表示"自动拉取该 dataset 下所有 run"
TARGETS = {
    "CFA-Level1-2018": [
        "glm-5_0.1_round1_CFA-Level1-2018_round1",
        "safety_0.1_round1_CFA-Level1-2018_round1",
        "safety_0.1_round1_CFA-Level1-2018_round2",
        "deepseek-v3.2_0.1_round1_CFA-Level1-2018_round2",
        "deepseek-v3.2_0.1_round1_CFA-Level1-2018_round1",
        "kimi-k2.5_0.1_round1_CFA-Level1-2018_round2",
        "kimi-k2.5_0.1_round1_CFA-Level1-2018_round1",
        "qwen3.6-plus_0.1_round1_CFA-Level1-2018_round1",
        "qwen3.6-plus_0.1_round1_CFA-Level1-2018_round2"
    ],

    # 可继续追加其他 dataset / run
    # "CFA-Level2-2025": [],
}

OUTPUT_FILE = "Eval_FullReport.csv"

def safe_meta(raw) -> dict:
    """把 metadata 统一转成 dict，兼容 str / dict / None"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw.replace("'", '"'))
        except Exception:
            return {}
    return {}


def extract_generation(observations: list) -> tuple:
    """
    从 trace.observations 中找第一个 GENERATION 节点，
    返回 (actual_output, input_tokens, output_tokens, total_tokens)
    """
    for obs in observations:
        if getattr(obs, "type", "") == "GENERATION":
            output = obs.output or ""
            usage = obs.usage
            if usage:
                return (
                    output,
                    getattr(usage, "input", 0) or 0,
                    getattr(usage, "output", 0) or 0,
                    getattr(usage, "total", 0) or 0,
                )
            return output, 0, 0, 0
    return "未找到回答", 0, 0, 0


def get_all_runs(dataset_name: str, specified_runs: list,
                 include_keywords: list = None, exclude_keywords: list = None) -> list:
    """返回要处理的 run name 列表（指定或自动发现），支持关键字正选/反选过滤"""
    if specified_runs:
        candidates = specified_runs
    else:
        # 自动发现该 dataset 下所有 run
        try:
            runs_res = lf.client.datasets.get_runs(dataset_name)
            candidates = [r.name for r in (runs_res.data or [])]
        except Exception:
            # fallback: 用 HTTP
            import requests
            from requests.auth import HTTPBasicAuth
            AUTH = HTTPBasicAuth(
                os.environ.get("LANGFUSE_PUBLIC_KEY", LANGFUSE_PUBLIC_KEY),
                os.environ.get("LANGFUSE_SECRET_KEY", LANGFUSE_SECRET_KEY),
            )
            r = requests.get(
                f"{os.environ.get('LANGFUSE_HOST', LANGFUSE_HOST)}/api/public/datasets/{dataset_name}/runs",
                auth=AUTH, params={"limit": 100}
            )
            candidates = [x["name"] for x in r.json().get("data", [])] if r.ok else []

    # 正选过滤：保留包含任意 include 关键字的 run
    if include_keywords:
        candidates = [r for r in candidates if any(kw in r for kw in include_keywords)]
    # 反选过滤：排除包含任意 exclude 关键字的 run
    if exclude_keywords:
        candidates = [r for r in candidates if not any(kw in r for kw in exclude_keywords)]

    return candidates


# ===============================================================
# 3. 主提取逻辑
# ===============================================================

def export_eval_report(targets: dict = None, output_file: str = None,
                       include_keywords: list = None, exclude_keywords: list = None):
    all_rows = []
    targets = targets or TARGETS
    output_file = output_file or OUTPUT_FILE

    for dataset_name, run_names in targets.items():
        run_names = get_all_runs(dataset_name, run_names,
                                 include_keywords=include_keywords,
                                 exclude_keywords=exclude_keywords)
        print(f"\n📂 Dataset: {dataset_name}  →  {len(run_names)} 个 Run")

        for run_name in run_names:
            print(f"  ▶ Run: {run_name}")

            # ── 3.1 通过 get_dataset_run 拿到所有关联记录 ──────────
            try:
                run_obj = lf.get_dataset_run(dataset_name, run_name)
                run_items = run_obj.dataset_run_items or []
            except Exception as e:
                print(f"    ⚠️ 获取 run 失败: {e}")
                continue

            print(f"    🔗 共 {len(run_items)} 条关联记录，开始逐条拉取...")

            for ri in tqdm(run_items, desc=f"    {run_name[:40]}", leave=False):
                dataset_item_id = ri.dataset_item_id
                trace_id        = ri.trace_id  # run_item.trace_id 是正确字段

                # ── 3.2 Dataset Item：题目 & 参考答案 ───────────────
                try:
                    di = lf.client.dataset_items.get(dataset_item_id)
                    raw_input    = di.input    or {}
                    raw_expected = di.expected_output or {}
                    item_meta    = safe_meta(di.metadata)

                    # input 是 dict：提取 question + options
                    if isinstance(raw_input, dict):
                        question = raw_input.get("question", "")
                        options  = raw_input.get("options", {})
                        options_str = "  ".join([f"{k}: {v}" for k, v in options.items()]) if options else ""
                        question_full = f"{question}\n{options_str}".strip()
                    else:
                        question_full = str(raw_input)

                    # expected_output 是 dict：提取正确答案 + 官方解释
                    if isinstance(raw_expected, dict):
                        correct_answer = raw_expected.get("correct_answer", "")
                        explanation    = raw_expected.get("official_explanation", "")
                        reference      = f"{correct_answer}\n{explanation}".strip()
                    else:
                        reference = str(raw_expected)

                    item_difficulty = item_meta.get("difficulty_level", "")
                    item_category   = item_meta.get("fin_category", "")

                except Exception as e:
                    print(f"\n    ⚠️ dataset_item {dataset_item_id} 获取失败: {e}")
                    question_full = reference = item_difficulty = item_category = ""

                # ── 3.3 Trace：实际回答 / latency / tokens / scores ─
                if not trace_id:
                    continue
                try:
                    trace = lf.client.trace.get(trace_id)
                except Exception as e:
                    print(f"\n    ⚠️ trace {trace_id} 获取失败: {e}")
                    continue

                trace_meta = safe_meta(trace.metadata)

                # 分数
                scores_dict = {}
                comments_dict = {}
                if trace.scores:
                    for s in trace.scores:
                        key = s.name.lower()
                        scores_dict[key] = s.value
                        comments_dict[key] = getattr(s, "comment", None) or ""

                # 实际回答 & token 从 GENERATION observation 取
                actual_output, tok_in, tok_out, tok_total = extract_generation(
                    trace.observations or []
                )

                # 模型版本：直接使用 run_name（可按需解析）
                model_name    = trace_meta.get("tested_model", run_name.split("_")[0])
                model_version = run_name   # 完整 run_name 作为版本标识，便于趋势对比

                # 所属领域 & 难度：trace metadata 优先，item metadata 兜底
                domain     = trace_meta.get("cfa_category", item_category) or item_category
                difficulty = trace_meta.get("difficulty_level", item_difficulty) or item_difficulty

                # 轮次：取 run_name 中最后一个 _round 后缀
                round_match = re.search(r'_(round\d+)$', run_name)
                round_num = round_match.group(1) if round_match else ""

                row = {
                    "Dataset":        dataset_name,
                    "题目":           question_full,
                    "参考答案":       reference,
                    "实际回答":       actual_output,
                    "模型名称":       model_name,
                    "模型版本":       model_version,
                    "轮次":           round_num,
                    "所属领域":       domain,
                    "难度":           difficulty,
                    "Accuracy":                  scores_dict.get("accuracy"),
                    "Accuracy_comment":          comments_dict.get("accuracy", ""),
                    "reasoning_quality":         scores_dict.get("reasoning_quality"),
                    "reasoning_quality_comment": comments_dict.get("reasoning_quality", ""),
                    # 如有其他自定义分数（Pass@K / 研报能力）可在此追加：
                    # "Pass@K":        scores_dict.get("pass@k"),
                    # "研报能力":      scores_dict.get("研报能力"),
                    "Token_Input":    tok_in,
                    "Token_Output":   tok_out,
                    "Token_Total":    tok_total,
                    "Latency (s)":    trace.latency,
                    "总费用($)":      getattr(trace, "total_cost", 0) or 0,
                }
                all_rows.append(row)

    # ===============================================================
    # 4. 导出（按 dataset + 模型名称 分文件存储）
    # ===============================================================
    if not all_rows:
        print("\n⚠️ 没有采集到任何数据，请检查 TARGETS 配置。")
        return

    df = pd.DataFrame(all_rows)

    if output_file and os.path.isdir(output_file):
        out_dir = output_file
    elif output_file and not output_file.endswith(".csv"):
        out_dir = output_file
        os.makedirs(out_dir, exist_ok=True)
    else:
        out_dir = None

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        for (ds, model), group_df in df.groupby(["Dataset", "模型名称"]):
            safe_ds = ds.replace("/", "_").replace(" ", "_")
            safe_model = model.replace("/", "_").replace(" ", "_")
            fname = f"{safe_ds}_{safe_model}.csv"
            fpath = os.path.join(out_dir, fname)
            group_df.to_csv(fpath, index=False, encoding="utf-8-sig")
            print(f"  💾 {fname}  ({len(group_df)} 行)")
        print(f"\n✅ 完成！共 {len(df)} 行，按 dataset+模型 拆分存储到: {out_dir}")
    else:
        final_path = output_file or OUTPUT_FILE
        df.to_csv(final_path, index=False, encoding="utf-8-sig")
        print(f"\n✅ 完成！共 {len(df)} 行，已导出至: {final_path}")

    print(df[["模型名称", "所属领域", "难度", "Accuracy", "Token_Total", "Latency (s)"]].head(10).to_string())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从 Langfuse 拉取评测集实验数据并导出 CSV")
    parser.add_argument(
        "--dataset", "-d",
        type=str, nargs="+", default=None,
        metavar="DATASET",
        help="指定评测集名称，支持多个，例如 --dataset CFA-Level1-2018 CFA-Level2-2025。不填则使用脚本内 TARGETS 配置"
    )
    parser.add_argument(
        "--include", "-i",
        type=str, nargs="+", default=None,
        metavar="KEYWORD",
        help="正选：只保留实验名包含任意指定关键字的 run，例如 --include iquest_0509"
    )
    parser.add_argument(
        "--exclude", "-e",
        type=str, nargs="+", default=None,
        metavar="KEYWORD",
        help="反选：排除实验名包含任意指定关键字的 run，例如 --exclude qwen-plus"
    )
    parser.add_argument(
        "--output", "-o",
        type=str, default=None,
        help="输出 CSV 文件路径，默认使用脚本内 OUTPUT_FILE 配置"
    )
    args = parser.parse_args()

    # 若指定了 dataset，构建临时 targets（空列表 = 自动发现所有 run）
    targets = None
    if args.dataset:
        targets = {ds: [] for ds in args.dataset}
        print(f"📌 指定评测集: {args.dataset}")

    if args.include:
        print(f"✅ 正选关键字: {args.include}")
    if args.exclude:
        print(f"❌ 反选关键字: {args.exclude}")

    export_eval_report(
        targets=targets,
        output_file=args.output,
        include_keywords=args.include,
        exclude_keywords=args.exclude,
    )