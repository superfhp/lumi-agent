# eval_skill — 评测执行 SKILL 技术方案

> 版本：v2.0 · 2026-06-17
> 目标读者：评测者、Agent、未来扩展者
> 范围：金融 / 通用 / 后续多领域；多轮、长 PDF、客观+主观指标；多模型横评；Langfuse 全链路追踪

---

## 0. TL;DR

- **dataset 在 Langfuse(Lumi) 上集中管理**，遵循统一 v2 规范（[§3](#3-dataset-规范-v2)）。
- **prompt 不入 dataset**：system / 用户模板 / 多轮策略 / judge prompt 放在 `prompts/` 下文件，由 yaml 引用。改 prompt 不改 dataset；单次实验调 prompt 在 yaml 里 `prompt_ref` / `prompt_inline` / `user_prompt_ref` / `user_prompt_inline` 覆盖即可（[§5.4](#54-调-prompt-的工作流)、[§7.4](#74-yaml-中指定指标完整字段)）。
- **PDF 长文档不入 dataset**：dataset 只存路径或 URL，运行时由 `PDFRetriever` 做关键词命中 + 章节抽取 + 截断（沿用 [for_eval/analyse_report.py](../for_eval/analyse_report.py) 的方案）。
- **一份 yaml 跑横评**：`model_under_test` + `baselines` 共享同一份 dataset / prompt / 指标。
- **指标统一返回 `{value, reason, extra}`**：
  - **客观**（6 个）：accuracy / exact_match / contains / array_recall / array_f1 / numeric_match
  - **单值打分主观**（4 个）：reasoning_quality / factuality_score / rubric_judge / custom_judge
  - **领域级 pipeline 主观**（1 个）：rvec_judge — RVEC 三步法 6 次 LLM 调用，自动按 mut/baseline 配 cap
  - 主观指标都通过 `prompts/judge/` 下文件解耦（领域级走 `rvec_<domain>/` 包）
- **共享基础设施在 [skill_commons/](../skill_commons/)**：env 加载、host_profiles、Lumi 客户端、redaction profile —— 由 eval_skill / for_report_skill / for_dataset_skill 共用，eval_skill 自身不持有 host 注册表。
- **统一 Trace 上报**：每条 sample × model 生成一条 Langfuse trace；多轮场景按 turn 生成 generation span；执行结束后输出结果摘要表 + Langfuse 实验链接。
- **数据分析靠 `experiment_name + tags` 透传**：CSV 每行 + Langfuse trace tags 都有，便于切片。
- **CLI 入口**：`python -m eval_skill.cli run -c configs/runs/<file>.yaml`。
- **新建评测数据集**：参见 [ONBOARDING.md](ONBOARDING.md)。

---

## 1. 设计原则

| 原则 | 含义 |
|---|---|
| **关注点分离** | 题目（dataset）/ 提问方式（prompt）/ 评分标准（metric）/ 模型与 host（registry）/ 实验参数（yaml）五件事独立演化，互不耦合。 |
| **共享基础设施抽离** | host 注册、环境变量加载、Lumi 客户端、脱敏 profile 这类跨 skill 共享的能力放到兄弟目录 [`skill_commons/`](../skill_commons/)，eval_skill / for_report_skill / for_dataset_skill 共用，避免重复。 |
| **dataset 极简** | 只存"事实"——题目、答案、参考解析、领域分类。不存 prompt、不存 PDF 内容、不存模型/host 信息。 |
| **大文本懒加载** | PDF 等长文档以"指针"形式入 dataset，运行时 `PDFRetriever` 抽取压缩。 |
| **可复现** | 所有实验由一份可 review、可 diff、可入 git 的 yaml 描述。 |
| **可 check** | 每个指标必须返回 `value + reason`，CSV 直接看到为什么扣分，可人工复核。 |
| **主观评测 fail-loud** | LLM judge 解析失败一律返回 0 分 + `judge_failed=true`，绝不静默给高分。 |
| **host 与 model 解耦** | host（base_url/api_key）变化少，写在 `skill_commons/registry/host_profiles.yaml`；model 变化多，写在 yaml 里只引用 host 名。 |
| **dataset 一次性迁移到 v2** | 老数据不多，直接按 v2 规范重新导入到 Langfuse，不维护字段别名兼容层（避免长期技术债）。 |

---

## 2. 总体架构

```
┌───────────────────────────────────────────────────────────────────────────┐
│                       用户口语化需求 / Agent 调用                          │
└───────────────┬───────────────────────────────────────────────────────────┘
                │ 翻译成 yaml
                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  configs/runs/<experiment>.yaml   (ExperimentConfig)                       │
└───────────────┬───────────────────────────────────────────────────────────┘
                │ python -m eval_skill.cli run -c <yaml>
                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                              Evaluator (编排)                              │
│                                                                            │
│   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌────────────┐ │
│   │ DatasetLoader│──▶│ SampleParser │──▶│PromptBuilder │──▶│   Runner   │ │
│   │  (Langfuse)  │   │  v2 schema   │   │ system+turns │   │  streaming │ │
│   │  + sampling  │   │  pdf_refs→md │   │ +per_turn_pre│   │ +reasoning │ │
│   └──────────────┘   └──────┬───────┘   └──────────────┘   └─────┬──────┘ │
│                             │                                     │        │
│                             ▼                                     ▼        │
│                     ┌──────────────┐                     ┌──────────────┐ │
│                     │PDFRetriever  │                     │   Metrics    │ │
│                     │ smart/full   │                     │  obj + subj  │ │
│                     │ +redaction   │                     │ {value,reason}│ │
│                     │ +cache       │                     └──────┬───────┘ │
│                     └──────────────┘                            │         │
│                                                                 ▼         │
│                                                          ┌──────────────┐ │
│                                                          │    Judge     │ │
│                                                          │ global +     │ │
│                                                          │ per-metric   │ │
│                                                          │ override     │ │
│                                                          └──────┬───────┘ │
│                                                                 │         │
│                                                                 ▼         │
│   ┌──────────────────────────────────────────────────────────────────┐   │
│   │                            Reporter                              │   │
│   │  outputs/<exp>/samples.csv  + summary.json  + Langfuse traces   │   │
│   └──────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 3. dataset 规范 v2

dataset 集中放在 Langfuse(Lumi) 上。每条 item 三段式：

### 3.1 item.input

至少满足以下三种模式之一；可叠加：

| 模式 | 字段 | 何时使用 |
|---|---|---|
| **结构化** | `question` / `options`（dict）/ `background` / 业务自定义字段 | 推荐。题目独立于 prompt，可在 yaml 里改提问方式。 |
| **prompt-baked** | `prompt`（已渲染整段文本） | prompt 与数据强耦合（如 case2/3/4 那种含表格的指令）。改 prompt 必改 dataset。 |
| **多轮** | `turns: [{content, meta}, ...]`（按顺序的 user 内容） | 一条 item 装完整对话，避免 case6/7 那种拆条丢失上下文。 |
| **PDF 长文档** | `pdf_refs: [{label, path, ...}]` | PDF 不入 dataset；运行时 PDFRetriever 抽取注入。 |

### 3.2 item.expected

```jsonc
{
  "answer": "B" 或 ["x","y"] 或 1509222038 或 null,
  "reasoning_ref": "参考推理思路",                                // 可选，judge 用
  "explanation": "官方解析",                                      // 可选，judge 用
  "rubric": "主观题评分准则"                                      // open_ended/dialog 必填
}
```

> 注：题型一律由 `metadata.schema` 决定，answer 的类型本身（str / list / int / float / null）是补充信息，不再设 `answer_format` 这种冗余字段。

### 3.3 item.metadata

```jsonc
{
  "schema": "single_choice | array | string | number | open_ended | dialog | report_pair",
  "domain": "finance | general | medical | ...",
  "subset": "Fin-Compliance",
  "tags": ["L2", "F-极端方案"],
  "dialog_id": "fitness_extreme_001",          // dialog/多轮聚合键
  "label_map": {"0":"负面","1":"中性","2":"正面"}   // answer 是 id 时必填
}
```

### 3.4 schema → 默认指标对照

| schema | 默认客观 | 默认主观 |
|---|---|---|
| `single_choice` | `accuracy` | `reasoning_quality`（有 explanation 时） |
| `array` | `array_recall`, `array_f1` | `factuality_score` |
| `number` | `numeric_match`（带 tolerance） | `reasoning_quality` |
| `string` | `exact_match` 或 `contains` | `factuality_score` |
| `open_ended` | — | `rvec_judge`（领域级体系）或 `rubric_judge`（per-sample） |
| `dialog` | — | `rvec_judge` / `rubric_judge`（per-turn） |
| `report_pair` | — | `report_quality`, `grounding_coverage`（M3） |

### 3.5 老数据迁移规则（一次性，不保留兼容层）

用户决定按 v2 规范一次性重新导入，因此评测代码**不实现字段别名兼容**，dataset 只认 v2 字段名。迁移工具按下面的规则做字段重命名 + 类型修正：

| 老字段 / 老形态 | v2 字段 / 形态 |
|---|---|
| `expected.correct_answer` | `expected.answer` |
| `expected.ground_truth` | `expected.answer` |
| `expected.sentiment_id` | `expected.answer`（同时补 `metadata.label_map`） |
| `input.background_context` | `input.background` |
| `input.options` 是 dict | 保持 dict |
| 数组答案存成 `"14, 15"` 字符串 | 转成 `["14","15"]`（`metadata.schema=array` 接管题型） |
| 多轮拆成多条 item（无 dialog 关联） | 按 `dialog_id` 聚合成一条 item，`input.turns=[...]` |
| 主观题无答案 | 必填 `expected.rubric` |

迁移工具：[tools/migrate_legacy_dataset.py](tools/migrate_legacy_dataset.py)（M1 阶段产出）。

### 3.6 强制治理项（v2 规范要求）

由于 dataset 数量不多，**所有数据按 v2 重新导入**：

- case4 的 `"14,15"` → `["14","15"]`（`metadata.schema=array`）
- case5 的 id 答案 → 必填 `metadata.label_map`
- case6/7 同对话拆条 → 聚合成一条 item，使用 `input.turns`，主观题补 `expected.rubric`
- 字段名统一：`background_context` → `background`、各种答案字段 → `answer`

---

## 4. PDF 长文档处理

### 4.1 入库阶段：dataset 只存指针

```jsonc
input.pdf_refs: [
  {"label": "FY2015", "path": "/data/reports/zc-2015.pdf", "year": 2015}
]
expected: {
  "ground_truth_text_path": "/data/gt/zc-answer.txt",   // 或 ground_truth_pdf_path
  "rubric": "对比维度：业务/财务/风险/展望"
}
metadata: {"schema": "report_pair", "redaction_profile": "default"}
```

### 4.2 运行时：PDFRetriever

吸收 [for_eval/analyse_report.py#L246](../for_eval/analyse_report.py) 和 [for_eval/report_generate.py](../for_eval/report_generate.py) 的能力：

```python
class PDFRetriever:
    def __init__(self, redaction: dict, cache_dir: Path): ...

    # 全量抽取（用于基准研报，max_pages 截断保护）
    def extract_full(self, path, max_pages=50) -> str: ...

    # 智能章节抽取（关键词命中 + 前后 ±N 页 + 按字符上限截断）
    def extract_smart(self, path, keywords: list, window=(-1, 3),
                      max_chars=28000) -> str: ...
```

**两个关键设计**：

- **磁盘缓存**：以 `(abs_path, mtime, mode, keywords_hash)` 为 key，缓存到 `outputs/_cache/pdf/`，同一 PDF 同一策略只抽一次。
- **脱敏在抽取后立即施加**：`skill_commons/registry/redaction_profiles.yaml` 集中管理脱敏规则集，dataset 里只填 profile 名字。

### 4.3 yaml 配置

```yaml
preprocess:
  pdf:
    mode: smart
    keywords: [Item 1, Item 1A, Item 7, Item 8, Risk Factors, MD&A,
               Management, Discussion, Revenue, Net income]
    window: [-1, 3]
    max_chars: 28000
    redaction_profile: default      # 引用 skill_commons/registry/redaction_profiles.yaml

  ground_truth:
    mode: full
    max_pages: 50
```

### 4.4 注入位置

PromptBuilder 把每个 `pdf_refs` 抽取后的 markdown 作为**前置 user message**：

```
[system]   prompt_strategy.system_prompt_ref 的内容
[user]     # FY2015\n\n<smart_extract(zc-2015.pdf)>
[user]     # FY2018\n\n<smart_extract(zc-2018.pdf)>
[user]     <user_template 渲染结果 或 prompt 或 turns[0].content>
[assistant] ...
[user]     <turns[1].content>   ← 多轮才有
...
```

`expected.ground_truth_text_path` 仅 judge 时加载，不进 messages（避免泄漏给被测模型）。

---

## 5. Prompt 体系

### 5.1 PromptStrategy（yaml 字段）

```yaml
prompt_strategy:
  system_prompt_ref: prompts/system/finance_quant.txt   # 或 system_prompt 直接写

  # 仅"结构化"item 用；prompt-baked 与 turns 模式直接绕过
  user_template: |
    【背景】
    {background}

    【题目】
    {question}

    【选项】
    {options}

  # 多轮的统一前缀（可选）
  per_turn_prefix: ""
```

### 5.2 渲染优先级

PromptBuilder 按以下顺序决定 user messages：

```
1. input.turns 存在 → 逐 turn 渲染：per_turn_prefix + turn.content
2. input.prompt 存在 → 单轮：prompt 直接作为 user
3. 否则用 user_template + input 字段格式化
```

→ **改提问方式只动 yaml / prompts/system 文件，不动 dataset**。

### 5.3 prompts/ 目录

```
prompts/
  system/
    finance_quant.txt        # CFA/量化金融
    finance_compliance.txt   # 合规
    finance_news.txt         # 新闻情感/归因
    general.txt              # 通用
    sec_10k_analyst.txt      # 10-K 分析师
  user/
    10k_compare.txt          # 复杂用户模板，user_template_ref 引用
  judge/
    # 单值打分主观指标（一个 metric 对应一个 .md）
    reasoning_quality.md
    factuality_score.md
    rubric_judge.md
    custom_judge.md
    # 领域级 RVEC pipeline 包（一个 metric 对应一个目录）
    rvec_general/
      pack.yaml              # 信号集 + caps + scoring_mode
      step1_understand.md
      step2_R.md
      step2_V.md
      step2_E.md
      step2_C.md
      step3_scoring.md
    rvec_medical/            # 同构独立维护（M3 阶段产出）
      pack.yaml
      ...
    # M3 多文档评测
    report_quality.md        # 直接复用 for_eval/report_generate.py 的 prompt
    grounding_coverage.md    # 直接复用 for_eval/analyse_report.py 的 prompt
```

### 5.4 调 prompt 的工作流

主观指标的 prompt 分两段：
- **system prompt**：评分原则、维度定义、输出格式（所有样本共用）
- **user prompt**：拼装当前 sample + output（每样本不同）

两段都可以覆盖，都不需要改代码、不需要改 dataset：

| 范围 | system prompt | user prompt |
|---|---|---|
| **全局** | 直接改 [prompts/judge/&lt;name&gt;.md](prompts/judge/) | 指标实现里的默认兑底模板 |
| **某评测集 / 某次实验** | yaml 加 `prompt_ref: prompts/judge/<name>_<scope>.md` | yaml 加 `user_prompt_ref: prompts/judge/<name>_<scope>_user.md` |
| **临时调一次** | yaml 加 `prompt_inline: \| ...` | yaml 加 `user_prompt_inline: \| ...` |

user prompt 模板能用的变量（缺失自动空字符串）：

| 类别 | 变量 |
|---|---|
| 输入侧 | `{question}` `{background}` `{options}` |
| 模型输出 | `{answer}` `{prediction}` `{reasoning}` `{all_answers}` |
| ground truth | `{ground_truth}` `{explanation}` `{reasoning_ref}` `{rubric}` `{expected_md}` |
| 透传 | `input.fields.*`（直接写 `{step1}`）/ `metadata.*`（直接写 `{domain}`） |

**变体 prompt 命名规范**：`<metric>_<scope>.md`，例如：

```
prompts/judge/
  reasoning_quality.md                 # 默认
  reasoning_quality_compliance.md      # 合规题专用（更严扣分）
  reasoning_quality_10k.md             # 10-K 专用（侧重财务推理）
  factuality_score.md                  # 默认
  factuality_score_news.md             # 新闻类（侧重事件还原）
```

**领域级 RVEC 包的调整路径完全不同**——调 prompt 几乎都是改 [prompts/judge/rvec_<domain>/pack.yaml](prompts/judge/rvec_general/pack.yaml)（标签增删改、caps 调整）或 6 个 step 的 .md，详见 [§7.5](#75-主观评测-rvec-pipeline)。

**优先级**：`prompt_inline` > `prompt_ref` > 该指标默认 prompt 文件。

**示例：仅这次实验给 reasoning_quality 换一份 prompt**：

```yaml
metrics:
  - accuracy
  - name: reasoning_quality
    prompt_ref: prompts/judge/reasoning_quality_compliance.md
```

或临时内联：

```yaml
metrics:
  - accuracy
  - name: reasoning_quality
    prompt_inline: |
      你是合规题阅卷老师...
      评分细则：...
      严格输出 JSON: {"score":..,"reason":..,"thinking":..}
```

同样的覆盖机制对所有主观指标生效（`factuality_score` / `rubric_judge` / `custom_judge`）。RVEC 走领域包机制（[§7.5](#75-主观评测-rvec-pipeline)）。

---

## 6. 模型与 host

### 6.1 host_profiles.yaml（在 skill_commons，多 skill 共用）

```
<repo_root>/skill_commons/
  registry/
    host_profiles.yaml         ← 跨 skill 共享的 host 定义
    redaction_profiles.yaml    ← 脱敏规则集
  .env                          ← SKILL_COMMONS_ENV_FILE；API key 等机密
  hosts.py / lumi_client.py / env.py / redaction.py
```

```yaml
# skill_commons/registry/host_profiles.yaml
zerail:        {api_key: ${ZERAIL_API_KEY},  base_url: https://gateway.zerail.com/v1, timeout: 300}
iquest:        {api_key: ${IQUEST_API_KEY},  base_url: http://iqeust-litellm.../v1,    timeout: 300}
sft_zhuoguang: {api_key: EMPTY,               base_url: https://siflow-zhuoguang.../v1, timeout: 300}
local_qwen:    {api_key: ${LOCAL_KEY},       base_url: http://127.0.0.1:8081/v1,       timeout: 120}
```

`${VAR}` 占位运行时从 skill_commons/.env 加载；evaluator 通过
```python
from skill_commons import get_client
client = get_client("iquest")
```
拿到 OpenAI 客户端。**eval_skill 本身不再持有 host 注册表与 API key**。

### 6.2 yaml 中只引用 host 名

```yaml
model_under_test:
  host_profile: sft_zhuoguang
  model: sft-general-0509
  temperature: 0.1
  run_prefix: iquest_0509          # Langfuse trace 标签 / CSV 行标
  extra_body: {thinking: {type: disabled}}

baselines:
  - {host_profile: iquest, model: kimi-k2.6,         run_prefix: kimi-k2.6-0604}
  - {host_profile: iquest, model: claude-sonnet-4-6, run_prefix: claude-0604}
  - {host_profile: iquest, model: deepseek-v4-pro,   run_prefix: dsv4pro-0604}
  - {host_profile: iquest, model: glm-5.1,           run_prefix: glm51-0604}
```

### 6.3 横评执行

一份 yaml 同时跑 `model_under_test + baselines`，相同 dataset / prompt / metric / judge / 抽样。
CSV 每行带 `model + run_prefix`，summary.json 按 (model, round) 聚合。

---

## 7. 指标体系

### 7.1 统一返回结构

```python
@dataclass
class MetricResult:
    name: str
    value: float          # 0~1
    reason: str           # 为什么是这个分（必填）
    extra: dict = {}      # 子分、命中详情、judge_failed 等
```

`Metric.compute` 接口：

```python
class Metric:
    def compute(self, sample: Sample, output: RunOutput,
                judge: Optional[Judge] = None,
                is_baseline: bool = False,
                **_kwargs) -> MetricResult:
        ...
```

- `is_baseline=True` 表示当前评测的是 baseline 模型（不是 model_under_test）。Evaluator 自动从 [`ModelSpec.is_baseline`](core/config.py) 读。
- 不关心此参数的指标可忽略；RVEC 用它切换 mut/baseline 不同 caps。
- `**_kwargs` 保证向后兼容，后续扩展不需改现有指标。

CSV 列：`<metric>_value`、`<metric>_reason`，`extra` 中的基础类型子字段按 `<metric>__<key>` 自动展开（非基础类型如 list/dict 需 metric 实现里 `json.dumps` 后走字符串列）。

### 7.2 客观指标

| 名字 | 适用 | 算法 | 默认行为 / 可调参数 |
|---|---|---|---|
| `accuracy` | single_choice | 按 `extractor` 抽取选项后比对 | `extractor=cn_final_answer`（抽 `最终答案: X`），可改为 `en_the_answer` / `last_letter` / `regex` |
| `exact_match` | string | 严格相等 | `case_sensitive=false`、`normalizer=lower_strip` |
| `contains` | string | gt 是 pred 子串 | 同上 |
| `array_recall` | array | $\|gt \cap pred\| / \|gt\|$ | `splitter` 默认 `[,，;；\n、]`，`normalizer=lower_strip` |
| `array_f1` | array | F1(precision, recall) | 同 array_recall |
| `numeric_match` | number | $\|pred-gt\| \le \text{tol}$ | `tolerance=0.001`、`relative=false` |

所有指标 reason 必填，CSV 直接可 check（如 `expected=B actual=C` / `hit=[..] missed=[..]` / `pred=X gt=Y diff=Z`）。

### 7.3 主观指标

按复杂度分三类：

#### 7.3.1 单值打分（共用全局 judge，1 次 LLM 调用）

| 名字 | 用途 | judge prompt | 输出契约 |
|---|---|---|---|
| `reasoning_quality` | 推理过程对照 explanation/reasoning_ref | `prompts/judge/reasoning_quality.md` | `{score, reason, thinking}` |
| `factuality_score` | 事实一致性 | `factuality_score.md` | `{score, reason, thinking}` |
| `rubric_judge` | 按 expected.rubric 打分 | `rubric_judge.md` | `{score, reason, thinking}` |
| `custom_judge` | 用户在 yaml 里临时给 rubric | `custom_judge.md` | `{score, reason, thinking}` |

这四个都支持 `prompt_ref/inline` + `user_prompt_ref/inline` 覆盖（§5.4）。

#### 7.3.2 领域级 pipeline（按领域包跑 6 次 LLM 调用）

| 名字 | 用途 | 领域包 | 输出 |
|---|---|---|---|
| `rvec_judge` | RVEC 体系打标 + 0-4 分评分 | `prompts/judge/rvec_<domain>/` | `{final_score, worst_level, tag_coverage, bad_signals[], good_signals[], dcg_note, ...}` |

详见 [§7.5](#75-主观评测-rvec-pipeline)。

#### 7.3.3 复合主观指标（M3 阶段）

| 名字 | 用途 | judge prompt | 输出契约 |
|---|---|---|---|
| `report_quality` | PDF 研报对比基准研报 | `report_quality.md`（复用 [for_eval/report_generate.py](../for_eval/report_generate.py)） | `{factuality, recall, reasoning, structure, comprehensive, reason}` |
| `grounding_coverage` | 10-K 论点的源文支撑 + 覆盖率 | `grounding_coverage.md`（复用 [for_eval/analyse_report.py](../for_eval/analyse_report.py)） | `{grounding_score, coverage_score, overall_score, reason}` |

**Python 端重算**：像 `report_quality` 的 `comprehensive_score` 不信任 judge 自算，按权重 `0.4/0.3/0.2/0.1` 重算（沿用 [for_eval/report_generate.py#L504](../for_eval/report_generate.py) 的做法）。

### 7.4 yaml 中指定指标（完整字段）

两种写法等价：

```yaml
metrics:
  # 字符串简写：用所有默认参数
  - accuracy
  - reasoning_quality

  # 对象写法：可覆盖参数 / prompt / judge
  - name: numeric_match
    tolerance: 0.001
    relative: true              # |pred-gt|/|gt| <= tolerance

  # 同指标多变体共存（A/B 对比 prompt）→ 用 alias 区分列名
  - {name: reasoning_quality, alias: rq_default}
  - name: reasoning_quality
    alias: rq_compliance
    prompt_ref: prompts/judge/reasoning_quality_compliance.md

  # 客观指标的归一化与切分
  - name: array_recall
    alias: array_recall_strict
    case_sensitive: true
    normalizer: identity         # identity | lower_strip | chinese_punct
    splitter: '[\n]'             # 切分正则，默认 [,，;；\n、]

  # accuracy 抽取规则（应对中英文不同模板）
  - name: accuracy
    extractor: cn_final_answer   # cn_final_answer | en_the_answer | last_letter | regex
    # 或：
    # extractor: regex
    # extractor_regex: '答案[是为：:]?\s*\(?([A-Z])\)?'

  # 主观指标：换 system prompt + 自定义 user template + 临时换 judge
  - name: rubric_judge
    prompt_inline: |
      你是阅卷老师...
      输出 JSON: {"score":..,"reason":..,"thinking":..}
    user_prompt_inline: |
      【题目】{question}
      【rubric】{rubric}
      【模型回答】{answer}
    judge_override:
      host_profile: zerail
      model: gpt-5.3-chat
      temperature: 0.0

  # 领域级 pipeline——RVEC（详见 §7.5）
  - name: rvec_judge
    alias: general_rvec
    prompt_pack: prompts/judge/rvec_general    # 领域包目录，必填
    extra:
      caps:                                    # 可选；不写走 pack.yaml 默认
        bad_mut: 5
        bad_baseline: 4
        good: 3
        per_dim: {R: 2, V: 2, E: 1, C: 3}
      max_answer_chars: 8000                   # 可选，api answer 截断

  # 复合主观指标：子分权重（M3）
  - name: report_quality
    sub_weights: {factuality: 0.4, recall: 0.3, reasoning: 0.2, structure: 0.1}
```

**字段定义**：

| 字段 | 适用 | 说明 |
|---|---|---|
| `name` | 必填 | 指标名（见 [§7.2](#72-客观指标) / [§7.3](#73-主观指标)） |
| `alias` | 任意 | CSV 列名前缀；不填则用 `name`。**同 name 多变体共存时必填**。 |
| `weight` | 任意 | 综合分加权（可选） |
| **客观指标专属** ||  |
| `case_sensitive` | string / array | 默认 false |
| `normalizer` | string / array | `identity` / `lower_strip`（默认） / `chinese_punct` |
| `splitter` | array | 切分正则，默认 `[,，;；\n、]` |
| `tolerance` / `relative` | numeric_match | 数值容差（绝对/相对） |
| `extractor` | accuracy | `cn_final_answer`（默认） / `en_the_answer` / `last_letter` / `regex` |
| `extractor_regex` | accuracy | 当 `extractor=regex` 时必填，用第 1 个 group |
| **主观单值打分专属** ||  |
| `prompt_ref` | reasoning_quality / factuality_score / rubric_judge / custom_judge | 覆盖默认 judge **system** prompt 文件 |
| `prompt_inline` | 同上 | 内联 system prompt；优先级高于 `prompt_ref` |
| `user_prompt_ref` | 同上 | 覆盖 judge **user** prompt 模板文件 |
| `user_prompt_inline` | 同上 | 内联 user prompt 模板；优先级高于 `user_prompt_ref` |
| `judge_override` | 任意主观指标 | 为该指标单独指定 judge（不影响其它主观指标） |
| **RVEC pipeline 专属** ||  |
| `prompt_pack` | rvec_judge | 领域包目录，必填；指向包含 `pack.yaml` 与 6 个 step .md 的目录 |
| `extra.caps` | rvec_judge | 覆盖 `pack.yaml` 默认 caps（`bad_mut`/`bad_baseline`/`good`/`per_dim`） |
| `extra.max_answer_chars` | rvec_judge | answer 截断上限（默认 8000） |
| **复合主观指标** ||  |
| `sub_weights` | report_quality | 子分加权（Python 端重算 comprehensive） |

### 7.5 主观评测 RVEC pipeline

`rvec_judge` 是 RVEC v3.0 的完整实现，把"看 → 找 → 定"三步法落进 metric 框架。

#### 7.5.1 DAG（拓扑硬编码，不走 manifest）

```
┌──────────────────────────────────────────────────────────────────┐
│  STEP1「看」  step1_understand.md  → step1 JSON                   │
│  ↓ (输入：question + reference_block)                            │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ STEP2「找」 串行 4 次 LLM 调用                             │   │
│  │  ├─ step2_R.md  → R 维度 triggered_signals                │   │
│  │  ├─ step2_V.md  → V 维度 triggered_signals                │   │
│  │  ├─ step2_E.md  → E 维度 triggered_signals                │   │
│  │  └─ step2_C.md  → triggered_highlights                    │   │
│  │  共用输入：question + answer + step1 + reference_block     │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ↓                                                                │
│  AGGREGATE  Python，无 LLM；按 caps 裁剪 R/V/E 信号 + C 亮点      │
│  ↓                                                                │
│  STEP3「定」  step3_scoring.md  → final_score (0-4) + tag_coverage│
│  ↓                                                                │
│  归一化：score = final_score / 4 ∈ [0, 1]                         │
└──────────────────────────────────────────────────────────────────┘
```

每个 sample 共 6 次 LLM 调用。

#### 7.5.2 领域包（pack）

每个领域一个目录，**互不继承、独立维护**（避免长期维护成本）：

```
prompts/judge/rvec_<domain>/
├── pack.yaml               ← "会变的东西"集中地
├── step1_understand.md
├── step2_R.md
├── step2_V.md
├── step2_E.md
├── step2_C.md
└── step3_scoring.md
```

`pack.yaml` 结构（参见 [prompts/judge/rvec_general/pack.yaml](prompts/judge/rvec_general/pack.yaml)）：

```yaml
domain: general
version: v3.0
scoring_mode: llm                # 本期只支持 llm；写 rule 会报 NotImplementedError

caps:
  bad_total: {mut: 5, baseline: 4}
  good_total: 3
  per_dim: {R: 2, V: 2, E: 1, C: 3}

signals:                         # 60+ 个 R/V/E 信号
  - {tag_id: R-SAFE-1, name: 政治敏感与国家安全, dim: R1, levels: [P0]}
  - {tag_id: R-FACT-1, name: 事实/计算错误, dim: R3, levels: [P0, P1, P2]}
  - ...

highlights:                      # 12+ 个 C 亮点
  - {tag_id: C-R-01, name: 边界清晰, dim: C-R}
  - ...
```

step2 prompt 里的 `逐一检查以下信号` 段落由 `pack.yaml` 运行时**渲染注入**（占位符 `{signals_section}`），决议方案 A：**只渲染 tag_id + 中文名**，描述/案例只在 yaml 里给人看。

→ 增/删/改一个标签：**只动 pack.yaml**，prompt 文件不动。

#### 7.5.3 mut / baseline 不同 cap

`Metric.compute(..., is_baseline=True/False, ...)` 由 evaluator 自动设置：
- `model_under_test.is_baseline = False` → 用 `caps.bad_total.mut`
- 每个 `baselines[*].is_baseline = True` → 用 `caps.bad_total.baseline`

yaml 里可以覆盖：

```yaml
- name: rvec_judge
  prompt_pack: prompts/judge/rvec_general
  extra:
    caps:
      bad_mut: 6        # 自家更宽松
      bad_baseline: 4   # 竞品更严
```

#### 7.5.4 reference_block 自动组装

dataset 字段 → reference_block 文本由 `_rvec_helpers.format_reference_block()` 自动拼装：

| dataset 来源 | 拼成 |
|---|---|
| `expected_output.answer` | `- 参考答案：xxx` |
| `expected_output.reasoning_ref` | `- 参考推理思路：xxx` |
| `expected_output.rubric` | `- 评分准则：xxx` |
| 均无 | 空字符串 |

> 注：`expected_signals` 已从 dataset 规范中移除——预期信号是对模型行为的预测，不是题目的固有属性，存入 dataset 会污染 judge 独立性。

#### 7.5.5 fail-loud 失败处理

任意 step 解析失败：
1. 重试 1 次（temperature +0.1）
2. 仍失败 → 整个 sample 标记 `score=0.0`、`extra.judge_failed=true`、`extra.failed_step=step1/step2_R/...`、`extra.raw_at_failure` 带前 2000 字原始输出便于排查
3. **绝不静默给高分**

CSV 列 `<alias>__judge_failed` 与 `<alias>__failed_step` 直接可筛。

#### 7.5.6 CSV 输出列

除常规 `<alias>_value`/`<alias>_reason`，extra 中以下字段会自动展开成列：

| 列 | 类型 | 含义 |
|---|---|---|
| `<alias>__final_score_raw` | float | 0-4 原始分 |
| `<alias>__worst_level` | str | P0/P1/P2/无 |
| `<alias>__tag_coverage` | str | high/medium/low（vs 预期信号的覆盖度） |
| `<alias>__summary` | str | 一句话评测结论 |
| `<alias>__dcg_note` | str | 评分依据/去重/置信度 详情 |
| `<alias>__question_type` | str | 题型（来自 step1） |
| `<alias>__bad_signals_count` | int | 裁剪后的问题信号数 |
| `<alias>__good_signals_count` | int | 亮点数 |
| `<alias>__bad_signals_json` | str(JSON) | 完整 bad 列表 |
| `<alias>__good_signals_json` | str(JSON) | 完整 good 列表 |
| `<alias>__step1_json` | str(JSON) | step1 需求分析 |
| `<alias>__is_baseline` | bool | 是否 baseline 模型 |
| `<alias>__judge_failed` | bool | pipeline 是否失败 |
| `<alias>__failed_step` | str | 失败时填，否则空 |

#### 7.5.7 新建领域包

复制 `rvec_general/` 整个目录到 `rvec_<new_domain>/`，按需修改：
1. **pack.yaml**：增/删领域专属信号（如医疗加 💊 标签 R-FACT-7/8、R-REA-9 等）
2. **6 个 step .md**：举例改成本领域案例（如医疗换成血糖/血压案例）
3. yaml 里指 `prompt_pack: prompts/judge/rvec_<new_domain>` 即可

**不做继承机制** —— 当前已知的 RVEC 家族（通用/医疗/创作）数量有限，复制冗余比维护继承机制成本低。


## 8. ExperimentConfig（完整 yaml 字段）

```yaml
# ===== 元信息 =====
experiment_name: fin_compliance_0604_sft_vs_kimi    # 必填，唯一
tags: [finance, compliance, gradient-test]
description: "金融合规题，sft-0509 对比 kimi 与 claude"

# ===== 数据集 =====
# dataset.name 支持单名或名列表。列表时会拆成 N 份 ExperimentConfig，experiment_name
# 自动后缀 __<dataset_name>，方便一次 yaml 跑多个数据集。
dataset:
  name: Fin-Compliance        # 或 [Fin-Compliance, Fin-Investing, ...]
sampling:
  mode: n                      # full | n | ratio
  n: 50
  ratio: null
  seed: 42

# ===== Prompt =====
prompt_strategy:
  system_prompt_ref: prompts/system/finance_quant.txt
  user_template: |
    【背景】
    {background}

    【题目】
    {question}

    【选项】
    {options}
  per_turn_prefix: ""

# ===== PDF / 预处理（report_pair 才需要） =====
preprocess:
  pdf:
    mode: smart
    keywords: [Item 1, Item 1A, Item 7, Item 8, Risk Factors, MD&A]
    window: [-1, 3]
    max_chars: 28000
    redaction_profile: default          # 引用 skill_commons/registry/redaction_profiles.yaml
  ground_truth:
    mode: full
    max_pages: 50

# ===== 模型（host 引用 skill_commons/registry/host_profiles.yaml） =====
model_under_test:
  host_profile: sft_zhuoguang
  model: sft-general-0509
  temperature: 0.1
  run_prefix: iquest_0509
  extra_body: {thinking: {type: disabled}}

baselines:
  - {host_profile: iquest, model: kimi-k2.6,         run_prefix: kimi-k2.6-0604}
  - {host_profile: iquest, model: claude-sonnet-4-6, run_prefix: claude-0604}

# ===== Judge（主观指标默认共用） =====
judge:
  host_profile: iquest
  model: gemini-3.1-pro-preview
  temperature: 0.0

# ===== 指标 =====
metrics:
  - accuracy
  - reasoning_quality

  # 主观领域级评测示例（§7.5）
  - name: rvec_judge
    alias: general_rvec
    prompt_pack: prompts/judge/rvec_general

# ===== 执行 =====
execution:
  rounds: 2                    # 跑几轮（取均值/方差）
  concurrency: 8               # 注意 rvec_judge 内部 6 次 LLM调用，总 QPS 是 concurrency×6
  reporter: [csv, lumi]        # 至少一个
  resume: true                 # 按 (sample_id, model, run_prefix, round) diff 续跑
```

---

## 9. 目录结构

```
<repo_root>/
  skill_commons/                       ← 跨 skill 共用的基础设施
    __init__.py                        # 导出公共 API
    env.py                             # ensure_env_loaded / require_env / get_env
    hosts.py                           # HostProfile + load_host_profiles + get_client
    lumi_client.py                     # build_lumi_client
    redaction.py                       # load_redaction_profiles
    registry/
      host_profiles.yaml               # host 信息（key/url/timeout）
      redaction_profiles.yaml          # 脱敏规则集
    .env.example / .env / .gitignore
    README.md

  eval_skill/
    DESIGN.md                          ← 本文件
    SKILL.md                           ← Agent 使用说明（口语化需求 → yaml 流程）
    HERMES_ROUTING.md                  ← Hermes Agent 路由规则
    ONBOARDING.md                      ← 新建评测数据集操作指南
    cli.py                             ← `python -m eval_skill.cli run -c <yaml>`
    __init__.py

    core/
      config.py                        # ExperimentConfig、ModelSpec（含 is_baseline）、MetricSpec
      sample.py                        # Sample / GroundTruth
      sample_parser.py                 # raw item → Sample（识别 prompt-baked / 结构化 / turns / pdf_refs）
      dataset.py                       # Langfuse 拉取 + 抽样
      prompt_builder.py                # Sample × PromptStrategy → messages
      pdf_retriever.py                 # full / smart 抽取 + 脱敏 + 缓存
      runner.py                        # 流式调用 + reasoning_content + <think> 抽取
      judge.py                         # 单 LLM judge client + JSON 契约
      evaluator.py                     # 编排：sample × (mut+baselines) × rounds、传 is_baseline
      reporter.py                      # samples.csv + summary.json + Langfuse trace

    metrics/
      base.py                          # Metric 抽象类（compute 接收 is_baseline=False, **_kwargs）
      objective.py                     # accuracy / exact_match / contains / array_recall / array_f1 / numeric_match
      subjective.py                    # reasoning_quality / factuality_score / rubric_judge / custom_judge
      rvec.py                          # RVECJudge metric（DAG 硬编码 + 重试 + 归一化）
      _rvec_helpers.py                 # load_pack / render_signals_section / limit_signals
                                       # / format_reference_block / resolve_caps
      registry.py                      # 名字 → Metric 实例

    prompts/
      system/
        finance_quant.txt
        finance_compliance.txt
        finance_news.txt
        general.txt
        sec_10k_analyst.txt
      user/
        10k_compare.txt
      judge/
        # 单值主观指标
        reasoning_quality.md
        factuality_score.md
        rubric_judge.md
        custom_judge.md
        # 领域级 RVEC 包
        rvec_general/
          pack.yaml                    # 信号集 + caps + scoring_mode
          step1_understand.md
          step2_R.md / step2_V.md / step2_E.md / step2_C.md
          step3_scoring.md
        rvec_medical/                  # M3 阶段产出
          ...

    configs/
      examples/
        finance_compliance.yaml          # 单选题
        finance_horizontal_multi.yaml    # 多 dataset 横评
        reasoning_quality_variants.yaml  # 同名多变体 alias 示例
        general_rvec_demo.yaml           # RVEC 三步管线示例（领域=general）

    tools/
      push_dataset_to_lumi.py            # 本地 jsonl → Langfuse Dataset（v2 规范）
      validate_dataset.py                # 拉下 dataset 校验是否符合 v2
      view_dataset.py                    # 预览 dataset 统计信息 + 样本摘要 + dataset 链接
      migrate_legacy_dataset.py          # 老 dataset → v2 jsonl（按 §3.5 规则一次性迁移）

    outputs/
      .gitignore
      _cache/pdf/                        # PDFRetriever 缓存
      <experiment_name>/
        samples.csv
        summary.json
```

---

## 10. 数据流细节

### 10.1 单条样本生命周期

```
Langfuse item (v2)
   │
   ▼  SampleParser（模式识别 prompt/turns/structured + GroundTruth 构造）
Sample {input, ground_truth, metadata, raw}
   │
   ▼  PDFRetriever（如有 pdf_refs）
Sample.attachments_md += {label: markdown}
   │
   ▼  PromptBuilder（system + 前置 PDF + turns/prompt/template）
List[Message]
   │
   ▼  Runner（流式调用、reasoning_content/usage 抓取；多轮按 turn 链式调用）
RunOutput {turns: [{prompt, content, reasoning, usage, latency}], final_text, final_reasoning, error}
   │
   ▼  Metrics（每个指标接 Sample + RunOutput + Judge + is_baseline）
   │    ├─ 单值指标：1 次 LLM（或净规则）
   │    └─ RVEC pipeline：step1 → step2_R/V/E/C 串行 → step3 = 6 次 LLM
List[MetricResult{value, reason, extra}]
   │
   ▼  Reporter
   ├─ samples.csv 一行
   └─ Langfuse trace（trace.score per metric, item.link 关联）
```

### 10.2 多模型并发

每条 sample × 每个 model × 每 round = 一个并发任务。
相同模型内用 `ThreadPoolExecutor(max_workers=concurrency)`。
不同模型按顺序跑（避免对同一 host 加压过猛；横评结果可逐模型流式产出）。

⚠️ 包含 RVEC pipeline 指标时，单个样本会产生 6 次 judge LLM 调用，实际 judge 侧 QPS 近似
`concurrency × (1 + rvec_metric_count × 6)`。调 concurrency 时需要考虑 judge 侧限流。

### 10.3 断点续传

`resume: true` 时：
1. 实验目录已有 `samples.csv` → 加载已完成的 `(sample_id, model, run_prefix, round)` 集合。
2. 当前 sample 集 diff 已完成集 → 只跑差集。
不再使用旧脚本里的 `start_question` 数字下标（位移敏感、不稳定）。

---

## 11. Reporter 产出

### 11.1 samples.csv 列定义

```
experiment_name, tags, dataset, round, model, run_prefix, host_profile,
sample_id, ground_truth, prediction, reasoning, error,
tokens_input, tokens_output, latency_sec,
<alias_or_name>_value, <alias_or_name>_reason,        # 同 name 多变体时用 alias 区分
<alias_or_name>__<sub>_value                          # 子分（如 report_quality__factuality）
```

RVEC pipeline（§7.5）除产出上面公共列外，还会额外产出以下专属列（前缀 = alias）：

```
<alias>_final_score          # final_score ÷ 4 后的 0–1 归一化值
<alias>_judge_failed         # 任一中间步骤失败 → true（value=0.0 + judge_failed=true）
<alias>__R_score / V_score / E_score / C_score          # 四个维度原始分（0–4）
<alias>__R_signals / V_signals / E_signals / C_signals  # 交付集返回的 tag_id 串（";" 分隔）
<alias>__final_score_raw                                # 0–4 未归一化原始分
<alias>__caps_used                                      # mut | baseline（调用时的 cap 集）
```

### 11.2 summary.json

```json
{
  "experiment_name": "...",
  "tags": [...],
  "dataset": "Fin-Compliance",
  "total_rows": 300,
  "groups": [
    {
      "model": "sft-general-0509",
      "run_prefix": "iquest_0509",
      "round": 1,
      "n": 50,
      "errors": 0,
      "accuracy_mean": 0.78, "accuracy_std": 0.41,
      "reasoning_quality_mean": 0.72, "reasoning_quality_std": 0.18,
      "general_rvec_mean": 0.61, "general_rvec_std": 0.21,
      "general_rvec_judge_failed_rate": 0.04
    }
  ]
}
```

### 11.3 Langfuse trace

- `session_id = <experiment_name>_round<N>`
- `trace.tags = [*config.tags, model, dataset, run_prefix, "round_N"]`
- `trace.metadata = {tested_model, run_prefix, round, ...sample.metadata}`
- `trace.input`：多轮场景包含完整 `turns` 列表；单轮为 question/prompt
- `trace.output`：多轮场景输出完整 `conversation` 数组（role + content 交替）；单轮为 final_text
- 每条 turn 单独创建 `generation` span（`turn_0`, `turn_1`, ...），记录 prompt/completion/usage/latency
- 每个指标 `trace.score(name, value, comment=reason)`。RVEC 额外发送四维度子分（`<alias>__R/V/E/C`）
- `dataset_item.link(trace, session_id)` 关联回数据集

### 11.4 终端输出

执行结束后 `reporter.finalize()` 自动输出：

1. **结果摘要表**（Markdown 格式）：每行 = question(截断) / expected / actual / score / reason；末尾附 mean score
2. **Langfuse 实验链接**：`{LUMI_HOST}/datasets/{dataset_name}` 可直接跳转查看所有 trace

---

## 12. CLI

```bash
# 跑实验
python -m eval_skill.cli run -c eval_skill/configs/runs/<file>.yaml

# 临时覆盖采样
python -m eval_skill.cli run -c <file>.yaml --sample 20

# 列出已注册指标
python -m eval_skill.cli list-metrics

# 校验 dataset 是否符合 v2
python -m eval_skill.cli validate-dataset --name Fin-Compliance

# 一次性把本地 jsonl 推到 Langfuse Dataset
python -m eval_skill.tools.push_dataset_to_lumi \
    --jsonl data/fin_compliance_v2.jsonl \
    --name Fin-Compliance \
    --description "金融合规单选题 v2 规范"
```

---

## 13. Agent 使用流程（SKILL.md 摘要）

Agent 接到口语化需求后：

1. **识别 dataset 名**：跟用户对齐数据集名字（建议保持 Langfuse 上的命名规范，例如 `Fin-Compliance`、`FinNews-Anomalous-Emotion`）。
2. **识别模型 + 横评**：被测放 `model_under_test`，对照模型放 `baselines`，host 引用 [skill_commons/registry/host_profiles.yaml](../skill_commons/registry/host_profiles.yaml)，新 host 才动注册表。所有 `baselines.*.is_baseline` 会被 `_build()` 自动设为 True，供 RVEC caps 切换使用。
3. **识别指标**：参考 [§3.4](#34-schema--默认指标对照)；用户没说就按 schema 默认。
4. **识别采样**："全量" → `mode=full`；"抽 50 条" → `mode=n, n=50`。
5. **生成 yaml**：写到 [configs/runs/](configs/runs/)，命名 `<yyyymmdd>_<slug>.yaml`。
6. **执行**：`python -m eval_skill.cli run -c <path>`。
7. **回报产出**：
   - 终端结果摘要表（question / expected / actual / score / reason）
   - Langfuse 实验链接
   - 完整结果：[outputs/&lt;experiment_name&gt;/samples.csv](outputs/) 与 summary.json

---

## 14. 扩展指引

| 场景 | 改动点 |
|---|---|
| 加新领域（如 coding agent 评测） | 参见 [ONBOARDING.md](ONBOARDING.md)；在 prompts/system/ 加 system，在 metrics/ 按需加 metric；dataset 在 Langfuse 上建 |
| 加新 host | 仅改 [skill_commons/registry/host_profiles.yaml](../skill_commons/registry/host_profiles.yaml) |
| 加新脱敏规则 | 改 [skill_commons/registry/redaction_profiles.yaml](../skill_commons/registry/redaction_profiles.yaml) |
| 加新单值指标 | 继承 `metrics.base.Metric`，注册到 `metrics.registry`；如是 judge 类，加 `prompts/judge/<name>.md` |
| 加新主观 prompt | 改 `prompts/judge/<name>.md` 文本即可；已有指标无需改代码 |
| 加新 RVEC 领域包 | 复制 `prompts/judge/rvec_general/` 为 `prompts/judge/rvec_<domain>/`；改 pack.yaml 中信号与 caps；yaml 中用 `prompt_pack: prompts/judge/rvec_<domain>` 指向 |
| 单次实验调 prompt | yaml 里 metric 项加 `prompt_ref`/`user_prompt_ref`/`prompt_inline`，不动代码不动 dataset、不影响其它实验（[§5.4](#54-调-prompt-的工作流)） |
| 单次实验换 judge 模型 | yaml 里 metric 项加 `judge_override`；或全局换 yaml 顶层 `judge.model` |
| 加新数据格式 | 老格式都在 Langfuse 上；本地数据用 `tools/push_dataset_to_lumi.py` 推上去即可 |

---

## 15. 不做的事

- 不在 dataset 里塞 prompt / system / 模型信息。
- 不在 yaml 里写 api_key（统一在 host_profiles.yaml）。
- 不在评测路径里在线解析 PDF 后再缓存（PDFRetriever 自带缓存，但抽取仍在线进行；若未来 PDF 体积过大，再加 OSS 预处理流水线）。
- 不在指标里只打分不给 reason（违背可 check 原则）。
- 不复用 [for_eval/](../for_eval/) 下的旧脚本——那些是历史脚本，本 skill 取代它们。能力（PDF 智能抽取、judge prompt、stream usage、`<think>` 抽取、断点续传）都已迁移到 core/ 与 metrics/。

---

## 16. 路线图

| 阶段 | 状态 | 内容 |
|---|---|---|
| **M1** | ✅ 完成 | core/ + metrics/objective + metrics/subjective + cli + reporter + push_dataset_to_lumi。跑通 `single_choice` / `array` / `number` / `string` 四个 schema。 |
| **M2-A** | ✅ 完成 | RVEC 三步管线（§7.5）含 `rvec_general` 包、mut/baseline cap 自动切换、fail-loud、子维度列输出。 |
| **M2-B** | ✅ 完成 | 统一 Trace 上报：多轮 per-turn generation、完整对话 I/O、结果摘要表、Langfuse 实验链接。Dataset 预览优化（移除 expected_signals、展示 dataset 链接）。ONBOARDING.md。 |
| **M2-C** | 🔄 进行中 | `rvec_medical` 领域包 + step2 并行加速 + 人评样本迭代 prompt。 |
| **M3** | ⎙ 规划 | `dialog` schema（多轮 + per-turn）+ `report_pair` + PDFRetriever + report_quality + grounding_coverage。 |
| **M4** | ⎙ 规划 | Coding Agent 评测框架（场景设计、指标体系、数据集构建）。 |
| **M5** | ⎙ 规划 | dataset 校验工具、对比报表（baseline vs mut 胜率）、PDF OSS 拉取、通用评测扩展。 |

---

## 17. 风险与权衡

| 风险 | 缓解 |
|---|---|
| Langfuse 不稳定 / 接口变更 | DatasetLoader 单独抽象；本地 jsonl 通过 push 工具间接支持，Reporter 中 lumi 模块降级为只写 CSV |
| PDF 抽取截断丢失关键信息 | smart 模式关键词配置化；提供 full + max_pages 兜底；缓存可手动清除重抽 |
| judge 模型本身有误差 | 1) 多轮取均值 2) judge prompt 强制 JSON 契约 + Python 重算综合分 3) reason 字段必填便于人工抽查 |
| 老 dataset 迁移到 v2 | 提供 `tools/migrate_legacy_dataset.py` 一次性转换 + `validate-dataset` 校验；评测代码不留兼容层避免长期技术债 |
| prompt-baked 数据改 prompt 仍要改 dataset | 接受这是 prompt-baked 模式的固有代价；建议未来逐步迁出到结构化字段 |

---

## 18. 附：与 [for_eval/](../for_eval/) 旧脚本的能力对照

| 旧能力 | 在新方案的位置 |
|---|---|
| 多 host 缓存 client | [skill_commons/hosts.py](../skill_commons/hosts.py) |
| 脱敏规则加载 | [skill_commons/redaction.py](../skill_commons/redaction.py) + [skill_commons/registry/redaction_profiles.yaml](../skill_commons/registry/redaction_profiles.yaml) |
| Langfuse client 封装 | [skill_commons/lumi_client.py](../skill_commons/lumi_client.py) |
| 环境变量加载 | [skill_commons/env.py](../skill_commons/env.py) |
| 流式 + reasoning_content + usage 抓取 | [eval_skill/core/runner.py](core/runner.py) |
| `<think>` 标签抽取 | [eval_skill/core/runner.py](core/runner.py) |
| `最终答案：X` 抽取 | [eval_skill/metrics/objective.py](metrics/objective.py) `Accuracy._extract_choice` |
| ThreadPoolExecutor 并发 | [eval_skill/core/evaluator.py](core/evaluator.py) |
| `start_question` 断点续传 | [eval_skill/core/evaluator.py](core/evaluator.py) `resume`（按 (sample_id, model, run_prefix, round) diff，更稳） |
| `judge_reasoning` JSON 契约 | [eval_skill/core/judge.py](core/judge.py) + [prompts/judge/reasoning_quality.md](prompts/judge/reasoning_quality.md) |
| `judge_factuality` | [eval_skill/metrics/subjective.py](metrics/subjective.py) `factuality_score` + [prompts/judge/factuality_score.md](prompts/judge/factuality_score.md) |
| `extract_text_from_pdf_full` / `smart_extract_annual_report` / `extract_10k_core_sections` | 全部并入 [eval_skill/core/pdf_retriever.py](core/pdf_retriever.py) |
| `judge_generation_quality`（研报评分） | [eval_skill/metrics/subjective.py](metrics/subjective.py) `report_quality` + `prompts/judge/report_quality.md` + Python 端重算 comprehensive（M3） |
| `judge_10k_insights`（grounding/coverage） | [eval_skill/metrics/subjective.py](metrics/subjective.py) `grounding_coverage` + `prompts/judge/grounding_coverage.md`（M3） |
| **主观领域评测三步管线（[for_eval/zhuguan_prompt.py](../for_eval/zhuguan_prompt.py) 与 [for_eval/medical_prompt.py](../for_eval/medical_prompt.py)）** | [eval_skill/metrics/rvec.py](metrics/rvec.py) `RVECJudge` + [prompts/judge/rvec_general/](prompts/judge/rvec_general/) + [eval_skill/metrics/_rvec_helpers.py](metrics/_rvec_helpers.py)（§7.5） |
| **R/V/E/C 信号集 + caps + scoring_mode** | [prompts/judge/rvec_general/pack.yaml](prompts/judge/rvec_general/pack.yaml) |
| **mut vs baseline cap 切换** | [eval_skill/core/config.py](core/config.py) `ModelSpec.is_baseline` + [eval_skill/metrics/_rvec_helpers.py](metrics/_rvec_helpers.py) `resolve_caps` |
| Langfuse trace + score + item.link | [eval_skill/core/reporter.py](core/reporter.py) |
| `MODELS_CONFIG` + `INPUT_PAIRS` + 命令行参数 | yaml 中 `model_under_test` / `baselines` + `dataset` + cli flags |
