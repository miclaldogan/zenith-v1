"""Database safety gate: nothing migrates an existing database implicitly.

Regression tests for the 2026-10-23 incident in which the owner's live v1 database was upgraded
to v6 by an ordinary `zenith init`. Covered: a missing ZENITH_DB_PATH, a ZENITH_DB_PATH that points
at a real older database, implicit `zenith init`, every other command, read-only inspection,
fail-closed rehearsal targets, and the explicit confirmation required for a real migration.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zenith import migrations
from zenith.kernel import MigrationRequired, Store, ZenithError, default_db_path

SRC = str(Path(__file__).resolve().parents[1] / "src")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_v1(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    migrations.apply_pending(path, migrations=migrations.MIGRATIONS[:1], backup=False)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO projects VALUES ('p1','Owner project','/srv/x','personal','active','2026-01-01T00:00:00+00:00')")
    con.commit()
    con.close()


class Base(unittest.TestCase):
    """A fake HOME whose default data directory holds an older (v1) database, like the owner's."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.data = self.home / ".local" / "share"
        self.live = self.data / "zenith" / "zenith.db"
        make_v1(self.live)
        self.before = sha256(self.live)
        # No ZENITH_DB_PATH: the default path resolves inside the fake home.
        self.env = {k: v for k, v in os.environ.items() if k not in ("ZENITH_DB_PATH", "XDG_DATA_HOME", "ZENITH_STATE_DIR")}
        self.env.update({"HOME": str(self.home), "XDG_DATA_HOME": str(self.data), "PYTHONPATH": SRC,
                         "ZENITH_STATE_DIR": str(self.root / "state")})
        self.patch = mock.patch.dict(os.environ, self.env, clear=True)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def cli(self, *args, env=None, stdin=""):
        return subprocess.run([sys.executable, "-m", "zenith", *args], env=env or self.env,
                              capture_output=True, text=True, input=stdin, timeout=60)

    def assert_untouched(self):
        self.assertEqual(sha256(self.live), self.before, "live database bytes changed")
        self.assertEqual(migrations.current_version(sqlite3.connect(f"file:{self.live}?mode=ro", uri=True)), 1)
        self.assertEqual([p.name for p in self.live.parent.glob("*.bak")], [], "a backup means a migration ran")


class ImplicitMigrationTests(Base):
    def test_default_path_resolves_to_the_fake_live_db(self):
        self.assertEqual(default_db_path(), self.live)

    def test_init_without_env_refuses_and_changes_nothing(self):
        r = self.cli("init")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("Nothing was changed", r.stderr)
        self.assertIn("zenith db migrate", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assert_untouched()

    def test_store_init_in_process_raises_migration_required(self):
        with self.assertRaises(MigrationRequired) as cm:
            Store().init()
        self.assertEqual((cm.exception.found, cm.exception.needed), (1, migrations.LATEST_VERSION))
        with self.assertRaises(MigrationRequired):
            Store().list_projects()
        self.assert_untouched()

    def test_env_pointing_at_an_older_real_db_is_not_an_authorization(self):
        env = {**self.env, "ZENITH_DB_PATH": str(self.live)}
        self.assertEqual(self.cli("init", env=env).returncode, 2)
        self.assertEqual(self.cli("project", "list", env=env).returncode, 2)
        self.assert_untouched()

    def test_every_other_command_refuses_without_migrating(self):
        for args in (("project", "list"), ("misa", "--once", "selam"), ("brief",), ("task", "list"), ("events",),
                     ("project", "add", "--name", "x", "--path", str(self.root)), ("scan", "--project", "p1")):
            r = self.cli(*args)
            self.assertNotIn("Traceback", r.stderr, args)
            self.assertEqual(r.returncode, 2, args)
            self.assertIn("zenith db migrate", r.stderr, args)
        r = self.cli(stdin="selam\nçık\n")                    # interactive Misa session on an old DB
        self.assertEqual(r.returncode, 2)
        self.assertIn("zenith db migrate", r.stderr)
        # A plain backup of an older database is a read-only copy: allowed, and the source stays v1.
        r = self.cli("backup", "--out", str(self.root / "b.db"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(migrations.current_version(sqlite3.connect(f"file:{self.root / 'b.db'}?mode=ro", uri=True)), 1)
        self.assert_untouched()

    def test_doctor_and_status_are_read_only(self):
        doc = json.loads(self.cli("doctor").stdout)
        self.assertEqual((doc["schema_version"], doc["schema_expected"]), (1, migrations.LATEST_VERSION))
        self.assertIn("nothing migrates implicitly", doc["schema_action"])
        st = json.loads(self.cli("db", "status").stdout)
        self.assertEqual(st["schema_version"], 1)
        self.assertEqual(st["integrity"], "ok")
        self.assertEqual(len(st["pending_migrations"]), migrations.LATEST_VERSION - 1)
        self.assertEqual(st["backups"], [])
        self.assertIn("--confirm-migrate", st["action"])
        self.assert_untouched()


class RehearsalTests(Base):
    def test_rehearsal_fails_closed_on_live_targets(self):
        store = Store()
        for bad in (self.live, self.live.parent / "copy.db", self.live.parent / "sub" / "copy.db",
                    self.data / "zenith" / "rehearsal.db"):
            with self.assertRaises(ZenithError, msg=str(bad)):
                store.rehearse(bad)
        with self.assertRaises(ZenithError):
            store.rehearse(self.root / "missing-dir" / "copy.db")
        self.assertEqual(self.cli("db", "rehearse", "--copy-to", str(self.live)).returncode, 2)
        self.assert_untouched()
        self.assertFalse((self.data / "zenith" / "rehearsal.db").exists())

    def test_rehearsal_on_a_scratch_copy_leaves_the_source_untouched(self):
        scratch = self.root / "scratch"
        scratch.mkdir()
        out = json.loads(self.cli("db", "rehearse", "--copy-to", str(scratch / "rehearsal.db")).stdout)
        self.assertEqual((out["from"], out["to"], out["copy_integrity"]), (1, migrations.LATEST_VERSION, "ok"))
        self.assertTrue(out["source_sha256_unchanged"])
        copy = sqlite3.connect(f"file:{scratch / 'rehearsal.db'}?mode=ro", uri=True)
        self.assertEqual(migrations.current_version(copy), migrations.LATEST_VERSION)
        self.assertEqual(copy.execute("SELECT name FROM projects").fetchone()[0], "Owner project")
        with self.assertRaises(ZenithError):                  # a second rehearsal never overwrites the copy
            Store().rehearse(scratch / "rehearsal.db")
        self.assert_untouched()


class AuthorizedMigrationTests(Base):
    def test_migrate_requires_explicit_target_and_verbatim_confirmation(self):
        r = self.cli("db", "migrate", "--db", str(self.live))                      # no confirmation
        self.assertNotEqual(r.returncode, 0)
        r = self.cli("db", "migrate", "--db", str(self.live), "--confirm-migrate", "yes")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not confirmed", r.stderr)
        r = self.cli("db", "migrate", "--db", str(self.live), "--confirm-migrate", str(self.live) + "x")
        self.assertEqual(r.returncode, 2)
        with self.assertRaises(ZenithError):
            Store(self.live).migrate(confirm=None)
        with self.assertRaises(ZenithError):
            Store(self.live).migrate(confirm=str(self.root / "other.db"))
        self.assert_untouched()

    def test_confirmed_migration_backs_up_then_upgrades(self):
        out = json.loads(self.cli("db", "migrate", "--db", str(self.live), "--confirm-migrate", str(self.live)).stdout)
        self.assertEqual((out["from"], out["to"]), (1, migrations.LATEST_VERSION))
        backups = list(self.live.parent.glob("zenith.db.pre-v*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(sha256(backups[0]) == self.before or
                         migrations.current_version(sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True)) == 1, True)
        store = Store()
        self.assertEqual(store.list_projects()[0]["name"], "Owner project")
        self.assertEqual([e["event_type"] for e in store.events(5) if e["event_type"] == "schema.migrated"], ["schema.migrated"])
        # Idempotent afterwards; init is a no-op and makes no second backup.
        self.assertEqual(json.loads(self.cli("init").stdout)["initialized"], False)
        self.assertEqual(Store().migrate(confirm=str(self.live))["note"], "already at the current schema; nothing done")
        self.assertEqual(len(list(self.live.parent.glob("*.bak"))), 1)


class FirstTimeInitTests(unittest.TestCase):
    def test_fresh_default_path_still_initializes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            env = {k: v for k, v in os.environ.items() if k not in ("ZENITH_DB_PATH", "XDG_DATA_HOME")}
            env.update({"HOME": str(home), "XDG_DATA_HOME": str(home / ".local" / "share"), "PYTHONPATH": SRC,
                        "ZENITH_STATE_DIR": str(Path(tmp) / "state")})
            r = subprocess.run([sys.executable, "-m", "zenith", "init"], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            db = home / ".local" / "share" / "zenith" / "zenith.db"
            self.assertEqual((out["db_path"], out["initialized"]), (str(db), True))
            self.assertEqual(oct(db.stat().st_mode & 0o777), "0o600")
            self.assertEqual(migrations.current_version(sqlite3.connect(f"file:{db}?mode=ro", uri=True)), migrations.LATEST_VERSION)
            self.assertEqual(list(db.parent.glob("*.bak")), [])
            r = subprocess.run([sys.executable, "-m", "zenith", "project", "list"], env=env, capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "[]"))
            r = subprocess.run([sys.executable, "-m", "zenith", "misa", "--once", "yardım"], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
