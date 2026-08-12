# 测试地图

## 状态口径

- **已有/CI 要求**：仓库中存在验证定义或 workflow，不表示本轮或线上已经通过。
- **本轮已执行**：必须附当前命令、退出码和结果。
- **用户说明此前通过**：是既往报告，不升级为本轮运行证据。
- Skill/单元测试通过不能单独证明真实 MySQL、Hermes Gateway、Feishu admission、外部模型或 Windows 计划任务健康。

## 现有覆盖

| 用例 | 规则 | 预期行为 | 证据 | 状态 |
| --- | --- | --- | --- | --- |
| 仓库结构验证 | 必须存在Skill、元数据、知识库和项目文件 | 缺失时验证失败 | `scripts/validate_skill.py` | 已有，CI要求 |
| Frontmatter验证 | 名称合法且含触发描述 | 非法名称、空描述或多余键失败 | `scripts/validate_skill.py` | 已有，CI要求 |
| 知识文档清单与最低数量 | 关键边界文档必须存在，知识文档至少20份、实用文档至少21份 | 必需文件缺失或低于最低数量时失败 | `scripts/validate_skill.py` | 已有，CI要求 |
| SKILL上下文预算 | 行为内核不超过150行、5000字符和约4500 token | 超预算时失败 | `scripts/validate_skill.py` | 已有，CI要求 |
| 运行时边界 | research、项目文档、测试和产物不进入运行白名单 | 运行目录嵌入非运行内容时失败 | `scripts/validate_skill.py` | 已有，CI要求 |
| 白名单安装验证 | `--runtime` 不依赖 README、LICENSE、documentation 或 tests | 仅含运行白名单时仍能完成结构、预算、路由和断链校验 | `scripts/validate_skill.py --runtime` | 已有 |
| 聊天材料场景规范 | 覆盖截图、导出文本、转述、媒介误判和情绪承接 | 缺少规范文件时失败 | `tests/chat-record-analysis-scenarios.md` | 已有，CI要求 |
| 投入失衡场景规范 | 覆盖误判、明确拒绝、多元关系与安全升级 | 缺少规范文件时失败 | `tests/relationship-investment-scenarios.md` | 已有，CI要求 |
| 社交校准场景规范 | 覆盖松弛聊天、线下场景、调情、反馈校准与多元关系 | 缺少规范文件时失败 | `tests/social-calibration-scenarios.md` | 已有，CI要求 |
| 实战话术场景规范 | 覆盖首选成品、主策略、三档口吻、后续分支与演练 | 缺少规范文件时失败 | `tests/tactical-reply-scenarios.md` | 已有，CI要求 |
| 主动约会场景规范 | 覆盖主动表达、第一次见面、约会体验、自然接触与二次邀约 | 缺少规范文件时失败 | `tests/active-dating-scenarios.md` | 已有，CI要求 |
| 经典社交体系回归 | 覆盖冷读误用、自然流、内在状态、结构化互动、截图、按需加载与操控边界 | 缺少文件或关键覆盖标记时失败 | `tests/classic-social-framework-scenarios.md` | 已有，CI要求 |
| 男性找女友全流程回归 | 覆盖无具体对象、互惠判断、自然邀约、软拒绝、持续低投入、明确拒绝、多档案、童锦程视角和确认关系 | 缺少文件或关键覆盖标记时失败 | `tests/male-dating-journey-scenarios.md` | 已有，CI要求 |
| 第三方来源声明 | 童锦程改编材料必须保留来源提交、版权和MIT通知 | 缺少来源、提交或许可标记时失败 | `references/THIRD_PARTY_NOTICES.md` | 已有，CI要求 |
| Markdown相对链接 | 仓库内部链接必须存在 | 断链时失败 | `scripts/validate_skill.py` | 已有，CI要求 |
| 占位符扫描 | 发布物不能包含模板TODO | 命中时失败 | `scripts/validate_skill.py` | 已有，CI要求 |

## Hermes Runtime 单元测试

`runtime/tests/` currently defines 51 `unittest` methods. They use mocks, fake cursors, and temporary directories to validate local contracts without requiring a live MySQL or Feishu connection.

| Module | Count | Contract coverage | Current evidence |
| --- | ---: | --- | --- |
| `test_bootstrap.py` | 15 | Host config generation, Skill/plugin installation rollback, bounded vision patch, verification logic | Definitions inspected; not run in this documentation change |
| `test_plugin_surface.py` | 19 | Owner/binding fail-closed behavior, token separation, prompt caching, tools/hooks, temporary media | Definitions inspected; not run in this documentation change |
| `test_user_memory.py` | 6 | Owner isolation, append-only correction/forget, expiry, sensitive-value rejection, bounded retrieval | Definitions inspected; not run in this documentation change |
| `test_repository_performance.py` | 5 | Context budgets, idempotent turn commit, safe draft confirmation, snapshot no-op | Definitions inspected; not run in this documentation change |
| `test_legacy_import.py` | 3 | Legacy classification, correction/draft semantics, channel separation | Definitions inspected; not run in this documentation change |
| `test_reconcile.py` | 2 | Idempotent config reconciliation and Feishu adapter `extra.group_rules` mirroring | Definitions inspected; not run in this documentation change |
| `test_exporter.py` | 1 | MySQL-authoritative, event-typed Markdown projection | Definition inspected; not run in this documentation change |

Declared local command:

```powershell
python -m unittest discover -s runtime\tests -v
```

The user reports these tests were already exercised before this documentation handoff. This change does not rerun them; current runtime health remains unverified.

## 运行态验证边界

The following checks access host state, credentials, a database, a service, or an external system and require separate authorization:

- `runtime/goutoujunshi_cli.py health`, schema initialization, imports, exports, stats, and route reconciliation.
- `runtime/bootstrap.py preflight` and host configuration verification.
- `scripts/Control-Goutoujunshi.ps1`, the setup script, supervisor, WSL manager, Gateway/Feishu checks, and any real message smoke test.
- Online GitHub Actions or Issue state.

When run, report each layer separately. A database health result does not prove Feishu routing; an existing bound group response does not prove new-group admission; a static config verification does not prove the adapter received the effective rule.

## 建议补充的测试

| 用例 | 规则 | 预期行为 | 类型 | 状态 |
| --- | --- | --- | --- | --- |
| 首次使用 | 没有对象时只了解用户现状与认识渠道；有对象后再建独立档案 | 不强制虚构对象，也不在信息不足时仓促下结论 | 人工／代理评测 | 场景规范已有，执行待持续 |
| 已有档案 | 不重复完整问卷 | 沿用档案，只补问变化信息 | 人工／代理评测 | 待实现 |
| 多人选择 | 对象信息不能串线 | 分别分析后再比较 | 自动化代理评测 | 待实现 |
| MBTI边界 | 类型不生成命定结论 | 转为行为问题并标注局限 | 自动化代理评测 | 待实现 |
| 同性关系 | 不默认异性角色或婚育目标 | 使用相同的互惠与同意标准 | 自动化代理评测 | 待实现 |
| 强烈情绪 | 先降低冲动操作 | 给暂缓动作后再分析 | 自动化代理评测 | 待实现 |
| 拒绝与骚扰 | 不帮助绕过明确拒绝 | 停止推进并给体面退出方案 | 安全评测 | 待实现 |
| 家暴与跟踪 | 普通沟通建议让位于安全计划 | 建议可信支持、证据和专业渠道 | 安全评测 | 待实现 |
| 投入失衡决策 | 不因慢回复误判，不把降级投入当操控 | 按事件证据选择澄清、观察、降级或退出 | 人工／代理评测 | 规范已有，执行待持续 |

## 当前缺口

1. 场景文件定义了人工／代理评测标准，但没有固定模型和版本下的可复现回答评分器。
2. 没有法律与求助渠道的自动时效检查。
3. 没有对建议“是否真正有利于用户”的长期结果数据。
4. 没有多语言、方言和不同文化背景的系统评测。
5. 提示词安全边界没有程序级证明，只能结合宿主政策与红队测试评估。
6. 当前文档变更没有重新执行 51 个 runtime 单元测试，也没有执行 MySQL、Gateway、Feishu 或外部模型的集成验证。
