"""Deterministic, evidence-cited project briefing. No model, no network, no writes."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import dates
from .kernel import ZenithError, _load_json

SOURCE_STALE_DAYS = 7
CHECKIN_STALE_DAYS = 14
DUE_SOON_DAYS = 7

STATUS_MEANING = {
    "UNKNOWN": "no evidence recorded",
    "REPORTED_ONLY": "only human/document/model claims; nothing observed or verified",
    "OBSERVED": "repository activity observed; no passing test verification (commits are not completion)",
    "VERIFIED": "latest passing test verification matches the currently observed clean revision",
    "VERIFICATION_STALE": "a passing verification exists but the revision has moved or cannot be confirmed",
    "VERIFICATION_FAILING": "latest test verification failed",
}


def _age_days(iso: str | None, now: datetime) -> float | None:
    when = dates.safe_parse_utc(iso)
    return None if when is None else round((now - when).total_seconds() / 86400, 1)


def _source_view(store, src: dict, now: datetime) -> dict:
    with store.connection() as con:
        snap = con.execute("SELECT * FROM snapshots WHERE source_id=? ORDER BY collected_at DESC, rowid DESC LIMIT 1",
                           (src["id"],)).fetchone()
        ev = (con.execute("SELECT id FROM evidence WHERE source_ref=?", (f"snapshot:{snap['id']}",)).fetchone()
              if snap else None)
    view = {"source_id": src["id"], "label": src["label"], "path": src["path"], "node_id": src["node_id"],
            "state": "never_scanned", "last_seen_at": None, "age_days": None, "head": None,
            "branch": None, "tracked_changed_count": None, "evidence_id": ev["id"] if ev else None}
    if snap is None:
        return view
    try:
        d = _load_json(snap["details"], "snapshot")
    except ZenithError:
        view.update(state="corrupt_record", last_seen_at=snap["collected_at"])
        return view
    view.update(last_seen_at=snap["last_seen_at"] or snap["collected_at"], head=d.get("head"),
                branch=d.get("branch"), tracked_changed_count=d.get("tracked_changed_count"))
    view["age_days"] = _age_days(view["last_seen_at"], now)
    if d.get("source_status") != "available":
        view["state"] = "unavailable"
    elif view["age_days"] is not None and view["age_days"] > SOURCE_STALE_DAYS:
        view["state"] = "stale"
    else:
        view["state"] = "fresh"
    return view


def _task_view(t: dict) -> dict:
    return {"task_id": t["id"], "title": t["title"], "state": t["state"],
            "due_local": t["due_local"], "blocked_by": t["blocked_by"]}


def _project_brief(store, project: dict, now: datetime) -> dict:
    pid = project["id"]
    sources = [_source_view(store, s, now) for s in project["sources"]]
    by_origin = {o: store.list_evidence(pid, o, 1) for o in ("TEST_VERIFICATION", "HUMAN_REPORT")}
    verification = by_origin["TEST_VERIFICATION"][0] if by_origin["TEST_VERIFICATION"] else None
    report = by_origin["HUMAN_REPORT"][0] if by_origin["HUMAN_REPORT"] else None
    observed = [s for s in sources if s["state"] in ("fresh", "stale")]
    claims = [e for e in store.list_evidence(pid, limit=500) if e["origin"] in ("HUMAN_REPORT", "DOCUMENT", "MODEL_INFERENCE")]

    caveats: list[str] = []
    basis: list[str] = []
    applicability = None
    if verification:
        basis.append(verification["id"])
        vd = verification["details"]
        src = next((s for s in sources if s["source_id"] == vd.get("source_id")), None)
        # Scope-aware freshness: only the source that was verified matters. Other sources being
        # unavailable never downgrade a valid verification; the verification row itself is never altered.
        if not vd.get("passed"):
            status, applicability = "VERIFICATION_FAILING", "failed"
        elif src is None:
            status, applicability = "VERIFICATION_STALE", "source_removed"
        elif src["state"] == "unavailable":
            status, applicability = "VERIFICATION_STALE", "source_unavailable"
        elif src["state"] in ("never_scanned", "corrupt_record"):
            status, applicability = "VERIFICATION_STALE", "source_unknown"
        elif src["head"] != vd.get("head"):
            status, applicability = "VERIFICATION_STALE", "head_moved"
        elif src["tracked_changed_count"]:
            status, applicability = "VERIFICATION_STALE", "uncommitted_changes"
        elif src["state"] == "stale":
            status, applicability = "VERIFICATION_STALE", "scan_stale"
        else:
            status, applicability = "VERIFIED", "current"
            basis.append(src["evidence_id"])
        if status == "VERIFICATION_STALE":
            caveats.append("Verified revision %s is not confirmed as the current clean revision (%s)."
                           % (str(vd.get("head"))[:7], applicability))
    elif observed:
        status = "OBSERVED"
        basis += [s["evidence_id"] for s in observed if s["evidence_id"]]
    elif claims:
        status = "REPORTED_ONLY"
        basis.append(claims[0]["id"])
    else:
        status = "UNKNOWN"

    unknowns: list[str] = []
    for s in sources:
        name = s["label"] or s["path"]
        if s["state"] == "never_scanned":
            unknowns.append(f"Source '{name}' has never been scanned: repository state unknown.")
        elif s["state"] == "unavailable":
            unknowns.append(f"Source '{name}' was unavailable at its last scan ({s['last_seen_at']}): current state unknown.")
        elif s["state"] == "corrupt_record":
            unknowns.append(f"Source '{name}': latest snapshot record is corrupt; state unknown until rescanned.")
        elif s["state"] == "stale":
            unknowns.append(f"Source '{name}' last observed {s['age_days']} days ago: may be out of date.")
    if not sources:
        unknowns.append("No sources registered: nothing can be observed.")
    if not verification:
        unknowns.append("No test verification recorded: completion is unverified.")

    report_view = None
    if report:
        with store.connection() as con:
            newer = [r["id"] for r in con.execute(
                "SELECT id FROM evidence WHERE project_id=? AND origin IN ('REPOSITORY_OBSERVATION','TEST_VERIFICATION') "
                "AND last_confirmed_at>? ORDER BY last_confirmed_at DESC, seq DESC LIMIT 5", (pid, report["observed_at"]))]
        age = _age_days(report["observed_at"], now)
        report_view = {"evidence_id": report["id"], "confidence": report["confidence"],
                       "observed_at": report["observed_at"], "node_id": report["node_id"],
                       "note": report["summary"], "age_days": age,
                       "stale": age is not None and age > CHECKIN_STALE_DAYS,
                       "newer_evidence": newer,
                       "verified": False}  # a human report can never be verification

    tasks = store.list_tasks(pid, open_only=True, now=now)
    soon_cutoff = now + timedelta(days=DUE_SOON_DAYS)
    overdue = [t for t in tasks if t["overdue"]]
    due_soon = [t for t in tasks if not t["overdue"] and dates.safe_parse_utc(t["due_at"])
                and dates.safe_parse_utc(t["due_at"]) <= soon_cutoff]
    blocked = [t for t in tasks if t["blocked_by"]]

    actions: list[dict] = []
    for t in overdue:
        actions.append({"action": f"Task '{t['title']}' is overdue (was due {t['due_local']}).", "citations": [t["id"]]})
    for t in due_soon:
        actions.append({"action": f"Task '{t['title']}' is due {t['due_local']}.", "citations": [t["id"]]})
    for s in sources:
        name = s["label"] or s["path"]
        if s["state"] in ("never_scanned", "stale"):
            actions.append({"action": f"Scan source '{name}' (zenith scan --project {pid} --source {s['source_id']}).",
                            "citations": [s["evidence_id"]] if s["evidence_id"] else []})
        elif s["state"] == "corrupt_record":
            actions.append({"action": f"Rescan source '{name}': its stored snapshot could not be read.", "citations": []})
        elif s["state"] == "unavailable":
            actions.append({"action": f"Source '{name}' is unavailable: reconnect it, move it, or remove it.",
                            "citations": [s["evidence_id"]] if s["evidence_id"] else []})
    if status == "VERIFICATION_STALE":
        actions.append({"action": "Revision changed since the last passing verification: re-verify before claiming progress.",
                        "citations": basis})
    if report_view and not report_view["newer_evidence"] and status != "VERIFIED":
        actions.append({"action": "Latest human report is unverified: observe or test before treating it as progress.",
                        "citations": [report_view["evidence_id"]]})
    if report_view is None or report_view["stale"]:
        actions.append({"action": "No recent check-in: ask for a short update (zenith checkin).",
                        "citations": [report_view["evidence_id"]] if report_view else []})
    if not sources:
        actions.append({"action": "Register a source (zenith project sources add).", "citations": []})

    return {"project_id": pid, "name": project["name"], "category": project["category"],
            "status": {"label": status, "meaning": STATUS_MEANING[status], "basis": basis, "caveats": caveats},
            "sources": sources, "latest_human_report": report_view,
            "latest_verification": ({"evidence_id": verification["id"], "observed_at": verification["observed_at"],
                                     "summary": verification["summary"], "passed": verification["details"].get("passed"),
                                     "head": verification["details"].get("head"), "source_id": verification["details"].get("source_id"),
                                     "command_id": verification["details"].get("command_id"),
                                     "applies": applicability == "current", "applicability": applicability,
                                     "current_head": next((s["head"] for s in sources if s["source_id"] == verification["details"].get("source_id")), None)}
                                    if verification else None),
            "tasks": {"open": len(tasks), "overdue": [_task_view(t) for t in overdue],
                      "due_soon": [_task_view(t) for t in due_soon], "blocked": [_task_view(t) for t in blocked]},
            "unknowns": unknowns, "next_actions": actions}


def build_brief(store, project_id: str | None, now: datetime) -> dict:
    projects = [store.get_project(project_id)] if project_id else store.list_projects()
    return {"generated_at": dates.iso_utc(now), "timezone": "Europe/Istanbul",
            "note": "Deterministic report from stored evidence only. REPORTED = human claim, OBSERVED = Zenith "
                    "read metadata, VERIFIED = deterministic test verification. Unknown stays unknown.",
            "projects": [_project_brief(store, p, now) for p in projects]}


def render_text(brief: dict) -> str:
    lines = [f"Zenith brief — {brief['generated_at']}"]
    for p in brief["projects"]:
        st = p["status"]
        lines += ["", f"== {p['name']} [{p['project_id']}]", f"status: {st['label']} — {st['meaning']}"]
        lines += [f"  caveat: {c}" for c in st["caveats"]]
        for s in p["sources"]:
            head = f" HEAD {s['head'][:7]}" if s["head"] else ""
            lines.append(f"  source {s['label'] or s['path']}: {s['state']}{head}"
                         + (f" (seen {s['last_seen_at']})" if s["last_seen_at"] else ""))
        r = p["latest_human_report"]
        if r:
            lines.append(f"  last human report [REPORTED, unverified] {r['observed_at']}: {r['note']}"
                         + (" (STALE)" if r["stale"] else ""))
        t = p["tasks"]
        lines.append(f"  tasks: {t['open']} open, {len(t['overdue'])} overdue, {len(t['due_soon'])} due soon, "
                     f"{len(t['blocked'])} blocked")
        lines += [f"  unknown: {u}" for u in p["unknowns"]]
        for a in p["next_actions"]:
            cite = f" [{', '.join(c[:8] for c in a['citations'])}]" if a["citations"] else ""
            lines.append(f"  -> {a['action']}{cite}")
    return "\n".join(lines)
