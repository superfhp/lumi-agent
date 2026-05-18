import os
import re
import json
import time
import sys
import argparse
from langfuse import Langfuse
from openai import OpenAI
import concurrent.futures # 🌟 引入并发库

# ================= 1. 初始化客户端 =================
# LANGFUSE_PUBLIC_KEY = "pk-lf-549c72cb-f737-4ace-9b49-cedb0cbd70c7"
# LANGFUSE_SECRET_KEY = "sk-lf-53fc113b-95e9-400a-ab8d-ad877ab03f67"
# LANGFUSE_HOST = "http://8.147.62.209:3000"

# os.environ["LANGFUSE_PUBLIC_KEY"] = LANGFUSE_PUBLIC_KEY
# os.environ["LANGFUSE_SECRET_KEY"] = LANGFUSE_SECRET_KEY
# os.environ["LANGFUSE_HOST"] = LANGFUSE_HOST 

# lf_client = Langfuse(
#     public_key="pk-lf-c74d0e5b-42da-43c5-a9bb-215e19394ef5",
#     secret_key="sk-lf-7e1eb50d-9c83-4c8e-9c9a-ad2cedd6b656",
#     host="http://localhost:3000",
#     timeout=120, 
#     # 开启后台异步重试
#     max_retries=3 
# )
# lf_client = Langfuse(
#     public_key="pk-lf-c74d0e5b-42da-43c5-a9bb-215e19394ef5", # B 的 Public Key
#     secret_key="sk-lf-7e1eb50d-9c83-4c8e-9c9a-ad2cedd6b656", # B 的 Secret Key
#     host="http://localhost:3000" 
# )
LUMI_SECRET_KEY="sk-lf-24f65774-ec0c-4490-bd7f-6cf9635f1d4e"
LUMI_PUBLIC_KEY="pk-lf-ae40d3e8-0b00-4412-9734-c90b2cd77e49"
LUMI_BASE_URL="http://172.16.217.163:3000"
os.environ["LUMI_PUBLIC_KEY"] = LUMI_PUBLIC_KEY
os.environ["LUMI_SECRET_KEY"] = LUMI_SECRET_KEY
os.environ["LUMI_HOST"] = LUMI_BASE_URL 

lumi_client = Langfuse(
    public_key=LUMI_PUBLIC_KEY,
    secret_key=LUMI_SECRET_KEY,
    host=LUMI_BASE_URL,
    timeout=120
)


class _CompatGeneration:
    def __init__(self, generation_obj):
        self._generation = generation_obj

    def end(self, output=None, usage=None, metadata=None, level=None, status_message=None):
        if self._generation is None:
            return

        update_kwargs = {}
        metadata_payload = dict(metadata or {})
        if usage:
            metadata_payload["usage"] = usage
        if level:
            metadata_payload["level"] = level
        if status_message:
            metadata_payload["status_message"] = status_message

        if output is not None:
            update_kwargs["output"] = output
        if metadata_payload:
            update_kwargs["metadata"] = metadata_payload

        try:
            if update_kwargs and hasattr(self._generation, "update"):
                self._generation.update(**update_kwargs)
            if hasattr(self._generation, "end"):
                self._generation.end()
        except Exception as e:
            print(f"      [Langfuse generation.end 兼容层异常]: {e}")


class _CompatTrace:
    def __init__(self, client, trace_obj=None, root_span=None, root_ctx=None, trace_id=None):
        self._client = client
        self._trace = trace_obj
        self._root_span = root_span
        self._root_ctx = root_ctx
        self.trace_id = trace_id

    def generation(self, name, model, input):
        # 旧版 SDK
        if self._trace is not None and hasattr(self._trace, "generation"):
            try:
                return self._trace.generation(name=name, model=model, input=input)
            except Exception as e:
                print(f"      [Langfuse trace.generation 异常]: {e}")

        # 新版 SDK
        if self._root_span is not None and hasattr(self._root_span, "start_observation"):
            try:
                gen_obj = self._root_span.start_observation(
                    name=name,
                    as_type="generation",
                    input=input,
                    model=model,
                    metadata={},
                )
                return _CompatGeneration(gen_obj)
            except Exception as e:
                print(f"      [Langfuse start_observation 异常]: {e}")

        return _CompatGeneration(None)

    def score(self, name, value, comment=""):
        # 旧版 SDK
        if self._trace is not None and hasattr(self._trace, "score"):
            try:
                return self._trace.score(name=name, value=value, comment=comment)
            except Exception:
                pass

        # 新版 SDK（尽力兼容不同方法名）
        for method_name in ("score", "create_score"):
            method = getattr(self._client, method_name, None)
            if method is None:
                continue
            try:
                kwargs = {"name": name, "value": value, "comment": comment}
                if self.trace_id:
                    kwargs["trace_id"] = self.trace_id
                return method(**kwargs)
            except Exception:
                continue

    def update(self, output=None, usage=None, level=None):
        # 旧版 SDK
        if self._trace is not None and hasattr(self._trace, "update"):
            try:
                kwargs = {}
                if output is not None:
                    kwargs["output"] = output
                if usage is not None:
                    kwargs["usage"] = usage
                if level is not None:
                    kwargs["level"] = level
                if kwargs:
                    return self._trace.update(**kwargs)
                return
            except Exception:
                pass

        # 新版 SDK
        if self._root_span is not None and hasattr(self._root_span, "update"):
            try:
                kwargs = {}
                metadata = {}
                if usage is not None:
                    metadata["usage"] = usage
                if level is not None:
                    metadata["level"] = level
                if output is not None:
                    kwargs["output"] = output
                if metadata:
                    kwargs["metadata"] = metadata
                if kwargs:
                    self._root_span.update(**kwargs)
            except Exception as e:
                print(f"      [Langfuse trace.update 兼容层异常]: {e}")

    def close(self):
        if self._root_ctx is not None:
            try:
                self._root_ctx.__exit__(None, None, None)
            except Exception:
                pass


def create_compat_trace(client, name, session_id, input_payload, metadata, tags=None):
    # 旧版 SDK
    if hasattr(client, "trace"):
        trace_obj = client.trace(
            name=name,
            session_id=session_id,
            input=input_payload,
            metadata=metadata,
            tags=tags or [],
        )
        return _CompatTrace(client=client, trace_obj=trace_obj)

    # 新版 SDK
    trace_id = None
    if hasattr(client, "create_trace_id"):
        try:
            trace_id = client.create_trace_id(seed=f"{session_id}::{name}")
        except Exception:
            trace_id = None

    trace_context = {}
    if trace_id:
        trace_context["trace_id"] = trace_id
    if session_id:
        trace_context["session_id"] = session_id

    root_ctx = client.start_as_current_observation(
        trace_context=trace_context or None,
        name=name,
        as_type="chain",
        input=input_payload,
        metadata={**(metadata or {}), "tags": tags or []},
        end_on_exit=False,
    )
    root_span = root_ctx.__enter__()
    return _CompatTrace(client=client, root_span=root_span, root_ctx=root_ctx, trace_id=trace_id)

student_client = OpenAI(
    api_key="sk-67737e76fa2a42319d00f68d67e2ca64",
    base_url="http://47.99.95.132:11434/v1",
    timeout=100
)

EVAL_DATASETS = [
    "Fin-dataset-1"
]

UNIFIED_SYS_PROMPT = "你是一个专业的量化金融分析师。请严格使用【第一步：提取已知量】、【第二步：选择公式】、【第三步：计算】的结构化思维链（Chain of Thought）进行推演。格式必须清晰，最后输出 '最终答案：X'。"


MODELS_CONFIG = [
    {
        "model_id": "qwen3.5:9b",
        "run_prefix": "qwen3.5:9b_0.1_round_test_hpfu",
        "temperature": 0.1,
        "sys_prompt": UNIFIED_SYS_PROMPT
    }
]


# 专用于阅卷的裁判客户端
# 可通过环境变量覆盖：JUDGE_MODEL / JUDGE_BASE_URL / JUDGE_ENABLED(0|1)
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gpt-5.2")
JUDGE_BASE_URL = os.getenv("JUDGE_BASE_URL", "https://gateway.zerail.com/v1")
JUDGE_ENABLED = os.getenv("JUDGE_ENABLED", "1") != "0"
_JUDGE_TEMP_DISABLED = False
judge_client = OpenAI(
    api_key="sk-eModH1YZpV9YVdvc1WLA5mEvwFYbEkrKbSJq0TUbxWKe2y1K",
    base_url=JUDGE_BASE_URL,
    timeout=300
)
# ================= 2. 裁判引擎 (LLM-as-a-Judge) =================
def judge_reasoning(question_text, official_explanation, student_reasoning):
    """
    使用大模型作为裁判，对比学生的推导过程和官方解析，给出过程分和评语。
    """
    global _JUDGE_TEMP_DISABLED

    if not JUDGE_ENABLED:
        return 1.0, "已关闭裁判模型评分（JUDGE_ENABLED=0）"

    if _JUDGE_TEMP_DISABLED:
        return 1.0, "裁判模型不可用，已自动跳过评分"

    # 如果没有官方解析，则无法对比过程，默认给个参考分
    if not official_explanation or official_explanation == "无解析":
        return 1.0, "缺少官方解析，略过过程评分"

    judge_sys_prompt = """
    你是一个严苛且专业的 CFA 阅卷老师。你需要对比【考生推导过程】与【官方解析】。
    评分标准：
    1. 核心逻辑是否正确？是否使用了正确的金融概念和公式？
    2. 计算步骤是否完整？
    3. 允许考生有不同的解题思路，只要逻辑自洽且符合金融常识即可给高分。
    
    请严格返回 JSON 格式，包含两个键：
    "score": 0.0 到 1.0 之间的浮点数 (1.0代表过程完美，0.5代表部分正确，0.0代表完全瞎编或逻辑错误)
    "reason": 一句话的简短评语（指出推导的亮点或致命错误）
    
    只输出 JSON，不要任何多余文本或 Markdown 标记！
    """
    
    judge_user_prompt = f"【题目】\n{question_text}\n\n【官方解析】\n{official_explanation}\n\n【考生推导过程】\n{student_reasoning}"
    
    try:
        response = judge_client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": judge_sys_prompt},
                {"role": "user", "content": judge_user_prompt}
            ],
            temperature=0.0 # 判分必须绝对理性和稳定
        )
        
        raw_output = response.choices[0].message.content.strip()
        # 清理可能存在的 markdown json 标记
        clean_json_str = re.sub(r"```json\s*", "", raw_output)
        clean_json_str = re.sub(r"\s*```$", "", clean_json_str, flags=re.MULTILINE).strip()
        
        result = json.loads(clean_json_str)
        return float(result.get("score", 0.0)), result.get("reason", "未提供有效评语")
        
    except Exception as e:
        err = str(e)
        if "404" in err or "does not exist" in err:
            _JUDGE_TEMP_DISABLED = True
            print("      [裁判引擎告警] 模型/端点不可用，已自动关闭后续裁判评分。")
            print(f"      当前 JUDGE_MODEL={JUDGE_MODEL} | JUDGE_BASE_URL={JUDGE_BASE_URL}")
            print("      可通过环境变量切换，例如: JUDGE_MODEL=qwen-max")
            return 1.0, "裁判模型不可用，自动跳过评分"

        print(f"      [裁判引擎异常]: {e}")
        return 0.0, f"判分失败: {str(e)}"

# ================= 3. 核心评测逻辑 =================


MAX_CONCURRENT_WORKERS = 10

COMPLETED_SESSIONS = {
}
def evaluate_single_question(item, dataset_name, model_config, run_name, q_index, total_items):
    """
    独立处理单道题目的函数，设计为可以被线程池安全调用的无状态函数
    """
    model_name = model_config["model_id"]
    sys_prompt = model_config["sys_prompt"]
    current_temp = model_config.get("temperature", 0.5)
    
    # 🌟 兼容不同的字段名：有些数据集用 question_number，有些没有
    q_num = item.metadata.get("question_number", "") if item.metadata else ""
    
    # 🌟 提取 input，兼容字段名差异
    input_data = item.input or {}
    bg = input_data.get("background_context", "") or input_data.get("background", "")
    q = input_data.get("question", "")
    opts = input_data.get("options", {})
    opts_str = "\n".join([f"{k}: {v}" for k, v in opts.items()])
    
    # 🌟 兼容缺失背景的数据集：动态构造提示词
    if bg:
        user_prompt = f"【背景材料】\n{bg}\n\n【题目】\n{q}\n\n【选项】\n{opts_str}"
    else:
        user_prompt = f"【题目】\n{q}\n\n【选项】\n{opts_str}"
    
    trace_metadata = item.metadata.copy() if item.metadata else {}
    trace_metadata.update({
        "tested_model": model_name,
        "temperature": current_temp,
        "prompt_style": model_config['run_prefix'],
        "dataset_source": dataset_name
    })

    # 构造 Trace 级别的输入信息
    trace_input = {
        "question_number": q_num,
        "background_context": bg,
        "question": q,
        "options": opts,
        "model": model_name,
        "temperature": current_temp
    }

    # 创建独立的 Trace，并在初始化时就上报 input
    trace = create_compat_trace(
        client=lumi_client,
        name="Fin_Gradient_Test",
        session_id=run_name,
        input_payload=trace_input,
        metadata=trace_metadata,
        tags=[model_name, dataset_name, f"Temp_{current_temp}"],
    )
    
    messages_to_llm = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt}
    ]
    
    # 构造结构化的输入信息，用于Langfuse上报
    structured_input = {
        "messages": messages_to_llm,
        "question_number": q_num,
        "background": bg[:300] + "..." if len(bg) > 300 else bg,
        "question": q[:200] + "..." if len(q) > 200 else q,
        "options": opts
    }
    
    generation = trace.generation(
        name="model_reasoning",
        model=model_name,
        input=structured_input
    )
    
    try:
        start_time = time.time()
        
        # 为了防止多线程瞬间打爆 API，可以引入微小的随机抖动 (Jitter)
        # time.sleep(random.uniform(0.1, 0.5)) 
        
        response_stream = student_client.chat.completions.create(
            model=model_name,
            messages=messages_to_llm,
            temperature=current_temp,
            stream=True,
            stream_options={"include_usage": True},
            extra_body={"thinking": {"type": "disabled"}}  # 🌟 禁用 thinking 模式以提升效率
        )
        
        full_content, full_reasoning = "", ""
        prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
        finish_reason = "stop"

        for chunk in response_stream:
            if not chunk.choices: 
                if hasattr(chunk, 'usage') and chunk.usage:
                    prompt_tokens = chunk.usage.prompt_tokens
                    completion_tokens = chunk.usage.completion_tokens
                    total_tokens = chunk.usage.total_tokens
                continue
                
            delta = chunk.choices[0].delta
            
            if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                full_reasoning += delta.reasoning_content
            if hasattr(delta, 'content') and delta.content:
                full_content += delta.content
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason

        latency_seconds = round(time.time() - start_time, 2)

        if "<think>" in full_content:
            think_match = re.search(r'<think>(.*?)</think>', full_content, re.DOTALL)
            if think_match:
                extracted_think = think_match.group(1).strip()
                if not full_reasoning: full_reasoning = extracted_think
                full_content = re.sub(r'<think>.*?</think>', '', full_content, flags=re.DOTALL).strip()

        # 先提取模型答案选择
        match = re.search(r'最终答案[：:]?\s*([A-C])', full_content, re.IGNORECASE)
        model_choice = match.group(1).upper() if match else (re.findall(r'\b([A-C])\b', full_content)[-1].upper() if re.findall(r'\b([A-C])\b', full_content) else "UNKNOWN")
        
        # 构造结构化的输出信息，用于Langfuse上报
        structured_output = {
            "content": full_content,
            "reasoning_process": full_reasoning,
            "model_choice": model_choice
        }
        
        # 参考report_analyse.py的做法，完整上报input/output token和metadata
        generation.end(
            output=structured_output, 
            usage={
                "input": prompt_tokens,
                "output": completion_tokens,
                "total": total_tokens
            },
            metadata={
                "latency_sec": latency_seconds,
                "finish_reason": finish_reason,
                "model": model_name,
                "temperature": current_temp
            }
        )

        # 🌟 兼容不同的字段名：有些数据集用 correct_answer，有些用 answer
        expected_output = item.expected_output or {}
        correct_answer = (
            expected_output.get("correct_answer") or 
            expected_output.get("answer") or 
            ""
        ).upper()
        
        accuracy_score = 1.0 if model_choice == correct_answer else 0.0

        trace.score(name="accuracy", value=accuracy_score, comment=f"预期: {correct_answer} | 实际: {model_choice}")
        
        # ⚠️ 注意：如果你依然想用裁判模型，裁判模型也算一次 API 调用。并发时也要算入配额。
        # 🌟 兼容不同的字段名：有些数据集用 official_explanation，有些用 explanation
        official_explanation = (
            expected_output.get("official_explanation") or 
            expected_output.get("explanation") or 
            ""
        )
        student_reasoning_for_judge = f"【思考】\n{full_reasoning}\n\n【作答】\n{full_content}"
        reasoning_score, judge_feedback = judge_reasoning(f"【背景】\n{bg}\n\n【题目】\n{q}", official_explanation, student_reasoning_for_judge)
        
        trace.score(name="reasoning_quality", value=reasoning_score, comment=f"裁判点评: {judge_feedback}")
        
        # 🌟 在 Trace 级别上报完整的输出信息
        trace_output = {
            "model_choice": model_choice,
            "expected_choice": correct_answer,
            "reasoning_process": full_reasoning,
            "full_response": full_content,
            "accuracy_score": accuracy_score,
            "reasoning_quality_score": reasoning_score,
            "judge_feedback": judge_feedback
        }
        
        trace.update(
            output=trace_output,
            usage={
                "input": prompt_tokens,
                "output": completion_tokens,
                "total": total_tokens
            }
        )
        
        
        try:
            item.link(trace, run_name)
        except Exception:
            # 某些 SDK 版本要求传 trace_id；若都不支持则忽略，不影响主流程
            try:
                if getattr(trace, "trace_id", None):
                    item.link(trace.trace_id, run_name)
            except Exception:
                pass
        
        # 多线程打印容易乱序，用统一的格式输出
        status = "✅" if accuracy_score == 1.0 else "❌"
        return f"[{q_index}/{total_items}] 题号 {q_num} | {status} 选{model_choice}(应{correct_answer}) | 耗时: {latency_seconds}s"

    except Exception as e:
        error_output = {
            "error": str(e),
            "error_type": type(e).__name__
        }
        trace.update(output=error_output, level="ERROR")
        generation.end(level="ERROR", status_message=str(e))
        trace.update(level="ERROR")
        return f"[{q_index}/{total_items}] 题号 {q_num} | ⚠️ 异常失败: {str(e)[:300]}"
    finally:
        trace.close()

# ================= 3. 线程池分发引擎 =================
def run_evaluation_for_model_concurrent(dataset_name: str, model_config: dict, round_num: int = 1, start_question_index: int = 0):
    """支持多轮评估的核心函数
    Args:
        dataset_name: 数据集名称
        model_config: 模型配置
        round_num: 轮次（1或2）
        start_question_index: 从第几道题开始（基于1的索引，0表示从第1道开始）
    """
    model_name = model_config["model_id"]
    run_name = f"{model_config['run_prefix']}_{dataset_name}_round{round_num}"
    if run_name in COMPLETED_SESSIONS:
        print(f"\n" + "="*70)
        print(f"⏭️ [缓存命中] 批次 [{run_name}] 已在完成名单中，直接跳过！")
        print("="*70)
        return
    print(f"\n" + "="*70)
    print(f"⚡ 启动并发引擎 | 模型: [{model_name}] | 轮次: [Round {round_num}] | 线程数: [{MAX_CONCURRENT_WORKERS}]")
    print(f"📌 批次名: {run_name}")
    print("="*70)

    try:
        dataset = lumi_client.get_dataset(dataset_name)
    except Exception as e:
        print(f"❌ 找不到数据集 {dataset_name}，报错: {e}")
        return

    total_items = len(dataset.items)
    print(f"   共 {total_items} 道题，开始火力全开！\n")
    
    # 🌟 支持断点续传：从指定题号开始
    if start_question_index > 0:
        print(f"   ⚠️ 断点续传模式：从第 {start_question_index + 1} 题开始（跳过前 {start_question_index} 题）\n")

    # 🌟 核心：使用 ThreadPoolExecutor 进行线程池管理
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORKERS) as executor:
        # 提交所有任务到线程池（从 start_question_index 开始）
        future_to_item = {
            executor.submit(
                evaluate_single_question, item, dataset_name, model_config, run_name, index+1, total_items
            ): item for index, item in enumerate(dataset.items) if index >= start_question_index
        }
        
        # as_completed 会在某个线程完成时立刻返回结果
        for future in concurrent.futures.as_completed(future_to_item):
            try:
                result_log = future.result()
                print(result_log) # 打印独立函数的返回结果，保持控制台整洁
            except Exception as exc:
                print(f"⚠️ 线程致命崩溃: {exc}")

    # 并发结束后统一冲刷缓冲区
    lumi_client.flush()
    print(f"\n{'='*70}")
    print(f"🎉 任务 [{run_name}] 并发跑批全部完成！")
    print(f"{'='*70}")

if __name__ == "__main__":
    # 🌟 命令行参数：支持断点续传
    parser = argparse.ArgumentParser(description="CFA 多模型评测系统")
    parser.add_argument(
        "--start-question", 
        type=int, 
        default=0,
        help="从第几道题开始评测（基于1的题号，例如 --start-question 50 表示从第50题开始），默认为0（从头开始）"
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="仅测试指定模型，例如 --model safety"
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default=None,
        help="仅测试指定数据集，例如 --dataset CFA-Level1-2018"
    )
    parser.add_argument(
        "--round",
        type=int,
        choices=[1, 2],
        default=None,
        help="仅测试指定轮次（1或2）"
    )
    
    args = parser.parse_args()
    start_question_idx = max(0, args.start_question - 1) if args.start_question > 0 else 0  # 转换为0-based索引
    
    print(f"\n{'='*70}")
    print(f"🚀 启动评测任务")
    print(f"📊 模型数: {len(MODELS_CONFIG)}")
    print(f"📚 数据集数: {len(EVAL_DATASETS)}")
    print(f"🔄 每个模型轮次: 2轮")
    print(f"📈 总计任务数: {len(MODELS_CONFIG) * len(EVAL_DATASETS) * 2}")
    if start_question_idx > 0:
        print(f"⚠️ 断点续传：将从第 {start_question_idx + 1} 题开始")
    if args.model:
        print(f"🎯 仅测试模型: {args.model}")
    if args.dataset:
        print(f"🎯 仅测试数据集: {args.dataset}")
    if args.round:
        print(f"🎯 仅测试轮次: Round {args.round}")
    print(f"{'='*70}\n")
    
    # 三重循环：模型 → 轮次 → 数据集
    for model_idx, m_config in enumerate(MODELS_CONFIG, 1):
        model_name = m_config["model_id"]
        
        # 🌟 模型过滤
        if args.model and model_name != args.model:
            continue
        
        print(f"\n{'#'*70}")
        print(f"📋 模型 [{model_idx}/{len(MODELS_CONFIG)}]: {model_name}")
        print(f"{'#'*70}")
        
        for round_num in [1, 2]:
            # 🌟 轮次过滤
            if args.round and round_num != args.round:
                continue
            
            print(f"\n{'*'*70}")
            print(f"🔄 轮次: Round {round_num}/2")
            print(f"{'*'*70}")
            
            for ds_idx, ds_name in enumerate(EVAL_DATASETS, 1):
                # 🌟 数据集过滤
                if args.dataset and ds_name != args.dataset:
                    continue
                
                print(f"\n📊 数据集 [{ds_idx}/{len(EVAL_DATASETS)}]: {ds_name}")
                run_evaluation_for_model_concurrent(
                    dataset_name=ds_name, 
                    model_config=m_config,
                    round_num=round_num,
                    start_question_index=start_question_idx
                )
                # 切换数据集时，给服务器一点喘息时间
                time.sleep(5)
            
            # 切换轮次时，给服务器更多休息时间
            print(f"\n⏳ Round {round_num} 完成，准备开始 Round {round_num + 1}...")
            time.sleep(10)
        
        # 切换模型时，给服务器足够的休息时间
        if model_idx < len(MODELS_CONFIG):
            print(f"\n⏳ 模型 {model_name} 全部完成，准备切换到下一个模型...")
            time.sleep(15)
    
    print(f"\n{'='*70}")
    print(f"✅ 全部评测任务完成!")
    print(f"{'='*70}")