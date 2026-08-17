# 自动化与代理边界

## 两类执行环境

| 项目 | Codex Skill | Hermes 私有运行时 |
| --- | --- | --- |
| 触发 | 用户显式调用或宿主选择 Skill | owner 飞书消息或受控 CLI |
| 输入 | 当前对话与按需 Markdown | 飞书事件、MySQL 权威上下文、会话和临时附件 |
| 工具 | Skill 不要求外部工具 | 关系提交/检索和 owner 记忆工具 |
| 持久化 | 无 | MySQL 事件、快照、绑定、记忆、检索文档和任务 |
| 自动任务 | 无 | supervisor 维护 Gateway、MySQL、路由、投影、媒体和备份 |
| 对外动作 | 建议文本 | 回复当前军师群；不向其他聊天平台代发 |

## Hermes Hooks 与工具

- `pre_gateway_dispatch`：处理命令，校验 owner/人物/route；按同人物同渠道规则解析上一 draft；注入 Skill、个人记忆和关系上下文。
- `post_llm_call` 与 session cleanup hooks：清除临时媒体和本轮状态，写入无正文本地指标。
- `relationship_commit_turn`：一个事务追加事件、写入时增强、精确 draft 和快照补丁。
- `relationship_search_events`：在当前人物内执行 MySQL 三分支检索，返回有界权威正文。
- `user_memory_remember/correct/forget`：维护 owner-scoped append-only 本人事实。

默认 Feishu toolsets 不暴露 terminal、任意文件读取或 web search。关系与个人记忆工具都只接受 Hermes 服务端 session 授权，模型参数不能提供或扩大权限。

`goutoujunshi` profile 设置 `tools.tool_search: false`，使该 profile 的 5 个受控插件工具直接可见，避免聊天截图轮因延迟披露而跳过搜索与提交。该设置不修改默认 Hermes profile。

## 后台 Supervisor

`Run-Goutoujunshi.ps1` 循环：

1. 保持 Ubuntu WSL 锚点和 MySQL 可用。
2. 按活动 binding 对账 Hermes routes 与 Feishu `extra.group_rules`。
3. 启动/检查 Gateway 和 Feishu 连接。
4. 重试投影，清理过期媒体登记。
5. 创建并轮换本地 MySQL 日备份。

supervisor 不运行历史增强 worker，也不接触 Milvus/Ollama。循环重试不证明宿主已经健康，仍需获批的运行态检查。

## 有副作用的命令

- `scripts/Setup-And-Start-Goutoujunshi.ps1`
- `scripts/Run-Goutoujunshi.ps1`
- `scripts/Control-Goutoujunshi.ps1`
- `scripts/wsl/Manage-Goutoujunshi-MySql.sh`
- `runtime/bootstrap.py` 的 secrets/preflight/configure/install 命令
- `runtime/goutoujunshi_cli.py` 的 init/import/export/reconcile/user-memory、draft resolve，以及 enrichment queue/work/retry 命令
- `runtime/benchmarks/run_mysql_search_benchmark.py`

其中 `enrichment-work` 会把历史关系正文发送给当前远程主模型；`--answer-eval` 会额外运行 80 例全量历史 oracle 与 Top-8 对照；benchmark 会改动独立测试数据库。执行前必须确认准确命令、目标、凭据、网络/数据库副作用和停止条件。

## Prompt 引导与硬边界

`SKILL.md` 的行为、安全和知识路由属于模型引导；owner allowlist、服务端 session/binding 校验、受限 toolset 和 MySQL 事务是更硬的运行边界。补强 prompt 要求只提取原文支持的检索路标，但增强仍是派生信息，所以永不作为权威事实返回。

失败策略：资料不足保留未知；binding/route/database 异常时不分析、不记录；增强缺失不阻断权威事件，搜索标记 `incomplete_enrichment`；零结果不生成“从未发生”的否定事实。
