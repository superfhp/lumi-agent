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
- `max_concurrency`：并发数，默认 2；小红书建议 2-3，不要过高。
- `sleep_sec`：抓取间隔秒数，默认 0.5；越低越快，但风控风险越高。
- `cookies`：只允许通过命令行参数或环境变量传入。不要在回复中打印 cookie。

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

使用已有的 MediaCrawler 项目。默认路径：

`/mnt/workspace/MediaCrawler`

无桌面 Linux server 运行 `xhs` 时，优先使用 cookie 登录，并确保 Chrome CDP 已经可用：

`http://127.0.0.1:9222/json/version`

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
3. 调用 `scripts/run_crawl.py`，传入平台、关键词、条目数、评论采集设置、并发数和 sleep 秒数。
4. 脚本会实时透传 MediaCrawler 日志，并按关键词逐个运行；不要在详情页抓取阶段中断，因为部分平台会在整页详情抓取完成后才写文件。
5. 解析脚本最终输出的 JSON manifest。
6. 返回生成文件路径、记录数、平台、关键词、执行状态。
7. 如果没有生成文件，检查脚本输出的 `log_tail` 并解释失败原因。

示例调用：

```bash
python skills/mediacrawler-social-crawl/scripts/run_crawl.py \
  --mediacrawler-root /mnt/workspace/MediaCrawler \
  --platform xhs \
  --keywords "大模型微调 保姆级教程,SFT 避坑" \
  --max-notes-count 20 \
  --max-concurrency 2 \
  --sleep-sec 0.5
```

## 输出字段映射

MediaCrawler 直接采集到的数据视为 L1 即时采集字段。下游数据集重点关注：

- `post_id`：平台笔记/帖子 ID。
- `url`：内容地址。
- `author_id`、`author_name`、`author_bio`：作者信息，可用时保留。
- `title`、`body_text`、`hashtags`：标题、正文、话题标签。
- `images`、`video_duration`、`publish_time`：媒体与发布时间。
- `likes`、`saves`、`shares`、`comments_count`：互动指标。
- `top_comments`、`comment_threads`：采集评论时保留高赞评论和评论树。
- `is_series`、`text_length`、`image_count`：内容结构辅助字段。
- `source_keyword` 或 `query_axis`：本条内容对应的 X 轴 × Y 轴搜索关键词。

L2 字段是离线计算字段，不要在采集阶段编造。包括：

`content_type`、`ai_score`、`sentiment_polarity`、`fact_check_flag`、`has_chart`、`image_ocr_text`、`entities`、`creator_credibility_score`、`audience_expert_match`、`negative_ratio`、`comment_depth_score`、`save_like_ratio`、`decay_rate_7d`、`decay_rate_30d`、`hotspot_delta_h`。

这些字段应在后续 enrichment 流程中通过 LLM、OCR、NER、情感分析、评论树分析或规则计算生成。
