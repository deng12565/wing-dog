# Wing-Dog

![A loving adult man and woman hold hands under the watch of a standing, godfather-like Wing-Dog relationship strategist](assets/wing-dog-hero-v2.png)

[简体中文](README.md) | **English**

> Hold the emotion first, examine the evidence, then choose one next step that can actually be taken.

[![GitHub Stars](https://img.shields.io/github/stars/deng12565/wing-dog?style=social)](https://github.com/deng12565/wing-dog/stargazers)
[![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial-coral)](LICENSE)
[![Codex Skill](https://img.shields.io/badge/Codex-Skill-0f766e)](SKILL.md)

Wing-Dog is an AI relationship strategist built for real conversations and real constraints. The user can simply submit a new message, screenshot, or offline update; Wing-Dog then identifies the current objective and returns the action to take now, copy-ready content when needed, execution timing, and the next decision point.

This project is an adaptation and extension of [powerycy/goutoujunshi](https://github.com/powerycy/goutoujunshi). It is not an original-from-scratch work, and it does not imply upstream participation or endorsement. Wing-Dog retains the upstream relationship-advice Skill while adding Hermes and Feishu integration, authoritative MySQL storage, person and channel isolation, cross-channel retrieval, privacy-bounded public web search, resumable enrichment jobs, and local operations. The upstream license and its Required Notice remain intact in [LICENSE](LICENSE).

## How it helps

| Real situation | What Wing-Dog does |
| --- | --- |
| You post a new reply without asking a question | Automatically gives the current action, timing, and next decision point |
| You do not know how to reply | Gives one copy-ready preferred reply and says what to observe next |
| An invitation or escalation window is ready | Proposes the appropriate move without waiting for the user to ask for permission |
| You ask what to do when meeting | Distinguishes general learning, a scheduled date, an imminent meeting, live context, and review |
| The other person is inconsistent | Separates pacing differences and temporary pressure from sustained imbalance, with observation and stop signals |
| There are many screenshots or unclear speakers | Resolves authorship and sequence first, and treats only visible text and behavior as facts |
| History is split across WeChat, Douyin, Moments, and offline contact | Searches across channels for the same person while keeping drafts and sent-state channel-specific |
| Advice depends on current public information | Searches only on demand in a bound relationship group, anonymizes the query first, and cites the page title, URL, and retrieval date |
| You are getting to know several people | Keeps each relationship independent and compares reciprocity, reliability, attraction, values, and feasibility |
| There is no current person | Starts from real life and sustainable ways to meet people, without inventing a target or imposing a deadline |
| There is a clear refusal or safety risk | Stops escalation; prioritizes safety for control, stalking, coercion, fraud, or violence |

## What this fork adds

```text
Relationship-advice Skill
      |
      +-- Codex: loads relevant relationship knowledge and produces a next step
      |
      +-- Wing-Dog private runtime
            +-- Feishu / Hermes: mobile entry point and owner verification
            +-- MySQL: authoritative people, channels, events, and owner memory
            +-- Search: source text + ngram + bounded enrichment retrieval
            +-- Web: anonymized, read-only public search in bound groups only
            +-- Projection: generated read-only relationship review files
```

- **Mobile access** through controlled Feishu groups and Hermes Gateway.
- **Bounded relationship memory** with independent people and channels; only owner facts are shared across groups.
- **Explicit message state** separating `received`, `sent`, `draft`, `background`, `analysis`, and `correction`.
- **Authoritative retrieval** where derived summaries help find events but are never returned as facts.
- **Anonymized web queries** for necessary public facts, returning titles, URLs, and snippets without automatically persisting them as relationship memory.
- **Server-side authorization** where relationship and owner-memory tools use Hermes session state and recheck the owner, chat, and current person binding without model-copied tokens.
- **Fail-closed behavior** when MySQL is unavailable or the active person binding is ambiguous.
- **Advice only**: Wing-Dog never sends messages to WeChat, Douyin, or another external contact on the user's behalf.

## Two supported surfaces

The repository maintains two surfaces with different trust boundaries:

1. **Distributable Codex Skill**: `SKILL.md`, `agents/`, `references/`, and `tests/` provide behavior, on-demand knowledge, and scenarios. The Skill alone has no database, background service, or external-message writer.
2. **Private Hermes runtime**: `runtime/` and the operations scripts integrate Feishu, Hermes Gateway, and WSL/Docker MySQL for identity checks, person binding, persistence, relationship retrieval, controlled public web search, and read-only projections.

Neither surface proves the other is healthy. Repository code does not prove that a local deployment is running, and installing the Skill does not automatically provide Feishu or persistent relationship memory. See the [architecture document](documentation/architecture.md) for details.

## Install the Codex Skill

Clone the repository into the Codex Skills directory:

```bash
git clone https://github.com/deng12565/wing-dog.git ~/.codex/skills/wing-dog
```

Then ask Codex:

```text
Use $goutoujunshi to read the latest relationship update and tell me the current objective, what to do now, when to do it, and what signal should trigger the next step.
```

If there is no specific person, describe your real life, usual ways of meeting people, goal, and main obstacle. When someone is involved, post the latest context or screenshot and identify the source when it is not visible. Wing-Dog does not require a questionnaire and asks at most one question that would materially change the action.

> [!IMPORTANT]
> Hermes, Feishu, and MySQL form a separate controlled deployment surface. They are not installed by cloning the Skill. Installation, startup, shutdown, migration, historical enrichment, and external preflight commands are side-effectful and require explicit review and authorization.

## Always-on server deployment

`deployment/linux/` provides an isolated `gateway + mysql + backup` Compose stack for a Rocky Linux host. MySQL has no published host port; the Gateway derives from a digest-pinned Hermes 0.20.4 image, and DDGS is installed from a SHA256-locked offline wheelhouse. See [Server deployment and local cold backup](documentation/server-deployment.md) for the migration, rollback, and repatriation procedure.

The server and local Windows environment are not active-active. The local scheduled task is disabled only during cutover, while the local code, Hermes installation, WSL MySQL, secrets, archives, and backups are retained indefinitely without automatic synchronization.

## Compatibility identifiers

This release uses a layered rename to protect existing deployments. The public brand is **Wing-Dog**, while the following internal identifiers remain temporarily unchanged:

- Skill invocation: `$goutoujunshi`
- Python package and runtime path: `runtime/goutoujunshi/`
- Database and default database user: `goutoujunshi`, `goutoujunshi_app`
- Environment-variable prefix: `GOUTOUJUNSHI_*`
- Existing Hermes toolset/profile, scheduled-task, and operations-script identifiers

These are legacy compatibility identifiers, not a second product brand. Renaming them directly would affect database privileges, installed plugins, Hermes routes, scheduled tasks, environment configuration, and rollback behavior, so that migration belongs in a separate version.

## Relationship data and retrieval

The MySQL `goutoujunshi` database is the sole authoritative relationship source. `.local/relationships/` contains generated read-only projections and must not be edited manually.

Schema v6 retains MySQL 8 `ngram` FULLTEXT retrieval and adds SHA256-bound structured imports plus append-only recovery for invalid inferred-sent events. Retrieval combines three candidate branches inside one person binding:

1. Exact and substring matches against authoritative source text.
2. Ngram full-text retrieval against authoritative source text.
3. Retrieval against bounded summaries, concepts, aliases, entities, and time hints.

After fixed RRF ranking, the runtime hydrates authoritative event bodies from MySQL and includes the correction closure. It returns at most eight events by default. Enrichment text is a retrieval aid only and is never presented as factual event content. Relationship-history retrieval does not require Ollama, Milvus, or local embeddings.

## Controlled public web search

Hermes plugin 1.8.0 exposes `relationship_web_search` only inside a Wing-Dog relationship group with an active person binding. The tool rechecks the owner, chat, and current MySQL binding from server-side session state, applies a second anonymization pass to a minimal query, and then asks the Hermes provider registry for the exact keyless `ddgs` provider. It never calls the generic search entry point or falls back to another provider; missing or unavailable DDGS fails closed. Unbound groups have owner-memory tools only and cannot search the web.

Each search returns at most five titles, URLs, and snippets. It does not fetch full pages, expose a browser, or promise a reliable publication date. Responses must separate web information, authoritative MySQL relationship memory, and model inference, and cite the title, URL, and retrieval date for web material. Any instruction inside a page title, snippet, or other web text is untrusted data and must never be executed. Results stay in the current Hermes session and are not automatically written to events, snapshots, drafts, owner memory, or Markdown projections. Anonymization rejection, timeout, and provider failure are reported as explicit degradation.

An ordinary follow-up no longer marks the previous draft as sent. A draft is resolved only by a strict owner confirmation, manual draft review, or a new `received` event for the same person and channel with `confirm_previous_draft`. Structured history imports require matching source and manifest hashes plus one unique active person. A `wechat-agent-archive/v1` import additionally requires an explicitly confirmed local WeChat author and deduplicates overlapping exports by conversation and message anchor. ASR, missing media, and `PARTIAL` exports retain derived/uncertain provenance instead of being represented as verbatim dialogue; immutable archive evidence is never overwritten.

## Repository layout

```text
wing-dog/
|-- SKILL.md                    # Wing-Dog behavior and routing kernel
|-- agents/openai.yaml         # Codex display metadata and default prompt
|-- references/
|   |-- knowledge/             # Relationship science and related knowledge
|   |-- practical/             # Practical communication and strategy guides
|   `-- THIRD_PARTY_NOTICES.md # Third-party sources and licenses
|-- tests/                     # Skill scenario specifications
|-- documentation/             # Architecture, flows, permissions, and tests
|-- deployment/linux/          # Rocky Linux Compose and operations scripts
|-- runtime/
|   |-- goutoujunshi/          # Compatibility-named Hermes plugin and MySQL layer
|   |-- benchmarks/            # Synthetic Chinese retrieval set and evaluators
|   |-- tests/                 # Private runtime unit tests
|   |-- bootstrap.py           # Install, configuration, and static verification
|   `-- goutoujunshi_cli.py    # Data and route maintenance CLI
`-- scripts/                   # Skill validator and local operations scripts
```

## Verification

```powershell
python scripts\validate_skill.py
python -m unittest discover -s runtime\tests -v
```

The first command validates the Skill structure, budget, links, and runtime boundary. The second covers the plugin, data rules, relationship retrieval, controlled public web search, exports, routing, and isolated bootstrap behavior. Neither command proves that a real MySQL, Hermes, DDGS, Feishu, scheduled task, or external model is currently healthy.

Further reading: [Product](documentation/product.md) · [Architecture](documentation/architecture.md) · [Server deployment and local cold backup](documentation/server-deployment.md) · [Flows](documentation/flows.md) · [Variables and secrets](documentation/variables.md) · [Permissions](documentation/permissions.md) · [Tests](documentation/tests.md)

## Design principles

1. **Hold the person before solving the problem.** Advice is hard to use when the emotion has not been recognized.
2. **Letting things develop naturally still includes action.** Express, invite, and stop respectfully when the response is absent.
3. **Behavior is stronger evidence than labels.** Do not mind-read from MBTI, gender, or a single exchange.
4. **Reciprocity matters more than winning someone.** Less rumination, preserved dignity, and future options also count as success.
5. **Every update produces an order.** Even without a direct question, give the action to take now, its timing, and the next decision point; expand the full strategy only when needed.
6. **Consent and the right to exit are non-negotiable.** A clear refusal is not an obstacle to bypass.
7. **Safety comes first in dangerous situations.** Violence, coercion, stalking, fraud, and self-harm risk are not ordinary dating problems.

## Attribution, license, and contributions

Wing-Dog is adapted from [powerycy/goutoujunshi](https://github.com/powerycy/goutoujunshi) and translates selected third-party research into general interaction capabilities that do not depend on a named persona or mode. See [Third-Party Notices](references/THIRD_PARTY_NOTICES.md) for exact baselines, licenses, and research-only declarations.

The project remains under the [PolyForm Noncommercial License 1.0.0](LICENSE) and preserves the upstream required notice:

```text
Required Notice: Copyright 2026 powerycy.
```

Research corrections, clearer language, evidence updates, and anonymized scenarios are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before contributing.

Wing-Dog provides relationship education and decision support. It does not replace therapy, medical diagnosis, legal advice, police, or emergency services.
