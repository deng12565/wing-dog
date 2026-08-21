# 公司服务器部署与本机冷备

## 目标与边界

线上实例运行在 Rocky Linux 宿主机的独立 Docker Compose 项目中。实际代码和数据路径由受保护的 `deployment/linux/server.env` 中 `WING_DOG_CODE_ROOT`、`WING_DOG_DATA_ROOT` 固定；开发容器只把这些目录作为 `/workspace` 和 `/data` 挂载，不承载 Compose 生命周期。

本机 Windows、Hermes、WSL MySQL、计划任务、配置、秘密、不可变档案、dump 和历史备份长期保留。切换时只禁用 `Hermes-Goutoujunshi` 计划任务并正常停止本机 Gateway/MySQL，不卸载软件、不删除任务、不注销 WSL、不删除容器或卷，也不执行 `docker compose down -v`。

本机冷备不与服务器自动同步。服务器切换后新增的数据只存在于服务器；若离职前未完成最终回迁就失去服务器权限，只能恢复到迁移当天的本机状态。

## Compose 结构

`deployment/linux/compose.yaml` 定义三个服务：

- `mysql`：锁定 MySQL 8.4.6 digest，仅加入内部网络，不映射宿主端口。
- `gateway`：从锁定 digest 的 Hermes 0.20.4 官方镜像派生。s6 初始化按官方要求以 root 启动，随后将 Gateway 和 supervisor 主进程降权到映射后的普通 `hermes` 用户；容器挂载只读代码、Hermes 状态、秘密文件和关系投影目录。
- `backup`：每日执行一致性 dump、生成 SHA256 并轮换服务器备份；不挂载 Docker socket。

Gateway 镜像从 `deployment/linux/wheelhouse/requirements-linux.lock` 离线安装 `ddgs==9.14.4` 和 PyMySQL。wheelhouse 文件不进入 Git，迁移时连同 SHA256 锁文件单独传输。Hermes runtime patch 同时覆盖 `gateway/run.py`、`agent/turn_context.py`、`plugins/platforms/feishu/adapter.py` 与 `hermes_logging.py`，只接受已知 Hermes 0.20.4 版本及逐文件原始 SHA256，写入采用多文件回滚，并在完成后逐文件验证补丁 SHA256；任一条件不匹配时构建直接失败。

`deployment/linux/supervisor.py` 每 60 秒检查数据库、对账人物路由、重试只读投影和清理过期临时媒体；路由变化时调用 Hermes s6 生命周期正常重启 Gateway。它不执行历史补强，也不主动联网搜索。

## 受保护目录

服务器数据根目录必须为 `700`，`secrets/` 为 `700`，其中每个秘密文件为 `600`。Hermes global/profile 的 `.env` 必须为 `600`；两处 `logs/` 必须为 `700`、内部日志为 `600`。bootstrap 不删除旧日志，只收紧已有目录和文件权限。`runtime/bootstrap.py prepare-server-secrets` 只迁移飞书、模型、owner 和项目数据库所需变量，并生成新的服务器 MySQL 密码；不得把生成目录、`server.env`、`.env`、dump、fingerprint、日志或私人 operator 文档加入 Git。

Compose 不公开 MySQL 或 Gateway 端口，不挂载 Docker socket。`gateway` 只加入内部数据库网络和允许外联的网络；数据库只加入内部网络。

## 初次迁移顺序

1. 轮换任何已暴露的 SSH 口令，确认 SSH key 登录有效。
2. 对本机工作树、Hermes、WSL MySQL、档案和备份生成清单与 SHA256，不移动或删除原件。
3. 校验并提交迁移实现，生成 Git bundle；通过 SSH 传输 bundle、Linux wheelhouse、私有档案、operator 文档和受保护秘密，不推送 GitHub。
4. 服务器先构建镜像、启动 MySQL、恢复一次暂存 dump，并执行 bootstrap、schema、模型、DDGS、工具面及临时恢复检查。此时不得启动远端 Gateway。
5. 切换窗口先禁用本机计划任务，再正常停止本机 Gateway；在无写入状态生成最终 dump、SHA256 和 `migration-fingerprint`。
6. 停止远端 Gateway/backup，恢复最终 dump。启动远端 Gateway 前比较全部 12 张项目表的行数和确定性哈希；任一差异都停止切换并恢复本机。
7. 指纹一致后启动远端 Gateway/backup，确认只存在一个飞书连接，重新生成关系投影并执行验收。

`migration-fingerprint` 不输出关系正文，只输出 schema、表名、行数和 SHA256。它覆盖项目 schema 中的 12 张表；共享库中的非项目表不在比较范围内。

## 操作入口

在服务器项目根目录执行：

```bash
deployment/linux/control.sh build
deployment/linux/control.sh seed-home
deployment/linux/control.sh bootstrap
deployment/linux/control.sh start
deployment/linux/control.sh status
deployment/linux/control.sh fingerprint
deployment/linux/control.sh backup
deployment/linux/control.sh logs
deployment/linux/control.sh stop
```

恢复命令会替换权威数据库，必须显式提供确认参数：

```bash
deployment/linux/restore.sh --confirm-replace-goutoujunshi /absolute/data-root/migration/final.sql
```

所有命令均有外部副作用。执行前必须确认当前主节点、目标路径、备份 SHA256 和飞书连接状态。

## plugin 1.8 / schema v6 修复上线顺序

本轮修复不是一次 bootstrap 即完成。必须严格按以下顺序执行，并分别获得构建/配置、数据库写入、session 轮换、Gateway 重启和真实飞书消息验收的精确授权：

1. 停止写入窗口，生成数据库备份与迁移前 `migration-fingerprint`，记录现有镜像和 Hermes 文件 SHA256。
2. 构建锁定 Hermes 0.20.4 的派生镜像；离线运行 Skill validator、完整 runtime tests、runtime patch fixtures 和 disposable MySQL schema v6 重放。
3. 在 Gateway 保持停止时运行 bootstrap，使 global/profile 同时安装 plugin 1.8.0 与完整 Skill，并执行动态 discovery、6 个 tool schema、实际 toolset、Skill loader 和权限核验。
4. 应用 schema v6。先用 manifest 从锁定原文件提取的两个别名把活动人物唯一解析；零个或多个匹配均停止，不进行 correction 或导入。
5. 对事件 `#1210` 至 `#1221` 运行 `correct-inferred-sent`，只追加“自动推断无效、实际发送状态未知”的 correction，不修改旧事件。
6. 校验 Echo 原文件 SHA256 必须为 `a213400c2ccac8addb45ff960afe0cee6c5d555df492f376a491b8926a2b450f`，生成结构化 manifest，再用 `import-structured` 将源文件、manifest 和 sidecar 不可覆盖地归档后幂等导入。
7. Gateway 仍停止时运行受 `gateway/session.py` SHA256 约束的 `rotate-profile-sessions`。命令先以迁移原因结束旧 transcript，再移除 global/profile route mirror；不修改 MySQL 关系历史。
8. 只重启一个 Gateway，执行健康检查并生成迁移后 fingerprint。真实飞书验收由 owner 依次发送 `/new`、普通关系消息、追问总结、单图后 2.5 秒内补说明，以及 `/relation status`。

任何阶段失败都不得跳过到下一步。源文件或 manifest SHA 不匹配、人物不唯一、session transcript 无法写入迁移原因、工具 discovery 不完整或 fingerprint 异常时保持 Gateway 停止并按备份回滚。

## 验收与回滚

服务器验收至少包括 Compose 配置、镜像 digest、Hermes/DDGS/plugin 版本、四文件 runtime patch 的原始/补丁 SHA、非 root、无公开端口、秘密/日志权限、schema v6 重放、12 表指纹、日备份及临时恢复、profile session 轮换、supervisor 路由重启、投影重试、媒体清理、容器重启恢复、Feishu 连接和远程模型预检。

真实飞书验收由 owner 从飞书发起 `/new`、`/relation status`、`/me status`、一条普通关系消息、一次追问总结、一张测试图片后在 2.5 秒内补说明，以及一次不含私人信息的 DDGS 查询。随后核对首轮 Skill/工具可见、第二轮 `history>0`、无跨库计数告警、普通消息未确认草稿、图片与说明同轮、MySQL 事件类型、INFO 日志无正文/视觉描述和敏感文件权限。机器人不得替 owner 向外部联系人发送消息。

切换失败时，先停止远端 Gateway 并确认飞书断开，再启动本机 MySQL、重新启用本机计划任务并验证本机连接。任何时刻只允许一个 Gateway 在线。

## 离职前最终回迁

1. 在公司账号停用前安排维护窗口，停止远端 Gateway 并确认飞书断开。
2. 生成远端最终 dump、12 表指纹、当前代码 bundle 和秘密白名单包，通过 SSH 拉回本机并校验 SHA256。
3. 先备份本机旧库，再恢复远端最终库；应用对应 schema、Skill 和插件后比较 12 表指纹。
4. 远端保持停止，重新启用本机计划任务并验证 MySQL、Gateway、飞书、模型、图片和 DDGS。
5. 本机验收成功后轮换飞书应用 secret 与模型 API key，只把新值保存在个人本机。
6. 公司服务器清理是单独的不可逆操作，只能在本机回迁验收成功且再次明确授权后执行。
