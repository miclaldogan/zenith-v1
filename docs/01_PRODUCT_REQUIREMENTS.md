# 01 — Product Requirements / Ürün Gereksinimleri

**Version:** v1.0 design baseline · **Updated:** 2026-10-23 · **Status:** Accepted vision; implementation staged

## 1. Problem
A single person manages personal projects, advisor projects, academic/coursework projects, family projects, applications and unstarted ideas across several computers. Repository activity, deadlines, email, calendar, decisions and unanswered questions are fragmented. A plain todo list is insufficient: Zenith must connect evidence to goals, preserve continuity and route pre-approved engineering work to agents.

## 2. Product vision
A private, local-first *personal intelligence and agent orchestration layer* with an independent source of truth, multi-machine nodes, controlled action permissions, verifiable progress, proactivity, and optional remote hub. Model providers are replaceable workers. Core operation cannot require an LLM.

## 3. Key users and operating constraints
- Primary actor: single owner and approver; no multi-user SaaS in v1.
- Arch Linux + Hyprland main workstation; other devices can have local projects and delayed sync.
- CLI-first and later Textual TUI. tmux is an observability UI, not task state storage.
- Powerful remote models preferred for complex reasoning. No obligation to run small local LLMs.
- Provider subscription vs programmatic/API restrictions respected; do not proxy login credentials.
- Turkish interaction; **English** engineering/task prompts; consistent UTC timestamps.

## 4. Functional requirements (FR)
| ID | Requirement | MVP phase | Success evidence |
|---|---|---|---|
| FR-01 | Register projects with unique IDs and zero-to-many paths/repos/sources | S0-S1 | Paths can be linked without duplicate projects |
| FR-02 | Record tasks, milestones, commitments and deadlines | S1 | Query active, due, blocked, awaiting-response |
| FR-03 | Append actor-attributed events and persist decisions | S0-S1 | State survives restart; event log auditable |
| FR-04 | Separate self-reported, observed and verified progress | S1 | Provenance shown in all reports |
| FR-05 | Read approved local paths and Git metadata | S0-S1 | No writes; sensitive paths excluded |
| FR-06 | Produce context bundles and compare against past snapshots | S1-S2 | Repeatable, evidence-backed change summary |
| FR-07 | Execute bounded planner/implementer/reviewer agent workflow | S2 | Contract, idempotency, max attempts, verification |
| FR-08 | Request human approval for sensitive operations | S1-S2 | Unapproved action denied by code, not prompt |
| FR-09 | Show/mirror live execution in tmux/Hyprland workspace | S3 | Runs do not depend on visible terminal |
| FR-10 | Track multiple machines as separate offline-capable nodes | S3-S5 | Stale state labelled; reconnect reconciles |
| FR-11 | Project check-ins and proactive Telegram digests | S4 | Rate-limited, deduped, relevant notifications |
| FR-12 | Read Gmail/Calendar/GitHub with OAuth least-privilege | S5 | Source evidence and explicit write confirmation |
| FR-13 | Resume context after weeks of inactivity | S4 | Accurate handoff with unresolved questions |
| FR-14 | Research literature and report papers with cited sources | Backlog | On-demand sourced report; never fabricate papers |

## 5. Non-functional requirements (NFR)
- **Safety:** deny-by-default permissions, project/path allowlists; no unrestricted shells from text prompts.
- **Reliability:** transactional state machine; no duplicate execution on retry; crash-safe restart.
- **Truthfulness:** label fact/observation/inference/unknown; never treat model response as test proof.
- **Privacy:** redact secrets, minimal retention, per-source data scope; opt-in remote transmission.
- **Latency:** deterministic monitoring fast/offline; asynchronous model analysis can queue.
- **Portability:** Linux-node primary, other OS nodes eventually possible; headless mode must work.
- **Cost control:** token budget per day/task; max attempt and concurrency caps.
- **Observability:** durable events, run IDs, errors, test artifacts, timestamps, trace correlation.

## 6. Canonical use cases
1. **Continuity:** “What did I last do on Stabilimentum?” → show last verified Git evidence, human check-in and confidence, ask one relevant question if stale.
2. **Engineering:** “Approved: have Codex architect and Claude implement this scoped fix” → plan / approval policy / isolated worktree / tests / independent review / report / no automatic push.
3. **Daily briefing:** tasks, deadlines, blockers, advisor email changes, calendar feasibility, grouped notifications.
4. **Passive project discovery:** new directory → suggest new project or link to existing; never auto-scan whole home.
5. **Cross-device:** second computer offline → show last-seen, queue question; on reconnect reconcile against actual state.
6. **Idea vault:** capture unstarted idea separately; refrain from treating it as active deadline.

## 7. Out of scope for initial usable MVP
Autonomous deployment, privileged system admin, arbitrary terminal remote shell, unattended email send, destructive cleanup, OAuth setup, always-on cloud service, multi-tenant access, live remote desktop, and broad academic deep research automation. Future work is tracked without blocking kernel.

## 8. Definition of done for first live vertical slice
From a **scratch demo repository**, human registers project and task, explicitly grants scoped approval, runs one supported agent in an isolated worktree, captures structured result, independently validates tests/diff, marks `COMPLETED` or `BLOCKED`, and receives a status report. No changes to actual personal repositories during POC.

## 9. Product principles
- Evidence over activity; one passing test > ten generic commits.
- Personal memory without surveillance; do not collect keystrokes or full desktop history.
- Ask only when uncertainty affects a decision.
- Local-first core; headless-first execution; UI optional.
- No silent permission escalation, including between agents.
