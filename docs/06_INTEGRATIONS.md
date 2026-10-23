# 06 — Integration Contracts

Integrations are opt-in and **none** is assumed connected at project bootstrap.

## A. Claude Code / Codex
- Interfaces: `AgentAdapter.start(task, run_ctx)`, `.poll(run_id)`, `.cancel(run_id)`, `.collect(run_id)` and `.capabilities()`.
- Implement `FakeAgentAdapter` first for offline tests. Real CLI adapters should feature-detect installed versions and login mode; reject unsupported options.
- Claude Code `claude -p --output-format json` can produce structured run output; `stream-json` for events. Check CLI flags against installed version.
- Codex `codex exec --json` yields **JSON Lines events**; parse incrementally; use `--output-schema` for structured final results if supported.
- Plan-review split is a default routing policy, not a hard-wired model identity.
- Claude Remote Control is intentionally **deferred** and must not be foundational.
- Do not let the orchestrator impersonate a public Claude/Anthropic API or route subscription credentials generically.

## B. tmux and Hyprland
- Task state is stored by Zenith; tmux session/window is only an observation console.
- Session naming: `zenith-{task_id}`; map task run IDs to session IDs; detect session collision.
- Headless worker can run without compositor. Only UI adapter opens terminal in selected workspace.
- Workspace preference: 3 Zenith, 4 Claude, 5 Codex; never overwrite owner's active workspaces.
- Hyprland syntax varies by release. Probe `hyprctl version`, supported `dispatch` syntax and `HYPRLAND_INSTANCE_SIGNATURE` before taking action. Do not ship untested fixed key bindings.
- No display available → record `ui_attach_deferred`, do not fail actual work.
- `tmux` CLI commands must be constructed from safe task IDs; no user-text interpolation into shell command.

## C. Project collectors
- Paths registered explicitly. Ignore `.git` internals, `.env*`, `node_modules`, virtual environments, private keys, caches, outputs with sensitive data.
- Git collector records branch, HEAD SHA, dirty flag and named status entries where privacy permits. A commit is activity, not proof of completion.
- Snapshot incremental changes keyed by project/node/path revision. Full scans bounded by file count/size and skip symlinks crossing allowed root.

## D. GitHub
- Read-only repo metadata, issues, PR status and check runs initially. Link by stable project ID and repo ID, never just matching names.
- Handle API quotas, private repo permissions, webhook signatures if ever used, and source freshness.

## E. Gmail & Calendar
- Gmail: query project-specific threads, pull minimal metadata, optionally parse email with explicit classification/consent, extract deadline candidates as **unconfirmed** proposals.
- Calendar: read existing events, resolve scheduling conflicts, draft proposed events; write on approval. Timezone user-configurable (initial Europe/Istanbul).
- Gmail push/watch is future complexity; periodic sync with cursor is sufficient first. Calendar uses incremental sync where appropriate.
- Recurring academic class hours can be internal constraints without automatically copying them to the calendar.

## F. Telegram
- Bot receives `status`, `approve`, `reject`, `pause`, `continue`, `checkin` commands, all bound to owner identity.
- One scheduled morning digest and event-driven meaningful changes by default; notification dedupe & quiet hours.
- Messages link to project ID, task ID, evidence and due dates with local timezone.
- Ask short, answerable check-in questions if progress is stale; record answer as `HUMAN_REPORT` pending later verification.
- Bot availability independent of model access; compose notifications deterministically when possible.

## G. Remote Hub / Nodes
- Hub initially optional and deferred. Private network or authenticated outbound relay, not public shell service.
- Node heartbeat and lease; data sync from append-only events with dedupe and conflict/reconciliation.
- No automatic wake guarantee for powered-off PCs. Remote WOL may need separate always-on LAN relay.

## H. MCP surface
`list_projects`, `get_project_context`, `list_tasks`, `submit_observation`, `propose_task`, `submit_agent_report`, `request_approval`, `list_pending_approvals`. Read-only tools exposed first. Every write tool requires server-side policy. MCP authorization does not replace Zenith approvals.

## Verified source pages
- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/headless
- https://code.claude.com/docs/en/hooks
- https://developers.openai.com/codex/non-interactive-mode
- https://developers.openai.com/codex/app-server
- https://developers.openai.com/codex/codex-sdk
- https://developers.openai.com/codex/developer-commands
- https://wiki.hypr.land/Configuring/Advanced-and-Cool/Using-hyprctl/
