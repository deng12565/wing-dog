# 测试地图

## 状态口径

- 仓库中存在验证定义，不等于本轮或运行环境已通过。
- 本轮证据必须报告实际命令、退出码与结果。
- Skill/单元测试不能证明真实 MySQL、Hermes Gateway、Feishu、远程模型或计划任务健康。

## Skill 与场景覆盖

`scripts/validate_skill.py` 检查 frontmatter、知识/实用资料最低数量、SKILL 上下文预算、运行白名单、场景规范、第三方声明、Markdown 链接、占位符和编译产物。`tests/` 的七份人工/代理规范覆盖聊天材料、投入失衡、社交校准、即时话术、主动约会、经典社交框架和男性找女友全流程。

```powershell
python scripts\validate_skill.py
python scripts\validate_skill.py --runtime
```

## Runtime 单元测试

| Module | Count | 主要合同 |
| --- | ---: | --- |
| `test_bootstrap.py` | 16 | host 配置、安装回滚、plugin 1.6.1 清单、profile 工具可见性、视觉补丁和静态核验 |
| `test_plugin_surface.py` | 27 | owner/binding/session 失败关闭、无模型 token schema、prompt 缓存、tools/hooks、写入时增强 schema |
| `test_user_memory.py` | 6 | owner 隔离、append-only correction/forget、过期和敏感值拒绝 |
| `test_repository_performance.py` | 5 | 上下文预算、幂等 turn commit、draft 确认、snapshot no-op |
| `test_legacy_import.py` | 3 | legacy 分类、correction/draft 和渠道隔离 |
| `test_reconcile.py` | 2 | route 对账幂等及 Feishu adapter 规则镜像 |
| `test_exporter.py` | 1 | MySQL 权威的只读 Markdown 投影 |
| `test_relationship_search.py` | 22 | 三支 RRF、MySQL 模式、纠正、人物/渠道/draft、显式 draft 不被三支候选挤出、输出预算、增强校验、权威 hash 复核、benchmark 纠正闭包、批量预算、任务重试、schema v5 和无向量路径 |
| `test_schema_v5_mysql.py` | 2 | 在显式授权的专用 MySQL 测试库验证空库幂等，以及带历史事件、draft 和旧派生表的 v4 模拟迁移与二次幂等；默认跳过 |

```powershell
python -m unittest discover -s runtime\tests -p "test_*.py"
```

这些测试使用 mock、fake cursor、合成数据和临时目录，不访问真实 MySQL、Feishu 或远程模型。

## 固定 MySQL 检索基准

- `relationship_search_cases.py` 固定 120 条中文合成事件，覆盖 preference、busy reason、invitation、boundary、background、correction 六类，无真实关系数据。
- 固定拆分为 40 development + 80 frozen；frozen 中 40 条为与权威正文无连续中文二元重叠的同义改写，40 条为精确词。
- `run_mysql_search_benchmark.py` 要求名称以 `goutoujunshi_benchmark` 开头且运行前为空的独立数据库，创建 10000 条 fixture，执行与在线实现相同的三支候选、固定 RRF 和最多 8 层纠正闭包，最终删除测试表。
- frozen 门槛：同义改写 Recall@5 >= 0.90、MRR@5 >= 0.80、精确词 Recall@5 = 1.00。
- 性能同时报告复用连接的 SQL 核心延迟，以及按线上 repository 边界为候选、hydrate、纠正查询分别建立连接的数据库路径延迟；10000 条门槛以后者 P95 <= 250ms、任何一次 <= 500ms 为准。
- `--answer-eval` 对 80 条 frozen 分别调用当前远程主模型处理“全量合成历史 oracle”和“MySQL Top-8”；结构化返回关键 event ID 和 `advance/observe/stop/clarify/support`。关键事实覆盖率 >= 95%，行动方向一致率 >= 90%。
- 人物隔离、显式渠道、纠正优先、draft 隔离和数据库失败关闭由 runtime 测试与后续真实集成验收共同要求 100%。

该 benchmark 需要专用数据库权限；`--answer-eval` 还会产生 160 次远程模型请求。二者都不能作为普通离线测试自动运行。

## 运行态验证边界

以下检查需要单独授权：

- schema v5 对真实 `goutoujunshi` 的迁移及第二次幂等应用。
- `enrichment-backfill/work/status` 对活动和归档人物的历史补强。
- 独立 MySQL benchmark 和远程 answer oracle。
- `runtime/bootstrap.py preflight`、host 配置、启动/停止脚本、Gateway/Feishu、计划任务和真实消息冒烟。

报告时必须分层：数据库健康不证明 Feishu 路由；静态配置不证明 adapter 收到有效规则；离线测试不证明历史补强已完成。运行路径无 Ollama/Milvus 依赖可通过仓库扫描和获批后的进程/网络观测分别验证。

## 当前缺口

### 2026-08-12 验收记录

- 完整 runtime：80 项，78 通过，2 项专用 MySQL 测试按默认配置跳过；同两项随后在授权的 disposable MySQL 库单独运行并全部通过。Skill 普通与 `--runtime` 校验、Python 编译和 `git diff --check` 通过。
- 生产 schema v5 首次与二次应用通过；409 个非 draft 文档/任务、157 个 draft 排除、两个 ngram FULLTEXT、旧表不存在、共享 `function_calls` 不变。
- 历史补强最终为 409 done/enriched、0 pending/running/failed，409 个 source hash 全部匹配权威事件。
- 10000 条/80 frozen 最终报告：语义 Recall@5 1.00、MRR@5 0.873、精确 Recall@5 1.00、correction Recall@1 1.00、SQL core P95 47.04ms、线上数据库路径 P95 168.86ms/max 185.52ms、事实覆盖 1.00、行动一致 0.975。
- plugin 1.5.0 的历史生产只读探针曾通过跨渠道、显式渠道、人物隔离、draft 默认隐藏/显式可见、`mysql_enriched`、内容预算和增强字段隐藏；独立不可达端口探针验证数据库失败关闭。

### 2026-08-13 仓库验收记录

- 完整 runtime：84 项，82 通过，2 项专用 MySQL 测试按默认配置跳过。Skill 普通与 `--runtime` 校验、Python 编译和 `git diff --check` 通过。
- plugin 1.6.0 的仓库测试覆盖服务端 session 授权、缺失 session、task/session 不一致、跨 owner、跨人物、归档 binding、session 清理、同轮 search-then-commit、旧 token 参数无影响，以及个人记忆当前 `source_ref` 隔离。
- `test_bootstrap.py` 验证 plugin 1.6.1 清单、安装回滚、源目录与安装目录清单一致，以及关系 profile 固定关闭 `tools.tool_search`。
- `test_plugin_surface.py` 验证服务端 binding 高于截图/OCR、引用消息和旧会话文本，并覆盖同 session 的 search-then-commit 与 session 清理。
- 部署时仍须按“运行态验证边界”逐项核验；真实姓名、session 标识、binding 数量和当前服务状态只保留在本地 operator 记录，不进入公开仓库。

### 剩余边界

1. 生产 14 条历史 correction 均未链接 `supersedes_event_id`，因此生产 correction closure 无现成真实样本；专用 MySQL fixture 和自动测试已覆盖闭包排序。
2. 发布后没有代替用户发送飞书消息；真实 inbound -> 模型 -> outbound 往返由用户下一条正常消息自然验收。
3. 没有法律/求助资料的自动时效检查，也没有长期关系结果遥测。
4. prompt 安全边界没有程序级完整证明，仍需结合宿主政策和红队测试。
