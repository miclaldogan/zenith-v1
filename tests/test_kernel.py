import json
import tempfile
import unittest
from pathlib import Path
from subprocess import run

from zenith.kernel import Store, ZenithError


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.project_dir = root / "myrepo"
        self.project_dir.mkdir()
        self.store = Store(root / "zenith.db")
        self.store.init()

    def tearDown(self):
        self.temp.cleanup()

    def test_project_persist_and_no_duplicate(self):
        p = self.store.add_project("Test", str(self.project_dir))
        self.assertEqual(self.store.get_project(p)["name"], "Test")
        self.assertEqual(Store(self.store.db_path).list_projects()[0]["id"], p)
        with self.assertRaises(ZenithError):
            self.store.add_project("Duplicate", str(self.project_dir))

    def test_task_state_requires_consent(self):
        p = self.store.add_project("Test", str(self.project_dir))
        t = self.store.add_task(p, "A safe task", risk="L2")
        with self.assertRaises(ZenithError):
            self.store.transition(t, "COMPLETED")
        self.store.transition(t, "CONTEXT_READY")
        self.store.transition(t, "PLANNED")
        with self.assertRaises(ZenithError):
            self.store.transition(t, "QUEUED")
        self.store.transition(t, "AWAITING_APPROVAL")
        self.store.approve(t)
        self.assertEqual(self.store.transition(t, "QUEUED"), "QUEUED")
        with self.assertRaises(ZenithError):
            self.store.transition(t, "RUNNING")
        self.assertEqual(self.store.list_tasks()[0]["state"], "QUEUED")

    def test_approval_validation(self):
        p = self.store.add_project("Test", str(self.project_dir))
        t = self.store.add_task(p, "Task", risk="L1")
        with self.assertRaises(ZenithError):
            self.store.approve(t)
        self.store.transition(t, "CONTEXT_READY")
        self.store.transition(t, "PLANNED")
        self.store.transition(t, "AWAITING_APPROVAL")
        with self.assertRaises(ZenithError):
            self.store.approve(t, minutes=9999)
        with self.assertRaises(ZenithError):
            self.store.approve(t, actor="random")
        self.assertIsInstance(self.store.approve(t), str)

    def test_git_scan_is_metadata_only(self):
        run(["git", "init", "-q", str(self.project_dir)], check=True)
        (self.project_dir / ".env").write_text("SECRET_DO_NOT_READ=hello\n")
        p = self.store.add_project("Test", str(self.project_dir))
        obs = self.store.scan_project(p)
        self.assertEqual(obs["source_status"], "available")
        self.assertTrue(obs["is_git_repo"])
        self.assertNotIn("SECRET_DO_NOT_READ", json.dumps(obs))
        ev = self.store.events()
        self.assertEqual(ev[0]["event_type"], "project.observed")
        self.assertGreaterEqual(len(ev), 2)

    def test_task_validation(self):
        p = self.store.add_project("Test", str(self.project_dir))
        with self.assertRaises(ZenithError):
            self.store.add_task(p, " ")
        with self.assertRaises(ZenithError):
            self.store.add_task(p, "Time", due_at="not-a-date")

    def test_missing_path_rejected(self):
        with self.assertRaises(FileNotFoundError):
            self.store.add_project("Bad", str(self.project_dir / "missing"))


if __name__ == "__main__":
    unittest.main()
