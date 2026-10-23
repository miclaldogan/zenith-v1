"""Sprint 1 acceptance hardening: migrations, path ownership, integrity, ordering, scanner, CLI."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from zenith import migrations, policy, scanner
from zenith.kernel import Store, ZenithError

SRC = str(Path(__file__).resolve().parents[1] / "src")


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
                          cwd=path, check=True, capture_output=True, text=True).stdout.strip()


def commit(path: Path, name: str = "f.txt", text: str = "x") -> str:
    (path / name).write_text(text)
    git(path, "add", name)
    git(path, "commit", "-q", "-m", f"add {name}")
    return git(path, "rev-parse", "HEAD")


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.other = self.root / "other"
        self.other.mkdir()
        self.store = Store(self.root / "data" / "zenith.db")
        self.store.init()

    def tearDown(self):
        self.temp.cleanup()

    def legacy_db(self, upto: int) -> Path:
        """A database at schema version `upto` holding data in every table that exists there."""
        path = self.root / f"legacy-v{upto}.db"
        migrations.apply_pending(path, migrations=migrations.MIGRATIONS[:upto], backup=False)
        con = sqlite3.connect(path)
        if upto == 1:
            con.executescript("""
              INSERT INTO projects VALUES ('p1','Legacy','%s','personal','active','2026-01-01T00:00:00+00:00');
              INSERT INTO tasks VALUES ('t1','p1','Old task','obj','L1','AWAITING_APPROVAL','2026-01-02T00:00:00+00:00','2026-11-01',1);
              INSERT INTO approvals VALUES ('a1','t1','hash','owner','2099-01-01T00:00:00+00:00','2026-01-02T00:00:00+00:00');
              INSERT INTO snapshots VALUES ('s1','p1','2026-01-03T00:00:00+00:00','git_metadata','{"head": "abc", "source_status": "available"}');
              INSERT INTO events VALUES (1,'e1','project','p1','project.created','owner','{}','2026-01-01T00:00:00+00:00');
              INSERT INTO events VALUES (2,'e2','task','t1','task.created','owner','{}','2026-01-02T00:00:00+00:00');
            """ % self.repo)
        else:  # v2 rows exactly as the Sprint 1 kernel wrote them (no seq column yet)
            con.executescript("""
              INSERT INTO projects VALUES ('p1','V2','personal','active','2026-01-01T00:00:00+00:00');
              INSERT INTO project_sources(id,project_id,path,added_at) VALUES ('s1','p1','%s','2026-01-01T00:00:00+00:00');
              INSERT INTO evidence(id,project_id,origin,confidence,summary,details,observed_at,last_confirmed_at,recorded_at)
                VALUES ('e1','p1','HUMAN_REPORT','REPORTED','first','{}','2026-01-01T00:00:01+00:00','2026-01-01T00:00:01+00:00','2026-01-01T00:00:01+00:00');
              INSERT INTO evidence(id,project_id,origin,confidence,summary,details,observed_at,last_confirmed_at,recorded_at)
                VALUES ('e2','p1','HUMAN_REPORT','REPORTED','second','{}','2026-01-01T00:00:01+00:00','2026-01-01T00:00:01+00:00','2026-01-01T00:00:02+00:00');
            """ % self.repo)
        con.commit()
        con.close()
        return path


class MigrationV3Tests(Base):
    def test_v1_to_latest_preserves_every_table(self):
        path = self.legacy_db(1)
        Store(path).init()
        con = sqlite3.connect(path)
        self.assertEqual(migrations.current_version(con), migrations.LATEST_VERSION)
        self.assertEqual(con.execute("SELECT name FROM projects").fetchone()[0], "Legacy")
        self.assertEqual(con.execute("SELECT state, due_at FROM tasks").fetchone(), ("AWAITING_APPROVAL", "2026-11-01"))
        self.assertEqual(con.execute("SELECT scope_hash FROM approvals").fetchone()[0], "hash")
        self.assertEqual(con.execute("SELECT count(*) FROM events WHERE id IN ('e1','e2')").fetchone()[0], 2)
        self.assertEqual(con.execute("SELECT source_id IS NOT NULL FROM snapshots").fetchone()[0], 1)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        triggers = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        self.assertTrue({"events_no_update", "events_no_delete", "evidence_seq_required",
                         "sources_no_overlap_insert", "sources_no_overlap_update"} <= triggers)
        # The pre-existing approval still gates the legacy task exactly as before.
        store = Store(path)
        with self.assertRaises(ZenithError):
            store.transition("t1", "QUEUED")  # scope hash 'hash' is not the real one -> refused

    def test_v2_to_v3_assigns_gap_free_seq_in_recording_order(self):
        path = self.legacy_db(2)
        con = sqlite3.connect(path)
        self.assertEqual(migrations.current_version(con), 2)
        self.assertNotIn("seq", [r[1] for r in con.execute("PRAGMA table_info(evidence)")])
        con.close()
        Store(path).init()
        con = sqlite3.connect(path)
        rows = con.execute("SELECT seq, summary FROM evidence ORDER BY recorded_at").fetchall()
        self.assertEqual(rows, [(1, "first"), (2, "second")])
        self.assertEqual(len(list(self.root.glob("legacy-v2.db.pre-v3-*.bak"))), 1)
        # New rows continue the sequence, assigned inside the write transaction.
        store = Store(path)
        pid = store.list_projects()[0]["id"]
        store.checkin(pid, "third")
        self.assertEqual(con.execute("SELECT max(seq), count(*) FROM evidence").fetchone(), (3, 3))

    def test_crash_before_commit_leaves_previous_version_intact(self):
        """A process dying mid-upgrade (after all DDL, before COMMIT) must not corrupt the file."""
        path = self.legacy_db(1)
        code = f"""
import os, sys
sys.path.insert(0, {SRC!r})
from zenith import migrations
migrations._BEFORE_COMMIT_HOOK = lambda v: os._exit(9) if v == 2 else None
migrations.apply_pending({str(path)!r})
"""
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 9, r.stderr)
        con = sqlite3.connect(path)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(migrations.current_version(con), 1)
        self.assertIn("path", [r[1] for r in con.execute("PRAGMA table_info(projects)")])
        self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='evidence'").fetchone())
        self.assertEqual(con.execute("SELECT count(*) FROM tasks").fetchone()[0], 1)
        con.close()
        self.assertEqual(len(list(self.root.glob("legacy-v1.db.pre-v*.bak"))), 1)  # kept, not deleted
        Store(path).init()  # the retry completes from the intact v1 state
        self.assertEqual(Store(path).get_project("p1")["name"], "Legacy")

    def test_restore_rehearsal_from_backup_copy(self):
        pid = self.store.add_project("Live", str(self.repo))
        self.store.checkin(pid, "before")
        backup = self.store.backup(self.root / "rehearsal.db")
        self.store.checkin(pid, "after")
        # Restore = point ZENITH_DB_PATH at the backup and run init (idempotent at current version).
        restored = Store(backup)
        restored.init()
        self.assertEqual([e["summary"] for e in restored.list_evidence(pid)], ["before"])
        self.assertEqual(sqlite3.connect(backup).execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(list(self.root.glob("rehearsal.db.pre-v*.bak")), [])
        self.assertEqual(len(self.store.list_evidence(pid)), 2)  # original untouched by the rehearsal

    def test_repeated_init_is_idempotent(self):
        before = sqlite3.connect(self.store.db_path).execute("SELECT count(*) FROM events").fetchone()[0]
        for _ in range(3):
            self.store.init()
        after = sqlite3.connect(self.store.db_path).execute("SELECT count(*) FROM events").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(list((self.root / "data").glob("*.bak")), [])

    def test_failed_backup_leaves_no_partial_file_and_blocks_migration(self):
        path = self.legacy_db(1)
        data = bytearray(path.read_bytes())
        data[4096 + 8: 4096 + 40] = b"\xff" * 32  # corrupt the second page
        path.write_bytes(data)
        with self.assertRaises((migrations.MigrationError, sqlite3.DatabaseError)):
            migrations.backup_database(path, self.root / "partial.db")
        self.assertFalse((self.root / "partial.db").exists())
        with self.assertRaises((ZenithError, sqlite3.DatabaseError, migrations.MigrationError)):
            Store(path).init()
        self.assertEqual(path.read_bytes(), bytes(data))  # the damaged file was not touched


class PathOwnershipTests(Base):
    def test_parent_child_overlap_refused_across_and_within_projects(self):
        child = self.repo / "sub"
        child.mkdir()
        a = self.store.add_project("A", str(self.repo))
        b = self.store.add_project("B", str(self.other))
        with self.assertRaisesRegex(ZenithError, "is inside"):
            self.store.add_source(b, str(child))
        with self.assertRaisesRegex(ZenithError, "is inside"):
            self.store.add_source(a, str(child))  # same project: still ambiguous ownership
        self.store.remove_source(self.store.list_sources(a)[0]["id"])
        self.store.add_source(b, str(child))  # free once the parent is detached
        with self.assertRaisesRegex(ZenithError, "contains"):
            self.store.add_source(a, str(self.repo))
        self.assertEqual(self.store.source_overlaps(), [])

    def test_database_trigger_refuses_overlap_from_raw_sql(self):
        self.store.add_project("A", str(self.repo))
        con = sqlite3.connect(self.store.db_path)
        for bad in (str(self.repo), str(self.repo / "x"), str(self.root)):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "overlaps"):
                con.execute("INSERT INTO project_sources(id,project_id,path,added_at) VALUES ('x','p',?,'t')", (bad,))
        con.execute("INSERT INTO project_sources(id,project_id,path,added_at,removed_at) "
                    "VALUES ('x','p',?,'t','t')", (str(self.repo),))  # removed rows do not claim
        self.assertEqual(len(self.store.source_overlaps()), 0)

    def test_move_cannot_create_overlap_but_may_move_within_itself(self):
        p = self.store.add_project("A", str(self.repo))
        src = self.store.list_sources(p)[0]["id"]
        self.store.add_project("B", str(self.other))
        with self.assertRaises(ZenithError):
            self.store.move_source(src, str(self.other))
        sub = self.other / "deep"
        sub.mkdir()
        with self.assertRaises(ZenithError):
            self.store.move_source(src, str(sub))
        own_sub = self.repo / "inner"
        own_sub.mkdir()
        self.store.move_source(src, str(own_sub))  # narrowing a source is not an overlap with itself
        self.assertEqual(self.store.get_project(p)["path"], str(own_sub))

    def test_symlinked_and_traversal_paths_are_canonicalised(self):
        link = self.root / "link"
        os.symlink(self.repo, link)
        p = self.store.add_project("A", str(link / ".." / "repo" / "."))
        self.assertEqual(self.store.get_project(p)["path"], str(self.repo))
        with self.assertRaises(ZenithError):
            self.store.add_project("B", str(link))  # same directory through a symlink
        with self.assertRaises(ZenithError):
            self.store.add_source(p, str(self.repo / ".." / "repo"))

    def test_allowlist_is_enforced_at_scan_time(self):
        p = self.store.add_project("A", str(self.repo))
        src = self.store.list_sources(p)[0]["id"]
        self.assertEqual(self.store.scan_source(src)["source_status"], "available")
        with mock.patch.dict(os.environ, {"ZENITH_ALLOWED_ROOTS": str(self.root / "elsewhere")}):
            out = self.store.scan_source(src)
        self.assertEqual((out["source_status"], out["reason"]), ("unavailable", "outside_allowlist"))
        self.assertNotIn("file_count", out)
        with mock.patch.dict(os.environ, {"ZENITH_ALLOWED_ROOTS": str(self.root)}):
            self.assertEqual(self.store.scan_source(src)["source_status"], "available")

    def test_doctor_reports_pre_existing_overlaps(self):
        self.store.add_project("A", str(self.repo))
        con = sqlite3.connect(self.store.db_path)
        for t in ("sources_no_overlap_insert",):
            con.execute(f"DROP TRIGGER {t}")  # simulate rows written before v3
        con.execute("INSERT INTO project_sources(id,project_id,path,added_at) VALUES ('old','p',?,'t')",
                    (str(self.repo / "nested"),))
        con.commit()
        overlaps = self.store.source_overlaps()
        self.assertEqual(len(overlaps), 1)
        self.assertEqual(overlaps[0]["relation"], "contains")


class IntegrityAndOrderingTests(Base):
    def setUp(self):
        super().setUp()
        self.project = self.store.add_project("I", str(self.repo))
        self.source = self.store.list_sources(self.project)[0]["id"]

    def test_events_are_immutable_through_sql(self):
        con = sqlite3.connect(self.store.db_path)
        for sql in ("UPDATE events SET payload='{}'", "UPDATE events SET event_type='x'", "DELETE FROM events"):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                con.execute(sql)
        self.assertGreaterEqual(con.execute("SELECT count(*) FROM events").fetchone()[0], 2)

    def test_evidence_seq_is_required_unique_and_immutable(self):
        e = self.store.checkin(self.project, "a")["evidence_id"]
        con = sqlite3.connect(self.store.db_path)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "seq must be assigned"):
            con.execute("INSERT INTO evidence(id,project_id,origin,confidence,summary,observed_at,last_confirmed_at,"
                        "recorded_at) VALUES ('z',?,'HUMAN_REPORT','REPORTED','s','t','t','t')", (self.project,))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            con.execute("UPDATE evidence SET seq=99 WHERE id=?", (e,))
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("INSERT INTO evidence(id,project_id,origin,confidence,summary,observed_at,last_confirmed_at,"
                        "recorded_at,seq) VALUES ('z',?,'HUMAN_REPORT','REPORTED','s','t','t','t',1)", (self.project,))

    def test_rapid_events_and_evidence_reconstruct_in_order(self):
        ids = [self.store.checkin(self.project, f"note {i}")["evidence_id"] for i in range(40)]
        con = sqlite3.connect(self.store.db_path)
        seqs = [r[0] for r in con.execute("SELECT seq FROM evidence ORDER BY seq")]
        self.assertEqual(seqs, list(range(1, 41)))
        by_seq = [r[0] for r in con.execute("SELECT id FROM evidence ORDER BY seq")]
        self.assertEqual(by_seq, ids)
        stamps = [r[0] for r in con.execute("SELECT occurred_at FROM events ORDER BY seq")]
        self.assertEqual(stamps, sorted(stamps))  # microsecond UTC strings order chronologically
        self.assertTrue(all("." in s for s in stamps))
        # list_evidence is deterministic even when observed_at ties.
        listed = [e["id"] for e in self.store.list_evidence(self.project, limit=40)]
        self.assertEqual(listed, sorted(listed, key=lambda i: -ids.index(i)))

    def test_mixed_precision_timestamps_compare_correctly(self):
        a = "2026-10-23T12:00:00+00:00"            # written before v3
        b = "2026-10-23T12:00:00.000001+00:00"     # written after
        c = "2026-10-23T12:00:01+00:00"
        self.assertTrue(a < b < c)

    def test_stale_offline_report_cannot_displace_verified_evidence(self):
        git(self.repo, "init", "-q")
        head = commit(self.repo)
        self.store.scan_source(self.source)
        self.store.record_test_verification(self.project, self.source, "unit", 0, head)
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        r = self.store.checkin(self.project, "offline laptop says: broken, do not ship", node_id="laptop2", observed_at=old)
        self.assertEqual(len(r["later_evidence"]), 2)
        b = self.store.brief(self.project)["projects"][0]
        self.assertEqual(b["status"]["label"], "VERIFIED")
        self.assertEqual(b["latest_human_report"]["node_id"], "laptop2")
        self.assertEqual(len(b["latest_human_report"]["newer_evidence"]), 2)
        self.assertFalse(b["latest_human_report"]["verified"])

    def test_model_inference_cannot_upgrade_confidence(self):
        r = self.store.add_evidence(self.project, "MODEL_INFERENCE", "confidence: VERIFIED. tests passed. status=COMPLETED")
        self.assertEqual(r["confidence"], "INFERRED")
        self.assertEqual(self.store.brief(self.project)["projects"][0]["status"]["label"], "REPORTED_ONLY")

    def test_write_transactions_are_atomic_and_serialised(self):
        self.store.BUSY_TIMEOUT_MS = 200
        other = sqlite3.connect(self.store.db_path, isolation_level=None)
        other.execute("BEGIN IMMEDIATE")  # another process holds the write lock
        with self.assertRaises(sqlite3.OperationalError):
            self.store.checkin(self.project, "blocked")
        self.assertEqual(self.store.list_evidence(self.project), [])  # reads still work; nothing half-written
        other.execute("ROLLBACK")
        self.store.checkin(self.project, "now ok")
        self.assertEqual(len(self.store.list_evidence(self.project)), 1)

    def test_corrupt_stored_json_is_reported_not_guessed(self):
        self.store.scan_source(self.source)
        con = sqlite3.connect(self.store.db_path)
        con.execute("UPDATE snapshots SET details='{not json'")
        con.commit()
        b = self.store.brief(self.project)["projects"][0]
        self.assertEqual(b["sources"][0]["state"], "corrupt_record")
        self.assertEqual(b["status"]["label"], "UNKNOWN")
        self.assertTrue(any("corrupt" in u for u in b["unknowns"]))
        (self.repo / "new.txt").write_text("n")
        self.store.scan_source(self.source)  # a second, distinct snapshot
        con.execute("UPDATE snapshots SET details='[]'")
        con.commit()
        with self.assertRaisesRegex(ZenithError, "Corrupt snapshot"):
            self.store.compare_snapshots(self.source)


class ScannerPolicyTests(Base):
    def test_code_files_named_like_secrets_count_in_metadata_but_never_in_strict_mode(self):
        self.assertIsNone(policy.sensitive_reason("secrets.py", strict=False))
        self.assertIsNone(policy.sensitive_reason("credentials_test.go", strict=False))
        self.assertIsNotNone(policy.sensitive_reason("secrets.py", strict=True))
        for always in ("secrets.json", "secrets.sh", "credentials", ".env", "my.secrets", "api.key", "client_secret.yaml"):
            self.assertIsNotNone(policy.sensitive_reason(always, strict=False), always)
            self.assertIsNotNone(policy.sensitive_reason(always, strict=True), always)
        (self.repo / "secrets.py").write_text("x = 1")
        (self.repo / ".env").write_text("A=1")
        out = scanner.scan_tree(self.repo)
        self.assertEqual((out["file_count"], out["skipped"]["sensitive"]), (1, 1))
        self.assertNotIn("secrets.py", json.dumps(out))  # names never appear in output

    def test_git_status_is_limited_to_the_source_directory(self):
        git(self.repo, "init", "-q")
        sub = self.repo / "pkg"
        sub.mkdir()
        commit(self.repo, "pkg/a.txt")
        commit(self.repo, "top.txt")
        (self.repo / "top.txt").write_text("dirty outside the source")
        meta = scanner.git_metadata(sub)
        self.assertTrue(meta["git_root_outside_source"])
        self.assertEqual(meta["tracked_changed_count"], 0)
        (sub / "a.txt").write_text("dirty inside")
        self.assertEqual(scanner.git_metadata(sub)["tracked_changed_count"], 1)

    def test_scan_of_unreadable_directory_is_counted_not_fatal(self):
        locked = self.repo / "locked"
        locked.mkdir()
        (locked / "x").write_text("x")
        locked.chmod(0)
        try:
            out = scanner.scan_tree(self.repo)
        finally:
            locked.chmod(0o700)
        self.assertEqual(out["skipped"]["unreadable"], 1)
        self.assertEqual(out["file_count"], 0)


class CliHardeningTests(unittest.TestCase):
    def run_cli(self, env, *args):
        return subprocess.run([sys.executable, "-m", "zenith", *args], env=env, capture_output=True, text=True)

    def test_old_schema_and_corrupt_file_give_clean_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "zenith.db"
            migrations.apply_pending(db, migrations=migrations.MIGRATIONS[:1], backup=False)
            env = {**os.environ, "ZENITH_DB_PATH": str(db), "PYTHONPATH": SRC}
            doc = json.loads(self.run_cli(env, "doctor").stdout)
            self.assertEqual(doc["schema_version"], 1)
            self.assertIn("zenith init", doc["schema_action"])
            r = self.run_cli(env, "project", "list")
            self.assertEqual(r.returncode, 2)
            self.assertIn("zenith init", r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            db.write_bytes(b"this is not a database file" * 100)
            r = self.run_cli(env, "project", "list")
            self.assertEqual(r.returncode, 3)
            self.assertIn("database error", r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_unsafe_paths_rejected_from_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "ZENITH_DB_PATH": str(Path(tmp) / "z.db"), "PYTHONPATH": SRC}
            self.run_cli(env, "init")
            for bad in ("/", str(Path.home()), str(Path(tmp) / "missing"), "../../etc"):
                r = self.run_cli(env, "project", "add", "--name", "x", "--path", bad)
                self.assertEqual(r.returncode, 2, bad)
                self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
