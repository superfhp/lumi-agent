---
name: fetch-data
description: "Connect to the Lumi to get the evaluation data."
version: 1.0.0
author: Hpfu
license: MIT
metadata:
  hermes:
    tags: [Data, LLM, Eval, Finanical, API]
    related_skills: [report-writing]
---

# fetch-data

通过python调用 script.generate_benchmark_report.py 获取金融评测的原始数据，归档在 /mnt/workspace/data 目录下

## Quick Reference
| Action | Command |
|--------|---------|
| create python environment | `source .venv/bin/activate` |
| fetch data | `python generate_benchmark_report.py` |

## 获取金融模型评测的原始数据

```bash
source .venv/bin/activate
python generate_benchmark_report.py
```

