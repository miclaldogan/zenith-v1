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

Sprint 1 acceptance audit and hardening record: [`docs/plans/SPRINT_1_ACCEPTANCE.md`](docs/plans/SPRINT_1_ACCEPTANCE.md). Sprint 2 plan (not implemented): [`docs/plans/SPRINT_2_PLAN.md`](docs/plans/SPRINT_2_PLAN.md).

**Not implemented:** agent execution, test execution (so nothing can reach `VERIFIED` through the CLI yet — `record_test_verification` is library plumbing for Sprint 2's deterministic verifier), TUI, MCP, Gmail, Calendar, Telegram, remote Hub/Node sync, tmux automation, Hyprland window management, autonomous planning. Never claim they are functional.

## Start on Arch Linux

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

The SQLite database defaults to `${XDG_DATA_HOME:-~/.local/share}/zenith/zenith.db`. Override with `ZENITH_DB_PATH=/path/to/zenith.db`. Zenith does not modify registered projects. If you upgrade from an older database, `zenith doctor` says so; run `zenith init` once: it backs the database up (`zenith.db.pre-v<N>-<time>.bak`) and migrates it in place. See `docs/08_OPERATIONS.md` for the rollback procedure.

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
