---
name: fin-report-webserver
description: "show the report"
version: 1.0.0
author: Hpfu
license: MIT
metadata:
  hermes:
    tags: [Web, LLM, Eval, Finanical, Report]
    related_skills: [report-writing，fetch-data]
---

# fin-report-webserver

读取 `/mnt/workspace/achieveFinReport` 文件夹下所有html文件，启动一个python simple web server，用来展示报告，严格按照`Procedure`中的描述来完成。


## Quick Reference
- scripts：`scripts.serve.py`

## Procedure
执行scripts中的脚本
```bash
source .venv/bin/activate & python serve.py /mnt/workspace/achieveFinReport
```
