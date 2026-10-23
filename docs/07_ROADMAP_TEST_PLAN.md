# 07 — Roadmap, Implementation Sprints and Test Gates

## Sprint 0 — Bootstrap (included in this package)
**Delivered:** offline Python CLI, SQLite registry + event log, path-scoped Git snapshot, task lifecycle, explicit approval record, tests, and docs.

**Gate:** `python -m unittest discover -s tests -v` passes; no access to network or source repo writes by default; `zenith doctor` and `zenith init` work without LLM accounts.

## Sprint 1 — Verified project memory
**Build:** source mapping, project path scanning and sensitivity policy; normalized evidence, snapshot comparison, due dates; import human check-in; event replay/backup and migration framework; basic Textual TUI optional.

**Gate:** (1) same project supports two local directories, (2) manual progress never masquerades as verified, (3) compare two scans shows actual changed revision, (4) no `.env` or symlink escape read, (5) migration and restart tests.

## Sprint 2 — Controlled agent collaboration POC
**Build:** fake agent and subprocess adapters, explicit provider auth check, planner→implementer→reviewer contracts, dedicated Git worktree, policy enforcement and verifier, task lease/recovery, JSON/JSONL parser, budget and time caps.

**Gate:** on sacrificial demo repo only, a task can be drafted/planned/approved, implemented in worktree, tested, reviewed, and truthfully reported. Failure, timeout, duplicate enqueue and malicious prompt injection have negative tests. No auto-push.

## Sprint 3 — Runtime and terminal UX
**Build:** persistent worker, systemd user services, tmux observer, Hyprland per-version workspace mapping, TUI run views, pause/cancel/recover, session mapping.

**Gate:** UI close does not lose job state; no compositor does not fail headless task; crash recovery and cancel leave consistent states; tmux session reconnects.

## Sprint 4 — Proactivity / Telegram
**Build:** notification outbox, Telegram owner allowlist, meaningful events, rate limits, quiet hours, one-click scoped approval challenge, stale project check-ins, daily/weekly digest.

**Gate:** no duplicate notifications after restart; unauthorized chat denied; approval replay denied; project update can be captured while other node offline; no model dependency for basic message.

## Sprint 5 — Integrations and multi-machine
**Build:** GitHub read-only, Gmail and Calendar read-only OAuth with source references, approved calendar writes; authenticated Node/Hub sync; stale/conflict views.

**Gate:** test sources with fake API fixtures; expired tokens handled, protected emails aren't leaked to cloud by default, offline node events reconcile without duplicate task execution.

## Later: Research Intelligence and optional integrations
See `09_RESEARCH_BACKLOG.md`. No deep-research automation in kernel MVP. Also deferred, not dropped: Claude Remote Control as an optional future integration (behind the same adapter boundary and approval gates as any other provider surface).

Sprint 1 acceptance record: `plans/SPRINT_1_ACCEPTANCE.md`. Sprint 2 engineering plan: `plans/SPRINT_2_PLAN.md`.

## Test pyramid
- Unit: state transitions, scope hashes, ID validation, path allowlists, timezone conversion, dedupe, provider event parsing.
- Integration: temporary Git repos and SQLite; fake CLI scripts; process kill/timeout; crash recovery.
- End-to-end: disposable repo, fake mail/calendar, dry-run simulated Telegram; *only then* personal-project sandbox.
- Security: injection corpus, symlink escape, secret exclusion, unauthorized remote command, approval replay, provider token failures, false test assertions.
- Evaluation: retrieval/source grounding, false-positive deadline extraction, check-in precision, summary claims with verified evidence.

## Definition of Done for any integration
- Well-documented scopes and opt-in configuration.
- Can be disabled independently without breaking core.
- Contract tests with success/timeout/auth-error/invalid-output fixtures.
- Safe audit trail and bounded retries.
- Backwards-compatible schema and migration if persistence changes.
- README or runbook updated with an explicit feature status.

## First assignment to local agent
Start at **Sprint 1**; do not scaffold remote Hub or run live agent tasks yet. See `prompts/CLAUDE_IMPLEMENTATION_EN.md`.
