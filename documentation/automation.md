# 自动化与代理边界

## 两类执行环境

| 项目 | Codex Skill | Hermes 私有运行时 |
| --- | --- | --- |
| 触发 | 用户显式调用或宿主选择 Skill | owner 飞书消息或受控 CLI |
| 输入 | 当前对话与按需 Markdown | 飞书事件、MySQL 权威上下文、会话和临时附件 |
| 工具 | Skill 不要求外部工具 | 关系提交/检索、owner 记忆和受控公网搜索工具 |
| 持久化 | 无 | MySQL 事件、快照、绑定、记忆、检索文档和任务 |
| 自动任务 | 无 | supervisor 维护 Gateway、MySQL、路由、投影、媒体和备份 |
| 对外动作 | 建议文本 | 回复当前军师群；不向其他聊天平台代发 |

## Hermes Hooks 与工具

- `pre_gateway_dispatch`：处理命令，校验 owner/人物/route；按同人物同渠道规则解析上一 draft；注入 Skill、个人记忆和关系上下文。
- `post_llm_call` 与 session cleanup hooks：清除临时媒体和本轮状态，写入无正文本地指标。
- `relationship_commit_turn`：一个事务追加事件、写入时增强、精确 draft 和快照补丁。
- `relationship_search_events`：在当前人物内执行 MySQL 三分支检索，返回有界权威正文。
- `relationship_web_search`：仅在已绑定关系群校验服务端 session/binding、二次匿名化查询，再通过 Hermes provider registry 精确调用 DDGS；最多返回 5 条标题、URL 和摘要。
- `user_memory_remember/correct/forget`：维护 owner-scoped append-only 本人事实。

全局 Feishu toolset 精确为 `goutoujunshi-user`，未绑定群只有 owner 本人记忆工具。活动 binding 路由到 `goutoujunshi` profile 后，才增加关系工具和 `relationship_web_search`。原生 `web_search`、`web_extract`、browser、terminal、file、delegation、memory、cron、mcp 和 computer 均不直接暴露；wrapper 只把净化后的查询交给 registry 中名称精确为 `ddgs` 的可用 provider，不读取默认 provider，也不允许 fallback。

`goutoujunshi` profile 设置 `tools.tool_search: false`，使该 profile 的 6 个受控插件工具直接可见，避免聊天截图轮因延迟披露而跳过搜索与提交。profile 同时设置 `web.search_backend: ddgs` 和 `WEB_TOOLS_DEBUG=false`；DDGS 通过 Hermes 官方 `tools post-setup ddgs` 安装，不需要 API key。bootstrap `verify` 使用 Hermes 实际 toolset resolver 核验全局和关系 profile 的精确解析结果，并硬校验 `ddgs` import；YAML 表面值正确但解析泄漏或依赖不可导入仍判失败。

## 后台 Supervisor

`Run-Goutoujunshi.ps1` 循环：

1. 保持 Ubuntu WSL 锚点和 MySQL 可用。
2. 按活动 binding 对账 Hermes routes 与 Feishu `extra.group_rules`。
3. 启动/检查 Gateway 和 Feishu 连接。
4. 重试投影，清理过期媒体登记。
5. 创建并轮换本地 MySQL 日备份。

supervisor 不运行历史增强 worker，不主动发起公网搜索，也不接触 Milvus/Ollama。循环重试不证明宿主或 DDGS 网络已经健康，仍需获批的运行态检查。

## 有副作用的命令

- `scripts/Setup-And-Start-Goutoujunshi.ps1`
- `scripts/Run-Goutoujunshi.ps1`
- `scripts/Control-Goutoujunshi.ps1`
- `scripts/wsl/Manage-Goutoujunshi-MySql.sh`
- `runtime/bootstrap.py` 的 secrets/preflight/configure/install 命令
- Hermes `tools post-setup ddgs`，会修改受管 Python 环境中的包
- `runtime/goutoujunshi_cli.py` 的 init/import/export/reconcile/user-memory、draft resolve，以及 enrichment queue/work/retry 命令
- `runtime/benchmarks/run_mysql_search_benchmark.py`

其中 `enrichment-work` 会把历史关系正文发送给当前远程主模型；`--answer-eval` 会额外运行 80 例全量历史 oracle 与 Top-8 对照；benchmark 会改动独立测试数据库。执行前必须确认准确命令、目标、凭据、网络/数据库副作用和停止条件。

## Prompt 引导与硬边界

`SKILL.md` 的行为、安全和知识路由属于模型引导；owner allowlist、服务端 session/binding 校验、受限 toolset、公网查询二次匿名化、DDGS provider 锁定和 MySQL 事务是更硬的运行边界。补强 prompt 要求只提取原文支持的检索路标，但增强仍是派生信息，所以永不作为权威事实返回。网页标题、摘要和其他片段同样是不可信临时输入；其中任何指令都不得执行，也不能改变授权、工具、记忆或写入规则。

失败策略：资料不足保留未知；binding/route/database 异常时不分析、不记录；增强缺失不阻断权威事件，关系搜索标记 `incomplete_enrichment`；网页查询被隐私规则拒绝、超时或 backend 不可用时明确说明未完成联网核验。两类搜索的零结果都不生成“从未发生”的否定事实。
