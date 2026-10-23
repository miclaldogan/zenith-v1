# 10 — API and Runtime Contracts (Design, not yet implemented)

> All endpoints, IPC messages and lease logic below are **planned contracts**. Sprint-0 only ships a local CLI, registry and SQLite.

## 1. Interface boundaries
- CLI/TUI → local application service methods now; local Unix domain socket API later. No public HTTP listener default.
- MCP → adapters to same service methods, with separate read/write scopes.
- Zenith Hub (future) → HTTPS/mTLS or an authenticated private overlay; explicit device enrollment.
- Agent runner → typed `AgentAdapter` protocol; no terminal text scraping.

## 2. Future local API (version prefix `/v1`)
| Method | Route | Risk and effect |
|---|---|---|
| GET | `/projects` | L0, paginated read |
| POST | `/projects` | L0 local registry write, approved root only |
| GET | `/projects/{id}/context` | L0/L1 based on confidentiality |
| GET | `/tasks?state=` | L0 |
| POST | `/tasks` | creates DRAFT, no execution |
| POST | `/tasks/{id}/plan` | L1 model consent/budget |
| POST | `/tasks/{id}/approval-requests` | propose authorization challenge |
| POST | `/approvals/{id}/grant` | human principal only; atomic and expiring |
| POST | `/tasks/{id}/enqueue` | policy gate / idempotency / worker lease |
| POST | `/tasks/{id}/cancel` | cooperative cancel then bounded kill on owned process |
| GET | `/runs/{id}/events` | event stream (redacted) |
| GET | `/nodes` | last-seen timestamp, capabilities, staleness |

## 3. Event delivery and sequencing
- Database auto-increment `seq` in the starter; future unique global event IDs + per-device monotonic source sequence.
- Every event includes `aggregate_id`, `actor`, `occurred_at`, `correlation_id`, data classification.
- Outbox send is at-least-once with idempotency keys; do not promise exactly-once external side effects.
- Event consumers must dedupe by stable event ID and replay cursor.

## 4. Worker lease design
1. Scheduler selects QUEUED task with valid, still-current approval and allowed node.
2. Worker acquires lease transactionally; `run_id`, node_id, lease expiration, idempotency key persisted BEFORE invoking provider.
3. Worker starts an isolated worktree and limited subprocess.
4. Heartbeat extends lease while it owns child process.
5. On process loss, task becomes `RECONCILE_REQUIRED`; inspect provider session/worktree before requeue. No automatic second writer.
6. Only independent verification moves VERIFYING → COMPLETED.

## 5. Error taxonomy
`AUTH_REQUIRED`, `PROVIDER_UNAVAILABLE`, `MODEL_BUDGET_EXCEEDED`, `NODE_OFFLINE`, `WORKTREE_CONFLICT`, `APPROVAL_EXPIRED`, `POLICY_DENIED`, `OUTPUT_INVALID`, `VERIFICATION_FAILED`, `RUN_TIMEOUT`, `UNTRUSTED_CONTENT`, `RECONCILE_REQUIRED`.

Errors must be machine-readable and must not be rewritten as success by a summarizing LLM.

## 6. Session handling
- Provider session ID ≠ Zenith task/run ID. Multiple provider sessions per task possible.
- Resume only if session is valid and same project/revision/permissions. Otherwise re-plan/reauthorize.
- Console display (tmux/Hyprland) attaches to output but doesn't control security state or own leases.
- Persist tool exit codes, bounded log references and test artifact digests independently from prose.

## 7. Migrations and compatibility
- API version prefix `v1`, ACP schema `zenith.acp.v1`, SQLite migrations `0001`, `0002`, etc.
- One migrator owns schema changes. Upgrade from oldest supported version with backup/restore and rollback testing.
- A provider adapter reports supported capabilities; fail closed on unknown flag or output schema.
