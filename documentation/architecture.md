# 架构说明

## 产品概览

狗头军师在同一仓库中维护两个运行面：可独立分发的 Codex Skill，以及面向受控本机环境的 Hermes/飞书/MySQL 私有运行时。前者提供行为与知识，后者提供消息接入、人物绑定、持久化、只读投影和运维自动化。仓库没有前端应用或公开 Web API。

## 组件

| 运行面 | 组件 | 路径 | 职责 |
| --- | --- | --- | --- |
| Codex Skill | 行为内核 | `SKILL.md` | 定义触发、建档、分析、输出与安全边界 |
| Codex Skill | 界面与知识 | `agents/`、`references/` | 提供展示元数据与按需加载的知识/策略 |
| Codex Skill | 结构验证 | `scripts/validate_skill.py`、`tests/` | 检查发布结构并维护人工/代理场景规范 |
| Hermes runtime | 插件入口 | `runtime/goutoujunshi/__init__.py` | 注册工具与 hooks，执行飞书 owner/绑定检查并注入上下文 |
| Hermes runtime | 数据层 | `database.py`、`repository.py`、`schema.sql` | 事务、人物/渠道隔离、事件、快照和个人记忆 |
| Hermes runtime | 投影与维护 | `exporter.py`、`goutoujunshi_cli.py` | 生成只读 Markdown，处理导入、导出和路由对账 |
| 本机运维 | 安装与监督 | `runtime/bootstrap.py`、`scripts/*.ps1`、`scripts/wsl/*.sh` | 安装/配置 Hermes，管理 WSL MySQL、Gateway、备份和计划任务 |
| 项目文档 | 长期知识入口 | `README.md`、`AGENTS.md`、`documentation/` | 面向用户、开发者和后续代理维护当前事实 |

## 运行拓扑

```text
Codex request
  -> SKILL.md
  -> 1-3 relevant references
  -> advice text (no persistence by the Skill itself)

Feishu owner message
  -> Hermes Feishu Adapter
  -> pre_gateway_dispatch
  -> owner + group binding + profile route checks
  -> Skill + owner memory + one relationship context
  -> model and registered tools
  -> MySQL transaction
  -> Feishu reply
  -> export_jobs -> .local/relationships/*.md (read-only projection)
```

`runtime/goutoujunshi/plugin.yaml` declares the plugin tools and hooks. `pre_gateway_dispatch` accepts only the configured owner, separates unbound general groups from bound relationship groups, and fails closed when a managed group is archived, its route is not synchronized, or the data layer raises an error. Relationship tools receive session-bound HMAC claims; owner memory tools and relationship tools use separate token kinds.

## 数据与一致性

- WSL MySQL 8 database `goutoujunshi` is the only authoritative relationship store.
- `relationship_profiles` owns person-scoped snapshots; `chat_bindings` binds one Feishu chat to one relationship; `source_channels` keeps WeChat, Douyin, Moments, offline, and other channels separate.
- `relationship_events` preserves `received`, `sent`, `draft`, `background`, `analysis`, and `correction`; corrections and user-memory changes append history instead of overwriting it.
- `relationship_commit_turn` validates the current server message source and writes events, an optional exact draft, and snapshot changes in one transaction. A changed transaction queues at most one pending export for the relationship.
- `.local/relationships/*.md` is a generated review surface. `exporter.py` writes via a same-directory temporary file, replaces atomically, and marks the projection read-only.
- `.local/archive/imports/` and its SHA256 files are immutable migration evidence. Neither location belongs in Git.

## 部署与运维

`Setup-And-Start-Goutoujunshi.ps1` is a side-effectful installation path: it can install a Python dependency, prepare protected environment values, call an external model preflight, start/apply the WSL MySQL schema, import legacy data, install/patch Hermes packages, update host configuration, and register a login scheduled task. It is not a normal development check.

The registered `Hermes-Goutoujunshi` task runs `Run-Goutoujunshi.ps1`. The supervisor ensures MySQL, reconciles routes, starts/checks the Gateway and Feishu adapter, retries projections, removes expired temporary media, and performs daily database backups. `Control-Goutoujunshi.ps1` provides explicit Start/Stop actions; Stop also terminates the Ubuntu WSL distribution after graceful Gateway and MySQL shutdown.

These are repository implementation facts, not proof of current host state. Scheduled-task status, installed Hermes code, WSL Compose configuration, database contents, Feishu admission, and model availability require separate authorized runtime checks.

## Skill 分发边界

The Skill runtime allowlist contains `SKILL.md`, `agents/`, `references/`, `scripts/`, and `assets/` when present. Project documentation and tests are not copied into a runtime-only Skill installation. Hermes deployment mirrors the allowlisted Skill into global and relationship-profile homes, while the Python plugin is installed separately.

Declared validation commands are:

```powershell
python scripts\validate_skill.py
python scripts\validate_skill.py --runtime
python -m unittest discover -s runtime\tests -v
```

The validator checks repository/Skill structure and links. Runtime unit tests use mocks and temporary directories for plugin and data contracts. Neither category proves a live MySQL, Gateway, Feishu, or external model connection.

## 信任边界与已知限制

1. User content may contain sensitive relationship, health, financial, family, and sexual information.
2. Skill instructions guide model behavior but are not program-level enforcement.
3. Hermes owner, binding, route, token, toolset, and database checks provide runtime boundaries, subject to the correctness of the installed host and adapter.
4. The model proposes replies; the system does not send messages to women through WeChat, Douyin, or other external channels.
5. References may become stale, especially legal, platform, or crisis information.
6. The repository has no telemetry backend beyond local runtime metrics/logging declared by the private deployment.

## 相关文档

- [产品定位](product.md)
- [关键流程](flows.md)
- [权限边界](permissions.md)
- [变量与秘密](variables.md)
- [知识库治理](knowledge-base.md)
- [自动化与代理边界](automation.md)
- [测试地图](tests.md)
- Local `.local/operator/HERMES_狗头军师用户手册.md`: personalized operator guide, intentionally outside the public Git baseline
- Local `.local/operator/HERMES_飞书接管故障修复复盘.md`: host-specific incident review, intentionally outside the public Git baseline
