# 打标 Skill 设计文档

详见 `requiement.txt` 原始需求文档。

## 核心设计

### 两种打标模式

| 维度 | 场景细分 (scene_labeling) | 错误归因 (error_attribution) |
|------|--------------------------|------------------------------|
| 打标对象 | 全量样本 | badcase（筛选后） |
| 标签结构 | 多维度 L1→L2→L3 树形 | 固定枚举清单，多选 |
| 调用频率 | 同一评测集只打一次 | 每次评测后重新打 |
| 输出字段 | scene_labels.{维度}.{l1,l2,l3,reason} | error_labels[{label,severity}] + error_reason |

### 架构

```
run_tagging.py (入口)
  ├── schema_parser.py  → 解析用户配置（yaml/json/md）
  ├── io_utils.py       → 读取输入 / 写出结果（jsonl/csv/xlsx）
  ├── llm_client.py     → 调用 LLM（OpenAI 兼容）
  ├── lumi_client.py    → Lumi 平台集成（可选）
  ├── base_labeler.py   → 批处理框架（并发/重试/Trace）
  ├── scene_labeler.py  → 场景细分逻辑 + Prompt
  └── error_labeler.py  → 错误归因逻辑 + Prompt
```

### 失败处理流程

1. 第一轮并发打标，失败的先收集
2. 全部完成后统一重试 N 次（默认3次）
3. 最终输出：成功文件 + 失败文件
4. 控制台汇总成功/失败/跳过条数
