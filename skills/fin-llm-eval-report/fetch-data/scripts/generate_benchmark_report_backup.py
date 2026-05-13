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
import requests
from requests.auth import HTTPBasicAuth
import pandas as pd
from tqdm import tqdm
from langfuse import Langfuse

# ===============================================================
# 1. 配置
# ===============================================================
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "http://172.16.217.161:3000")
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "pk-lf-6c9a9751-70cc-4def-b650-533e176374a9")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "sk-lf-c3dd7903-0c39-4faf-bec9-c4e9448b105a")
AUTH = HTTPBasicAuth(LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY)

lf = Langfuse(
    public_key=LANGFUSE_PUBLIC_KEY,
    secret_key=LANGFUSE_SECRET_KEY,
    host=LANGFUSE_HOST
)

# ── 要提取的 dataset → run 列表，支持同一模型多个版本 ────────────
# 留空列表表示"自动拉取该 dataset 下所有 run"
def get_all_dataset_names() -> list:
    """获取 Langfuse 中全部 dataset 名称（自动分页）"""
    names = []
    page = 1
    limit = 100

    while True:
        resp = requests.get(
            f"{LANGFUSE_HOST}/api/public/datasets",
            auth=AUTH,
            params={"page": page, "limit": limit},
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            break

        names.extend([d.get("name") for d in data if d.get("name")])
        if len(data) < limit:
            break
        page += 1

    return names


def build_targets_from_all_datasets() -> dict:
    """通过 get_dataset() 获取全量 dataset，并构建 TARGETS（run 留空=自动拉取）"""
    targets = {}
    dataset_names = get_all_dataset_names()
    print(f"\n🔍 发现 {len(dataset_names)} 个 dataset: {dataset_names}")
    
    for dataset_name in dataset_names:
        try:
            # 用 get_dataset() 拉取完整 dataset 对象，确保该 dataset 可访问
            ds = lf.get_dataset(dataset_name)
            targets[getattr(ds, "name", dataset_name)] = []
            print(f"   ✅ 已加入: {dataset_name}")
        except Exception as e:
            print(f"   ⚠️ 跳过 {dataset_name}（get_dataset失败）: {e}")
    
    print(f"\n✨ TARGETS 最终配置: {list(targets.keys())}\n")
    return targets


TARGETS = build_targets_from_all_datasets()

OUTPUT_FILE = "/mnt/workspace/data/val_FullReport.csv"

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
        obs_type = obs.get("type", "") if isinstance(obs, dict) else getattr(obs, "type", "")
        if obs_type == "GENERATION":
            output = (obs.get("output") if isinstance(obs, dict) else obs.output) or ""
            usage = obs.get("usage") if isinstance(obs, dict) else obs.usage
            if usage:
                if isinstance(usage, dict):
                    return (
                        output,
                        usage.get("input", 0) or 0,
                        usage.get("output", 0) or 0,
                        usage.get("total", 0) or 0,
                    )
                return (
                    output,
                    getattr(usage, "input", 0) or 0,
                    getattr(usage, "output", 0) or 0,
                    getattr(usage, "total", 0) or 0,
                )
            return output, 0, 0, 0
    return "未找到回答", 0, 0, 0


def get_all_runs(dataset_name: str, specified_runs: list) -> list:
    """返回要处理的 run name 列表（指定或自动发现）"""
    if specified_runs:
        return specified_runs
    
    # 自动发现该 dataset 下所有 run
    try:
        runs_res = lf.client.datasets.get_runs(dataset_name)
        runs = [r.name for r in (runs_res.data or [])]
        print(f"    ℹ️ 通过 SDK 获取 {len(runs)} 个 run")
        return runs
    except Exception as e:
        print(f"    ℹ️ SDK get_runs 失败: {type(e).__name__}: {e}，尝试 HTTP fallback...")
    
    # fallback: 用 HTTP
    try:
        r = requests.get(
            f"{LANGFUSE_HOST}/api/public/datasets/{dataset_name}/runs",
            auth=AUTH,  # 使用全局 AUTH（同第一部分拉 dataset 的凭证）
            params={"limit": 100}
        )
        if r.ok:
            runs = [x["name"] for x in r.json().get("data", [])]
            print(f"    ℹ️ 通过 HTTP 获取 {len(runs)} 个 run")
            return runs
        else:
            print(f"    ⚠️ HTTP 请求失败 (status={r.status_code}): {r.text[:200]}")
            return []
    except Exception as e:
        print(f"    ⚠️ HTTP fallback 也失败: {type(e).__name__}: {e}")
        return []


# ===============================================================
# 3. 主提取逻辑
# ===============================================================

def export_eval_report():
    all_rows = []
    
    if not TARGETS:
        print("\n❌ TARGETS 为空！无 dataset 可处理。请检查:")
        print("   1. LANGFUSE_HOST 是否可达")
        print("   2. 凭证（PUBLIC_KEY / SECRET_KEY）是否正确")
        print("   3. Langfuse 实例中是否存在 dataset")
        return

    for dataset_name, run_names in TARGETS.items():
        run_names = get_all_runs(dataset_name, run_names)
        print(f"\n📂 Dataset: {dataset_name}  →  {len(run_names)} 个 Run")

        for run_name in run_names:
            print(f"  ▶ Run: {run_name}")

            # ── 3.1 通过 HTTP API 拿到所有关联记录（兼容 langfuse v4）──
            try:
                # 先查 dataset id 和确认 run 存在
                runs_resp = requests.get(
                    f"{LANGFUSE_HOST}/api/public/datasets/{dataset_name}/runs",
                    auth=AUTH, params={"limit": 100},
                )
                runs_resp.raise_for_status()
                runs_data = runs_resp.json().get("data", [])
                run_info = next((r for r in runs_data if r["name"] == run_name), None)
                if not run_info:
                    print(f"    ⚠️ 未找到 run: {run_name}")
                    continue
                dataset_id = run_info["datasetId"]

                # 用 datasetId + runName 拉取所有 items
                run_items = []
                page = 1
                while True:
                    resp = requests.get(
                        f"{LANGFUSE_HOST}/api/public/dataset-run-items",
                        auth=AUTH,
                        params={"datasetId": dataset_id, "runName": run_name, "limit": 100, "page": page},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    batch = data.get("data", [])
                    run_items.extend(batch)
                    if len(batch) < 100:
                        break
                    page += 1
            except Exception as e:
                print(f"    ⚠️ 获取 run 失败: {e}")
                continue

            print(f"    🔗 共 {len(run_items)} 条关联记录，开始逐条拉取...")

            for ri in tqdm(run_items, desc=f"    {run_name[:40]}", leave=False):
                dataset_item_id = ri.get("datasetItemId") or ri.get("dataset_item_id")
                trace_id        = ri.get("traceId") or ri.get("trace_id")

                # ── 3.2 Dataset Item：题目 & 参考答案 ───────────────
                try:
                    di_resp = requests.get(
                        f"{LANGFUSE_HOST}/api/public/dataset-items/{dataset_item_id}",
                        auth=AUTH,
                    )
                    di_resp.raise_for_status()
                    di = di_resp.json()
                    raw_input    = di.get("input")    or {}
                    raw_expected = di.get("expectedOutput") or {}
                    item_meta    = safe_meta(di.get("metadata"))

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

                    item_difficulty = item_meta.get("difficulty_level", "") or item_meta.get("difficultyLevel", "")
                    item_category   = item_meta.get("cfa_category", "") or item_meta.get("cfaCategory", "")

                except Exception as e:
                    print(f"\n    ⚠️ dataset_item {dataset_item_id} 获取失败: {e}")
                    question_full = reference = item_difficulty = item_category = ""

                # ── 3.3 Trace：实际回答 / latency / tokens / scores ─
                if not trace_id:
                    continue
                try:
                    tr_resp = requests.get(
                        f"{LANGFUSE_HOST}/api/public/traces/{trace_id}",
                        auth=AUTH,
                    )
                    tr_resp.raise_for_status()
                    trace = tr_resp.json()
                except Exception as e:
                    print(f"\n    ⚠️ trace {trace_id} 获取失败: {e}")
                    continue

                trace_meta = safe_meta(trace.get("metadata"))

                # 分数
                scores_dict = {}
                comments_dict = {}
                for s in trace.get("scores") or []:
                    key = s.get("name", "").lower()
                    scores_dict[key] = s.get("value")
                    comments_dict[key] = s.get("comment") or ""

                # 实际回答 & token 从 GENERATION observation 取
                actual_output, tok_in, tok_out, tok_total = extract_generation(
                    trace.get("observations") or []
                )

                # 模型版本：直接使用 run_name（可按需解析）
                model_name    = trace_meta.get("tested_model") or trace_meta.get("testedModel") or run_name.split("_")[0]
                model_version = run_name   # 完整 run_name 作为版本标识，便于趋势对比

                # 所属领域 & 难度：trace metadata 优先，item metadata 兜底
                domain     = trace_meta.get("cfa_category") or trace_meta.get("cfaCategory") or item_category
                difficulty = trace_meta.get("difficulty_level") or trace_meta.get("difficultyLevel") or item_difficulty

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
                    "Latency (s)":    trace.get("latency"),
                    "总费用($)":      trace.get("totalCost") or trace.get("total_cost") or 0,
                }
                all_rows.append(row)

    # ===============================================================
    # 4. 导出
    # ===============================================================
    if not all_rows:
        print("\n⚠️ 没有采集到任何数据，请检查 TARGETS 配置。")
        return

    df = pd.DataFrame(all_rows)
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"\n✅ 完成！共 {len(df)} 行，已导出至: {OUTPUT_FILE}")
    print(df[["模型名称", "所属领域", "难度", "Accuracy", "Token_Total", "Latency (s)"]].head(10).to_string())


if __name__ == "__main__":
    export_eval_report()