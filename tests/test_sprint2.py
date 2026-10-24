"""Sprint 2: director protocol, approvals v2, sandbox, worktrees, verifier, durable orchestration."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from zenith import contracts, extensions, migrations, sandbox, worktree
from zenith.agents.base import AdapterError
from zenith.agents.fake import FakeDirector, FakeImplementer
from zenith.contracts import ContractError
from zenith.demo import BUGGY, FIXED, make_repo
from zenith.kernel import Store, ZenithError
from zenith.orchestration import Orchestrator

SRC = str(Path(__file__).resolve().parents[1] / "src")
CAPS = sandbox.detect()
needs_sandbox = unittest.skipUnless(CAPS.ok, "bubblewrap isolation baseline unavailable: " + "; ".join(CAPS.reasons))

ACCEPT = [{"id": "unit", "argv": ["python", "-m", "unittest", "discover", "-s", "tests"]}]
SCOPE = {"allowed_files": ["src/**"], "excluded_files": ["tests/**"], "acceptance": ACCEPT}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        make_repo(self.repo)
        self.state = self.root / "state"
        self.store = Store(self.root / "zenith.db")
        self.store.init()
        self.project = self.store.add_project("P", str(self.repo))
        self.source = self.store.list_sources(self.project)[0]["id"]

    def tearDown(self):
        self.temp.cleanup()

    def orch(self, director=None, implementer=None, **kw) -> Orchestrator:
        return Orchestrator(self.store, FakeDirector(director), FakeImplementer(implementer), caps=CAPS,
                            state_root=self.state, **kw)

    def task(self, scope=None, risk="L2") -> str:
        return self.store.add_task(self.project, "Fix add()", "add returns wrong result", risk=risk, scope=scope or SCOPE)

    def ready(self, orch: Orchestrator, task: str, minutes: int = 30) -> str:
        """plan -> approve -> queue -> create run; returns run id."""
        orch.plan(task)
        self.store.transition(task, "AWAITING_APPROVAL")
        self.store.approve(task, minutes=minutes)
        self.store.transition(task, "QUEUED")
        return orch.create_run(task)["run_id"]

    def task_state(self, task):
        return next(t["state"] for t in self.store.list_tasks() if t["id"] == task)


# ---------------------------------------------------------------------------------------------
class ContractTests(unittest.TestCase):
    def test_strict_validation(self):
        good = {"claimed_status": "ready_for_verification", "summary": "s", "files_changed": [], "tests_claimed": [],
                "blocking_questions": []}
        out = contracts.validate("implementation_report", good)
        self.assertEqual(out["schema_version"], contracts.SCHEMA_VERSION)
        for bad in ({**good, "authorization": {"can_push": True}},            # smuggled field
                    {**good, "claimed_status": "verified"},                    # enum
                    {**good, "files_changed": "src/a.py"},                     # type
                    {**good, "schema_version": "zenith.director.v9"},          # version
                    {k: v for k, v in good.items() if k != "summary"},        # required
                    "not an object", None):
            with self.assertRaises(ContractError):
                contracts.validate("implementation_report", bad)
        with self.assertRaises(ContractError):
            contracts.validate("no_such_contract", good)

    def test_nested_review_contract(self):
        review = {"verdict": "correction_required", "summary": "s", "findings": [],
                  "next_step": {"kind": "revise_prompt", "reason": "r",
                                "revised_prompt": {"objective": "o", "instructions": [], "architectural_constraints": [],
                                                   "acceptance_criteria": [], "test_expectations": [], "output_requirements": [],
                                                   "zenith": {"authorization": {}}}}}
        with self.assertRaisesRegex(ContractError, "unknown fields: zenith"):
            contracts.validate("review_findings", review)
        review["next_step"]["revised_prompt"].pop("zenith")
        self.assertEqual(contracts.validate("review_findings", review)["next_step"]["kind"], "revise_prompt")
        review["next_step"]["kind"] = "ship_it"
        with self.assertRaises(ContractError):
            contracts.validate("review_findings", review)

    def test_scope_globs(self):
        self.assertTrue(contracts.path_allowed("src/a/b.py", ["src/**"], []))
        self.assertTrue(contracts.path_allowed("src/b.py", ["src/*.py"], []))
        self.assertFalse(contracts.path_allowed("src/a/b.py", ["src/*.py"], []))
        self.assertFalse(contracts.path_allowed("tests/t.py", ["**"], ["tests/**"]))
        self.assertFalse(contracts.path_allowed("anything", [], []))
        for bad in ("/abs", "../up", "src/../x"):
            with self.assertRaises(ZenithError):
                contracts.glob_to_regex(bad)

    def test_digest_is_canonical(self):
        self.assertEqual(contracts.digest({"b": 1, "a": [1, 2]}), contracts.digest({"a": [1, 2], "b": 1}))


class ExtensionTests(unittest.TestCase):
    def test_builtin_discovery_and_enablement(self):
        names = {d["name"]: d for d in extensions.describe_all()}
        self.assertEqual(names["fake-director"]["kind"], "director")
        self.assertTrue(names["fake-implementer"]["enabled"])
        self.assertIsInstance(extensions.load("fake-director", "director"), FakeDirector)
        with self.assertRaises(ZenithError):
            extensions.load("fake-director", "implementer")
        with self.assertRaises(ZenithError):
            extensions.load("claude-code", "implementer")

    def test_live_adapters_need_explicit_enablement_and_contract_match(self):
        live = extensions.AdapterManifest("live-x", "director", contracts.SCHEMA_VERSION, FakeDirector, live=True,
                                          permissions=("network",), source="entry-point:test")
        old = extensions.AdapterManifest("old-x", "director", "zenith.director.v0", FakeDirector)
        with mock.patch.object(extensions, "discover", return_value={"live-x": live, "old-x": old}):
            with self.assertRaisesRegex(ZenithError, "ZENITH_ENABLED_ADAPTERS"):
                extensions.load("live-x", "director")
            with mock.patch.dict(os.environ, {"ZENITH_ENABLED_ADAPTERS": "live-x"}):
                self.assertIsInstance(extensions.load("live-x", "director"), FakeDirector)
            with self.assertRaisesRegex(ZenithError, "requires"):
                extensions.load("old-x", "director")


# ---------------------------------------------------------------------------------------------
class ApprovalTests(Base):
    def setUp(self):
        super().setUp()
        self.t = self.task()
        for s in ("CONTEXT_READY", "PLANNED", "AWAITING_APPROVAL"):
            self.store.transition(self.t, s)

    def consume(self, run="run-1"):
        with self.store.write() as con:
            return self.store.consume_approval(con, self.t, "task.execute", run)

    def test_single_use_consumption_and_replay_denied(self):
        a = self.store.approve(self.t)
        self.assertEqual(self.consume(), a)
        row = self.store.list_approvals(self.t)[0]
        self.assertEqual((row["use_count"], row["used_by"]), (1, "run-1"))
        with self.assertRaisesRegex(ZenithError, "No valid approval"):
            self.consume("run-2")
        con = sqlite3.connect(self.store.db_path, isolation_level=None)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "max_uses"):
            con.execute("UPDATE approvals SET use_count=2 WHERE id=?", (a,))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "max_uses"):
            con.execute("UPDATE approvals SET use_count=0 WHERE id=?", (a,))  # cannot be "un-consumed"
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            con.execute("DELETE FROM approvals")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            con.execute("UPDATE approvals SET expires_at='2999-01-01' WHERE id=?", (a,))

    def test_bounded_reuse(self):
        self.store.approve(self.t, max_uses=2)
        self.consume("r1"); self.consume("r2")
        with self.assertRaises(ZenithError):
            self.consume("r3")
        with self.assertRaises(ZenithError):
            self.store.approve(self.t, max_uses=11)

    def test_expired_revoked_and_drifted_are_denied(self):
        a = self.store.approve(self.t, minutes=1)
        con = sqlite3.connect(self.store.db_path, isolation_level=None)
        with self.assertRaises(sqlite3.IntegrityError):  # expiry is immutable
            con.execute("UPDATE approvals SET expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", a))
        with mock.patch("zenith.kernel.utcnow", return_value=(datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()):
            with self.assertRaises(ZenithError):
                self.consume()
        self.consume()  # back in the present: `a` is valid exactly once
        b = self.store.approve(self.t)
        self.store.revoke_approval(b, "changed my mind")
        with self.assertRaises(ZenithError):
            self.consume()
        with self.assertRaises(ZenithError):
            self.store.revoke_approval(b)
        c = self.store.approve(self.t)
        with self.store.write() as con:
            self.store.bump_task_version(con, self.t, "plan drift", "test")
        with self.assertRaisesRegex(ZenithError, "scope changed|No valid approval"):
            self.consume()
        events = [e["event_type"] for e in self.store.events(50)]
        self.assertIn("approval.revoked", events)
        self.assertIn("task.version_bumped", events)
        self.assertNotIn(c, [x["used_by"] for x in self.store.list_approvals(self.t)])

    def test_operation_mismatch_and_non_grantable_operations(self):
        self.store.approve(self.t)
        with self.store.write() as con:
            with self.assertRaises(ZenithError):
                self.store.consume_approval(con, self.t, "git.push", "r")
        for op in ("git.commit", "git.push", "git.pr.merge"):
            with self.assertRaisesRegex(ZenithError, "not grantable"):
                self.store.approve(self.t, operation=op)
        with self.assertRaises(ZenithError):
            self.store.approve(self.t, operation="rm -rf")
        with self.assertRaises(ZenithError):
            self.store.approve(self.t, actor="model:fake-director")

    def test_queue_gate_and_completion_gate(self):
        with self.assertRaises(ZenithError):
            self.store.transition(self.t, "QUEUED")
        self.store.approve(self.t)
        self.store.transition(self.t, "QUEUED")
        with self.assertRaises(ZenithError):
            self.store.transition(self.t, "COMPLETED", cli=False)  # no evidence
        self.store.transition(self.t, "RUNNING", cli=False)
        self.store.transition(self.t, "VERIFYING", cli=False)
        with self.assertRaisesRegex(ZenithError, "TEST_VERIFICATION"):
            self.store.transition(self.t, "COMPLETED", cli=False, verification_evidence_id="nope")
        fail = self.store.record_test_verification(self.project, self.source, "unit", 1, "a" * 40)
        with self.assertRaises(ZenithError):
            self.store.transition(self.t, "COMPLETED", cli=False, verification_evidence_id=fail)
        ok = self.store.record_test_verification(self.project, self.source, "unit", 0, "a" * 40)
        self.assertEqual(self.store.transition(self.t, "COMPLETED", cli=False, verification_evidence_id=ok), "COMPLETED")

    def test_scope_validation(self):
        with self.assertRaises(ZenithError):
            self.store.add_task(self.project, "x", scope={"allowed_files": ["../etc"]})
        with self.assertRaises(ZenithError):
            self.store.add_task(self.project, "x", scope={"acceptance": [{"argv": "python -m unittest"}]})
        with self.assertRaises(ZenithError):
            self.store.add_task(self.project, "x", scope={"shell": "rm -rf /"})


class MigrationV4Tests(Base):
    def test_v1_to_v4_keeps_approvals_and_tasks(self):
        path = self.root / "legacy.db"
        migrations.apply_pending(path, migrations=migrations.MIGRATIONS[:1], backup=False)
        con = sqlite3.connect(path)
        con.executescript("""
          INSERT INTO projects VALUES ('p1','L','%s','personal','active','2026-01-01T00:00:00+00:00');
          INSERT INTO tasks VALUES ('t1','p1','T','o','L1','AWAITING_APPROVAL','2026-01-02T00:00:00+00:00',NULL,1);
          INSERT INTO approvals VALUES ('a1','t1','oldhash','owner','2099-01-01T00:00:00+00:00','2026-01-02T00:00:00+00:00');
        """ % self.repo)
        con.commit(); con.close()
        Store(path).migrate(confirm=str(path))
        con = sqlite3.connect(path)
        self.assertEqual(migrations.current_version(con), migrations.LATEST_VERSION)
        self.assertEqual(con.execute("SELECT operation,max_uses,use_count,project_id FROM approvals").fetchone(),
                         ("task.execute", 1, 0, "p1"))
        self.assertEqual(con.execute("SELECT scope FROM tasks").fetchone()[0], "{}")
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"execution_runs", "worktrees", "run_events", "artifacts", "decisions"} <= tables)
        # The legacy approval's hash no longer matches (scope now part of the hash): fail closed.
        with self.assertRaises(ZenithError):
            Store(path).transition("t1", "QUEUED")


# ---------------------------------------------------------------------------------------------
@needs_sandbox
class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.wt = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def run_py(self, code, timeout=20, **kw):
        return sandbox.run_isolated(["python", "-c", code], self.wt, timeout=timeout, caps=CAPS, **kw)

    def test_home_credentials_and_network_are_unreachable(self):
        r = self.run_py("import os,socket,pathlib\n"
                        "print('HOME', os.environ.get('HOME'), os.path.exists(os.path.expanduser('~/.ssh')))\n"
                        "print('ROOT', sorted(os.listdir('/')))\n"
                        "print('ENV', sorted(k for k in os.environ))\n"
                        "s=socket.socket(); s.settimeout(1)\n"
                        "try:\n s.connect(('203.0.113.1', 9)); print('NET open')\n"
                        "except OSError as e: print('NET closed', e.errno)\n")
        self.assertEqual(r.exit_code, 0, r.output_tail)
        self.assertIn("HOME /home/sandbox False", r.output_tail)
        self.assertNotIn("misa", r.output_tail)
        self.assertNotIn("'root'", r.output_tail)
        self.assertIn("NET closed", r.output_tail)
        for secret_env in ("ZENITH_DB_PATH", "SSH_AUTH_SOCK", "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            self.assertNotIn(secret_env, r.output_tail)

    def test_only_the_worktree_is_writable(self):
        r = self.run_py("open('/work/ok.txt','w').write('x')\n"
                        "for p in ('/usr/x','/etc/x','/x'):\n"
                        "  try: open(p,'w'); print('WROTE', p)\n"
                        "  except OSError: print('denied', p)\n")
        self.assertEqual(r.exit_code, 0, r.output_tail)
        self.assertNotIn("WROTE", r.output_tail)
        self.assertTrue((self.wt / "ok.txt").exists())
        self.assertFalse((self.wt / "__pycache__").exists())

    def test_timeout_kills_process_group_and_output_is_capped(self):
        t0 = time.monotonic()
        r = self.run_py("import subprocess,time\nsubprocess.Popen(['sleep','60'])\ntime.sleep(60)", timeout=1.5)
        self.assertTrue(r.timed_out)
        self.assertIsNone(r.exit_code)
        self.assertLess(time.monotonic() - t0, 10)
        r = self.run_py("print('x'*100000)", max_output_bytes=10_000)
        self.assertTrue(r.truncated)
        self.assertIsNone(r.exit_code)  # a truncated run is never trusted as a pass

    def test_no_shell_interpolation(self):
        (self.wt / "f").write_text("x")
        r = sandbox.run_isolated(["ls", "f; rm -rf /work/f", "$(touch pwned)"], self.wt, timeout=10, caps=CAPS)
        self.assertNotEqual(r.exit_code, 0)
        self.assertTrue((self.wt / "f").exists())
        self.assertFalse((self.wt / "pwned").exists())
        with self.assertRaises(ZenithError):
            sandbox.run_isolated(["python", ""], self.wt, timeout=1, caps=CAPS)


class SandboxFailClosedTests(unittest.TestCase):
    def test_refuses_without_baseline(self):
        bad = sandbox.Capabilities(ok=False, reasons=["bubblewrap (bwrap) not installed"])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(sandbox.SandboxUnavailable, "Refusing to execute"):
                sandbox.run_isolated(["python", "-c", "open('/tmp/marker','w')"], Path(tmp), timeout=5, caps=bad)
        with mock.patch("zenith.sandbox.shutil.which", return_value=None):
            caps = sandbox.detect()
        self.assertFalse(caps.ok)
        self.assertIn("not installed", caps.reasons[0])


# ---------------------------------------------------------------------------------------------
class WorktreeTests(Base):
    def test_create_change_and_remove_without_touching_primary(self):
        head = worktree.head_of(self.repo)
        wt = worktree.create(self.repo, self.state / "wt1", head, "zenith/t1/1")
        self.assertEqual(worktree.head_of(wt), head)
        self.assertEqual(worktree.changed_paths(wt), [])
        (wt / "src" / "calc.py").write_text(FIXED)
        (wt / "new.txt").write_text("n")
        self.assertEqual(worktree.changed_paths(wt), ["new.txt", "src/calc.py"])
        self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=self.repo, capture_output=True, text=True).stdout, "")
        self.assertEqual((self.repo / "src" / "calc.py").read_text(), BUGGY)
        with self.assertRaises(ZenithError):
            worktree.create(self.repo, self.state / "wt1", head, "zenith/t1/2")
        with self.assertRaises(ZenithError):
            worktree.create(self.repo, self.state / "wt2", head, "main")
        worktree.remove(self.repo, wt, "zenith/t1/1")
        self.assertFalse(wt.exists())

    def test_repo_hooks_do_not_run(self):
        hooks = self.repo / ".git" / "hooks"
        marker = self.root / "hook-ran"
        (hooks / "post-checkout").write_text(f"#!/bin/sh\ntouch {marker}\n")
        (hooks / "post-checkout").chmod(0o755)
        worktree.create(self.repo, self.state / "wt", worktree.head_of(self.repo), "zenith/t/1")
        self.assertFalse(marker.exists())


# ---------------------------------------------------------------------------------------------
@needs_sandbox
class WorkflowTests(Base):
    def test_full_workflow_completes_and_survives_restart(self):
        o = self.orch(implementer={"implement": {"writes": {"src/calc.py": FIXED}}})
        t = self.task()
        plan = o.plan(t)
        prompt = self.store.get_artifact(plan["prompt_id"])["payload"]
        self.assertEqual(prompt["zenith"]["allowed_scope"], ["src/**"])
        self.assertFalse(prompt["zenith"]["authorization"]["can_push"])
        self.assertEqual(self.task_state(t), "PLANNED")
        self.store.transition(t, "AWAITING_APPROVAL")
        self.store.approve(t)
        self.store.transition(t, "QUEUED")
        run = o.create_run(t)
        self.assertTrue(o.create_run(t)["duplicate"])
        res = o.run_once(run["run_id"])
        self.assertEqual((res["state"], res["exit_reason"], res["task_state"]), ("COMPLETED", "verified_and_reviewed", "COMPLETED"))
        kinds = [a["kind"] for a in reversed(self.store.list_artifacts(task_id=t))]
        self.assertEqual(kinds, ["context_package", "architecture_proposal", "implementation_prompt", "implementation_report",
                                 "verification_report", "review_findings"])
        self.assertEqual(self.store.list_approvals(t)[0]["use_count"], 1)
        self.assertEqual((self.repo / "src" / "calc.py").read_text(), BUGGY)  # primary tree untouched
        # Restart: a brand-new Store/Orchestrator sees the full history.
        again = Orchestrator(Store(self.store.db_path), FakeDirector(), FakeImplementer(), caps=CAPS, state_root=self.state)
        st = again.status(run["run_id"])
        self.assertEqual(st["state"], "COMPLETED")
        self.assertIn("run.started", [e["kind"] for e in st["events"]])
        self.assertIn("approval.consumed", [e["event_type"] for e in self.store.events(200)])
        brief = Store(self.store.db_path).brief(self.project)["projects"][0]
        self.assertIsNotNone(brief["latest_verification"])
        self.assertTrue(brief["latest_verification"]["passed"])
        self.assertEqual(len(self.store.list_decisions(self.project)), 2)  # director decision + review verdict

    def test_out_of_scope_change_is_blocked_with_revised_prompt(self):
        o = self.orch(implementer={"implement": {"writes": {"src/calc.py": FIXED, "tests/test_calc.py": "# gone\n"}}})
        t = self.task()
        res = o.run_once(self.ready(o, t))
        self.assertEqual((res["state"], res["exit_reason"], res["task_state"]), ("BLOCKED", "correction_required", "BLOCKED"))
        ver = next(self.store.get_artifact(a["id"])["payload"] for a in self.store.list_artifacts(task_id=t, kind="verification_report"))
        self.assertFalse(ver["passed"])
        self.assertEqual(ver["out_of_scope_paths"], ["tests/test_calc.py"])
        prompts = self.store.list_artifacts(task_id=t, kind="implementation_prompt")
        self.assertEqual(len(prompts), 2)
        revised = self.store.get_artifact(prompts[0]["id"])["payload"]
        self.assertEqual(revised["zenith"]["revision"], 2)
        self.assertEqual(revised["zenith"]["parent_prompt_id"], prompts[1]["id"])
        self.assertIn("Previous failure", " ".join(revised["instructions"]))
        with self.assertRaises(ZenithError):  # old approval was consumed and the scope hash changed
            self.store.transition(t, "QUEUED")

    def test_false_claims_never_complete(self):
        o = self.orch(implementer={"implement": {"writes": {"src/calc.py": BUGGY.replace("a - b", "a * b")},
                                                 "files_claimed": ["src/calc.py"],
                                                 "tests_claimed": [{"command_id": "unit", "claimed_pass": True}]}})
        t = self.task()
        res = o.run_once(self.ready(o, t))
        self.assertEqual(res["task_state"], "BLOCKED")
        ver = self.store.get_artifact(self.store.list_artifacts(task_id=t, kind="verification_report")[0]["id"])["payload"]
        self.assertFalse(ver["passed"])
        self.assertTrue(any("claimed unit passed" in m for m in ver["claims_mismatch"]))
        ev = self.store.list_evidence(self.project, "TEST_VERIFICATION")[0]
        self.assertFalse(ev["details"]["passed"])

    def test_reviewer_disagreement_blocks_until_owner_accepts(self):
        o = self.orch(director={"review": {"mode": "disagree"}}, implementer={"implement": {"writes": {"src/calc.py": FIXED}}})
        t = self.task()
        run = self.ready(o, t)
        res = o.run_once(run)
        self.assertEqual((res["state"], res["exit_reason"], res["task_state"]), ("BLOCKED", "reviewer_disagreement", "BLOCKED"))
        with self.assertRaises(ZenithError):
            o.accept_run(run, "x", actor="model:fake-director")
        self.assertEqual(o.accept_run(run, "naming is fine")["task_state"], "COMPLETED")

    def test_accept_requires_passing_verification(self):
        o = self.orch(implementer={"implement": {"writes": {"src/calc.py": "def add(a, b):\n    return 0\n"}}})
        t = self.task()
        run = self.ready(o, t)
        o.run_once(run)
        with self.assertRaisesRegex(ZenithError, "no passing verification"):
            o.accept_run(run, "please")

    def test_repeated_failures_exhaust_attempts(self):
        o = self.orch(implementer={"implement": {"writes": {"src/calc.py": "def add(a, b):\n    return 0\n"}}})
        t = self.task()
        run1 = self.ready(o, t)
        self.assertEqual(o.run_once(run1)["task_state"], "BLOCKED")
        self.store.approve(t)               # fresh approval for the revised prompt
        self.store.transition(t, "QUEUED")
        run2 = o.create_run(t, budget={"max_attempts": 2})["run_id"]
        res = o.run_once(run2)
        self.assertEqual((res["state"], res["exit_reason"], res["task_state"]), ("FAILED", "attempts_exhausted", "FAILED"))
        self.assertEqual(res["attempt"], 2)

    def test_invalid_model_output(self):
        o = self.orch(director={"plan": {"mode": "invalid_output"}})
        t = self.task()
        with self.assertRaisesRegex(ZenithError, "invalid_output"):
            o.plan(t)
        self.assertEqual(self.task_state(t), "CONTEXT_READY")
        self.assertIn("task.plan_failed", [e["event_type"] for e in self.store.events(20)])
        o2 = self.orch(implementer={"implement": {"mode": "invalid_output"}})
        t2 = self.task()
        res = o2.run_once(self.ready(o2, t2))
        self.assertEqual(res["task_state"], "BLOCKED")
        self.assertIn("implementer.failed", [e["kind"] for e in res["events"]])
        o3 = self.orch(director={"review": {"mode": "invalid_output"}}, implementer={"implement": {"writes": {"src/calc.py": FIXED}}})
        t3 = self.task()
        res = o3.run_once(self.ready(o3, t3))
        self.assertEqual((res["exit_reason"], res["task_state"]), ("review_unavailable", "BLOCKED"))

    def test_cancellation(self):
        o = self.orch(implementer={"implement": {"writes": {"src/calc.py": FIXED}}})
        t = self.task()
        run = self.ready(o, t)
        self.assertEqual(o.cancel_run(run)["state"], "CANCELLED")       # before start: no side effects
        self.assertEqual(self.store.list_approvals(t)[0]["use_count"], 0)
        with self.assertRaises(ZenithError):
            o.run_once(run)
        run2 = o.create_run(t)["run_id"]
        o2 = self.orch(implementer={"implement": {"mode": "wait_for_cancel"}})
        import threading
        threading.Timer(0.2, lambda: o2.cancel_run(run2)).start()
        res = o2.run_once(run2)
        self.assertEqual((res["state"], res["exit_reason"], res["task_state"]), ("CANCELLED", "cancelled", "BLOCKED"))

    def test_expired_approval_cannot_start_a_run(self):
        o = self.orch()
        t = self.task()
        run = self.ready(o, t, minutes=1)
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="microseconds")
        with mock.patch("zenith.kernel.utcnow", return_value=future):
            with self.assertRaisesRegex(ZenithError, "No valid approval"):
                o.run_once(run)
        self.assertEqual(o.status(run)["state"], "PENDING")
        self.assertEqual(self.task_state(t), "QUEUED")

    def test_interrupted_start_is_atomic(self):
        o = self.orch()
        t = self.task()
        run = self.ready(o, t)
        with mock.patch("zenith.orchestration._run_event", side_effect=RuntimeError("disk on fire")):
            with self.assertRaises(RuntimeError):
                o.run_once(run)
        self.assertEqual(o.status(run)["state"], "PENDING")
        self.assertEqual(self.store.list_approvals(t)[0]["use_count"], 0)
        self.assertEqual(self.task_state(t), "QUEUED")

    def test_crash_during_implementation_requires_reconciliation(self):
        o = self.orch(implementer={"implement": {"mode": "crash"}})
        t = self.task()
        run = self.ready(o, t)
        res = o.run_once(run)
        self.assertEqual((res["state"], res["task_state"]), ("RECONCILE_REQUIRED", "RECONCILE_REQUIRED"))
        self.assertIsNone(res["worktree"]["removed_at"])     # kept for inspection; source stays locked
        t2 = self.task()
        run2 = self.ready(o, t2)
        with self.assertRaisesRegex(ZenithError, "Source is busy"):
            o.run_once(run2)
        self.assertEqual(self.store.list_approvals(t2)[0]["use_count"], 0)
        with self.assertRaises(ZenithError):
            o.reconcile(run, "retry", "x", actor="model:fake")
        with self.assertRaisesRegex(ZenithError, "approval is required"):   # retry needs a fresh approval first
            o.reconcile(run, "retry", "worktree inspected, nothing happened")
        self.assertEqual(self.task_state(t), "RECONCILE_REQUIRED")
        self.store.approve(t)
        o.reconcile(run, "retry", "worktree inspected, nothing happened")
        self.assertEqual((self.task_state(t), o.status(run)["state"]), ("QUEUED", "FAILED"))
        good = self.orch(implementer={"implement": {"writes": {"src/calc.py": FIXED}}})
        res = good.run_once(good.create_run(t)["run_id"])
        self.assertEqual((res["attempt"], res["task_state"]), (2, "COMPLETED"))

    def test_worker_crash_leaves_stale_lease_then_reconciled(self):
        o = self.orch()
        t = self.task()
        run = self.ready(o, t)
        code = f"""
import sys; sys.path.insert(0, {SRC!r})
from zenith.kernel import Store
from zenith.agents.fake import FakeDirector, FakeImplementer
from zenith.orchestration import Orchestrator
from zenith import sandbox
o = Orchestrator(Store({str(self.store.db_path)!r}), FakeDirector(), FakeImplementer({{"implement": {{"mode": "hang"}}}}),
                 caps=sandbox.Capabilities(ok=True, bwrap='x'), state_root={str(self.state)!r}, lease_ttl_seconds=1)
o.run_once({run!r})
"""
        proc = subprocess.Popen([sys.executable, "-c", code])
        try:
            for _ in range(200):
                if o.status(run)["stage"] == "implement" and o.status(run)["worktree"]:
                    break
                time.sleep(0.05)
            proc.kill()
        finally:
            proc.wait()
        self.assertEqual(o.status(run)["state"], "RUNNING")
        self.assertEqual(o.reconcile_stale_leases(), [])          # lease not yet expired
        time.sleep(1.2)
        stale = o.reconcile_stale_leases()
        self.assertEqual([s["run_id"] for s in stale], [run])
        self.assertEqual((o.status(run)["state"], self.task_state(t)), ("RECONCILE_REQUIRED", "RECONCILE_REQUIRED"))
        self.assertEqual(o.reconcile_stale_leases(), [])          # idempotent
        o.reconcile(run, "failed", "process died mid-implementation")
        self.assertEqual(self.task_state(t), "FAILED")

    def test_model_output_cannot_grant_permissions(self):
        o = self.orch(director={"plan": {"prompt": {
            "objective": "o", "instructions": [], "architectural_constraints": [], "acceptance_criteria": [],
            "test_expectations": [], "output_requirements": [], "proposed_scope": ["**", ".git/**"],
        }}})
        t = self.task()
        plan = o.plan(t)
        prompt = self.store.get_artifact(plan["prompt_id"])["payload"]
        self.assertEqual(prompt["zenith"]["allowed_scope"], ["src/**"])       # owner scope wins
        self.assertEqual(prompt["zenith"]["scope_narrowed"], ["**", ".git/**"])
        self.assertEqual(self.store.list_approvals(t), [])                     # planning grants nothing
        con = sqlite3.connect(self.store.db_path, isolation_level=None)
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("UPDATE artifacts SET payload='{}' WHERE id=?", (plan["prompt_id"],))
        with self.assertRaises(ZenithError):
            self.store.get_artifact("missing")


class DemoCliTests(unittest.TestCase):
    @needs_sandbox
    def test_demo_poc_scenarios(self):
        from zenith.demo import run_poc
        with tempfile.TemporaryDirectory() as tmp:
            s = run_poc(str(Path(tmp) / "s"), "success")
            self.assertEqual((s["run"]["state"], s["run"]["task_state"]), ("COMPLETED", "COMPLETED"))
            self.assertTrue(s["primary_tree_untouched"])
            self.assertTrue(s["verification"]["passed"])
            self.assertEqual(s["approvals"][0]["use_count"], 1)
            c = run_poc(str(Path(tmp) / "c"), "crash")
            self.assertEqual(c["run"]["state"], "RECONCILE_REQUIRED")
            f = run_poc(str(Path(tmp) / "f"), "false_claim")
            self.assertEqual(f["run"]["task_state"], "BLOCKED")

    def test_cli_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp).resolve()
            repo = tmp / "repo"
            make_repo(repo)
            env = {**os.environ, "ZENITH_DB_PATH": str(tmp / "z.db"), "ZENITH_STATE_DIR": str(tmp / "state"), "PYTHONPATH": SRC}

            def z(*args, ok=True):
                r = subprocess.run([sys.executable, "-m", "zenith", *args], env=env, capture_output=True, text=True)
                self.assertEqual(r.returncode == 0, ok, r.stderr)
                return json.loads(r.stdout) if ok else r
            z("init")
            self.assertEqual(z("doctor")["schema_version"], migrations.LATEST_VERSION)
            self.assertIn("fake-director", [a["name"] for a in z("adapters")])
            self.assertIn("ok", z("sandbox", "doctor"))
            pid = z("project", "add", "--name", "P", "--path", str(repo))["project_id"]
            tid = z("task", "add", "--project", pid, "--title", "T", "--risk", "L2", "--allow", "src/**",
                    "--acceptance", "python -m unittest discover -s tests")["task_id"]
            plan = z("director", "plan", "--task", tid)
            self.assertEqual(plan["state"], "PLANNED")
            z("task", "transition", "--id", tid, "--to", "AWAITING_APPROVAL")
            aid = z("task", "approve", "--id", tid, "--max-uses", "1")["approval_id"]
            self.assertEqual(z("task", "approvals", "--id", tid)[0]["id"], aid)
            z("task", "transition", "--id", tid, "--to", "QUEUED")
            run = z("run", "create", "--task", tid)["run_id"]
            self.assertEqual(z("run", "status", "--run", run)["state"], "PENDING")
            self.assertEqual(len(z("artifacts", "list", "--task", tid)), 3)
            self.assertEqual(z("artifacts", "show", "--id", plan["prompt_id"])["kind"], "implementation_prompt")
            z("run", "cancel", "--run", run)
            z("worker", "--run", run, ok=False)
            z("task", "revoke", "--approval", aid, "--reason", "demo over")
            z("task", "approve", "--id", tid, ok=False)  # task is QUEUED, not awaiting approval
            self.assertEqual(z("worker", "--reconcile")["reconcile_required"], [])
            z("run", "reconcile", "--run", run, "--outcome", "retry", "--reason", "x", ok=False)


if __name__ == "__main__":
    unittest.main()
