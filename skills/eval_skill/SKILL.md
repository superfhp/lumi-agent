---
name: eval_skill
description: "【多领域评测入口：common/finance/medical/...】用户说‘帮我跑一次<领域>评测’时，禁止 search/grep/扫描本地 CSV/读取历史会话来猜 dataset；必须直接运行 eval_skill CLI：source /mnt/workspace/lumi-agent/venv/bin/activate && python -m eval_skill.cli list-datasets --domain <领域>。领域必须按用户意图严格映射：通用领域=common，金融领域=finance。只展示 CLI 返回的 metadata.domain=<领域> 数据集；用户确认 dataset/model/judge/rules 前禁止 cli run。不要使用 lumi-llm-eval-pipeline。"
version: 2.1.0
author: eval_skill
license: MIT
metadata:
  hermes:
    tags: [Eval, LLM, Benchmark, Common, General, Finance, Financial, Dataset, Lumi, Langfuse, RVEC, ModelComparison]
    related_skills: []
---

# eval_skill — 评测执行 SKILL

## Quick Reference（Hermes 优先读取）

| 场景 | 必须做什么 | 禁止做什么 |
|---|---|---|
| 用户说「帮我跑一次通用领域评测」 | 先执行 `source /mnt/workspace/lumi-agent/venv/bin/activate && python -m eval_skill.cli list-datasets --domain common` | 禁止 search/grep/ls/find/扫描本地 CSV/读取历史会话来猜 dataset；禁止改用 finance |
| 用户说「帮我跑一次金融领域评测」 | 先执行 `source /mnt/workspace/lumi-agent/venv/bin/activate && python -m eval_skill.cli list-datasets --domain finance` | 禁止 search/grep/ls/find/扫描本地 CSV/读取历史会话来猜 dataset；禁止改用 common |
| 第 1 步列 dataset | 只展示 CLI 返回的 `metadata.domain=<用户指定领域>` 结果 | 禁止展示其他 domain 的 dataset |
| 第 2 步预览 dataset | 向用户展示样例的 category、prompt、answer/rubric；预览末尾的全量评测集链接一并转发给用户 | 禁止在预览中展示预期信号 expected_signals |
| 用户未确认 dataset/model/judge/rules | 只能 list / preview / describe / dry-run | 禁止 `python -m eval_skill.cli run ...` |
| 执行结果汇报 | 必须给出 `samples.csv`、`summary.json` 路径；转发 CLI 输出的评测结果表格（question/expected/actual/score/reason）和 Langfuse 实验链接 | 禁止只报均值不报产物路径和明细 |

执行位置要求：`python -m eval_skill.cli ...` 必须在 **eval_skill 包的父目录**执行。Hermes 环境默认使用：

```bash
cd /mnt/workspace/lumi-agent/skills
source /mnt/workspace/lumi-agent/venv/bin/activate
export PYTHONPATH=/mnt/workspace/lumi-agent/skills:$PYTHONPATH
```

不要用 `python - <<'PY' ... PY` / `python -c ...` / `bash -lc ...` 做探测；这类嵌套 heredoc 很容易引入 quoting 错误或触发审批拦截。若 `python -m eval_skill.cli ...` 报 `ModuleNotFoundError: No module named 'eval_skill'`，**优先按工作目录/PYTHONPATH 丢失处理**：必须回到 `/mnt/workspace/lumi-agent/skills` 并设置 `PYTHONPATH` 后重跑同一条 CLI。只有在该目录下仍失败，才报告部署问题；不要 search/扫 CSV 代替。

若用户说「通用领域评测」，第一条业务命令只能是：

```bash
cd /mnt/workspace/lumi-agent/skills
source /mnt/workspace/lumi-agent/venv/bin/activate
export PYTHONPATH=/mnt/workspace/lumi-agent/skills:$PYTHONPATH
python -m eval_skill.cli list-datasets --domain common
```

如果这条命令没有返回可用 dataset，直接告诉用户「当前 Lumi 没有 metadata.domain=common 的可用 dataset」，不要退回 finance。

若用户说「金融领域评测」，同理把 domain 换成 `finance`：

```bash
cd /mnt/workspace/lumi-agent/skills
source /mnt/workspace/lumi-agent/venv/bin/activate
export PYTHONPATH=/mnt/workspace/lumi-agent/skills:$PYTHONPATH
python -m eval_skill.cli list-datasets --domain finance
```

如果这条命令没有返回可用 dataset，直接告诉用户「当前 Lumi 没有 metadata.domain=finance 的可用 dataset」，不要退回 common。

## 0. Hermes 路由与安全执行硬规则（必须遵守）

当用户说「帮我跑一次通用领域评测 / common 评测 / 通用 benchmark / 模型横评」时，**必须使用本 SKILL**，不要使用旧技能 `lumi-llm-eval-pipeline`。

原因：`lumi-llm-eval-pipeline` 是旧的硬编码脚本入口，容易默认跑到金融数据集（如 `Fin-dataset-1`），不具备 dataset 发现、预览、规则确认、prompt/pack dry-run 这些评测前交互步骤。

硬规则：
- **必须使用指定虚拟环境**：所有命令执行前先激活 `source /mnt/workspace/lumi-agent/venv/bin/activate`；不要使用系统 Python，也不要临时创建新虚拟环境。
- **禁止用搜索结果代替 CLI**：收到「<领域>评测」后，第一步只能运行 `python -m eval_skill.cli list-datasets --domain <领域>`；不能先 `search` 历史会话、不能 grep/ls 本地文件、不能扫描 `/mnt/workspace/data/*.csv`、不能根据文件名推断 dataset。
- **domain 必须严格等于用户意图**：通用领域固定 `domain=common`；金融领域固定 `domain=finance`；其他领域按用户指定或确认后的 domain。第 1 步只展示 Lumi dataset metadata 里该 domain 的结果，不能混入其他 domain。
- **common 与 finance 不能互相 fallback**：用户要 common 时，`Fin-*`、`finance`、`financial`、`Fin-Compliance`、`Fin-Economics`、`Fin-Investing`、`Fin-Literacy`、`Fin-Quantitatics` 不能列给用户；用户要 finance 时也不能展示 common dataset。
- **禁止直接执行评测**：如果用户只说「跑一次通用领域评测」，但没有明确 dataset、模型、judge、评测规则，必须先走 §3.0 的 5 步流程，不能先 `cli run`。
- **禁止替用户选择标准 benchmark**：不要擅自改成 MMLU / C-Eval / GSM8K / BBH / HumanEval，除非 lumi 的 `list-datasets --domain ...` 结果里确实有这些 dataset，且用户确认选择。
- **禁止使用硬编码金融脚本替代通用评测**：不能把 `Fin-dataset-1` 或任何金融 dataset 当成「通用领域评测」。
- **必须先回显给用户确认**：dataset 名称、前 5 条样例摘要、MUT 模型、baseline、judge、metric/pack/caps、预计调用量，都要在执行前展示。
- **只有用户确认后才能执行**：用户确认之前最多能运行 `list-datasets`、`preview-dataset`、`describe-config`、`upload-* --dry-run`，不能实际跑模型调用。

## 1. 何时调用我
当用户表达以下任一意图时，使用本 SKILL：
- 「跑一次评测 / 对比 / ablation / horizontal eval」
- 「拿 dataset XX 跑模型 Y，加上 baseline ZZ」
- 「帮我跑一次通用领域评测 / common 领域评测 / 通用 benchmark」
- 「对 dataset 抽 N 条评一下 reasoning quality / accuracy / array_recall …」
- 「换一个 prompt / 换一个 judge 重评一下」
- 「多轮对话评测 / 长 PDF 评测 / 数字答案评测」

## 2. 我能做什么
- 加载 Lumi/Langfuse 上的 v2 dataset（`input/expected/metadata`）。
- 自动适配 4 种输入形态：结构化 fields、prompt-baked、`turns` 多轮、附带 PDF 引用。
- 长 PDF 在运行时按关键词 `smart` 抽取或 `full` 抽取，带磁盘缓存与脱敏。
- 客观指标：`accuracy`/`exact_match`/`contains`/`array_recall`/`array_f1`/`numeric_match`。
- 主观指标：`reasoning_quality`/`factuality_score`/`rubric_judge`/`custom_judge`，每条结果都带 `score + reason`。
- 同一指标可通过 `alias` 跑多个 prompt 变体并行打分。
- model_under_test + 任意多 baselines 横向对比；rounds 多轮重复实验。
- 输出 `outputs/<experiment_name>/samples.csv` 明细 + `summary.json` 聚合；可选 push 到 Lumi trace（带 dataset_item link）。
- 断点续跑：识别 `samples.csv` 中已完成的 `(sample_id, model, run_prefix, round)` 跳过。
- 直接上传：单 prompt 文件、整套 RVEC 评测包（pack.yaml + 6 step prompt）、本地 csv·jsonl 数据，一条命令分别落到 `prompts/uploads/`、`prompts/uploads/packs/` 或 Lumi，无需在对话里逐句调（详见 §3）。

## 3. 关键命令

## Procedure（Hermes 必须严格按顺序执行）

### Step 0 · 激活固定虚拟环境
```bash
cd /mnt/workspace/lumi-agent/skills
source /mnt/workspace/lumi-agent/venv/bin/activate
export PYTHONPATH=/mnt/workspace/lumi-agent/skills:$PYTHONPATH
```

说明：`/mnt/workspace/lumi-agent/skills` 必须是 `eval_skill/` 包目录的父目录，即应存在 `/mnt/workspace/lumi-agent/skills/eval_skill/__init__.py`。如果之前曾在该目录成功执行过 `python -m eval_skill.cli ...`，则 `ModuleNotFoundError` 基本就是当前命令没有在该目录执行或 `PYTHONPATH` 丢失；先按 Step 0 重置环境后重跑，不要做额外搜索。

### Step 1 · 只列出用户指定 domain 的 Lumi dataset
```bash
python -m eval_skill.cli list-datasets --domain <domain>
```

执行要求：
- 领域映射：用户说「通用领域」→ `<domain>=common`；用户说「金融领域」→ `<domain>=finance`；其他领域不明确时先问用户确认 domain。
- 这一步**必须直接运行上面的 CLI**，不得调用 search / grep / ls / find / 读取历史会话 / 扫描本地 CSV。
- 不得为了排查 import 问题而运行 `python - <<'PY' ...`、`python -c ...`、`bash -lc ...` 这类嵌套脚本；如果导入失败，直接报告部署/PYTHONPATH 问题。
- 输出只允许来自 `list-datasets --domain <domain>` 的结果。
- 如果 CLI 没返回 dataset，就告诉用户「当前 Lumi 没有 metadata.domain=<domain> 的可用 dataset」，不要改列其他 domain。

### Step 2 · 用户选择后预览 5 条
```bash
python -m eval_skill.cli preview-dataset --name <用户选择的dataset> --limit 5
```

输出要求：
- 预览展示样例的 category、prompt、answer/rubric，不展示预期信号（expected_signals）。
- CLI 末尾会输出「🔗 查看全量评测集」链接，必须一并转发给用户。
- 预览完成后必须停下问用户是否继续第 3 步；不要自动进入 `describe-config`。

### Step 3 · 展示评测计划，不实际执行
```bash
python -m eval_skill.cli describe-config -c <对应yaml>
```

执行前检查：
- `execution.reporter` 必须包含 `csv`，否则不会写明细 CSV。
- 如用户希望 Lumi 上看到 Experiments / trace，`execution.reporter` 必须包含 `lumi`。
- 默认推荐：`reporter: [csv, lumi]`。

### Step 4 · 如需上传 prompt/pack/dataset，只能先 dry-run
```bash
python -m eval_skill.cli upload-prompt --file <file> --kind <kind> --slug <slug> --dry-run
python -m eval_skill.cli upload-prompt-pack --file <pack.yaml> --type rvec --slug <slug> --mode lite --dry-run
python -m eval_skill.cli upload-dataset --file <file> --name <dataset> --dry-run
```

### Step 5 · 用户确认后才执行
```bash
python -m eval_skill.cli run -c <yaml>
```

默认行为：`cli run` 会确保 reporter 至少包含 `csv` 和 `lumi`，因此会写本地 CSV/summary，并尝试上传 Lumi trace / Experiments。只有明确本地调试时才加 `--no-lumi`。

执行后必须向用户回显：
- `samples.csv` 路径（来自 `[reporter] CSV 已写入:` 或 `[evaluator] done. samples.csv =>`）
- `summary.json` 路径
- CLI 输出的评测结果表格（包含 question / expected / actual / score / reason）必须原样转发给用户
- CLI 输出的 Langfuse 实验结果链接（🔗）必须原样转发给用户
- 若使用 `rvec_judge`，Lumi trace 里必须能看到 step 级 observation/span：`step1_understand`、`step2_R`、`step2_V`、`step2_E`、`step2_C`、`aggregate`、`step3_scoring`；trace output / CSV 里必须包含命中的 `bad_tags_json` / `good_tags_json`（同 `bad_signals_json` / `good_signals_json`）。

### 3.0 标准评测流程：评测前 5 步（重要 — 用户没指定 yaml 时务必照走）

接到「跑评测」任务时不要直接 `cli run`。完整流程是 5 步，每步都让用户先确认再走下一步：

| # | 步骤 | 用什么 | 要拿到什么 |
|---|---|---|---|
| 1 | **列评测集** | `cli list-datasets --domain <domain>`；通用=common，金融=finance | 只展示 metadata.domain=<domain> 的 dataset 清单 |
| 2 | **预览数据** | `cli preview-dataset --name <Y> --limit 5` | 5 条样例 + schema/turn_kind/answer 展示；末尾附带全量评测集链接 |
| 3 | **看评测规则** | `cli describe-config -c <yaml>` | yaml 里 metric / pack / caps / 模型 / reporter / 预计 LLM 调用量；确认 reporter 含 `csv`，需要 Lumi trace 时含 `lumi` |
| 4a | **（可选）改 prompt 包** | `cli upload-prompt[-pack] --dry-run` 先看回显 | 占位符列表 / pack 元信息 / signals 抽样；用户确认后去掉 `--dry-run` 真传 |
| 4b | **（可选）改 dataset** | `cli upload-dataset --dry-run` | 解析后的 5 条预览；用户确认后真传 |
| 5 | **执行** | `cli run -c <yaml>` | 实际跑一次；结束后必须转发 `samples.csv`、`summary.json`、评测结果表格、Langfuse 实验链接 |

约定：
- 用户说「通用领域评测」时，`domain` 固定为 `common`；用户说「金融领域评测」时，`domain` 固定为 `finance`。不要自行替换成其他 domain，也不要从本地文件/历史搜索结果归纳候选集。
- **dataset 必须在 lumi 上有 `metadata.domain` 标记**（如 `{"domain":"common"}`）才会出现在 list-datasets 里。没 metadata 的视作废弃。
- **upload-* 一律先 `--dry-run`**：上传 prompt / pack / dataset 之前都先 dry-run 一遍把"我准备落到哪、占位符 / signals / 5 条预览"过给用户看，得到确认再真传。
- **describe-config 是合同**：一旦用户确认了它输出的 plan，后续 `cli run` 就照那份执行；如果用户想改 metric / 模型，回到 yaml 改完再 describe-config 再跑。

### 3.1 命令清单

```bash
# 所有命令执行前必须先激活指定虚拟环境
cd /mnt/workspace/lumi-agent/skills
source /mnt/workspace/lumi-agent/venv/bin/activate
export PYTHONPATH=/mnt/workspace/lumi-agent/skills:$PYTHONPATH

# 跑实验（单 yaml）
python -m eval_skill.cli run -c eval_skill/configs/examples/finance_compliance.yaml

# 本地调试时才关闭 Lumi 上传
python -m eval_skill.cli run -c <yaml> --no-lumi

# 跑多个 yaml（彼此独立的实验，按顺序串跑）
python -m eval_skill.cli run \
  -c eval_skill/configs/examples/finance_compliance.yaml \
  -c eval_skill/configs/examples/reasoning_quality_variants.yaml

# 批量加载某目录下所有 yaml
python -m eval_skill.cli run --config-dir eval_skill/configs/examples --pattern '*.yaml'

# 抽样 3 条快速验证
python -m eval_skill.cli run -c <yaml> --sample 3 --no-resume

# 列出已注册指标
python -m eval_skill.cli list-metrics

# 抽样校验 dataset 是否符合 v2/v2.1 schema
python -m eval_skill.cli validate-dataset --name Fin-Compliance --limit 20

# 列出某领域的评测集（评测前流程第 1 步）
python -m eval_skill.cli list-datasets --domain common

# 预览某 dataset 前 N 条 + 整体统计（评测前流程第 2 步）
python -m eval_skill.cli preview-dataset --name common-dataset-v3 --limit 5

# 渲染评测计划（评测前流程第 3 步，给用户确认；不实际跑）
python -m eval_skill.cli describe-config -c eval_skill/configs/examples/general_rvec_demo.yaml

# 把旧 Lumi dataset 迁移为 v2（推荐先 --dry-run 5 看一眼）
python -m eval_skill.tools.migrate_legacy_dataset \
  --source Fin-Compliance-old --target Fin-Compliance \
  --export-jsonl backup/fc.jsonl

# 原地更新现有 dataset item，保留评分历史（要求 source == target）
# 适用场景：字段修复 / v2 升级，想让之前的 trace・评分继续生效
python -m eval_skill.tools.migrate_legacy_dataset \
  --source Fin-Compliance --target Fin-Compliance --upsert

# 把外部编辑器写好的单个 prompt 直接落到 prompts/uploads/，跳过对话调
python -m eval_skill.cli upload-prompt \
  --file ~/Desktop/strict_judge.md --kind judge --slug strict_v2

# 上传一套 RVEC 评测包（3 档起点）
#   ① 拿一份带注释的 pack.yaml 模板（唯一需要用户填的文件）
python -m eval_skill.cli init-pack --type rvec --to ~/work/rvec_finance.yaml
#   ② 把你领域的标签集填进去，然后 lite 上传（6 个 step prompt 自动复用 rvec_general）
python -m eval_skill.cli upload-prompt-pack \
  --file ~/work/rvec_finance.yaml --type rvec --slug rvec_finance_v1 --mode lite --overwrite

#   进阶：merge 模式，额外重写 step3_scoring.md（调评分阈值），其他不变
python -m eval_skill.cli upload-prompt-pack \
  --dir ~/work/rvec_finance --type rvec --slug rvec_finance_v2 --mode merge --overwrite

#   进阶：strict 模式，要求 7 文件齐全（连流程 prompt 都重写时才用）
python -m eval_skill.cli upload-prompt-pack \
  --dir ~/work/rvec_finance_full --type rvec --slug rvec_finance_strict --mode strict

# 把本地 jsonl/csv 直接推成 v2 dataset 到 Lumi
python -m eval_skill.cli upload-dataset --file ./eval.jsonl --name MyEval-v1
python -m eval_skill.cli upload-dataset --file ./eval.csv --name MyEval-v1 \
  --csv-schema single_choice --csv-input-keys question,options \
  --csv-expected-key answer --csv-metadata-keys domain --dry-run
```

> upload-prompt 适用场景：**对着 OpenWebUI 一句句调 prompt 太慢**，用编辑器写好直接 upload，命令打印的 `prompts/uploads/<kind>/<slug>.md` 拷进 yaml 的 `prompt_ref` / `system_prompt_ref` / `user_template_ref` 即可。
>
> upload-prompt-pack 三档模式、三个场景：
> - **lite**（默认、推荐）：只需 1 份 `pack.yaml`（6 个 step prompt 从 `rvec_general` 兼底）。默认接受任意 `.yaml/.yml` 文件名，落盘时统一改为 `pack.yaml`。**适用 95% 场景（分换标签、流程不变）**。
> - **merge**：你给什么用什么，缺的从模板兼底。适用改了标签 + 调了评分阈值 这类中等定制。
> - **strict**：7 文件齐全，零兼底。仅在需要完全重写流程时使用。
>
> init-pack 适用场景：**用户手里只有一份 RVEC 设计文档（markdown / word / wiki）**，pack.yaml 是机器要解析的、不可避免要结构化拆出来。init-pack 吐一份带注释的模板讲明每个字段怎么填，用户只需拆这一份。
>
> upload-dataset 强校验 v2（`metadata.schema` 必填、`expected_output.answer` 存在），不合规直接拒绝；csv 模式下 `--csv-schema=array` 时字符串答案 `"1, 3; 5"` 会自动切成 list。
>
> migrate_legacy_dataset 场景分化：
> - **默认模式**：追加新增 item，原旧 dataset 保留，新 item 和旧 item 并存（用于灰度测试）。
> - **`--upsert` 模式**：用原 item id 覆盖更新现有 item，保留该 item 上所有历史 trace/评分记录。适用数据修复或格式升级后直接替换（旧 trace 继续生效，新增 trace 记在原 item 上）。
>   - ⚠️ **要求 `--source == --target`**：langfuse 的 dataset item id 全局唯一、不能跨 dataset 复用，跨 dataset 复用会 404。
>   - 如果只是个别 item 在旧 dataset 中已被删，脚本会跳过并打印警告，不会中断整体迁移。

### 多 dataset 一份 yaml
如果只是同一组模型 / 指标 / prompt，**横扫多个 dataset**，把 `dataset.name` 写成 list 即可，运行时会展开成多个独立实验，`experiment_name` 自动后缀 `__<dataset>`：

```yaml
dataset:
  name:
    - Fin-Compliance
    - Fin-Economics
    - Fin-Investing
```

完整示例：[configs/examples/finance_horizontal_multi.yaml](configs/examples/finance_horizontal_multi.yaml)。

各 dataset 的 prompt / metric 不同 → 还是写多份 yaml，再用 `-c a -c b ...` 或 `--config-dir` 批跑。

## 4. 密钥与环境变量
**所有 api_key / Lumi 凭据都通过环境变量注入，不要写进任何 yaml / py。**

### 4.0 Python 虚拟环境（Hermes 必须使用）

Hermes 执行本 SKILL 的任何命令前，必须进入 `eval_skill` 包的父目录、激活固定虚拟环境，并设置 `PYTHONPATH`：

```bash
cd /mnt/workspace/lumi-agent/skills
source /mnt/workspace/lumi-agent/venv/bin/activate
export PYTHONPATH=/mnt/workspace/lumi-agent/skills:$PYTHONPATH
```

约束：
- 不使用系统 Python。
- 不临时创建新虚拟环境。
- 不使用项目本地其他 venv，除非用户明确要求切换。
- 后续所有命令均以该环境里的 `python -m eval_skill.cli ...` 执行。
- 不使用 `bash -lc` / `python -c` / heredoc 嵌套脚本做环境探测；这类命令容易触发审批或 quoting 错误。
- 若出现 `ModuleNotFoundError: No module named 'eval_skill'`，优先认为是工作目录/PYTHONPATH 问题：回到 `/mnt/workspace/lumi-agent/skills`、重新 `source` venv、重新 `export PYTHONPATH` 后直接重跑原 CLI。若仍失败，再报告 `/mnt/workspace/lumi-agent/skills/eval_skill/__init__.py` 可能不存在或代码未部署；不能用搜索/本地 CSV 替代 CLI 结果。

eval_skill 不自己管密钥；这部分由 [skill_commons](../skill_commons/README.md) 统一负责，多个 skill（评测执行 / 报告生成 / 数据集上传）共用一份配置：

```bash
# 一次配置，所有 skill 受益
cp skill_commons/.env.example skill_commons/.env
vim skill_commons/.env     # 填好 LUMI_* / ZERAIL_API_KEY / IQUEST_API_KEY ...
```

加载顺序（先到先得，后续不覆盖）：
1. 进程已有 env（CI / shell `export`）
2. `$SKILL_COMMONS_ENV_FILE` 指向的文件
3. `skill_commons/.env`

### 4.1 与 Hermes 的 .env 完全隔离
| 文件 | 谁管理 | 放什么 |
|---|---|---|
| `<repo_root>/.env` | Hermes gateway | `HERMES_*` / `OPENAI_BASE_URL` / `API_SERVER_*`（运行态） |
| `skill_commons/.env` | 评测/报告/上传 共用 | `LUMI_*` / `<HOST>_API_KEY` / `<HOST>_BASE_URL`（开发态密钥） |

skill_commons 故意 **不读** `<repo_root>/.env`，避免命名空间和生命周期混淆。

### 4.2 host_profiles 占位符
[skill_commons/registry/host_profiles.yaml](../skill_commons/registry/host_profiles.yaml) 字段值支持 `${ENV_VAR}` / `${ENV_VAR:default}` 占位，缺失会在创建 client 时报清晰错误。

## 5. 配置最小骨架
```yaml
experiment_name: my_eval
tags: [demo]
dataset: { name: Fin-Compliance }
sampling: { mode: n, n: 5 }

prompt_strategy:
  system_prompt_ref: prompts/system/finance_default.txt
  user_template: |
    【题目】{question}
    【选项】{options}
    最末写：最终答案：X

model_under_test:
  host_profile: zerail
  model: claude-sonnet-4
  run_prefix: mut

baselines:
  - { host_profile: iquest, model: kimi-k2.6, run_prefix: kimi }

judge: { host_profile: zerail, model: claude-sonnet-4, temperature: 0 }

metrics:
  - accuracy
  - reasoning_quality

execution: { rounds: 1, concurrency: 5, reporter: [csv, lumi], resume: true }
```

如只想本地调试、不上传 Lumi，才降级写：

```yaml
execution: { rounds: 1, concurrency: 5, reporter: [csv], resume: true }
```

## 6. 三个高频场景
### 6.1 换 MUT
改 `model_under_test.model`，可改 `run_prefix` 区分历史。其他不动 → resume 自动跳过老 baseline 行。

### 6.2 加一个新指标
`metrics:` 末尾 append 一个 `name`，如：
```yaml
- name: array_recall
  splitter: '[,，;；\n]'
- name: factuality_score
```

### 6.3 微调一个指标的 prompt（不污染全局）
```yaml
- name: reasoning_quality
  alias: rq_strict
  prompt_inline: |
    你是极其严格的评分员……
    输出 JSON: {"thinking":"...","score":0.x,"reason":"..."}
```

## 7. 主观评测怎么评

主观题有两个层次的方案，**先选层次再选具体指标**：

| 你的需求 | 推荐 | 适用于 |
|---|---|---|
| **领域级体系化打标**（如 RVEC：72 个标签 + P0/P1/P2 + 0-4 分） | `rvec_judge` | 通用对话、医疗、创作等成熟评测体系 |
| **per-sample 自定义打分卡**（每条题单独一份 rubric） | `rubric_judge` | 题集小、规则各异、不需要标签体系 |
| **维度级单分**（评推理质量、事实性等单一指标） | `reasoning_quality` / `factuality_score` | 简单场景 |
| **完全自定义 prompt** | `custom_judge` | 一次性实验 |

### 7.1 RVEC 三步管线（推荐用于领域级评测）

`rvec_judge` 是 RVEC v3.0 的完整实现，每个 sample 跑 6 次 LLM 调用：

```
STEP1 「看」    理解用户需求 (step1 JSON)
                    ↓
STEP2 「找」    R/V/E/C 四维度信号检测（串行）
                    ↓
AGGREGATE       按 caps 裁剪 bad_signals/good_signals（Python，无 LLM）
                    ↓
STEP3 「定」    DCG 综合评分 0-4 → 归一化到 [0,1]
```

**领域包结构**（"会变的东西"集中在这 7 个文件）：
```
prompts/judge/rvec_general/
├── pack.yaml              ← 60 个信号 + 12 个亮点 + caps + scoring_mode
├── step1_understand.md
├── step2_R.md / step2_V.md / step2_E.md / step2_C.md
└── step3_scoring.md
```

**新增/删除/修改一个标签**：只动 [pack.yaml](prompts/judge/rvec_general/pack.yaml)，prompt 不动（信号清单运行时从 yaml 渲染注入）。

**最小 yaml**：
```yaml
metrics:
  - name: rvec_judge
    alias: general_rvec
    prompt_pack: prompts/judge/rvec_general
    # caps 不写就用 pack.yaml 默认（mut=5/baseline=4/good=3）
```

**自家 vs 竞品分别配 cap**（baseline 模型自动用更严的 cap）：
```yaml
metrics:
  - name: rvec_judge
    prompt_pack: prompts/judge/rvec_general
    extra:
      caps:
        bad_mut: 6        # 自家
        bad_baseline: 4   # 竞品
        good: 3
        per_dim: {R: 2, V: 2, E: 1, C: 3}
```

**dataset 字段约定**（per-sample reference_block 自动组装）：
```jsonc
{
  "input": {"prompt": "用户问题..."},
  "expected_output": {"answer": "参考答案（可选）"},
  "metadata": {
    "schema": "open_ended",
    "expected_signals": ["R-REA-3", "V-INFO-1"]   // 可选，预期易错信号
  }
}
```

**CSV 输出列**（除 `<alias>_value`/`<alias>_reason` 外）：
| 列 | 含义 |
|---|---|
| `<alias>__final_score_raw` | 0-4 原始分 |
| `<alias>__worst_level` | P0/P1/P2/无 |
| `<alias>__tag_coverage` | high/medium/low |
| `<alias>__bad_signals_json` | 完整 bad 列表 JSON |
| `<alias>__good_signals_json` | 完整 good 列表 JSON |
| `<alias>__bad_tags_json` / `__good_tags_json` | 兼容旧脚本命名的命中 bad/good tags JSON |
| `<alias>__step2_R_json` / `__step2_V_json` / `__step2_E_json` / `__step2_C_json` | 各 STEP2 维度原始 JSON 输出 |
| `<alias>__step3_json` | STEP3 综合评分原始 JSON 输出 |
| `<alias>__step1_json` | step1 需求分析 JSON |
| `<alias>__question_type` | 题型 |
| `<alias>__bad_signals_count` / `__good_signals_count` | 数量 |
| `<alias>__judge_failed` / `__failed_step` | 失败时填 |

**失败行为**：任一 step 解析失败 → 重试 1 次（temperature +0.1）→ 仍失败则整个 sample fail-loud：score=0.0、`judge_failed=true`、`failed_step` 标记哪步挂的。

**新建一个领域包**（如医疗）：复制 `rvec_general/` 整个目录到 `rvec_medical/`，改 `pack.yaml`（增删 💊 标签 + 调整 caps），按需改 6 个 prompt 文件，无继承机制（避免维护成本）。

> **手里只有一份 RVEC 设计文档（markdown/word/wiki）怎么办？**推荐 3 步走法，全程只拆 1 份文件：
> 1. `python -m eval_skill.cli init-pack --type rvec --to ~/work/rvec_<领域>.yaml` 拿带注释的模板
> 2. 把设计文档里的标签清单填进这份 yaml（8–10 个关键标签起步，不必一次填 60 条）
> 3. `upload-prompt-pack --file ~/work/rvec_<领域>.yaml --type rvec --slug rvec_<领域> --mode lite`
>
> 6 个 step prompt 不需要拆：它们是流程骨架（JSON 输出格式、占位符 `{signals_section}`、校准原则），跨领域复用，领域差异 95% 集中在 `pack.yaml`。只有你发现默认评分阈值不合领域（比如医疗该更保守）才需要走 `--mode merge` 重写 `step3_scoring.md`。

完整示例：[configs/examples/general_rvec_demo.yaml](configs/examples/general_rvec_demo.yaml)。

### 7.2 per-sample rubric（rubric_judge）

每条题带自己的判分规则时用：

```jsonc
// dataset item
{
  "expected_output": {
    "answer": null,
    "rubric": "评分准则：1) 直接给结论 1.0/0.5/0; 2) 事实正确 1.0/0.5/0; ..."
  }
}
```

```yaml
metrics:
  - name: rubric_judge
    alias: my_rubric
    # 默认从 prompts/judge/rubric_judge.md 读 system prompt
```

### 7.3 自定义 judge 的两段 prompt
所有单值打分指标都支持：
- `prompt_ref` / `prompt_inline` → judge **system prompt**（评分原则、维度定义、输出格式）
- `user_prompt_ref` / `user_prompt_inline` → judge **user prompt 模板**（每条样本要填进去什么）

user prompt 模板里能用的占位（`_render_context`，缺失自动空字符串）：

| 类别 | 变量 |
|---|---|
| 输入侧 | `{question}` `{background}` `{options}` |
| 模型输出 | `{answer}` `{prediction}` `{reasoning}` `{all_answers}` |
| ground truth | `{ground_truth}` `{explanation}` `{reasoning_ref}` `{rubric}` `{expected_md}` |
| 透传 | `input.fields.*`（直接写 `{step1}`）/ `metadata.*`（直接写 `{domain}`）|

### 7.4 上游产物（如 step1）怎么进来
判分用到的"用户需求分析" / "上游摘要" 这类**派生字段**有两种放法：
1. **在数据集导入时就算好**，存到 `input.step1` 或 `metadata.step1` → user template 里直接 `{step1}`。推荐做法，可复现、可审计。
2. **每个评测都重新算一遍** → `rvec_judge` 内部的 STEP1 已经在做这件事；其他指标如需 metric chain 留待 M2+。

## 8. dataset v2 spec（导入新数据时遵循）
每条 item：
```jsonc
{
  "input": {
    // 三选一：
    "prompt": "完整 user 文本（prompt-baked）",
    "turns": ["user1 第1轮文本", "user2 第2轮文本"],   // multi-turn dialog；list[str]
    // 或结构化字段：question / options / background / ...
    "pdf_refs": [{"label": "2024年报", "path": "media_data/xxx.pdf"}]
  },
  "expected_output": {
    "answer": "A" /* 或 list / number / null */,
    "reasoning_ref": "参考推理...",
    "explanation": "官方解析",
    "rubric": "rubric_judge 用",
    "expected_md": "report_pair 用"
  },
  "metadata": {
    // v2.1：拆 schema → 两个正交字段（推荐写）；v2 schema 字段保留向后兼容
    "turn_kind": "single|multi",
    "scoring_mode": "exact|numeric|array|rubric|report_pair|none",
    "schema": "single_choice|array|string|number|open_ended|dialog|report_pair",  // 老协议
    "domain": "common|finance|...",     // ⚠️ list-datasets 必须有这个才会列出来
    "tags": [...]
  }
}
```

> **v2 → v2.1 spec 升级要点**：
> - `metadata.schema` 一个字段既表达「轮次」又表达「评分方式」，混淆。v2.1 拆成两个正交字段：
>   - `turn_kind`：`single` / `multi`（与 input 的 prompt vs turns 对应）
>   - `scoring_mode`：`exact` / `numeric` / `array` / `rubric` / `report_pair` / `none`（决定 metric 怎么打分）
> - `migrate_legacy_dataset` 自动**双写**两个新字段；老 dataset 不需手工迁移，`Sample.turn_kind` / `Sample.scoring_mode` 会从 schema 推断。
> - 新建 dataset 时**优先**写 `turn_kind` / `scoring_mode` 而不是 `schema`，schema 只作向后兼容字段。
> - `metadata.domain` 是 `list-datasets` 的过滤键。**没有 domain 的 dataset 在 list-datasets 里默认不展示**，视作废弃。


## 9. 输出
- `outputs/<exp>/samples.csv`：每行一个 `(sample, model, round)`，包含 `prediction`/`reasoning`/`<metric>_value`/`<metric>_reason`/`tokens_*`/`latency_sec`/`error`。
- `outputs/<exp>/summary.json`：按 `(model, run_prefix, round)` 聚合 mean/std + 错误数。
- `outputs/_cache/pdf/`：PDF 抽取结果缓存（按 path+mtime+mode+keywords hash）。
- 当 `execution.reporter` 包含 `lumi`：每条样本会创建 Lumi trace、写入 metric score，并通过 dataset run item 关联到原 dataset item，形成 Experiments 视图。runName 格式：`<experiment_name>__<run_prefix>__round<N>`。
- `rvec_judge` 的 Lumi trace 会额外创建 step 级 observation/span：`step1_understand`、`step2_R`、`step2_V`、`step2_E`、`step2_C`、`aggregate`、`step3_scoring`，并在 trace output 的 `metrics.<alias>` 下展示 `bad_tags_json` / `good_tags_json` 与各 step JSON。

## 10. 进一步阅读
完整技术规范：`eval_skill/DESIGN.md`（v1.2，18 节）。
