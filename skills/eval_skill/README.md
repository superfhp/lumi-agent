# 新评测集接入指南

> 版本：v1.0 · 2026-06-15
> 适用对象：首次接入评测集的新人
> 前置条件：已拉取仓库、skill_commons/.env 已配置 API key

---

## 0. 接入一个评测集的完整流程

```
准备数据 → 写 jsonl/csv → upload 到 Lumi → 写 yaml 配置 → preview 验证 → 跑评测
```

下面按步骤详细说明。

---

## 1. 准备数据：评测集格式要求

### 1.1 每条 item 三段式结构

评测集的每条数据必须严格遵循 **v2.1 规范**，由三部分组成：

```jsonc
{
  "input": { ... },             // 题目输入
  "expected_output": { ... },   // 预期答案 / 评分标准
  "metadata": { ... }           // 元信息（domain、题型等）
}
```

### 1.2 input —— 题目怎么写

input 支持 **四种模式**，根据你的数据形态选一种：

| 模式 | 适用场景 | 字段 | 示例 |
|---|---|---|---|
| **结构化**（推荐） | 题目和 prompt 可以分开迭代 | `question`、`options`、`background` 等 | 选择题、问答题 |
| **prompt-baked** | prompt 和数据强耦合、不可拆 | `prompt`（一整段文本） | 含表格的复杂指令 |
| **多轮** | 多轮对话评测 | `turns: ["第1轮", "第2轮", ...]` | 对话连贯性评测 |
| **PDF 长文档** | 需要读 PDF 才能答题 | `pdf_refs: [{label, path}]` | 年报分析 |

**结构化示例**（最常用）：

```json
{
  "input": {
    "question": "以下哪项属于私募基金的合规要求？",
    "options": {"A": "可公开募集", "B": "须备案登记", "C": "无投资限制", "D": "无需披露信息"},
    "background": "根据《私募投资基金监督管理暂行办法》..."
  }
}
```

**prompt-baked 示例**：

```json
{
  "input": {
    "prompt": "请阅读以下财报数据并分析：\n\n| 项目 | 2024 | 2023 |\n|---|---|---|\n| 营收 | 100亿 | 80亿 |\n\n问题：营收增长率是多少？"
  }
}
```

**多轮示例**：

```json
{
  "input": {
    "turns": [
      "帮我分析一下苹果公司的财务状况",
      "重点看一下现金流部分",
      "和去年同期相比有什么变化？"
    ]
  }
}
```

### 1.3 expected_output —— 预期答案怎么写

根据题型不同，填不同的字段：

| 题型 | 必填字段 | 示例 |
|---|---|---|
| 单选 | `answer: "B"` | `{"answer": "B"}` |
| 多选 | `answer: ["A", "C"]` | `{"answer": ["A", "C"]}` |
| 数值 | `answer: 1509222038` | `{"answer": 1509222038}` |
| 精确文本 | `answer: "备案登记"` | `{"answer": "备案登记"}` |
| 主观/开放 | `rubric: "..."` 或留空 | `{"rubric": "需包含风险分析和投资建议"}` |

可选字段（帮助 judge 更好评分）：

```jsonc
{
  "answer": "B",
  "reasoning_ref": "参考推理过程...",    // 可选，judge 参考
  "explanation": "官方解析...",          // 可选，judge 参考
  "rubric": "评分标准..."               // 主观题必填
}
```

**关键原则：`expected_output` 里只放题目本身的"事实"——答案、解析、评分标准。不要放预期信号（expected_signals）或任何对模型行为的预判。**

### 1.4 metadata —— 必须填的元信息

```jsonc
{
  "metadata": {
    "domain": "finance",                // ⚠️ 必填！没有 domain 的 dataset 不会被 list-datasets 列出
    "schema": "single_choice",           // ⚠️ 必填！决定题型和默认指标
    "category": "合规",                 // 可选，便于分类统计
    "tags": ["L2", "私募"]              // 可选，便于筛选
  }
}
```

**两个强制字段**：

**`domain`** —— 决定评测集归属哪个领域：

| domain | 含义 |
|---|---|
| `common` | 通用领域 |
| `finance` | 金融领域 |
| `medical` | 医疗领域 |
| 其他 | 按需自定义，确保 `list-datasets --domain <你的domain>` 能找到 |

**`schema`** —— 决定题型，系统据此选择默认指标和答案解析方式：

| schema | 含义 | answer 类型 | 默认指标 |
|---|---|---|---|
| `single_choice` | 单选题 | `"B"` | `accuracy` |
| `array` | 多选题 | `["A", "C"]` | `array_recall` / `array_f1` |
| `number` | 数值题 | `1509222038` | `numeric_match` |
| `string` | 精确文本 | `"备案登记"` | `exact_match` / `contains` |
| `open_ended` | 开放主观题 | `null`（靠 rubric） | `rubric_judge` |
| `dialog` | 多轮对话 | `null`（靠 rubric） | `rubric_judge` |
| `report_pair` | 研报对比 | `null`（靠 expected_md） | `report_quality` |

> **大多数评测集是客观题**（`single_choice` / `array` / `number` / `string`），只需填好 `schema` + `answer`，跑默认客观指标即可，不需要配 judge。

### 1.5 完整示例：一条单选题

```json
{
  "input": {
    "question": "以下哪项属于私募基金的合规要求？",
    "options": {"A": "可公开募集", "B": "须备案登记", "C": "无投资限制", "D": "无需披露信息"},
    "background": "根据《私募投资基金监督管理暂行办法》..."
  },
  "expected_output": {
    "answer": "B"
  },
  "metadata": {
    "domain": "finance",
    "schema": "single_choice",
    "category": "合规"
  }
}
```

### 1.6 完整示例：一条开放主观题

```json
{
  "input": {
    "question": "请分析2024年A股市场的主要风险因素"
  },
  "expected_output": {
    "rubric": "需覆盖宏观经济、地缘政治、流动性三个维度，有数据支撑"
  },
  "metadata": {
    "domain": "finance",
    "schema": "open_ended",
    "category": "投研分析"
  }
}
```

---

## 2. 上传数据到 Lumi

### 2.1 准备文件

两种格式二选一：

**jsonl（推荐）**：每行一个完整的 v2.1 item

```bash
# my_dataset.jsonl，每行一个 JSON
{"input":{"question":"...","options":{"A":"...","B":"..."}},"expected_output":{"answer":"A"},"metadata":{"domain":"finance","schema":"single_choice","category":"合规"}}
{"input":{"question":"..."},"expected_output":{"answer":"42"},"metadata":{"domain":"finance","schema":"number"}}
```

**csv**：扁平字段，上传时指定映射

```csv
question,options,answer,category,domain
"以下哪项...","{""A"":""...""",""B"":""...""}","B","合规","finance"
```

### 2.2 先 dry-run 预览

```bash
# jsonl
python -m eval_skill.cli upload-dataset \
    --file my_dataset.jsonl \
    --name My-Finance-Compliance \
    --dry-run

# csv（需指定字段映射）
python -m eval_skill.cli upload-dataset \
    --file my_dataset.csv \
    --name My-Finance-Compliance \
    --csv-schema single_choice \
    --csv-input-keys question,options \
    --csv-expected-key answer \
    --csv-metadata-keys category,domain \
    --dry-run
```

dry-run 会显示前 5 条解析结果，**确认格式正确后**再去掉 `--dry-run` 真正上传：

```bash
python -m eval_skill.cli upload-dataset \
    --file my_dataset.jsonl \
    --name My-Finance-Compliance
```

### 2.3 验证上传结果

```bash
# 看 dataset 是否出现在列表里
python -m eval_skill.cli list-datasets --domain finance

# 预览前 5 条
python -m eval_skill.cli preview-dataset --name My-Finance-Compliance --limit 5
```

---

## 3. 写评测配置 yaml

### 3.1 最小配置模板

在 `eval_skill/configs/` 下新建你的 yaml：

```yaml
experiment_name: my_compliance_eval
tags: [finance, compliance]
description: "金融合规评测"

# ── 评测集 ──
dataset:
  name: My-Finance-Compliance       # Lumi 上的 dataset 名

# ── 抽样（可选） ──
sampling:
  mode: n                           # n=抽N条, all=全量
  n: 10

# ── 提问方式 ──
prompt_strategy:
  system_prompt_ref: prompts/system/finance_default.txt    # system prompt 文件
  user_template: |
    【题目】
    {question}

    【选项】
    {options}

    请先给出推理过程，再在最后一行写：最终答案：X

# ── 被测模型 ──
model_under_test:
  host_profile: zerail              # skill_commons/registry/host_profiles.yaml 里的 host 名
  model: claude-sonnet-4
  temperature: 0.1
  run_prefix: mut                   # CSV 里的标签

# ── 对比模型（可选）──
baselines:
  - host_profile: iquest
    model: kimi-k2.6
    run_prefix: kimi

# ── 指标（客观题只需客观指标，不需要配 judge）──
metrics:
  - name: accuracy
    extractor: cn_final_answer      # 从模型回答里抽取"最终答案：X"

# ── 评分 judge（仅主观指标需要，客观题删掉这段）──
# judge:
#   host_profile: zerail
#   model: claude-sonnet-4
#   temperature: 0.0

# ── 执行 ──
execution:
  rounds: 1
  reporter: [csv, lumi]            # csv=本地文件, lumi=上传 Langfuse
```

### 3.2 prompt 怎么指定

prompt 体系分三层，**改 prompt 不需要改 dataset**：

#### 3.2.1 system prompt

给模型设定角色和回答规范。放在 `prompts/system/` 下：

```yaml
prompt_strategy:
  # 方式 1：引用文件（推荐）
  system_prompt_ref: prompts/system/finance_default.txt

  # 方式 2：直接内联
  system_prompt: |
    你是一位金融分析师...
```

常用 system prompt 文件：

| 文件 | 用途 |
|---|---|
| `prompts/system/general.txt` | 通用领域 |
| `prompts/system/finance_quant.txt` | CFA/量化 |
| `prompts/system/finance_compliance.txt` | 金融合规 |
| `prompts/system/finance_news.txt` | 金融新闻 |
| `prompts/system/sec_10k_analyst.txt` | 10-K 分析 |

没有合适的？新建一个 `prompts/system/your_domain.txt` 即可。

#### 3.2.2 user template

控制题目怎么拼成 user message。只有**结构化 input** 才需要（prompt-baked 和 turns 模式会绕过）：

```yaml
prompt_strategy:
  user_template: |
    【背景】
    {background}

    【题目】
    {question}

    【选项】
    {options}

    请给出推理过程，最后一行写：最终答案：X
```

模板里用 `{字段名}` 引用 input 里的字段，缺失字段自动替换为空字符串。

#### 3.2.3 judge prompt（主观指标的评分 prompt）

主观指标（reasoning_quality、rubric_judge 等）有默认 prompt，也可以覆盖：

```yaml
metrics:
  # 用默认 prompt
  - name: reasoning_quality

  # 引用自定义 prompt 文件
  - name: reasoning_quality
    alias: rq_strict                                            # 同指标多变体时必须加 alias
    prompt_ref: prompts/judge/reasoning_quality_compliance.md

  # 临时内联 prompt
  - name: rubric_judge
    prompt_inline: |
      你是阅卷老师...
      输出 JSON: {"score":..,"reason":..,"thinking":..}
```

**RVEC pipeline** 走领域包机制，不用上面的 prompt_ref/inline：

```yaml
metrics:
  - name: rvec_judge
    alias: finance_rvec
    prompt_pack: prompts/judge/rvec_general    # 指向 pack.yaml 所在目录
```

### 3.3 常见题型的 yaml 配方

<details>
<summary>📋 单选题（schema=single_choice）</summary>

```yaml
metrics:
  - name: accuracy
    extractor: cn_final_answer    # 抽取「最终答案：X」
prompt_strategy:
  user_template: |
    {question}
    {options}
    请在最后一行写：最终答案：X
```
</details>

<details>
<summary>📋 多选题（schema=array）</summary>

```yaml
metrics:
  - name: array_recall
  - name: array_f1
prompt_strategy:
  user_template: |
    {question}
    {options}
    请列出所有正确选项，用逗号分隔。
```
</details>

<details>
<summary>📋 数值计算题（schema=number）</summary>

```yaml
metrics:
  - name: numeric_match
    tolerance: 0.01
prompt_strategy:
  user_template: |
    {question}
    请给出最终数值结果。
```
</details>

<details>
<summary>📋 文本匹配题（schema=string）</summary>

```yaml
metrics:
  - name: exact_match            # 严格相等
  # 或：
  # - name: contains             # 答案是回答的子串即可
prompt_strategy:
  user_template: |
    {question}
    请直接给出答案。
```
</details>

<details>
<summary>📋 开放主观题（schema=open_ended，需要配 judge）</summary>

```yaml
metrics:
  - name: rubric_judge           # 按 rubric 打分，1 次 LLM 调用
judge:
  host_profile: zerail
  model: claude-sonnet-4
  temperature: 0.0
```

如果需要更精细的 RVEC 体系评分（6 次 LLM 调用，成本更高）：

```yaml
metrics:
  - name: rvec_judge
    alias: general_rvec
    prompt_pack: prompts/judge/rvec_general
```
</details>

---

## 4. 跑评测

### 4.1 先确认配置

```bash
python -m eval_skill.cli describe-config -c configs/my_eval.yaml
```

检查输出的评测计划——dataset 名、模型、指标、预计调用量是否符合预期。

### 4.2 小规模试跑

```bash
python -m eval_skill.cli run -c configs/my_eval.yaml --sample 3 --no-resume
```

确认 CSV 输出正常、分数合理。

### 4.3 正式执行

```bash
python -m eval_skill.cli run -c configs/my_eval.yaml
```

执行完成后会输出：
- `samples.csv` 路径 —— 每行一条评测结果明细
- `summary.json` 路径 —— 聚合统计
- 评测结果表格 —— question / expected / actual / score / reason
- Langfuse 实验链接 —— 点击可在 Langfuse 上查看详细 trace

---

## 5. Checklist：新评测集接入自查

| # | 检查项 | 通过标准 |
|---|---|---|
| 1 | `metadata.domain` 已填 | `list-datasets --domain <domain>` 能看到 |
| 2 | `metadata.schema` 已填且匹配题型 | `preview-dataset` 显示正确 |
| 3 | 客观题 `expected_output.answer` 非空 | preview 中无 ⚠️ 标记 |
| 4 | 主观题 `expected_output.rubric` 已填 | schema=open_ended/dialog 时必填 |
| 5 | yaml 里 `dataset.name` 与 Lumi 上一致 | `describe-config` 不报错 |
| 6 | `prompt_strategy` 的 user_template 和 system_prompt 已设置 | 小规模试跑输出合理 |
| 7 | `metrics` 与 schema 匹配 | 参考 §1.4 schema 对照表 |
| 8 | `reporter` 包含 `csv`（必须）和 `lumi`（推荐） | — |
| 9 | `--sample 3` 试跑正常 | CSV 有输出、分数在合理范围 |

---

## 6. 常见问题

**Q: `list-datasets` 看不到我的 dataset？**
A: 检查 `dataset的domain` 是否已填。没有 domain 的 dataset 默认不展示。

**Q: 上传 csv 报 schema 错误？**
A: csv 模式必须指定 `--csv-schema`，告诉系统题型是什么。

**Q: 模型回答格式不对，accuracy 全是 0？**
A: 检查 `extractor` 是否和 user_template 里的回答引导语匹配。比如 prompt 写了「最终答案：X」就用 `cn_final_answer`。

**Q: 想换一个 prompt 重跑，需要改 dataset 吗？**
A: 不需要。prompt 和 dataset 完全解耦——改 yaml 里的 `system_prompt_ref` / `user_template` / `prompt_ref` 即可。

**Q: 主观题用 rubric_judge 还是 rvec_judge？**
A: 大多数场景用 `rubric_judge` 就够了（1 次 LLM 调用，简单快速）。`rvec_judge` 是 RVEC 三步法体系（6 次 LLM 调用），适用于需要 R/V/E/C 四维精细打标的场景，成本更高，先用 `--sample 5` 小规模验证。
