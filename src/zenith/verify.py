"""Independent, deterministic verification of an implementation run.

Nothing here reads model text to decide pass/fail. Inputs are the worktree, Git, the sandboxed
exit codes of the owner's acceptance commands, file hashes and stored evidence. The implementer's
report is compared against reality and mismatches are recorded as findings; it never counts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import sandbox, worktree
from .contracts import glob_to_regex, path_allowed, validate
from .kernel import Store


def _artifact_matches(root: Path, pattern: str) -> list[dict]:
    rx = glob_to_regex(pattern)
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith(".git/"):
            continue
        if rx.match(rel):
            out.append({"path": rel, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size})
    return out


def verify_run(store: Store, run: dict, task: dict, wt_path: Path, caps: sandbox.Capabilities,
               report: dict | None, context_head: str | None, timeout: float) -> dict:
    """Return a validated `verification_report`; records one TEST_VERIFICATION evidence row per command."""
    scope = json.loads(task["scope"] or "{}")
    allowed = scope.get("allowed_files", [])
    excluded = scope.get("excluded_files", [])
    findings: list[str] = []

    observed_head = worktree.head_of(wt_path)
    if observed_head != run["base_head"]:
        findings.append(f"worktree HEAD moved from {run['base_head'][:7]} to {observed_head[:7]} "
                        "(commits are not authorized for implementation runs)")

    changed = worktree.changed_paths(wt_path)
    out_of_scope = [p for p in changed if not path_allowed(p, allowed, excluded)]
    if not allowed:
        findings.append("task scope has no allowed_files: every change is out of scope")
    if out_of_scope:
        findings.append("out-of-scope changes: " + ", ".join(out_of_scope[:20]))
    if not changed:
        findings.append("no files changed in the worktree")

    commands = scope.get("acceptance", [])
    results: list[dict] = []
    evidence_ids: list[str] = []
    if not commands:
        findings.append("no acceptance commands configured: cannot verify behaviour")
    for i, cmd in enumerate(commands):
        command_id = cmd.get("id") or f"acceptance-{i}"
        try:
            res = sandbox.run_isolated(cmd["argv"], wt_path, timeout=timeout, caps=caps, network=False)
        except sandbox.SandboxUnavailable as exc:
            findings.append(f"{command_id}: {exc}")
            results.append({"command_id": command_id, "argv": cmd["argv"], "exit_code": None, "error": str(exc)})
            continue
        expected = int(cmd.get("expected_exit", 0))
        ok = res.exit_code == expected
        if res.timed_out:
            findings.append(f"{command_id}: timed out after {timeout}s")
        elif res.truncated:
            findings.append(f"{command_id}: output exceeded the cap; result not trusted")
        elif not ok:
            findings.append(f"{command_id}: exit {res.exit_code}, expected {expected}")
        rec = {"command_id": command_id, **res.as_dict(), "expected_exit": expected, "passed": ok}
        results.append(rec)
        eid = store.record_test_verification(task["project_id"], run["source_id"], command_id,
                                             res.exit_code if res.exit_code is not None else -1,
                                             observed_head, output_digest=res.output_digest)
        evidence_ids.append(eid)

    artifacts: list[dict] = []
    for pattern in scope.get("artifacts", []):
        matches = _artifact_matches(wt_path, pattern)
        if not matches:
            findings.append(f"expected artifact missing: {pattern}")
        artifacts.extend(matches)

    context_fresh = context_head == run["base_head"]
    if not context_fresh:
        findings.append("context evidence head differs from the run's base revision")

    claims_mismatch: list[str] = []
    if report:
        claimed = set(report.get("files_changed", []))
        if claimed != set(changed):
            claims_mismatch.append(f"files_changed claimed {sorted(claimed)} vs observed {changed}")
        for tc in report.get("tests_claimed", []):
            actual = next((r for r in results if r["command_id"] == tc.get("command_id")), None)
            if tc.get("claimed_pass") and not (actual and actual.get("passed")):
                claims_mismatch.append(f"claimed {tc.get('command_id')} passed; verification says otherwise")
        if report.get("claimed_status") != "ready_for_verification":
            findings.append(f"implementer claimed status {report.get('claimed_status')!r}")

    passed = (not findings and bool(commands) and all(r.get("passed") for r in results))
    return validate("verification_report", {
        "passed": passed, "base_head": run["base_head"], "observed_head": observed_head,
        "changed_paths": changed, "out_of_scope_paths": out_of_scope, "commands": results,
        "artifacts": artifacts, "findings": findings, "evidence_ids": evidence_ids,
        "context_fresh": context_fresh, "claims_mismatch": claims_mismatch,
    })
