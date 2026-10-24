"""`zenith misa --claude`: Misa inside the owner's own, unmodified Claude Code CLI.

Authentication is Claude Code's native login (the owner's claude.ai subscription); Zenith never
reads, copies or forwards any credential — it only asks `claude auth status` whether a login exists
and refuses to start otherwise. Nothing here is mocked: if the CLI is missing or logged out, the
command says so and the offline `zenith` Misa keeps working.

Safety posture of the launched session (all supported flags of the installed CLI, nothing patched):
  --restricted            no command/code tools, no WebFetch, user/project/local settings ignored,
                          bypassPermissions refused
  --tools ""              every built-in tool off: only the Zenith MCP tools remain
  --strict-mcp-config     only the Zenith MCP server (no ~/.claude.json / .mcp.json / plugin servers)
  --disable-slash-commands no skills or custom commands
  --system-prompt         Misa's versioned profile replaces the coding system prompt
  cwd                     a dedicated empty workspace under Zenith's state dir (no project CLAUDE.md,
                          no hooks, no untrusted repository configuration)
  env                     ANTHROPIC_API_KEY / *_AUTH_TOKEN / CLAUDE_CODE_OAUTH_TOKEN removed so the
                          session can only run on the native login
Read tools are pre-allowed (no prompts); the optional note-recording tool is NOT, so Claude Code
asks the owner before every write. Session ids are chosen by Zenith and kept in an inspectable
registry so `--resume` continues the last Misa conversation.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from ..kernel import Store, ZenithError, state_dir, utcnow
from .mcp_server import NOTE_TOOL, READ_TOOL_NAMES
from .profile import PROFILE_TEXT, PROFILE_VERSION, profile_sha256

STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_PROFILE")
STRIPPED_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_")     # API-key / third-party-provider routes: never for Misa
DENIED_TOOLS = ("Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "Agent", "Task", "KillShell")
MCP_SERVER_NAME = "zenith"


def _stripped(key: str) -> bool:
    return key in STRIPPED_ENV or key.startswith(STRIPPED_PREFIXES)


def child_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not _stripped(k)}
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"      # documented switch: no auto-memory from this workspace
    return env


def auto_loaded_memory(workspace: Path) -> list[str]:
    """CLAUDE.md-style files the CLI would read for this cwd (documented discovery: cwd and ancestors,
    ~/.claude/CLAUDE.md, ~/.claude/rules, /etc/claude-code/CLAUDE.md). Reported, not silently accepted."""
    found = []
    home = Path.home()
    for d in (workspace, *workspace.parents):
        for name in ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md"):
            f = d / name
            if f.is_file() and f.stat().st_size > 0:
                found.append(str(f))
        rules = d / ".claude" / "rules"
        if rules.is_dir() and any(rules.glob("*.md")):
            found.append(str(rules))
    for f in (home / ".claude" / "CLAUDE.md", Path("/etc/claude-code/CLAUDE.md")):
        if f.is_file() and f.stat().st_size > 0 and str(f) not in found:
            found.append(str(f))
    rules = home / ".claude" / "rules"
    if rules.is_dir() and any(rules.glob("*.md")):
        found.append(str(rules))
    return found


def claude_binary() -> str | None:
    override = os.environ.get("ZENITH_CLAUDE_BIN")
    if override:
        return override if Path(override).exists() else None
    return shutil.which("claude")


def auth_status(binary: str) -> dict:
    """`claude auth status` → parsed JSON. Reports login state only; no token is read or printed."""
    try:
        r = subprocess.run([binary, "auth", "status"], capture_output=True, text=True, timeout=30,
                           env=child_env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"loggedIn": False, "error": f"cannot run claude auth status: {exc}"}
    try:
        data = json.loads(r.stdout.strip() or "{}")
    except ValueError:
        return {"loggedIn": False, "error": "claude auth status did not return JSON"}
    return {k: data.get(k) for k in ("loggedIn", "authMethod", "apiProvider", "subscriptionType", "email", "orgName")}


def misa_root() -> Path:
    return Path(os.environ.get("ZENITH_MISA_CLAUDE_DIR") or (state_dir() / "misa-claude"))


def registry_path() -> Path:
    return misa_root() / "sessions.json"


def load_registry() -> list[dict]:
    p = registry_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def _save_registry(entries: list[dict]) -> None:
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    p.write_text(json.dumps(entries[-200:], ensure_ascii=False, indent=1), encoding="utf-8")
    p.chmod(0o600)


def shared_project_count(store: Store) -> tuple[int, int]:
    projects = store.list_projects()
    shared = sum(1 for p in projects if store.project_settings(p["id"])["cloud_consent"])
    return shared, len(projects) - shared


def build_launch(store: Store, resume: bool = False, allow_notes: bool = False, model: str | None = None,
                 session_name: str = "Misa") -> dict:
    """Everything about the session, computed without starting anything (printable as a plan)."""
    root = misa_root()
    workspace = root / "workspace"
    binary = claude_binary()
    last = load_registry()[-1] if load_registry() else None
    session_id = (last["session_id"] if (resume and last) else str(uuid.uuid4()))
    python = sys.executable
    audit_file = root / "audit" / f"{session_id}.jsonl"
    db_env = {"ZENITH_DB_PATH": str(store.db_path), "ZENITH_MCP_AUDIT_FILE": str(audit_file), "ZENITH_MISA_SESSION": session_id}
    if allow_notes:
        db_env["ZENITH_MCP_ALLOW_NOTES"] = "1"
    mcp_config = {"mcpServers": {MCP_SERVER_NAME: {"type": "stdio", "command": python, "args": ["-I", "-m", "zenith.misa.mcp_server"],
                                                    "env": db_env}}}
    allowed = [f"mcp__{MCP_SERVER_NAME}__{t}" for t in READ_TOOL_NAMES]
    argv = [binary or "claude", "--restricted", "--strict-mcp-config", "--mcp-config", str(root / "mcp.json"),
            "--tools", "", "--disallowedTools", *DENIED_TOOLS, "--allowedTools", *allowed, "--disable-slash-commands",
            "--system-prompt", PROFILE_TEXT, "--system-prompt-snapshot", "off", "--name", session_name]
    if model:
        argv += ["--model", model]
    argv += (["--resume", session_id] if (resume and last) else ["--session-id", session_id])
    env = child_env()
    env["ZENITH_MISA_PROFILE"] = f"{PROFILE_VERSION}:{profile_sha256()[:12]}"
    shared, withheld = shared_project_count(store)
    return {"binary": binary, "cwd": str(workspace), "argv": argv, "mcp_config_path": str(root / "mcp.json"), "mcp_config": mcp_config,
            "session_id": session_id, "resume": bool(resume and last), "allow_notes": allow_notes,
            "profile": {"version": PROFILE_VERSION, "sha256": profile_sha256()},
            "allowed_tools": allowed, "prompted_tools": [f"mcp__{MCP_SERVER_NAME}__{NOTE_TOOL['name']}"] if allow_notes else [],
            "stripped_env": sorted(k for k in os.environ if _stripped(k)),
            "env_overrides": {"ZENITH_MISA_PROFILE": env["ZENITH_MISA_PROFILE"], "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
            "denied_tools": list(DENIED_TOOLS), "auto_loaded_memory_files": auto_loaded_memory(workspace),
            "audit_file": str(audit_file), "registry": str(registry_path()),
            "shared_projects": shared, "withheld_projects": withheld,
            "data_boundary": "Only cloud-shared projects (cloud_consent=yes) are visible to the session; everything the model sees goes "
                             "through the Zenith MCP tools and is logged in the audit file. No files, repositories or credentials are exposed.",
            "_env": env}


def preflight(store: Store, plan: dict) -> list[str]:
    blockers = []
    if not plan["binary"]:
        blockers.append("claude CLI not found on PATH (install Claude Code; ZENITH_CLAUDE_BIN may point at it)")
        return blockers
    st = auth_status(plan["binary"])
    plan["auth"] = st
    if not st.get("loggedIn"):
        blockers.append("Claude Code is not logged in: run `claude auth login` yourself (Zenith never handles credentials)"
                        + (f" [{st['error']}]" if st.get("error") else ""))
    elif st.get("authMethod") != "claude.ai" or st.get("apiProvider") not in ("firstParty", None):
        blockers.append(f"auth method is {st.get('authMethod')}/{st.get('apiProvider')}; Misa runs only on the native claude.ai login "
                        "(no API keys, no setup-token, no third-party provider)")
    if plan["auto_loaded_memory_files"]:
        plan.setdefault("warnings", []).append("Claude Code would also read these memory files (documented discovery): "
                                               + ", ".join(plan["auto_loaded_memory_files"]))
    if plan["shared_projects"] == 0:
        plan.setdefault("warnings", []).append("no project is cloud-shared: Misa will know nothing about your projects until you run "
                                               "`zenith project docs --id <ID> --cloud-consent yes`")
    return blockers


def launch(store: Store, resume: bool = False, allow_notes: bool = False, model: str | None = None, plan_only: bool = False,
           out=print) -> int:
    plan = build_launch(store, resume=resume, allow_notes=allow_notes, model=model)
    blockers = preflight(store, plan)
    plan["blockers"] = blockers
    if plan_only:
        shown = {k: v for k, v in plan.items() if k not in ("_env",)}
        shown["argv"] = [a if a != PROFILE_TEXT else f"<misa profile v{PROFILE_VERSION} {profile_sha256()[:12]}>" for a in plan["argv"]]
        out(json.dumps(shown, ensure_ascii=False, indent=2))
        return 0 if not blockers else 2
    if blockers:
        raise ZenithError("Cannot start the native Misa session: " + "; ".join(blockers) + ". The offline `zenith` Misa still works.")
    root = misa_root()
    workspace = Path(plan["cwd"])
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    Path(plan["mcp_config_path"]).write_text(json.dumps(plan["mcp_config"], indent=1), encoding="utf-8")
    Path(plan["mcp_config_path"]).chmod(0o600)
    entries = load_registry()
    entries.append({"session_id": plan["session_id"], "started_at": utcnow(), "resumed": plan["resume"], "allow_notes": allow_notes,
                    "profile": plan["profile"], "workspace": plan["cwd"], "audit_file": plan["audit_file"],
                    "auth": {k: plan.get("auth", {}).get(k) for k in ("authMethod", "subscriptionType")}})
    _save_registry(entries)
    for w in plan.get("warnings", []):
        out(f"misa: note — {w}", file=sys.stderr) if out is print else out(f"misa: note — {w}")
    out(f"misa: opening Claude Code (session {plan['session_id'][:8]}…, profile v{PROFILE_VERSION}, "
        f"{plan['shared_projects']} shared project(s), {plan['withheld_projects']} withheld; audit → {plan['audit_file']})")
    try:
        rc = subprocess.run(plan["argv"], cwd=str(workspace), env=plan["_env"]).returncode
    except KeyboardInterrupt:
        rc = 130
    out(f"misa: session ended (exit {rc}). Resume with `zenith misa --claude --resume`; audit log: {plan['audit_file']}")
    return rc
