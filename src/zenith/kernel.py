"""Durable project/task registry, evidence ledger and policy-gated manual workflow.

Zenith does not execute coding agents or change project files. Scans read metadata only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from . import dates, migrations, policy, scanner


class ZenithError(Exception):
    """User-facing validation or state error."""


RISK_LEVELS = ("L0", "L1", "L2", "L3", "L4")
TRANSITIONS = {
    "DRAFT": {"CONTEXT_READY", "CANCELLED"},
    "CONTEXT_READY": {"PLANNED", "CANCELLED"},
    "PLANNED": {"AWAITING_APPROVAL", "QUEUED", "CANCELLED"},
    "AWAITING_APPROVAL": {"QUEUED", "CANCELLED"},
    "QUEUED": {"RUNNING", "CANCELLED"},
    "RUNNING": {"VERIFYING", "BLOCKED", "FAILED", "RECONCILE_REQUIRED"},
    "VERIFYING": {"COMPLETED", "BLOCKED", "FAILED", "RECONCILE_REQUIRED"},
    "BLOCKED": {"QUEUED", "CANCELLED", "COMPLETED"},        # -> COMPLETED only with a passing verification
    "RECONCILE_REQUIRED": {"QUEUED", "BLOCKED", "FAILED", "CANCELLED"},  # owner decides (ADR-005)
    "COMPLETED": set(),
    "CANCELLED": set(),
    "FAILED": set(),
}
CLI_TRANSITIONS = {"CONTEXT_READY", "PLANNED", "AWAITING_APPROVAL", "QUEUED", "CANCELLED"}
# Operations an approval can name. Only task.execute is grantable now; the Git operations are the
# designed-but-unavailable REQ-002 permissions (see docs/11_REQUIREMENTS_REGISTRY.md).
APPROVAL_OPERATIONS = ("task.execute", "git.branch", "git.worktree", "git.stage", "git.commit",
                       "git.push", "git.pr.open", "git.pr.merge")
GRANTABLE_OPERATIONS = ("task.execute",)
TERMINAL_STATES = {s for s, nxt in TRANSITIONS.items() if not nxt}

EVIDENCE_ORIGINS = ("HUMAN_REPORT", "REPOSITORY_OBSERVATION", "TEST_VERIFICATION", "DOCUMENT", "MODEL_INFERENCE")
# Confidence is derived from origin by code; callers can never pick a higher one.
CONFIDENCE_BY_ORIGIN = {"HUMAN_REPORT": "REPORTED", "REPOSITORY_OBSERVATION": "OBSERVED",
                        "TEST_VERIFICATION": "VERIFIED", "DOCUMENT": "REPORTED", "MODEL_INFERENCE": "INFERRED"}
# Origins a person may attach by hand. Observation/verification come only from Zenith's own code.
MANUAL_EVIDENCE_ORIGINS = ("HUMAN_REPORT", "DOCUMENT", "MODEL_INFERENCE")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def utcnow() -> str:
    """Microsecond UTC. Lexicographic order of these strings equals chronological order,
    also against the second-resolution values written before schema v3."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def state_dir() -> Path:
    """Worktrees and run logs live here, never inside a registered project."""
    override = os.environ.get("ZENITH_STATE_DIR")
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))).expanduser()
    return base / "zenith"


def default_db_path() -> Path:
    override = os.environ.get("ZENITH_DB_PATH")
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))).expanduser()
    return base / "zenith" / "zenith.db"


def _uuid() -> str:
    return str(uuid.uuid4())


def _record_event(con: sqlite3.Connection, kind: str, aggregate_id: str, event_type: str,
                  payload: dict, actor: str = "owner") -> None:
    con.execute(
        "INSERT INTO events (id,aggregate_type,aggregate_id,event_type,actor,payload,occurred_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (_uuid(), kind, aggregate_id, event_type, actor, json.dumps(payload, ensure_ascii=False), utcnow()),
    )


def _load_json(text: str, what: str) -> dict:
    """Stored JSON must parse; a corrupt record is reported, never guessed."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ZenithError(f"Corrupt {what} record in database: {exc}") from exc
    if not isinstance(value, dict):
        raise ZenithError(f"Corrupt {what} record in database: not an object")
    return value


class Store:
    BUSY_TIMEOUT_MS = 5000  # how long a writer waits for another writer before failing

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path or default_db_path()).expanduser()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        if not self.db_path.exists():
            raise ZenithError("Database not initialized. Run `zenith init`.")
        # Autocommit mode: reads never hold a transaction; writes go through `write()`.
        con = sqlite3.connect(str(self.db_path), timeout=self.BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        try:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute(f"PRAGMA busy_timeout={int(self.BUSY_TIMEOUT_MS)}")
            version = migrations.current_version(con)
            if version != migrations.LATEST_VERSION:
                raise ZenithError(f"Database schema is v{version}, this Zenith needs "
                                  f"v{migrations.LATEST_VERSION}. Run `zenith init` to upgrade "
                                  "(a backup is written first).")
            yield con
        finally:
            con.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """One write transaction. BEGIN IMMEDIATE takes the write lock *before* the reads a
        check-then-write method performs, so two processes cannot both pass the same check."""
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")

    def init(self) -> str:
        """Create the database or upgrade it in place (backup first, rollback on failure)."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Prevent trivially exposing project metadata on a shared filesystem.
        if self.db_path.exists() and self.db_path.is_symlink():
            raise ZenithError("Refusing symlinked database path")
        try:
            outcome = migrations.apply_pending(self.db_path)
        except migrations.MigrationError as exc:
            raise ZenithError(str(exc)) from exc
        self.db_path.chmod(0o600)
        if outcome["from"] != outcome["to"]:
            with self.write() as con:
                _record_event(con, "system", "schema", "schema.migrated",
                              {"from": outcome["from"], "to": outcome["to"],
                               "backup": Path(outcome["backup"]).name if outcome["backup"] else None},
                              actor="zenith")
        return str(self.db_path)

    def schema_version(self) -> int | None:
        """Read-only probe used by `doctor`; None when the database does not exist."""
        if not self.db_path.exists():
            return None
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5)
        try:
            return migrations.current_version(con)
        finally:
            con.close()

    def backup(self, dest: Path | str) -> str:
        """Write a consistent copy with the SQLite backup API; never overwrites `dest`."""
        dest = Path(dest).expanduser()
        if not self.db_path.exists():
            raise ZenithError("Database not initialized. Run `zenith init`.")
        if not dest.parent.is_dir():
            raise ZenithError("Backup directory does not exist")
        try:
            migrations.backup_database(self.db_path, dest)
        except migrations.MigrationError as exc:
            raise ZenithError(str(exc)) from exc
        with self.write() as con:
            _record_event(con, "system", "database", "db.backup_created",
                          {"file": dest.name, "bytes": dest.stat().st_size}, actor="zenith")
        return str(dest)

    # ---- projects and sources -------------------------------------------------------------

    @staticmethod
    def _vet_path(path: str) -> Path:
        try:
            return policy.validate_source_path(path)
        except policy.PolicyError as exc:
            raise ZenithError(str(exc)) from exc

    @staticmethod
    def _claim_path(con: sqlite3.Connection, path: Path, node_id: str = "local",
                    ignore_source: str | None = None) -> None:
        """Fail closed: a directory may belong to one source, and sources may not nest.

        Nesting (parent/child) is refused across *and* within projects because two owners of
        the same files make ownership ambiguous and double-count observations. The same rule
        is enforced by a database trigger (schema v3), which also closes the race between
        two concurrent writers.
        """
        for row in con.execute("SELECT id, path, project_id FROM project_sources "
                               "WHERE node_id=? AND removed_at IS NULL", (node_id,)):
            if row["id"] == ignore_source:
                continue
            relation = policy.path_relation(str(path), row["path"])
            if relation == "same":
                raise ZenithError("That directory is already registered to a project")
            if relation:
                raise ZenithError(f"Path {relation} an existing source ({row['path']}); "
                                  "nested sources are refused to keep ownership unambiguous")

    def add_project(self, name: str, path: str, category: str = "personal") -> str:
        name = name.strip()
        if not name or len(name) > 200:
            raise ZenithError("Project name must be 1–200 characters")
        p = self._vet_path(path)
        project_id, source_id = _uuid(), _uuid()
        with self.write() as con:
            self._claim_path(con, p)
            now = utcnow()
            con.execute("INSERT INTO projects(id,name,category,created_at) VALUES (?,?,?,?)",
                        (project_id, name, category, now))
            self._insert_source(con, source_id, project_id, p, None, now)
            _record_event(con, "project", project_id, "project.created", {"name": name, "path": str(p)})
            _record_event(con, "project_source", source_id, "source.added",
                          {"project_id": project_id, "path": str(p)})
        return project_id

    @staticmethod
    def _insert_source(con: sqlite3.Connection, source_id: str, project_id: str, p: Path,
                       label: str | None, now: str) -> None:
        try:
            con.execute("INSERT INTO project_sources(id,project_id,path,label,added_at) VALUES (?,?,?,?,?)",
                        (source_id, project_id, str(p), label, now))
        except sqlite3.IntegrityError as exc:  # unique index / overlap trigger: second line of defence
            raise ZenithError(f"Path conflicts with an existing source: {exc}") from exc

    def _project_row(self, con: sqlite3.Connection, project_id: str) -> dict:
        r = con.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if r is None:
            raise ZenithError(f"Unknown project: {project_id}")
        return self._with_sources(con, dict(r))

    @staticmethod
    def _with_sources(con: sqlite3.Connection, project: dict) -> dict:
        rows = con.execute("SELECT * FROM project_sources WHERE project_id=? AND removed_at IS NULL "
                           "ORDER BY added_at, rowid", (project["id"],)).fetchall()
        project["sources"] = [dict(x) for x in rows]
        project["path"] = project["sources"][0]["path"] if rows else None  # primary source (Sprint-0 field)
        return project

    def list_projects(self) -> list[dict]:
        with self.connection() as con:
            rows = con.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE").fetchall()
            return [self._with_sources(con, dict(x)) for x in rows]

    def get_project(self, project_id: str) -> dict:
        with self.connection() as con:
            return self._project_row(con, project_id)

    PROJECT_SETTING_KEYS = ("docs", "cloud_consent")

    def project_settings(self, project_id: str) -> dict:
        with self.connection() as con:
            row = con.execute("SELECT settings FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise ZenithError(f"Unknown project: {project_id}")
        s = _load_json(row["settings"] or "{}", "project settings")
        return {"docs": list(s.get("docs", [])), "cloud_consent": bool(s.get("cloud_consent", False))}

    def set_project_settings(self, project_id: str, docs: list[str] | None = None,
                             cloud_consent: bool | None = None, actor: str = "owner") -> dict:
        """`docs`: repo-relative globs Misa may read locally (strict sensitive policy still applies).
        `cloud_consent`: whether this project's context may be sent to an external model. Independent."""
        current = self.project_settings(project_id)
        if docs is not None:
            for g in docs:
                if not isinstance(g, str) or not g or g.startswith("/") or ".." in g.split("/"):
                    raise ZenithError(f"Invalid doc pattern: {g!r}")
            current["docs"] = docs
        if cloud_consent is not None:
            current["cloud_consent"] = bool(cloud_consent)
        with self.write() as con:
            con.execute("UPDATE projects SET settings=? WHERE id=?", (json.dumps(current, sort_keys=True), project_id))
            _record_event(con, "project", project_id, "project.settings_changed", current, actor)
        return current

    def _source_row(self, con: sqlite3.Connection, source_id: str, active: bool = True) -> sqlite3.Row:
        r = con.execute("SELECT * FROM project_sources WHERE id=?", (source_id,)).fetchone()
        if r is None or (active and r["removed_at"] is not None):
            raise ZenithError(f"Unknown source: {source_id}")
        return r

    def add_source(self, project_id: str, path: str, label: str | None = None) -> str:
        label = label.strip() if label else None
        if label and len(label) > 100:
            raise ZenithError("Source label must be at most 100 characters")
        p = self._vet_path(path)
        source_id = _uuid()
        with self.write() as con:
            self._project_row(con, project_id)
            self._claim_path(con, p)
            self._insert_source(con, source_id, project_id, p, label, utcnow())
            _record_event(con, "project_source", source_id, "source.added",
                          {"project_id": project_id, "path": str(p), "label": label})
        return source_id

    def list_sources(self, project_id: str, include_removed: bool = False) -> list[dict]:
        with self.connection() as con:
            self._project_row(con, project_id)
            sql = "SELECT * FROM project_sources WHERE project_id=?"
            if not include_removed:
                sql += " AND removed_at IS NULL"
            return [dict(x) for x in con.execute(sql + " ORDER BY added_at, rowid", (project_id,))]

    def remove_source(self, source_id: str) -> str:
        """Detach a source from its project. History is kept; the directory is never touched."""
        with self.write() as con:
            src = self._source_row(con, source_id)
            con.execute("UPDATE project_sources SET removed_at=? WHERE id=?", (utcnow(), source_id))
            _record_event(con, "project_source", source_id, "source.removed",
                          {"project_id": src["project_id"], "path": src["path"]})
        return source_id

    def move_source(self, source_id: str, new_path: str) -> dict:
        """Point an existing source at a new directory; project and source IDs stay stable."""
        p = self._vet_path(new_path)
        with self.write() as con:
            src = self._source_row(con, source_id)
            if src["path"] == str(p):
                raise ZenithError("Source already points at that path")
            self._claim_path(con, p, src["node_id"], ignore_source=source_id)
            try:
                con.execute("UPDATE project_sources SET path=? WHERE id=?", (str(p), source_id))
            except sqlite3.IntegrityError as exc:
                raise ZenithError(f"Path conflicts with an existing source: {exc}") from exc
            _record_event(con, "project_source", source_id, "source.moved",
                          {"project_id": src["project_id"], "from": src["path"], "to": str(p)})
        return {"source_id": source_id, "project_id": src["project_id"], "path": str(p)}

    @staticmethod
    def _validate_scope(scope: dict | None) -> str:
        """Owner-defined engineering scope: allowed/excluded file globs and acceptance commands."""
        scope = dict(scope or {})
        unknown = set(scope) - {"allowed_files", "excluded_files", "acceptance", "artifacts", "context_files"}
        if unknown:
            raise ZenithError("Unknown scope keys: " + ", ".join(sorted(unknown)))
        for key in ("allowed_files", "excluded_files", "artifacts", "context_files"):
            vals = scope.get(key, [])
            if not isinstance(vals, list) or not all(isinstance(v, str) and v and ".." not in v.split("/")
                                                     and not v.startswith("/") for v in vals):
                raise ZenithError(f"scope.{key} must be a list of relative glob strings")
        for cmd in scope.get("acceptance", []):
            if (not isinstance(cmd, dict) or not isinstance(cmd.get("argv"), list) or not cmd["argv"]
                    or not all(isinstance(a, str) and a for a in cmd["argv"])
                    or not isinstance(cmd.get("expected_exit", 0), int)):
                raise ZenithError("scope.acceptance entries need a non-empty argv list (no shell) and int expected_exit")
        return json.dumps(scope, sort_keys=True)

    def add_task(self, project_id: str, title: str, objective: str = "", risk: str = "L0",
                 due_at: str | None = None, scope: dict | None = None) -> str:
        if risk not in RISK_LEVELS:
            raise ZenithError("Risk must be L0, L1, L2, L3 or L4")
        title = title.strip()
        if not title or len(title) > 200:
            raise ZenithError("Task title must be 1–200 characters")
        self.get_project(project_id)
        if due_at:
            try:
                # Stored as UTC; a bare date means end of that day in Europe/Istanbul.
                due_at = dates.iso_utc(dates.parse_due(due_at))
            except ValueError as exc:
                raise ZenithError("due_at must be ISO 8601 (e.g. 2026-11-01)") from exc
        scope_json = self._validate_scope(scope)
        task_id = _uuid()
        with self.write() as con:
            con.execute(
                "INSERT INTO tasks (id,project_id,title,objective,risk,state,created_at,due_at,scope) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (task_id, project_id, title, objective, risk, "DRAFT", utcnow(), due_at, scope_json),
            )
            _record_event(con, "task", task_id, "task.created", {"title": title, "risk": risk})
        return task_id

    def list_tasks(self, project_id: str | None = None, state: str | None = None,
                   open_only: bool = False, overdue: bool = False, due_within_days: int | None = None,
                   blocked: bool = False, now: datetime | None = None) -> list[dict]:
        """Read-only listing. Adds `due_local` (Europe/Istanbul), `overdue` and `blocked_by`."""
        if state is not None and state not in TRANSITIONS:
            raise ZenithError(f"Unknown state: {state}")
        if due_within_days is not None and not (0 <= due_within_days <= 3650):
            raise ZenithError("due-within must be 0–3650 days")
        now = now or datetime.now(timezone.utc)
        where, args = [], []
        if project_id:
            where.append("project_id=?")
            args.append(project_id)
        if state:
            where.append("state=?")
            args.append(state)
        sql = "SELECT * FROM tasks" + (" WHERE " + " AND ".join(where) if where else "")
        with self.connection() as con:
            rows = con.execute(sql + " ORDER BY created_at DESC, rowid DESC", args).fetchall()
            states = {r["id"]: r["state"] for r in con.execute("SELECT id,state FROM tasks")}
            deps: dict[str, list[str]] = {}
            for d in con.execute("SELECT task_id,depends_on FROM task_dependencies ORDER BY rowid"):
                deps.setdefault(d["task_id"], []).append(d["depends_on"])
        out = []
        for r in rows:
            t = dict(r)
            terminal = t["state"] in TERMINAL_STATES
            due = dates.safe_parse_utc(t["due_at"])
            t["due_local"] = dates.to_local(due) if due else None
            t["overdue"] = bool(due and due < now and not terminal)
            t["blocked_by"] = [] if terminal else [d for d in deps.get(t["id"], []) if states.get(d) != "COMPLETED"]
            if open_only and terminal:
                continue
            if overdue and not t["overdue"]:
                continue
            if due_within_days is not None and not (due and not terminal and due <= now + timedelta(days=due_within_days)):
                continue
            if blocked and not t["blocked_by"]:
                continue
            out.append(t)
        if overdue or due_within_days is not None:
            out.sort(key=lambda t: dates.safe_parse_utc(t["due_at"]))
        return out

    def add_dependency(self, task_id: str, depends_on: str) -> None:
        if task_id == depends_on:
            raise ZenithError("A task cannot depend on itself")
        with self.write() as con:
            for tid in (task_id, depends_on):
                if con.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone() is None:
                    raise ZenithError(f"Unknown task ID: {tid}")
            if con.execute("SELECT 1 FROM task_dependencies WHERE task_id=? AND depends_on=?",
                           (task_id, depends_on)).fetchone():
                raise ZenithError("Dependency already exists")
            # Reject cycles: depends_on must not already (transitively) depend on task_id.
            seen, stack = set(), [depends_on]
            while stack:
                cur = stack.pop()
                if cur == task_id:
                    raise ZenithError("Dependency would create a cycle")
                if cur in seen:
                    continue
                seen.add(cur)
                stack.extend(r[0] for r in con.execute(
                    "SELECT depends_on FROM task_dependencies WHERE task_id=?", (cur,)))
            con.execute("INSERT INTO task_dependencies(task_id,depends_on,created_at) VALUES (?,?,?)",
                        (task_id, depends_on, utcnow()))
            _record_event(con, "task", task_id, "task.dependency_added", {"depends_on": depends_on})

    def remove_dependency(self, task_id: str, depends_on: str) -> None:
        with self.write() as con:
            cur = con.execute("DELETE FROM task_dependencies WHERE task_id=? AND depends_on=?",
                              (task_id, depends_on))
            if cur.rowcount == 0:
                raise ZenithError("No such dependency")
            _record_event(con, "task", task_id, "task.dependency_removed", {"depends_on": depends_on})

    @staticmethod
    def _scope_hash(task: sqlite3.Row | dict) -> str:
        """Binds an approval to the exact task: any change (incl. a new prompt → version bump) invalidates it."""
        scope = {k: task[k] for k in ("id", "project_id", "title", "objective", "risk", "version", "scope")}
        return hashlib.sha256(json.dumps(scope, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def _valid_approval(con: sqlite3.Connection, task: sqlite3.Row, operation: str) -> sqlite3.Row | None:
        """Unexpired, unrevoked, unexhausted, scope- and operation-bound. None = deny."""
        return con.execute(
            "SELECT * FROM approvals WHERE task_id=? AND scope_hash=? AND operation=? AND expires_at>? "
            "AND revoked_at IS NULL AND use_count<max_uses ORDER BY created_at DESC LIMIT 1",
            (task["id"], Store._scope_hash(task), operation, utcnow())).fetchone()

    def consume_approval(self, con: sqlite3.Connection, task_id: str, operation: str, used_by: str,
                         actor: str = "zenith-worker") -> str:
        """Atomically spend one use of a valid approval inside the caller's write transaction."""
        task = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if task is None:
            raise ZenithError("Unknown task ID")
        approval = self._valid_approval(con, task, operation)
        if approval is None:
            raise ZenithError(f"No valid approval for {operation} on this task scope "
                              "(missing, expired, revoked, consumed, or the scope changed)")
        now = utcnow()
        cur = con.execute("UPDATE approvals SET use_count=use_count+1, used_at=?, used_by=? "
                          "WHERE id=? AND use_count<max_uses AND revoked_at IS NULL", (now, used_by, approval["id"]))
        if cur.rowcount != 1:
            raise ZenithError("Approval could not be consumed (concurrent use)")
        _record_event(con, "approval", approval["id"], "approval.consumed",
                      {"task_id": task_id, "operation": operation, "used_by": used_by,
                       "use_count": approval["use_count"] + 1, "max_uses": approval["max_uses"]}, actor)
        return approval["id"]

    def revoke_approval(self, approval_id: str, reason: str = "", actor: str = "owner") -> None:
        if actor != "owner":
            raise ZenithError("Only the owner may revoke approvals")
        with self.write() as con:
            row = con.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if row is None:
                raise ZenithError("Unknown approval ID")
            if row["revoked_at"]:
                raise ZenithError("Approval already revoked")
            con.execute("UPDATE approvals SET revoked_at=?, revoke_reason=? WHERE id=?",
                        (utcnow(), reason[:500], approval_id))
            _record_event(con, "approval", approval_id, "approval.revoked",
                          {"task_id": row["task_id"], "reason": reason[:500]}, actor)

    def list_approvals(self, task_id: str) -> list[dict]:
        with self.connection() as con:
            return [dict(r) for r in con.execute("SELECT * FROM approvals WHERE task_id=? ORDER BY created_at", (task_id,))]

    def bump_task_version(self, con: sqlite3.Connection, task_id: str, reason: str, actor: str) -> int:
        """Plan drift: a new prompt or scope invalidates every earlier approval (scope hash changes)."""
        cur = con.execute("UPDATE tasks SET version=version+1 WHERE id=?", (task_id,))
        if cur.rowcount != 1:
            raise ZenithError("Unknown task ID")
        version = con.execute("SELECT version FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
        _record_event(con, "task", task_id, "task.version_bumped", {"version": version, "reason": reason}, actor)
        return version

    def transition(self, task_id: str, target: str, actor: str = "owner", cli: bool = True,
                   verification_evidence_id: str | None = None, reason: str | None = None,
                   con: sqlite3.Connection | None = None) -> str:
        """State transitions are made by code only. COMPLETED additionally needs a passing
        TEST_VERIFICATION evidence row for the task's project (never a model claim)."""
        if target not in TRANSITIONS or (cli and target not in CLI_TRANSITIONS):
            raise ZenithError("Transition unavailable from CLI (execution requires verified worker)")
        if con is not None:
            return self._transition(con, task_id, target, actor, verification_evidence_id, reason)
        with self.write() as con:
            return self._transition(con, task_id, target, actor, verification_evidence_id, reason)

    def _transition(self, con, task_id, target, actor, verification_evidence_id, reason) -> str:
        if True:
            task = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise ZenithError("Unknown task ID")
            previous = task["state"]
            if target not in TRANSITIONS[previous]:
                raise ZenithError(f"Invalid transition: {previous} -> {target}")
            if target == "COMPLETED":
                ok = verification_evidence_id and con.execute(
                    "SELECT 1 FROM evidence WHERE id=? AND project_id=? AND origin='TEST_VERIFICATION' "
                    "AND json_extract(details,'$.passed')=1", (verification_evidence_id, task["project_id"])).fetchone()
                if not ok:
                    raise ZenithError("COMPLETED requires a passing TEST_VERIFICATION evidence id")
            if target == "QUEUED":
                unfinished = [r[0] for r in con.execute(
                    "SELECT d.depends_on FROM task_dependencies d JOIN tasks t ON t.id=d.depends_on "
                    "WHERE d.task_id=? AND t.state<>'COMPLETED'", (task_id,))]
                if unfinished:
                    raise ZenithError("Task is blocked by unfinished dependencies: " + ", ".join(unfinished))
                # L0 deterministic work can queue with no model call. Other risks require
                # an explicit, unexpired approval tied to the exact task scope.
                if task["risk"] != "L0" or previous in ("AWAITING_APPROVAL", "BLOCKED", "RECONCILE_REQUIRED"):
                    if self._valid_approval(con, task, "task.execute") is None:
                        raise ZenithError("A valid scope-bound approval is required")
            payload = {"from": previous, "to": target}
            if reason:
                payload["reason"] = reason[:500]
            con.execute("UPDATE tasks SET state=? WHERE id=?", (target, task_id))
            _record_event(con, "task", task_id, "task.state_changed", payload, actor)
        return target

    def approve(self, task_id: str, actor: str = "owner", minutes: int = 60,
                operation: str = "task.execute", max_uses: int = 1) -> str:
        """Owner grants one bounded, scope-hashed permission. Single-use unless max_uses > 1."""
        if not (1 <= minutes <= 1440):
            raise ZenithError("Approval lifetime must be 1–1440 minutes")
        if operation not in APPROVAL_OPERATIONS:
            raise ZenithError(f"Unknown operation {operation!r}")
        if operation not in GRANTABLE_OPERATIONS:
            raise ZenithError(f"{operation} is designed (REQ-002) but not grantable in this sprint")
        if not (1 <= max_uses <= 10):
            raise ZenithError("max_uses must be 1–10")
        if actor != "owner":
            raise ZenithError("Only the authenticated owner (local CLI) may grant approvals")
        with self.write() as con:
            task = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise ZenithError("Unknown task ID")
            if task["state"] not in ("AWAITING_APPROVAL", "BLOCKED", "RECONCILE_REQUIRED"):
                raise ZenithError("Task must be AWAITING_APPROVAL (or BLOCKED / RECONCILE_REQUIRED for a retry)")
            approval_id = _uuid()
            expiry = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="microseconds")
            con.execute(
                "INSERT INTO approvals(id,task_id,scope_hash,actor,expires_at,created_at,operation,project_id,max_uses) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (approval_id, task_id, self._scope_hash(task), actor, expiry, utcnow(), operation,
                 task["project_id"], max_uses),
            )
            _record_event(con, "task", task_id, "task.approved",
                          {"approval_id": approval_id, "operation": operation, "max_uses": max_uses,
                           "scope_hash": self._scope_hash(task), "expires_at": expiry}, actor)
        return approval_id

    # ---- Misa conversation state (Sprint 3) ---------------------------------------------------

    def conversation_state(self, conversation_id: str = "primary") -> dict:
        with self.connection() as con:
            row = con.execute("SELECT state FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            turns = con.execute("SELECT count(*) FROM conversation_turns WHERE conversation_id=? AND role='owner'",
                                (conversation_id,)).fetchone()[0]
        state = _load_json(row["state"], "conversation") if row else {}
        state["turns"] = turns
        return state

    def save_conversation_state(self, conversation_id: str, state: dict) -> None:
        if not _NAME_RE.match(conversation_id):
            raise ZenithError("conversation id must match [A-Za-z0-9._-]{1,64}")
        body = json.dumps({k: v for k, v in state.items() if k != "turns"}, ensure_ascii=False)
        with self.write() as con:
            con.execute("INSERT INTO conversations(id,state,created_at,updated_at) VALUES (?,?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
                        (conversation_id, body, utcnow(), utcnow()))

    def add_turn(self, conversation_id: str, role: str, language: str, intent: str | None, text: str,
                 citations: list[str], generated_by: str = "deterministic") -> None:
        """Append one conversation turn. `generated_by` distinguishes templates from (future) model text."""
        if role not in ("owner", "misa") or language not in ("tr", "en"):
            raise ZenithError("invalid turn role or language")
        text, _ = policy.redact(text)
        with self.write() as con:
            if con.execute("SELECT 1 FROM conversations WHERE id=?", (conversation_id,)).fetchone() is None:
                con.execute("INSERT INTO conversations(id,state,created_at,updated_at) VALUES (?,?,?,?)",
                            (conversation_id, "{}", utcnow(), utcnow()))
            con.execute("INSERT INTO conversation_turns(conversation_id,role,language,intent,text,citations,generated_by,"
                        "occurred_at) VALUES (?,?,?,?,?,?,?,?)",
                        (conversation_id, role, language, intent, text[:4000], json.dumps(citations[:50]), generated_by, utcnow()))

    def record_exchange(self, conversation_id: str, language: str, intent: str | None, owner_text: str,
                        misa_text: str, citations: list[str], state: dict, generated_by: str = "deterministic") -> None:
        """Owner turn + reply + conversation state in ONE transaction: no half-recorded exchanges."""
        if language not in ("tr", "en"):
            raise ZenithError("invalid language")
        if not _NAME_RE.match(conversation_id):
            raise ZenithError("conversation id must match [A-Za-z0-9._-]{1,64}")
        owner_text, _ = policy.redact(owner_text)
        misa_text, _ = policy.redact(misa_text)
        body = json.dumps({k: v for k, v in state.items() if k != "turns"}, ensure_ascii=False)
        now = utcnow()
        with self.write() as con:
            con.execute("INSERT INTO conversations(id,state,created_at,updated_at) VALUES (?,?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
                        (conversation_id, body, now, now))
            for role, text, gen in (("owner", owner_text, "owner"), ("misa", misa_text, generated_by)):
                con.execute("INSERT INTO conversation_turns(conversation_id,role,language,intent,text,citations,generated_by,"
                            "occurred_at) VALUES (?,?,?,?,?,?,?,?)",
                            (conversation_id, role, language, intent, text[:4000], json.dumps(citations[:50]), gen, now))

    def list_turns(self, conversation_id: str = "primary", limit: int = 20) -> list[dict]:
        with self.connection() as con:
            rows = con.execute("SELECT seq,role,language,intent,text,citations,generated_by,occurred_at FROM conversation_turns "
                               "WHERE conversation_id=? ORDER BY seq DESC LIMIT ?", (conversation_id, max(1, min(limit, 500)))).fetchall()
        return [{**dict(r), "citations": json.loads(r["citations"])} for r in reversed(rows)]

    # ---- artifacts and decisions (Engineering Director protocol persistence) --------------

    def add_artifact(self, con: sqlite3.Connection, kind: str, payload: dict, project_id: str,
                     task_id: str | None = None, run_id: str | None = None, parent_id: str | None = None,
                     produced_by: str = "zenith", schema_version: str = "zenith.director.v1",
                     actor: str = "zenith") -> str:
        """Persist an immutable, digest-stamped protocol artifact inside the caller's transaction."""
        artifact_id = _uuid()
        stamped = {**payload, "artifact_id": artifact_id, "produced_by": produced_by,
                   "created_at": utcnow(), "parent_id": parent_id}
        body = json.dumps(stamped, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        dig = hashlib.sha256(body.encode()).hexdigest()
        con.execute("INSERT INTO artifacts(id,seq,kind,schema_version,project_id,task_id,run_id,parent_id,"
                    "produced_by,payload,digest,created_at) VALUES (?,(SELECT COALESCE(MAX(seq),0)+1 FROM artifacts),"
                    "?,?,?,?,?,?,?,?,?,?)",
                    (artifact_id, kind, schema_version, project_id, task_id, run_id, parent_id, produced_by,
                     body, dig, stamped["created_at"]))
        _record_event(con, "artifact", artifact_id, "artifact.recorded",
                      {"kind": kind, "task_id": task_id, "run_id": run_id, "parent_id": parent_id,
                       "produced_by": produced_by, "digest": dig}, actor)
        return artifact_id

    def get_artifact(self, artifact_id: str, verify: bool = True) -> dict:
        with self.connection() as con:
            row = con.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row is None:
            raise ZenithError(f"Unknown artifact: {artifact_id}")
        if verify and hashlib.sha256(row["payload"].encode()).hexdigest() != row["digest"]:
            raise ZenithError(f"Artifact {artifact_id} failed its integrity check")
        return {**dict(row), "payload": _load_json(row["payload"], "artifact")}

    def list_artifacts(self, project_id: str | None = None, task_id: str | None = None,
                       run_id: str | None = None, kind: str | None = None, limit: int = 100) -> list[dict]:
        where, args = [], []
        for col, val in (("project_id", project_id), ("task_id", task_id), ("run_id", run_id), ("kind", kind)):
            if val:
                where.append(f"{col}=?")
                args.append(val)
        sql = "SELECT id,seq,kind,schema_version,project_id,task_id,run_id,parent_id,produced_by,digest,created_at " \
              "FROM artifacts" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY seq DESC LIMIT ?"
        with self.connection() as con:
            return [dict(r) for r in con.execute(sql, (*args, max(1, min(limit, 500))))]

    def add_decision(self, project_id: str, decision: str, rationale: str, made_by: str = "owner",
                     task_id: str | None = None, run_id: str | None = None, artifact_id: str | None = None,
                     con: sqlite3.Connection | None = None) -> str:
        decision_id = _uuid()

        def _do(c):
            c.execute("INSERT INTO decisions(id,project_id,task_id,run_id,artifact_id,decision,rationale,made_by,"
                      "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (decision_id, project_id, task_id, run_id, artifact_id, decision[:2000], rationale[:4000],
                       made_by, utcnow()))
            _record_event(c, "decision", decision_id, "decision.recorded",
                          {"project_id": project_id, "task_id": task_id, "run_id": run_id, "made_by": made_by}, made_by)
        if con is not None:
            _do(con)
        else:
            with self.write() as c:
                _do(c)
        return decision_id

    def list_decisions(self, project_id: str, limit: int = 50) -> list[dict]:
        with self.connection() as con:
            return [dict(r) for r in con.execute("SELECT * FROM decisions WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                                                 (project_id, max(1, min(limit, 500))))]

    def events(self, limit: int = 20) -> list[dict]:
        with self.connection() as con:
            rows = con.execute("SELECT seq,aggregate_type,aggregate_id,event_type,actor,payload,occurred_at "
                               "FROM events ORDER BY seq DESC LIMIT ?", (max(1, min(limit, 500)),))
            return [dict(row) for row in rows]

    def source_overlaps(self) -> list[dict]:
        """Active sources that nest or duplicate each other (only possible for pre-v3 data)."""
        with self.connection() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT id, project_id, node_id, path FROM project_sources WHERE removed_at IS NULL ORDER BY path")]
        out = []
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                if a["node_id"] == b["node_id"] and policy.path_relation(a["path"], b["path"]):
                    out.append({"a": a, "b": b, "relation": policy.path_relation(a["path"], b["path"])})
        return out

    # ---- observation, snapshots and evidence -------------------------------------------------

    def _insert_evidence(self, con: sqlite3.Connection, project_id: str, origin: str, summary: str,
                         source_id: str | None = None, source_ref: str | None = None,
                         digest: str | None = None, details: dict | None = None,
                         node_id: str = "local", observed_at: str | None = None,
                         actor: str = "owner") -> str:
        """The only INSERT path into the evidence ledger; confidence follows origin, never the caller."""
        evidence_id, now = _uuid(), utcnow()
        confidence = CONFIDENCE_BY_ORIGIN[origin]
        digest = digest or hashlib.sha256(summary.encode()).hexdigest()
        # `seq` is assigned inside the write transaction, so it is a gap-free, stable record of
        # the order in which evidence was recorded (rowids are not stable across VACUUM).
        con.execute(
            "INSERT INTO evidence (id,project_id,source_id,origin,confidence,summary,source_ref,digest,"
            "details,node_id,observed_at,last_confirmed_at,recorded_at,seq) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,(SELECT COALESCE(MAX(seq),0)+1 FROM evidence))",
            (evidence_id, project_id, source_id, origin, confidence, summary[:2000], source_ref, digest,
             json.dumps(details or {}, ensure_ascii=False), node_id, observed_at or now,
             observed_at or now, now))
        _record_event(con, "evidence", evidence_id, "evidence.recorded",
                      {"project_id": project_id, "source_id": source_id, "origin": origin,
                       "confidence": confidence}, actor)
        return evidence_id

    def scan_source(self, source_id: str) -> dict:
        """Read-only metadata observation of one registered source.

        Records a snapshot and a REPOSITORY_OBSERVATION evidence row. A scan identical to the
        previous one only refreshes `last_seen_at`; it does not add a duplicate snapshot.
        """
        with self.connection() as con:
            src = dict(self._source_row(con, source_id))
        registered = Path(src["path"])
        result: dict = {"source_id": source_id, "project_id": src["project_id"],
                        "path": src["path"], "observed_at": utcnow()}
        try:
            real = registered.resolve(strict=True)
        except OSError:
            real = None
        roots = policy.allowed_roots()
        if real is None or not real.is_dir():
            result.update(source_status="unavailable", reason="path_missing")
        elif real != registered:  # registered directory was swapped for a symlink elsewhere
            result.update(source_status="unavailable", reason="path_resolves_elsewhere")
        elif roots and not any(real == r or r in real.parents for r in roots):
            # The allowlist is re-checked at scan time, not only at registration time.
            result.update(source_status="unavailable", reason="outside_allowlist")
        else:
            result.update(source_status="available", is_git_repo=False)
            result.update(scanner.git_metadata(real))
            result.update(scanner.scan_tree(real))
        keys = ("source_status", "reason", "path", "is_git_repo", "head", "branch",
                "tracked_changed_count", "fingerprint", "file_count", "truncated")
        signature = hashlib.sha256(json.dumps({k: result.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()
        if result["source_status"] != "available":
            summary = f"source unavailable ({result['reason']})"
        elif result.get("head"):
            summary = (f"git {result['head'][:12]} on {result.get('branch') or 'detached HEAD'}; "
                       f"{result.get('tracked_changed_count')} tracked changes; {result['file_count']} files (metadata only)")
        else:
            summary = f"{result['file_count']} files; no commits or no Git repository (metadata only)"
        if result.get("truncated"):
            summary += "; scan truncated at limits"
        with self.write() as con:
            self._source_row(con, source_id)
            prev = con.execute("SELECT id,content_signature FROM snapshots WHERE source_id=? "
                               "ORDER BY collected_at DESC, rowid DESC LIMIT 1", (source_id,)).fetchone()
            if prev is not None and prev["content_signature"] == signature:
                snapshot_id = prev["id"]
                con.execute("UPDATE snapshots SET last_seen_at=?, seen_count=seen_count+1 WHERE id=?",
                            (result["observed_at"], snapshot_id))
                con.execute("UPDATE evidence SET last_confirmed_at=? WHERE source_ref=? "
                            "AND origin='REPOSITORY_OBSERVATION'", (result["observed_at"], f"snapshot:{snapshot_id}"))
                result["unchanged"] = True
            else:
                snapshot_id = _uuid()
                con.execute(
                    "INSERT INTO snapshots(id,project_id,collected_at,source_type,details,source_id,"
                    "content_signature,last_seen_at) VALUES (?,?,?,?,?,?,?,?)",
                    (snapshot_id, src["project_id"], result["observed_at"],
                     "git_metadata" if result.get("is_git_repo") else "fs_metadata",
                     json.dumps(result), source_id, signature, result["observed_at"]))
                result["evidence_id"] = self._insert_evidence(
                    con, src["project_id"], "REPOSITORY_OBSERVATION", summary, source_id=source_id,
                    source_ref=f"snapshot:{snapshot_id}", digest=signature, node_id=src["node_id"],
                    observed_at=result["observed_at"], actor="zenith",
                    details={k: result.get(k) for k in ("source_status", "head", "branch", "tracked_changed_count",
                                                        "file_count", "truncated")})
                result["unchanged"] = False
            result["snapshot_id"] = snapshot_id
            if result["source_status"] == "available":
                con.execute("UPDATE project_sources SET last_seen_at=? WHERE id=?",
                            (result["observed_at"], source_id))
            _record_event(con, "project", src["project_id"], "project.observed",
                          {k: v for k, v in result.items() if k != "path"}, actor="zenith")
        return result

    def scan_sources(self, project_id: str) -> list[dict]:
        sources = self.list_sources(project_id)
        if not sources:
            raise ZenithError("Project has no registered sources")
        return [self.scan_source(s["id"]) for s in sources]

    def scan_project(self, project_id: str) -> dict:
        """Scan the project's primary (first-added) source. Use `scan_sources` for all of them."""
        sources = self.list_sources(project_id)
        if not sources:
            raise ZenithError("Project has no registered sources")
        return self.scan_source(sources[0]["id"])

    def compare_snapshots(self, source_id: str) -> dict:
        """Compare the two most recent distinct snapshots of a source."""
        with self.connection() as con:
            src = dict(self._source_row(con, source_id, active=False))
            rows = con.execute("SELECT * FROM snapshots WHERE source_id=? "
                               "ORDER BY collected_at DESC, rowid DESC LIMIT 2", (source_id,)).fetchall()
        if len(rows) < 2:
            return {"source_id": source_id, "status": "insufficient_history", "snapshots": len(rows),
                    "note": "Need two distinct observations; scan again after the source changes."}
        new, old = _load_json(rows[0]["details"], "snapshot"), _load_json(rows[1]["details"], "snapshot")
        fields = ("source_status", "path", "is_git_repo", "head", "branch", "tracked_changed_count",
                  "file_count", "fingerprint")
        changes = {f: {"from": old.get(f), "to": new.get(f)} for f in fields if old.get(f) != new.get(f)}
        notes: list[str] = []
        if new.get("source_status") != "available":
            notes.append("Source unavailable at the latest scan: current content is unknown.")
        elif old.get("source_status") != "available":
            notes.append("Source was unavailable at the previous scan: nothing to compare against.")
        else:
            if "path" in changes:
                notes.append("Source path changed between scans (moved).")
            if "head" in changes:
                n = None
                if old.get("head") and new.get("head"):
                    n = scanner.commits_between(Path(new["path"]), old["head"], new["head"])
                changes["head"]["commits_between"] = n
                notes.append(f"HEAD moved {str(old.get('head'))[:7]} -> {str(new.get('head'))[:7]}"
                             + (f" ({n} commits)" if n is not None else "")
                             + ". Commits are activity, not proof of completion.")
            elif "fingerprint" in changes:
                notes.append("Files changed without a new commit (uncommitted or non-Git work).")
            if any(old.get(f) is None for f in ("file_count", "fingerprint")):
                notes.append("Previous snapshot predates metadata fingerprints; file-level comparison unknown.")
        return {"source_id": source_id, "status": "changed" if changes else "unchanged",
                "from": {"snapshot_id": rows[1]["id"], "collected_at": rows[1]["collected_at"]},
                "to": {"snapshot_id": rows[0]["id"], "collected_at": rows[0]["collected_at"],
                       "last_seen_at": rows[0]["last_seen_at"]},
                "changes": changes, "notes": notes}

    def add_evidence(self, project_id: str, origin: str, summary: str, source_ref: str | None = None,
                     observed_at: str | None = None, node_id: str = "local", actor: str = "owner") -> dict:
        """Attach a human-supplied claim. REPOSITORY_OBSERVATION and TEST_VERIFICATION are refused."""
        if origin not in MANUAL_EVIDENCE_ORIGINS:
            raise ZenithError("Origin %s can only be produced by Zenith's own scanner/verifier; "
                              "manual origins: %s" % (origin, ", ".join(MANUAL_EVIDENCE_ORIGINS)))
        summary, redactions = policy.redact(summary.strip())
        if not summary or len(summary) > 2000:
            raise ZenithError("Evidence summary must be 1–2000 characters")
        if not _NAME_RE.match(node_id):
            raise ZenithError("node id must match [A-Za-z0-9._-]{1,64}")
        when = self._parse_observed(observed_at)
        with self.write() as con:
            self._project_row(con, project_id)
            evidence_id = self._insert_evidence(con, project_id, origin, summary, source_ref=source_ref,
                                                node_id=node_id, observed_at=when, actor=actor,
                                                details={"redactions": redactions})
        return {"evidence_id": evidence_id, "origin": origin, "confidence": CONFIDENCE_BY_ORIGIN[origin],
                "observed_at": when, "redactions": redactions}

    @staticmethod
    def _parse_observed(value: str | None) -> str:
        now = datetime.now(timezone.utc)
        if not value:
            return utcnow()  # full precision, so it orders correctly against scan timestamps
        try:
            when = dates.parse_instant(value)
        except ValueError as exc:
            raise ZenithError("Timestamp must be ISO 8601 (e.g. 2026-10-20T18:00)") from exc
        if when > now + timedelta(minutes=5):
            raise ZenithError("Timestamp is in the future")
        return dates.iso_utc(when)

    def checkin(self, project_id: str, note: str, node_id: str = "local",
                observed_at: str | None = None) -> dict:
        """Capture a short human update. Stored as HUMAN_REPORT / REPORTED, never VERIFIED.

        `observed_at` lets a second, offline machine back-date the note; if Zenith already holds
        later observations or verifications, the result says so.
        """
        note = note.strip()
        if not note or len(note) > 1000:
            raise ZenithError("Check-in note must be 1–1000 characters")
        out = self.add_evidence(project_id, "HUMAN_REPORT", note, source_ref=f"checkin:{node_id}",
                                observed_at=observed_at, node_id=node_id)
        with self.connection() as con:
            later = con.execute(
                "SELECT id,origin,last_confirmed_at FROM evidence WHERE project_id=? AND "
                "origin IN ('REPOSITORY_OBSERVATION','TEST_VERIFICATION') AND last_confirmed_at>? "
                "ORDER BY last_confirmed_at DESC, seq DESC LIMIT 5", (project_id, out["observed_at"])).fetchall()
        out["later_evidence"] = [dict(x) for x in later]
        out["reconciliation"] = ("later repository/test evidence exists; treat this report as possibly outdated"
                                 if later else "no later evidence; claim is unverified until observed or tested")
        return out

    def record_test_verification(self, project_id: str, source_id: str, command_id: str, exit_code: int,
                                 head: str, output_digest: str | None = None,
                                 actor: str = "zenith-verifier") -> str:
        """Plumbing for the deterministic verifier (Sprint 2). Deliberately has no CLI surface."""
        if actor != "zenith-verifier":
            raise ZenithError("Only the deterministic verifier may record TEST_VERIFICATION evidence")
        if not _NAME_RE.match(command_id) or not isinstance(exit_code, int) or not re.fullmatch(r"[0-9a-f]{7,64}", head):
            raise ZenithError("Invalid verification record")
        details = {"source_id": source_id, "command_id": command_id, "exit_code": exit_code,
                   "passed": exit_code == 0, "head": head}
        with self.write() as con:
            src = self._source_row(con, source_id, active=False)
            if src["project_id"] != project_id:
                raise ZenithError("Source does not belong to that project")
            return self._insert_evidence(
                con, project_id, "TEST_VERIFICATION", f"{command_id} exit={exit_code} at {head[:12]}",
                source_id=source_id, source_ref=f"git:{head}", digest=output_digest,
                details=details, node_id=src["node_id"], actor=actor)

    def list_evidence(self, project_id: str, origin: str | None = None, limit: int = 50) -> list[dict]:
        if origin is not None and origin not in EVIDENCE_ORIGINS:
            raise ZenithError(f"Unknown origin: {origin}")
        sql, args = "SELECT * FROM evidence WHERE project_id=?", [project_id]
        if origin:
            sql += " AND origin=?"
            args.append(origin)
        with self.connection() as con:
            self._project_row(con, project_id)
            rows = con.execute(sql + " ORDER BY observed_at DESC, seq DESC LIMIT ?",
                               (*args, max(1, min(limit, 500)))).fetchall()
        return [{**dict(r), "details": _load_json(r["details"], "evidence")} for r in rows]

    def brief(self, project_id: str | None = None, now: datetime | None = None) -> dict:
        from .brief import build_brief
        return build_brief(self, project_id, now or datetime.now(timezone.utc))
