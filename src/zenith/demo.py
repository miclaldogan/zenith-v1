"""Disposable end-to-end demonstration of the mock-agent workflow.

Everything lives under one throwaway directory: its own database, state dir and Git repo.
The owner's real database and projects are never touched. No model is called.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from .agents.fake import FakeDirector, FakeImplementer
from .kernel import Store
from .orchestration import Orchestrator
from .sandbox import detect

BUGGY = "def add(a, b):\n    return a - b  # bug\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = ("import sys, unittest\nsys.path.insert(0, 'src')\nfrom calc import add\n\n"
        "class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")

SCENARIOS = {
    "success": {"implementer": {"implement": {"writes": {"src/calc.py": FIXED}}}},
    "out_of_scope": {"implementer": {"implement": {"writes": {"src/calc.py": FIXED, "tests/test_calc.py": "# weakened\n"}}}},
    "false_claim": {"implementer": {"implement": {"writes": {"src/calc.py": BUGGY.replace("a - b", "a * b")},
                                                  "summary": "Fixed it, all tests pass."}}},
    "disagree": {"implementer": {"implement": {"writes": {"src/calc.py": FIXED}}},
                 "director": {"review": {"mode": "disagree"}}},
    "crash": {"implementer": {"implement": {"mode": "crash"}}},
}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=demo@example.invalid", "-c", "user.name=demo", *args],
                   cwd=cwd, check=True, capture_output=True)


def make_repo(repo: Path) -> None:
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "calc.py").write_text(BUGGY)
    (repo / "tests" / "test_calc.py").write_text(TEST)
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial (failing test)")


def run_poc(base_dir: str | None = None, scenario: str = "success") -> dict:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario}")
    base = Path(base_dir or tempfile.mkdtemp(prefix="zenith-poc-")).resolve()
    repo, state = base / "repo", base / "state"
    make_repo(repo)
    store = Store(base / "zenith.db")
    store.init()
    caps = detect()
    script = SCENARIOS[scenario]
    orch = Orchestrator(store, FakeDirector(script.get("director")), FakeImplementer(script.get("implementer")),
                        caps=caps, state_root=state)
    steps: list[str] = []
    project = store.add_project("POC calculator", str(repo))
    task = store.add_task(project, "Make the calculator tests pass", "add() returns the wrong result", risk="L2",
                          scope={"allowed_files": ["src/**"], "excluded_files": ["tests/**"],
                                 "acceptance": [{"id": "unit", "argv": ["python", "-m", "unittest", "discover", "-s", "tests"]}]})
    steps.append("1. owner registered the project and an engineering objective (task)")
    plan = orch.plan(task)
    steps.append(f"2-3. director produced proposal {plan['proposal_id'][:8]} and prompt {plan['prompt_id'][:8]} (persisted)")
    store.transition(task, "AWAITING_APPROVAL")
    approval = store.approve(task, minutes=30)
    store.transition(task, "QUEUED")
    steps.append(f"4. owner approval {approval[:8]} recorded (single use, scope-hashed) and task queued")
    run = orch.create_run(task)
    dup = orch.create_run(task)
    steps.append(f"5. run {run['run_id'][:8]} created; duplicate submission ignored={dup['duplicate']}")
    result = orch.run_once(run["run_id"]) if caps.ok else None
    if result is None:
        steps.append("5-8. NOT EXECUTED: sandbox baseline unavailable: " + "; ".join(caps.reasons))
    else:
        steps.append(f"5-8. worker finished: run {result['state']} ({result['exit_reason']}), task {result['task_state']}")
    # 9. restart: a fresh process-independent view of the same database.
    reopened = Store(base / "zenith.db")
    orch2 = Orchestrator(reopened, FakeDirector(), FakeImplementer(), caps=caps, state_root=state)
    status = orch2.status(run["run_id"])
    artifacts = reopened.list_artifacts(task_id=task)
    brief = reopened.brief(project)["projects"][0]
    steps.append(f"9. after reopening: {len(artifacts)} artifacts, {len(status['events'])} run events, "
                 f"brief status {brief['status']['label']}")
    primary_clean = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout == ""
    return {
        "scenario": scenario, "dir": str(base), "sandbox_ok": caps.ok, "steps": steps,
        "run": {k: status[k] for k in ("id", "state", "exit_reason", "task_state", "attempt")},
        "artifacts": [{"kind": a["kind"], "id": a["id"], "produced_by": a["produced_by"]} for a in reversed(artifacts)],
        "approvals": [{k: a[k] for k in ("id", "use_count", "max_uses", "used_by")} for a in reopened.list_approvals(task)],
        "verification": next((reopened.get_artifact(a["id"])["payload"] for a in artifacts if a["kind"] == "verification_report"), None),
        "primary_tree_untouched": primary_clean,
        "note": "Fake adapters only; nothing here is real Claude Code or Codex execution.",
    }
