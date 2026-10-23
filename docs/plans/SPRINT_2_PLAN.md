# Sprint 2 — Controlled agent collaboration POC: implementation plan

**Status:** plan only (2026-10-23). Nothing here is implemented. Live Claude Code / Codex execution is **out of Sprint 2 scope**; Sprint 2 ends with the fake-adapter proof of concept. Decisions that change architecture are in `docs/adr/ADR-005-agent-execution-boundaries.md` (proposed).

## 0. Principles carried over
- Zenith owns task state; adapters return *claims*. No model output grants permission, changes authorization, or marks anything verified.
- Deny by default, argv-only sub-processes, no shell, no `--dangerously-*` flags, no auto-push.
- Every mutation is an event; every side effect is preceded by a persisted record (lease, approval consumption) in the same transaction.
- Git worktree isolation ≠ process sandboxing (ADR-005).

## 1. Agent adapter interface (`zenith/agents/base.py`)

Provider-independent, dataclass-based, no inheritance from the kernel:

```python
class AgentCapabilities:  streaming: bool; resume: bool; structured_output: bool; budget_cap: bool; sandbox_modes: tuple[str, ...]
class AuthStatus:         ok: bool; detail: str          # never contains tokens
class AgentRequest:       run_id, role, task_envelope (zenith.acp.v1), worktree, allowed_roots, max_wall_seconds, max_budget_usd, output_schema
class AgentEvent:         seq, kind ("text"|"tool_use"|"result"|"error"|"usage"), payload (dict), observed_at
class AgentResult:        claimed_status, summary, files_changed, tests_claimed, blocking_questions, artifacts, raw_ref, usage
class AgentAdapter(Protocol):
    name: str
    def capabilities(self) -> AgentCapabilities
    def check_auth(self) -> AuthStatus                     # read-only probe; no network beyond the provider's own check
    def start(self, req: AgentRequest) -> RunHandle        # spawns the sub-process; returns pid + log paths
    def events(self, h: RunHandle) -> Iterator[AgentEvent] # JSONL stream or poll; bounded by caps
    def cancel(self, h: RunHandle, grace_seconds: float) -> None   # SIGTERM then SIGKILL the process *group*
    def collect(self, h: RunHandle) -> AgentResult         # parses structured output; raises InvalidOutput
    def resume(self, h: RunHandle) -> RunHandle | None     # only if capabilities.resume and the lease is still held
```
Rules: adapters never import the kernel's state machine; the orchestrator never parses provider-specific JSON. Each adapter ships a fixture corpus: success, timeout, auth error, invalid JSON, oversized output, prompt-injection text.

## 2. Claude Code and Codex integration (documented, not implemented in S2)

Verified locally on 2026-10-23 with `--help` only (no execution): **Claude Code 2.1.309**, **Codex CLI 0.162.1**. Re-verify flags at implementation time; both CLIs change often.

| Concern | Claude Code (`claude`) | Codex (`codex`) |
|---|---|---|
| Non-interactive | `-p/--print` | `exec` |
| Structured output | `--output-format json` / `stream-json` (JSONL), `--json-schema <schema>` | `exec --json` (JSONL), `--output-schema <file>`, `-o/--output-last-message <file>` |
| Scope | `--add-dir`, `--tools`, `--allowedTools` / `--disallowedTools`, `--permission-mode` (never `bypassPermissions`), `--strict-mcp-config`, `--settings <file>` | `-C/--cd <worktree>`, `--add-dir`, `-s/--sandbox <mode>` (read-only for planner/reviewer; workspace-write for implementer), `--ephemeral`, `--ignore-user-config`, `--ignore-rules` |
| Caps | `--max-budget-usd`, wall-clock enforced by Zenith (`--max-turns` to verify) | wall-clock enforced by Zenith; model via `-m` |
| Sessions | `--session-id <uuid>`, `--resume <id>` (resume only while Zenith's lease is valid) | `resume` is interactive-only → `capabilities.resume = False` |
| Auth probe | exit code of a trivial `-p` call is **not** safe (spends budget); use `claude --version` + settings presence; treat auth failure at `start` as `BLOCKED`, never fall back | `codex login status` |
| Forbidden | `--dangerously-skip-permissions` | `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`, `--approve-for-me` |

Terms: the personal CLI login is used only as the owner's own tool on the owner's machine (ADR-003). Programmatic/service use goes through API-key SDK adapters (Claude Agent SDK; Codex SDK / app-server) as a separate adapter implementation. Zenith never proxies or stores the subscription session.

## 3. Durable orchestration (schema v4)

New tables: `execution_runs(id, task_id, role, adapter, worktree_id, state, lease_owner, lease_expires_at, attempt, started_at, ended_at, exit_reason, usage_json)`, `worktrees(id, project_id, source_id, path, branch, run_id, created_at, removed_at)`, `run_events(seq, run_id, kind, payload_ref, occurred_at)` (payloads to capped, redacted JSONL files under `$XDG_STATE_HOME/zenith/runs/<run_id>/`), `approvals.used_at`, `approvals.used_by_run`.

Workflow (all transitions by Zenith code; `cli=False` paths only):
```
QUEUED ─lease+consume approval (one txn)→ RUNNING(planner) → RUNNING(implementer) → VERIFYING(verifier) → RUNNING(reviewer, advisory) → COMPLETED | BLOCKED | FAILED
RUNNING/VERIFYING ─lease lost / process gone─→ RECONCILE_REQUIRED (owner decides; never auto re-run)
```
- **Idempotency:** `execution_runs.id = sha256(task_id, scope_hash, attempt)`; a duplicate enqueue is a no-op event.
- **Leases:** `lease_owner = node_id:pid`, heartbeat every 30 s, TTL 120 s; one active lease per `(worktree)` and per `(project source, branch)`.
- **Cancellation:** `zenith run cancel` sets `cancel_requested`; the worker kills the process group; state → `BLOCKED` with reason.
- **Timeouts/budget:** wall clock and `max_budget_usd` from the envelope; exceeding either → `FAILED(reason=timeout|budget)`.
- **Crash recovery:** on worker start, expired leases → `RECONCILE_REQUIRED`; the worktree is kept for inspection. A started side effect is never re-executed automatically.
- **Retries:** `max_attempts` from the envelope (default 2) and only from `BLOCKED` via explicit owner `retry` (existing `BLOCKED → QUEUED` edge, requiring a fresh approval if the scope hash changed).
- **Events:** `run.started`, `run.heartbeat`, `run.output_truncated`, `run.cancelled`, `run.ended`, `approval.consumed`, `worktree.created/removed`, `verification.recorded`.

## 4. Execution security

- **Worktree:** `git worktree add <state>/worktrees/<run_id> -b zenith/<task_id>/<attempt> <head>` from the registered source; `git` runs with `core.fsmonitor=false`, `GIT_TERMINAL_PROMPT=0`; no hooks (`core.hooksPath=/dev/null`); the primary working tree is never written. Post-run: `git diff --name-only <head>` must be a subset of `allowed_files` globs, else `BLOCKED`.
- **Process:** `subprocess.Popen(argv, cwd=worktree, env=sanitized, start_new_session=True, stdin=DEVNULL)`; env allowlist `PATH, HOME(=run scratch), LANG, TERM, XDG_*` + adapter-declared credential variable names only; output capped (bytes/lines) and redacted (`policy.redact`) before storage; `resource.setrlimit` for CPU/file size/processes.
- **Sandbox (separate from worktree):** profile detection at startup — `bwrap` (preferred: ro root, rw worktree + scratch, `--unshare-net` for verifier runs) or `systemd-run --user --property=…`; if none available, execution of *repository* commands (tests/build) is refused unless the owner sets `ZENITH_UNSANDBOXED_TESTS=1` per project, recorded as an event. Model CLIs keep their own sandbox flags **in addition**.
- **Approval gate:** `QUEUED→RUNNING` consumes a scope-bound approval (`used_at`) atomically with the lease; `L3`+ actions (commit to primary tree, push) are not available to Sprint 2 adapters at all.
- **Prompt injection:** repository text goes to models as quoted data inside the envelope; the authorization block is never inside model-editable content; negative tests feed `ignore previous instructions` corpora and assert no state/permission change.

## 5. Independent verification (`zenith/verify.py`)

Deterministic, model-free, produces `TEST_VERIFICATION` evidence via the existing verifier-only `record_test_verification`:
1. worktree HEAD == `base_head` + committed-or-staged changes only inside `allowed_files`;
2. each acceptance `command` from the envelope runs (sandboxed) with exit code captured; `expected: exit-code-0` compared by code, never by parsing "passed" text;
3. required artifacts exist and are hashed;
4. source revision recorded; evidence freshness: a verification older than the latest observation of the same source is `VERIFICATION_STALE` (already in the brief);
5. reviewer output is stored as `MODEL_INFERENCE` evidence — advisory only.
Result → `COMPLETED` only if every acceptance item passed; else `BLOCKED` with the failing item.

## 6. Safe proof of concept (disposable repository)

`tests/test_poc_fake_agents.py` + `zenith demo poc --repo <tmp>`:
1. temp Git repo with a failing test; `zenith project add`, `task add --risk L2` with envelope (allowed_files `src/*.py tests/*.py`, acceptance `python -m unittest`).
2. `FakePlanner` returns a fixture plan → `PLANNED`; scope hash computed.
3. `zenith task approve` (owner) → `AWAITING_APPROVAL → QUEUED`.
4. worker leases, consumes approval, creates worktree; `FakeImplementer` applies a fixture patch inside the worktree only.
5. verifier runs the real unit test in the worktree; records `TEST_VERIFICATION`.
6. `FakeReviewer` returns feedback (stored as `MODEL_INFERENCE`).
7. `COMPLETED`; `zenith brief` shows `VERIFIED` citing the verification; primary tree untouched; no push.
Negative variants: implementer touches a file outside scope → `BLOCKED`; test fails but implementer claims success → `BLOCKED`; timeout → `FAILED`; worker killed mid-run → `RECONCILE_REQUIRED`; duplicate enqueue → no-op; injection text in README → no permission change.

## 7. Ordered engineering tasks

| ID | Task | Depends on | Gate |
|---|---|---|---|
| S2-01 | Schema v4: `execution_runs`, `worktrees`, `run_events`, `approvals.used_at/used_by_run`; `RECONCILE_REQUIRED` state; migration + rollback tests | — | v3→v4 tests green; v1→v4 rehearsal on temp DB |
| S2-02 | Adapter interface + `FakeAgentAdapter` with fixture corpus (success/timeout/auth/invalid/oversized/injection) | — | contract tests for every fixture |
| S2-03 | Worktree manager (create/lease/diff-scope/remove; never primary tree) | S2-01 | two writers cannot lease one repo; out-of-scope diff detected |
| S2-04 | Executor: argv runner, env sanitization, caps, redaction, rlimits, sandbox profile detection, refusal without sandbox | — | kill/timeout/oversize tests; no shell; env leak test |
| S2-05 | Verifier | S2-03, S2-04 | false "tests passed" claim cannot complete a task |
| S2-06 | Orchestrator/worker: lease, approval consumption, state transitions, heartbeat, cancel, crash recovery, retries, budget | S2-01…S2-05 | all §6 negative variants |
| S2-07 | CLI: `zenith run start/status/cancel/retry`, `zenith worker --once`, `zenith runs` | S2-06 | CLI from empty DB |
| S2-08 | POC demo + docs (README status table, runbook, ADR-005 acceptance) | S2-07 | §6 end-to-end green on disposable repo |
| S2-09 *(future sprint, gated)* | Claude Code / Codex CLI adapters behind explicit owner config, `check_auth`, fixture-replayed tests only in CI | S2-08 accepted by owner | no live call in tests; owner authorizes first live run |

Owner decisions needed before S2-06: (a) approve `RECONCILE_REQUIRED` as a new task state (ADR-005); (b) choose the sandbox baseline for this machine (`bwrap` availability); (c) confirm that no adapter may ever receive `L3`/`L4` capabilities in Sprint 2.
