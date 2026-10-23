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

**Sprint 2.6 (2026-10-23):** live path validated offline (auth matrix, in-sandbox credential injection, command-free Codex director, director context dirs, proxy hardening); first live smoke test prepared and awaiting owner authorization — `plans/SPRINT_2_6_VALIDATION.md`; Sprint 3 plan — `plans/SPRINT_3_PLAN.md`.

**Sprint 2.5 (2026-10-23):** live adapters prepared and gated (`claude-code-director`, `claude-code-implementer`, `codex-director`; `codex-implementer` unsupported), allowlist egress proxy, identity (REQ-004), smoke-test procedure — see `plans/SPRINT_2_5_READINESS.md`. **No live call made.**

**Status 2026-10-23:** gate met with **fake adapters** (`zenith demo poc`, `tests/test_sprint2.py`, 35 tests). Live Claude Code / Codex adapters are not started (S2-09, owner-gated). Requirements registry: `11_REQUIREMENTS_REGISTRY.md`.

## Sprint 3 — Runtime and terminal UX
**Build:** persistent worker, systemd user services, tmux observer, Hyprland per-version workspace mapping, TUI run views, pause/cancel/recover, session mapping.

**Gate:** UI close does not lose job state; no compositor does not fail headless task; crash recovery and cancel leave consistent states; tmux session reconnects.

## Sprint 4 — Proactivity / Telegram
**Build:** notification outbox, Telegram owner allowlist, meaningful events, rate limits, quiet hours, one-click scoped approval challenge, stale project check-ins, daily/weekly digest.

**Gate:** no duplicate notifications after restart; unauthorized chat denied; approval replay denied; project update can be captured while other node offline; no model dependency for basic message.

## Sprint 5 — Integrations and multi-machine
**Build:** GitHub read-only, Gmail and Calendar read-only OAuth with source references, approved calendar writes; authenticated Node/Hub sync; stale/conflict views.

**Gate:** test sources with fake API fixtures; expired tokens handled, protected emails aren't leaked to cloud by default, offline node events reconcile without duplicate task execution.

## Requirement → milestone map (Misa Intelligence directive)
| Requirement | Milestones |
|---|---|
| REQ-005 Misa Personal Intelligence | S3 (status board, guided flow, decision continuity) → S4 (notification outbox, Telegram, check-ins, digests, commitments, quiet hours) → S5 (multi-machine context retrieval) |
| REQ-006 Research Intelligence | Sprint 6: research module behind the registry; source verification + DOI references first, reports second, scheduling + delivery third |
| REQ-007 Creativity Studio | Sprint 7: ideas domain, provenance, novelty-vs-evidence (depends on REQ-006) |
| REQ-008 Model Independence | S2 ports (done) → S3-07 config → S4 deterministic router → additional providers after S3-09 |
| REQ-009 Multi-Agent Specialist Coordination | engineering roles done (S2–2.6) → S3-08 role-capable module manifests → Sprint 6 role planner + research roles → Sprint 7 creative role |
| REQ-010 Persona & Communication Policy | S3-03 deterministic Turkish templates with provenance → S3-07 persona configuration → S4 persona on Telegram under notification policy |

## Sprint 6 — Research Intelligence (plan)
**Build:** research module behind the registry; scholarly provider port with OpenAlex/Crossref/arXiv/Semantic Scholar adapters evaluated; source records with evidence tiers, retrieval dates and hashes; Evidence Judge and Methodology Judge as schema-driven roles; research artifacts; source-grounded reports; delivery via the S4 notification outbox; discussion-context continuation.
**Gate:** REQ-006 acceptance criteria (1)–(6); fixture-replayed provider tests (no live calls in the suite); a fabricated citation injected by a mock model is caught and labelled.

## Sprint 7 — Creativity Studio (plan)
**Build:** ideas domain with provenance, idea↔project/research links, novelty-vs-evidence comparison via REQ-006 verification, idea → research/engineering plan, optional dedicated creative model behind REQ-008.
**Gate:** no idea content leaves the machine without per-idea consent; novelty claims always cite the literature check that backs them.

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
