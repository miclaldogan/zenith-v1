"""Explicitly authorized live smoke test for one provider adapter.

Without `--authorize-live-call` (and the matching environment acknowledgement) this performs
the non-live preflight only and prints exactly what a live call would do. A live call makes
ONE minimal, structured request through the full isolation path (sandbox, allowlist proxy,
read-only credential bind) against a disposable repository and records the outcome as an
audit event. It is never run by tests or by any other command.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from . import contracts, sandbox
from .agents.base import AdapterError, RunContext
from .extensions import discover, load
from .kernel import Store, ZenithError, _record_event, utcnow

ACK_ENV = "ZENITH_LIVE_SMOKE_ACK"
ACK_VALUE = "I-AUTHORIZE-ONE-LIVE-PROVIDER-CALL"


def _tiny_context(repo: Path) -> dict:
    files = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*")
                   if p.is_file() and ".git/" not in p.relative_to(repo).as_posix())[:50]
    return contracts.validate("context_package", {
        "project_id": "smoke", "task_id": "smoke-task",
        "task": {"id": "smoke-task", "title": "Smoke test: describe this repository",
                 "objective": "Produce a one-step architecture proposal and a trivial implementation prompt that "
                              "adds a file named ZENITH_SMOKE.txt containing the word ok.",
                 "risk": "L1", "state": "DRAFT", "version": 1, "scope": json.dumps({"allowed_files": ["ZENITH_SMOKE.txt"]})},
        "sources": [{"id": "smoke-src", "path": "/work", "label": "disposable", "last_seen_at": None}],
        "evidence": [], "brief_status": {"label": "UNKNOWN", "meaning": "smoke", "basis": [], "caveats": []},
        "constraints": ["This is a smoke test.", f"Files: {files}"], "generated_at": utcnow()})


def authorization_request(adapters: list, repo_desc: str, max_budget_usd: float, operation: str, outcome: str) -> dict:
    """What the owner must see before saying yes (REQ: explicit, specific authorization)."""
    return {
        "providers": [{"adapter": a.name, "provider": a.provider, "role": a.kind, "auth_mode": a.auth_mode(),
                       "auth_configured": a.check_auth().ok, "allowed_hosts": sorted(a.allowed_hosts),
                       "model": a.model} for a in adapters],
        "estimated_budget_usd_cap": max_budget_usd,
        "runtime_caps": {"stage_deadline_seconds": 180, "max_attempts": 1, "retries": "none"},
        "files_accessible_to_agents": repo_desc,
        "exact_operation": operation,
        "expected_outcome": outcome,
        "network": "egress only to allowed_hosts through Zenith's allowlist proxy; everything else denied and logged",
        "git": "no commits, no pushes; disposable repository only",
        "how_to_authorize": f"re-run with --authorize-live-call and {ACK_ENV}={ACK_VALUE}",
    }


def run_smoke(store: Store, adapter_name: str, repo: str | None, authorize: bool, max_budget_usd: float,
              full: bool = False, implementer_name: str | None = None) -> dict:
    if full:
        return run_full_smoke(store, adapter_name, implementer_name or "claude-code-implementer", authorize, max_budget_usd)
    if not repo:
        raise ZenithError("--repo is required (a disposable Git repository)")
    repo_path = Path(repo).expanduser().resolve()
    if not (repo_path / ".git").exists():
        raise ZenithError("--repo must be a disposable Git repository (never a registered personal project)")
    registered = {s["path"] for p in store.list_projects() for s in p["sources"]}
    if str(repo_path) in registered:
        raise ZenithError("Refusing: that repository is registered in Zenith; use a throwaway copy")
    manifest = discover().get(adapter_name)
    if manifest is None or manifest.kind == "broken":
        raise ZenithError(f"Unknown adapter: {adapter_name}")
    adapter = load(adapter_name, manifest.kind, script={"max_budget_usd": max_budget_usd})
    pre = adapter.preflight()
    pre["live_call_performed"] = False
    pre["authorization_request"] = authorization_request(
        [adapter], f"read-only copy of {repo_path} (director: Zenith context dir only; implementer: the copy)",
        max_budget_usd, f"one {manifest.kind} call ({'plan' if manifest.kind == 'director' else 'create ZENITH_SMOKE.txt'})",
        "a validated structured contract; no files outside the copy touched")
    blockers = []
    if not pre["sandbox_ok"]:
        blockers.append("sandbox baseline unavailable")
    if not pre["auth"]["ok"]:
        blockers.append("auth: " + pre["auth"]["detail"])
    if pre.get("binary") is None:
        blockers.append(pre.get("binary_error", "binary missing"))
    if not pre["autonomous_supported"]:
        blockers.append("adapter is not supported for autonomous execution (ADR-007)")
    pre["blockers"] = blockers
    if not authorize:
        pre["note"] = ("Preflight only. To perform ONE live call: fix blockers, then re-run with "
                       f"--authorize-live-call and {ACK_ENV}={ACK_VALUE} in the environment.")
        return pre
    if os.environ.get(ACK_ENV) != ACK_VALUE:
        raise ZenithError(f"Live call refused: set {ACK_ENV}={ACK_VALUE} to acknowledge the provider call and its cost")
    if blockers:
        raise ZenithError("Live call refused: " + "; ".join(blockers))

    caps = sandbox.detect()
    events: list[dict] = []
    ctx = RunContext(run_id="smoke", stage="smoke", deadline_seconds=180.0,
                     emit=lambda kind, payload: events.append({"kind": kind, **payload}))
    work = Path(tempfile.mkdtemp(prefix="zenith-smoke-"))
    outcome: dict = {"ok": False}
    try:
        shutil.copytree(repo_path, work / "repo", symlinks=False)
        try:
            if adapter.kind == "director":
                ctx.read_root = work / "repo"
                raw = adapter.plan(ctx, _tiny_context(work / "repo"))
                outcome["proposal"] = contracts.validate("architecture_proposal", raw.get("architecture_proposal"))
                outcome["prompt"] = contracts.validate("implementation_prompt", raw.get("implementation_prompt"))
            else:
                ctx.worktree = work / "repo"
                prompt = {"objective": "Create ZENITH_SMOKE.txt containing the single word ok.",
                          "instructions": ["Create the file ZENITH_SMOKE.txt at the repository root with content 'ok'."],
                          "architectural_constraints": [], "acceptance_criteria": ["file exists"],
                          "test_expectations": [], "output_requirements": ["implementation_report"],
                          "zenith": {"allowed_scope": ["ZENITH_SMOKE.txt"], "exclusions": [],
                                     "authorization": {"can_commit": False, "can_push": False}}}
                raw = adapter.implement(ctx, prompt)
                outcome["report"] = contracts.validate("implementation_report", raw)
                outcome["file_created"] = (work / "repo" / "ZENITH_SMOKE.txt").exists()
            outcome["ok"] = True
        except (AdapterError, contracts.ContractError) as exc:
            outcome["error"] = {"kind": getattr(exc, "kind", "contract"), "detail": str(exc)[:500]}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    cost = sum(e.get("cost_usd", 0) for e in events if e["kind"] == "provider.usage")
    denied = [e for e in events if e["kind"] == "network.denied"]
    with store.write() as con:
        _record_event(con, "system", "smoke", "smoke.live_call",
                      {"adapter": adapter_name, "ok": outcome["ok"], "cost_usd": cost, "events": len(events),
                       "network_denied": len(denied), "sandbox": caps.as_dict()}, actor="owner")
    return {**pre, "live_call_performed": True, "outcome": outcome, "cost_usd": cost,
            "network_denied": denied, "event_count": len(events)}


def run_full_smoke(store: Store, director_name: str, implementer_name: str, authorize: bool, max_budget_usd: float) -> dict:
    """Director request → persisted prompt → ONE implementation → deterministic verification → report,
    on a synthetic repository Zenith generates itself (no user files), max_attempts=1, no retries."""
    from .demo import make_repo
    from .orchestration import Orchestrator
    director = load(director_name, "director", script={"max_budget_usd": max_budget_usd / 2})
    implementer = load(implementer_name, "implementer", script={"max_budget_usd": max_budget_usd / 2})
    pres = {"director": director.preflight(), "implementer": implementer.preflight()}
    blockers = []
    for role, pre in pres.items():
        if not pre["sandbox_ok"]:
            blockers.append(f"{role}: sandbox baseline unavailable")
        if not pre["auth"]["ok"]:
            blockers.append(f"{role}: {pre['auth']['detail']}")
        if pre.get("binary") is None:
            blockers.append(f"{role}: {pre.get('binary_error', 'binary missing')}")
        if not pre["autonomous_supported"]:
            blockers.append(f"{role}: not supported for autonomous execution (ADR-007)")
    request = authorization_request(
        [director, implementer],
        "a synthetic calculator repository generated by Zenith in a temporary directory (src/calc.py, tests/test_calc.py); "
        "director: Zenith context dir (context.json, listing, no repository files unless context_files set); "
        "implementer: a disposable worktree of that synthetic repository",
        max_budget_usd,
        "plan (director) → approve → implement (edit-only) → verify in credential-free sandbox → review (director)",
        "task COMPLETED with a passing TEST_VERIFICATION, or a classified failure; nothing outside the temp dir changes")
    result = {"mode": "full", "preflight": pres, "blockers": blockers, "authorization_request": request,
              "live_call_performed": False}
    if not authorize:
        result["note"] = "Preflight only. No provider call was made."
        return result
    if os.environ.get(ACK_ENV) != ACK_VALUE:
        raise ZenithError(f"Live call refused: set {ACK_ENV}={ACK_VALUE} to acknowledge the provider calls and their cost")
    if blockers:
        raise ZenithError("Live call refused: " + "; ".join(blockers))
    base = Path(tempfile.mkdtemp(prefix="zenith-live-smoke-"))
    repo = base / "repo"
    make_repo(repo)
    smoke_store = Store(base / "zenith.db")   # the owner's database only receives the audit event
    smoke_store.init()
    orch = Orchestrator(smoke_store, director, implementer, state_root=base / "state")
    project = smoke_store.add_project("Live smoke calculator", str(repo))
    task = smoke_store.add_task(project, "Make the calculator tests pass", "add() returns the wrong result", risk="L2",
                                scope={"allowed_files": ["src/**"], "excluded_files": ["tests/**"],
                                       "context_files": ["src/**", "tests/**"],
                                       "acceptance": [{"id": "unit", "argv": ["python", "-m", "unittest", "discover", "-s", "tests"]}]})
    outcome: dict = {}
    try:
        plan = orch.plan(task, deadline_seconds=180.0)
        outcome["prompt_artifact"] = smoke_store.get_artifact(plan["prompt_id"])["payload"]
        smoke_store.transition(task, "AWAITING_APPROVAL")
        smoke_store.approve(task, minutes=30)
        smoke_store.transition(task, "QUEUED")
        run = orch.create_run(task, budget={"max_attempts": 1, "max_wall_seconds": 600, "max_budget_usd": max_budget_usd,
                                            "command_timeout": 120})
        status = orch.run_once(run["run_id"])
        outcome["run"] = {k: status[k] for k in ("state", "exit_reason", "task_state", "usage")}
        outcome["artifacts"] = [a["kind"] for a in reversed(smoke_store.list_artifacts(task_id=task))]
        ver = next((smoke_store.get_artifact(a["id"])["payload"] for a in smoke_store.list_artifacts(task_id=task, kind="verification_report")), None)
        outcome["verification"] = ver
        outcome["network_denied"] = [e for e in status["events"] if e["kind"] == "network.denied"]
        outcome["ok"] = status["task_state"] == "COMPLETED"
    except (ZenithError, AdapterError) as exc:
        outcome["ok"] = False
        outcome["error"] = {"kind": getattr(exc, "kind", "zenith"), "detail": str(exc)[:500]}
    with store.write() as con:
        _record_event(con, "system", "smoke", "smoke.live_full_run",
                      {"director": director_name, "implementer": implementer_name, "ok": outcome.get("ok"),
                       "dir": str(base), "usage": outcome.get("run", {}).get("usage")}, actor="owner")
    return {**result, "live_call_performed": True, "dir": str(base), "outcome": outcome}
