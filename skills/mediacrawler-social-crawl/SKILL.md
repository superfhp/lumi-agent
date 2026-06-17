---
name: mediacrawler-social-crawl
description: 当用户希望使用 MediaCrawler 按关键词抓取或搜索社媒内容，并返回 xhs、wb、bili、dy、ks、tieba、zhihu 等平台生成的数据文件时使用此 skill。
---

# MediaCrawler 社媒语料采集

使用这个 skill 调用已有的 MediaCrawler 项目，按关键词采集社媒内容，并把输出数据文件返回给用户。

## 必填输入

运行前必须收集这些参数；缺少任何一项都要先询问用户：

- `keywords`：一个或多个社媒搜索主题。优先使用高价值搜索短语，多个关键词用英文逗号分隔。
- `platform`：平台，必须是 `xhs`、`wb`、`bili`、`dy`、`ks`、`tieba`、`zhihu` 之一。
- `max_notes_count`：每个关键词/主题期望获取的内容条目数。

可选输入：

- `with_comments`：是否采集评论，默认 `false`，速度更快、风险更低。
- `max_concurrency`：默认且**必须保持 1**。多并发 = 风控触发。超过 1 会被脚本强制降为 1，并在 manifest.warnings 里发警告。
- `sleep_min_sec` / `sleep_max_sec`：MediaCrawler 内部单次请求间的随机区间（秒），默认 `0.5` / `1.5`。MediaCrawler 自身就是按 min/max 随机睡，**固定间隔反而更容易被识别**，建议保留区间。旧参数 `--sleep-sec` 仍可用，仅做向后兼容，会被自动展开为 `min=max=X` 并打 warning。
- `per_keyword_sleep_min` / `per_keyword_sleep_max`：关键词之间随机间隔区间（秒），默认 `300` / `900`（即 5–15 分钟）。只在多关键词时生效，严禁为了赶进度调低。
- `background`：设为 true 后调用 `--background` 后台跑；前台立即返回 `task_id`/`log_path`/`status_path`，后台采集产出的 manifest.json 落在 `<output_root>/_task/manifest.json`。多关键词加随机间隔会跑十几到几十分钟，**需要多关键词时默认采用后台模式**。
- `cookies`：只允许通过命令行参数或环境变量传入；**绝不要在最终回复/日志摘要/报错摘录里回显 cookie（即使用户粘贴了）**。如用户在聊天中提供 cookie，后续输出必须用 `<REDACTED_COOKIE>` 代替。

- `output_format`（xhs 专用，强烈建议）：优先按用户规范输出 **posts.csv + comments.csv** 两个文件（字段名严格一致）。当用户强调“评论必须提供”时，抓取阶段必须开启 `--with-comments`，并在交付前核对 `search_comments_*.jsonl` 是否存在。

## 关键词策略

当用户需要帮助生成关键词时，使用“X 轴 × Y 轴”矩阵组合高价值搜索词：

- X 轴：行业、领域、产品、生活场景、决策上下文。
- Y 轴：意图、痛点、价值类型、互动触发词。

常用搜索公式：

- 痛点诊断式：`[行业/场景词] + [负面情绪/困境词]`，例如 `SFT 避坑`、`劳动仲裁 被坑惨了`。
- 方法论深度式：`[行业/场景词] + [结构化/干货词]`，例如 `Prompt工程 底层逻辑`、`爆款文案 框架拆解`。
- 听劝求助式：`[具体场景] + [听劝/求助/对比]`，例如 `Offer选择 听劝`、`租房 真心求建议`。
- 信息差揭秘式：`[行业/场景词] + [大实话/潜规则/没告诉你]`，例如 `独立开发 行业内幕`、`理财 没人告诉你`。

推荐 X 轴词：

- AI 与开发：`大模型`、`LLM`、`SFT`、`微调`、`Prompt`、`RAG`、`Agent智能体`、`Cursor`、`Vibe Coding`、`自动化工作流`。
- 搞钱与职场：`副业实录`、`搞钱思维`、`信息差`、`独立开发`、`数字游民`、`自由职业`、`简历优化`、`劳动仲裁`。
- 现实决策：`理财配置`、`保险配置`、`买房决策`、`租房听劝`、`装修避坑`、`法律咨询`。
- 内容创作：`爆款文案`、`故事脚本`、`小红书文案`、`个人品牌IP`、`逻辑推导`。
- 情绪与生活：`心理自救`、`恋爱复盘`、`消费测评`、`旅游攻略`、`备考经验`。

推荐 Y 轴词：

`保姆级教程`、`底层逻辑`、`避坑`、`怎么破局`、`救救孩子`、`卡在这一步了`、`神器推荐`、`真香警告`、`效率翻倍`、`从零开始`、`测评`、`红黑榜`、`VS对比`、`小白必看`、`大实话`、`行业内幕`、`没告诉你`、`真心求建议`、`框架拆解`、`思维导图`、`内行视角`。

## 条目数限制

对用户请求做保守限制：

- `xhs`：最小有效页大小为 20，最大 100。
- `wb`：最大 200。
- `bili`：最大 100。
- `dy`：最大 100。
- `ks`：最大 100。
- `tieba`：最大 200。
- `zhihu`：最大 100。

如果用户请求超过上限，自动截断到上限并说明。`xhs` 如果小于 20，自动提升到 20。`max_notes_count` 表示每个关键词的限制，不是所有关键词总和。

## 运行要求

> 额外参考：
> - `references/bili-login-and-cdp-troubleshooting.md`（bili 登录/二维码/CDP 常见问题与推荐解法）
> - `references/bili-output-minimal-fields-and-comment-heuristics.md`（只输出标题/简介/评论时的字段映射与“置顶/热评”口径）
> - `references/file-transfer-and-delivery-options.md`（文件在哪/怎么下/无法代下载时的交付话术与选项）

### 三条硬性约束（Agent 不可绕过）

1. **单 platform 禁止并发**。脚本默认 `max_concurrency=1` 且会强制下调；同时为每个 platform 上 `.locks/<platform>.lock`（fcntl flock），同一时刻**同一 platform 只能跑 1 个 `run_crawl.py`**。拿不到锁时 manifest 返回 `status=error / error=lock_held / held_by={pid,run_id,started_at,keywords}`，Agent **不要重试**，直接告诉用户“current platform 有任务在跑”并呈上 held_by 信息，让用户决定等还是手动 kill。
2. **多关键词轮询必须随机间隔**。脚本默认 `per_keyword_sleep_min=300, per_keyword_sleep_max=900`（即 5–15 分钟），**严禁为了赶完手上任务调低间隔**。该机制只在多关键词时生效，单关键词时不生效。
3. **多关键词默认走后台**。随机间隔 × 关键词数常需 10–60 分钟，前台会快思路阻塞不可取。Agent 只要关键词数 ≥ 2，**默认加 `--background`**；拿到 `task_id / log_path / status_path` 即可返回用户，后续以文件接口查状态。

### 状态查询与交付时机

- 后台任务的实时状态走 `<status_path>` JSON 文件：
  ```jsonc
  {
    "state": "running | done | error | starting",
    "task_id": "...",
    "current_keyword_index": 3,
    "current_keyword": "AI写的 护肤",
    "sleep_until": "2026-06-08T15:42:00",   // 不为 null 表示正在随机间隔中
    "last_heartbeat": "2026-06-08T15:30:12",
    "finished_at": null,
    "manifest_path": null                    // done/error 后出现
  }
  ```
- Agent 在用户追问进度时读 `status_path`，看到 `state=done` 才读 `manifest_path` 走交付流程；`state=running` 时告诉用户当前进度（第几个关键词 / 下一个关键词几点开始）；`state=error` 告知用户并呈上 `log_path`。
- 前台模式依然适用：单关键词、且预期几分钟能跑完的任务可以不加 `--background`，拿到 stdout 末尾的 `[manifest]` 直接用。

### 虚拟环境（所有 Python 命令之前必须激活）

本 skill 下的 `run_crawl.py` / `step1_ocr.py` / `step1_preprocess.py` **均要跑在 MediaCrawler 项目的 venv 里**（含 rapidocr-onnxruntime / playwright 等依赖）。

- venv 路径：`/mnt/workspace/MediaCrawler/.venv`
- 激活命令：`source /mnt/workspace/MediaCrawler/.venv/bin/activate`
- Agent 调用任何 Python 命令前，**必须在同一个 shell 会话里先 source venv**；需要保证激活后再走后续 `python ...` 命令（`subprocess` 默认不继承 shell该状态，用 `bash -c "source ...; python ..."` 包住）。
- 诊断 venv 是否激活：`which python` 应返回 `.venv/bin/python`，不是系统 `python`。

使用已有的 MediaCrawler 项目。默认路径：

`/mnt/workspace/MediaCrawler`

无桌面 Linux server 运行（尤其是 `bili` / `xhs`）时：

- **尽量不要走二维码登录**：无桌面/不可渲染图片的环境会卡在“等待扫码”。
- **优先用 cookie 登录**（`--lt cookie --cookies "…"` 或对应的 `MEDIACRAWLER_*_COOKIE` 环境变量）。注意：即便传了 cookie，MediaCrawler 仍可能先尝试一次 `pong` 并打印“账号未登录”再进入 cookie 登录流程，这不一定是失败。
- **CDP 连接常见坑**：MediaCrawler 默认先尝试连接远程调试端口；若出现 `connect_over_cdp ... 404 Not Found`，通常是没有真正跑在“远程调试模式”或端口不是对应的浏览器实例。此时会 fallback 到标准模式，通常不影响 cookie 登录抓取。

尽量通过环境变量传 cookie：

- `MEDIACRAWLER_XHS_COOKIE`
- `MEDIACRAWLER_WB_COOKIE`
- `MEDIACRAWLER_BILI_COOKIE`
- `MEDIACRAWLER_DY_COOKIE`
- `MEDIACRAWLER_KS_COOKIE`
- `MEDIACRAWLER_TIEBA_COOKIE`
- `MEDIACRAWLER_ZHIHU_COOKIE`

不要在聊天框、日志摘要或最终回复中暴露 cookie。

## 执行流程

1. 校验必填输入。
2. 将关键词规范化为英文逗号分隔字符串。
3. **并发 + 间隔 + 后台决策**：
   - `--max-concurrency` 始终不动（保持 1）。
   - **多关键词**（≥ 2）默认不动 `--per-keyword-sleep-min/max`，且**加 `--background`** 跑后台。仅在用户明确要求“现在等着验证”且关键词 ≤ 2 时，才考虑前台。
   - **单关键词**默认前台。
4. 调用 `run_crawl.py`，脚本自动根据平台选择 CDP 或 Playwright 模式。
5. 脚本首先取 `platform` 锁——拿不到则返回 `error=lock_held` + `held_by`，Agent 直接告知用户并结束，**绝不重试**。
6. 前台模式：脚本实时透传 MediaCrawler 日志；后台模式：前台返回 `task_id/log_path/status_path` 后退出，用户用 `status_path` 查进度。
7. 解析 manifest：
   - 前台 → stdout 末尾的 `[manifest]` 块。
   - 后台 → 等 `status.state == "done"` 后读 `<status.manifest_path>` 文件。
8. 从 manifest 返回生成文件路径、记录数、平台、关键词、执行状态。
9. **OCR（仅 xhs）**：拿 `manifest.suggested_ocr_cmds` 逐条执行 `step1_ocr.py`，产出 `ocr_<input_stem>.jsonl`。非 xhs 平台 / 没采到数据时该字段为空，跳过此步。
10. **预处理（仅 xhs）**：拿 `manifest.suggested_preprocess_cmds`（已自动拼上 `--ocr-file`）跑 `step1_preprocess.py`，产出 `preprocessed_*.jsonl/.csv/.summary.json`。
11. **交付文件**：使用技能 `file-delivery` 将 contents/comments/ocr/preprocessed 文件交付用户下载。

> **重要**：若 `image_ocr_line` 在 posts CSV 里全空，但已存在 `ocr_*.jsonl`，说明“导出/预处理时未把 OCR 回填进 CSV”。此时不要重新爬取，按本 skill 的 *Pitfalls → OCR 已完成但 posts CSV 的 image_ocr_line 为空（回填修复）* 进行回填修复并重新打包交付。
12. 如果没有生成文件，检查 `log_tail` 并解释失败原因。

示例调用（1）**单关键词 + 前台**（CDP 平台，无需传 cookie）：

```bash
source /mnt/workspace/MediaCrawler/.venv/bin/activate
python /mnt/workspace/media_data/run_crawl.py \
  --platform xhs \
  --keywords "大模型微调 保姆级教程" \
  --max-notes-count 20
```

示例调用（2）**多关键词 + 后台**（55 分、默认 5–15 min 随机间隔）：

```bash
source /mnt/workspace/MediaCrawler/.venv/bin/activate
python /mnt/workspace/media_data/run_crawl.py \
  --platform xhs \
  --keywords "AI写的 护肤,问了AI 源代码,跟骨子里 难受" \
  --max-notes-count 30 \
  --background
# 前台 manifest 中 task_id=20260608_153012_ab12cd34，status_path=.../_task/status.json
# 查状态：
# cat /mnt/workspace/MediaCrawler/skill_runs/20260608_153012_ab12cd34/_task/status.json
```

示例调用（3）**Playwright 平台，cookie 从 cookies.json 读取**：

```bash
source /mnt/workspace/MediaCrawler/.venv/bin/activate
python /mnt/workspace/media_data/run_crawl.py \
  --platform bili \
  --keywords "AI Agent 开发教程" \
  --max-notes-count 50 \
  --with-comments
```

## 采集完成后的 OCR 与预处理（仅 xhs）

xhs 图文帖的"干货"常常全在图片里（步骤截图、对比表、备忘录、清单），`desc` 正文往往只是几句配文。不跑 OCR，LLM 拿到的几乎是空。所以 xhs 默认两步：**先 OCR 再预处理**。

> 补充：如果用户需要下载链接，交付前先判定 9200 是 `serve.py` 还是 `python -m http.server`。
> - `serve.py` 才支持 `/download/`；
> - `http.server` 需走目录直出（见 `references/9200-httpserver-vs-servepy-and-linking.md`）。

### 1）OCR 拽取图片文字

`step1_ocr.py` 走 `rapidocr-onnxruntime`（纯 Python、CPU 友好、离线）。**默认就是高吞吐版**，不需要手动调参：

- **多进程并行**：`--workers N`（默认 **8**）。每个 worker 启动时预热一份 RapidOCR 实例。`workers=1` 走串行兜底。机器 CPU 多就调高，但留余量给系统、爬虫、其他任务（128 vCPU 推荐 8–16）。
- **PIL 预缩图**：`--resize-long-edge`（默认 **1280**）。OCR 前把图长边压到 1280px 再喂引擎，**直接砍掉 50%+ 的 det/rec 耗时**且精度几乎不损。设 0 关闭缩图。
- **图片采样**：`--max-images-per-note`（默认 **8**）。每条 note 只 OCR 前 N 张图——前 6–8 张承载 80%+ 信息密度，再多边际效益低。设 0 不限。
- **per-image 超时**：`--per-image-timeout`（默认 **60s**）。下载 + 缩图 + OCR 整体超时；任一阶段卡死该图被跳过，不影响其它。
- **URL 缓存**：相同图片 URL 落到 `<cache_dir>/<md5>.bin`，多次 run、跨 keyword 共享。
- **断点续跑**：输出 jsonl 里已存在的 `note_id` 直接 skip，可以反复重跑。
- **跳过 `type=video`**：视频帖 OCR 无意义，`--include-video` 可关闭跳过。
- **后台模式**：`--background`（**多关键词、大量级时强制开**）。前台立即返回 `status_path / log_path / pid`，OCR 在 fork 出的子进程里跑，**脱开 caller 端的 timeout 笼子**（bash tool 600s / agent 调用方限制都不再阻塞 OCR 完成）。
- **进程锁**：每个输出 jsonl 上 fcntl flock，防止两次 run 互写打架。
- **status.json**：实时刷新 `processed_notes / total_to_process / current_note_id / ocr_total_images / ocr_failed_images / elapsed_sec / state`，Agent 用文件接口查进度。

#### 状态查询

`<status_path>` JSON 形如：

```jsonc
{
  "state": "running | done | error | starting",
  "task_id": "ocr_status_search_contents_2026-06-09",
  "input_file": "...",
  "output_file": "...",
  "workers": 8,
  "total_to_process": 30,
  "processed_notes": 12,
  "current_note_id": "67d68d06...",
  "ocr_total_images": 56,
  "ocr_failed_images": 2,
  "elapsed_sec": 124.3,
  "last_heartbeat": "2026-06-09T16:42:10",
  "finished_at": null,                 // done/error 后出现
  "summary_path": null                  // done 后出现
}
```

Agent 行为：
- `state=running` 时报告 `processed_notes / total_to_process` 和 `current_note_id`
- `state=done` 后读 `summary_path` 拿统计、`output_file` 即最终 `ocr_*.jsonl`
- `state=error` 时把 `error` 字段呈给用户，并指 `log_path`

#### 推荐用法（Agent 自动选）

manifest 里 `suggested_ocr_cmds` 已经默认拼上了高吞吐参数 + 后台模式：

```bash
python /mnt/workspace/media_data/step1_ocr.py \
  --input "/.../search_contents_2026-06-09.jsonl" \
  --workers 8 \
  --max-images-per-note 8 \
  --resize-long-edge 1280 \
  --background
```

**Agent 直接执行该命令即可**，拿到前台返回的 `status_path` 后定时轮询。

#### 时间预算（128 vCPU、CPU-only 经验值）

- **单 note 平均 4–5 张图**，缩图后 OCR ~1.3s/图（worker=8 摊薄）
- **单关键词 30 帖** ≈ 4 分钟（vs. 老版 55 分钟，13× 提速）
- **72 关键词 × 30 帖矩阵全量 OCR** ≈ 4 小时（vs. 老版 66 小时）
- 实际机器算力可能波动 ±50%，以 status.json 的 `elapsed_sec / processed_notes` 为准

#### 失败兜底

- **OCR 失败不阻断**：单图失败写空字符串，多图拼接时省略空位，整体仍出文件
- **缺 rapidocr 依赖**：status 收 `error`，error message 提示 `pip install rapidocr-onnxruntime`
- **lock 被占**：返回 `error=lock_held + held_by`，Agent 不要重试，直接呈给用户决定

首次跑需要在 venv 里安装 OCR 依赖（只装一次）：

```bash
source /mnt/workspace/MediaCrawler/.venv/bin/activate
pip install rapidocr-onnxruntime pillow
```

### 2）预处理拼接 OCR 结果

`step1_preprocess.py` 在 OCR 产物存在时自动拼 OCR：

- `keyword_results[i].preprocess_cmd`：单个关键词对应的命令（非 xhs 平台或无 contents 文件时为 `null`）。命令中已自动拼上 `--ocr-file "<同目录>/ocr_<stem>.jsonl"`。若该文件不存在，脚本会警告但继续跑不报错，后续补 OCR 后重跑即可。
- `suggested_preprocess_cmds`：聚合所有非空命令的列表，便于一次性执行。

示例片段：

```json
{
  "suggested_preprocess_cmds": [
    "python /mnt/workspace/media_data/step1_preprocess.py \\\n  --input \"/mnt/workspace/MediaCrawler/skill_runs/.../search_contents_2026-06-08.jsonl\" \\\n  --comments \"/mnt/workspace/MediaCrawler/skill_runs/.../search_comments_2026-06-08.jsonl\" \\\n  --ocr-file \"/mnt/workspace/MediaCrawler/skill_runs/.../ocr_search_contents_2026-06-08.jsonl\"  # 如 OCR 未跑请先执行 suggested_ocr_cmds"
  ]
}
```

执行顺序（在采集完成之后）：

1. 从 manifest 读 `suggested_ocr_cmds`，先跑完 OCR。平台不是 xhs / 字段为空 → 跳过本步。
2. 从 manifest 读 `suggested_preprocess_cmds`，逐条执行预处理。只采到 contents、未采到 comments 时命令会自动省略 `--comments`。
3. 默认输出文件位于 contents 文件同目录下，文件名形如 `preprocessed_<input_stem>.jsonl` / `.csv` / `.summary.json`。
4. 把 OCR 与预处理输出文件一并通过 `file-delivery` 技能交付给用户。

## Pitfalls

### 同次对话里用户提出“不要重复爬取，只做 OCR/清洗并交付”
用户明确要求只做离线处理时：
- **不要重新跑 `run_crawl.py`**。
- 先在用户给出的 run_id / 目录下定位已有产物：`search_contents_*.jsonl`、`search_comments_*.jsonl`、`ocr_search_contents_*.jsonl`。
- 若缺少 OCR 文件 → 只跑 `step1_ocr.py`（或按用户要求直接做回填/清洗）。
- 若已有 OCR 但 CSV 里 `image_ocr_line` 全空 → 走“回填修复”。
- 最终交付时再打包（posts+comments）。

### OCR 已完成但 posts CSV 的 `image_ocr_line` 全空（回填修复）
如果你发现：
- `ocr_*.jsonl` 里 OCR 文本是非空的；但
- 导出的 posts CSV 里 `image_ocr_line` 全空；

不要重新爬取。按 `references/xhs-ocr-backfill-into-posts-csv.md` 的方案，按 `note_id` 将 OCR 回填到 posts CSV，再打包交付。

### 9200 端口不是 serve.py（而是 python -m http.server），导致 /download/ 404
如果 `curl -I http://127.0.0.1:9200/` 响应头包含 `Server: SimpleHTTP/`，说明 9200 是静态目录服务：
- **不要**生成 `/download/<path>` 链接（会 404）。
- 应用“目录直出”的链接（`/<relative_path>`），并通过访问根目录的 listing 判断其根目录（例如能看到 `/media_data/` 则优先把产物复制到 `/hpfu/media_data/exports/` 再交付）。

### 多个 run_id 合并时“并非所有 run 都有 8 个关键词产物”
当用户说“同一批是 8/12 个关键词”，但你在给定 run_id 下只找到部分关键词：
- 用 `glob('**/search_contents_*.jsonl')` 与 `glob('**/search_comments_*.jsonl')` 逐 run 统计。
- 只基于**真实落盘文件**生成统计与交付，不要假设每个 run 都包含完整矩阵。
- 若 comments 规范要求“必须提供”但 run 内无 `search_comments_*.jsonl`：明确告知缺失原因，并建议用户提供包含 comments 产物的 run 或重新抓取时开启 `--with-comments`。

## 输出字段映射

MediaCrawler 直接采集到的数据视为 L1 即时采集字段。下游数据集重点关注：

- `post_id`：平台笔记/帖子 ID。
- `url`：内容地址。
- `author_id`、`author_name`、`author_bio`：作者信息，可用时保留。
- `title`、`body_text`、`hashtags`：标题、正文、话题标签。
- `images`、`video_duration`、`publish_time`：媒体与发布时间。
- `image_ocr_line`（xhs）：`step1_ocr.py` 拼接后的单行图片 OCR 文本，多图以 ` | ` 分隔。**xhs 图文帖干货几乎都在这个字段里**，下游 LLM 打标必读。
- `likes`、`saves`、`shares`、`comments_count`：互动指标。
- `top_comments`、`comment_threads`：采集评论时保留高赞评论和评论树。
- `is_series`、`text_length`、`image_count`：内容结构辅助字段。
- `source_keyword` 或 `query_axis`：本条内容对应的 X 轴 × Y 轴搜索关键词。

L2 字段是离线计算字段，不要在采集阶段编造。包括：

`content_type`、`ai_score`、`sentiment_polarity`、`fact_check_flag`、`has_chart`、`entities`、`creator_credibility_score`、`audience_expert_match`、`negative_ratio`、`comment_depth_score`、`save_like_ratio`、`decay_rate_7d`、`decay_rate_30d`、`hotspot_delta_h`。

这些字段应在后续 enrichment 流程中通过 LLM、NER、情感分析、评论树分析或规则计算生成（`image_ocr_line` 已上提为 L1，不再列入 L2）。
