# 飞书关系消息如何进入 Skill 并形成决策

本文解释一条飞书消息如何进入 Wing-Dog 的 Hermes 私有运行时、完整 `SKILL.md` 何时加载、谁决定调用哪个工具、关系建议最终由谁作出，以及这些环节分别消耗什么上下文和工具轮次。

证据边界是本迁移分支、plugin `1.7.0` 和已锁定的 Hermes `0.20.4` 源码。本文是源码与静态配置合同说明，不代表 MySQL、Hermes Gateway、飞书、DDGS 或远程模型此刻健康。文中的人物和消息均为虚构示例。

## 先直接回答核心问题

飞书消息并不是先经过一个语义分类器，再“路由到恋爱 Skill”。真实过程是：

```text
Feishu MessageEvent
  -> chat_id 命中 gateway.profile_routes，选择 goutoujunshi profile
  -> plugin pre_gateway_dispatch 校验 owner、群和人物 binding
  -> plugin 每轮设置 event.auto_skill = "goutoujunshi"
  -> 仅新 session：Hermes Gateway 读取完整 SKILL.md，拼进首条 user 消息
  -> plugin 注入 owner 记忆和当前人物的 channel_prompt
  -> 主模型同时看到当前输入、Skill、上下文和 6 个业务工具 schema
  -> 主模型自己判断“回复 / 邀约 / 观察 / 停止”以及要不要调用工具
  -> 一旦模型调用工具，服务端代码再执行授权、检索、匿名化和事务
  -> 主模型根据 tool result 继续推理，最后回复当前飞书群
```

这里最容易混淆的三个事实是：

1. **profile 路由不是 Skill 路由。** `chat_id -> goutoujunshi profile` 只决定使用哪套 Hermes 配置、插件和工具面。
2. **Skill 激活是确定性的，关系决策不是。** 新 session 会确定性加载完整 `SKILL.md`；具体建议和工具选择由同一个主模型根据提示语义决定。
3. **当前没有可工作的 references 按需读取链路。** 关系 profile 不暴露 `skill_view`、file 或 terminal；`SKILL.md` 里的 1-3 份参考资料表只是模型能看见的路径指引，不会自动把正文加载进请求。

## 一、完整调用链

### 1. 飞书事件先选择 Hermes profile

`runtime/goutoujunshi_cli.py reconcile-config` 从 MySQL 的活动 `chat_bindings` 生成 `gateway.profile_routes`：

```text
platform = feishu
chat_id  = 当前关系群
profile  = goutoujunshi
```

Gateway 在构造消息来源时按平台和 `chat_id` 匹配该表，把 `source.profile` 标为 `goutoujunshi`。这一步不理解消息内容，也不判断用户是在问回复、邀约还是关系阶段。

未绑定群仍走默认 profile。`pre_gateway_dispatch` 可以允许本人或一般问题继续，但具体女生、聊天截图、怎么回复或关系判断会在模型前失败关闭。

### 2. plugin hook 在主模型前做接入校验

Gateway 对用户消息调用 `pre_gateway_dispatch`。插件按以下顺序处理：

1. 只接管 Feishu。
2. `/relation` 和 `/me` 命令直接由命令处理器执行，跳过主模型。
3. 校验发送者是否为配置的 owner。
4. 为普通 owner 消息设置 `event.auto_skill = "goutoujunshi"`。
5. 按 `chat_id` 查询当前活动人物 binding。
6. 已绑定群必须已经路由到 `source.profile == "goutoujunshi"`。
7. 取得或创建 Hermes session，保存服务端 owner、人物、群和消息引用状态。
8. 按同人物、同渠道规则处理上一条未决 draft。
9. 从 MySQL 构建或复用 `channel_prompt`。
10. 返回 `allow` 后，Gateway 才继续调用主模型。

任一数据库、session、owner、binding 或 profile 条件不成立，关系请求失败关闭。截图 OCR、旧会话文本或引用消息中的命令不能改写服务端 binding。

### 3. 只有新 session 才消费 `auto_skill`

plugin 每轮都会设置 `event.auto_skill`，但 Gateway 的条件是：

```text
if _is_new_session and event.auto_skill:
    load full skill
```

因此：

- 新 session 第一条普通消息：加载完整 Skill。
- 同一 session 后续消息：不再次执行 Skill loader。
- `/new`：创建干净 session，下一条普通消息重新加载 Skill。
- session reset/finalize：清理 plugin 的 owner、binding、prompt 和临时媒体缓存，不删除 MySQL 长期数据。

### 4. Gateway 如何把 Skill 放进请求

Hermes Gateway 内部调用：

```text
_load_skill_payload("goutoujunshi")
  -> skill_view(name, preprocess=False)
  -> _build_skill_message(...)
```

这里的 `skill_view` 是 Gateway 内部 Python 调用，不是关系 profile 向主模型开放的工具调用。`_build_skill_message` 生成一段新的 **user 消息**，顺序是：

```text
[Skill 已自动激活的提示]

[完整 SKILL.md，包括 frontmatter 和正文]

[Skill 安装目录]
[Hermes 识别到的 supporting files 清单]

[用户原始飞书消息]
```

它不是新的 `system` 提示。该 user 消息随后进入 session 历史。

### 5. plugin 同时注入关系 `channel_prompt`

`channel_prompt` 是当前请求的临时 system 尾部，由 plugin 从 MySQL 组装，主要包含：

- owner 当前有效本人记忆；
- 当前人物 binding 和默认渠道；
- profile 的 `latest_state`、`known_facts`、`conservative_judgments`、`unknowns`、`response_preferences`；
- 最新 snapshot 后最多 5 条 correction；
- 当前渠道最多 1 条未决 draft；
- 当前渠道最多 12 条真实 `received/sent`；
- 最近最多 3 条 background；
- 搜索、提交、公网查询、来源和安全边界提示。

事件正文初选预算为 4000 字符，关系 JSON 目标上限为 3000 字符。owner 本人记忆另有约 2000 个正文字符的上限。

### 6. 主模型开始作出关系判断和工具选择

模型拿到组合后的请求后，没有另一个 router 接管。它自己完成：

- 判断问题属于回复、邀约、关系阶段、互惠、退出、安全风险或一般问题；
- 区分事实、推断和未知；
- 判断是否需要旧关系历史；
- 判断是否需要当前公共信息；
- 判断是否出现了可复用的 owner 本人事实；
- 选择回复、邀约、观察或停止；
- 决定是否调用搜索、记忆或提交工具。

`SKILL.md` 的“每次分析”和“按需加载”章节只是提供模型引导。当前没有程序级的关系阶段分类器、决策 enum、规则引擎、独立 router model 或结构化 decision record。

### 7. 工具被选择后才进入确定性代码

当前关系 profile 每轮直接暴露 6 个 schema：

1. `relationship_commit_turn`
2. `relationship_search_events`
3. `relationship_web_search`
4. `user_memory_remember`
5. `user_memory_correct`
6. `user_memory_forget`

`tools.tool_search: false` 的含义是这些 schema 不延迟披露，而不是强制模型调用。模型选择工具后，handler 才执行服务端 session/task、owner、群、人物和当前 MySQL binding 回查。

工具成功返回后，结果以 `tool` 消息进入下一次模型请求。模型可以继续调用工具或形成最终回答。

## 二、六种“路由”不能混为一谈

| 名称 | 解决的问题 | 决策者 | 确定性 | 输出 |
| --- | --- | --- | --- | --- |
| profile 路由 | 这个 `chat_id` 使用哪套 Hermes profile | Gateway `profile_routes` | 是 | `source.profile` |
| 人物 binding | 这个群属于哪个 owner 和人物 | plugin + MySQL | 是 | 单一活动 relationship |
| Skill 激活 | 新 session 是否加载 `goutoujunshi` | plugin `auto_skill` + Gateway | 是 | 首条完整 Skill user 消息 |
| 关系决策 | 应回复、邀约、观察还是停止 | 主模型 | 否，提示驱动 | 自然语言建议 |
| 工具选择 | 是否搜索、联网、记忆或提交 | 主模型 | 否，提示驱动 | function call |
| 数据执行 | 搜索哪些事件、能否联网、写入什么 | plugin/repository/MySQL | 是 | 有界 tool result 或错误 |

因此，“消息已经进入 Skill”和“模型已经选中一个具体关系决策”是两件事。前者有明确代码条件，后者目前没有独立可观测的路由结果。

## 三、首轮和后续轮次实际看到什么

### 1. 新 session 第一轮

```text
Request 1
├─ stable system
│  ├─ Hermes 身份、平台和工具规则
│  └─ session 等宿主元数据
├─ ephemeral system tail
│  ├─ Feishu 平台上下文
│  └─ plugin channel_prompt
├─ user
│  ├─ Skill activation note
│  ├─ 完整 SKILL.md
│  ├─ Skill directory + 3 个 supporting-file 条目
│  └─ 用户原始消息
└─ tools
   └─ 6 个业务工具 schema
```

当前关系 profile 没有 `skills` toolset，因此基础 system 不会生成可调用 Skill 的 available-skills 索引。Skill 正文来自 `auto_skill` 的首轮 user 注入，而不是系统索引。

若消息包含截图，Hermes 的视觉处理还会产生当轮文字描述和说话人线索。图片路径和二进制不进入关系事件、owner 记忆或只读投影。

### 2. 同一 session 后续轮次

```text
Request N
├─ stable system
├─ ephemeral system tail
│  └─ 同 session 字节稳定的 channel_prompt
├─ conversation history
│  ├─ 第一轮包含完整 SKILL.md 的 user 消息
│  ├─ 后续 user / assistant 消息
│  └─ 已发生的 tool calls / tool results
├─ current user message
└─ 同样的 6 个业务工具 schema
```

Skill loader 没有再次读文件，但完整 Skill 仍作为历史参与后续请求，直到 session reset 或上下文压缩处理它。所谓“只加载一次”只表示 loader 执行一次，不表示后续 API 请求不再携带其历史内容。

### 3. 缓存能做什么，不能做什么

plugin 用 `session_id + owner_id + relationship_id` 缓存 `channel_prompt`，同一 session 在 binding 不变时复用完全相同的字节。这样有利于 Hermes agent cache 和模型前缀缓存。

缓存不会让这些内容从逻辑上下文消失：

- `channel_prompt` 仍属于每次模型请求；
- 第一轮 Skill user 消息仍在会话历史；
- 6 个工具 schema 仍直接可见；
- provider 缓存可能减少重复计算或计费，但不等于释放上下文窗口；
- 当前配置在 48000 tokens 开始主动裁剪，并在 64000 tokens 触发压缩流程；压缩后保留什么由 Hermes context compressor 决定。

## 四、为什么当前 references 并没有被路由进来

### 1. `SKILL.md` 里确实有知识路由表

Skill 会提示模型：一句话回复优先实战话术、截图优先在线关系、投入失衡优先互惠/退出、安全问题优先法律与危机资料。这解释了“应该读什么”，但不提供读取能力。

### 2. Hermes loader 没有自动内联 reference 正文

`skill_view` 加载主 Skill 时只把 `SKILL.md` 正文放进 `content`，supporting files 只作为路径清单。它不会根据用户消息自动挑选并读取 1-3 个文件。

Hermes `0.20.4` 对普通本地 Skill 的 reference 清单使用 `references/*.md` 顶层枚举，不递归进入子目录。当前包的静态结果是：

| 项目 | 数量 |
| --- | ---: |
| `references/` 下 Markdown 总数 | 43 |
| 顶层 Markdown | 1 |
| `knowledge/`、`practical/` 中的嵌套 Markdown | 42 |
| 正常 Skill loader 最终列出的 supporting files | 3 |

这 3 个条目是顶层第三方声明、hero 资源和校验脚本；42 份关系知识正文没有出现在 loader 的 linked-files 清单里。

### 3. 主模型也没有读取这些文件的工具

关系 profile 的 Feishu toolsets 精确为 `goutoujunshi` 和 `goutoujunshi-user`。它没有：

- `skills`，所以没有 `skill_view` / `skills_list`；
- `file`，所以没有任意文件读取；
- `terminal`，所以不能通过命令读取路径。

所以当前真实状态不是“参考资料按需加载”，而是：

```text
完整 SKILL.md 已加载
SKILL.md 中能看到 reference 路径
reference 正文没有进入请求
模型没有工具继续读取正文
```

Codex Skill 运行面仍可以按照 Codex 的 Skill 机制读取这些资料；这里的问题只针对受限的 Hermes 关系 profile。

### 4. 当前 verify 字段不能证明 `skill_view` 可用

`runtime/bootstrap.py verify` 输出的 `skill_view_enabled` 目前只检查 `agent.disabled_toolsets` 中是否没有 `skills`。但 Hermes 平台工具面是 allowlist 解析：`skills` 没被列入 `platform_toolsets.feishu` 时，即使它也没出现在 disabled list，最终仍不会暴露。

同一个 verify 流程还会检查实际 resolved toolsets 是否精确为两个业务 toolset；这反而证明当前受限工具面不包含 `skills`。因此 `skill_view_enabled` 这个字段名不能作为参考资料可读性的证据。

## 五、具体关系决策到底怎么产生

### 1. 当前是一层主模型决策，不是多级决策树

主模型同时接收：

- 完整 `SKILL.md`；
- 用户本轮消息和 session 历史；
- owner 本人记忆；
- 当前人物有界关系上下文；
- 6 个工具 schema；
- 如果已经搜索，则还有 tool result。

然后在同一个模型循环中生成自然语言、function call 或两者。没有独立模块先输出：

```json
{
  "scene": "reply",
  "relationship_stage": "observing_reciprocity",
  "action": "invite",
  "confidence": 0.82
}
```

上述结构当前不存在，所以日志可以看到工具调用和耗时，却不能直接回答“本轮命中了 Skill 的哪条决策分支”。

### 2. 模型引导与硬约束的边界

| 层次 | 能保证什么 | 不能保证什么 |
| --- | --- | --- |
| profile/binding | 群只能进入指定 profile 和单一人物 | 建议一定正确 |
| Skill/channel prompt | 告诉模型如何判断、何时搜索和提交 | 模型一定遵循 |
| 直接工具 schema | 模型每轮都看得到 6 个工具 | 模型一定调用 |
| handler 授权 | 跨 owner、跨群、跨人物调用失败关闭 | 模型一定选择正确参数语义 |
| MySQL 事务 | 类型、渠道、去重、事件/索引/export 一致 | 截图作者一定识别正确 |
| 公网 wrapper | 匿名化、DDGS 锁定、5 条结果上限 | 网页一定正确或完整 |

### 3. 一个“怎么回”请求的模型路径

例如用户在已绑定群发送来源明确的微信截图并问“怎么回”：

```text
主模型第一次请求
  -> 根据 Skill 和 channel_prompt 判断是聊天回复场景
  -> 按 prompt 要求调用 relationship_search_events

关系搜索 tool result
  -> 进入主模型第二次请求
  -> 模型结合当前截图和旧事件形成回复方向
  -> 如出现用户本人新事实，可调用 user_memory_remember
  -> 调用一次 relationship_commit_turn 写 received + 精确 draft + 可选 snapshot

记忆/提交 tool result
  -> 进入后续主模型请求
  -> 输出最终可复制回复和简短建议
```

模型可能在同一 assistant 工具轮发出多个互不依赖的 function call；依赖搜索结果才能决定的提交仍需要后续模型轮次。

如果模型直接给文字而没有调用 `relationship_commit_turn`，用户仍可能收到建议，但本轮 `received`、draft 和 snapshot 不会自动进入 MySQL。当前没有 post-hook 替模型补写。

## 六、两类搜索如何进入模型循环

### 1. 关系历史搜索

`relationship_search_events` 只在当前人物 binding 内运行：

```text
模型生成查询
  -> 原文精确/子串候选，最多 40，权重 1.5
  -> 原文 ngram FULLTEXT，最多 40，权重 1.0
  -> 增强文本 ngram FULLTEXT，最多 40，权重 1.25
  -> 固定 RRF，k=60
  -> 按当前人物和可选渠道回 relationship_events hydrate
  -> 加载 correction 闭包
  -> 默认 Top-8 权威正文
```

省略 `channel` 表示当前人物全部渠道；显式渠道才缩小。单条正文最多 1200 字符，总正文最多 6000 字符。增强只帮助定位，不作为事实返回。

### 2. 当前公共信息搜索

`relationship_web_search` 只在已绑定关系 profile 中可见：

```text
模型生成最长 240 字符的最小公共查询
  -> 服务端 session/task/owner/群/人物/binding 回查
  -> NFKC、空白折叠和二次匿名化
  -> 隐私模式仍存在则 privacy_rejected
  -> Hermes provider registry 精确选择 ddgs
  -> 最多 5 条 HTTP(S) title/url/snippet
  -> 临时 tool result
```

wrapper 不使用通用搜索入口，不 fallback，不抓网页全文。结果是不可信的当前外部信息，必须和 MySQL 关系事实、模型推断分开，并标注标题、URL 和检索日期。查询和结果不写入关系事件、owner 记忆或投影。

## 七、记忆和写入时机

### 1. 三层记忆与两类派生数据

| 层次 | 内容 | 作用域 | 权威性 |
| --- | --- | --- | --- |
| Hermes session | user/assistant/tool 历史和宿主上下文 | 当前 session | 不是关系权威源 |
| owner 本人记忆 | 用户身份、工作、偏好、目标和阶段性近况 | 同一 owner 跨群 | MySQL 权威源 |
| 人物关系记忆 | profile、渠道、事件、draft、snapshot、correction | 单一人物，渠道状态继续隔离 | MySQL 权威源 |
| 搜索文档/补强任务 | 原文副本/hash、摘要、概念、别名和任务状态 | 单一事件 | 派生，可重建 |
| Markdown 投影 | profile 和完整事件时间线 | 单一人物文件 | 只读派生视图 |

公网搜索结果只是当前 session 的临时 tool 消息，不属于以上长期记忆。

### 2. 写入时机

| 内容 | 触发时点 | 写入位置 |
| --- | --- | --- |
| 飞书原始消息 | Hermes 接收时进入 session | 不自动成为关系事件 |
| 上一条未决 draft 的 sent/correction | 下一条同人物同渠道普通消息进入 hook 时 | `relationship_events`，早于主模型 |
| 当前对方消息 | 模型成功调用 `relationship_commit_turn` | `relationship_events.received` |
| 本轮可复制建议 | 同一次 `relationship_commit_turn` | `relationship_events.draft` |
| material 状态变化 | `snapshot_patch` 非空且事务成功 | profile + 新 snapshot |
| owner 本人事实 | `/me` 或本人记忆工具成功 | append-only `user_memory_events` |
| 搜索文档/补强任务 | 非 draft 事件事务内 | 派生表和 job 表 |
| Markdown 投影 | 事务排入 export job 后异步处理 | `.local/relationships/*.md` |
| 公网查询和网页摘要 | 不持久化 | 仅当前 session tool 消息 |
| 截图文件和路径 | 当轮临时使用后清理 | 不进入长期数据 |

`relationship_commit_turn` 把本轮事件、一个精确 draft、可选 snapshot patch、非 draft 检索文档和 export job 放在一个 MySQL 事务中。入口 hook 对上一 draft 的确认是更早的独立事务，不会因后续模型失败而回滚。

## 八、性能地图

以下是静态源码能够证明的成本位置。字符数不是 provider token 数，源码也不能证明真实线上延迟。

| 环节 | 发生频率 | 当前静态基线/指标 | 潜在优化收益 | 主要代价或风险 |
| --- | --- | --- | --- | --- |
| 基础 system | 每次模型 API 请求 | Hermes 生成；当前关系 profile 无 Skill index | 缩短固定前缀 | 可能丢失宿主约束 |
| 首轮 Skill user 消息 | 每个新 session 构建一次，随后作为历史参与请求 | `SKILL.md` 4885 字符；按本机路径渲染约 5965 字符，不含原始消息和 channel prompt | 精简 Hermes 专用 Skill 可降低首轮和长期历史占用 | 规则删减会降低判断和安全一致性 |
| supporting-file 清单 | 新 session 一次，随后留在历史 | 当前只列 3 项；42 个嵌套 reference 未列出 | 删除无用清单可小幅减负；修复读取链路可提升知识质量 | 开放文件能力会扩大权限面 |
| `channel_prompt` | 每次请求 | `prompt_chars`、`prompt_reused`；关系 JSON 目标 <= 3000 字符，owner 正文约 <= 2000 字符 | 更精确的工作集可减 token | 裁剪过度会丢关键关系事实 |
| 6 个工具 schema | 每次请求 | `tool_search=false`，全部直接可见 | 延迟披露或场景化工具面可减 schema token | 模型可能再次漏掉搜索/提交工具 |
| 关系搜索 | 被模型选择时 | `relationship_search.duration_ms`、候选数和结果数 | 改善查询或减少无效搜索轮 | 搜索不足会漏旧事件 |
| 公网搜索 | 被模型选择时 | `relationship_web_search.duration_ms`、状态、结果数 | 减少不必要联网与等待 | 不搜索会使用过时公共信息 |
| 模型工具循环 | 每个 function-call round | `tool_calls`、`tool_rounds`、`api_duration_ms` | 合并无依赖调用、避免空转可直接降延迟 | 过度合并会让模型在缺少 tool result 时提前提交 |
| 截图视觉处理 | 有附件时 | `image_count`、视觉处理 duration 指标 | 只处理必要图片、控制并发 | 跳过会失去作者和原文证据 |
| 上下文压缩 | 长 session | proactive 48000 tokens，threshold 64000 tokens | 更早压缩可控制窗口 | 摘要可能损失 Skill 或关系细节 |

### 当前可观测和不可观测

现有指标可以回答：

- prompt 是否复用、字符数多少；
- 是否有图片；
- 调用了几个工具、经过几轮 API；
- 关系搜索、公网搜索和提交分别耗时多久；
- 工具成功、隐私拒绝、provider 失败或事务失败。

现有指标不能直接回答：

- 本轮命中了 Skill 的哪一类关系场景；
- 模型为什么选择“邀请”而不是“继续观察”；
- 哪条 Skill 规则影响了最终建议；
- 某份 reference 是否参与判断，因为当前 reference 正文根本不可读。

### 后续优化入口，本轮不实施

1. **精简 Hermes 专用 Skill。** 保留硬边界和决策内核，把 Codex 专用说明从 Hermes 首轮提示分离。
2. **增加最小权限 reference reader。** 只允许读取当前 Skill 白名单文件，不开放通用 file/terminal。
3. **由服务端选择性注入资料。** 在模型前确定有限场景并注入一份参考，但需要评估误路由成本。
4. **增加结构化 decision trace。** 让模型输出场景、阶段、动作和证据引用，便于评测；不能把自报理由当作真实内部推理。
5. **减少无效工具轮次。** 合并互不依赖的本人记忆和关系提交，保留“先搜索、后依赖结果提交”的顺序。

这些方向在实施前需要分别测量 token、首字延迟、完整轮次耗时、工具漏调率、关系建议一致性和安全回归，不能只根据文档字符数决定。

## 九、失败分支和权威边界

| 情况 | 行为 | 是否写入当前关系消息 |
| --- | --- | --- |
| 非 owner | plugin skip | 否 |
| 未绑定群的一般/本人问题 | 只加载 owner 本人上下文 | 不读写人物关系 |
| 未绑定群的具体女生或截图问题 | 模型前阻断 | 否 |
| 已归档群或 profile 未同步 | 失败关闭 | 否 |
| MySQL/session store 不可用 | 明确本条未记录、未分析 | 否 |
| 模型跳过 commit | 可能仍回复文字 | 否 |
| commit 参数非法 | 整个本轮关系事务回滚 | 否 |
| 关系搜索零结果 | 只能说本次未检索到 | 不生成“从未发生” |
| 公网查询隐私拒绝 | 不发出请求 | 否 |
| DDGS 不可用/超时 | 明确未完成联网核验，不 fallback | 否 |
| 投影失败 | MySQL 已提交内容仍有效，job 可重试 | MySQL 是最新权威源 |

`.local/relationships/*.md` 永远只是 MySQL 的只读投影。系统只回复当前军师群，不向微信、抖音或任何女性自动代发。

## 十、代码证据索引

### 当前仓库

| 环节 | 入口 |
| --- | --- |
| profile route 生成 | `runtime/goutoujunshi_cli.py` 的 `reconcile-config` |
| Skill 激活、binding 和上下文 | `runtime/goutoujunshi/__init__.py` 的 `pre_gateway_dispatch`、`_context_prompt`、`_cached_session_prompt` |
| 六工具注册 | `runtime/goutoujunshi/__init__.py` 的 `register`、`DEFAULT_TOOL_NAMES` |
| 关系提交 | `handle_commit_turn`、`repository.commit_turn` |
| 关系检索 | `search_relationship_events`、`reciprocal_rank_fusion` |
| 公网搜索 | `handle_relationship_web_search`、查询匿名化和 DDGS registry wrapper |
| 上一 draft 规则 | `repository.apply_next_message_draft_rule` |
| 只读投影 | `exporter.export_relationship`、`process_export_jobs` |

### Hermes 0.20.4 宿主源码

| 环节 | 入口 |
| --- | --- |
| 新 session 消费 `auto_skill` | `gateway/run.py` 的 `_is_new_session` / auto-skill block |
| Skill 文件加载 | `agent/skill_commands.py` 的 `_load_skill_payload` |
| 首轮 user 消息组装 | `agent/skill_commands.py` 的 `_build_skill_message` |
| supporting files 枚举 | `tools/skills_tool.py` 的 `skill_view` |
| platform toolset 解析 | `hermes_cli/tools_config.py` 的 `_get_platform_tools` |
| Skill system index 条件 | `agent/system_prompt.py` 的 `has_skills_tools` |

宿主升级后必须重新核对这些行为，特别是 supporting-file 枚举、auto-skill 注入位置、toolset allowlist 和上下文压缩。

## 十一、一句话判断当前状态

- **群路由到 `goutoujunshi` profile**：只说明进入了受限关系运行面。
- **新 session 的 Skill 已加载**：说明完整 `SKILL.md` 已进入首轮 user 历史。
- **模型给出了关系建议**：说明主模型完成了提示驱动判断，不代表有独立决策路由记录。
- **模型提到某份 reference**：不代表它读过正文；当前 profile 无读取链路。
- **工具没有成功**：关系消息、draft、snapshot 或本人事实没有相应持久化。
- **`relationship_commit_turn` 成功**：本轮关系写入以 MySQL 为准。
- **公网搜索成功**：只获得当前 session 的临时网页摘要，不更新关系记忆。
- **发送 `/new`**：重新建立短期 session 和 Skill 首轮注入，不删除长期 MySQL 数据。

## 相关文档

- [架构说明](architecture.md)
- [产品定位](product.md)
- [关键流程](flows.md)
- [自动化与代理边界](automation.md)
- [知识库治理](knowledge-base.md)
- [权限边界](permissions.md)
- [测试地图](tests.md)
