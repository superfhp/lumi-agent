---
name: fetch-data
description: "严格按照`Procedure`执行步骤，过程中不允许出现任何额外的脚本，以及捏造数据。"
version: 1.0.0
author: Hpfu
license: MIT
metadata:
  hermes:
    tags: [Data, LLM, Eval, Finanical, API]
    related_skills: [report-writing]
---

# fetch-data

严格按照`Procedure`执行步骤，过程中不允许出现任何额外的脚本，以及捏造数据。
通过python调用 script.generate_benchmark_report.py 获取金融评测的原始数据，归档在 /mnt/workspace/data 目录下

## Quick Reference
| Action | Command |
|--------|---------|
| create python environment | `source /mnt/workspace/lumi-agent/venv/bin/activate` |
| fetch data | `python generate_benchmark_report.py` |

## Procedure

### Step 1 · 删除之前生成的数据文件
```bash
rm -rf /mnt/workspace/data/*
```


### Step 2 · 进入python虚拟环境
```bash
source .venv/bin/activate & python generate_benchmark_report.py
```


## Pitfalls
| Action | Command |
|--------|---------|
|检查`/mnt/workspace/data`目录下有没有生成`.csv`文件，且文件不为空 | 如果没有生成文件或文件为空，直接返回到chat对话中，并终止技能的后续执行。｜