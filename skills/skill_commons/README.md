# skill_commons

多 skill 共享的基础设施层。**职责单一：env 加载 + Lumi/host 客户端构造 + 共享 registry。**
不放业务逻辑、不放 prompt、不放评测/报告/上传特定的代码。

## 1. 谁在用我

| skill | 用途 |
|---|---|
| eval_skill | 拉 Lumi dataset / push trace / 调各 host 模型 |
| for_report_skill（待接入） | 报告生成时调模型，并把产物写回 Lumi |
| for_dataset_skill（待接入） | 数据集上传 |

任何 skill 只要 `import skill_commons` 即可使用同一份配置。

## 2. 文件结构

```
skill_commons/
├── env.py                 # ensure_env_loaded / require_env / get_env
├── hosts.py               # HostProfile + load_host_profiles + get_client + ${VAR} 占位
├── lumi_client.py         # build_lumi_client (Langfuse)
├── redaction.py           # load_redaction_profiles
├── registry/
│   ├── host_profiles.yaml         # 全局 host 注册表
│   └── redaction_profiles.yaml    # 全局脱敏 profile
├── .env                   # ⚠️ 共享密钥（gitignored）
├── .env.example           # 模板
└── README.md
```

## 3. 与 Hermes gateway 的 .env 关系

| 文件 | 谁管理 | 放什么 |
|---|---|---|
| `<repo_root>/.env` | Hermes 团队 | `HERMES_*` / `OPENAI_BASE_URL` / `API_SERVER_*`（运行态） |
| `skill_commons/.env`（本目录） | 评测/报告/上传 共用 | `LUMI_*` / `<HOST>_API_KEY` / `<HOST>_BASE_URL`（开发态密钥） |

skill_commons **不会** 读 `<repo_root>/.env`，避免命名空间和生命周期混淆。
两个变量空间天然不冲突（`HERMES_*` / `OPENAI_*` / `API_SERVER_*`  vs  `LUMI_*` / `<HOST>_*`）。

如果某天真要跨项目共享一个变量（例如 Langfuse 同实例），有两种做法：
1. 在 shell 启动前 `export` 到进程环境（最高优先级）
2. 设置 `SKILL_COMMONS_ENV_FILE=/path/to/shared.env`

## 4. 第一次使用

```bash
cp skill_commons/.env.example skill_commons/.env
vim skill_commons/.env     # 填好 LUMI_* 和各 host 的 *_API_KEY
```

之后所有 skill 都自动读到这份配置。

## 5. 公开 API

```python
from skill_commons import (
    ensure_env_loaded,         # 一般不用手动调；hosts/lumi_client 内部已自动调
    require_env, get_env,      # 取自定义变量
    HostProfile,               # 数据类
    load_host_profiles,        # 默认从 skill_commons/registry/host_profiles.yaml 加载
    get_profile, get_client, all_profiles,
    build_lumi_client,
    load_redaction_profiles,
)

# 典型用法
client = get_client("zerail")             # OpenAI 兼容客户端
resp = client.chat.completions.create(...)

lumi = build_lumi_client()                 # Langfuse 客户端
ds = lumi.get_dataset("Fin-Compliance")
```

## 6. 扩展

### 6.1 添加新的 host
编辑 [registry/host_profiles.yaml](registry/host_profiles.yaml)：
```yaml
my_new_host:
  api_key: ${MY_NEW_HOST_API_KEY}
  base_url: ${MY_NEW_HOST_BASE_URL:https://api.example.com/v1}
  timeout: 300
```
再到 `.env` 加上 `MY_NEW_HOST_API_KEY=...`。

### 6.2 skill 私有 host_profiles
不推荐，但确实需要时可以传自己的 yaml：
```python
load_host_profiles("/path/to/your/private_profiles.yaml")
```

### 6.3 添加新的脱敏 profile
编辑 [registry/redaction_profiles.yaml](registry/redaction_profiles.yaml)：
```yaml
medical_phi:
  "张三": "[患者A]"
  "李四": "[医生B]"
```
