# 变量、配置与秘密

## 两个配置边界

可分发 Codex Skill 不要求环境变量或 API key。Hermes 私有运行时需要宿主保护的 `.env`、Hermes YAML 和 Codex provider 信息。真实值不得进入仓库、日志、示例或 Git 历史。

`runtime/goutoujunshi/plugin.yaml` 只声明插件启动必须的 `GOUTOUJUNSHI_DB_PASSWORD` 和 `GOUTOUJUNSHI_OWNER_ID`。其余变量由安装脚本配置。

## Runtime 变量

| Name | Purpose | Source/default | Absence impact |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | Hermes 主模型、显式历史补强和 oracle 鉴权 | 受保护 env 或交互安装 | 对应远程模型调用失败 |
| `FEISHU_ALLOW_ALL_USERS` | 禁止开放接入 | setup 写 `false` | 错误配置可能扩大接入 |
| `FEISHU_ALLOWED_USERS` | Feishu owner allowlist | 唯一历史 owner | owner 消息可能被拒绝 |
| `GOUTOUJUNSHI_OWNER_ID` | owner 身份与数据命名空间 | 同一 owner | 插件失败关闭 |
| `GOUTOUJUNSHI_DB_HOST` / `PORT` | 权威 MySQL 地址 | `127.0.0.1` / `3306` | 连接失败 |
| `GOUTOUJUNSHI_DB_NAME` | 权威数据库名 | `goutoujunshi` | 选错数据库 |
| `GOUTOUJUNSHI_DB_USER` / `PASSWORD` | 最小权限数据库凭据 | `goutoujunshi_app` / 受保护随机值 | 数据层拒绝连接 |
| `GOUTOUJUNSHI_EXPORT_ROOT` | 只读关系投影目录 | `.local/relationships` | exporter 使用 cwd 默认值 |
| `GOUTOUJUNSHI_OPENAI_BASE_URL` | Responses-compatible 主模型地址 | 从 Codex provider 派生 | 聊天/补强/预检失败 |
| `GOUTOUJUNSHI_MODEL` | 当前远程主模型 | 安装目标 | 聊天/补强失败 |
| `GOUTOUJUNSHI_REASONING` | reasoning effort | 安装目标 | 配置预检失败 |
| `WEB_TOOLS_DEBUG` | 禁止 Hermes web provider 输出调试查询 | 关系 profile `.env` 固定 `false` | 可能扩大查询日志内容 |
| `HERMES_HOME` | Hermes config/state/cache 根目录 | operator 脚本设置 | 宿主定位可能不同 |
| `PYTHONPATH` | Hermes Python 根目录 | operator 脚本设置 | CLI 导入失败 |

MySQL 关系检索没有语义开关、Milvus/Ollama URL、embedding 模型或 RRF 环境变量。`RRF k=60` 和三支权重在实现中固定，避免部署配置漂移。公网搜索使用免 API key 的 DDGS，不新增搜索密钥；DDGS Python 包由 Hermes 官方 `tools post-setup ddgs` 安装。wrapper 经 provider registry 只接受精确 `ddgs`，不读取 active/default provider，也没有 fallback 配置。

## 独立 benchmark 变量

`run_mysql_search_benchmark.py` 只接受数据库名以 `goutoujunshi_benchmark` 开头的独立库，拒绝连接权威库。它使用 `GOUTOUJUNSHI_BENCHMARK_DB_HOST/PORT/NAME/USER/PASSWORD`；启用 `--answer-eval` 时还使用上表的远程主模型变量。benchmark 会创建并最终删除自己的测试表，必须在专用库与单独授权下运行。

## Linux Compose 变量与秘密

`deployment/linux/server.env` 只保存非秘密的路径、UID/GID 和锁定镜像 digest，不进入 Git。真正秘密位于服务器数据根的 `secrets/`：`hermes.env`、`mysql-app-password` 和 `mysql-root-password`，目录权限必须为 `700`、文件为 `600`。

`runtime/bootstrap.py prepare-server-secrets` 从受保护的本机 Hermes `.env` 只迁移 `OPENAI_API_KEY`、飞书 app/allowlist、owner、主模型地址/模型/reasoning，并生成新的远端 MySQL app/root 密码。它强制 `FEISHU_ALLOW_ALL_USERS=false`、数据库主机为 `mysql`、投影根为 `/opt/data/relationships`；不会迁移旧 token、无关 provider 或缓存凭据。

## 配置文件

- `runtime/goutoujunshi/plugin.yaml`: v1.7.0 插件接口、6 个默认工具与 hooks，无秘密值。
- Hermes global `config.yaml`: Feishu toolset 精确为 `goutoujunshi-user`，未绑定群不具备关系或公网搜索能力；文件不在仓库中。
- Hermes 关系 profile `config.yaml`: Feishu toolsets 为 `goutoujunshi` 和 `goutoujunshi-user`，设置 `web.search_backend: ddgs`、`tools.tool_search: false`，并禁用 Hermes 0.20.4 自动加入的 `bfl` 及原生 terminal、file、web、browser 等 toolsets；文件不在仓库中。bootstrap `verify` 还用 Hermes 实际 resolver 检查精确工具面，并直接 `import ddgs`，不能只信任 YAML 表面值。
- `runtime/goutoujunshi/schema.sql`: schema v5，新增两个 MySQL 检索/任务表和 ngram FULLTEXT，无密码。
- `scripts/wsl/Manage-Goutoujunshi-MySql.sh`: 面向仓库外的 WSL Docker MySQL；调用具有副作用。
- `deployment/linux/compose.yaml` 与 `server.env`: Rocky Linux 常驻栈及其非秘密部署参数；真实 `server.env` 不进入 Git。

## Secret 处理与发布检查

- 不回显 `.env`、auth、token、密码、cookie、私钥、session 或人物标识。
- v1.6.0 起不再生成或使用 `GOUTOUJUNSHI_TOKEN_SECRET`；升级时保留现有 `.env` 中的旧值，避免无关配置改写，但该值不参与授权。
- 关系 profile 固定 `tools.tool_search: false`，保证 6 个受控插件工具直接可见；该值不是环境变量。未绑定群仍只使用全局 `goutoujunshi-user` toolset。
- 不记录公网搜索原始查询或完整净化查询；wrapper 日志只允许净化后查询的 SHA256、长度、耗时、结果数和状态。
- `.local/`、`.env*`、dump、backup、log、关系 handoff、import package 和投影不得进入 Git。
- 安装、预检、schema、回填和 benchmark 可能读取凭据或访问外部系统，执行前需要明确授权。
- commit 前检查准确 staged 路径，并扫描秘密赋值和私密关系材料。
