# 医学评测 Pipeline 集成方案
## eval_skill ↔ eval-dataset-tagging 联动

> 版本：v1.0 | 日期：2026-06-15

---

## 1. 整体架构

```
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 1: 数据生成 (gen_medical_responses.py)                            │
│   输入: data/检查检验q评测集_1.csv / 药品评测集.xlsx / 医学试题评测集.xlsx │
│   输出: medical_qa_with_responses__*.csv + Lumi Dataset                 │
│   命令: run_py.bat gen_medical_responses.py --dataset all --upload-dataset│
└──────────────────────────────┬─────────────────────────────────────────┘
                               ↓
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 2: RVEC 评测执行 (eval_skill)                                     │
│   输入: Lumi Dataset (Medical-XJ-QEval / Medical-Drug-Eval / Medical-Exam-Eval)│
│   输出: outputs/<experiment>/samples.csv + summary.json + Lumi Traces   │
│   命令: python -m eval_skill.cli run -c configs/examples/medical_rvec.yaml│
└──────────────────────────────┬─────────────────────────────────────────┘
                               ↓
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 3: 错误归因打标 (eval-dataset-tagging)                            │
│   输入: Phase 2 的 samples.csv                                          │
│   输出: labeled.xlsx（含 M场景 + T任务 + RVEC标签 + P等级 + 评分 + 理由）│
│   命令: python run_tagging.py --mode med_rvec \                         │
│            --config config/medical_rvec_config.yaml \                    │
│            --input outputs/<experiment>/samples.csv --auto               │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 数据流字段对齐

### Phase 2 → Phase 3 字段映射（自动）

| eval_skill samples.csv 列 | eval-dataset-tagging 映射角色 | 说明 |
|---|---|---|
| `sample_id` | 唯一标识 | 如 `EXAM-med001` |
| `input` | question + context | JSON 含 prompt/question/options/background |
| `expected_output` | ground_truth_structured | JSON 含 answer/explanation/rubric |
| `model_response` | model_response | 模型原始回复文本 |
| `model` | 模型标识 | 如 `gemini-3.1-pro` |
| `score` | Accuracy | 客观评分（0/1） |
| `judge_comment` | judge_comment | RVEC Judge 评测细节（JSON） |
| `metadata.dataset_key` | norm_task_from_filename | 用于 family 路由 |
| `metadata.schema` | schema 辅助 | single_choice / multi_choice / open_ended |

### Family 路由规则

| metadata.dataset_key | 自动路由到 Family | 模板 |
|---|---|---|
| `exam` | MED_CHOICE | 医学选择题 |
| `xj` | DIAGNOSIS | 检查检验解读 |
| `drug` | DRUG_QA | 药品用药问答 |
| 其他 | GENERIC_MEDICAL | 通用兜底 |

---

## 3. 操作步骤（完整流程）

### Step 0: 环境准备
```bash
# 服务器上
cd /mnt/workspace/lumi-agent/skills
source /mnt/workspace/lumi-agent/venv/bin/activate
export PYTHONPATH=/mnt/workspace/lumi-agent/skills:$PYTHONPATH
```

### Step 1: 生成模型回复（已完成）
```bash
# 本地 Windows
run_py.bat gen_medical_responses.py --dataset all --upload-dataset
```

### Step 2: 运行 RVEC 评测
```bash
# 服务器
python -m eval_skill.cli list-datasets --domain medical
python -m eval_skill.cli preview-dataset --name Medical-Exam-Eval --limit 5
python -m eval_skill.cli describe-config -c configs/examples/medical_rvec.yaml
python -m eval_skill.cli run -c configs/examples/medical_rvec.yaml
```
产出：`outputs/medical_rvec_<timestamp>/samples.csv`

### Step 3: 错误归因打标
```bash
# 服务器（同一环境）
cd /mnt/workspace/lumi-agent/skills/eval-dataset-tagging/scripts

# 3.1 预览数据映射是否正确
python run_tagging.py --mode med_rvec \
    --config ../config/medical_rvec_config.yaml \
    --input /path/to/outputs/medical_rvec_xxx/samples.csv \
    --snapshot-only

# 3.2 预览 3 条样例（确认打标质量）
python run_tagging.py --mode med_rvec \
    --config ../config/medical_rvec_config.yaml \
    --input /path/to/outputs/medical_rvec_xxx/samples.csv \
    --preview 3

# 3.3 全量打标
python run_tagging.py --mode med_rvec \
    --config ../config/medical_rvec_config.yaml \
    --input /path/to/outputs/medical_rvec_xxx/samples.csv \
    --auto
```

---

## 4. 配置文件清单

```
tag_skill/skills/eval-dataset-tagging/config/
├── medical_rvec_config.yaml          # 🆕 医学 RVEC 主配置（M轴/T轴/RVEC/评分）
├── medical_field_mapping.yaml        # 🆕 字段映射说明（eval_skill → tagging）
├── rvec/
│   ├── medical_families.yaml         # 🆕 医学题型族路由
│   ├── medical_prompts.yaml          # 🆕 医学打标 Prompt 模板（5 套 family）
│   └── medical_rules.yaml            # 🆕 医学评测规则（独立完整）
│   ├── families.yaml                 # (既有) 金融题型族
│   ├── prompts.yaml                  # (既有) 金融 Prompt
│   └── rules.yaml                    # (既有) 金融规则
├── fin_rvec_config.yaml              # (既有) 金融主配置
└── fin_rvec_config.json              # (既有) 金融 JSON 回退
```

---

## 5. 输出字段说明

打标完成后输出 CSV/XLSX 包含以下新增列：

| 字段 | 含义 |
|------|------|
| `label_med_scene` | 医学业务场景（M01-M14/M99），多选用「；」分隔 |
| `label_task_type` | 任务类型（T00-T16/T99） |
| `label_rve_primary` | 最主要的 RVE 负向标签 |
| `label_rve_all` | 全部 RVE 负向标签（含 P 等级） |
| `label_rve_score_all` | 各负向信号独立分值（0~1） |
| `label_severity` | 最严重 P 等级（P0/P1/P2/NONE） |
| `label_score` | 综合评分（0-4 分） |
| `label_highlights` | 正向亮点标签（C 系列） |
| `label_evidence` | 问题摘录 |
| `label_reason` | 判定理由 |
| `labeler` | 自动填 `med_rvec_tag@<模型名>` |
| `review_status` | 自动填 `pending` |

---

## 6. 自动后处理规则

与金融版本对齐，脚本对 LLM 输出做强制后处理：

1. **答对的题（Accuracy=1）自动剔除 R-FACT-* 标签**
2. **选择题（T16）若 primary=R-FACT-1，自动改为过程标签**
3. **`label_rve_all` 顺序自动重排**，保证 primary 在第一位
4. **`label_severity` 由脚本派生覆盖**（P0 > P1 > P2）
5. **`label_score` 按 scoring_rules 兜底校验**
6. **危急值相关标签（R-RISK-3 + R-SAFE-8）自动升级为至少 P1**

---

## 7. 与金融版本的差异对照

| 维度 | 金融 (fin_rvec) | 医学 (med_rvec) |
|------|----------------|----------------|
| 场景轴 | F01-F12 (金融业务) | M01-M14 (医学科室) |
| 专业 R 标签 | R-SAFE-7~10 / R-FACT-8 / R-REA-6~8 / R-RISK-4 | R-SAFE-7~10 / R-FACT-8~10 / R-REA-6~8 / R-RISK-3~5 |
| P0 触发条件 | 金融违法/严重误导 | 危险用药/危急值漏识别/致命方案 |
| 题型族 | QA_CHOICE / SENTIMENT / REPORT_EVAL / LONG_GEN | MED_CHOICE / DIAGNOSIS / DRUG_QA / MED_REPORT |
| 交叉校验 | F×T (金融场景×任务) | M×T (医学场景×任务) |
| 核心差异标签 | R-SAFE-7 金融专业越界 / R-FACT-8 数据口径 | R-SAFE-8 危险用药 / R-FACT-9 药物信息 / R-RISK-3 危急值 |

---

## 8. 待确认事项

- [ ] `run_tagging.py` 是否需要新增 `--mode med_rvec` 支持（或复用 `--mode fin_rvec` + `--config` 覆盖）
- [ ] eval_skill 的 samples.csv 实际列名确认（需跑一次拿到真实输出）
- [ ] 是否需要 `--pivot-wide` 命令支持宽格式 CSV 转长格式
- [ ] 医学领域 few-shot 样例是否需要扩充（当前 3 例，金融也是 3 例）
- [ ] 服务器部署路径确认（medical_rvec_config.yaml 等文件放哪）
