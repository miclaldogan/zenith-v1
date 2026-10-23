"""Context retrieval for Misa: facts with provenance, from the kernel's public services only.

Everything returned here is read from the store (projects, sources, evidence, tasks, decisions,
runs, approvals, brief). Nothing is inferred; missing information is reported as missing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .. import dates
from ..kernel import Store, ZenithError

PROVENANCE = {"REPORTED": "human report, unverified", "OBSERVED": "observed by Zenith", "VERIFIED": "verified by tests",
              "INFERRED": "model inference", "UNKNOWN": "unknown"}


@dataclass
class Fact:
    key: str
    value: object
    cite: str = ""            # short id: ev:xxxxxxxx task:… run:… dec:…
    confidence: str = ""      # REPORTED/OBSERVED/VERIFIED/INFERRED
    when: str | None = None


@dataclass
class Answer:
    intent: str
    facts: list[Fact] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    project: dict | None = None
    data: dict = field(default_factory=dict)


def _short(prefix: str, ident: str | None) -> str:
    return f"{prefix}:{ident[:8]}" if ident else ""


def _local(iso: str | None) -> str | None:
    when = dates.safe_parse_utc(iso)
    return dates.to_local(when)[:16].replace("T", " ") if when else None


class Retriever:
    def __init__(self, store: Store):
        self.store = store

    # ---- projects --------------------------------------------------------------------------
    def projects(self) -> Answer:
        ans = Answer("projects")
        plist = self.store.list_projects()
        if not plist:
            ans.unknowns.append("no_projects")
            return ans
        brief = {p["project_id"]: p for p in self.store.brief()["projects"]}
        rows = []
        for i, p in enumerate(plist, 1):
            b = brief.get(p["id"])
            rows.append({"n": i, "id": p["id"], "name": p["name"], "category": p["category"],
                         "status": b["status"]["label"] if b else "UNKNOWN", "sources": len(p["sources"]),
                         "open_tasks": b["tasks"]["open"] if b else 0, "overdue": len(b["tasks"]["overdue"]) if b else 0})
        ans.data["rows"] = rows
        return ans

    def resolve_project(self, ref: str) -> tuple[dict | None, list[dict]]:
        """Exact name/id, else a unique partial match. Returns (match, candidates); never guesses.
        Numbers are NOT resolved here: they belong to the listing the owner saw (REPL)."""
        ref = ref.strip().lower().rstrip("'’")
        plist = self.store.list_projects()
        if not ref or ref.isdigit():
            return None, []
        exact = [p for p in plist if p["name"].lower() == ref or p["id"] == ref]
        if len(exact) == 1:
            return exact[0], exact
        partial = [p for p in plist if ref in p["name"].lower() or p["id"].startswith(ref)]
        return (partial[0], partial) if len(partial) == 1 else (None, partial)

    _STOP = {"about", "which", "there", "their", "these", "those", "after", "before", "today", "tomorrow", "yarın",
             "bugün", "için", "sonra", "önce", "zaman", "kadar", "biraz", "şimdi", "hakkında", "update", "fixed",
             "finished", "started", "write", "yazdım", "yaptım", "bitti", "devam"}

    def _significant(self, text: str) -> set[str]:
        return {w for w in re.findall(r"[\wçğıöşü]+", text.lower()) if len(w) >= 5 and not w.isdigit() and w not in self._STOP}

    def projects_mentioned(self, text: str, exclude: str | None = None) -> list[dict]:
        """Other registered projects a free-text note points at: by project name (≥ 4 chars) or by a
        significant word of one of their OPEN task titles. Used to refuse ambiguous check-ins."""
        low = text.lower()
        words = self._significant(text)
        out = []
        for p in self.store.list_projects():
            if p["id"] == exclude:
                continue
            name = p["name"].lower()
            if len(name) >= 4 and re.search(rf"(^|[^\wçğıöşü]){re.escape(name)}", low):
                out.append({**p, "via": "name"})
                continue
            for t in self.store.list_tasks(p["id"], open_only=True):
                hit = words & self._significant(t["title"])
                if hit:
                    out.append({**p, "via": f"task '{t['title']}'"})
                    break
        return out

    # ---- project views -----------------------------------------------------------------------
    def status(self, project_id: str) -> Answer:
        b = self.store.brief(project_id)["projects"][0]
        ans = Answer("status", project=self.store.get_project(project_id), data={"brief": b})
        ans.facts.append(Fact("status", b["status"]["label"], " ".join(_short("ev", x) for x in b["status"]["basis"])))
        for u in b["unknowns"]:
            ans.unknowns.append(u)
        return ans

    def last(self, project_id: str) -> Answer:
        """Where was this project left? Latest evidence by origin, latest decision, latest run."""
        ans = Answer("last", project=self.store.get_project(project_id))
        evidence = self.store.list_evidence(project_id, limit=50)
        if not evidence:
            ans.unknowns.append("no_evidence")
        seen_origin = set()
        for e in evidence:  # newest first; one per origin keeps the answer short
            if e["origin"] in seen_origin:
                continue
            seen_origin.add(e["origin"])
            ans.facts.append(Fact(e["origin"], e["summary"], _short("ev", e["id"]), e["confidence"], _local(e["observed_at"])))
        decisions = self.store.list_decisions(project_id, limit=1)
        if decisions:
            d = decisions[0]
            ans.facts.append(Fact("decision", f"{d['decision']} — {d['rationale'][:120]}", _short("dec", d["id"]),
                                  "OBSERVED", _local(d["created_at"])))
        runs = self._runs(project_id, limit=1)
        if runs:
            r = runs[0]
            ans.facts.append(Fact("run", f"{r['state']} ({r['exit_reason'] or r['stage'] or 'pending'})",
                                  _short("run", r["id"]), "OBSERVED", _local(r["ended_at"] or r["created_at"])))
        b = self.store.brief(project_id)["projects"][0]
        ans.data["status"] = b["status"]["label"]
        ans.data["verification"] = b["latest_verification"]
        ans.data["sources"] = b["sources"]
        ans.data["stale_sources"] = [s for s in b["sources"] if s["state"] in ("stale", "unavailable", "never_scanned", "corrupt_record")]
        return ans

    def evidence(self, project_id: str, limit: int = 8) -> Answer:
        ans = Answer("evidence", project=self.store.get_project(project_id))
        for e in self.store.list_evidence(project_id, limit=limit):
            ans.facts.append(Fact(e["origin"], e["summary"], _short("ev", e["id"]), e["confidence"], _local(e["observed_at"])))
        if not ans.facts:
            ans.unknowns.append("no_evidence")
        return ans

    def decisions(self, project_id: str, limit: int = 8) -> Answer:
        ans = Answer("decisions", project=self.store.get_project(project_id))
        for d in self.store.list_decisions(project_id, limit=limit):
            ans.facts.append(Fact(d["made_by"], f"{d['decision']} — {d['rationale'][:160]}", _short("dec", d["id"]),
                                  "OBSERVED", _local(d["created_at"])))
        if not ans.facts:
            ans.unknowns.append("no_decisions")
        return ans

    def tasks(self, project_id: str | None, now: datetime | None = None) -> Answer:
        now = now or datetime.now(timezone.utc)
        ans = Answer("tasks", project=self.store.get_project(project_id) if project_id else None)
        tasks = self.store.list_tasks(project_id, open_only=True, now=now)
        soon = now + timedelta(days=7)
        groups = {"overdue": [], "due_soon": [], "blocked": [], "awaiting_approval": [], "other": []}
        for t in tasks:
            due = dates.safe_parse_utc(t["due_at"])
            t = {**t, "due_local": t["due_local"][:16].replace("T", " ") if t["due_local"] else None}
            if t["overdue"]:
                groups["overdue"].append(t)
            elif t["state"] == "AWAITING_APPROVAL":
                groups["awaiting_approval"].append(t)
            elif t["blocked_by"]:
                groups["blocked"].append(t)
            elif due and due <= soon:
                groups["due_soon"].append(t)
            else:
                groups["other"].append(t)
        ans.data["groups"] = groups
        ans.data["total"] = len(tasks)
        if not tasks:
            ans.unknowns.append("no_open_tasks")
        return ans

    def sources(self, project_id: str) -> Answer:
        b = self.store.brief(project_id)["projects"][0]
        ans = Answer("sources", project=self.store.get_project(project_id), data={"sources": b["sources"]})
        if not b["sources"]:
            ans.unknowns.append("no_sources")
        return ans

    def _runs(self, project_id: str, limit: int = 5) -> list[dict]:
        with self.store.connection() as con:
            return [dict(r) for r in con.execute(
                "SELECT id,task_id,attempt,state,stage,exit_reason,created_at,ended_at FROM execution_runs "
                "WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, limit))]

    def runs(self, project_id: str, limit: int = 5) -> Answer:
        ans = Answer("runs", project=self.store.get_project(project_id))
        titles = {t["id"]: t["title"] for t in self.store.list_tasks(project_id)}
        for r in self._runs(project_id, limit):
            ans.facts.append(Fact(titles.get(r["task_id"], r["task_id"][:8]),
                                  f"attempt {r['attempt']}: {r['state']}" + (f" ({r['exit_reason']})" if r["exit_reason"] else ""),
                                  _short("run", r["id"]), "OBSERVED", _local(r["ended_at"] or r["created_at"])))
        if not ans.facts:
            ans.unknowns.append("no_runs")
        return ans

    def approvals(self, project_id: str | None) -> Answer:
        ans = Answer("approvals", project=self.store.get_project(project_id) if project_id else None)
        waiting = [t for t in self.store.list_tasks(project_id, state="AWAITING_APPROVAL")]
        reconcile = [t for t in self.store.list_tasks(project_id, state="RECONCILE_REQUIRED")]
        blocked = [t for t in self.store.list_tasks(project_id, state="BLOCKED")]
        ans.data.update(awaiting=waiting, reconcile=reconcile, blocked=blocked)
        if not (waiting or reconcile or blocked):
            ans.unknowns.append("nothing_pending")
        return ans

    def next_actions(self, project_id: str) -> Answer:
        b = self.store.brief(project_id)["projects"][0]
        ans = Answer("next", project=self.store.get_project(project_id), data={"actions": b["next_actions"], "status": b["status"]})
        if not b["next_actions"]:
            ans.unknowns.append("no_actions")
        return ans

    def opening(self, active_id: str | None = None) -> Answer:
        """Session opener: what is due, blocked, stale — across all projects. Also names the project
        with the most recent evidence, which is informational and never becomes the active one."""
        ans = Answer("opening")
        brief = self.store.brief()
        projects = brief["projects"]
        ans.data["project_count"] = len(projects)
        ans.data["active"] = next((p["name"] for p in projects if p["project_id"] == active_id), None)
        latest = None
        for p in projects:
            ev = self.store.list_evidence(p["project_id"], limit=1)
            if ev and (latest is None or ev[0]["observed_at"] > latest[1]):
                latest = (p["name"], ev[0]["observed_at"], p["project_id"])
        ans.data["most_recent"] = {"name": latest[0], "at": _local(latest[1]), "id": latest[2]} if latest else None
        ans.data["overdue"] = [(p["name"], t) for p in projects for t in p["tasks"]["overdue"]]
        ans.data["due_soon"] = [(p["name"], t) for p in projects for t in p["tasks"]["due_soon"]]
        ans.data["blocked"] = [(p["name"], t) for p in projects for t in p["tasks"]["blocked"]]
        ans.data["stale"] = [(p["name"], s) for p in projects for s in p["sources"]
                             if s["state"] in ("stale", "unavailable", "never_scanned", "corrupt_record")]
        ans.data["reconcile"] = self.store.list_tasks(state="RECONCILE_REQUIRED")
        ans.data["awaiting"] = self.store.list_tasks(state="AWAITING_APPROVAL")
        return ans

    def checkin(self, project_id: str, note: str) -> Answer:
        ans = Answer("checkin", project=self.store.get_project(project_id))
        try:
            result = self.store.checkin(project_id, note)
        except ZenithError as exc:
            ans.unknowns.append(f"error:{exc}")
            return ans
        ans.facts.append(Fact("HUMAN_REPORT", note, _short("ev", result["evidence_id"]), result["confidence"], _local(result["observed_at"])))
        ans.data["later_evidence"] = result["later_evidence"]
        return ans
