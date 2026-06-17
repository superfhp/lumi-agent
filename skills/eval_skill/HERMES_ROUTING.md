# Hermes 路由修复说明：多领域评测必须走 eval_skill CLI

## 这次为什么跑偏

用户输入「帮我跑一次通用领域评测」后，Hermes 选中了旧技能 `lumi-llm-eval-pipeline`。

该旧技能的问题：

1. 只有一个硬编码脚本入口：`scripts/eval_multiple_pipeline.py`
2. 不做 dataset 发现 / 预览 / 规则展示 / 用户确认
3. 容易默认跑到金融数据集，如 `Fin-dataset-1`
4. 会把「通用领域评测」错误解释成“跑现有金融流水线”或“自行建议 MMLU/C-Eval/GSM8K”
5. 没有使用 `metadata.domain` 过滤可用 dataset

所以它不适合当前 eval_skill 的评测前 5 步工作流。

## 正确路由

当用户说以下任意话术时，必须选择 `eval_skill`：

- 帮我跑一次通用领域评测
- 跑 common 领域评测
- 先看看有哪些评测集
- 预览评测集内容再跑
- 展示评测规则后再执行
- 上传 prompt / 标签包 / dataset，先 dry-run 给我确认
- 模型横评 / benchmark / RVEC judge / 多轮对话评测

不要选择 `lumi-llm-eval-pipeline`。

## 正确执行流程

如果用户只说「帮我跑一次<领域>评测」，不要直接跑模型调用。必须按下面顺序：

领域映射：
- 通用领域 → `domain=common`
- 金融领域 → `domain=finance`
- 其他领域 → 先向用户确认 domain，再执行 `list-datasets --domain <domain>`

禁止事项：
- 禁止先调用 search / grep / ls / find / 历史会话检索来猜 dataset。
- 禁止扫描 `/mnt/workspace/data/*.csv` 或本地评测结果目录。
- 禁止把非目标 domain 的 dataset 列为候选。用户要 common 时不能列 `Fin-*` / `finance`；用户要 finance 时也不能列 common。
- 如果 `list-datasets --domain <domain>` 为空，只能报告该 domain 暂无可用 dataset，不能降级展示其他 domain。

```bash
# Step 0：必须先激活 Hermes 指定虚拟环境
cd /mnt/workspace/lumi-agent/skills
source /mnt/workspace/lumi-agent/venv/bin/activate
export PYTHONPATH=/mnt/workspace/lumi-agent/skills:$PYTHONPATH

# Step 1：列出用户指定领域可用 dataset（通用=common，金融=finance）
python -m eval_skill.cli list-datasets --domain <domain>

# Step 2：用户选 dataset 后，预览 5 条
python -m eval_skill.cli preview-dataset --name <用户选择的dataset> --limit 5

# Step 2 输出要求：展示样例的 category、prompt、answer/rubric，不展示预期信号。
# CLI 末尾输出的「🔗 查看全量评测集」链接必须一并转发给用户。
# 预览后停下等待用户确认是否继续第 3 步。

# Step 3：展示评测配置和规则，不执行
python -m eval_skill.cli describe-config -c <对应yaml>

# Step 3 检查：execution.reporter 必须包含 csv；如用户要 Lumi trace/Experiments，必须包含 lumi。
# 推荐默认：reporter: [csv, lumi]

# Step 4：如用户要改 prompt/pack/dataset，先 dry-run
python -m eval_skill.cli upload-prompt --file <file> --kind <kind> --slug <slug> --dry-run
python -m eval_skill.cli upload-prompt-pack --file <pack.yaml> --type rvec --slug <slug> --mode lite --dry-run
python -m eval_skill.cli upload-dataset --file <file> --name <dataset> --dry-run

# Step 5：只有用户确认 dataset/model/judge/rules 后才执行
python -m eval_skill.cli run -c <yaml>

# cli run 默认会确保 reporter 至少包含 csv+lumi；只有本地调试时才允许 --no-lumi。
# Step 5 输出要求：
# - 必须抬 samples.csv 路径、summary.json 路径
# - 必须转发 CLI 输出的评测结果表格（question / expected / actual / score / reason）
# - 必须转发 CLI 输出的 Langfuse 实验结果链接（🔗）
# - 如果 metric 是 rvec_judge，还必须确认 Lumi trace 中有 step 级 observation/span：
#   step1_understand / step2_R / step2_V / step2_E / step2_C / aggregate / step3_scoring，
#   并且 trace output/CSV 中能看到 bad_tags_json / good_tags_json。
```

## 用户确认清单

执行前必须向用户回显并取得确认：

- dataset 名称
- 前 5 条样例内容摘要
- MUT 模型
- baseline 模型（如有）
- judge 模型
- metrics / prompt_pack / caps
- 预计 judge 调用量
- 输出路径

没有确认前，不允许调用 `cli run`。

## Import / 环境问题处理

`python -m eval_skill.cli ...` 必须在 `eval_skill` 包的父目录执行。Hermes 默认约定：

```bash
cd /mnt/workspace/lumi-agent/skills
source /mnt/workspace/lumi-agent/venv/bin/activate
export PYTHONPATH=/mnt/workspace/lumi-agent/skills:$PYTHONPATH
python -m eval_skill.cli list-datasets --domain common
```

如果出现：

```text
ModuleNotFoundError: No module named 'eval_skill'
```

含义不是“应该去搜索本地文件”。如果之前在 `/mnt/workspace/lumi-agent/skills` 下执行过 `python -m eval_skill.cli ...` 并成功，优先判定为本次命令的工作目录/PYTHONPATH 丢失，而不是代码不存在。

可能原因：

1. `/mnt/workspace/lumi-agent/skills/eval_skill/__init__.py` 不存在，skill 只同步了 `SKILL.md`，Python 包代码没部署；或
2. `PYTHONPATH` 没包含 `/mnt/workspace/lumi-agent/skills`。

处理规则：
- 先回到 `/mnt/workspace/lumi-agent/skills`，重新激活 venv，重新设置 `PYTHONPATH`，然后直接重跑原 `python -m eval_skill.cli ...` 命令。
- 如果重跑仍失败，再向用户报告部署/PYTHONPATH 问题。
- 不要调用 search / grep / find / ls 大范围扫描。
- 不要运行 `python - <<'PY' ... PY`、`python -c ...`、`bash -lc ...` 等嵌套脚本探测；这类命令容易产生 heredoc quoting 错误或触发审批。
- 不要改扫 `/mnt/workspace/data/*.csv` 或历史会话来冒充 dataset 列表。

## 对旧 skill 的建议处理

建议将 `lumi-llm-eval-pipeline` 标记为 deprecated 或收窄描述：

- 只适用于旧金融硬编码流水线
- 不适用于通用领域评测
- 不适用于 dataset 发现/预览/确认式评测
- 用户说「通用领域评测」时必须让路给 `eval_skill`

建议在旧 skill 的 frontmatter description 里加入：

```yaml
description: "Deprecated legacy financial-only eval pipeline. Do NOT use for 通用领域评测/common eval/general benchmark/model comparison. Use eval_skill instead."
metadata:
  hermes:
    tags: [Deprecated, Legacy, FinancialOnly]
```
