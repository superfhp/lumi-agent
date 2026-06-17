---
name: eval-dataset-tagging
description: 对评测集数据做 LLM 辅助打标/打分/标注。支持两阶段：(1) 上传原始评测集进行问题分类标注并入库 Lumi；(2) 基于已有评测集和模型初次评测结果，按 RVEC 规则对模型效果不佳条目进行打标分析，找出模型能力欠缺部分。
version: 4.0.0
metadata:
  hermes:
    category: evaluation
    tags: [eval, labeling, tagging, scoring, 打标, 打分, 评测, 标注, rvec, finance, medical, scene, error-attribution, dataset-ingest, 入库]
---

# 评测集智能打标（双阶段工作流）

## 两阶段设计

| 阶段 | 模式 | 触发条件 | 输入 | 输出 |
|------|------|----------|------|------|
| **① 评测集入库** | `dataset_ingest` | 用户上传原始题库（仅 question/reference，无模型回答） | CSV/XLSX 评测题 | 分类后上传 Lumi Dataset |
| **② RVEC 打标** | `fin_rvec` / `med_rvec` | 用户有评测结果（含 model_response + accuracy） | eval_skill 输出的 samples.csv | RVEC 标签 + P 等级 + 评分 |

### 阶段 ① 评测集入库（dataset_ingest）

适用：用户刚拿到评测题库，需要先分类组织后再跑评测

```bash
python3 run_tagging.py --mode dataset_ingest \
  --input medical_exam_raw.csv \
  --lumi-create-dataset "Medical-Exam-V2" \
  --auto-detect --workers 5
```

自动完成：
1. 领域检测（金融/医学/通用）→ 选择对应 scene_schema
2. LLM 对每题标注：场景（M/F轴）+ 任务类型（T轴）+ 难度 + 是否多选 + 关键词
3. 结构化上传 Lumi Dataset（metadata 含完整分类信息）
4. 本地输出分类结果文件

### 阶段 ② RVEC 打标（fin_rvec / med_rvec）

适用：eval_skill 跑完模型评测后，对模型回答做深度问题分析

```bash
python3 run_tagging.py --mode med_rvec \
  --input medical_exam_samples.csv \
  --auto-detect --workers 5
```

自动完成：
1. 字段映射（兼容 eval_skill 输出格式：prediction/ground_truth/accuracy_value）
2. LLM 打 RVEC 全标签（场景+任务+R/V/E/C+P等级+评分+证据+理由）
3. 评分规则自动校验（P0→0分，P1→1分）
4. 输出打标结果文件（可选回写 Lumi）

## 适用场景

- 用户说：帮我对这份数据做打标/标注/评测/分类/打分
- 用户上传了 xlsx/csv/jsonl 评测数据，希望得到带标签的结果文件
- 用户需要对模型输出做金融 RVEC 综合评测、场景分类、或错误归因
- 用户想**改 prompt 或 RVEC 评分规则后看效果**（A/B 对比 / 单条 explain 调试）
- 用户想**改完配置文件后先验证合法性**（`--validate-config`）

## 🟢 首轮响应协议（v3.6 简洁化，按完整性状态分档）

**取数方式**：先跑 `--inspect`，从输出 JSON 读 `completeness_status` 字段（v3.6 新增），按下表三档自适应回复，**不要再机械套 4 段模板**。

### 档位 1：`completeness_status = "FULLY_READY"` → 极简（3 行）

```text
✅ 已识别：{filename} | {N} 行 | 字段完整 | 题型族 {top1_family}:{n}, {top2}:{m}
� 计划：直接 --auto 全量打标，预计 {耗时}，输出 → {output_path}
💬 回复 ✅ 开始 / 🔍 先看 prompt / 📊 先抽 20 条预览
```

### 档位 2：`completeness_status = "READY_WITH_GAPS"` → 中等（5-6 行）

```text
✅ 已识别：{filename} | {N} 行 | ⚠️ 字段有缺口但不阻断打标
   缺失：{missing_optional 前 3 项，逗号分隔}
   影响：参考答案缺 → 评分依赖 LLM 自判；推理缺 → reason 略简略
� 计划：建议先 --preview 3 看效果（缺口下 prompt 仍然能跑），确认后 --auto
💬 回复 ✅ 直接全量 / 🔍 先 preview / 🔧 想补字段
```

### 档位 3：`completeness_status = "BLOCKED"` → 必须停（指明阻断原因）

```text
❌ 已识别：{filename} | {N} 行 | 缺必需字段，无法打标
   缺失：{missing_required，例如 "模型回答"}
🔧 解决路径：
   - 选项 A：检查列名是否非标（脚本识别表见下），改列名后重新上传
   - 选项 B：告诉我哪一列是模型回答，我用 --field-map 指定
💬 是否继续？
```

> **关键差异（vs v3.5）**：
>
> 1. **不再无脑出 4 段**：字段完整就给 3 行，节省用户阅读成本
> 2. **完整性状态显眼**：`completeness_label` 字段已经把 ✅/⚠️/❌ 拼好了，**直接复用**
> 3. **缺口透明**：用户清楚知道缺什么、影响什么、怎么补
> 4. **不强制询问**：完整字段场景默认直接给推荐计划，不问"先看 prompt 吗"

## 核心原则

- **首轮简洁优先**：字段完整就给 3 行，**不要堆砌 4 段固定模板**。
- **完整性状态显眼**：从 inspect 的 `completeness_status` / `completeness_label` 直接读，配 ✅/⚠️/❌ 图标。
- **大数据默认抽样**：>100 条数据未明确要求全量时，**默认建议先抽 20-50 条**。
- **交付前必校验**：跑完检查文件存在、大小>0，然后才能给用户链接。
- **结果必带样例**：跑完展示前 3 条具体打标示例 + **质量信号**（让用户判断好坏）。
- **只做一条命令**：收集参数 → 拼命令 → 执行 → 解析 manifest → 交付文件。
- **不做预检查**：不要检查权限、目录、环境、也不要安装依赖（环境已就绪）。
- **输出跟随输入**：输入 xlsx 则输出 xlsx，输入 csv 则输出 csv。
- **规则按需展示**：`--show-rules` / `--show-prompt` 仅在用户**明确要求**时跑，不要主动堆砌规则说明。
- **预览即洞察**：preview / explain / snapshot-only 输出**完整输入快照** + 标签结果，用户**无需回 excel** 比对。
- **Prompt 用户主导**：所有 prompt 都在 `config/rvec/prompts.yaml`，用户改文件即生效。

## 5 步工作流（每步可独立跑，每步都有清晰输出）

| 步骤 | 命令 | 用途 | Agent 必做动作 |
|---|---|---|---|
| ① 探数据 | `--inspect` | 看列名、字段映射、领域、推荐模式（不调 LLM） | **跑完按首轮响应协议出 4 段** |
| ② 看规则 | `--show-rules [--section X] [--family Y]` | 看 RVEC 标签体系/评分规则/family 路由（不调 LLM） | 仅在用户明确要求时跑 |
| ③ 看 prompt | `--show-prompt --family X [--with-sample data.csv]` | 看 system + family 模板，可拼真实样本（不调 LLM） | 仅在用户明确要求时跑 |
| ④ 试跑预览 | `--preview 3` / `--explain` / `--snapshot-only` / `--before A --after B` | 多档预览 | **>100 条数据强烈推荐** |
| ⑤ 全量打标 | `--auto` | 一条命令完成探查→打标→输出→manifest | **跑完必校验文件 + 展示 3 条样例** |

## 必填参数（缺少时询问用户）

- `input`：数据文件路径（xlsx/csv/jsonl） — **打标/预览/explain/inspect 必填；--show-rules / --show-prompt / --validate-config 不需要**

## 双入口选择

| 入口 | 适用 | 特点 |
|---|---|---|
| **`fin_rvec_tag.py`**（推荐） | 金融 RVEC 打标（**服务器跑批主力**） | 自包含单文件、纯标准库 + openpyxl、含 family 路由 + 后处理校验、支持完整 5 步工作流 |
| **`run_tagging.py`** | 通用打标（场景细分 / 错误归因 / 智能判别）| 多模式路由、依赖 pandas+openai SDK、支持 Lumi 双向打通、`--validate-config` 配置校验 |

**Agent 选择策略**：

1. 用户数据**含金融关键词 + 含模型回答** → 优先用 `fin_rvec_tag.py --auto`
2. 用户数据**仅有 question 无 answer** → 用 `run_tagging.py --mode scene_labeling --config X`
3. 用户数据**含 answer 但非金融域** → 用 `run_tagging.py --mode error_attribution --config X`
4. 用户**不确定走哪条**：先 `fin_rvec_tag.py --inspect --input X`，看 `recommended_mode` 字段，再二选一

## 字段映射回显（仅在用户明确询问时展示）

**v3.6 关键变更**：首轮响应已经按 `completeness_status` 自适应给出概览，**不再强制把所有字段映射列全**。

只有在以下场景才需要展示完整字段映射：

1. 用户明确问「字段都怎么映射的」「是不是看错列了」
2. `completeness_status = "BLOCKED"`（必需字段缺失，必须告诉用户具体哪列对哪个角色）
3. `completeness_status = "READY_WITH_GAPS"` 且用户问「缺什么会影响什么」

完整模板（仅在上述场景展示）：

```text
🔍 字段映射详情（来自 inspect.field_confirmation）

主字段（直接进入 prompt）：
  题目     ← question                  | 模型回答 ← model_response
  参考答案 ← ground_truth_structured   | 推理过程 ← model_reasoning（或 ❌ 未识别）

辅助线索（提示 LLM 但不覆盖最终标签）：
  原始评分 ← Accuracy | judge 评注 ← judge_comment | 题型族 ← norm_task_from_filename | 截断 ← model_response_truncated

ID：answer_id（无 → 用行号兜底）

⚠️  缺口影响（如有）：
  - 推理过程缺：R-REA-* 类标签缺二级证据，reason 略简略
  - norm_task 缺：全部归 GENERIC family，建议补该列以走专属 prompt
```

> **关键原则（v3.6）**：默认不秀完整映射，**仅在状态异常或用户明确问起时**给。

## 抽样默认决策表

`--inspect` 输出的 `sample_advice.recommend` 给出明确建议：

| 数据量 | recommend 字段 | Agent 推荐策略 |
|---|---|---|
| ≤ 20 条 | `all` | 直接 `--auto` 全量 |
| 21-100 条 | `all_or_preview` | 默认全量；若用户对 prompt 不放心 → 先 `--preview 3` |
| > 100 条 | `sample_first` | **强烈推荐先抽样** `--sample-size 20-50 --auto`，跑完展示样例 + 询问"是否继续全量" |
| > 500 条 | `sample_first` | 同上，但抽样后**强制**展示 5 条样例 + 评分分布图 |

**抽样跑批后的 Agent 必做动作**：

```text
🎯 抽样 {N} 条已完成（占总量 {ratio}%）
📊 评分分布：4分:{}条 | 3分:{}条 | 2分:{}条 | 1分:{}条 | 0分:{}条
🏷  TOP3 问题标签：R-FACT-1: {}次 | R-REA-2: {}次 | V-INFO-3: {}次
📝 样例预览（详见下方 3 条 + 整批质量速览）

❓ 标注口径是否符合预期？
   ✅ 「确认 → 继续全量」 → 我跑 --auto 不带 sample-size
   🔧 「调一下 prompt」 → 我帮你 --show-prompt --family X，改完再跑
   ❌ 「重抽一批」 → 我换种子重跑 --sample-size N --seed M
```

## 命令模板（一步执行，不要拆成多步）

```bash
# 默认：金融 RVEC 综合打标（自包含脚本，服务器主力）
python3 /root/.hermes/skills/eval-dataset-tagging/scripts/fin_rvec_tag.py \
  --input {input_file_path} \
  --output /root/.hermes/skills/eval-dataset-tagging/output/ \
  --auto \
  --workers 2

# 通用入口（智能判别 / 场景细分 / 错误归因 / 配置校验）
python3 /root/.hermes/skills/eval-dataset-tagging/scripts/run_tagging.py \
  --input {input_file_path} \
  --output /root/.hermes/skills/eval-dataset-tagging/output/ \
  --mode auto \
  --workers 2 --no-lumi
```

### 工作流命令（按需追加）

```bash
# ② 看规则
python3 fin_rvec_tag.py --show-rules                                  # 全部规则
python3 fin_rvec_tag.py --show-rules --section scoring                # 只看评分规则
python3 fin_rvec_tag.py --show-rules --section families               # 只看 family 路由
python3 fin_rvec_tag.py --show-rules --section rvec                   # 只看 RVEC 标签体系
python3 fin_rvec_tag.py --show-rules --section ft_cross               # F×T 交叉校验
python3 fin_rvec_tag.py --show-rules --section families --family QA_CHOICE  # 只看 QA_CHOICE 题型族

# ③ 看 prompt
python3 fin_rvec_tag.py --show-prompt --family QA_CHOICE                          # 看模板
python3 fin_rvec_tag.py --show-prompt --family REPORT_EVAL --with-sample data.csv # 拼真实样本

# ④ 多档预览
# 4a. 仅看输入快照（不调 LLM，最便宜，验证字段映射对不对）
python3 fin_rvec_tag.py --input data.csv --snapshot-only

# 4b. 普通预览：每条样本「输入快照 + 打标结果」并列展示，用户无需回 excel
python3 fin_rvec_tag.py --input data.csv --preview 3

# 4c. EXPLAIN 单条深度调试：8 段过程（输入→字段→family→user prompt→LLM 原始返回→F×T→后处理→最终）
python3 fin_rvec_tag.py --input data.csv --explain                                  # 默认调试第 1 条
python3 fin_rvec_tag.py --input data.csv --explain --pick "5"                       # 调试第 5 条
python3 fin_rvec_tag.py --input data.csv --explain --pick "id=cfa-001"              # 按 ID 调试
python3 fin_rvec_tag.py --input data.csv --explain --filter-family REPORT_EVAL      # 调试某 family 第 1 条

# 4d. A/B 对比：改 prompt 前后对照
python3 fin_rvec_tag.py --input data.csv --preview 3 \
    --before config/fin_rvec_config_v1.yaml \
    --after  config/fin_rvec_config_v2.yaml

# 4e. 挑选样本（行号/区间/ID 混用）
python3 fin_rvec_tag.py --input data.csv --preview --pick "1,3,5"          # 第 1/3/5 条
python3 fin_rvec_tag.py --input data.csv --preview --pick "1-10"           # 第 1-10 条
python3 fin_rvec_tag.py --input data.csv --preview --pick "id=ABC,DEF"     # 按 ID
python3 fin_rvec_tag.py --input data.csv --preview --pick "1-3;id=XYZ"     # 混用
```

### v4 Phase 2 新增：智能判别 + 配置校验

```bash
# ① 智能判别（推荐 Agent 拿不准模式时先跑这条）
python3 run_tagging.py --inspect --input data.csv --no-lumi
# 输出 JSON 含：
#   field_confirmation     → patch② 用，按角色分类的字段映射
#   sample_advice          → patch③ 用，抽样决策表
#   ready_for_labeling     → patch① 用，是否可直接打标
#   norm_task_family_dist  → 题型族分布
#   recommended_mode + recommend_reason + actionable_hints

# ⓥ 配置校验（改完 yaml/json 后必跑，不调 LLM 不读数据）
python3 run_tagging.py --validate-config config/fin_rvec_config.yaml
python3 run_tagging.py --validate-config config/rvec/prompts.yaml config/rvec/rules.yaml  # 可批量
python3 run_tagging.py --validate-config config/fin_rvec_config.yaml --strict             # 有 warning 也报错
```

### 可选参数（按需追加）

- `--endpoint iquest` 或 `--endpoint zerail`：指定 LLM 端点（默认自动探测）
- `--workers N`：并发数，默认 2（避免限流）
- `--sample-size N`：随机抽样 N 条
- `--filter-family QA_CHOICE|SENTIMENT|REPORT_EVAL|LONG_GEN|GENERIC`：按题型族筛选记录
- `--pick "1,3-5;id=ABC"`：挑选样本（行号/区间/ID 混用）
- `--explain`：单条深度调试（输出 8 段完整过程）
- `--snapshot-only`：仅打印输入快照不调 LLM（最便宜的核对手段）
- `--output-format xlsx|csv|jsonl`：强制输出格式
- `--config /path/to/config.yaml`：指定自定义配置文件
- `--temperature 0.0`：LLM 温度（默认 0）

## 配置文件分层（用户可改）

```text
config/
├── fin_rvec_config.yaml/.json     ← 兜底总配置（向后兼容，服务器跑批必需）
└── rvec/                          ← 【用户主要改这里】各资产分文件
    ├── prompts.yaml               ← system + 5 套 family prompt（用户主导改这个）
    ├── families.yaml              ← 题型族路由（match_norm_task / focus_labels / extra_rules）
    └── rules.yaml                 ← F/T schema + RVEC 标签体系 + scoring_rules + ft_cross_validation
```

**加载策略**：

1. 不指定 `--config` 时：先读 `fin_rvec_config.json/yaml` 作 base，再用 `rvec/*.yaml` 覆盖对应字段
2. 用户显式 `--config xxx` 时：直接用该文件，**不再 merge rvec/**
3. `rvec/*.yaml` 任一文件缺失：自动回退到 base config 的对应字段
4. `prompts.yaml` 未加载到时：回退到脚本内置常量

## 预览输入快照（snapshot-only / preview / explain 共用）

| 字段 | 含义 |
|---|---|
| 🎬 题型族 | family 自动路由结果 |
| ❓ 题目 | question |
| 📄 上下文 | context |
| ✅ 参考答案 | ground_truth_structured |
| 📎 非结构化参考 | ground_truth_unstructured |
| 🤖 模型回答 | model_response |
| 🅰  模型选项 | model_choice |
| 🧠 模型推理 | model_reasoning |
| 📊 原始 Accuracy | 原始评分 |
| 🎯 期望选项 | judge_comment.expected_choice |
| 💡 judge 线索 | judge_comment 关键字段摘要 |
| ⚠️ 截断 | model_response_truncated=yes 时显示原始长度 |
| 📈 多维评分 | factuality/recall/reasoning/structure/comprehensive |
| 🏷  norm_task | norm_task_from_filename |
| 🆔 ID | answer_id / id |

## 执行后（v3.6 加强：让用户看得懂好坏）

跑完后 manifest 会包含以下字段：

```json
{
  "status": "completed",
  "output_file": "output/xxx_rvec_labeled.xlsx",
  "output_file_exists": true,           // ← 必须 true 才能交付
  "output_file_size_bytes": 123456,
  "output_file_size_human": "120.6KB",
  "delivery_ready": true,                // ← 综合判断（exists && size>0）
  "sample_preview": [                    // ← v3.6 加强：前 3 条带对照面 + 质量信号
    {
      "id": "fin-001",
      "family": "QA_CHOICE",
      "question": "请问融资融券维持担保比例低于多少强平？",
      "model_response": "150%",
      "reference": "130%",                                    // ← v3.6 新增：参考答案对照
      "accuracy_origin": 0,                                   // ← v3.6 新增：原始评分对照
      "label_fin_scene": "F04 资本市场",
      "label_rve_primary": "R-FACT-1 事实错误",
      "label_rve_all": "R-FACT-1 事实错误",                  // ← v3.6 新增：所有 RVE 标签
      "label_severity": "P1",
      "label_score": 1,
      "label_evidence": "把 130% 误说为 150%",                // ← v3.6 新增：LLM 摘录证据
      "label_highlights": "",
      "label_reason": "把维持担保比例 130% 误说为 150%...",
      "quality_signals": {                                    // ← v3.6 新增：自动质量信号
        "score_align": "✅ 一致",                            // 原始评分 vs LLM 评分
        "has_evidence": true,
        "has_reason": true,
        "severity_match": "✅"
      }
    }
  ],
  "quality_summary": {                                        // ← v3.6 新增：整批速览
    "preview_n": 3,
    "score_alignment_rate": "3/3",
    "evidence_coverage": "3/3",
    "reason_coverage": "3/3",
    "severity_score_match": "3/3"
  },
  "avg_score": 2.3,
  "score_distribution": {"0": 5, "1": 12, "3": 30}
}
```

**Agent 必做的 4 步交付校验**：

1. 解析 stdout 中 `__MANIFEST_START__ ... __MANIFEST_END__` 之间的 JSON
2. **检查 `delivery_ready` 必须为 `true`**。若 `false` → **不要给用户下载链接**，直接报错并展示 manifest.status
3. **必须用 v3.6 增强模板展示 `sample_preview` 中前 3 条**（见下方「sample_preview 展示模板 v3.6」）
4. 用 `file-delivery` skill 交付下载链接（如有该 skill）。**若 file-delivery 不可用**：直接告诉用户**输出文件的绝对路径**，并提示"可以让我转换格式或重新生成"。

### sample_preview 展示模板 v3.6（强制，让用户能判断标得对不对）

**关键变化（vs v3.5）**：必须把"输入对照面"和"质量信号"一起展示，**用户不必猜 LLM 标得对不对**。

```text
📋 打标样例预览（3/N） · 整批质量速览：评分一致 3/3 ✅ | 有证据 3/3 ✅ | 严重度匹配 3/3 ✅
═══════════════════════════════════════════════════════════════
【样例 1】 fin-001 · QA_CHOICE
  ❓ 题目: 请问融资融券维持担保比例低于多少强平？
  📚 参考: 130%
  🤖 模型: 150%                                  （原始 Accuracy=0 = 答错）
  ─────────────────────────────────────────
  🏷  场景: F04 资本市场 · 任务: T03 知识问答
  🚨 主问题: R-FACT-1 事实错误 · P1 · 1分
  🔖 全部标签: R-FACT-1 事实错误
  💡 证据摘录: 把 130% 误说为 150%
  💬 理由: 把维持担保比例 130% 误说为 150%，属于事实错误
  ✓ 质量信号: 评分一致 ✅ | 有证据 ✅ | 严重度匹配 ✅
═══════════════════════════════════════════════════════════════
【样例 2】 ...
═══════════════════════════════════════════════════════════════
```

**展示要点**（优先级从高到低）：

1. **顶部一行整批速览**（来自 `quality_summary`）：评分一致率 / 证据覆盖率 / 严重度匹配率
2. **题目 + 参考 + 模型回答 + 原始 Accuracy 4 行并列**：用户一眼能判断模型答得对不对
3. **场景 / 任务 / 主问题三件套**：让用户知道打了什么标
4. **证据摘录 + 理由各 1 行**：证据 = 模型说错的关键句；理由 = 标这个 RVE 的依据
5. **质量信号 1 行兜底**：3 个 ✅ 表示这条标得稳；任何 ⚠️ 都标记出异常以便用户判断

**遇到 `quality_signals` 异常时（任一字段非 ✅）**：在样例下加 1 行黄字 `⚠️  注意：原始 Accuracy=1 但 LLM 打 1 分，建议人工复核`，**不要默认这条没问题**。

### 展示统计摘要（必给）

```text
📊 打标统计
   ✅ 成功: {success} 条 | ❌ 失败: {failed} 条 | 📌 已存在标注: {already_labeled} 条
   📈 平均分: {avg_score} | 分数分布: 4分:{} 3分:{} 2分:{} 1分:{} 0分:{}
   🏷  TOP3 问题标签: ...
   📄 输出文件: {output_file} ({output_file_size_human})
   📄 失败文件: {failed_file}（仅当 failed > 0）
```

**失败处理**：只读 stdout 的错误信息，给出可行动的原因说明。

## 字段自动映射

脚本自动识别以下列名（按优先级匹配新 schema 优先）：

| 角色 | 识别的列名 |
|------|-----------|
| 题目 | question, 题目, prompt, query, 问题 |
| 上下文 | context, 输入, input, full_context, 题干 |
| 模型回答 | model_response, 实际回答, output, answer, response |
| 选择题答案 | model_choice |
| 参考答案 | ground_truth_structured, 参考答案, expected_output, reference, gold |
| 非结构化参考 | ground_truth_unstructured（report 池 PDF 名） |
| 推理过程 | model_reasoning, 推理过程, reasoning, trace_output |
| 原始评分 | Accuracy, accuracy, score, 评分 |
| 自动评测线索 | judge_comment（JSON：含 expected_choice/factual_check/recall_check 等） |
| 截断标记 | model_response_truncated, model_response_full_length |
| 多维评分 | factuality_score / recall_score / reasoning_score / structure_score / comprehensive_score |
| 题型族识别 | norm_task_from_filename |

## 题型族（自动路由，规则在 `config/rvec/families.yaml`）

| Family | 匹配任务 | 重点关注标签 |
|---|---|---|
| **QA_CHOICE** | Fin-Compliance / Economics / Investing / Literacy / Quantitatics | `R-REA-*` / `R-UND-*` / `R-FACT-2` / `V-INFO-*`；考试题强制不用 `R-FACT-1` 当 primary |
| **SENTIMENT** | FinNews-Sentiment / Anomalous-Emotion | `R-UND-1/2` / `V-INFO-1` / `E-CONS-*` |
| **REPORT_EVAL** | Eval_FullReport / NewsReport / ResearchReport | `R-FACT-7` / `R-FACT-3` / `R-CONS-*` / `E-STR-*`；截断时不判 `R-FACT-3` |
| **LONG_GEN** | 10K_Analysis / Report_Analysis | `R-FACT-8` / `V-INFO-*` / `C-V-*` / `C-R-*` |

## 金融 RVE 输出字段

| 字段 | 含义 |
|------|------|
| `label_fin_scene` | 金融业务场景（F01-F12/F99），多选用「；」分隔，首个为 primary |
| `label_task_type` | 任务类型（T00-T16/T99），T16 必搭配实际任务标签 |
| `label_rve_primary` | 最主要的 RVE 负向标签（仅 R/V/E 三轴，不含 C） |
| `label_rve_all` | 全部 RVE 负向标签，**顺序与 primary 对齐** |
| `label_rve_score_all` | 各负向信号独立分值（0~1） |
| `label_severity` | 最严重 P 等级（P0/P1/P2/NONE） |
| `label_score` | 综合评分（0-4 分） |
| `label_highlights` | 正向亮点标签（仅 C 系列） |
| `label_evidence` | 问题摘录 |
| `label_reason` | 判定理由 |
| `labeler` | 自动填 `fin_rvec_tag@<模型名>` / `scene_tag@<模型名>` / `error_tag@<模型名>` |
| `review_status` | 自动填 `pending` |

## 自动后处理保障

脚本对 LLM 输出做强制后处理（避免 LLM 不听 prompt）：

1. **答对的题（Accuracy=1）自动剔除所有 R-FACT-* 标签**
2. **考试题（T16）若 primary=R-FACT-1，自动改为过程标签**
3. **`label_rve_all` 顺序自动重排**，保证 primary 在第一位
4. **`label_rve_score_all` 顺序与 rve_all 同步 + 缺失分值自动按 P 等级派生**
5. **`label_severity` 由脚本派生覆盖**（P0 > P1 > P2）
6. **`label_score` 按 scoring_rules 兜底校验**
7. **CSV/XLSX 列收集所有记录的 keys**

> 用 `--explain` 可看到「LLM 原始输出 → 后处理改了什么 → 最终标签」三层完整 diff。

## v4 Phase 2 多模式打标对齐

`scene_labeler.py` / `error_labeler.py` 已统一通过 `field_mapping.map_field` 抽取字段，**完全对齐新 schema**。同一份评测数据可在三种模式间无缝切换（不需要改字段名），只需改 `--mode` 即可。输出固定补齐 `labeler` + `review_status` 两个字段。

## 常见坑（Pitfalls）

- **医学 med_rvec 运行环境踩坑**：若系统 `python3` 缺少 `openai` 依赖，优先改用项目 venv 里的 Python 运行（如仓库 `venv/bin/python`）；不要先假设系统 Python 可直接跑通。在本环境里，`/mnt/workspace/lumi-agent/venv/bin/python` 已验证可运行 `run_tagging.py`。
- **医学评测结果混入失败样本**：来自 `eval_skill` 的医疗 `samples.csv` 可能同时包含成功回答与模型调用失败记录。做医学打标前，先检查 `prediction`、`error`、以及评分列是否存在；若目标是分析“有效评测结果”，默认仅保留 `prediction` 非空且 `error` 为空的记录，必要时先生成一个 `*_effective.csv` 作为 med_rvec 输入。
- **大样本 med_rvec 运行形态**：500+ 条全量样本直接前台跑，容易超出工具超时时间。对这类任务，应先用前几十条验证端点与速率，再改用后台进程运行并轮询状态；同时保留一个可直接交付的中间产物（如过滤后的 effective CSV 或样例精标 CSV），避免用户等待期间“零交付”。
- **全量打标与样例精标分层交付**：若用户要求“尽快先看结果”，可同时交付两层结果——(1) 已实际 LLM 精标的小样本 CSV/样例；(2) 基于全量评测结果补齐标签字段的增强版 CSV/HTML，用于浏览和决策。回复时必须显式说明哪部分是“逐条实际打标”，哪部分是“基于现有结果生成的增强版/衍生版”，避免口径混淆。
- **首轮过度啰嗦**：v3.6 起按 `completeness_status` 三档自适应，**字段完整就只给 3 行**，不要再机械堆 4 段模板。
- **看不出标得好坏**：sample_preview 必须用 v3.6 模板（题目 + 参考 + 模型 + 原始 Accuracy 并列 + 质量信号），让用户**不必猜**。
- **大数据没抽样直接全量**：>100 条没明确要求全量时**必须先建议抽样**，否则可能浪费 LLM 配额 + 跑完才发现 prompt 不对。
- **交付前没校验文件**：`delivery_ready=false` 时**严禁给下载链接**。
- **跑完只给 manifest 摘要**：必须展示前 3 条 `sample_preview` + `quality_summary` 整批速览。
- **质量信号异常被忽略**：任何 `quality_signals` 字段出 ⚠️ 时，必须显眼告诉用户"建议人工复核"。
- **429 Too Many Requests**：降低并发 `--workers 1`，脚本内置 429 长退避。
- **pyyaml 未安装**：`rvec/*.yaml` 自动跳过并回退到 `fin_rvec_config.json` + 内置常量。
- **输出太大导致终端截断**：以 manifest 和落盘文件为准，不要依赖屏幕回显的完整内容。
- **A/B 对比拿到一致结果**：说明改的 config 部分没有进入 LLM 真实调用路径，可用 `--show-prompt --family X` 对比两次模板渲染。
- **想确认脚本看到了什么**：先用 `--snapshot-only` 看输入快照。
- **改完 yaml/json 后没生效**：先跑 `python3 run_tagging.py --validate-config xxx.yaml` 确认无 errors。
- **`run_tagging.py --mode auto` 退出码 1**：说明数据非金融域且没给 `--config`，看 stderr 的 `actionable_hints`。

## 禁止事项

- 禁止首轮堆砌冗长模板（v3.6 起字段完整就给 3 行）。
- 禁止把字段映射结果藏起来不告诉用户。
- 禁止 >100 条数据直接全量打标（除非用户明确"全量"）。
- 禁止在 `delivery_ready=false` 时给用户下载链接。
- 禁止只给 manifest 摘要不展示 sample_preview + quality_summary。
- 禁止忽略 `quality_signals` 中的 ⚠️ 标记。
- 禁止执行 pip install（环境不支持）。
- 禁止修改脚本文件。
- 禁止在回复中暴露 API Key，用 `<REDACTED>` 代替。
- 禁止把打标拆成多条 tool call 执行（必须一条命令完成）。


