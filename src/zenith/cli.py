"""Minimal dependency-free Zenith CLI; no unapproved autonomous operations."""
from __future__ import annotations
import argparse
import json
import shlex
from pathlib import Path
import shutil
import sqlite3
import sys
from .kernel import MANUAL_EVIDENCE_ORIGINS, Store, ZenithError
from . import __version__


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zenith", description="Zenith local-first safe kernel")
    p.add_argument("--version", action="version", version=f"zenith {__version__}")
    cmds = p.add_subparsers(dest="cmd", required=False)
    ms = cmds.add_parser("misa", help="Interactive Misa session (default when no command is given)")
    ms.add_argument("--once", metavar="TEXT", help="answer one message and exit (non-interactive)")
    ms.add_argument("--eval", action="store_true", help="run conversation acceptance scenarios A/B/C on a throwaway DB (offline or mock)")
    ms.add_argument("--provider", default=None, help="conversation adapter for --eval (default: offline composer; e.g. fake-conversation)")
    ms.add_argument("--live-plan", action="store_true", help="print the owner-authorized live conversation test plan; makes no call")
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
    ta.add_argument("--allow", action="append", default=[], metavar="GLOB", help="files an implementer may change")
    ta.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    ta.add_argument("--acceptance", action="append", default=[], metavar="CMD",
                    help="acceptance command (split with shlex, run without a shell in the sandbox)")
    ta.add_argument("--artifact", action="append", default=[], metavar="GLOB", help="expected output artifact")
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
    ap = tasks.add_parser("approve", help="Grant one bounded, scope-hashed task.execute approval")
    ap.add_argument("--id", required=True)
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--max-uses", type=int, default=1)
    rv = tasks.add_parser("revoke")
    rv.add_argument("--approval", required=True)
    rv.add_argument("--reason", default="")
    tap = tasks.add_parser("approvals")
    tap.add_argument("--id", required=True)

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

    def adapter_args(sp):
        sp.add_argument("--director", default="fake-director")
        sp.add_argument("--implementer", default="fake-implementer")
        sp.add_argument("--script", help="JSON file with fake adapter scripts (testing only)")

    dr = cmds.add_parser("director", help="Engineering Director stages (mock providers only)").add_subparsers(
        dest="dir_sub", required=True)
    dp = dr.add_parser("plan", help="Context package -> architecture proposal + implementation prompt")
    dp.add_argument("--task", required=True)
    adapter_args(dp)

    rn = cmds.add_parser("run", help="Execution runs").add_subparsers(dest="run_sub", required=True)
    rc = rn.add_parser("create", help="Create a PENDING run for a QUEUED task (idempotent)")
    rc.add_argument("--task", required=True)
    rc.add_argument("--source")
    rc.add_argument("--max-attempts", type=int)
    rc.add_argument("--max-wall-seconds", type=int)
    adapter_args(rc)
    rs = rn.add_parser("status"); rs.add_argument("--run", required=True)
    rl = rn.add_parser("list"); rl.add_argument("--task"); rl.add_argument("--state")
    rx = rn.add_parser("cancel"); rx.add_argument("--run", required=True)
    rr = rn.add_parser("reconcile", help="Owner resolves a RECONCILE_REQUIRED run")
    rr.add_argument("--run", required=True)
    rr.add_argument("--outcome", required=True, choices=["retry", "blocked", "failed", "cancelled"])
    rr.add_argument("--reason", required=True)
    ra = rn.add_parser("accept", help="Owner accepts a verified run the reviewer disagreed with")
    ra.add_argument("--run", required=True)
    ra.add_argument("--reason", required=True)

    wk = cmds.add_parser("worker", help="Drive one run to completion, or reconcile stale leases")
    wk.add_argument("--run")
    wk.add_argument("--reconcile", action="store_true", help="mark runs with expired leases RECONCILE_REQUIRED")
    adapter_args(wk)

    ar = cmds.add_parser("artifacts", help="Director protocol artifacts").add_subparsers(dest="art_sub", required=True)
    al = ar.add_parser("list"); al.add_argument("--project"); al.add_argument("--task"); al.add_argument("--run"); al.add_argument("--kind")
    ash = ar.add_parser("show"); ash.add_argument("--id", required=True)
    dc = cmds.add_parser("decisions"); dc.add_argument("--project", required=True)
    ad = cmds.add_parser("adapters", help="Discovered adapters and their enablement").add_subparsers(dest="ad_sub")
    ad.add_parser("list")
    pr = ad.add_parser("probe", help="Non-live preflight: binary, auth file presence, sandbox, exact argv")
    pr.add_argument("--adapter", required=True)
    pr.add_argument("--version-check", action="store_true", help="also run `<cli> --version` inside the sandbox")
    idp = cmds.add_parser("identity", help="REQ-004 identities, roles and attribution").add_subparsers(dest="id_sub")
    idp.add_parser("show")
    cp = idp.add_parser("commit-preview", help="Preview a governed commit message; nothing is committed")
    cp.add_argument("--subject", required=True)
    cp.add_argument("--body", default="")
    cp.add_argument("--assistant-contributed", action="store_true")
    sm = cmds.add_parser("smoke", help="Live smoke test (REQUIRES explicit authorization flags; never automatic)")
    sm.add_argument("--adapter", required=True, help="director adapter (or the single adapter for a non-full smoke)")
    sm.add_argument("--repo", help="disposable repository (non-full smoke)")
    sm.add_argument("--full", action="store_true", help="director → prompt → implement → verify → review on a synthetic repo")
    sm.add_argument("--implementer", default="claude-code-implementer")
    sm.add_argument("--authorize-live-call", action="store_true")
    sm.add_argument("--max-budget-usd", type=float, default=0.25)
    sb = cmds.add_parser("sandbox", help="Isolation baseline").add_subparsers(dest="sb_sub", required=True)
    sb.add_parser("doctor")
    dm = cmds.add_parser("demo", help="Disposable demonstrations").add_subparsers(dest="demo_sub", required=True)
    poc = dm.add_parser("poc", help="Full mock-agent workflow in a throwaway repository")
    poc.add_argument("--dir", help="where to create the throwaway repo and state (default: temp dir)")
    poc.add_argument("--scenario", default="success",
                     choices=["success", "out_of_scope", "false_claim", "disagree", "crash"])
    ev = cmds.add_parser("events")
    ev.add_argument("--limit", type=int, default=20)
    return p


def _orchestration_command(store: Store, args) -> object:
    from .extensions import load
    from .orchestration import Orchestrator
    script = {}
    if getattr(args, "script", None):
        with open(args.script) as fh:
            script = json.load(fh)
    needs_adapters = args.cmd in ("director", "worker") or (args.cmd == "run" and args.run_sub == "create")
    if needs_adapters:
        director = load(args.director, "director", script.get("director"))
        implementer = load(args.implementer, "implementer", script.get("implementer"))
    else:
        director = load("fake-director", "director")
        implementer = load("fake-implementer", "implementer")
    orch = Orchestrator(store, director, implementer)
    if args.cmd == "director":
        return orch.plan(args.task)
    if args.cmd == "worker":
        if args.reconcile:
            return {"reconcile_required": orch.reconcile_stale_leases()}
        if not args.run:
            raise ZenithError("worker needs --run ID or --reconcile")
        return orch.run_once(args.run)
    sub = args.run_sub
    if sub == "create":
        budget = {k: v for k, v in (("max_attempts", args.max_attempts), ("max_wall_seconds", args.max_wall_seconds)) if v}
        return orch.create_run(args.task, args.source, budget)
    if sub == "status":
        return orch.status(args.run)
    if sub == "list":
        return orch.list_runs(args.task, args.state)
    if sub == "cancel":
        return orch.cancel_run(args.run)
    if sub == "reconcile":
        return orch.reconcile(args.run, args.outcome, args.reason)
    if sub == "accept":
        return orch.accept_run(args.run, args.reason)
    raise ZenithError("unknown run subcommand")


def output(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> None:
    p = make_parser()
    args = p.parse_args()
    store = Store()
    try:
        if args.cmd in (None, "misa"):
            if getattr(args, "eval", False):
                import tempfile
                from .misa import acceptance
                from .extensions import load
                base = Path(tempfile.mkdtemp(prefix="misa-eval-"))
                acceptance.build_world(base)
                prov = load(args.provider, "conversation", script={}) if args.provider else None
                print(acceptance.render(acceptance.run_scenarios(Store(base / "zenith.db"), prov, state_root=base)))
                sys.exit(0)
            if getattr(args, "live_plan", False):
                from .misa.acceptance import live_plan
                output(live_plan())
                sys.exit(0)
            from .misa.repl import main as misa_main
            sys.exit(misa_main(store, getattr(args, "once", None)))
        if args.cmd == "init":
            output({"db_path": store.init(), "initialized": True})
        elif args.cmd == "doctor":
            version = store.schema_version()
            from .migrations import LATEST_VERSION
            overlaps = store.source_overlaps() if version == LATEST_VERSION else None
            output({"database_exists": store.db_path.exists(),
                    "db_path": str(store.db_path),
                    "schema_version": version, "schema_expected": LATEST_VERSION,
                    "schema_action": (None if version in (None, LATEST_VERSION)
                                      else "run `zenith init` to upgrade (backup is written first)"),
                    "source_overlaps": overlaps,
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
                scope = {}
                if args.allow: scope["allowed_files"] = args.allow
                if args.exclude: scope["excluded_files"] = args.exclude
                if args.acceptance: scope["acceptance"] = [{"id": f"acceptance-{i}", "argv": shlex.split(c), "expected_exit": 0}
                                                           for i, c in enumerate(args.acceptance)]
                if args.artifact: scope["artifacts"] = args.artifact
                output({"task_id": store.add_task(args.project, args.title, args.objective, args.risk, args.due, scope)})
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
                output({"approval_id": store.approve(args.id, minutes=args.minutes, max_uses=args.max_uses)})
            elif args.sub == "revoke":
                store.revoke_approval(args.approval, args.reason)
                output({"revoked": args.approval})
            elif args.sub == "approvals":
                output(store.list_approvals(args.id))
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
        elif args.cmd in ("director", "run", "worker"):
            output(_orchestration_command(store, args))
        elif args.cmd == "artifacts":
            if args.art_sub == "list":
                output(store.list_artifacts(args.project, args.task, args.run, args.kind))
            else:
                output(store.get_artifact(args.id))
        elif args.cmd == "decisions":
            output(store.list_decisions(args.project))
        elif args.cmd == "adapters":
            from .extensions import describe_all, discover, load
            if args.ad_sub == "probe":
                manifest = discover().get(args.adapter)
                if manifest is None or manifest.kind == "broken":
                    raise ZenithError(f"Unknown adapter: {args.adapter}")
                adapter = load(args.adapter, manifest.kind, script={})
                info = adapter.preflight() if hasattr(adapter, "preflight") else {"adapter": args.adapter, "live": False}
                if args.version_check and hasattr(adapter, "version"):
                    info["version"] = adapter.version()
                output(info)
            else:
                output(describe_all())
        elif args.cmd == "identity":
            from . import identity
            cfg = identity.load()
            if args.id_sub == "commit-preview":
                if cfg.owner is None:
                    raise ZenithError("Owner identity unknown: set ZENITH_OWNER_NAME/EMAIL or git config user.name/email")
                contributors = list(cfg.coauthors) + ([cfg.assistant] if args.assistant_contributed else [])
                print(identity.commit_message(args.subject, args.body, cfg.owner, contributors), end="")
            else:
                output(cfg.describe())
        elif args.cmd == "smoke":
            from .smoke import run_smoke
            output(run_smoke(store, args.adapter, args.repo, args.authorize_live_call, args.max_budget_usd,
                             full=args.full, implementer_name=args.implementer))
        elif args.cmd == "sandbox":
            from .sandbox import detect
            output(detect().as_dict())
        elif args.cmd == "demo":
            from .demo import run_poc
            output(run_poc(args.dir, args.scenario))
        elif args.cmd == "events":
            output(store.events(args.limit))
    except (ZenithError, OSError, ValueError) as exc:
        print(f"Zenith error: {exc}", file=sys.stderr)
        sys.exit(2)
    except sqlite3.Error as exc:
        # e.g. "database is locked" after busy_timeout, or a malformed file. Nothing was committed.
        print(f"Zenith database error: {exc}", file=sys.stderr)
        sys.exit(3)
    except KeyboardInterrupt:
        print("Zenith: interrupted; no partial writes were committed", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
