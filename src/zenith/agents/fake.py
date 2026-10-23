"""Deterministic fake adapters driven by a script. No model, no network, no credentials.

A script is a plain dict (loadable from JSON) describing what each stage returns, so tests and
the POC can exercise success, invalid output, timeouts, crashes, out-of-scope edits, false
claims and reviewer disagreement without any live provider.
"""
from __future__ import annotations

import time
from pathlib import Path

from .base import AdapterError, AgentCapabilities, AuthStatus, RunContext

_CAPS = AgentCapabilities(streaming=False, resume=False, structured_output=True, budget_cap=True,
                          sandbox_modes=("none",), live=False)


def _behaviour(ctx: RunContext, spec: dict) -> None:
    """Common failure modes a script can request for any stage."""
    mode = spec.get("mode", "ok")
    if mode == "auth_error":
        raise AdapterError("auth", "fake provider: not logged in")
    if mode == "crash":
        raise RuntimeError("fake adapter crashed")
    if mode == "hang":  # for crash/stale-lease tests: the worker process is killed meanwhile
        time.sleep(60)
    if mode == "timeout":
        # Sleep slightly past the stage deadline; the orchestrator classifies the overrun.
        time.sleep(min(ctx.deadline_seconds + 0.2, 5.0))
    if mode == "wait_for_cancel":
        for _ in range(50):
            if ctx.should_cancel():
                raise AdapterError("cancelled", "fake adapter observed cancellation")
            time.sleep(0.02)


class FakeDirector:
    name = "fake-director"

    def __init__(self, script: dict | None = None):
        self.script = script or {}
        self.calls: list[str] = []

    def capabilities(self) -> AgentCapabilities:
        return _CAPS

    def check_auth(self) -> AuthStatus:
        if self.script.get("auth_ok", True):
            return AuthStatus(True, "fake director: no credentials needed")
        return AuthStatus(False, "fake director: configured as unauthenticated")

    def plan(self, ctx: RunContext, context_package: dict) -> dict:
        self.calls.append("plan")
        spec = self.script.get("plan", {})
        _behaviour(ctx, spec)
        if spec.get("mode") == "invalid_output":
            return {"architecture_proposal": "not an object", "implementation_prompt": None}
        task = context_package["task"]
        proposal = spec.get("proposal") or {
            "summary": f"Fix '{task['title']}' with the smallest change that satisfies the acceptance criteria.",
            "decisions": [{"title": "Minimal change", "decision": "Edit only the failing module",
                           "rationale": "Keeps the diff reviewable"}],
            "risks": ["Hidden coupling with other modules"],
            "decomposition": [{"step": 1, "description": "Apply the fix"}, {"step": 2, "description": "Run tests"}],
        }
        prompt = spec.get("prompt") or {
            "objective": task["objective"] or task["title"],
            "instructions": ["Make the unit tests pass without changing the tests."],
            "architectural_constraints": ["No new dependencies", "No network access"],
            "acceptance_criteria": ["All acceptance commands exit 0"],
            "test_expectations": ["Existing tests remain green"],
            "output_requirements": ["Return an implementation_report with files_changed"],
            "proposed_scope": spec.get("proposed_scope", ["src/**"]),
        }
        return {"architecture_proposal": proposal, "implementation_prompt": prompt}

    def review(self, ctx: RunContext, context_package: dict, prompt: dict, report: dict,
               verification: dict) -> dict:
        self.calls.append("review")
        spec = self.script.get("review", {})
        _behaviour(ctx, spec)
        if spec.get("mode") == "invalid_output":
            return {"verdict": "maybe"}
        if spec.get("mode") == "disagree":  # tests pass, but the director wants changes
            return {"verdict": "correction_required", "summary": "Tests pass but naming is unclear.",
                    "findings": [{"severity": "P2", "detail": "Rename helper for clarity"}],
                    "next_step": {"kind": "revise_prompt", "reason": "quality",
                                  "revised_prompt": {**_strip(prompt), "revision_reason": "Improve naming",
                                                     "instructions": prompt["instructions"] + ["Rename helper"]}}}
        if verification["passed"]:
            return {"verdict": "approve", "summary": "Verified evidence matches the acceptance criteria.",
                    "findings": [], "next_step": {"kind": "complete", "reason": "verification passed"}}
        revised = {**_strip(prompt), "revision_reason": "Previous attempt failed verification",
                   "instructions": prompt["instructions"] + [
                       "Previous failure: " + "; ".join(verification["findings"])[:500]]}
        return {"verdict": "correction_required", "summary": "Verification failed; corrective prompt attached.",
                "findings": [{"severity": "P1", "detail": f} for f in verification["findings"]],
                "next_step": {"kind": "revise_prompt", "reason": "verification failed", "revised_prompt": revised}}


def _strip(prompt: dict) -> dict:
    """Director-authored fields only: Zenith's stamped fields are never echoed back."""
    keep = ("objective", "instructions", "architectural_constraints", "acceptance_criteria",
            "test_expectations", "output_requirements", "proposed_scope", "proposed_exclusions")
    return {k: prompt[k] for k in keep if k in prompt}


class FakeImplementer:
    name = "fake-implementer"

    def __init__(self, script: dict | None = None):
        self.script = script or {}
        self.calls: list[str] = []

    def capabilities(self) -> AgentCapabilities:
        return _CAPS

    def check_auth(self) -> AuthStatus:
        return AuthStatus(True, "fake implementer: no credentials needed")

    def implement(self, ctx: RunContext, prompt: dict) -> dict:
        self.calls.append("implement")
        spec = self.script.get("implement", {})
        _behaviour(ctx, spec)
        if spec.get("mode") == "invalid_output":
            return {"claimed_status": "ready_for_verification"}  # missing required fields
        assert ctx.worktree is not None
        for rel, content in spec.get("writes", {}).items():
            target = (ctx.worktree / rel)
            if not target.resolve().is_relative_to(ctx.worktree.resolve()):
                raise AdapterError("crash", "fake implementer refused to write outside the worktree")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        return {
            "claimed_status": spec.get("claimed_status", "ready_for_verification"),
            "summary": spec.get("summary", "Applied the scripted change."),
            "files_changed": spec.get("files_claimed", sorted(spec.get("writes", {}))),
            "tests_claimed": spec.get("tests_claimed", [{"command_id": "acceptance-0", "claimed_pass": True}]),
            "blocking_questions": spec.get("blocking_questions", []),
        }

    def cancel(self, ctx: RunContext) -> None:
        self.calls.append("cancel")
