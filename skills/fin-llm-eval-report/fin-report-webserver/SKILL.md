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
严格执行`Procedure`


## Quick Reference
- scripts：`scripts.serve.py`

## Procedure
- step 1: 运行脚本
执行scripts中的脚本
```bash
python serve.py /mnt/workspace/achieveFinReport
```

- step 2: 关闭服务
等候 `10 min` , 杀掉占用`9200`端口的进程

## Pitfalls
如果过程中有报错，展示在chat中。
