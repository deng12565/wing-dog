# Project Agent Rules

## Long-Term Dating Goal

For this user, the default long-term goal is to naturally meet women and develop a mutually willing girlfriend relationship without binding the whole strategy to any one person. Do not impose a dating deadline, weekly quota, or guarantee.

Treat "顺其自然" as active but non-coercive: recommend one clear expression, invitation, observation, or stop action when the evidence supports it. If a person clearly refuses, is incompatible, or repeatedly shows no reciprocal investment, help the user stop spending energy and return attention to their own life and other possibilities without demeaning or punishing anyone.

Keep every woman's relationship record independent. A current person's profile may personalize the current answer but must not narrow the general Skill, contaminate another profile, or turn friendliness into confirmed romantic interest.

## Relationship Data Authority

1. The `goutoujunshi` database in the WSL MySQL 8 container is the only authoritative relationship data source.
2. Files under `.local/relationships/` are generated, read-only projections for Codex and human review. Never edit, patch, append to, or create a relationship Markdown file directly.
3. Add or correct relationship data only through the installed Hermes relationship tools or the project runtime in `runtime/goutoujunshi/`. Corrections append new `correction` events and never overwrite history.
4. If MySQL is unavailable or the current Feishu group has no unambiguous binding, fail closed: say that the message was not recorded or analyzed. Do not fall back to a generic guess.
5. The immutable source archive and its SHA256 under `.local/archive/imports/` are migration evidence. Never rewrite or delete them.

## Relationship Context Maintenance

For every ongoing relationship or chat-advice task in this project:

1. Resolve exactly one active person binding before analysis. Load only that person's current snapshot, confirmed facts, conservative judgments, unknowns, current-channel draft, and recent relevant events. Search older events only as needed.
2. Keep `received`, `sent`, `draft`, `background`, `analysis`, and `correction` separate. Never record a draft as sent unless the user explicitly confirms it or the same person and same channel subsequently has a new received message. An explicit correction always wins.
3. Determine source in this order: explicit user statement or correction, text prefix, screenshot interface evidence, then current channel. If still uncertain, ask before writing a definite event.
4. Keep WeChat, Douyin, Moments, and offline records independent. Activity in one channel cannot confirm a draft in another.
5. Screenshots are ephemeral input. Do not store image files, paths, binary data, or irrelevant identifiers in MySQL or Markdown.
6. Preserve uncertainty. Do not turn friendliness into confirmed romantic interest, invent missing context, or repeatedly ask for information the user has already said they do not know.
7. Save an exact copyable reply suggestion as `draft`; keep the surrounding analysis out of draft content.
8. The bot provides advice and record maintenance only. It must never send or automate messages to a woman on WeChat, Douyin, or another external channel.

## Development And Maintenance

1. For project work, read this file first, then `README.md`, `documentation/architecture.md`, and the document for the subsystem being changed. Read `.local/operator/HERMES_狗头军师用户手册.md` for operator behavior and `.local/operator/HERMES_飞书接管故障修复复盘.md` only when installation history or troubleshooting matters.
2. Distinguish repository implementation, documentation claims, test definitions, prior test reports, and current runtime observations. Static source proves only the checked-out implementation; do not claim MySQL, Hermes, Feishu, a scheduled task, or an external model is healthy without a current authorized check.
3. Treat `scripts/Setup-And-Start-Goutoujunshi.ps1`, `scripts/Run-Goutoujunshi.ps1`, `scripts/Control-Goutoujunshi.ps1`, `scripts/wsl/Manage-Goutoujunshi-MySql.sh`, database CLI commands, and `runtime/bootstrap.py` configuration or preflight commands as side-effectful. Do not run them without explicit authorization for the exact command and external systems involved.
4. Keep documentation synchronized when architecture, hooks, tools, schemas, environment variables, permissions, deployment, startup, or verification commands change. Use `README.md` as the human entry point and `documentation/` for detailed contracts; keep the user manual operational and the incident review historical.
5. Never add `.local/`, relationship handoffs, import packages, screenshots, logs, backups, dumps, generated projections, temporary output, `.env` files, credentials, or other private runtime state to Git. Review the exact staged path list before every commit.
6. Preserve the two supported surfaces: the distributable Codex Skill (`SKILL.md`, `agents/`, `references/`, and its validator) and the private Hermes runtime (`runtime/` plus operational scripts). Do not describe one surface as proof of the other's installed or running state.
