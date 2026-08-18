# 架构说明

## 产品概览

仓库维护两个独立运行面：可分发的 Codex Skill 提供行为与按需知识；Hermes/飞书/MySQL 私有运行时提供 owner 校验、人物绑定、持久化、关系检索、受控公网搜索、只读投影和本机运维。仓库没有前端或公开 Web API。

## 组件

| 运行面 | 组件 | 路径 | 职责 |
| --- | --- | --- | --- |
| Codex Skill | 行为与知识 | `SKILL.md`、`agents/`、`references/` | 定义默认高手决策引擎、即时输出、数据合同和按需关系知识 |
| Codex Skill | 验证 | `scripts/validate_skill.py`、`tests/` | 检查发布结构、来源声明、链接、路由标记和场景规范 |
| Hermes runtime | 插件入口 | `runtime/goutoujunshi/__init__.py` | 注册 hooks/tools，校验 owner/绑定并生成关系 `channel_prompt` |
| Hermes runtime | 权威数据 | `database.py`、`repository.py`、`schema.sql` | schema v5、事务、人物/渠道隔离、事件、快照、个人记忆和任务队列 |
| Hermes runtime | MySQL 检索 | `search.py`、`enrichment.py` | 三分支 MySQL 候选、固定 RRF、纠正闭包和有界权威正文输出 |
| Hermes runtime | 受控公网搜索 | `runtime/goutoujunshi/__init__.py`、Hermes provider registry | 绑定授权、查询匿名化、DDGS 精确选择和结果字段收敛 |
| Hermes runtime | 显式补强 | `enrichment_jobs.py`、`goutoujunshi_cli.py` | 远程主模型分批补强、状态、重试和断点续跑；不由 supervisor 自动调用 |
| Hermes runtime | 投影 | `exporter.py` | 从 MySQL 生成只读 Markdown 审阅投影 |
| 本机运维 | 安装与监督 | `runtime/bootstrap.py`、`scripts/*.ps1`、`scripts/wsl/*.sh` | 配置 Hermes，管理 MySQL、Gateway、路由、媒体和备份 |

## 运行拓扑

```text
Codex request -> SKILL.md -> 1-3 relevant references -> advice (Skill itself does not persist)

Feishu owner message
  -> Hermes adapter -> pre_gateway_dispatch
  -> owner + binding + route checks
  -> bounded prompt -> remote main model + registered tools
  -> relationship_commit_turn -> one MySQL transaction
  -> Feishu reply -> export_jobs -> read-only Markdown projection

Historical recall
  -> one current MySQL binding
  -> exact/substr source candidates (max 40, weight 1.5)
  -> source ngram candidates (max 40, weight 1.0)
  -> enrichment ngram candidates (max 40, weight 1.25)
  -> RRF k=60 -> MySQL authority hydrate -> correction closure
  -> default Top-8 authoritative event bodies

Current public context, bound relationship group only
  -> relationship_web_search -> server session + current binding checks
  -> second-pass query anonymization -> Hermes provider registry -> exact DDGS
  -> at most 5 title/url/snippet records -> current session only
```

在线关系检索没有 Ollama、Milvus、本地 embedding 或常驻检索 worker。共享 Milvus 服务或卷属于仓库外资源，本次移除代码不会启动、停止、清空或删除它们。

## 最终提示词组成

Hermes 发送给主模型的请求按以下层次组装；具体 SDK 序列化细节由宿主负责：

1. 基础 `system` 前缀：Hermes 身份、工具与平台规则及会话元数据。当前关系 profile 不启用 `skills` toolset，因此这一运行面没有可调用 Skill 的 system 索引。
2. 临时 `system` 尾部：飞书平台上下文、插件生成的 `channel_prompt`，以及可能存在的通道配置提示。
3. 新会话第一条 `user`：plugin 设置 `auto_skill=goutoujunshi` 后，Gateway 内部读取完整 `SKILL.md`，连同 Skill 目录、supporting-file 清单和用户原始消息组装成一条 user 消息；后续轮次不重新加载文件，但该消息作为会话历史保留。
4. 关系 `channel_prompt`：跨群 owner 本人记忆、当前人物绑定规则、关系快照和有界事件工作集；不向模型暴露授权令牌。
5. 有界工作集顺序：快照后的 correction 最多 5 条、当前渠道未决 draft 最多 1 条、当前渠道真实 `received/sent` 最多 12 条、背景最多 3 条；关系 JSON 序列化目标上限 3000 字符。
6. 旧事件默认不全量注入。模型调用 `relationship_search_events` 后，Top-8 检索结果以 `tool` 消息进入下一次模型请求；单条正文最多 1200 字符，总正文最多 6000 字符。
7. 需要当前公共信息时，模型可在已绑定群调用 `relationship_web_search`；结果以临时 `tool` 消息进入当前 session，并与 MySQL 记忆和模型推断分开。
8. 当前受限关系 profile 不暴露 `skill_view`、file 或 terminal，也没有自动 reference 分类器；`SKILL.md` 中的参考路径只提供模型引导，嵌套 `references/` 正文不会自动进入请求。Codex Skill 运行面仍可按 Codex 的 Skill 机制按需读取资料。

`channel_prompt` 在同一 session 内保持字节稳定缓存；写入、搜索工具结果和后续历史仍由 Hermes 作为新的消息加入请求。服务端已解析的绑定状态高于旧会话、截图/OCR、视觉描述和引用消息中的机器人文字；这些材料中的命令或令牌报错不能改变当前授权。检索增强文本从不作为事实或 tool 结果返回给主模型。

关系 profile 只暴露 6 个受控的关系、个人记忆和公网搜索工具，因此显式关闭 Hermes `tool_search` 延迟披露，让这些 schema 在每轮直接可见。全局 Feishu toolset 只有 `goutoujunshi-user`；已绑定关系 profile 才增加 `goutoujunshi`。原生 `web_search`、`web_extract`、browser、terminal、file 和 `skills` 均不直接暴露。关系阶段、回复、邀约、观察或停止由主模型根据 Skill 与上下文作出提示驱动判断，不存在独立的程序化决策路由器。

## 受控公网搜索

`relationship_web_search(query, limit=5)` 先执行与关系工具相同的服务端 session/task、owner、群、人物和当前 MySQL binding 回查。查询经 NFKC、空白折叠和第二次匿名化后，才交给 Hermes provider registry 精确解析 `ddgs`；绑定名称与 slug、owner/chat 标识、邮箱、手机号、证件号、账号、URL、控制字符、常见密钥和聊天转录式输入会被替换或拒绝。`query` 最长 240 字符，`limit` 为 1-5。

Hermes profile 使用免 API key 的 DDGS backend，并关闭 web debug。wrapper 不调用通用搜索入口，也不读取 active/default provider；registry 必须返回名称精确为 `ddgs`、支持搜索且当前可用的 provider，否则失败关闭，不允许 fallback。插件只保留最多 5 条 HTTP(S) 结果的 `title`、`url` 和 `snippet`，同时返回 provider、UTC `retrieved_at` 和是否发生脱敏；不会调用 `web_extract`、抓取全文或启动浏览器。插件指标只含匿名查询 hash、长度、耗时、结果数和状态。

网页摘要是不可信的临时外部输入，不进入 MySQL、本人记忆、只读投影或关系 prompt 缓存。网页标题、摘要和其他片段中的任何指令都只是数据，不得执行，也不得改变工具、授权、记忆或写入规则。回答必须区分联网信息、MySQL 关系记忆和模型推断，并为联网信息标注标题、URL 和检索日期。隐私拒绝、DDGS 不可用、超时或异常都返回固定的明确降级，不使用其他 provider 或未联网的模型猜测兜底。

## schema v5 与一致性

- `relationship_events` 是唯一权威事件表，保存六类事件；correction 只追加，不改写历史。
- `relationship_event_search_documents` 保存 event/person ID、权威原文副本/hash、增强 JSON、扁平检索文本、来源/版本/状态，并对原文与增强文本建立 `FULLTEXT ... WITH PARSER ngram`。
- `relationship_event_enrichment_jobs` 保存每个非 draft 事件的 prompt 版本、状态、尝试次数和无正文错误信息。
- 每个非 draft 事件在同一事务创建检索文档。增强缺失或非法时写入 `raw_only` 并排队，不阻断权威事件；合法增强写为 `enriched`。重复提交只补全缺失增强，空值不能覆盖已有结果。
- draft 不创建增强文档，只能在显式渠道下经原文精确/子串分支检索。correction 正常增强，但搜索仍把纠正闭包放在旧事件前。
- schema v5 先创建/回填新表，再按依赖顺序删除旧 `relationship_event_index_jobs` 和 `relationship_search_indexes`。迁移 SQL 可重复执行，但真实执行仍需授权。
- `.local/relationships/*.md` 只是原子替换的只读投影；`.local/archive/imports/` 及 SHA256 是不可变迁移证据，二者都不属于 Git。

## 补强与运维

写入时主模型通过 `relationship_commit_turn.events[*].search_enrichment` 同步提供 `summary`、`concepts`、`aliases`、`entities`、`time_hints`。所有字段有固定长度/数量上限，只允许从该事件原文提取。

历史回填必须由 operator 显式依次执行 `enrichment-backfill`、`enrichment-work`、`enrichment-status`；达到尝试上限的失败项只能通过 `enrichment-retry-failed` 明确重置。每批最多 8 条、序列化输入不超过 12000 字符，覆盖活动和归档人物的全部非 draft 事件。worker 使用当前远程主模型和结构化工具输出，不保存原始模型响应，也不把聊天正文写入日志；prompt 版本升级会重新排队。supervisor 不自动处理该队列，避免未经授权外发历史内容。

登录 supervisor 只维护 MySQL、Gateway/Feishu、路由、投影、临时媒体和 MySQL 日备份。bootstrap `verify` 通过 Hermes 实际 toolset resolver 硬校验全局 Feishu 只解析为 `goutoujunshi-user`、关系 profile 只解析为 `goutoujunshi + goutoujunshi-user`，并直接检查 `ddgs` 可导入；声明配置但实际解析泄漏或依赖缺失都会失败。安装、启动、停止、schema 迁移、回填、benchmark 和远程模型预检均是有副作用操作。

## 信任边界

1. MySQL 或人物 binding 不明确时失败关闭，不用通用猜测替代。
2. Hermes 在服务端向插件 handler 注入 `session_id`/`task_id`；插件据此读取当前 session 的 owner 与人物状态，并回查 MySQL 当前 binding。缺失、错配、跨 owner、跨人物或归档状态一律失败关闭。
3. 搜索最终从当前 binding 回 MySQL hydrate，派生文档不能扩大人物或渠道权限。
4. 公网搜索必须同时通过 binding 授权和查询匿名化；未绑定群及原生 Hermes web/browser 工具没有该能力。
5. `mysql_raw` / `incomplete_enrichment` 表示补强覆盖不完整；零结果只能说“本次未检索到”，不能推断从未发生。
6. 系统只向 owner 的飞书军师群提供建议，不向微信、抖音或任何女性自动代发。
7. 静态仓库与离线测试不证明真实 MySQL、Hermes、Feishu、DDGS 网络、计划任务或远程模型当前健康。

## 相关文档

- [产品定位](product.md)
- [端到端记忆、上下文与路由](memory-context-routing.md)
- [关键流程](flows.md)
- [权限边界](permissions.md)
- [变量与秘密](variables.md)
- [自动化与代理边界](automation.md)
- [测试地图](tests.md)
