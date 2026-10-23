# Sprint 3 — A usable personal assistant experience (plan)

**Status:** plan only (2026-10-23). Depends on: a passed live smoke test (Sprint 2.6 §6), ADR-005/006/007 accepted, live DB migrated.

## Target user flow
`zenith` (no arguments) opens a **Misa session**: a short evidence-grounded opening (what changed, what is due, what is blocked) followed by a **conversation**, not a static dashboard. The owner asks in plain Turkish ("Stabilimentum'da en son ne yaptım?", "bu hafta ne bitmeli?"); Misa answers from stored evidence with citations, asks one relevant question when context is stale, and offers actions. From the conversation: pick a project → Misa assembles context (brief + evidence + open tasks + decisions) → Engineering Director produces a plan and prompt → owner approves (scope-hashed, single use) → implementation in a worktree → verifier → Misa reports the outcome and records the decision → next suggested action.

**Minimum usable conversation (Sprint 3):** deterministic retrieval over projects, sources, evidence, tasks, decisions and runs with provenance labels on every claim; when a director adapter is enabled and the owner consents, answers may be **model-generated and grounded** in that retrieved context (provider-independent, through the existing adapter port, budgeted), otherwise Misa falls back to deterministic Turkish templates. Full persona customization (humour, verbosity, tone — REQ-010) is Sprint 4.

## Principles
Deterministic first (status, briefs, approvals are code); the model appears only in director/implementer stages; every screen shows provenance (REPORTED/OBSERVED/VERIFIED); nothing happens without an explicit approval step; the assistant identity is configuration (REQ-004); engineering is one module — the board must work for projects with no repository.

## Ordered tasks
| ID | Task | Depends on | Gate |
|---|---|---|---|
| S3-01 | `zenith` default command = Misa session: evidence-grounded opening (brief, due tasks, blocked runs, reconcile-required items) + conversation loop; signature from `identity` | — | works offline with no adapters enabled; Turkish by default |
| S3-02 | Conversation retrieval: questions about projects/tasks/evidence/decisions/runs answered deterministically with citations ("son ne yaptım", "ne bitmeli", "neden bloke"); stale-context question; action offers | S3-01 | every answer cites stored ids; unknown stays unknown; tests on a fixture store |
| S3-02b | Grounded model answers (optional): the retrieved context becomes a `zenith.conversation.v1` contract sent through the enabled director adapter; reply quoted with provenance; budgeted; off by default | S3-02, live smoke passed | fake-adapter test; no live call in the suite |
| S3-02c | Guided flow from the conversation: select project → `director plan` → show prompt artifact → approve → run → live progress from `run_events` → final report | S3-02 | end-to-end with fake adapters; no manual ids |
| S3-03 | "Assistant turn" contract: Misa's messages are either deterministic templates fed by evidence or grounded model text quoted and labelled; no claim without provenance | S3-02 | tests assert provenance labels on every claim |
| S3-04 | Persistent worker (`zenith worker --loop`) with lease heartbeats from a thread for live adapters; `systemd --user` unit example (not enabled) | 2.6 live smoke | crash-recovery test with a real subprocess |
| S3-05 | Decisions and check-ins surfaced in the board; "what did I last do" answer with citations | S3-01 | citations resolve to stored ids |
| S3-06 | Optional Textual TUI behind an extra (`pip install zenith[tui]`), same data as the text board | S3-02 | board unaffected when Textual absent |
| S3-07 | `zenith.toml` configuration (identity, roles, enabled adapters, allowlists, credential file paths) replacing env vars | — | env vars still honoured; config validated |
| S3-08 | Package move `zenith.engineering.*` (ADR-006), `zenith.modules` entry-point group for feature modules | — | tests unchanged |
| S3-09 | Live director/implementer run from the guided flow on a sacrificial *personal* repository (owner-chosen, after the synthetic smoke) | S3-02, smoke passed | COMPLETED with verified evidence; primary tree untouched |

## Explicitly deferred (kept in the registry, not built in Sprint 3)
Telegram notifications and approvals, Gmail/Calendar, multi-node sync, tmux/Hyprland, proactive check-ins and digests, Research Intelligence, autonomous Git commits/push (REQ-002), GitHub App "Misa", MCP exposure.
