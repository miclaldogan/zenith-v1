# Misa in native Claude Code (`zenith misa --claude`)

**Owner decision (2026-10-24):** Claude Code is Misa's primary AI experience for this phase. No Console API key,
no hosted service; the Anthropic API adapter stays available but disabled. The conversation port and the
provider-independent engine are untouched: this is a third way to talk to Misa (offline composer, sandboxed
provider adapters, native Claude Code session).

## 1. What the command does
`zenith misa --claude [--resume] [--allow-notes] [--model M] [--plan]` starts the owner's own, unmodified Claude
Code CLI as Misa:

| Concern | Mechanism (installed CLI 2.1.310; flags verified against `claude --help` and code.claude.com docs) |
|---|---|
| Authentication | Claude Code's native login (`claude auth status` must report `authMethod: claude.ai`). Zenith never reads, copies or brokers a credential; `ANTHROPIC_*`, `CLAUDE_CODE_OAUTH_TOKEN` and `CLAUDE_CODE_USE_*` are removed from the child environment so the session cannot fall onto an API key or a third-party provider. `--bare` is not used (it excludes subscription login); `claude setup-token` is not used. |
| Billing | Subscription seat allowance (Team) per the Claude Code docs; no dollar metering while inside the allowance. |
| Personality | `--system-prompt` with the versioned Misa profile (`misa/profile.py`, v1.0, SHA-256 recorded per session); `--system-prompt-snapshot off` so the current profile applies on resume. |
| Zenith memory | one stdio MCP server (`python -I -m zenith.misa.mcp_server`) passed with `--mcp-config` + `--strict-mcp-config`, so no other MCP server (global `~/.claude.json`, `.mcp.json`, plugins) loads. |
| Built-in tools | `--tools ""` (all built-ins off; docs confirm MCP tools stay) plus `--disallowedTools Bash Edit Write MultiEdit NotebookEdit WebFetch WebSearch Agent Task KillShell` and `--restricted` (no code tools, no WebFetch, settings files ignored, `bypassPermissions` refused). |
| Skills/commands | `--disable-slash-commands`. |
| Working directory | `~/.local/state/zenith/misa-claude/workspace` (empty, 0700): no project `CLAUDE.md`, hooks or `.mcp.json`. `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. The launcher lists any CLAUDE.md the CLI would still discover (ancestors, `~/.claude/CLAUDE.md`, rules) as a warning. |
| Permissions | the seven read tools are pre-allowed (`--allowedTools mcp__zenith__<tool>`); `record_note` (only with `--allow-notes`) is never pre-allowed, so Claude Code asks the owner before every write. |
| Continuity | Zenith chooses the session UUID (`--session-id`) and records it in `~/.local/state/zenith/misa-claude/sessions.json`; `--resume` re-passes every flag (docs: `--mcp-config` is not restored by resume) and continues that session. |
| Audit | every MCP call is appended to `~/.local/state/zenith/misa-claude/audit/<session>.jsonl` (tool, arguments, projects named, result size). |

`--plan` prints all of the above plus the preflight (binary, auth method, shared/withheld project counts, memory
files found) and starts nothing. If the CLI is missing or logged out the command refuses with a clear message;
the offline `zenith` Misa keeps working. Nothing is mocked.

## 2. Data-sharing boundary (fail-closed)
The MCP server only ever names or describes projects whose `cloud_consent` setting is true
(`zenith project docs --id <ID> --cloud-consent yes`). Others appear as `withheld_count`. Past Misa turns that
mention a withheld project are not returned. Repository text marked `local_only` is dropped. No SQL, no file
access, no paths, no credentials. Results carry `provenance` (ids, origin, epistemic tag, observed time, age) and
temporal applicability (`applies` / `applicability` of the latest verification), and are labelled as untrusted data.

Tools: `list_projects`, `project_summary(project, focus)`, `recent_evidence`, `decisions`, `tasks`,
`verification_status`, `previous_conversations(query)`; optional `record_note(project, text)` → HUMAN_REPORT.

## 3. Audit of what the CLI would auto-load on this machine (2026-10-24)
- `~/.claude/settings.json`: 10 enabled plugins, no hooks — ignored under `--restricted`; plugin MCP servers ignored under `--strict-mcp-config`; skills/commands disabled by `--disable-slash-commands`. Whether plugin hooks are also skipped under `--restricted` is not documented; the first session should confirm with `/hooks`, `/mcp` and `/context`.
- `~/.claude/settings.local.json`: permissions for other projects — ignored under `--restricted`.
- `~/.claude.json`: global MCP servers `godot-mcp`, `devrelay-gateway` — excluded by `--strict-mcp-config`.
- `~/.claude/CLAUDE.md`: exists, 0 bytes (nothing to load). No `/etc/claude-code/managed-settings.json`.
- Repository `CLAUDE.md` of zenith-v1: not loaded (the session's cwd is the dedicated workspace, not the repo).

## 4. Session continuity vs. Zenith memory
Claude Code's transcript (`~/.claude/projects/<workspace>/<session>.jsonl`) is the conversation's own history and is
never read by Zenith. Zenith's memory stays authoritative: facts reach the model only through the MCP tools, and the
only way back is `record_note`, explicitly owner-approved per call and stored as the owner's report (REPORTED, never
VERIFIED). A conversational summary is therefore never evidence.

## 5. Engineering requests
Misa's profile says so and the tools make it so: there is no code, file, shell, Git or network tool in the session.
A change request has to enter the Zenith workflow (`zenith task add …`, approvals, sandboxed runs). No commit, no push.

## 6. Limitations
- A genuine session needs the owner at the terminal (interactive login, trust dialog for the new workspace once).
- Grounding in the native session is enforced by the profile and the tool data, not by Zenith's deterministic
  validator (that runs only in the engine-driven providers). The acceptance questions are scored by the owner.
- `--restricted` is documented as silent on CLAUDE.md discovery and plugin hooks; the launcher reports the memory
  files it can see and the first session should verify `/context`.
- Lexical recall of previous turns is simple; cross-language paraphrase is not matched.
- Only projects registered in Zenith and explicitly shared are visible; the live database currently holds none.

## 7. Launch checklist (owner)
```
zenith project add --name Zenith --path ~/projects/now-doing/zenith-v1     # or: zenith project discover ~/projects
zenith scan --project <ID>
zenith project docs --id <ID> --cloud-consent yes
zenith misa --claude --plan        # preflight only
zenith misa --claude               # the session; `zenith misa --claude --resume` later
```
