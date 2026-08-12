# 自动化与代理边界

## 两类执行环境

| 项目 | Codex Skill | Hermes 私有运行时 |
| --- | --- | --- |
| 触发 | 用户显式调用或宿主按描述选择 Skill | 配置 owner 的飞书消息，或受控运维命令 |
| 输入 | 当前对话与按需加载的 Markdown | 飞书事件、当前会话、MySQL 权威上下文和临时附件 |
| 工具 | Skill 本身不要求外部工具 | 关系提交/检索与 owner 个人记忆工具 |
| 持久化 | 无 | MySQL 事件、快照、绑定、个人记忆和队列 |
| 自动任务 | 无后台任务 | Windows 计划任务监督 Gateway、MySQL、路由、投影、媒体清理和备份 |
| 对外动作 | 只生成建议文本 | 可回复当前飞书军师群；不向微信、抖音或女生本人代发消息 |

## Hermes Hooks 与工具

`runtime/goutoujunshi/__init__.py` 注册以下运行时入口：

- `pre_gateway_dispatch`：处理 `/relation`、`/relationship` 和 `/me` 命令，校验 owner、人物绑定与 profile route，注入 Skill、个人记忆和关系上下文。
- `post_llm_call`、`on_session_end` 和 session cleanup hooks：清除本轮临时媒体与会话内状态，并记录本地指标。
- `relationship_commit_turn`：在一个事务中追加本轮事件、精确草稿与快照补丁。
- `relationship_search_events`：仅在当前绑定人物内按需查询旧事件。
- `user_memory_remember/correct/forget`：维护 owner-scoped、append-only 的跨群个人事实。

关系令牌与个人记忆令牌不能互换或跨会话使用。默认 Feishu toolsets 不暴露 terminal、任意文件读取或 web search。

## 后台 Supervisor

`Run-Goutoujunshi.ps1` 由 `Hermes-Goutoujunshi` 登录计划任务启动，并循环执行：

1. 确保 Ubuntu WSL 锚点和 MySQL 容器可用。
2. 根据活动绑定对账 Hermes profile routes 与 adapter `extra.group_rules`。
3. 启动或检查 Hermes Gateway 与 Feishu 连接。
4. 重试待处理关系投影。
5. 清理超时临时媒体登记。
6. 创建并轮换本地 MySQL 日备份。

循环捕获异常并写入本地日志，下一轮继续尝试。代码中定义的重试不证明本机服务当前成功；应使用获批的健康检查确认。

## 有副作用的命令

以下入口会修改主机、外部配置、数据或服务状态，不能作为普通只读验证运行：

- `scripts/Setup-And-Start-Goutoujunshi.ps1`
- `scripts/Run-Goutoujunshi.ps1`
- `scripts/Control-Goutoujunshi.ps1`
- `scripts/wsl/Manage-Goutoujunshi-MySql.sh`
- `runtime/bootstrap.py` 中的 secrets、preflight、configure、install 命令
- `runtime/goutoujunshi_cli.py` 中的 init、import、export、reconcile 和 user-memory 写命令

执行前必须确认准确命令、目标环境、所需凭据、网络/数据库访问、副作用和停止条件。

## Prompt 引导与硬边界

`SKILL.md` 的首次建档、情绪支持、知识路由和安全限制属于模型引导。Hermes 的 owner allowlist、绑定隔离、会话签名令牌、受限 toolset 和 MySQL 事务提供更硬的运行时边界，但仍依赖宿主实现、部署配置和外部适配器正确工作。

失败策略：资料不足时保留未知；未绑定具体人物、路由未同步、绑定已归档或数据库异常时不分析、不记录；法律/医疗风险说明局限；危险请求拒绝有害部分并提供低风险替代。
