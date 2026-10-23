"""Minimal dependency-free Zenith CLI; no unapproved autonomous operations."""
from __future__ import annotations
import argparse
import json
import shutil
import sys
from .kernel import MANUAL_EVIDENCE_ORIGINS, Store, ZenithError
from . import __version__


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zenith", description="Zenith local-first safe kernel")
    p.add_argument("--version", action="version", version=f"zenith {__version__}")
    cmds = p.add_subparsers(dest="cmd", required=True)
    cmds.add_parser("init", help="Initialize the local SQLite database")
    cmds.add_parser("doctor", help="Read-only check of installed local tools")

    projects = cmds.add_parser("project", help="Manage project registry").add_subparsers(dest="sub", required=True)
    a = projects.add_parser("add")
    a.add_argument("--name", required=True)
    a.add_argument("--path", required=True)
    a.add_argument("--category", default="personal")
    projects.add_parser("list")
    sh = projects.add_parser("show")
    sh.add_argument("--id", required=True)
    srcs = projects.add_parser("sources", help="Manage the directories that make up a project").add_subparsers(
        dest="src_sub", required=True)
    sa = srcs.add_parser("add")
    sa.add_argument("--project", required=True)
    sa.add_argument("--path", required=True)
    sa.add_argument("--label")
    sl = srcs.add_parser("list")
    sl.add_argument("--project", required=True)
    sl.add_argument("--all", action="store_true", help="include removed sources")
    sr = srcs.add_parser("remove", help="Detach a source (the directory is never touched)")
    sr.add_argument("--source", required=True)
    sm = srcs.add_parser("move", help="Point a source at a new directory; IDs stay stable")
    sm.add_argument("--source", required=True)
    sm.add_argument("--path", required=True)
    sd = srcs.add_parser("diff", help="Compare the two latest snapshots of a source")
    sd.add_argument("--source", required=True)

    tasks = cmds.add_parser("task", help="Manage task lifecycle").add_subparsers(dest="sub", required=True)
    ta = tasks.add_parser("add")
    ta.add_argument("--project", required=True)
    ta.add_argument("--title", required=True)
    ta.add_argument("--objective", default="")
    ta.add_argument("--risk", default="L0", choices=["L0", "L1", "L2", "L3", "L4"])
    ta.add_argument("--due")
    tl = tasks.add_parser("list")
    tl.add_argument("--project")
    tl.add_argument("--state")
    tl.add_argument("--open", action="store_true", help="hide COMPLETED/CANCELLED/FAILED")
    tl.add_argument("--overdue", action="store_true")
    tl.add_argument("--due-within", type=int, metavar="DAYS", help="open tasks due within DAYS (incl. overdue)")
    tl.add_argument("--blocked", action="store_true", help="only tasks with unfinished dependencies")
    td = tasks.add_parser("depend", help="Task --id cannot be queued until --on is COMPLETED")
    td.add_argument("--id", required=True)
    td.add_argument("--on", required=True)
    tu = tasks.add_parser("undepend")
    tu.add_argument("--id", required=True)
    tu.add_argument("--on", required=True)
    tt = tasks.add_parser("transition")
    tt.add_argument("--id", required=True)
    tt.add_argument("--to", required=True)
    ap = tasks.add_parser("approve")
    ap.add_argument("--id", required=True)
    ap.add_argument("--minutes", type=int, default=60)

    sc = cmds.add_parser("scan", help="Read-only metadata scan of a project's sources")
    sc.add_argument("--project", required=True)
    scope = sc.add_mutually_exclusive_group()
    scope.add_argument("--source", help="scan one source (default: the primary source)")
    scope.add_argument("--all", action="store_true", help="scan every source of the project")

    evd = cmds.add_parser("evidence", help="Inspect or attach evidence").add_subparsers(dest="ev_sub", required=True)
    el = evd.add_parser("list")
    el.add_argument("--project", required=True)
    el.add_argument("--origin")
    el.add_argument("--limit", type=int, default=50)
    ea = evd.add_parser("add", help="Attach a claim; never VERIFIED or OBSERVED")
    ea.add_argument("--project", required=True)
    ea.add_argument("--origin", required=True, choices=MANUAL_EVIDENCE_ORIGINS)
    ea.add_argument("--summary", required=True)
    ea.add_argument("--source-ref")
    ea.add_argument("--at", help="ISO 8601 observation time (default now; naive = Europe/Istanbul)")

    ck = cmds.add_parser("checkin", help="Record a short human update (REPORTED, not verified)")
    ck.add_argument("--project", required=True)
    ck.add_argument("--note", required=True)
    ck.add_argument("--node", default="local", help="machine the update comes from")
    ck.add_argument("--at", help="ISO 8601 time the update was made (for offline machines)")

    br = cmds.add_parser("brief", help="Deterministic evidence-cited status report")
    br.add_argument("--project")
    br.add_argument("--format", choices=["json", "text"], default="json")

    bk = cmds.add_parser("backup", help="Consistent database copy via the SQLite backup API")
    bk.add_argument("--out", required=True, help="new file; existing files are never overwritten")
    ev = cmds.add_parser("events")
    ev.add_argument("--limit", type=int, default=20)
    return p


def output(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> None:
    p = make_parser()
    args = p.parse_args()
    store = Store()
    try:
        if args.cmd == "init":
            output({"db_path": store.init(), "initialized": True})
        elif args.cmd == "doctor":
            output({"database_exists": store.db_path.exists(),
                    "db_path": str(store.db_path),
                    "schema_version": store.schema_version(),
                    "tools": {x: bool(shutil.which(x)) for x in ("git", "tmux", "hyprctl", "claude", "codex")},
                    "note": "Tool presence is not proof of authentication or permission."})
        elif args.cmd == "project":
            if args.sub == "add":
                output({"project_id": store.add_project(args.name, args.path, args.category)})
            elif args.sub == "list":
                output(store.list_projects())
            elif args.sub == "show":
                output(store.get_project(args.id))
            elif args.sub == "sources":
                if args.src_sub == "add":
                    output({"source_id": store.add_source(args.project, args.path, args.label)})
                elif args.src_sub == "list":
                    output(store.list_sources(args.project, args.all))
                elif args.src_sub == "remove":
                    output({"removed": store.remove_source(args.source)})
                elif args.src_sub == "move":
                    output(store.move_source(args.source, args.path))
                elif args.src_sub == "diff":
                    output(store.compare_snapshots(args.source))
        elif args.cmd == "task":
            if args.sub == "add":
                output({"task_id": store.add_task(args.project, args.title, args.objective, args.risk, args.due)})
            elif args.sub == "list":
                output(store.list_tasks(args.project, state=args.state, open_only=args.open,
                                        overdue=args.overdue, due_within_days=args.due_within,
                                        blocked=args.blocked))
            elif args.sub == "depend":
                store.add_dependency(args.id, args.on)
                output({"task_id": args.id, "depends_on": args.on})
            elif args.sub == "undepend":
                store.remove_dependency(args.id, args.on)
                output({"task_id": args.id, "removed_dependency": args.on})
            elif args.sub == "transition":
                output({"state": store.transition(args.id, args.to)})
            elif args.sub == "approve":
                output({"approval_id": store.approve(args.id, minutes=args.minutes)})
        elif args.cmd == "scan":
            if args.source:
                output(store.scan_source(args.source))
            elif args.all:
                output(store.scan_sources(args.project))
            else:
                output(store.scan_project(args.project))
        elif args.cmd == "evidence":
            if args.ev_sub == "list":
                output(store.list_evidence(args.project, args.origin, args.limit))
            else:
                output(store.add_evidence(args.project, args.origin, args.summary, args.source_ref, args.at))
        elif args.cmd == "checkin":
            output(store.checkin(args.project, args.note, args.node, args.at))
        elif args.cmd == "brief":
            report = store.brief(args.project)
            if args.format == "text":
                from .brief import render_text
                print(render_text(report))
            else:
                output(report)
        elif args.cmd == "backup":
            output({"backup": store.backup(args.out)})
        elif args.cmd == "events":
            output(store.events(args.limit))
    except (ZenithError, OSError, ValueError) as exc:
        print(f"Zenith error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
