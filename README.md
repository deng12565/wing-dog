# Wing-Dog

![一对相爱的成年男女在站立的教父式 Wing-Dog 关系军师见证下牵手相望](assets/wing-dog-hero-v2.png)

**简体中文** | [English](README_EN.md)

> 先接住情绪，再看清证据，最后给出一个真正能执行的下一步。

[![GitHub Stars](https://img.shields.io/github/stars/deng12565/wing-dog?style=social)](https://github.com/deng12565/wing-dog/stargazers)
[![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial-coral)](LICENSE)
[![Codex Skill](https://img.shields.io/badge/Codex-Skill-0f766e)](SKILL.md)

Wing-Dog 是一个面向真实关系场景的 AI 恋爱决策助手。它不会只说“勇敢去追”或“赶紧放弃”，而是先区分事实、推测与未知，再综合互惠、现实条件、风险、机会成本和长期选择权，最后给出一句可发送的话、一次具体邀约、一个观察信号，或一个明确的停止动作。

本项目是对 [powerycy/goutoujunshi](https://github.com/powerycy/goutoujunshi) 的改造与扩展，不是从零原创，也不代表上游作者参与或认可本分支。Wing-Dog 保留了上游的关系建议 Skill，并增加了 Hermes/飞书接入、MySQL 权威存储、人物与渠道隔离、多渠道检索、历史补强任务和本机运维能力。许可证及原作者 Required Notice 完整保留在 [LICENSE](LICENSE) 中。

## 它如何帮你

| 真实场景 | Wing-Dog 的处理方式 |
| --- | --- |
| 不知道这句话怎么回 | 先给一条可直接复制的首选回复，再说明发送时机和后续分支 |
| 想邀约或推进关系 | 判断当前阶段与互惠证据，给一个低压力、可退出的具体动作 |
| 对方忽冷忽热 | 区分节奏差异、暂时压力和持续失衡，设置观察信号与停止条件 |
| 聊天截图很多、说话人复杂 | 先确认作者与顺序，只把可见原文和行为当作事实 |
| 微信、抖音、朋友圈信息分散 | 在同一人物范围内跨渠道检索历史，同时保持草稿与发送状态逐渠道隔离 |
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
            └── Projection：自动生成只读关系审阅文件
```

- **移动端可用**：通过受控的飞书群与 Hermes Gateway 对话。
- **关系记忆有边界**：每个关系人物独立，每个来源渠道独立；跨群只共享用户本人事实。
- **状态不会混淆**：`received`、`sent`、`draft`、`background`、`analysis` 和 `correction` 分开保存。
- **检索返回权威正文**：增强摘要只用于查找，不会被当作事实返回给模型。
- **授权不经过模型转抄**：关系与个人记忆工具只使用 Hermes 服务端 session 状态，并回查 owner、群和当前人物绑定。
- **失败关闭**：MySQL 不可用或人物绑定不明确时，不记录、不分析，也不退回通用猜测。
- **只提供建议**：不会自动替用户向微信、抖音或任何外部联系人发送消息。

## 两个运行面

仓库同时维护两个边界不同的运行面：

1. **可分发 Codex Skill**：`SKILL.md`、`agents/`、`references/` 和 `tests/` 提供行为规则、按需知识与场景验证。它本身不带数据库、后台服务或外部消息写入。
2. **本机 Hermes 私有运行时**：`runtime/` 与运维脚本接入飞书、Hermes Gateway 和 WSL/Docker MySQL，负责身份校验、人物绑定、持久化、检索和只读投影。

两者不能互相代替证据：代码存在不代表本机服务当前健康；单独安装 Skill 也不会自动获得飞书接入或关系持久化。详细结构见[架构说明](documentation/architecture.md)。

## 安装 Codex Skill

将仓库克隆到 Codex Skills 目录：

```bash
git clone https://github.com/deng12565/wing-dog.git ~/.codex/skills/wing-dog
```

然后在 Codex 中输入：

```text
使用 Wing-Dog（$goutoujunshi）帮我判断当前恋爱阶段，并给一个自然的推进、观察或停止动作。
```

没有具体对象时，可以直接讲自己的生活、常见认识渠道、目标和障碍。出现具体对象后，可以提供：

```text
你：MBTI / 主观综合评分 0-100 / 主要优势和短板
对象（如有）：代号 / MBTI / 主观综合评分 0-100 / 当前关系
经过：认识方式、发展时间、关键事件、联系和双方投入
目标：推进、确认、修复、比较选择，还是退出
情绪：目前最难受的点、强度 0-10，以及是否必须马上回复
```

不知道的项目可以留空。Wing-Dog 会从叙述中整理已知信息，并只追问真正会改变建议的内容。

> [!IMPORTANT]
> Hermes、飞书和 MySQL 是独立的受控部署面，不属于 `git clone` 即可完成的 Skill 安装。安装、启动、停止、迁移、历史补强和外部预检都有副作用，执行前必须阅读相应文档并明确授权。

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

固定 RRF 排序后，系统重新从 MySQL 权威事件表读取正文，并带回 correction 闭包。默认最多返回 8 条事件；增强文本只负责定位，不作为事实输出。在线检索不依赖 Ollama、Milvus 或本地 embedding。

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
python -m unittest discover -s runtime\tests -v
```

第一条验证 Skill 的结构、预算、链接和运行时边界；第二条覆盖插件、数据规则、检索、导出、路由和 bootstrap 的隔离测试。它们不能证明真实 MySQL、Hermes、飞书、计划任务或远程模型当前健康。

进一步阅读：[产品定位](documentation/product.md) · [架构说明](documentation/architecture.md) · [端到端记忆、上下文与路由](documentation/memory-context-routing.md) · [关键流程](documentation/flows.md) · [变量与秘密](documentation/variables.md) · [权限边界](documentation/permissions.md) · [测试地图](documentation/tests.md)

## 设计原则

1. **先接住人，再解决事。** 情绪没有被看见时，正确建议也可能无法执行。
2. **顺其自然也要行动。** 该表达就表达，该邀约就邀约；对方不接时体面收住。
3. **行为比标签可靠。** 不凭 MBTI、性别或一次聊天替目标对象读心。
4. **互惠比追到更重要。** 减少内耗、保留尊严和未来选择权也是成功。
5. **策略必须说明代价。** 可以讨论表达与节奏，但必须交代条件和长期成本。
6. **同意和退出权不可绕过。** 明确拒绝不是需要破解的障碍。
7. **危险情境先保安全。** 暴力、胁迫、跟踪、诈骗和自伤风险不能用普通恋爱话术处理。

## 来源、许可与贡献

Wing-Dog 改造自 [powerycy/goutoujunshi](https://github.com/powerycy/goutoujunshi)，并吸收了 [hotcoffeeshake/tong-jincheng-skill](https://github.com/hotcoffeeshake/tong-jincheng-skill) 的部分经验框架。具体基线、版权和 MIT 许可见[第三方声明](references/THIRD_PARTY_NOTICES.md)。

项目继续采用 [PolyForm Noncommercial License 1.0.0](LICENSE)，并保留上游要求的声明：

```text
Required Notice: Copyright 2026 powerycy.
```

欢迎补充研究、改进表达、纠正文献或提交匿名化场景。开始前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

本项目提供关系教育与决策支持，不替代心理治疗、医疗诊断、律师意见、警方或紧急服务。
