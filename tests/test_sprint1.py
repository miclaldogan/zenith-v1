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

from zenith import dates, migrations, policy
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
        self.other = self.root / "notes"
        self.other.mkdir()
        self.store = Store(self.root / "data" / "zenith.db")
        self.store.init()

    def tearDown(self):
        self.temp.cleanup()

    def git_repo(self) -> str:
        git(self.repo, "init", "-q")
        return commit(self.repo)


class MigrationTests(Base):
    def legacy_db(self) -> Path:
        """A real Sprint-0 database: baseline migration only, with data in every table."""
        path = self.root / "legacy.db"
        migrations.apply_pending(path, migrations=migrations.MIGRATIONS[:1])
        con = sqlite3.connect(path)
        con.executescript("""
          INSERT INTO projects VALUES ('p1','Legacy','%s','personal','active','2026-01-01T00:00:00+00:00');
          INSERT INTO tasks VALUES ('t1','p1','Old task','obj','L0','DRAFT','2026-01-02T00:00:00+00:00','2026-11-01',1);
          INSERT INTO snapshots VALUES ('s1','p1','2026-01-03T00:00:00+00:00','git_metadata','{"head": "abc", "source_status": "available"}');
          INSERT INTO events VALUES (1,'e1','project','p1','project.created','owner','{}','2026-01-01T00:00:00+00:00');
        """ % self.repo)
        con.commit()
        con.close()
        return path

    def test_schema_upgrade_preserves_existing_state(self):
        path = self.legacy_db()
        store = Store(path)
        with self.assertRaisesRegex(ZenithError, "zenith db migrate"):
            store.list_projects()  # refuses to run on an un-upgraded schema
        with self.assertRaisesRegex(ZenithError, "zenith db migrate"):
            store.init()           # and init does not upgrade it either
        with self.assertRaisesRegex(ZenithError, "not confirmed"):
            store.migrate(confirm=None)
        store.migrate(confirm=str(path))
        p = store.get_project("p1")
        self.assertEqual((p["name"], p["path"]), ("Legacy", str(self.repo)))
        self.assertEqual(len(p["sources"]), 1)
        self.assertEqual(store.list_tasks()[0]["title"], "Old task")
        self.assertEqual(store.list_tasks()[0]["due_local"], "2026-11-01T23:59:59+03:00")
        con = sqlite3.connect(path)
        self.assertEqual(con.execute("SELECT source_id IS NOT NULL, seen_count FROM snapshots").fetchone(), (1, 1))
        self.assertEqual(con.execute("SELECT count(*) FROM events WHERE event_type='schema.migrated'").fetchone()[0], 1)
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        backups = list(self.root.glob("legacy.db.pre-v*.bak"))
        self.assertEqual(len(backups), 1)
        old = sqlite3.connect(backups[0])  # the backup is the untouched v1 database
        self.assertEqual(migrations.current_version(old), 1)
        self.assertIn("path", [r[1] for r in old.execute("PRAGMA table_info(projects)")])

    def test_init_is_idempotent_and_makes_no_extra_backup(self):
        self.store.init()
        self.assertEqual(list(self.root.glob("data/*.bak")), [])
        self.assertEqual(migrations.current_version(sqlite3.connect(self.store.db_path)), migrations.LATEST_VERSION)

    def test_failed_migration_rolls_back_and_keeps_backup(self):
        path = self.legacy_db()
        real = migrations.MIGRATIONS[1]

        def broken(con):
            real[2](con)
            raise RuntimeError("boom after all DDL ran")

        with self.assertRaisesRegex(migrations.MigrationError, "rolled back.*backup"):
            migrations.apply_pending(path, migrations=[migrations.MIGRATIONS[0], (2, real[1], broken)])
        con = sqlite3.connect(path)
        self.assertEqual(migrations.current_version(con), 1)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("evidence", tables)
        self.assertNotIn("project_sources", tables)
        self.assertEqual(con.execute("SELECT path FROM projects").fetchone()[0], str(self.repo))
        self.assertEqual(len(list(self.root.glob("legacy.db.pre-v2-*.bak"))), 1)
        # A later, healthy upgrade still succeeds from the intact v1 state.
        Store(path).migrate(confirm=str(path))
        self.assertEqual(Store(path).get_project("p1")["name"], "Legacy")

    def test_later_failed_migration_leaves_previous_version(self):
        def bad(con):
            con.execute("CREATE TABLE half_done (x)")
            raise RuntimeError("nope")

        nxt = migrations.LATEST_VERSION + 1
        with self.assertRaises(migrations.MigrationError):
            migrations.apply_pending(self.store.db_path, migrations=[*migrations.MIGRATIONS, (nxt, "bad", bad)])
        con = sqlite3.connect(self.store.db_path)
        self.assertEqual(migrations.current_version(con), migrations.LATEST_VERSION)
        self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='half_done'").fetchone())

    def test_refuses_downgrade(self):
        con = sqlite3.connect(self.store.db_path)
        con.execute("UPDATE schema_meta SET value='99' WHERE key='schema_version'")
        con.commit()
        con.close()
        with self.assertRaisesRegex(ZenithError, "newer than this Zenith"):
            self.store.init()

    def test_backup_restore_roundtrip_with_wal(self):
        p = self.store.add_project("Keep", str(self.repo))
        self.store.checkin(p, "before backup")
        copy = self.root / "backup.db"
        self.store.backup(copy)
        self.assertEqual(oct(copy.stat().st_mode & 0o777), "0o600")
        with self.assertRaises(ZenithError):  # never overwrites
            self.store.backup(copy)
        self.store.checkin(p, "after backup")
        restored = Store(copy)  # restore = open the copy as the database
        self.assertEqual(len(restored.list_evidence(p)), 1)
        self.assertEqual(restored.get_project(p)["name"], "Keep")
        self.assertEqual(len(self.store.list_evidence(p)), 2)
        self.assertEqual(self.store.events(1)[0]["event_type"], "evidence.recorded")
        self.assertEqual(sqlite3.connect(copy).execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_backup_of_missing_dir_or_db_is_clean_error(self):
        with self.assertRaises(ZenithError):
            self.store.backup(self.root / "nope" / "b.db")
        with self.assertRaises(ZenithError):
            Store(self.root / "none.db").backup(self.root / "b.db")


class SourceTests(Base):
    def test_two_paths_one_project(self):
        p = self.store.add_project("Multi", str(self.repo))
        s2 = self.store.add_source(p, str(self.other), label="notes")
        project = self.store.get_project(p)
        self.assertEqual(len(project["sources"]), 2)
        self.assertEqual(project["path"], str(self.repo))  # primary = first added
        self.assertEqual(len(self.store.list_projects()), 1)
        results = self.store.scan_sources(p)
        self.assertEqual({r["source_id"] for r in results}, {x["id"] for x in project["sources"]})
        self.assertIn(s2, {r["source_id"] for r in results})

    def test_no_duplicate_path_claims_across_projects(self):
        a = self.store.add_project("A", str(self.repo))
        b = self.store.add_project("B", str(self.other))
        with self.assertRaises(ZenithError):
            self.store.add_source(b, str(self.repo))
        with self.assertRaises(ZenithError):
            self.store.add_source(a, str(self.repo))
        with self.assertRaises(ZenithError):
            self.store.add_source("missing", str(self.other))

    def test_path_move_keeps_ids_and_history(self):
        p = self.store.add_project("Mover", str(self.repo))
        src = self.store.list_sources(p)[0]["id"]
        t = self.store.add_task(p, "survives move")
        self.store.scan_source(src)
        moved = self.root / "moved"
        self.repo.rename(moved)
        self.assertEqual(self.store.scan_source(src)["source_status"], "unavailable")
        out = self.store.move_source(src, str(moved))
        self.assertEqual(out["project_id"], p)
        self.assertEqual(self.store.get_project(p)["path"], str(moved))
        self.assertEqual(self.store.list_tasks(p)[0]["id"], t)
        self.assertEqual(self.store.scan_source(src)["source_status"], "available")
        self.assertEqual(self.store.events(50)[0]["event_type"], "project.observed")
        self.assertIn("source.moved", [e["event_type"] for e in self.store.events(50)])
        with self.assertRaises(ZenithError):
            self.store.move_source(src, str(moved))

    def test_removed_source_frees_path_and_keeps_history(self):
        p = self.store.add_project("R", str(self.repo))
        src = self.store.list_sources(p)[0]["id"]
        self.store.scan_source(src)
        self.store.remove_source(src)
        self.assertTrue(self.repo.exists())  # directory untouched
        self.assertEqual(self.store.list_sources(p), [])
        self.assertEqual(len(self.store.list_sources(p, include_removed=True)), 1)
        self.store.add_source(p, str(self.repo))  # path can be claimed again
        with self.assertRaises(ZenithError):
            self.store.scan_source(src)
        with self.assertRaises(ZenithError):
            self.store.scan_project(self.store.add_project("Empty", str(self.other)) and "nope")

    def test_path_policy(self):
        for bad in ("/", str(Path.home())):
            with self.assertRaises(ZenithError):
                self.store.add_project("Bad", bad)
        ssh = self.root / ".ssh"
        ssh.mkdir()
        with self.assertRaises(ZenithError):
            self.store.add_project("Bad", str(ssh))
        with self.assertRaises(FileNotFoundError):  # Sprint-0 contract for missing paths
            self.store.add_project("Bad", str(self.root / "missing"))
        (self.root / "afile").write_text("x")
        with self.assertRaises(ZenithError):
            self.store.add_project("Bad", str(self.root / "afile"))
        with mock.patch.dict(os.environ, {"ZENITH_ALLOWED_ROOTS": str(self.root / "elsewhere")}):
            with self.assertRaisesRegex(ZenithError, "ZENITH_ALLOWED_ROOTS"):
                self.store.add_project("Outside", str(self.repo))
        with mock.patch.dict(os.environ, {"ZENITH_ALLOWED_ROOTS": str(self.root)}):
            self.store.add_project("Inside", str(self.repo))


class ScannerTests(Base):
    def setUp(self):
        super().setUp()
        self.project = self.store.add_project("S", str(self.repo))
        self.source = self.store.list_sources(self.project)[0]["id"]

    def test_symlink_escape_is_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "private.txt").write_text("outside data")
        (self.repo / "ok.txt").write_text("ok")
        os.symlink(outside, self.repo / "link_dir")
        os.symlink(outside / "private.txt", self.repo / "link_file")
        first = self.store.scan_source(self.source)
        self.assertEqual(first["file_count"], 1)
        self.assertEqual(first["skipped"]["symlinks"], 2)
        self.assertEqual(first["skipped"]["symlinks_escaping_root"], 2)
        (outside / "private.txt").write_text("changed, and much longer than before")
        (outside / "new.txt").write_text("n")
        again = self.store.scan_source(self.source)
        self.assertTrue(again["unchanged"])  # nothing outside the root influences the scan
        self.assertEqual(first["fingerprint"], again["fingerprint"])

    def test_source_swapped_for_symlink_is_unavailable(self):
        target = self.root / "target"
        target.mkdir()
        (target / "x").write_text("x")
        self.repo.rename(self.root / "repo.bak")
        os.symlink(target, self.repo)
        out = self.store.scan_source(self.source)
        self.assertEqual((out["source_status"], out["reason"]), ("unavailable", "path_resolves_elsewhere"))
        self.assertNotIn("file_count", out)

    def test_secrets_are_excluded_and_never_opened(self):
        (self.repo / "code.py").write_text("print(1)")
        secrets = [".env", ".env.production", "id_rsa", "id_ed25519.pub", "server.pem", "api.key",
                   "credentials.json", "my_secret_notes.txt", ".netrc", "store.kdbx"]
        for name in secrets:
            f = self.repo / name
            f.write_text("TOP-SECRET-VALUE")
            f.chmod(0)  # any attempt to open the contents would raise
        (self.repo / ".ssh").mkdir()
        (self.repo / ".ssh" / "config").write_text("TOP-SECRET-VALUE")
        try:
            out = self.store.scan_source(self.source)
        finally:
            for name in secrets:
                (self.repo / name).chmod(0o600)
        self.assertEqual(out["file_count"], 1)
        self.assertEqual(out["skipped"]["sensitive"], len(secrets) + 1)
        self.assertEqual(out["skipped"]["unreadable"], 0)
        blob = json.dumps(out) + json.dumps(self.store.list_evidence(self.project))
        con = sqlite3.connect(self.store.db_path)
        blob += json.dumps(con.execute("SELECT details FROM snapshots").fetchall())
        blob += json.dumps(con.execute("SELECT payload FROM events").fetchall())
        for needle in ("TOP-SECRET-VALUE", ".env", "id_rsa", "kdbx"):
            self.assertNotIn(needle, blob)

    def test_scan_is_bounded(self):
        for i in range(30):
            (self.repo / f"f{i}.txt").write_text("x")
        from zenith import scanner
        out = scanner.scan_tree(self.repo, max_files=10)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["file_count"], 10)
        ticks = iter(range(0, 1000, 20))
        out = scanner.scan_tree(self.repo, max_seconds=30, clock=lambda: next(ticks))
        self.assertTrue(out["truncated"])
        full = scanner.scan_tree(self.repo)
        self.assertFalse(full["truncated"])
        self.assertEqual(full["fingerprint"], scanner.scan_tree(self.repo)["fingerprint"])  # deterministic

    def test_scan_does_not_write_to_the_project(self):
        self.git_repo()
        (self.repo / "dirty.txt").write_text("d")
        before = sorted(str(p.relative_to(self.repo)) for p in self.repo.rglob("*"))
        index_before = (self.repo / ".git" / "index").read_bytes()
        self.store.scan_source(self.source)
        self.assertEqual(before, sorted(str(p.relative_to(self.repo)) for p in self.repo.rglob("*")))
        self.assertEqual(index_before, (self.repo / ".git" / "index").read_bytes())

    def test_repo_config_cannot_run_commands_during_scan(self):
        self.git_repo()
        marker = self.root / "pwned"
        git(self.repo, "config", "core.fsmonitor", f"touch {marker}; echo")
        out = self.store.scan_source(self.source)
        self.assertTrue(out["is_git_repo"])
        self.assertFalse(marker.exists())

    def test_non_git_path_is_supported(self):
        (self.repo / "a.txt").write_text("a")
        out = self.store.scan_source(self.source)
        self.assertEqual((out["source_status"], out["is_git_repo"], out["file_count"]), ("available", False, 1))

    def test_duplicate_scans_do_not_duplicate_snapshots(self):
        self.git_repo()
        a = self.store.scan_source(self.source)
        b = self.store.scan_source(self.source)
        c = self.store.scan_source(self.source)
        self.assertFalse(a["unchanged"])
        self.assertTrue(b["unchanged"] and c["unchanged"])
        self.assertEqual(a["snapshot_id"], c["snapshot_id"])
        con = sqlite3.connect(self.store.db_path)
        self.assertEqual(con.execute("SELECT count(*), max(seen_count) FROM snapshots").fetchone(), (1, 3))
        self.assertEqual(len(self.store.list_evidence(self.project, "REPOSITORY_OBSERVATION")), 1)
        self.assertEqual(len([e for e in self.store.events(100) if e["event_type"] == "project.observed"]), 3)

    def test_compare_shows_actual_revision_change(self):
        first = self.git_repo()
        self.assertEqual(self.store.compare_snapshots(self.source)["status"], "insufficient_history")
        self.store.scan_source(self.source)
        self.assertEqual(self.store.compare_snapshots(self.source)["status"], "insufficient_history")
        commit(self.repo, "g.txt")
        second = commit(self.repo, "h.txt")
        self.store.scan_source(self.source)
        diff = self.store.compare_snapshots(self.source)
        self.assertEqual(diff["status"], "changed")
        self.assertEqual(diff["changes"]["head"]["from"], first)
        self.assertEqual(diff["changes"]["head"]["to"], second)
        self.assertEqual(diff["changes"]["head"]["commits_between"], 2)
        self.assertIn("not proof of completion", " ".join(diff["notes"]))

    def test_compare_uncommitted_and_missing_source(self):
        self.git_repo()
        self.store.scan_source(self.source)
        (self.repo / "wip.txt").write_text("wip")
        self.store.scan_source(self.source)
        self.assertIn("without a new commit", " ".join(self.store.compare_snapshots(self.source)["notes"]))
        moved = self.root / "gone"
        self.repo.rename(moved)
        self.store.scan_source(self.source)
        d = self.store.compare_snapshots(self.source)
        self.assertEqual(d["changes"]["source_status"]["to"], "unavailable")
        self.assertIn("unknown", " ".join(d["notes"]))


class EvidenceTests(Base):
    def setUp(self):
        super().setUp()
        self.project = self.store.add_project("E", str(self.repo))
        self.source = self.store.list_sources(self.project)[0]["id"]

    def test_manual_evidence_cannot_claim_observation_or_verification(self):
        for origin in ("TEST_VERIFICATION", "REPOSITORY_OBSERVATION", "bogus"):
            with self.assertRaises(ZenithError):
                self.store.add_evidence(self.project, origin, "tests pass, trust me")
        r = self.store.add_evidence(self.project, "MODEL_INFERENCE", "probably done")
        self.assertEqual(r["confidence"], "INFERRED")
        r = self.store.add_evidence(self.project, "DOCUMENT", "design doc says X", source_ref="doc:design-v1")
        self.assertEqual(r["confidence"], "REPORTED")

    def test_database_enforces_confidence_ceiling(self):
        con = sqlite3.connect(self.store.db_path)
        row = ("e1", self.project, "HUMAN_REPORT", "VERIFIED", "s", "2026-01-01T00:00:00+00:00",
               "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", 1)
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("INSERT INTO evidence(id,project_id,origin,confidence,summary,observed_at,"
                        "last_confirmed_at,recorded_at,seq) VALUES (?,?,?,?,?,?,?,?,?)", row)

    def test_evidence_is_append_only(self):
        e = self.store.add_evidence(self.project, "HUMAN_REPORT", "note")["evidence_id"]
        con = sqlite3.connect(self.store.db_path)
        for sql in ("UPDATE evidence SET summary='rewritten'", "UPDATE evidence SET confidence='UNKNOWN'",
                    "DELETE FROM evidence"):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                con.execute(sql)
        con.execute("UPDATE evidence SET last_confirmed_at='2030-01-01T00:00:00+00:00' WHERE id=?", (e,))

    def test_verification_is_gated_and_consistent(self):
        head = self.git_repo()
        with self.assertRaises(ZenithError):
            self.store.record_test_verification(self.project, self.source, "unit", 0, head, actor="owner")
        with self.assertRaises(ZenithError):
            self.store.record_test_verification(self.project, self.source, "unit", 0, "not-a-hash")
        other = self.store.add_project("O", str(self.other))
        with self.assertRaises(ZenithError):
            self.store.record_test_verification(other, self.source, "unit", 0, head)
        eid = self.store.record_test_verification(self.project, self.source, "unit", 0, head)
        ev = self.store.list_evidence(self.project, "TEST_VERIFICATION")[0]
        self.assertEqual((ev["id"], ev["confidence"], ev["details"]["passed"]), (eid, "VERIFIED", True))

    def test_checkin_is_reported_never_verified(self):
        r = self.store.checkin(self.project, "Finished the parser; Bearer-less token=sk-abcdefghijklmnopqrstuv", node_id="laptop2")
        self.assertEqual((r["origin"], r["confidence"]), ("HUMAN_REPORT", "REPORTED"))
        self.assertGreaterEqual(r["redactions"], 1)
        ev = self.store.list_evidence(self.project)[0]
        self.assertNotIn("abcdefghijklmnopqrstuv", ev["summary"])
        self.assertEqual(ev["node_id"], "laptop2")
        with self.assertRaises(ZenithError):
            self.store.checkin(self.project, "  ")
        with self.assertRaises(ZenithError):
            self.store.checkin(self.project, "x" * 1001)
        with self.assertRaises(ZenithError):
            self.store.checkin(self.project, "ok", node_id="bad node!")
        with self.assertRaises(ZenithError):
            self.store.checkin(self.project, "ok", observed_at="2999-01-01")
        with self.assertRaises(ZenithError):
            self.store.checkin(self.project, "ok", observed_at="garbage")
        with self.assertRaises(ZenithError):
            self.store.checkin("missing-project", "ok")

    def test_backdated_offline_checkin_is_reconciled_against_later_evidence(self):
        self.git_repo()
        self.store.scan_source(self.source)
        r = self.store.checkin(self.project, "worked offline yesterday", node_id="laptop2",
                               observed_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
        self.assertEqual(len(r["later_evidence"]), 1)
        self.assertIn("possibly outdated", r["reconciliation"])
        fresh = self.store.checkin(self.project, "just now")
        self.assertEqual(fresh["later_evidence"], [])
        self.assertIn("unverified", fresh["reconciliation"])

    def test_every_write_emits_an_audit_event(self):
        before = len(self.store.events(500))
        self.store.checkin(self.project, "n")
        t = self.store.add_task(self.project, "t")
        t2 = self.store.add_task(self.project, "t2")
        self.store.add_dependency(t2, t)
        self.store.scan_source(self.source)
        kinds = [e["event_type"] for e in self.store.events(500)][: len(self.store.events(500)) - before]
        for expected in ("evidence.recorded", "task.created", "task.dependency_added", "project.observed"):
            self.assertIn(expected, kinds)


class TaskTests(Base):
    def setUp(self):
        super().setUp()
        self.project = self.store.add_project("T", str(self.repo))

    def test_deadline_normalisation_istanbul(self):
        add = lambda due: self.store.list_tasks()[0] if self.store.add_task(self.project, "x", due_at=due) else None
        self.assertEqual(add("2026-11-01")["due_at"], "2026-11-01T20:59:59+00:00")
        self.assertEqual(add("2026-11-01")["due_local"], "2026-11-01T23:59:59+03:00")
        self.assertEqual(add("2026-11-01T09:00")["due_at"], "2026-11-01T06:00:00+00:00")
        self.assertEqual(add("2026-11-01T09:00:00Z")["due_local"], "2026-11-01T12:00:00+03:00")
        self.assertEqual(add("2026-11-01T09:00:00+01:00")["due_at"], "2026-11-01T08:00:00+00:00")
        self.assertEqual(dates.to_local(dates.parse_due("2026-07-01")), "2026-07-01T23:59:59+03:00")  # no DST
        with self.assertRaises(ZenithError):
            self.store.add_task(self.project, "x", due_at="2026-13-45")

    def test_listing_filters_are_deterministic(self):
        now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        late = self.store.add_task(self.project, "late", due_at="2026-10-20")
        soon = self.store.add_task(self.project, "soon", due_at="2026-10-25")
        far = self.store.add_task(self.project, "far", due_at="2027-01-01")
        none = self.store.add_task(self.project, "none")
        ids = lambda **kw: [t["id"] for t in self.store.list_tasks(now=now, **kw)]
        self.assertEqual(ids(overdue=True), [late])
        self.assertEqual(ids(due_within_days=7), [late, soon])
        self.assertEqual(ids(due_within_days=0), [late])
        self.assertEqual(set(ids(open_only=True)), {late, soon, far, none})
        self.assertEqual(ids(state="CANCELLED"), [])
        self.store.transition(none, "CANCELLED")
        self.assertEqual(set(ids(open_only=True)), {late, soon, far})
        self.assertEqual(ids(state="CANCELLED"), [none])
        with self.assertRaises(ZenithError):
            self.store.list_tasks(state="NOPE")
        self.assertEqual(ids(project_id=self.store.add_project("Other", str(self.other))), [])

    def test_dependencies_block_queueing_and_reject_cycles(self):
        a = self.store.add_task(self.project, "A")
        b = self.store.add_task(self.project, "B")
        c = self.store.add_task(self.project, "C")
        self.store.add_dependency(b, a)
        self.store.add_dependency(c, b)
        for bad in ((a, a), (a, c), (b, a)):
            with self.assertRaises(ZenithError):
                self.store.add_dependency(*bad)
        with self.assertRaises(ZenithError):
            self.store.add_dependency(a, "missing")
        self.assertEqual({t["id"] for t in self.store.list_tasks(blocked=True)}, {b, c})
        for state in ("CONTEXT_READY", "PLANNED"):
            self.store.transition(b, state)
        with self.assertRaisesRegex(ZenithError, "blocked by unfinished dependencies"):
            self.store.transition(b, "QUEUED")
        blocked = {t["id"]: t["blocked_by"] for t in self.store.list_tasks(blocked=True)}
        self.assertEqual(blocked, {b: [a], c: [b]})
        self.store.remove_dependency(b, a)
        self.assertEqual(self.store.transition(b, "QUEUED"), "QUEUED")
        with self.assertRaises(ZenithError):
            self.store.remove_dependency(b, a)

    def test_completed_dependency_unblocks(self):
        a = self.store.add_task(self.project, "A")
        b = self.store.add_task(self.project, "B")
        self.store.add_dependency(b, a)
        with sqlite3.connect(self.store.db_path) as con:  # COMPLETED is worker-only; simulate its outcome
            con.execute("UPDATE tasks SET state='COMPLETED' WHERE id=?", (a,))
        self.assertEqual(self.store.list_tasks(blocked=True), [])
        for state in ("CONTEXT_READY", "PLANNED", "QUEUED"):
            self.store.transition(b, state)


class BriefTests(Base):
    def setUp(self):
        super().setUp()
        self.project = self.store.add_project("B", str(self.repo))
        self.source = self.store.list_sources(self.project)[0]["id"]

    def status(self, now=None):
        return self.store.brief(self.project, now=now)["projects"][0]

    def test_empty_project_is_unknown_not_invented(self):
        b = self.status()
        self.assertEqual(b["status"]["label"], "UNKNOWN")
        self.assertEqual(b["sources"][0]["state"], "never_scanned")
        text = " ".join(b["unknowns"])
        self.assertIn("never been scanned", text)
        self.assertIn("completion is unverified", text)
        self.assertIsNone(b["latest_verification"])

    def test_stale_human_progress_never_overrides_verified_evidence(self):
        head = self.git_repo()
        self.store.scan_source(self.source)
        self.store.record_test_verification(self.project, self.source, "unit", 0, head)
        self.assertEqual(self.status()["status"]["label"], "VERIFIED")
        # A confident human claim after verification must not change the status or become verified.
        self.store.checkin(self.project, "EVERYTHING IS DONE AND TESTED, ship it")
        b = self.status()
        self.assertEqual(b["status"]["label"], "VERIFIED")
        self.assertEqual(b["latest_human_report"]["confidence"], "REPORTED")
        self.assertFalse(b["latest_human_report"]["verified"])
        # New commit: the earlier verification no longer covers the current revision.
        commit(self.repo, "new.txt")
        self.store.scan_source(self.source)
        b = self.status()
        self.assertEqual(b["status"]["label"], "VERIFICATION_STALE")
        self.assertTrue(b["latest_human_report"]["newer_evidence"])
        self.assertTrue(any("re-verify" in a["action"] for a in b["next_actions"]))

    def test_human_report_alone_is_reported_only(self):
        self.store.checkin(self.project, "done!")
        b = self.status()
        self.assertEqual(b["status"]["label"], "REPORTED_ONLY")
        self.assertTrue(any("unverified" in a["action"] for a in b["next_actions"]))

    def test_commits_alone_are_observed_not_verified(self):
        self.git_repo()
        self.store.scan_source(self.source)
        self.assertEqual(self.status()["status"]["label"], "OBSERVED")

    def test_failing_and_dirty_verification(self):
        head = self.git_repo()
        self.store.scan_source(self.source)
        self.store.record_test_verification(self.project, self.source, "unit", 1, head)
        self.assertEqual(self.status()["status"]["label"], "VERIFICATION_FAILING")
        self.store.record_test_verification(self.project, self.source, "unit", 0, head)
        self.assertEqual(self.status()["status"]["label"], "VERIFIED")
        (self.repo / "f.txt").write_text("edited after verification")
        self.store.scan_source(self.source)
        self.assertEqual(self.status()["status"]["label"], "VERIFICATION_STALE")

    def test_staleness_and_missing_sources_are_reported_as_unknown(self):
        self.git_repo()
        self.store.scan_source(self.source)
        self.store.checkin(self.project, "update")
        later = datetime.now(timezone.utc) + timedelta(days=30)
        b = self.status(later)
        self.assertEqual(b["sources"][0]["state"], "stale")
        self.assertTrue(b["latest_human_report"]["stale"])
        actions = " ".join(a["action"] for a in b["next_actions"])
        self.assertIn("Scan source", actions)
        self.assertIn("No recent check-in", actions)
        self.repo.rename(self.root / "vanished")
        self.store.scan_source(self.source)
        b = self.status()
        self.assertEqual(b["sources"][0]["state"], "unavailable")
        self.assertIn("current state unknown", " ".join(b["unknowns"]))
        self.assertNotEqual(b["status"]["label"], "VERIFIED")

    def test_citations_point_at_real_records(self):
        self.git_repo()
        self.store.scan_source(self.source)
        self.store.checkin(self.project, "wip")
        t = self.store.add_task(self.project, "Due soon", due_at=(datetime.now() + timedelta(days=2)).date().isoformat())
        b = self.status()
        known = {e["id"] for e in self.store.list_evidence(self.project)} | {t}
        cited = {c for a in b["next_actions"] for c in a["citations"]} | set(b["status"]["basis"])
        self.assertTrue(cited and cited <= known)

    def test_brief_is_read_only_and_text_renders(self):
        from zenith.brief import render_text
        self.git_repo()
        self.store.scan_source(self.source)
        con = sqlite3.connect(self.store.db_path)
        counts = lambda: [con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                          for t in ("events", "evidence", "snapshots", "tasks")]
        before = counts()
        text = render_text(self.store.brief())
        self.assertEqual(before, counts())
        self.assertIn("status: OBSERVED", text)
        with self.assertRaises(ZenithError):
            self.store.brief("missing")


class CliTests(unittest.TestCase):
    def test_cli_from_empty_database_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp).resolve()
            repo = tmp / "repo"
            repo.mkdir()
            git(repo, "init", "-q")
            commit(repo)
            env = {**os.environ, "ZENITH_DB_PATH": str(tmp / "fresh" / "zenith.db"), "PYTHONPATH": SRC}

            def z(*args, ok=True):
                r = subprocess.run([sys.executable, "-m", "zenith", *args], env=env, capture_output=True, text=True)
                self.assertEqual(r.returncode == 0, ok, r.stderr)
                return json.loads(r.stdout) if ok and r.stdout.lstrip().startswith(("{", "[")) else r

            z("project", "list", ok=False)  # not initialised yet: clean error, no traceback
            self.assertEqual(z("init")["initialized"], True)
            self.assertEqual(z("doctor")["schema_version"], migrations.LATEST_VERSION)
            pid = z("project", "add", "--name", "Demo", "--path", str(repo))["project_id"]
            other = tmp / "docs"
            other.mkdir()
            sid = z("project", "sources", "add", "--project", pid, "--path", str(other), "--label", "docs")["source_id"]
            self.assertEqual(len(z("project", "sources", "list", "--project", pid)), 2)
            self.assertEqual(len(z("scan", "--project", pid, "--all")), 2)
            self.assertEqual(z("scan", "--project", pid, "--source", sid)["unchanged"], True)
            self.assertEqual(z("scan", "--project", pid)["is_git_repo"], True)
            self.assertEqual(z("project", "sources", "diff", "--source", sid)["status"], "insufficient_history")
            ci = z("checkin", "--project", pid, "--note", "ported the parser", "--node", "laptop2")
            self.assertEqual(ci["confidence"], "REPORTED")
            z("evidence", "add", "--project", pid, "--origin", "TEST_VERIFICATION", "--summary", "x", ok=False)
            z("evidence", "add", "--project", pid, "--origin", "MODEL_INFERENCE", "--summary", "likely half done")
            self.assertEqual(len(z("evidence", "list", "--project", pid, "--origin", "HUMAN_REPORT")), 1)
            t1 = z("task", "add", "--project", pid, "--title", "A", "--due", "2020-01-01")["task_id"]
            t2 = z("task", "add", "--project", pid, "--title", "B")["task_id"]
            z("task", "depend", "--id", t2, "--on", t1)
            self.assertEqual([t["id"] for t in z("task", "list", "--blocked")], [t2])
            self.assertEqual([t["id"] for t in z("task", "list", "--overdue")], [t1])
            brief = z("brief")
            self.assertEqual(brief["projects"][0]["status"]["label"], "OBSERVED")
            text = subprocess.run([sys.executable, "-m", "zenith", "brief", "--format", "text"], env=env,
                                  capture_output=True, text=True)
            self.assertIn("status: OBSERVED", text.stdout)
            z("backup", "--out", str(tmp / "b.db"))
            z("backup", "--out", str(tmp / "b.db"), ok=False)
            self.assertEqual(oct((tmp / "fresh" / "zenith.db").stat().st_mode & 0o777), "0o600")


class PolicyUnitTests(unittest.TestCase):
    def test_sensitive_names(self):
        for name in (".env", ".ENV.local", "id_rsa", "id_rsa.pub", "a.pem", "prod.key", "Credentials",
                     "aws_credentials", "client_secret.json"):
            self.assertIsNotNone(policy.sensitive_reason(name), name)
        for name in ("README.md", "main.py", "environment.md", "keyboard.py", "monkey.txt"):
            self.assertIsNone(policy.sensitive_reason(name), name)
        self.assertIsNotNone(policy.sensitive_reason(".ssh", is_dir=True))
        self.assertIsNone(policy.sensitive_reason("src", is_dir=True))

    def test_redaction(self):
        text, n = policy.redact("password: hunter2 and ghp_" + "a" * 30 + " AKIAABCDEFGHIJKLMNOP plain words")
        self.assertEqual(n, 3)
        self.assertNotIn("hunter2", text)
        self.assertIn("plain words", text)
        self.assertEqual(policy.redact("nothing secret here"), ("nothing secret here", 0))


if __name__ == "__main__":
    unittest.main()
