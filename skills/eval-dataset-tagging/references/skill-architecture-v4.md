# 评测集打标 Skill 整体架构 v4（Agent 化演进设计）

> **目标**：把"金融 RVEC 单点工具"演进为**通用评测集打标 Agent Skill**，覆盖
>「场景细分（无模型回复）」+「错误归因（含模型回复）」两阶段，支持本地文件和 Lumi 平台双通道，
> 配置项（题型族规则 / RVEC 体系 / Judge prompt）可被用户查看并热修改。
>
> 兼容已稳定的 `fin_rvec_tag.py`（v3.x）—— 它将作为 v4 中 `error_attribution` 模式的具体实现保留。

---

## 1. 用户视角的四象限

按"输入侧是否含模型回复"× "输出落地形式"切两刀：

|  | 输出到本地文件 | 输出回 Lumi |
|---|---|---|
| **仅评测题（无模型回复）** | A. 场景细分→ CSV/XLSX | B. 场景细分→ Lumi dataset.metadata |
| **评测题 + 模型回复 + 评分** | C. RVEC 错误归因→ CSV/XLSX | D. RVEC 错误归因→ Lumi run/trace.metadata |

**Agent 的智能判断**：在用户只给"输入文件"时，自动按以下优先级路由：

```text
有 model_response/answer 字段 + 不全为空 → error_attribution（RVEC）
仅 question/input → scene_labeling
                       ↓ 进一步识别 schema
                       ├─ 用户自带 scene_schema_*.yaml → 走通用 SceneLabeler
                       └─ 否则 → 提示用户上传 schema 或选用内置 fin_rvec
```

---

## 2. 模块分层（v4 架构图）

```text
┌─────────────────────────────────────────────────────────────┐
│  SKILL.md  ← Agent 入口（主流程：判别→询问最少参数→执行）        │
└──────────────┬──────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────┐
│  scripts/run_tagging.py  ← 统一 CLI 入口                      │
│   ├─ --auto                自动判别 mode                      │
│   ├─ --mode {scene|rvec|generic_error}                       │
│   ├─ --input / --lumi-dataset                                │
│   ├─ --output / --lumi-write-back                            │
│   ├─ --config <path>       覆盖默认配置                        │
│   └─ --inspect             仅探查不调 LLM                      │
└──────────────┬──────────────────────────────────────────────┘
               │
       ┌───────┴────────┬──────────────┬─────────────────────┐
       ▼                ▼              ▼                     ▼
  io/                  llm/          labelers/            config/
  ├─ readers.py        ├─ client.py  ├─ base_labeler.py   ├─ rvec/
  │   ├─ from_file     ├─ endpoints  ├─ scene_labeler.py  │   ├─ rvec_schema.json
  │   └─ from_lumi     └─ retry      ├─ rvec_labeler.py   │   ├─ task_family_rules.json
  ├─ writers.py        └─ prompt_    │   (= 现 fin_rvec)  │   └─ judge_prompts.json
  │   ├─ to_file       └─ registry   └─ generic_error_    │       ↑ 可视化/可编辑
  │   └─ to_lumi                          labeler.py      ├─ scene/
  └─ schema_inspect.py                                    │   └─ scene_schema.yaml
                                                          └─ user_overrides/
                                                              （用户的修改放这里，
                                                               用 deep_merge 合并）
```

---

## 3. 配置可视化与可编辑（需求 #3 落地方式）

### 3.1 三类可调变量

| 配置类别 | 文件 | 作用 |
|---|---|---|
| **题型族规则** | `config/rvec/task_family_rules.json` | `match_norm_task` / `display_name` / `focus_labels` / `forbid_primary` / `extra_rules` |
| **RVEC 体系** | `config/rvec/rvec_schema.json` | F/T 轴、R/V/E/C 标签、P 等级、scoring_rules |
| **Judge prompt** | `config/rvec/judge_prompts.json` | system_prompt 模板 + 各 family 的 user_prompt 模板（结构化为 JSON 而非硬编码） |

> 现状：这三块**都已经在 `fin_rvec_config.json` 里**，只是 prompt 模板还硬编码在 `fin_rvec_tag.py`。
> v4 的关键改造 = **把 prompt 模板抽成 JSON 文件 + 提供 inspect/edit 命令**。

### 3.2 用户操作命令（在 SKILL.md 里给出）

```bash
# 查看当前配置
python run_tagging.py --inspect-config rvec_schema
python run_tagging.py --inspect-config task_family_rules
python run_tagging.py --inspect-config judge_prompts

# 编辑（输出 JSON 路径，告知用户用 VS Code 打开修改）
python run_tagging.py --edit-config rvec_schema

# 验证配置（修改后跑这个，确保 JSON 合法、prompt 占位符齐全、family 命中规则不冲突）
python run_tagging.py --validate-config

# 用自定义配置打标（不污染默认）
python run_tagging.py --input data.csv --config-override ./my_rules.json
```

### 3.3 配置合并机制

```text
默认配置 (config/rvec/*.json)
    ↓ deep_merge
用户 override (config/user_overrides/*.json，可选)
    ↓ deep_merge
命令行 --config-override 指定的 JSON
    ↓
最终生效配置（写到 output/_effective_config.json 便于复盘）
```

---

## 4. Agent 智能判别流程（SKILL.md 主流程）

Agent 收到用户请求后按以下决策树执行（**不需要询问用户太多**）：

```text
1. 用户必填：input（文件路径或 Lumi dataset 名）

2. 探查（必跑，不调 LLM）
   python run_tagging.py --input X --inspect
   → 输出 JSON：{
       columns, total_rows,
       has_question, has_answer, has_accuracy,
       norm_task_family_dist,  # QA_CHOICE: 100, SENTIMENT: 50, ...
       recommended_mode,       # rvec | scene | error_generic
       recommend_reason,
       missing_required_fields  # 若不足以打标，列出缺失字段
     }

3. Agent 判别：
   ┌─ recommended_mode = rvec：直接走 fin_rvec_tag.py（现状 happy path）
   ├─ recommended_mode = scene + 用户给了 scene_schema：走 SceneLabeler
   ├─ recommended_mode = scene + 没给 schema：
   │   反问用户："是否使用内置金融场景？或上传 scene_schema.yaml？"
   └─ missing_required_fields 非空：报错并退出，提示用户补字段

4. 执行（一条命令到底，绝不拆步）：
   python run_tagging.py --auto --input X [--lumi-dataset Y] [--lumi-write-back]

5. 解析 __MANIFEST_START__ 段，向用户汇报：
   - status / total / success / failed
   - mode / 输出文件 / Lumi 回填情况
   - 关键统计（评分分布 / TOP5 标签 / family 分布）

6. 失败处理：
   - 用 file-delivery skill 交付失败明细
   - 给出"用 --workers 1 重跑 / 缩小 sample-size / 检查 JSON 输出"等建议
```

---

## 5. 输入/输出双通道（需求 #2、#4 落地方式）

### 5.1 输入侧

| 来源 | CLI 参数 | 实现 |
|---|---|---|
| 本地文件（xlsx/csv/jsonl） | `--input data.csv` | `io.readers.from_file` |
| Lumi 平台 dataset | `--lumi-dataset MyEvalSet --lumi-version v3` | `io.readers.from_lumi`（已有 `lumi_client.py`） |
| 直接传 records JSON | `--input -`（stdin） | `io.readers.from_stdin` |

### 5.2 输出侧

| 去向 | CLI 参数 | 实现 | 说明 |
|---|---|---|---|
| 本地文件 | `--output dir/` | `io.writers.to_file`（自动跟随输入扩展名）| 已有 |
| Lumi dataset.metadata 回填 | `--lumi-write-back` + `--lumi-dataset Y` | `io.writers.to_lumi_dataset` | 写入 `metadata.labels.*` |
| Lumi run（绑 trace） | `--lumi-run RunName` | `io.writers.to_lumi_run` | 已有三阶段绑定逻辑 |

### 5.3 写出字段对齐 schema 文档

| 字段 | scene_labeling 模式 | error_attribution 模式 |
|---|---|---|
| `label_*_scene` 或 `scene_labels.{dim}` | ✅ 多维 L1/L2/L3 | ❌ |
| `label_fin_scene` / `label_task_type` | ❌ | ✅ 金融 RVEC 才有 |
| `label_rve_*` 系列 | ❌ | ✅ |
| `labeler` | ✅ `scene_tag@<model>` | ✅ `fin_rvec_tag@<model>` |
| `review_status` | ✅ `pending` | ✅ `pending` |

---

## 6. 实施路线（向后兼容，不破坏现有跑批）

### Phase 1（短期 1 天）—— 配置抽出，不改运行时

- [ ] 把 `fin_rvec_tag.py` 的 5 个硬编码 prompt 模板（`USER_PROMPT_QA_CHOICE` 等）抽到 `config/rvec/judge_prompts.json`
- [ ] `fin_rvec_tag.py` 启动时优先读 JSON、未找到则回退到内置常量（不破坏现有跑批）
- [ ] 加 `--inspect-config <name>` / `--edit-config <name>` 子命令（只读/输出路径）
- [ ] SKILL.md 增加"配置查看与编辑"段

### Phase 2（中期 2 天）—— 智能判别 + 通用 scene 模式

- [ ] `run_tagging.py` 加 `--auto` 智能判别（已有雏形，加固判别规则）
- [ ] 修复 `scene_labeler.py` 与新 schema 字段对齐（`model_response` / `model_reasoning` / 等）
- [ ] 加 `--validate-config`（JSON Schema 校验 + prompt 占位符检查）
- [ ] inspect 模式输出 `norm_task_family_dist` 帮 Agent 决策

### Phase 3（中期 3 天）—— Lumi 双向打通

- [ ] `--lumi-dataset` 输入端打通（`lumi_client.read_dataset_items` 已存在，封装到 `io.readers.from_lumi`）
- [ ] `--lumi-write-back` 输出端打通（写 `dataset_item.metadata.labels`）
- [ ] `--lumi-run` 绑定 trace 和打标结果

### Phase 4（长期）—— 配置可视化前端（可选）

- [ ] 静态 HTML/JS 页面读取 JSON 配置，提供 schema 浏览 + 在线编辑（POST 回 `--config-override`）
- [ ] 用 `--show-prompt-preview` 输出某条样本的完整渲染后 prompt（供 prompt 调试）

---

## 7. SKILL.md 主入口改造预览（v4）

```markdown
# 评测集智能打标

## 输入识别（Agent 自动做）
1. 探查文件 → 判断是否含 model_response → 决定 mode：
   - 含模型回复 + Accuracy → `rvec`（错误归因，金融领域走 fin_rvec_tag.py）
   - 仅 question/input → `scene`（场景细分）
2. 探查 norm_task → 自动选 family prompt
3. 检测 Lumi 信息 → 自动启用 trace 上报

## 一条命令完成
python run_tagging.py --auto --input <X> [--lumi-write-back]

## 配置随时查看/编辑
python run_tagging.py --inspect-config rvec_schema
python run_tagging.py --edit-config judge_prompts
python run_tagging.py --validate-config
```

---

## 8. 验收标准

| 需求 | 验收 |
|---|---|
| #1 智能判别场景/RVEC | `--auto` 在 4 类输入上路由正确：①仅题 ②题+回复 ③Lumi dataset ④含 norm_task 的 RVEC 数据 |
| #2 多通道输入 + 自动化 | 同一命令支持文件/Lumi 输入，输出 manifest JSON 给 Agent |
| #3 配置可查看/编辑 | `--inspect-config` 显示当前生效；`--edit-config` 给路径供 VS Code 打开；`--validate-config` 检查合法性；改后立即生效 |
| #4 多通道输出 | `--output` 写文件、`--lumi-write-back` 写 Lumi、两者可并存 |

---

## 附录：与现状的对照

| 现状文件 | v4 角色 | 改造 |
|---|---|---|
| `fin_rvec_tag.py` | rvec_labeler 实现 | 仅抽 prompt 到 JSON，主体不动 |
| `run_tagging.py` | 统一 CLI 入口 | 加 `--auto` / `--inspect-config` / `--lumi-write-back` |
| `scene_labeler.py` | scene 模式实现 | 字段映射对齐新 schema |
| `error_labeler.py` | 通用 error 模式 | 保留作为非金融 fallback |
| `lumi_client.py` | Lumi 适配层 | 加 `read_dataset_items` / `write_dataset_metadata` 高级 API |
| `schema_parser.py` | 配置加载 | 加 `validate_config` / `deep_merge_overrides` |
| `io_utils.py` | IO 适配 | 拆为 `readers/writers` 子模块（可选） |
| `fin_rvec_config.json` | RVEC 完整体系 | 拆为 `rvec_schema.json` + `task_family_rules.json` + `judge_prompts.json`（可选；维持单文件也行） |
