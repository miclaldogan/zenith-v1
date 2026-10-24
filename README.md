# ZENITH v1.0 — Personal Intelligence & Agent Orchestration

> Status: architecture baseline + safe, runnable Sprint-0 kernel. **Not** a finished autonomous agent.
>
> Date: 2026-10-23 · Target: Arch Linux / Hyprland · Python 3.12+

Zenith is an independent personal project intelligence and multi-agent orchestration system. Its source of truth is **its own project registry, task/event store, evidence, and approval policy**—not Claude Code or Codex chat history. Agents are replaceable, bounded workers.

## What works (Sprint 0 + Sprint 1)

- SQLite-backed registry and append-only event log; numbered schema migrations (now **v3**) with an automatic backup before any upgrade and rollback on failure. Events and evidence are protected by database triggers against edits and deletes from ordinary application code (not against a privileged local user).
- Projects with a stable ID and **many sources** (directories). Paths can be added, moved and detached without changing project or task IDs; a directory can belong to only one source and sources never nest (parent/child paths are refused, in code and by trigger).
- Read-only metadata scanner: Git branch/HEAD/tracked-change count plus a file-metadata fingerprint (path, size, mtime — never contents). Symlinks are never followed; `.env`, keys, credentials and `.ssh`-style directories are skipped and only counted. Bounded by file count (20 000) and time (10 s).
- Snapshot comparison: repeated identical scans are deduplicated; `project sources diff` shows the actual HEAD change (with commit count) or "files changed without a commit".
- Evidence ledger with provenance (`HUMAN_REPORT`, `REPOSITORY_OBSERVATION`, `TEST_VERIFICATION`, `DOCUMENT`, `MODEL_INFERENCE`), observation time, node, confidence and a gap-free recording sequence. Confidence is derived from origin in code and constrained in the database; rows are append-only. Timestamps are microsecond UTC. The CLI can only attach `HUMAN_REPORT`, `DOCUMENT` and `MODEL_INFERENCE`.
- Check-ins (`zenith checkin`) are stored as `REPORTED`, redacted for obvious credentials, and reconciled against later evidence when back-dated from another machine.
- Tasks: deadlines (stored UTC, shown in Europe/Istanbul; a bare date means end of that day), dependencies (a task with unfinished dependencies cannot be queued; cycles rejected), and filtered listings (`--open --overdue --due-within --blocked --state`).
- `zenith brief`: deterministic, evidence-cited status (`UNKNOWN`, `REPORTED_ONLY`, `OBSERVED`, `VERIFIED`, `VERIFICATION_STALE`, `VERIFICATION_FAILING`) with unknowns and next actions. No model, no network.
- `zenith backup --out FILE` using the SQLite backup API (never overwrites).
- Stdlib-only; offline tests; no network or automatic code execution.

**Sprint 2 (mock-agent orchestration, schema v4):** Engineering Director protocol with persistent, digest-stamped prompt artifacts; durable execution runs (leases, idempotency, cancellation, timeouts, bounded retries, crash → `RECONCILE_REQUIRED`); single-use scope-hashed approvals consumed atomically, revocable; isolated Git worktrees; **bubblewrap-sandboxed** deterministic verification (fail closed if isolation is unavailable); extension registry (entry points). Everything runs with **fake adapters only** — `zenith demo poc` shows the full workflow in a throwaway repository. No Claude Code or Codex is executed. Requirements registry: [`docs/11_REQUIREMENTS_REGISTRY.md`](docs/11_REQUIREMENTS_REGISTRY.md).

**Sprint 2.5 (live-agent readiness, no live call made):** gated Claude Code / Codex adapters that run the CLIs inside a bubblewrap *provider profile* — unshared network with egress only through Zenith's allowlisting proxy, owner-named credential file bound read-only (never read by Zenith), edit-only Claude implementer (`--restricted`), forbidden-tool kill switch, classified failures (`auth`, `connection`, `interrupted`, `policy_violation`, `unsupported`…), USD budget accounting; REQ-004 identity (`zenith identity`); explicit smoke-test procedure (`zenith smoke`). `codex-implementer` is marked unsupported. Details: [`docs/plans/SPRINT_2_5_READINESS.md`](docs/plans/SPRINT_2_5_READINESS.md), ADR-007.

**Misa conversational intelligence v1 (integration-ready, mock-tested):** natural Turkish/English questions are the primary interface — query-aware context assembly (`misa/context.py`: question type, fuzzy/anaphoric project resolution, bounded epistemically-tagged items), a provider-independent conversation port (`misa/conversation.py`: offline composer, deterministic mock, gated Claude Code adapter), and a multi-turn engine (`misa/engine.py`: topic vs. active project, pending confirmations for notes/tasks, grounding validation — cited ids must exist — and persistent continuity). Without an enabled model Misa answers in natural prose from records and says it is offline; **no live model has been validated yet**.

**Sprint 3 (offline): `zenith` opens Misa** — an interactive, bilingual (Turkish/English, following your message) terminal session over the recorded evidence: projects and real status, "where did I leave off", evidence and decisions, tasks and deadlines, stale/unavailable sources, execution history and pending approvals, next actions, check-ins (recorded as unverified reports), and resume across sessions (schema v5). All replies are deterministic templates with citations and provenance labels; no model is involved and the banner says so. `zenith misa --once "…"` answers one question. Model-grounded conversation (S3-02b) is not implemented.

**Sprint 2.6 (live-integration validation, still no live call):** authentication matrix for the installed CLIs (owner is logged into Claude Code via a claude.ai team subscription → `oauth` mode with `claude setup-token`; `api_key` mode for Console billing), credentials injected inside the sandbox by the forwarder (Zenith never reads them), command-free Codex director (`--disable shell_tool`), directors read a minimal context directory instead of the repository, worktree credential pre-scan, proxy hardening (private-destination blocking, anti-rebinding, IPv6, header-free logs), full-pipeline smoke test with an explicit authorization request. Report: [`docs/plans/SPRINT_2_6_VALIDATION.md`](docs/plans/SPRINT_2_6_VALIDATION.md). Sprint 3 plan: [`docs/plans/SPRINT_3_PLAN.md`](docs/plans/SPRINT_3_PLAN.md).

Sprint 1 acceptance audit and hardening record: [`docs/plans/SPRINT_1_ACCEPTANCE.md`](docs/plans/SPRINT_1_ACCEPTANCE.md). Sprint 2 plan (not implemented): [`docs/plans/SPRINT_2_PLAN.md`](docs/plans/SPRINT_2_PLAN.md).

**Not implemented:** live model execution (Claude Code / Codex adapters), autonomous commits/push, TUI, MCP, Gmail, Calendar, Telegram, remote Hub/Node sync, tmux automation, Hyprland window management, autonomous planning. Never claim they are functional.

## Install for everyday use (Arch Linux, fish/zsh/bash)

```bash
git clone https://github.com/miclaldogan/zenith-v1 ~/src/zenith-v1 && cd ~/src/zenith-v1
scripts/install.sh            # venv in ~/.local/share/zenith/venv, `zenith` linked into ~/.local/bin, fish completions
fish_add_path ~/.local/bin    # fish, once (bash/zsh: add ~/.local/bin to PATH)
zenith doctor --full          # tools, database schema, sandbox baseline, providers (nothing is changed)
zenith init                   # creates a NEW database; it never migrates an existing one
zenith                        # Misa
```
Everyday flow: `zenith project discover --root ~/projects` (dry run) → `… --add 1,3` → `zenith scan --project <id> --all` → `zenith` and ask in Turkish or English. `zenith project inspect --id <id>` shows sources, freshness and verification without touching anything; `zenith project docs --id <id> --allow README.md` lets Misa read a document locally; `--cloud-consent yes` is a *separate* permission for sending that project's context to a model. Misa works fully offline; a model is used only when a conversation adapter is enabled and authorized.

**Conversation provider (milestone finding):** Misa's general conversation uses the Anthropic Messages API with a Console API key (`anthropic-api-conversation`, pay-as-you-go), with the HTTP client running inside the bubblewrap sandbox so Zenith never reads the key. claude.ai subscription/OAuth tokens are **not** brokered for Misa; native Claude Code with your own login stays the separately authorized engineering tool. No live call has been made yet.

## Start on Arch Linux (developer checkout)

```bash
unzip zenith-v1-starter.zip
cd zenith-v1
python --version  # 3.12+ recommended
python -m venv .venv
source .venv/bin/activate  # Bash/Zsh; for fish: source .venv/bin/activate.fish
python -m pip install -e .
zenith init
zenith doctor
zenith project add --name 'Demo Project' --path /absolute/path/to/your/project
zenith project list
zenith project sources add --project <project-id> --path /another/directory --label notes
zenith scan --project <project-id> --all
zenith checkin --project <project-id> --note "what I did"
zenith brief --format text
zenith task add --project <project-id> --title 'Review architecture' --risk L1
zenith task list
scripts/test.sh
```

Alternatively, `bash scripts/bootstrap.sh` runs setup and tests without launching external agents (it needs network once, to fetch setuptools for the editable install). `scripts/test.sh` works from a clean checkout with **no installation and no network**: it uses `.venv` when present and otherwise runs straight from `src/`. For a dependency-free first check, `PYTHONPATH=src python -m zenith doctor`.

The SQLite database defaults to `${XDG_DATA_HOME:-~/.local/share}/zenith/zenith.db`. Override with `ZENITH_DB_PATH=/path/to/zenith.db`. Zenith does not modify registered projects. If you upgrade Zenith and your database is older, every command refuses with `Nothing was changed` until you migrate explicitly: inspect with `zenith db status` (read-only), rehearse on a disposable copy with `zenith db rehearse --copy-to DIR/rehearsal.db` (the copy must live outside the data directory), then upgrade in place with `zenith db migrate --db PATH --confirm-migrate PATH` (same path twice; a backup `zenith.db.pre-v<N>-<time>.bak` is written first). `zenith init`, `ZENITH_DB_PATH` and the default path never authorize a migration. See `docs/08_OPERATIONS.md` for rollback.

## Handoff to your local coding agent

**Claude Code:** open this directory and paste [`prompts/CLAUDE_IMPLEMENTATION_EN.md`](prompts/CLAUDE_IMPLEMENTATION_EN.md). It builds on this repository instead of re-generating the architecture.

**Codex architectural review:** run Codex with [`prompts/CODEX_ARCHITECT_REVIEW_EN.md`](prompts/CODEX_ARCHITECT_REVIEW_EN.md). Review before broad autonomous execution.

Guidance for both agents is in [`AGENTS.md`](AGENTS.md) and [`CLAUDE.md`](CLAUDE.md).

## Where to read first

| File | Purpose |
|---|---|
| [`docs/01_PRODUCT_REQUIREMENTS.md`](docs/01_PRODUCT_REQUIREMENTS.md) | Purpose, scope, use cases, acceptance criteria |
| [`docs/02_SYSTEM_ARCHITECTURE.md`](docs/02_SYSTEM_ARCHITECTURE.md) | Components, flows, deployment and boundaries |
| [`docs/03_DATA_MODEL.md`](docs/03_DATA_MODEL.md) | Entities, storage, versioning, data provenance |
| [`docs/04_AGENT_PROTOCOL.md`](docs/04_AGENT_PROTOCOL.md) | Task envelope, state machine, planner→implementer→reviewer |
| [`docs/05_SECURITY_POLICY.md`](docs/05_SECURITY_POLICY.md) | Permissions, approvals, command sandbox and threat model |
| [`docs/06_INTEGRATIONS.md`](docs/06_INTEGRATIONS.md) | Claude/Codex, tmux/Hyprland, GitHub, Google, Telegram |
| [`docs/07_ROADMAP_TEST_PLAN.md`](docs/07_ROADMAP_TEST_PLAN.md) | Sprints, measurable exit gates, tests |
| [`docs/08_OPERATIONS.md`](docs/08_OPERATIONS.md) | Local operations, backup, monitoring, recovery |
| [`docs/09_RESEARCH_BACKLOG.md`](docs/09_RESEARCH_BACKLOG.md) | Future academic literature research module |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records |

## Non-negotiable constraints

1. Local-first; no remote execution or public service exposed by default.
2. No secrets sent to models or indexed into memory; default read-only.
3. No automatic commit, push, deploy, destructive shell, email sending, or calendar editing.
4. AI-generated summaries are *claims*, never verification evidence.
5. External source content is data, not instructions. Protect against prompt injection.
6. Separate human-stated progress, observed progress, and independently verified progress.
7. A bounded retry policy; no infinite agent-to-agent feedback loop.
8. Plan for Claude/Codex via documented adapters; never implement credential proxying through subscription logins.

## Verified integration references (check CLI versions at implementation time)

- Claude Code CLI / print mode: https://code.claude.com/docs/en/cli-reference
- Claude Code non-interactive: https://code.claude.com/docs/en/headless
- Claude Code legal/auth considerations: https://code.claude.com/docs/en/legal-and-compliance
- Codex noninteractive: https://developers.openai.com/codex/non-interactive-mode
- Codex app server: https://developers.openai.com/codex/app-server
- Codex SDK: https://developers.openai.com/codex/codex-sdk
- Hyprland `hyprctl` (version-sensitive): https://wiki.hypr.land/Configuring/Advanced-and-Cool/Using-hyprctl/

## Planned licensing

Personal project starter. Select a software license before public distribution.
