"""Numbered schema migrations with pre-migration backup and per-migration rollback."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


class MigrationError(Exception):
    """Migration could not be applied; the database is left at its last good version."""


def _m001_baseline(con: sqlite3.Connection) -> None:
    """Sprint-0 schema, verbatim, so legacy databases and fresh ones converge."""
    for stmt in (
        "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        """CREATE TABLE IF NOT EXISTS projects (
             id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
             category TEXT NOT NULL DEFAULT 'personal', state TEXT NOT NULL DEFAULT 'active',
             created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS tasks (
             id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
             title TEXT NOT NULL, objective TEXT NOT NULL DEFAULT '', risk TEXT NOT NULL,
             state TEXT NOT NULL, created_at TEXT NOT NULL,
             due_at TEXT, version INTEGER NOT NULL DEFAULT 1)""",
        """CREATE TABLE IF NOT EXISTS approvals (
             id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
             scope_hash TEXT NOT NULL, actor TEXT NOT NULL,
             expires_at TEXT NOT NULL, created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS events (
             seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
             aggregate_type TEXT NOT NULL, aggregate_id TEXT NOT NULL,
             event_type TEXT NOT NULL, actor TEXT NOT NULL,
             payload TEXT NOT NULL, occurred_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS snapshots (
             id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
             collected_at TEXT NOT NULL, source_type TEXT NOT NULL, details TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_events_aggregate ON events(aggregate_type,aggregate_id)",
    ):
        con.execute(stmt)


_ORIGINS = "('HUMAN_REPORT','REPOSITORY_OBSERVATION','TEST_VERIFICATION','DOCUMENT','MODEL_INFERENCE')"
_EVIDENCE_COLUMNS = ("id", "project_id", "source_id", "task_id", "origin", "confidence", "summary",
                     "source_ref", "digest", "details", "node_id", "observed_at", "recorded_at")


def _m002_verified_project_memory(con: sqlite3.Connection) -> None:
    """Sprint 1: multi-source projects, evidence ledger, task dependencies, snapshot dedup keys."""
    con.execute("""CREATE TABLE project_sources (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        kind TEXT NOT NULL DEFAULT 'local_path' CHECK (kind IN ('local_path')),
        path TEXT NOT NULL, label TEXT, node_id TEXT NOT NULL DEFAULT 'local',
        added_at TEXT NOT NULL, last_seen_at TEXT, removed_at TEXT)""")
    con.execute("CREATE UNIQUE INDEX idx_sources_active_path ON project_sources(node_id, path) "
                "WHERE removed_at IS NULL")
    con.execute("CREATE INDEX idx_sources_project ON project_sources(project_id)")

    # Each legacy project path becomes that project's first source; project ids are unchanged.
    for pid, path, created in con.execute("SELECT id, path, created_at FROM projects").fetchall():
        con.execute("INSERT INTO project_sources(id,project_id,path,added_at) VALUES (?,?,?,?)",
                    (str(uuid.uuid4()), pid, path, created))

    con.execute("ALTER TABLE snapshots ADD COLUMN source_id TEXT REFERENCES project_sources(id)")
    con.execute("ALTER TABLE snapshots ADD COLUMN content_signature TEXT")
    con.execute("ALTER TABLE snapshots ADD COLUMN last_seen_at TEXT")
    con.execute("ALTER TABLE snapshots ADD COLUMN seen_count INTEGER NOT NULL DEFAULT 1")
    con.execute("UPDATE snapshots SET last_seen_at=collected_at, source_id=("
                "SELECT s.id FROM project_sources s WHERE s.project_id=snapshots.project_id "
                "ORDER BY s.added_at, s.rowid LIMIT 1)")
    con.execute("CREATE INDEX idx_snapshots_source ON snapshots(source_id, collected_at)")

    # Standard SQLite table rebuild (foreign keys are OFF while migrations run): drop projects.path.
    con.execute("""CREATE TABLE projects_new (
        id TEXT PRIMARY KEY, name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'personal', state TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL)""")
    con.execute("INSERT INTO projects_new(id,name,category,state,created_at) "
                "SELECT id,name,category,state,created_at FROM projects")
    con.execute("DROP TABLE projects")
    con.execute("ALTER TABLE projects_new RENAME TO projects")

    con.execute(f"""CREATE TABLE evidence (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        source_id TEXT REFERENCES project_sources(id),
        task_id TEXT REFERENCES tasks(id),
        origin TEXT NOT NULL CHECK (origin IN {_ORIGINS}),
        confidence TEXT NOT NULL CHECK (confidence IN ('VERIFIED','OBSERVED','REPORTED','INFERRED','UNKNOWN')),
        summary TEXT NOT NULL, source_ref TEXT, digest TEXT,
        details TEXT NOT NULL DEFAULT '{{}}', node_id TEXT NOT NULL DEFAULT 'local',
        observed_at TEXT NOT NULL, last_confirmed_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
        CHECK ((origin='TEST_VERIFICATION' AND confidence IN ('VERIFIED','UNKNOWN'))
            OR (origin='REPOSITORY_OBSERVATION' AND confidence IN ('OBSERVED','UNKNOWN'))
            OR (origin='DOCUMENT' AND confidence IN ('OBSERVED','REPORTED','UNKNOWN'))
            OR (origin='HUMAN_REPORT' AND confidence IN ('REPORTED','UNKNOWN'))
            OR (origin='MODEL_INFERENCE' AND confidence IN ('INFERRED','UNKNOWN'))))""")
    con.execute("CREATE INDEX idx_evidence_project ON evidence(project_id, observed_at)")
    con.execute("CREATE INDEX idx_evidence_source ON evidence(source_id, origin, observed_at)")
    con.execute("CREATE TRIGGER evidence_no_delete BEFORE DELETE ON evidence "
                "BEGIN SELECT RAISE(ABORT,'evidence is append-only'); END")
    changed = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in _EVIDENCE_COLUMNS)
    con.execute(f"CREATE TRIGGER evidence_only_reconfirm BEFORE UPDATE ON evidence WHEN {changed} "
                "BEGIN SELECT RAISE(ABORT,'evidence is append-only'); END")

    con.execute("""CREATE TABLE task_dependencies (
        task_id TEXT NOT NULL REFERENCES tasks(id), depends_on TEXT NOT NULL REFERENCES tasks(id),
        created_at TEXT NOT NULL, PRIMARY KEY (task_id, depends_on), CHECK (task_id <> depends_on))""")
    con.execute("CREATE INDEX idx_tasks_due ON tasks(due_at)")


def _m003_integrity_and_ordering(con: sqlite3.Connection) -> None:
    """Sprint 1 hardening: immutable events, gap-free evidence order, no nested sources."""
    # Events are the audit log: ordinary application code must not be able to rewrite them.
    con.execute("CREATE TRIGGER events_no_update BEFORE UPDATE ON events "
                "BEGIN SELECT RAISE(ABORT,'events are append-only'); END")
    con.execute("CREATE TRIGGER events_no_delete BEFORE DELETE ON events "
                "BEGIN SELECT RAISE(ABORT,'events are append-only'); END")

    # Stable, gap-free recording order for evidence (rowid is not stable across VACUUM).
    con.execute("DROP TRIGGER IF EXISTS evidence_only_reconfirm")
    con.execute("ALTER TABLE evidence ADD COLUMN seq INTEGER")
    con.execute("UPDATE evidence SET seq=rowid")
    con.execute("CREATE UNIQUE INDEX idx_evidence_seq ON evidence(seq)")
    changed = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in (*_EVIDENCE_COLUMNS, "seq"))
    con.execute(f"CREATE TRIGGER evidence_only_reconfirm BEFORE UPDATE ON evidence WHEN {changed} "
                "BEGIN SELECT RAISE(ABORT,'evidence is append-only'); END")
    con.execute("CREATE TRIGGER evidence_seq_required BEFORE INSERT ON evidence WHEN NEW.seq IS NULL "
                "BEGIN SELECT RAISE(ABORT,'evidence.seq must be assigned'); END")

    # A directory belongs to at most one active source and sources never nest (fail closed).
    overlap = ("EXISTS (SELECT 1 FROM project_sources s WHERE s.removed_at IS NULL AND s.id<>NEW.id "
               "AND s.node_id=NEW.node_id AND (s.path=NEW.path "
               "OR substr(NEW.path,1,length(s.path)+1)=s.path||'/' "
               "OR substr(s.path,1,length(NEW.path)+1)=NEW.path||'/'))")
    for when, name in (("INSERT", "sources_no_overlap_insert"), ("UPDATE OF path", "sources_no_overlap_update")):
        con.execute(f"CREATE TRIGGER {name} BEFORE {when} ON project_sources WHEN NEW.removed_at IS NULL AND {overlap} "
                    "BEGIN SELECT RAISE(ABORT,'source path overlaps an existing source'); END")


def _append_only(con: sqlite3.Connection, table: str, allow_update_cols: tuple[str, ...] = ()) -> None:
    con.execute(f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
                f"BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END")
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})") if r[1] not in allow_update_cols]
    changed = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in cols)
    con.execute(f"CREATE TRIGGER {table}_no_rewrite BEFORE UPDATE ON {table} WHEN {changed} "
                f"BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END")


def _m004_durable_orchestration(con: sqlite3.Connection) -> None:
    """Sprint 2: approval consumption, execution runs, leases, worktrees, artifacts, decisions."""
    # Approvals become operation-scoped, bounded-use, revocable and append-only.
    for ddl in ("operation TEXT NOT NULL DEFAULT 'task.execute'", "project_id TEXT",
                "max_uses INTEGER NOT NULL DEFAULT 1", "use_count INTEGER NOT NULL DEFAULT 0",
                "used_at TEXT", "used_by TEXT", "revoked_at TEXT", "revoke_reason TEXT"):
        con.execute(f"ALTER TABLE approvals ADD COLUMN {ddl}")
    con.execute("UPDATE approvals SET project_id=(SELECT project_id FROM tasks WHERE tasks.id=approvals.task_id)")
    _append_only(con, "approvals", ("use_count", "used_at", "used_by", "revoked_at", "revoke_reason"))
    con.execute("CREATE TRIGGER approvals_bounded_use BEFORE UPDATE OF use_count ON approvals "
                "WHEN NEW.use_count > NEW.max_uses OR NEW.use_count < OLD.use_count "
                "BEGIN SELECT RAISE(ABORT,'approval use exceeds max_uses'); END")

    # Owner-defined engineering scope travels with the task and is part of its scope hash.
    con.execute("ALTER TABLE tasks ADD COLUMN scope TEXT NOT NULL DEFAULT '{}'")

    con.execute("""CREATE TABLE execution_runs (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
        project_id TEXT NOT NULL REFERENCES projects(id), source_id TEXT REFERENCES project_sources(id),
        idempotency_key TEXT NOT NULL UNIQUE, attempt INTEGER NOT NULL,
        state TEXT NOT NULL, stage TEXT, director TEXT NOT NULL, implementer TEXT NOT NULL,
        lease_owner TEXT, lease_expires_at TEXT, cancel_requested_at TEXT,
        worktree_id TEXT, base_head TEXT, context_artifact_id TEXT, prompt_artifact_id TEXT,
        budget TEXT NOT NULL DEFAULT '{}', usage TEXT NOT NULL DEFAULT '{}',
        exit_reason TEXT, created_at TEXT NOT NULL, started_at TEXT, ended_at TEXT)""")
    con.execute("CREATE INDEX idx_runs_task ON execution_runs(task_id, attempt)")
    con.execute("CREATE INDEX idx_runs_state ON execution_runs(state)")

    con.execute("""CREATE TABLE worktrees (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        source_id TEXT NOT NULL REFERENCES project_sources(id), run_id TEXT NOT NULL REFERENCES execution_runs(id),
        path TEXT NOT NULL UNIQUE, branch TEXT NOT NULL, base_head TEXT NOT NULL,
        created_at TEXT NOT NULL, removed_at TEXT)""")
    # Single writer per source: at most one live worktree per registered directory.
    con.execute("CREATE UNIQUE INDEX idx_worktrees_single_writer ON worktrees(source_id) WHERE removed_at IS NULL")

    con.execute("""CREATE TABLE run_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES execution_runs(id),
        stage TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', occurred_at TEXT NOT NULL)""")
    con.execute("CREATE INDEX idx_run_events_run ON run_events(run_id, seq)")
    _append_only(con, "run_events")

    con.execute("""CREATE TABLE artifacts (
        id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, kind TEXT NOT NULL, schema_version TEXT NOT NULL,
        project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT REFERENCES tasks(id),
        run_id TEXT REFERENCES execution_runs(id), parent_id TEXT REFERENCES artifacts(id),
        produced_by TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL, created_at TEXT NOT NULL)""")
    con.execute("CREATE INDEX idx_artifacts_task ON artifacts(task_id, kind, seq)")
    con.execute("CREATE INDEX idx_artifacts_run ON artifacts(run_id, seq)")
    _append_only(con, "artifacts")

    con.execute("""CREATE TABLE decisions (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT REFERENCES tasks(id),
        run_id TEXT REFERENCES execution_runs(id), artifact_id TEXT REFERENCES artifacts(id),
        decision TEXT NOT NULL, rationale TEXT NOT NULL, made_by TEXT NOT NULL, created_at TEXT NOT NULL)""")
    _append_only(con, "decisions")


Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]
MIGRATIONS: list[Migration] = [
    (1, "sprint0_baseline", _m001_baseline),
    (2, "sprint1_verified_project_memory", _m002_verified_project_memory),
    (3, "sprint1_hardening_integrity_and_ordering", _m003_integrity_and_ordering),
    (4, "sprint2_durable_orchestration", _m004_durable_orchestration),
]
LATEST_VERSION = MIGRATIONS[-1][0]


def current_version(con: sqlite3.Connection) -> int:
    """0 for an empty database; otherwise the recorded schema version."""
    has_meta = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'").fetchone()
    if not has_meta:
        return 0
    row = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def backup_database(db_path: Path, dest: Path) -> Path:
    """Consistent copy via the SQLite online backup API. Refuses to overwrite `dest`."""
    if dest.exists():
        raise MigrationError(f"Refusing to overwrite existing file: {dest}")
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)
            if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise MigrationError("Backup failed its integrity check")
        except BaseException:
            out.close()
            dest.unlink(missing_ok=True)  # only the file this call was writing; never a prior backup
            raise
        finally:
            out.close()
    finally:
        src.close()
    dest.chmod(0o600)
    return dest


_BEFORE_COMMIT_HOOK: Callable[[int], None] | None = None  # tests only: simulate a crash before COMMIT


def apply_pending(db_path: Path, migrations: list[Migration] | None = None, backup: bool = True) -> dict:
    """Bring `db_path` to the latest version (creating it when absent).

    A database with prior data is backed up first. Every migration runs in its own transaction
    with foreign keys off (required for table rebuilds), is checked with `foreign_key_check`,
    and is rolled back as a unit on any error, leaving the previous version and the backup.
    """
    db_path = Path(db_path)
    migrations = MIGRATIONS if migrations is None else migrations
    latest = migrations[-1][0]
    con = sqlite3.connect(str(db_path), timeout=5, isolation_level=None)
    backup_path: Path | None = None
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("PRAGMA journal_mode=WAL")
        before = current_version(con)
        if before > latest:
            raise MigrationError(f"Database schema v{before} is newer than this Zenith (v{latest}); "
                                 "refusing to downgrade")
        pending = [m for m in migrations if m[0] > before]
        if not pending:
            return {"from": before, "to": before, "backup": None}
        if before > 0 and backup:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = db_path.with_name(f"{db_path.name}.pre-v{pending[-1][0]}-{stamp}.bak")
            n = 0
            while backup_path.exists():
                n += 1
                backup_path = db_path.with_name(f"{db_path.name}.pre-v{pending[-1][0]}-{stamp}-{n}.bak")
            backup_database(db_path, backup_path)
        for version, name, fn in pending:
            con.execute("BEGIN IMMEDIATE")
            try:
                fn(con)
                con.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
                            (str(version),))
                if con.execute("PRAGMA foreign_key_check").fetchall():
                    raise MigrationError("foreign key violations after migration")
                if _BEFORE_COMMIT_HOOK is not None:
                    _BEFORE_COMMIT_HOOK(version)
                con.execute("COMMIT")
            except BaseException as exc:
                if con.in_transaction:
                    con.execute("ROLLBACK")
                hint = f" Pre-migration backup kept at {backup_path}." if backup_path else ""
                raise MigrationError(f"Migration {version} ({name}) failed and was rolled back: {exc}.{hint}") from exc
        return {"from": before, "to": latest, "backup": str(backup_path) if backup_path else None}
    finally:
        con.close()
