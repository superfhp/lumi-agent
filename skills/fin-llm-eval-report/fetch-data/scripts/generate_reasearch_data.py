"""
提取指定 trace name 的研报能力评测数据，输出为 CSV。
字段：trace_id, run_name, 模型名称, 模型版本, 研报能力, Token消耗(input/output/total), Latency(s), 总费用
"""
import os
import ast
import argparse
import pandas as pd
from langfuse import Langfuse
from tqdm import tqdm
import requests
from requests.auth import HTTPBasicAuth
import json

lf = Langfuse(
    public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", "pk-lf-ae40d3e8-0b00-4412-9734-c90b2cd77e49"),
    secret_key=os.environ.get("LANGFUSE_SECRET_KEY", "sk-lf-24f65774-ec0c-4490-bd7f-6cf9635f1d4e"),
    host="https://elliptic-implicit-tummy.ngrok-free.dev/siflow/auriga/vscs/skyinfer/xyli05/lumi/proxy/3000"
)

# 默认 trace name 和 model tag 列表
DEFAULT_TRACE_NAMES = ["Report_Analysis_Eval"]
DEFAULT_MODEL_TAGS  = ["deepseek-v4-pro","sft-general-0509", "kimi-k2.6", "glm-5.1","claude-sonnet-4-6"]
DEFAULT_OUTPUT_FILE = "Eval_ResearchReport.csv"


def fetch_rows(trace_names: list = None, model_tags: list = None) -> list:
    rows = []
    # trace_names 为 None 表示不按 name 过滤，拉全部
    name_list = trace_names if trace_names else [None]
    for trace_name in name_list:
        label_name = trace_name if trace_name else "ALL"
        print(f"\n[Trace] {label_name}")
        if model_tags:
            # 按 tag 分批拉取
            tag_groups = [([tag, "Report_Eval"], tag) for tag in model_tags]
        else:
            # 不传 model_tag，不过滤直接拉全部
            tag_groups = [(None, "ALL")]

        for tags_filter, label in tag_groups:
            kwargs = {}
            if trace_name:
                kwargs["name"] = trace_name
            if tags_filter:
                kwargs["tags"] = tags_filter
            traces_response = lf.get_traces(**kwargs)
            count = len(getattr(traces_response, "data", []))
            print(f"  [{label}] trace count: {count}")
            for trace in traces_response.data:
                try:
                    full_trace = lf.client.trace.get(trace.id)
                except Exception as e:
                    print(f"  [WARN] 获取完整 trace {trace.id} 失败: {e}")
                    full_trace = trace

                trace_id = full_trace.id
                run_name = getattr(full_trace, "name", "")

                # ── 模型名称 / 版本：从 trace.input 里取 ──────────
                input_obj = getattr(full_trace, "input", {}) or {}
                if isinstance(input_obj, str):
                    try:
                        input_obj = json.loads(input_obj)
                    except Exception:
                        input_obj = {}
                model_name    = input_obj.get("model", label)
                model_version = input_obj.get("model_version", "") or run_name
                input_text    = json.dumps(input_obj, ensure_ascii=False) if input_obj else ""

                # ── 分数 ───────────────────────────────────────────
                scores_dict = {}
                if getattr(full_trace, "scores", None):
                    for s in full_trace.scores:
                        sname = getattr(s, "name", "") or ""
                        scores_dict[sname.lower()] = s.value

                factuality_score    = scores_dict.get("factuality_score")
                recall_score        = scores_dict.get("recall_score")
                reasoning_score     = scores_dict.get("reasoning_score")
                structure_score     = scores_dict.get("structure_score")
                comprehensive_score = scores_dict.get("comprehensive_score")

                # ── 轮次：从 metadata["round"] 取 ─────────────────
                trace_metadata = getattr(full_trace, "metadata", {}) or {}
                if isinstance(trace_metadata, str):
                    try:
                        trace_metadata = json.loads(trace_metadata)
                    except Exception:
                        trace_metadata = {}
                round_val = trace_metadata.get("round", "")
                round_num = f"round{round_val}" if round_val != "" else ""

                latency    = getattr(full_trace, "latency",    None)
                total_cost = getattr(full_trace, "total_cost", None)

                # ── output 展开 ────────────────────────────────────
                output_obj = getattr(full_trace, "output", "") or ""
                output_flat = {}
                if isinstance(output_obj, dict):
                    for k, v in output_obj.items():
                        output_flat[k] = v
                else:
                    output_flat["output"] = output_obj

                row = {
                    "trace_id":             trace_id,
                    "trace_name":           label_name,
                    "run_name":             run_name,
                    "模型名称":             model_name,
                    "模型版本":             model_version,
                    "轮次":                 round_num,
                    "factuality_score":     factuality_score,
                    "recall_score":         recall_score,
                    "reasoning_score":      reasoning_score,
                    "structure_score":      structure_score,
                    "comprehensive_score":  comprehensive_score,
                    "Latency (s)":          latency,
                    "总费用($)":            total_cost,
                    "输入":                 input_text,
                }
                row.update(output_flat)
                rows.append(row)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从 Langfuse 拉取研报评测 trace 数据并导出 CSV")
    parser.add_argument(
        "--trace-name", "-t",
        type=str, nargs="+", default=None,
        metavar="NAME",
        help=f"指定 trace name，支持多个，例如 --trace-name Report_Analysis_Eval 10K_Core_Insights_Eval。不填默认: {DEFAULT_TRACE_NAMES}"
    )
    parser.add_argument(
        "--include", "-i",
        type=str, nargs="+", default=None,
        metavar="TAG",
        help="正选：只拉取 model_tag 包含任意指定关键字的 trace，例如 --include iquest_0509 deepseek"
    )
    parser.add_argument(
        "--exclude", "-e",
        type=str, nargs="+", default=None,
        metavar="TAG",
        help="反选：排除 model_tag 包含任意指定关键字的 trace，例如 --exclude qwen-plus"
    )
    parser.add_argument(
        "--output", "-o",
        type=str, default=None,
        help=f"输出 CSV 文件路径，默认: {DEFAULT_OUTPUT_FILE}"
    )
    args = parser.parse_args()

    trace_names = args.trace_name or None  # None 表示不过滤 trace name，拉全部

    # model_tags 过滤：不传则为 None（拉全部）
    model_tags = None
    if args.include or args.exclude:
        model_tags = list(DEFAULT_MODEL_TAGS)
        if args.include:
            model_tags = [t for t in model_tags if any(kw in t for kw in args.include)]
            # 若 include 关键字不在默认列表，直接将其加入
            for kw in args.include:
                if not any(kw in t for t in model_tags):
                    model_tags.append(kw)
        if args.exclude:
            model_tags = [t for t in model_tags if not any(kw in t for kw in args.exclude)]

    output_file = args.output or DEFAULT_OUTPUT_FILE

    print(f"Trace Names : {trace_names if trace_names else 'ALL (no filter)'}")
    print(f"Model Tags  : {model_tags if model_tags else 'ALL (no filter)'}")
    if args.include:
        print(f"[+] 正选关键字  : {args.include}")
    if args.exclude:
        print(f"[-] 反选关键字  : {args.exclude}")
    print(f"Output      : {output_file}")

    rows = fetch_rows(trace_names, model_tags)

    if rows:
        df = pd.DataFrame(rows)

        if output_file and os.path.isdir(output_file):
            out_dir = output_file
        elif output_file and not output_file.endswith(".csv"):
            out_dir = output_file
            os.makedirs(out_dir, exist_ok=True)
        else:
            out_dir = None

        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            for (tn, model), group_df in df.groupby(["trace_name", "模型名称"]):
                safe_tn = tn.replace("/", "_").replace(" ", "_")
                safe_model = model.replace("/", "_").replace(" ", "_")
                fname = f"{safe_tn}_{safe_model}.csv"
                fpath = os.path.join(out_dir, fname)
                group_df.to_csv(fpath, index=False, encoding="utf-8-sig")
                print(f"  -> {fname}  ({len(group_df)} 行)")
            print(f"\n[OK] 已导出 {len(df)} 行，按 trace_name+模型 拆分存储到: {out_dir}")
        else:
            final_path = output_file or DEFAULT_OUTPUT_FILE
            df.to_csv(final_path, index=False, encoding="utf-8-sig")
            print(f"\n[OK] 已导出 {len(df)} 行到 {final_path}")
    else:
        print("\n[WARN] 没有采集到任何数据。")