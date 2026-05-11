---
name: fin-llm-eval-report
description: >-
  报告生成：严格按照`Procedure`，生成评测报告。
  原始数据：严格使用  `fetch-data`技能获取数据`.csv`，不允许编造数据
  规范：严格按照技能中描述的`输入的数据规范`，`报告结构`，`评测维度及数据来源映射`来进行生成
version: 1.0.0
author: hpfu
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [LLM, Eval, Finance, Reporting, HTML, ECharts, Benchmark]
    category: research
    related_skills: [fetch-data]


---
# Skill Title

基于三类评测 CSV 数据，生成面向汇报演示的单文件自包含 HTML 可视化报告。
覆盖 7 个金融能力维度，配置 ECharts 交互图表，每图附文字解读。

## When To Use
  - **帮我生成评测报告**  
  - **大模型能力对比**  
  - **评测维度分析** 
  - **帮我出一份评测报告** 
  - **基于评测结果生成报告** 
  - **模型对比报告** 

## Quick Reference
| 参考描述 | 参考文件位置 |
|--------|---------|
| 图例参考 | `references/chart-templates.md` |
| 嵌入图表 | `references/css-skeleton.md` |


## 输入数据规范

### 支持的三种 CSV 类型

| 类型 | 核心列 | 说明 |
|------|--------|------|
| 知识问答（FullReport） | model, domain, difficulty, Accuracy(0/1), Accuracy_comment, reasoning_quality_comment | 多选题，按领域/难度分组 |
| 情绪分类（NewsReport） | model, difficulty, Accuracy(0/1), Accuracy_comment | 新闻情绪分类，三档难度 |
| 研报生成（ResearchReport） | model, factuality_score, recall_score, factual_check, recall_check, reasoning_chain_quality | 开放生成，双维度评分 |

### 关键字段识别规则
- **模型名**：列名含 `model`，值即模型标识（如 `deepseek-v3.2`、`kimi-k2.5`）
- **准确率**：`Accuracy` 列，值为 0/1 二元，取均值×100 = 百分制得分
- **研报分数**：`factuality_score`、`recall_score`，原始值为 0-1 或 0-100，统一转换为百分制
- **评分理由**：`Accuracy_comment`、`reasoning_quality_comment`、`factual_check`、`recall_check` 列，用于归因分析章节

---

## 7 个评测维度及数据来源映射

| 维度 | 数据来源 | 领域关键词 |
|------|---------|-----------|
| 市场情绪与新闻分类 | NewsReport | 全部 |
| 基本面与经济学 | FullReport | Economics, Equity Valuation, Corporate Finance |
| 投资常识 | FullReport | Alternative Investments, Portfolio Management, Fixed Income |
| 法律法规与职业操守 | FullReport | Ethical and Professional Standards |
| 研报能力与财报解读 | ResearchReport | factuality_score + recall_score |
| 财务常识 | FullReport | Financial Reporting and Analysis |
| 量化计算与数理分析 | FullReport | Quantitative Methods, Derivatives |

### 综合得分权重（7维加权均值）

```
市场情绪        × 10%
基本面与经济学   × 15%
投资常识        × 15%
法律法规        × 15%
研报财报解读     × 20%
财务常识        × 15%
量化计算        × 10%
```

### 研报维度计算公式
```
研报得分 = factuality_avg × 60% + recall_avg × 40%
```
若某模型缺少 recall 数据，仅用 factuality 得分，并在报告中注明。

### 题目数量显示规则
- 显示**唯一题数** = 总行数 ÷ 模型数量（同一套题被多个模型跑，不重复计数）

---

## 报告结构（12 节 + 附录）

| 节 | 标题 | 核心内容 |
|----|------|---------|
| 01 | 评测概览 | 数据集构成饼图、指标说明卡片、模型参与矩阵 |
| 02 | 核心结论 | 5 条结论卡片，每条含具体数字 |
| 03 | 综合排名 | 排行榜表格 + 水平柱状图 + 7维雷达图 |
| 04 | 多维度分析 | 4.1 子领域分组柱状图、4.2 难度分层、4.3 10域详细对比 |
| 05 | 模型横向对比 | 按模型雷达图 + 擅长/薄弱标签 |
| 06 | 稳定性分析 | σ = √(p·(1-p)) 标准差柱状图 |
| 07 | 交叉热力图 | 模型×维度颜色矩阵（绿→红色阶） |
| 08 | 根因分析 | Badcase 聚类、三大失败类型、评分理由原文引用 |
| 09 | 效率对比 | 延迟折线图、Token 消耗堆叠柱状图 |
| 10 | 版本迭代（占位） | 多版本折线图骨架，标注"数据收集中" |
| 附录 | 方法论 | 指标定义、权重说明、数据处理流程（默认折叠） |

---

## Procedure

### Step 1 · 数据获取
使用技能fetch-data，获取评测数据集，获取数据集之后，在`/mnt/workspace/data` 路径中检查是否生成了对应的`.csv`文件，如果没有文件生成，终止后续执行并返回：“未正确生成数据文件”。

### Step 2 · 解析数据
解析 `/mnt/workspace/data` 目录下所有的`.csv`文件

```python
# 伪代码：解析所有 CSV
for each CSV:
    detect model column
    detect score columns (Accuracy / factuality_score / recall_score)
    detect domain / difficulty columns
    extract comment columns for attribution
    compute per_model_per_domain accuracy
    compute per_model_per_difficulty accuracy
```

**输出**：JSON 格式的完整得分矩阵（含每个维度、每个子领域、每档难度）

### Step 2 · 计算 7 维得分
- 按维度映射规则聚合子领域得分
- 研报维度用公式：`factuality×0.6 + recall×0.4`
- 对缺失数据的模型在对应维度标注 `null`，综合排名排除该模型

### Step 3 · 计算综合得分
- 加权均值（见上方权重表）
- 只对参与全部3类评测的模型计算完整综合分
- 仅参与部分评测的模型标注"待补全"

### Step 4 · 生成核心结论（5条）
每条结论必须包含：
1. 综合最优模型 + 具体分值 + 与第二名差距
2. 最偏科模型 + 最高分维度 vs 最低分维度
3. 关键超参发现（温度/解码模式对精度的影响）
4. 难度层级最大跌幅（Easy→Medium 降幅最大的模型）
5. 研报生成质量对比（factuality vs recall 分化）

### Step 5 · 构建 HTML 骨架
参考 `references/css-skeleton.md`：
- 左侧固定侧边栏（宽 220px）+ 右侧滚动内容区
- CSS Custom Properties 定义颜色系统
- IntersectionObserver 驱动侧边栏 active 状态

### Step 6 · 嵌入图表
参考 `references/chart-templates.md`：
- ECharts 5.4.3 CDN 引入
- IntersectionObserver 懒初始化（进入视口才渲染）
- 每图下方固定 `<div class="chart-insight">` 解读文字

### Step 7 · 去标识化处理
- **禁止出现**：CFA、特定题库名称、具体机构名
- 用通用表述替代：「金融综合知识库」「研报生成评测集」「市场情绪分类评测集」
- 评测集来源在附录中用模糊描述

### Step 8 · 质量检查
- [ ] 所有图表数值与数据矩阵一致
- [ ] 每节均有文字解读
- [ ] 侧边栏点击跳转正常
- [ ] IQuest/新模型占位区块标注清晰
- [ ] 附录默认折叠
- [ ] 题目数量显示为唯一题数（÷模型数）
- [ ] HTML 文件完全自包含（无外部依赖除 ECharts CDN）
---

### Step 9 · 文件归档

- 生成的报告命名为：「finEvalReport-YYYYMMDD-HHMMSS.html」,文件名中`YYYYMMDD-HHMMSS`用生成本报告时的当前「年月日-时分秒」替换。
- 生成的报告保存在 `/mnt/workspace/achieveFinReport`目录下

### Step 9 · 展示报告
- 使用 `fin-report-webserver` 技能，在chat中输入网址给到用户。网址严格按照技能中的描述来生成。

## 视觉规范

### 颜色系统
```css
--bg: #F8FAFC;           /* 页面背景 */
--card: #FFFFFF;          /* 卡片背景 */
--sidebar-bg: #F1F5F9;   /* 侧边栏背景 */
--primary: #3B82F6;       /* 主色蓝 */
--muted: #64748B;         /* 次要文字 */
```

### 模型配色（可扩展）
```js
// 示例配色方案
'deepseek-v3.2': '#10B981'  // 绿
'kimi-k2.5':     '#8B5CF6'  // 紫
'qwen3.6-plus':  '#3B82F6'  // 蓝
'iquest':        '#F59E0B'  // 琥珀
'glm-5':         '#EC4899'  // 粉
// 新增模型按顺序取：#06B6D4, #EF4444, #84CC16 ...
```

### 得分颜色编码
| 分值范围 | 颜色 | 含义 |
|---------|------|------|
| ≥ 80 | `#059669` 绿 | 优秀 |
| 70–79 | `#2563EB` 蓝 | 良好 |
| 60–69 | `#D97706` 琥珀 | 中等 |
| < 60 | `#DC2626` 红 | 待改善 |

---

## 归因分析专项规范（第8节）

### 数据来源
使用 CSV 中的评分理由列：
- `Accuracy_comment`：答错原因（知识盲区/推理错误/格式问题）
- `reasoning_quality_comment`：推理链质量评价
- `factual_check`：研报事实核查说明
- `recall_check`：研报召回率评价

### 输出格式
1. **Badcase 热点表**：失败率最高的 Top-7「模型×领域×难度」组合
2. **三大根因类型卡片**：
   - 知识盲区型（Easy/Medium 失败率相近）
   - 多步推理型（仅 Medium/Hard 失败）
   - 结构化理解型（涉及表格/多指标交叉）
3. **原文引用区块**：从 comment 列提取代表性评语，每类根因至少引用 2 条

---

## 注意事项

1. **研报异常记录**：若某模型出现延迟异常（>1000s）或得分全 0，单独标注并排除在综合排名外
2. **模型版本一致性**：同系列不同版本（如 qwen-plus-2025-12-01 vs QwenPlus）在报告中统一展示名
3. **超参记录**：若 CSV 含 temperature 列，在效率分析节展示超参对精度的影响
4. **glm-5 类模型**：若缺少研报评测数据，综合分标注"待补全"，其余维度正常显示
