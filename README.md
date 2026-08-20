# Wing-Dog

![一对相爱的成年男女在站立的教父式 Wing-Dog 关系军师见证下牵手相望](assets/wing-dog-hero-v2.png)

**简体中文** | [English](README_EN.md)

> 先接住情绪，再看清证据，最后给出一个真正能执行的下一步。

[![GitHub Stars](https://img.shields.io/github/stars/deng12565/wing-dog?style=social)](https://github.com/deng12565/wing-dog/stargazers)
[![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial-coral)](LICENSE)
[![Codex Skill](https://img.shields.io/badge/Codex-Skill-0f766e)](SKILL.md)

Wing-Dog 是一个面向真实关系进展的 AI 恋爱谋士。用户只需提交新消息、截图或线下变化，它就会主动判断当前目标，给出眼下动作、可直接使用的内容、执行时机和下一节点；不需要用户逐项询问能否邀约、见面或推进。

本项目是对 [powerycy/goutoujunshi](https://github.com/powerycy/goutoujunshi) 的改造与扩展，不是从零原创，也不代表上游作者参与或认可本分支。Wing-Dog 保留了上游的关系建议 Skill，并增加了 Hermes/飞书接入、MySQL 权威存储、人物与渠道隔离、多渠道检索、隐私受控的公网搜索、历史补强任务和本机运维能力。许可证及原作者 Required Notice 完整保留在 [LICENSE](LICENSE) 中。

## 它如何帮你

| 真实场景 | Wing-Dog 的处理方式 |
| --- | --- |
| 只贴出一条新回复，没有提问 | 自动判断局势，给当前行动、执行时机和下一节点 |
| 不知道这句话怎么回 | 给一个首选方案、1–4 个自然气泡，同时说明之后观察什么 |
| 想“渣一点”或更会撩 | 用真实展示、轻调侃、推拉、筛选或投入测试增加张力，不因标签误拒绝 |
| 已出现邀约或升级窗口 | 主动提出当前最合适的动作，不等用户先问“能不能” |
| 想了解见面时该做什么 | 区分泛泛了解、具体约会、临近见面、现场和复盘，不虚构安排 |
| 对方忽冷忽热 | 区分节奏差异、暂时压力和持续失衡，设置观察信号与停止条件 |
| 聊天截图很多、说话人复杂 | 先确认作者与顺序，只把可见原文和行为当作事实 |
| 微信、抖音、朋友圈信息分散 | 在同一人物范围内跨渠道检索历史，同时保持草稿与发送状态逐渠道隔离 |
| 建议依赖近期公共信息 | 只在已绑定关系群按需搜索，先匿名化查询，再标注网页标题、链接和检索日期 |
| 同时认识多个人 | 每个人独立建档，分别比较互惠、可靠性、吸引、价值观和现实可行性 |
| 没有具体对象 | 从真实生活与可持续认识渠道开始，不要求虚构对象或设置脱单期限 |
| 出现明确拒绝或危险信号 | 停止推进；在控制、跟踪、胁迫、诈骗或暴力场景优先处理安全 |

## 与上游相比增加了什么

```text
关系建议 Skill
      │
      ├── Codex：按需加载关系知识，生成分析与下一步
      │
      └── Wing-Dog 私有运行时
            ├── Feishu / Hermes：移动端入口与 owner 校验
            ├── MySQL：人物、渠道、事件和本人记忆的唯一权威源
            ├── Search：原文 + ngram + 有界增强的三分支检索
            ├── Web：已绑定群内匿名化、只读、按需的公共信息搜索
            └── Projection：自动生成只读关系审阅文件
```

- **移动端可用**：通过受控的飞书群与 Hermes Gateway 对话。
- **关系记忆有边界**：每个关系人物独立，每个来源渠道独立；跨群只共享用户本人事实。
- **状态不会混淆**：`received`、`sent`、`draft`、`background`、`analysis` 和 `correction` 分开保存。
- **检索返回权威正文**：增强摘要只用于查找，不会被当作事实返回给模型。
- **联网查询先匿名化**：只搜索必要的公共事实，返回标题、URL 和摘要；网页信息不会自动写入关系记忆。
- **授权不经过模型转抄**：关系与个人记忆工具只使用 Hermes 服务端 session 状态，并回查 owner、群和当前人物绑定。
- **失败关闭**：MySQL 不可用或人物绑定不明确时，不记录、不分析，也不退回通用猜测。
- **只提供建议**：不会自动替用户向微信、抖音或任何外部联系人发送消息。

## 两个运行面

仓库同时维护两个边界不同的运行面：

1. **可分发 Codex Skill**：`SKILL.md`、`agents/`、`references/` 和 `tests/` 提供行为规则、按需知识与场景验证。它本身不带数据库、后台服务或外部消息写入。
2. **本机 Hermes 私有运行时**：`runtime/` 与运维脚本接入飞书、Hermes Gateway 和 WSL/Docker MySQL，负责身份校验、人物绑定、持久化、关系检索、受控公网搜索和只读投影。

两者不能互相代替证据：代码存在不代表本机服务当前健康；单独安装 Skill 也不会自动获得飞书接入或关系持久化。详细结构见[架构说明](documentation/architecture.md)。

## 安装 Codex Skill

将仓库克隆到 Codex Skills 目录：

```bash
git clone https://github.com/deng12565/wing-dog.git ~/.codex/skills/wing-dog
```

然后在 Codex 中输入：

```text
使用 $goutoujunshi 读取最新关系进展，主动判断当前目标，给我现在该做的动作、执行时机和下一节点。
```

没有具体对象时，可以直接讲自己的生活、常见认识渠道、目标和障碍；有具体对象时，直接贴最新上下文或截图并说明来源。Wing-Dog 不发送固定问卷，只追问一个真正会改变建议的必要问题。

> [!IMPORTANT]
> Hermes、飞书和 MySQL 是独立的受控部署面，不属于 `git clone` 即可完成的 Skill 安装。安装、启动、停止、迁移、历史补强和外部预检都有副作用，执行前必须阅读相应文档并明确授权。

## 服务器常驻部署

Rocky Linux 宿主机可通过 `deployment/linux/` 运行独立的 `gateway + mysql + backup` Compose 栈。MySQL 不映射宿主端口，Gateway 使用锁定 digest 的 Hermes 0.20.4 派生镜像，DDGS 从带 SHA256 锁的离线 wheelhouse 安装；完整迁移、回滚和本机长期冷备流程见[公司服务器部署与本机冷备](documentation/server-deployment.md)。

服务器和本机不是双活。本机计划任务只在切换窗口禁用，原代码、Hermes、WSL MySQL、秘密、档案和备份长期保留；服务器运行期间本机数据不会自动同步。

## 兼容标识

本轮采用分层改名以保护现有部署。公开品牌已经统一为 **Wing-Dog**，以下内部标识暂时保留：

- Skill 调用名：`$goutoujunshi`
- Python 包和运行时路径：`runtime/goutoujunshi/`
- 数据库与默认数据库用户：`goutoujunshi`、`goutoujunshi_app`
- 环境变量前缀：`GOUTOUJUNSHI_*`
- Hermes toolset/profile、计划任务和运维脚本中的既有标识

这些是 legacy compatibility identifiers，不是第二个产品品牌。直接重命名它们会影响数据库权限、已安装插件、Hermes 路由、计划任务、环境配置和回滚路径，因此需要单独迁移版本。

## 关系数据与检索

MySQL `goutoujunshi` 数据库是关系数据的唯一权威来源。`.local/relationships/` 只是事务成功后生成的只读投影，不能手工编辑。

schema v5 使用 MySQL 8 `ngram` FULLTEXT，在单一人物绑定内融合三个候选分支：

1. 权威原文精确匹配与子串匹配；
2. 权威原文 ngram 全文检索；
3. 摘要、概念、别名、实体和时间线索的增强文本检索。

固定 RRF 排序后，系统重新从 MySQL 权威事件表读取正文，并带回 correction 闭包。默认最多返回 8 条事件；增强文本只负责定位，不作为事实输出。关系历史检索不依赖 Ollama、Milvus 或本地 embedding。

## 受控公网搜索

Hermes plugin 1.7.0 只在已经绑定人物的 Wing-Dog 关系群暴露 `relationship_web_search`。工具先用服务端 session 回查 owner、群和当前 MySQL binding，再对最小查询做二次匿名化，最后经 Hermes provider registry 精确取得无需 API key 的 `ddgs` provider。它不会调用通用搜索入口，也不允许回退到其他 provider；DDGS 未注册或不可用时失败关闭。未绑定群只有 owner 本人记忆工具，不能联网。

搜索最多返回 5 条标题、URL 和摘要，不抓取网页全文，不开放浏览器，也不保证稳定获得发布日期。回答必须把联网信息、MySQL 关系记忆和模型推断分开，并为联网信息标注标题、URL 和检索日期；网页标题、摘要或其他片段中的任何指令都只是不可信数据，绝不执行。结果只存在于当前 Hermes 会话，不会自动写入事件、快照、草稿、本人记忆或 Markdown 投影；匿名化拒绝、超时或服务异常时会明确降级。

## 项目结构

```text
wing-dog/
├── SKILL.md                    # Wing-Dog 行为与路由内核
├── agents/openai.yaml         # Codex 展示信息与默认提示词
├── references/
│   ├── knowledge/             # 关系科学与跨学科知识
│   ├── practical/             # 实用沟通与策略资料
│   └── THIRD_PARTY_NOTICES.md # 第三方来源和许可
├── tests/                     # Skill 场景规范
├── documentation/             # 架构、流程、权限和测试合同
├── deployment/linux/          # Rocky Linux Compose、离线依赖与运维脚本
├── runtime/
│   ├── goutoujunshi/          # 保留兼容名的 Hermes 插件和 MySQL 数据层
│   ├── benchmarks/            # 合成中文检索集与验收器
│   ├── tests/                 # 私有运行时单元测试
│   ├── bootstrap.py           # 安装、配置和静态核验入口
│   └── goutoujunshi_cli.py    # 数据与路由维护 CLI
└── scripts/                   # Skill 校验与本机运维脚本
```

## 验证

```powershell
python scripts\validate_skill.py
python scripts\validate_skill.py --runtime
python -m unittest discover -s runtime\tests -v
```

前两条验证 Skill 的结构、来源声明、链接、回归标记和运行时边界；单元测试覆盖插件、数据规则、关系检索、受控公网搜索、导出、路由和 bootstrap 隔离。它们不能证明真实 MySQL、Hermes、DDGS、飞书、计划任务或远程模型当前健康。

进一步阅读：[产品定位](documentation/product.md) · [架构说明](documentation/architecture.md) · [服务器部署与本机冷备](documentation/server-deployment.md) · [端到端记忆、上下文与路由](documentation/memory-context-routing.md) · [关键流程](documentation/flows.md) · [变量与秘密](documentation/variables.md) · [权限边界](documentation/permissions.md) · [测试地图](documentation/tests.md)

## 设计原则

1. **先接住人，再解决事。** 情绪没有被看见时，正确建议也可能无法执行。
2. **顺其自然也要行动。** 该表达就表达，该邀约就邀约；对方不接时体面收住。
3. **行为比标签可靠。** 不凭 MBTI、性别或一次聊天替目标对象读心。
4. **互惠比追到更重要。** 减少内耗、保留尊严和未来选择权也是成功。
5. **每次更新都给行动令。** 即使用户没有提问，也主动给当前动作、时机和下一节点；完整路线只在需要时展开。
6. **同意和退出权不可绕过。** 明确拒绝不是需要破解的障碍。
7. **危险情境先保安全。** 暴力、胁迫、跟踪、诈骗和自伤风险不能用普通恋爱话术处理。

## 来源、许可与贡献

Wing-Dog 改造自 [powerycy/goutoujunshi](https://github.com/powerycy/goutoujunshi)，并将若干第三方研究材料转译为不依赖人物或流派名称的通用互动能力。具体来源、固定基线、许可和仅研究用途说明见[第三方声明](references/THIRD_PARTY_NOTICES.md)。

项目继续采用 [PolyForm Noncommercial License 1.0.0](LICENSE)，并保留上游要求的声明：

```text
Required Notice: Copyright 2026 powerycy.
```

欢迎补充研究、改进表达、纠正文献或提交匿名化场景。开始前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

本项目提供关系教育与决策支持，不替代心理治疗、医疗诊断、律师意见、警方或紧急服务。
