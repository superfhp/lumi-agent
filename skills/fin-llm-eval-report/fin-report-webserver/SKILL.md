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


## Procedure

### Step 1 · 进入到对应的目录
```bash
cd /mnt/workspace/achieveFinReport
```


### Step 2 · 进入python虚拟环境
```bash
source .venv/bin/activate
```
严格按照端口要求暴露服务，端口号为：`8888`
启动一个python simple web server，网址为 `http://localhost:8888` 展示所有html文件，可以点击展示对应的html。
