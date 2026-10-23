# Claude Code — Zenith Sprint 1 implementation directive (EN)

You are the lead engineer implementing the next increment of **Zenith**, a personal, local-first, evidence-driven agent orchestration system.

## Before editing
1. Read `AGENTS.md`, `CLAUDE.md`, `README.md` and all `docs/01`–`docs/08` documents. Do not reinterpret or silently expand product scope.
2. Inspect the existing `src/zenith/` package, `tests/`, and `pyproject.toml`. This starter is real, working code. Extend rather than rewrite.
3. Run `python -m unittest discover -s tests -v` and record the baseline.
4. Identify current gaps against **Sprint 1** in `docs/07_ROADMAP_TEST_PLAN.md`.
5. Keep a short execution checklist with acceptance evidence; report meaningful architecture deviations as proposed ADRs.

## Assignment
Implement **Sprint 1: Verified Project Memory** as small, reviewed, testable increments. Target:
- Migrations from the initial schema without destroying existing user state; schema version registry.
- Support multiple `project_source` entries per project and stable `project_id` independent of paths.
- Explicit path allowlist + symlink-escape avoidance + sensitive file exclusion. Never read secrets.
- Provenance-stamped evidence model: HUMAN_REPORT, REPOSITORY_OBSERVATION, TEST_VERIFICATION, DOCUMENT, MODEL_INFERENCE; observation timestamp and confidence.
- Incremental snapshot comparison against last verified observation; evidence-based status (do not equate commits with completion).
- Tasks: optional dependencies and deadlines, sensible listing/filtering. Keep state transitions code-enforced.
- Human check-in capture with a short note; mark as `REPORTED`, not `VERIFIED`.
- `zenith project sources`, `zenith evidence`, `zenith checkin`, `zenith brief` CLI subcommands (briefing may be deterministic, avoid cloud models in Sprint 1).
- Write a safe, bounded backup/restore migration test. Use SQLite backup API.
- README and runbook update with actual behavior, not planned features.

## Hard constraints
- DO NOT invoke Claude/Codex recursively, run hidden agents, create cloud credentials or connect real Gmail/Calendar/Telegram.
- DO NOT modify user's unrelated repositories; use only this Zenith working copy and disposable test repositories.
- DO NOT implement unrestricted shell execution, automated Git push, remote code execution, or bypass approval gates.
- DO NOT send project contents to external LLM services in this sprint.
- Preserve Python stdlib-only setup unless there is a strong, documented reason to add a small dependency.
- All critical writes must be transactional and emit an audit event.
- Handle nonexistent paths, stale device evidence and invalid JSON cleanly.

## Validation (must perform)
- Run the complete test suite, not just newly added tests.
- Add tests for two source paths mapped to one project, path move, symlink traversal exclusion, secret exclusion, duplicated snapshots, stale human progress vs verified evidence, schema upgrade and rollback safety.
- Check the CLI from an empty temporary `ZENITH_DB_PATH`.
- Audit your own diff for hidden writes, credentials or unapproved network access.

## Definition of done
Provide: (a) changed files, (b) test command/result, (c) feature demo commands, (d) known limitations and security considerations, (e) any ADRs proposed. Stop before Sprint 2. Do not automatically commit or push.

## Follow-up engineering strategy (not for immediate execution)
After Sprint 1, architect a separate Sprint 2 plan for a **FakeAgentAdapter** plus documented Claude/Codex CLI adapters with isolated Git worktrees, JSON/JSONL streams, time/budget caps, approval-gated commands and deterministic test verification. Prepare but do not run live provider calls without explicit authorization.
