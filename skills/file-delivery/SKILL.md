---
name: file-delivery
description: 当用户要求下载文件、导出结果、获取生成的文件时使用此 skill。通过 HTTP 链接提供文件下载。
version: 2.0.0
author: hpfu
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [file, download, delivery, utility]
    category: utility
---

# 文件交付 Skill

将服务器上生成的文件交付给用户下载。统一使用 HTTP 链接方式。
供其他 skill 串联调用，也支持用户直接触发。

> **注意**：不要使用 base64 data URI 方式，Open WebUI 的 markdown 渲染器会过滤 data URI，导致链接无法点击下载。

## When To Use
  - **帮我下载这个文件**
  - **把结果导出给我**
  - **我要下载**
  - **文件发给我**
  - **打包下载**
  - 其他 skill 生成文件后需要交付给用户时（串联调用）

## 入参说明

| 参数 | 是否必填 | 说明 | 示例 |
|------|---------|------|------|
| file_path | **必填** | 要下载的文件或目录的绝对路径 | `/hpfu/media_data/bili/jsonl/result.csv` |
| pack_name | 可选 | 多文件/目录打包时的压缩包名称 | `eval_results.tar.gz` |

- 如果 `file_path` 是**文件**：直接交付该文件
- 如果 `file_path` 是**目录**：打包为 tar.gz 后交付
- 支持通配符描述（如 "data 目录下所有 csv"），由 agent 解析后打包

## 交付方式

通过 9200 端口的 `serve.py` 提供 HTTP 下载链接。
`serve.py` 的 `/download/` 路由支持白名单目录下的任意文件下载。

### 链接拼接规则

文件绝对路径去掉白名单根目录前缀，得到相对路径，拼接到 URL：

```
文件路径:  /mnt/workspace/data/result.csv
白名单根: /mnt/workspace
相对路径:  data/result.csv
下载链接:  http://47.99.95.132:9200/download/data/result.csv

文件路径:  /hpfu/media_data/bili/output.jsonl
白名单根: /hpfu/media_data
相对路径:  bili/output.jsonl
下载链接:  http://47.99.95.132:9200/download/bili/output.jsonl
```

### 白名单根目录（DOWNLOAD_ROOTS）

| 根目录 | 说明 |
|--------|------|
| `/mnt/workspace` | 工作空间，评测数据、报告等 |
| `/hpfu/media_data` | 共享媒体数据挂载 |

文件必须位于以上目录之下，否则 serve.py 会返回 403。

### HTTP 文件服务配置

| 项目 | 值 |
|------|------|
| 服务脚本 | `/mnt/workspace/file-delivery/serve.py` |
| 服务端口 | 9200（与报告展示共用） |
| 报告路径 | `/lumifinreport/<report.html>` |
| 下载路径 | `/download/<relative_path>` |

## Procedure

### Step 1 · 确认文件存在
```bash
ls -lh {file_path}
```
- 文件不存在 → 返回错误提示
- 是目录 → 进入 Step 2 打包
- 是文件 → 跳到 Step 3

### Step 2 · 目录打包（仅目录时执行）
```bash
mkdir -p /mnt/workspace/tmp
tar -czf /mnt/workspace/tmp/{pack_name} -C {file_path} .
```
后续以 `/mnt/workspace/tmp/{pack_name}` 为交付目标。

### Step 3 · 确认 HTTP 服务运行
```bash
ss -tulnp | grep :9200
```
如未运行，启动服务：
```bash
nohup python3 /mnt/workspace/file-delivery/serve.py /mnt/workspace/achieveFinReport > /dev/null 2>&1 &
```

### Step 4 · 生成下载链接
根据文件绝对路径，匹配白名单根目录，去掉前缀得到相对路径，拼接链接。

**回复模板：**
```
文件已准备好，点击下载：

[📥 点击下载 {filename}](http://47.99.95.132:9200/download/{relative_path})
```

### Step 5 · 多文件交付
当用户要求下载多个文件时：
- 文件数 ≤ 3 → 逐个返回 HTTP 下载链接
- 文件数 > 3 → 统一打包为 tar.gz 后返回单个链接

## 其他 Skill 串联调用示例

其他 skill 在生成文件后，可在回复中提示用户使用 file-delivery：
```
数据已保存至 /mnt/workspace/data/iquest_0509/
如需下载，请说"帮我下载 /mnt/workspace/data/iquest_0509/"
```

或者在 skill 的 Procedure 最后一步直接串联：
```
### Step N · 交付文件
使用技能 file-delivery，将生成的文件交付给用户下载。
```

## Limitations
- HTTP 链接依赖 9200 端口 serve.py 服务正常运行
- 文件必须在白名单目录（`/mnt/workspace/` 或 `/hpfu/media_data/`）下
- 不要在链接或日志中暴露敏感信息
- **禁止使用 base64 data URI**，Open WebUI 不支持

## Pitfalls

### 9200 端口服务未运行
```bash
ss -tulnp | grep :9200
```
如未运行：
```bash
nohup python3 /mnt/workspace/file-delivery/serve.py /mnt/workspace/achieveFinReport > /dev/null 2>&1 &
```

### 文件不在白名单目录下
serve.py 只允许下载 DOWNLOAD_ROOTS 白名单内的文件。如需新增根目录，修改 serve.py 中的 `DOWNLOAD_ROOTS` 列表。
