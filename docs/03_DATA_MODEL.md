# 03 — Data Model / Veri Modeli

## 1. Canonical entities
| Entity | Important fields | Meaning |
|---|---|---|
| `project` | id, name, category, status, priority, created_at | Logical project, not directory |
| `project_source` | id, project_id, kind, URI/path, node_id, last_seen | Many resources per project |
| `task` | id, project_id, title, objective, risk, state, due_at, max_attempts | Unit of work |
| `task_dependency` | task_id, depends_on | DAG task relation |
| `approval` | id, task_id, actor, decision, scope_hash, expires_at, used_at | Human authorization |
| `execution_run` | id, task_id, node_id, worktree, provider, stage, start/end, cost | Concrete attempt |
| `agent_session` | id, provider, provider_session_id, run_id, mode | Provider session metadata |
| `evidence` | id, subject_id, type, origin, uri, digest, observed_at, confidence | Verifiable external artifact |
| `decision` | id, project_id, rationale, options, made_by, effective_at | Human/AI-suggested reasoning |
| `event` | event_id, aggregate, actor, type, payload, occurred_at | Immutable chronology |
| `machine` | id, label, capabilities, last_heartbeat, clock_skew | Node record |
| `notification_outbox` | id, key, channel, payload_ref, status, retry_after | Idempotent delivery |
| `sync_cursor` | node_id, stream, last_seen_event | Multi-device synchronization |
| `research_report` | id, project_id, topic, query, sources, date, provenance | **Future backlog only** |

## 2. Provenance and confidence
Every progress claim has:
- `origin`: `HUMAN_REPORT`, `REPOSITORY_OBSERVATION`, `TEST_VERIFICATION`, `DOCUMENT`, `EMAIL`, `MODEL_INFERENCE`.
- `observed_at`: when the source was captured.
- `source_ref`: original file/Git commit/test path/mail thread ID.
- `confidence`: `VERIFIED`, `OBSERVED`, `REPORTED`, `INFERRED`, `UNKNOWN`.
- `scope` and `node_id` so stale remote-machine state is identifiable.

A summary cannot upgrade `MODEL_INFERENCE` to `TEST_VERIFICATION` without real evidence. No fake precision percentages.

## 3. Storage
- SQLite in WAL mode; foreign keys ON; `busy_timeout`; SQL migrations with schema version.
- First schema is deliberately small: `projects`, `tasks`, `approvals`, `events`, `snapshots`, `schema_meta`.
- Later tables are introduced via numbered migrations, not giant initial schema.
- **As implemented (schema v2):** v1 tables plus `project_sources` (soft-removable; `projects.path` was dropped and is now derived from the first active source), `evidence` (append-only; DB triggers allow only `last_confirmed_at` to change, and a CHECK ties confidence to origin), `task_dependencies`, and `snapshots` gained `source_id`, `content_signature`, `last_seen_at`, `seen_count`. **Schema v3 (hardening):** `events` gained no-update/no-delete triggers; `evidence.seq` (unique, gap-free, assigned in the write transaction, immutable); `project_sources` triggers refuse duplicate or nested active paths per node. Timestamps are microsecond UTC. Not yet implemented: `decision`, `machine`, `notification_outbox`, `sync_cursor`, `execution_run`, `agent_session`, `EMAIL` origin (planned for v4, see `plans/SPRINT_2_PLAN.md`).
- Rich context stored as normalized facts and external artifact references. Huge transcripts and secrets are not copied into SQLite.
- Optional embeddings and FTS5 only after retrieval quality is measured. Early-stage keyword/metadata retrieval is preferable to premature vector infrastructure.

## 4. Example event envelope
```json
{
  "event_id": "uuid",
  "aggregate_type": "task",
  "aggregate_id": "task-uuid",
  "event_type": "task.state_changed",
  "actor": "user|zenith|agent:claude|node:arch",
  "occurred_at": "2026-10-23T11:00:00+00:00",
  "correlation_id": "task-uuid",
  "payload": {"from": "DRAFT", "to": "CONTEXT_READY"}
}
```

## 5. Retention + deletion design
- Default project metadata and consent/audit events long-lived.
- Raw email bodies never retained by default; store minimal metadata, reference and extracted claims with source links.
- Agent tool streams have user-defined retention, redaction and maximum size.
- Backup is encrypted when leaving the machine; rotation and restore tests mandatory before Hub.
- Project removal is explicit and separate from source file deletion; deleting a Zenith registry entry never removes the user's directory.

## 6. Consistency rules
- Project IDs stable across path moves and device changes.
- Work must not be assigned to an offline node without deferral.
- Each external action must reference a currently valid scope-bound approval and idempotency key.
- Use transaction boundaries for approval consumption + work lease, and for recording terminal run status.
