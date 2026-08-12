# 变量、配置与秘密

## 两个配置边界

可分发 Codex Skill 不要求环境变量或 API key。Hermes 私有运行时需要由宿主保护的 `.env`、Hermes YAML 配置和 Codex provider 信息。真实值不得写入本仓库、示例、日志、文档或 Git 历史。

`runtime/goutoujunshi/plugin.yaml` 声明插件启动所必需的三个变量：`GOUTOUJUNSHI_DB_PASSWORD`、`GOUTOUJUNSHI_OWNER_ID`、`GOUTOUJUNSHI_TOKEN_SECRET`。安装脚本还配置下列运行变量。

## Runtime 变量

| Name | Purpose | Source/default | Required/absence impact |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | Hermes model provider authentication | Protected Hermes env or interactive installation input | Model preflight/chat unavailable |
| `FEISHU_ALLOW_ALL_USERS` | Disable open admission | Setup writes `false` | Incorrect value may broaden admission |
| `FEISHU_ALLOWED_USERS` | Feishu adapter owner allowlist | Resolved from one historical Feishu owner | Owner messages may be rejected or wrong user admitted |
| `GOUTOUJUNSHI_OWNER_ID` | Plugin owner identity and data namespace | Same resolved owner | Plugin fails closed |
| `GOUTOUJUNSHI_DB_HOST` | MySQL host | Setup/default `127.0.0.1` | Connection uses default or fails |
| `GOUTOUJUNSHI_DB_PORT` | MySQL port | Setup/default `3306` | Connection uses default or fails |
| `GOUTOUJUNSHI_DB_NAME` | Authoritative database name | Setup/default `goutoujunshi` | Wrong database selected |
| `GOUTOUJUNSHI_DB_USER` | Least-privilege application user | Setup/default `goutoujunshi_app` | Database authentication fails |
| `GOUTOUJUNSHI_DB_PASSWORD` | Application database password | Generated/preserved in protected Hermes env | Data layer refuses to connect |
| `GOUTOUJUNSHI_TOKEN_SECRET` | HMAC key for session-bound tool claims | Generated/preserved; minimum 32 characters | Tool claims cannot be issued or verified |
| `GOUTOUJUNSHI_EXPORT_ROOT` | Generated relationship projection root | Setup points to `.local/relationships` | Exporter falls back to cwd `.local/relationships` |
| `GOUTOUJUNSHI_OPENAI_BASE_URL` | Responses-compatible provider base URL | Derived from Codex config | Preflight/configuration fails |
| `GOUTOUJUNSHI_MODEL` | Configured chat/vision model | Installation target | Preflight/configuration fails |
| `GOUTOUJUNSHI_REASONING` | Configured reasoning effort | Installation target | Preflight/configuration fails |
| `HERMES_HOME` | Hermes config, state, cache and media registry root | Operator scripts set `%LOCALAPPDATA%\hermes` | Plugin media registry/CLI host resolution may differ |
| `PYTHONPATH` | Installed Hermes Python package root | Operator scripts set the Hermes agent root | CLI imports may fail |

The setup script reads `%USERPROFILE%\.codex\auth.json`, `%USERPROFILE%\.codex\config.toml`, the Hermes session index, and an existing protected Hermes `.env`; these files are host inputs, not repository assets. It writes secrets through a staged file under ignored `.local/`, then replaces and restricts the host Hermes `.env`.

## 配置文件

- `runtime/goutoujunshi/plugin.yaml`: versioned plugin interface, required variable names, tools and hooks; contains no values.
- Hermes global/profile `config.yaml`: host-managed provider, toolset, route, compression and Feishu adapter settings; not stored in this repository.
- `runtime/goutoujunshi/schema.sql`: versioned MySQL schema; contains no password.
- `scripts/wsl/Manage-Goutoujunshi-MySql.sh`: assumes a WSL Docker container and Compose directory outside the authorized repository. Their current content is not proven by this checkout.

## Secret 处理与发布检查

- Never print or copy actual `.env`, auth, token, password, cookie, private-key, session or personal identifier values into documentation or review output.
- Keep `.local/`, `.env*` except a deliberate value-free `.env.example`, dumps, backups, logs, relationship handoffs, import packages and generated projections out of Git.
- Examples use variable names or synthetic placeholders only.
- Installation and preflight commands may read credentials and access external systems; require explicit authorization before execution.
- Before release or commit, inspect the exact staged path list and scan staged text for secret-like assignments and private relationship artifacts.
