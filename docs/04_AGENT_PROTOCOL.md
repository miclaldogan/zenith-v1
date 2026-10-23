# 04 — Agent Collaboration Protocol (ACP)

This is **Zenith's internal task contract**, not a claim of standard protocol compatibility. MCP is an exposure surface for tools; ACP governs durable tasks, approvals and evidence.

## 1. Roles
The roles below are the engineering instance of Zenith's general **specialist role** concept (REQ-009): a role is a logical capability, filled per task by an adapter chosen through the extension registry and model routing, speaking only versioned contracts. Research roles (Research Lead, Literature Discovery, Paper Analysis, Methodology Judge, Evidence Judge, Synthesis) and the Creative Reasoning Agent will use the same mechanism with their own contract family (`zenith.research.v1`, future).
- **Zenith Orchestrator:** owns task state, authorized context, role assignment, budget, deadlines, worktree lease.
- **Architect/Planner** (Codex default, replaceable): produces bounded plan with measurable acceptance criteria; no write permissions by default.
- **Implementer** (Claude Code default, replaceable): modifies an isolated authorized worktree, runs approved tests, produces structured report.
- **Reviewer** (Codex or another model, replaceable): examines diffs and failed evidence; advisory, not final proof.
- **Verifier** (deterministic Zenith worker): checks artifact hashes, diff scope, tests, process exit codes. Independent of a model.

## 2. Task lifecycle
```mermaid
stateDiagram-v2
  [*] --> DRAFT
  DRAFT --> CONTEXT_READY
  CONTEXT_READY --> PLANNED
  PLANNED --> AWAITING_APPROVAL
  PLANNED --> QUEUED: read-only and pre-authorized
  AWAITING_APPROVAL --> QUEUED: scoped grant
  QUEUED --> RUNNING: worker lease
  RUNNING --> VERIFYING: result submitted
  VERIFYING --> COMPLETED: proof meets acceptance
  VERIFYING --> BLOCKED: mismatch/needs owner
  RUNNING --> BLOCKED: unsafe or uncertain
  RUNNING --> FAILED: terminal failure
  DRAFT --> CANCELLED
  PLANNED --> CANCELLED
  QUEUED --> CANCELLED
  BLOCKED --> QUEUED: explicit retry approval
```

**Guard rule:** state transitions are made by Zenith code, not by any LLM text. A worktree write always needs policy allowance and valid approval scope; even when the task state is `QUEUED`.

### 2b. Implemented state machine additions (Sprint 2)
- `RUNNING`/`VERIFYING` → `RECONCILE_REQUIRED` when a worker crashes or a lease expires; `RECONCILE_REQUIRED` → `QUEUED | BLOCKED | FAILED | CANCELLED` only by the owner (`zenith run reconcile`), and `→ QUEUED` needs a fresh approval.
- `BLOCKED → COMPLETED` only through `zenith run accept` when the run holds a **passing** verification (reviewer disagreement override).
- Any `→ COMPLETED` requires a passing `TEST_VERIFICATION` evidence id; the kernel checks the row, not a claim.
- A new or revised implementation prompt bumps `tasks.version`, changing the scope hash and invalidating prior approvals.

Execution runs have their own states: `PENDING → RUNNING → VERIFYING → REVIEWING → COMPLETED | BLOCKED | FAILED | CANCELLED | RECONCILE_REQUIRED`, with stages `start, implement, verify, review, finalize`, a lease (`owner`, `expires_at`, heartbeat) and an idempotency key `sha256(task, scope_hash, attempt)`.

## 3. Machine-readable task envelope (illustrative)
```json
{
  "protocol_version": "zenith.acp.v1",
  "task_id": "ZEN-0042",
  "project_id": "stabilimentum",
  "idempotency_key": "sha256-scoped-hash",
  "objective": "Review and improve the existing module architecture",
  "task_type": "engineering",
  "input_refs": ["git:HEAD", "doc:architecture-v1"],
  "constraints": ["No network", "No new features", "Keep tests green"],
  "acceptance": [{"kind": "command", "name": "unit-tests", "expected": "exit-code-0"}],
  "execution": {
    "planner": "codex",
    "implementer": "claude-code",
    "reviewer": "codex",
    "max_review_cycles": 2,
    "max_attempts": 3,
    "max_wall_seconds": 1800,
    "max_budget_usd": 5.0
  },
  "authorization": {
    "risk": "L2",
    "allowed_roots": ["/approved/project/worktree"],
    "can_push": false,
    "can_deploy": false,
    "can_send_email": false
  }
}
```

Do not let the model edit the authorization envelope. Cryptographically hash canonicalized scope fields before approval; if plan changes, reauthorize.

## 4. Response envelope (from any agent)
```json
{
  "protocol_version": "zenith.acp.v1",
  "task_id": "ZEN-0042",
  "role": "implementer",
  "claimed_status": "ready_for_verification",
  "summary": "Adjusted dependency boundaries and added tests",
  "files_changed": ["src/example.py", "tests/test_example.py"],
  "tests_claimed": [{"command_id": "unit-tests", "claimed_pass": true}],
  "blocking_questions": [],
  "artifacts": ["artifact://runs/ZEN-0042/report.json"]
}
```

`claimed_status` is *not* a trusted `task.state`. Verification independently runs the checks and reads output.

## 5. Engineering Director protocol (implemented, `zenith.director.v1`)
All messages are strict JSON objects validated by `zenith.contracts` (required fields, types, enums, **no unknown fields** — a model cannot add `authorization`). Persisted messages are immutable `artifacts` rows with a digest and a parent link.

| Contract | Producer | Persisted as | Notes |
|---|---|---|---|
| `context_package` | Zenith | artifact | task, sources, last 10 evidence rows with ids, brief status, previous prompt/review/verification ids, constraints |
| `architecture_proposal` | Director | artifact (+ `decisions` rows) | summary, decisions, risks, decomposition |
| `engineering_decision`, `task_decomposition` | Director | inside proposal | |
| `implementation_prompt` | Director (authored) + **Zenith (stamped `zenith` block)** | artifact; revisions link to `parent_prompt_id` | Zenith sets `allowed_scope`, `exclusions`, `acceptance_commands`, `authorization`, `revision`, `context_ref`; director-proposed scope outside the owner's is recorded as `scope_narrowed` |
| `implementation_report` | Implementer | artifact | claims only; compared against reality by the verifier |
| `verification_report` | Zenith verifier | artifact + `TEST_VERIFICATION` evidence per command | `passed` is computed from exit codes, scope, revision, artifacts |
| `review_findings` + `next_step_proposal` (`complete`, `revise_prompt`, `escalate`) | Director | artifact (+ decision) | advisory; a `revised_prompt` becomes the next prompt revision |
| `approval_request` | Zenith | (future: outbox) | |

Rules: the director receives the **verification report** before writing corrective instructions; prompts are never recycled blindly — every revision has a `revision_reason`, a parent and a fresh context reference.

## 5. Retry and non-repetition policy
- Max 3 attempts, 2 reviewer loops by default; strict budget and wall-clock deadline.
- Retry only on diagnostic evidence and changed repair plan; repeated identical failures produce `BLOCKED`.
- Pause on approval timeout, credentials/auth errors, unexpected file changes or tool version mismatch.
- Same `idempotency_key` cannot schedule duplicate execution on the same scope.

## 6. CLI adapter implementation rules
- Use documented noninteractive modes. Claude: `claude -p --output-format json`; Codex: `codex exec --json` (JSONL events).
- Do not confuse `codex exec --json` JSONL with a single JSON document.
- Invoke commands with argument arrays, `shell=False`, explicit cwd, subprocess timeouts and controlled environment.
- Do not rely on subscription credentials for a third-party hosted API wrapper. Require supported authentication and settings.
- Session/stream adapters implement `start`, `poll/stream`, `cancel`, `collect_result`; `resume` only after checking current provider behavior.
- Store provider session IDs and use adapters to reconnect, never rely on tmux text scraping as ground truth.

## 7. Independent verification
- Validate worktree branch, allowed diff paths, `git diff --check`, expected output files, test commands with captured exit statuses.
- Reviewers provide risk findings. No successful verification if required deterministic tests failed.
- A report's summary must link to run ID, source revision and test artifact; stale proofs invalidate completion.
