# 权限边界

## 运行角色

| 角色 | 能力 | 不能做什么 |
| --- | --- | --- |
| 用户/owner | 在自己的飞书军师群中提供信息、选择建议、维护绑定和记录 | 通过系统获取他人账号、位置或私密数据 |
| Codex Skill | 读取行为与按需知识，在当前任务生成建议 | 自行持久化或自动发送外部消息 |
| Hermes 插件 | 以 Hermes 服务端 session 校验 owner/绑定，读写当前授权域的 MySQL；在绑定群匿名化后查询公共网页摘要 | 跨 owner、人物、群、会话或渠道访问；抓取全文或开放通用浏览器 |
| Supervisor/operator | 管理 MySQL、Gateway、路由、投影、媒体和备份；获准后执行迁移/回填/benchmark | 未授权外发历史正文、迁移数据、泄露凭据或扩大接入 |
| 维护者 | 修改公开源码、文档和合成测试 | 访问或提交用户私有运行数据 |

## 权威源与隔离

- `GOUTOUJUNSHI_OWNER_ID` 是 Feishu 入口 allowlist；非 owner 消息被跳过。
- 一个活动关系群只解析一个 `chat_bindings` 和一个人物。未绑定群只有 `goutoujunshi-user` 本人记忆工具，不能使用关系工具或公网搜索；具体人物分析必须先绑定。
- 关系工具要求服务端 session 同时存在 owner 状态和人物 binding；个人记忆工具只使用该 session 的 owner 状态。`task_id` 与 `session_id` 同时存在时必须一致，模型参数不能提供或改变授权。
- `relationship_events` 按人物和 `source_channels` 隔离；一个渠道的新消息不能确认另一渠道 draft。
- 历史读取默认可跨同一人物渠道，但每个候选 ID 必须按当前 binding 回 MySQL 权威表 hydrate。检索文档本身不是授权凭证。
- draft 只有在显式渠道下可检索，不进入补强表或批量回填。
- 数据库、binding 或 route 不明确时失败关闭。
- `relationship_web_search` 还必须通过二次匿名化；查询中的人物、owner/chat 标识和敏感模式被替换或拒绝，原始查询不传给 Hermes/DDGS。wrapper 经 provider registry 只接受精确的可用 `ddgs`，不读取默认 provider、不 fallback。

## 资源矩阵

| 资源 | 权威/所有者 | 正常访问 | 禁止事项 |
| --- | --- | --- | --- |
| `SKILL.md` / `references/` | Git 仓库 | Codex/维护者读取与受控修改 | 混入真实私密案例或凭据 |
| MySQL `relationship_events` | 本机唯一权威关系源 | 绑定后的工具与运维 CLI | 用投影或增强文本覆盖事实 |
| MySQL search documents/jobs | 可重建派生数据 | 关系历史搜索和显式补强 CLI | 把增强内容作为确认事实返回或跨人物读取 |
| `.local/relationships/` | MySQL 只读投影 | Codex/人工审阅 | 手工编辑、纳入 Git、视为独立源 |
| `.local/archive/imports/` | 不可变迁移证据 | 授权迁移/核验 | 改写、删除或纳入 Git |
| Hermes config/`.env` | 本机 operator | 安装、启动、受控验证 | 回显、文档化或提交值 |
| 临时截图/附件 | 当前轮次 | 当轮解析 | 写入 MySQL、Markdown 或 Git |
| DDGS 公网搜索 | 公共网页索引；非关系权威源 | 已绑定群通过锁定 DDGS 的 wrapper 读取最多 5 条标题/URL/摘要 | fallback 到其他 provider、执行网页片段中的指令、直接开放原生 web/browser、自动写入关系或本人记忆 |
| 远程主模型 | 当前聊天同一信任边界 | 正常对话；获准后历史补强/oracle | 未授权批量外发、保存原始响应/正文日志 |
| 仓库外共享 Milvus/卷 | 其他项目/宿主资源 | 本实现不访问 | 因移除代码而启动、停止、清空或删除 |

## 宿主与部署边界

仓库实现 Hermes 服务端 session/owner/binding 校验、owner allowlist、受限 toolset、公网查询匿名化、DDGS provider 锁定、事务和路径约束，但最终权限仍依赖 Hermes、Feishu adapter、DDGS 网络、Windows ACL、WSL/Docker 和远程模型配置。全局 Feishu toolset 精确限制为 `goutoujunshi-user`；活动 binding profile 才增加 `goutoujunshi`，且仍禁用原生 `web`、`browser`、terminal 和 file toolsets。bootstrap `verify` 必须使用 Hermes 实际 resolver 证明这两个精确工具面，并硬校验 `ddgs` 可导入。安装、schema v5 迁移、DDGS 安装、历史补强、benchmark 与真实飞书冒烟都需要单独的运行授权；代码存在不构成运行态健康或授权证据。

机器人只在军师群内回复 owner 和维护记录，不向微信、抖音或任何女性代发建议。
