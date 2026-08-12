# 权限边界

## 运行角色

| 角色 | 能力 | 不能做什么 |
| --- | --- | --- |
| 用户/owner | 提供信息、选择建议、在自己的飞书军师群中维护绑定与记录 | 不通过系统获得他人账号、位置或私密数据 |
| Codex Skill | 读取行为与按需知识，在当前任务生成建议 | 不自行持久化或自动发送外部消息 |
| Hermes 插件 | 为配置 owner 校验飞书上下文，读写当前授权域内的 MySQL 数据 | 不跨 owner、人物、群、会话或渠道使用关系令牌 |
| Supervisor/operator | 管理本机 MySQL、Gateway、路由、投影和备份 | 未经授权不得泄露凭据、真实关系数据或扩大外部访问 |
| 维护者/贡献者 | 修改公开源码、文档和合成测试 | 不应访问或提交使用者的私有运行数据 |

## 权威源与隔离

- `GOUTOUJUNSHI_OWNER_ID` 是飞书入口 allowlist 的项目侧身份边界；非 owner 消息在插件入口被跳过。
- 一个活动飞书关系群解析为一个 `chat_bindings` 记录和一个人物档案。未绑定群只可加载 owner 本人记忆；具体人物、截图、回复和关系判断必须先绑定。
- 关系工具使用包含 chat、relationship 和 session claims 的 HMAC 令牌；个人记忆工具使用独立 owner/session 令牌。两种令牌不可互换。
- `relationship_events` 按人物和 `source_channels` 隔离；一个渠道的新消息不能确认另一个渠道的草稿。
- `user_memory_events` 只保存脱离具体人物仍成立的 owner 本人事实。第三方信息、关系判断、截图路径和敏感值不得进入个人记忆。
- 数据库或绑定不明确时失败关闭，不退回通用记忆猜测。

## 资源矩阵

| 资源 | 权威/所有者 | 正常访问 | 禁止事项 |
| --- | --- | --- | --- |
| `SKILL.md` 与公开参考资料 | 版本控制仓库 | Codex/维护者可读，贡献流程可改 | 混入真实私密案例或凭据 |
| MySQL `goutoujunshi` | 本机运行时 | 绑定后的受限工具和运维 CLI | 直接以 Markdown 代替数据库写入 |
| `.local/relationships/` | MySQL 生成投影 | Codex/人工只读复核 | 编辑、纳入 Git、视为独立事实源 |
| `.local/archive/imports/` | 迁移证据 | 授权迁移/核验流程 | 改写、删除或纳入 Git |
| Hermes config/`.env` | 本机 operator | 安装、启动和受控验证 | 回显、文档化或提交值 |
| 临时截图/附件 | 当前处理轮次 | 仅当轮解析 | 写入数据库、Markdown 或 Git |
| 外部模型/飞书 | 外部服务 | 仅按已配置 host 和明确授权 | 把仓库定义当作在线健康证明 |

## 宿主与部署边界

仓库实现使用 HMAC 签名、owner allowlist、受限 toolset、事务和路径约束，但没有自建账号系统、行级安全服务或公开 API 网关。最终权限还依赖 Hermes、Feishu adapter、Windows ACL、WSL/Docker 和外部模型配置。安装脚本可能修改这些宿主资源，因此运行它不属于文档或单元测试权限。

机器人只在军师群内回复用户和维护记录；它不得向微信、抖音或其他外部渠道代发建议。
