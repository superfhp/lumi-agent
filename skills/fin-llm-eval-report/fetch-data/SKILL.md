---
name: fetch-data
description: "严格按照`Procedure`执行步骤，过程中不允许出现任何额外的脚本，以及捏造数据。"
version: 2.0.0
author: Hpfu
license: MIT
metadata:
  hermes:
    tags: [Data, LLM, Eval, Financial, API]
    related_skills: [report-writing]
---

# fetch-data

严格按照 `Procedure` 执行步骤，过程中不允许出现任何额外的脚本，以及捏造数据。
通过 python 调用评测数据拉取脚本，获取三类评测的原始数据，归档在 `/mnt/workspace/data/<include参数值>` 目录下。
不允许用系统 python 环境，必须根据技能中的描述使用虚拟环境。

## 入参说明

| 参数 | 是否必填 | 说明 | 示例 |
|------|---------|------|------|
| `--include` | **必填** | 被测试模型的关键字，用于从评测集中筛选对应实验 | `iquest_0509` |

所有脚本都使用 `--include` 参数来正选目标实验，输出目录统一为 `/mnt/workspace/data/<include值>/`。

## Quick Reference

假设用户传入 `--include iquest_0509`，则：

| 类型 | 脚本 | 命令 |
|------|------|------|
| 新闻情绪评测 | `generate_news_data.py` | `python generate_news_data.py --dataset FinNews-Sentiment-Eval --include iquest_0509 -o /mnt/workspace/data/iquest_0509/` |
| 新闻异常情绪评测 | `generate_news_data.py` | `python generate_news_data.py --dataset FinNews-Anomalous-Emotion --include iquest_0509 -o /mnt/workspace/data/iquest_0509/` |
| 金融基础评测 | `generate_finance_data.py` | `python generate_finance_data.py --dataset Fin-Compliance Fin-Literacy Fin-Economics Fin-Investing Fin-Quantitatics --include iquest_0509 -o /mnt/workspace/data/iquest_0509/` |
| 研报能力评测 | `generate_research_data.py` | `python generate_research_data.py --include iquest_0509 -o /mnt/workspace/data/iquest_0509/` |

输出文件按 `{评测集}_{模型名称}.csv` 自动拆分，例如：
- `FinNews-Sentiment-Eval_glm-5.1.csv`
- `Fin-Quantitatics_deepseek-v4-pro.csv`
- `Report_Analysis_Eval_sft-general-0509.csv`

## Procedure

以下步骤中，将 `{INCLUDE}` 替换为用户传入的 `--include` 参数值（例如 `iquest_0509`）。

### Step 1 · 清理并创建输出目录
```bash
rm -rf /mnt/workspace/data/{INCLUDE}
mkdir -p /mnt/workspace/data/{INCLUDE}
```

### Step 2 · 进入 python 虚拟环境
```bash
source /mnt/workspace/lumi-agent/venv/bin/activate
```

### Step 3 · 拉取新闻情绪评测数据
```bash
python generate_news_data.py --dataset FinNews-Sentiment-Eval --include {INCLUDE} -o /mnt/workspace/data/{INCLUDE}/
```

### Step 4 · 拉取新闻异常情绪评测数据
```bash
python generate_news_data.py --dataset FinNews-Anomalous-Emotion --include {INCLUDE} -o /mnt/workspace/data/{INCLUDE}/
```

### Step 5 · 拉取金融基础评测数据
```bash
python generate_finance_data.py --dataset Fin-Compliance Fin-Literacy Fin-Economics Fin-Investing Fin-Quantitatics --include {INCLUDE} -o /mnt/workspace/data/{INCLUDE}/
```

### Step 6 · 拉取研报能力评测数据
```bash
python generate_research_data.py --include {INCLUDE} -o /mnt/workspace/data/{INCLUDE}/
```

## Pitfalls

| 检查项 | 处理方式 |
|--------|---------|
| 检查 `/mnt/workspace/data/{INCLUDE}/` 目录下是否生成了按 `{评测集}_{模型名称}.csv` 命名的文件，且文件不为空 | 如果任一文件未生成或为空，直接返回到 chat 对话中报告失败的步骤，并终止技能的后续执行。|
| 用户未传入 `--include` 参数 | 必须提示用户提供被测试模型的关键字，不允许跳过此参数。|