# 11 — Requirements registry (product vision vs implementation status)

Product requirements are recorded here independently of what is implemented. Status values:
**implemented** (code + tests), **simulated** (works end-to-end with fake providers only),
**designed** (contracts/schemas exist, no execution), **planned**, **deferred** (kept, not scheduled).
Nothing in "deferred" is removed from the vision.

## Cross-cutting requirements

### ZENITH-REQ-001 — Autonomous Engineering Direction
Owner approves an objective → Zenith assembles context and evidence → Engineering Director analyses and writes English implementation instructions → Zenith validates scope and permissions → Implementation Agent executes → verifier collects independent evidence → Director reviews → Director proposes corrections or the next prompt → Zenith records the whole workflow. The owner never copies prompts or reports between agents by hand.

| Step | Status | Where |
|---|---|---|
| Context package with evidence citations | implemented | `orchestration.build_context`, contract `context_package` |
| Director plan → architecture proposal + prompt artifact | simulated (fake director) | `Orchestrator.plan`, artifacts `architecture_proposal`, `implementation_prompt` |
| Prompt as a first-class persistent artifact (ID, schema, parent/revision, context ref, objective, scope, exclusions, constraints, instructions, acceptance, test expectations, authorization, output requirements, timestamp, producer, digest) | implemented | `artifacts` table, `_stamp_prompt` |
| Scope and permission validation by Zenith, never by the model | implemented | `_stamp_prompt`, `consume_approval`, contract strictness |
| Data minimization toward providers (context dir, `context_files` opt-in, sensitive-name exclusion, worktree pre-scan) | implemented (mock-tested) | `Orchestrator.build_director_context_dir`, `sensitive_paths` |
| Authentication modes (Claude API key / subscription token; Codex auth file) with in-sandbox injection | implemented (mock-tested; live unverified) | `agents/providers.py`, `sandbox_forwarder.py` |
| Implementation in an isolated worktree | simulated (fake implementer) | `worktree.py`, `FakeImplementer` |
| Deterministic verification | implemented | `verify.py` (sandboxed acceptance commands, diff scope, revision, artifacts, claims audit) |
| Director review of *verified* evidence; corrective prompt revisions | simulated | `review_findings`, `next_step_proposal`, revision chain |
| Zenith records completion / correction required / reconciliation required | implemented | run states, task states, decisions, run events |
| Live Codex director / Claude Code implementer | **prepared, gated** (adapters + mock tests; no live call made) | `agents/providers.py`, `plans/SPRINT_2_5_READINESS.md`, ADR-007 |

### ZENITH-REQ-002 — Governed Autonomous Git Operations
Zenith must eventually create branches/worktrees, stage approved files, commit locally, push, open and merge PRs — each under a **separate** approval operation. Commit authorization is distinct from push/merge/deploy.

| Item | Status |
|---|---|
| Operation vocabulary `git.branch`, `git.worktree`, `git.stage`, `git.commit`, `git.push`, `git.pr.open`, `git.pr.merge` in the approval model | designed (`APPROVAL_OPERATIONS`); only `task.execute` is grantable |
| Worktree/branch creation for runs (`zenith/<task>/<attempt>`) | implemented as part of `task.execute` (no commits) |
| Autonomous commits | **not implemented**; requires separate owner approval of the work and of each execution. Commit permission (`git.commit`) and push permission (`git.push`) are distinct operations in the approval model |
| Push / PR / merge | planned, separate approvals, never automatic |

**Commit attribution design (no code yet).** Configuration keys (environment now; config file later):
`ZENITH_GIT_AUTHOR_NAME` / `ZENITH_GIT_AUTHOR_EMAIL` — the committing identity, defaulting to the owner's own `git config user.name/user.email`; never invented by Zenith. `ZENITH_COAUTHORS` — `;`-separated `Name <email>` trailers added as `Co-authored-by:`; the intended set is `m.iclaldogan <verified-user-email>` and `Zenith <configured-zenith-email>`, both supplied by the owner (GitHub recognition depends on the verified email). Rules: the author is never repeated as a co-author; a trailer is added only for an identity that genuinely contributed to that commit (Zenith when it generated or applied the change); model providers are not Git identities; unrelated identities or credentials are never used; commit signing follows the owner's Git config and is never forced.

### ZENITH-REQ-003 — Modular, Extensible, Product-Grade Architecture
Independently developed, installable, configurable, enable/disable-able capabilities without changes to core business logic. Modular monolith + hexagonal ports/adapters + scoped extension mechanism. See ADR-006.

| Layer | Status | Mechanism |
|---|---|---|
| Core domain (projects, sources, tasks, approvals, evidence, events, artifacts, decisions, policy) | implemented, stable | `zenith.kernel`, `zenith.policy`, `zenith.migrations` |
| First-party feature modules (engineering: orchestration, verify, worktree, sandbox, contracts) | implemented; to be moved under `zenith.engineering` without behaviour change | in-package modules |
| Provider adapters (director / implementer ports) | implemented (fake); discovery via entry points | `zenith.agents.base`, `zenith.extensions` |
| Optional installable plugins | designed: entry-point group `zenith.adapters` now; `zenith.modules` manifest for feature modules planned | ADR-006 |
| MCP interfaces | deferred (exposure surface over the core API, same policy engine) | — |
| Out-of-process workers for untrusted/privileged extensions | designed (sandboxed command execution exists; worker protocol planned) | `zenith.sandbox`, ADR-005 |

### ZENITH-REQ-004 — Agent Identity & Attribution
Definitions (Misa Intelligence directive, 2026-10-23): **Zenith** is the independent platform — persistent memory, orchestration infrastructure, permission engine and extension runtime (`zenith.platform`). **Misa** is the user-facing personal intelligence and assistant identity running on Zenith (`zenith.assistant.primary`; display name configurable, identifier stable). Misa is never hard-coupled to Claude, Codex, Gemini or any model: the active provider may change between tasks without destroying Misa's identity, project context, history or commitments. **Engineering Director** is one of Misa's functional capabilities (a *role*, initially assigned to Codex), never a permanent identity. Claude Code and Codex are interchangeable providers.

| Item | Status | Where |
|---|---|---|
| Stable ids vs display names; assistant/platform/owner identities | implemented | `identity.py` (`ZENITH_ASSISTANT_NAME`, `ZENITH_ASSISTANT_EMAIL`, `ZENITH_OWNER_*`, owner falls back to the owner's own `git config`) |
| Role assignments (`engineering_director`, `implementer`, `reviewer`, `verifier` → adapter names) | implemented | `ZENITH_ROLE_*`; defaults codex-director / claude-code-implementer |
| Notification signature | implemented (string only; no channel yet) | `IdentityConfig.signature()` → "— Misa (Zenith)" |
| Git identity and `Co-authored-by` trailers | implemented as **preview only** (`zenith identity commit-preview`); no commit is made | `identity.git_trailers`, `commit_message`; `ZENITH_COAUTHORS="Name <email>;…"` |
| Rules | enforced | author never repeated as co-author; duplicates removed; missing email → refusal (never invented); providers are not Git identities; the assistant is credited only when `--assistant-contributed` is asserted by the caller that applied the change |
| GitHub App "Misa" | **evaluated, not created** | see below |

**GitHub App evaluation.** A GitHub App named *Misa* would give repository-scoped, revocable permissions (contents: write for `zenith/*` branches; pull requests: write; no admin), short-lived installation tokens per repository (brokered by Zenith like other credentials — bound read-only into a sandbox only when a `git.push`/`git.pr.*` approval is consumed), and commits attributed to the App's bot identity (`misa[bot]`) with human `Co-authored-by` trailers — which avoids using the owner's personal token for autonomous pushes and keeps every operation auditable in GitHub's log. Costs: app registration and private-key custody, webhook/API plumbing, a Hub-era token broker. Decision: pursue when REQ-002 push/PR operations are scheduled; nothing is registered now.

### ZENITH-REQ-005 — Misa Personal Intelligence
Misa must eventually provide: persistent, evidence-grounded project awareness; conversation and decision continuity; personal commitment and deadline awareness; contextual, proactive check-ins; daily and weekly briefings; natural Telegram interaction; owner-approved autonomous task execution; reliable context retrieval across multiple machines. All proactive behaviour respects notification policy, authorization, quiet hours and explicit user controls; scheduling and observation are deterministic, escalating to a reasoning model only when analysis is needed (no always-running LLM process).

| Capability | Status | Milestone |
|---|---|---|
| Evidence-grounded project awareness (sources, evidence ledger, brief) | implemented (S1) | — |
| Decision continuity (`decisions`, artifacts, run events) | implemented (S2); conversation continuity planned | S3-05 |
| Commitments and deadlines (tasks, due dates, dependencies) | implemented, basic (S1) | S4 (commitment entity, prioritization) |
| Owner-approved autonomous task execution | simulated (fake adapters); live path prepared and gated | S3-02/S3-09 |
| Daily/weekly briefings | deterministic brief implemented; digests planned | S4 |
| Proactive check-ins | deferred | S4 (notification outbox, quiet hours, rate limits) |
| Natural Telegram interaction | deferred | S4 |
| Context retrieval across machines | `node_id` recorded; sync deferred | S5 |

### ZENITH-REQ-006 — Research Intelligence
On-demand and optionally scheduled academic research: literature discovery; publication and source verification; DOI and persistent source references; methodology comparison; findings, limitations and evidence assessment; source-grounded executive and technical reports; delivery through Telegram or another configured channel; continuation into an interactive research conversation. **Verified literature findings are kept separate from speculative ideas and hypotheses (distinct evidence origins); sources, citations and findings are never invented** — every claim must cite a stored, verifiable reference or be labelled as unverified. **Specialised workflow (directive 2026-10-23):** research request → research planning → literature discovery → source acquisition → paper analysis → methodological review → independent evidence verification → synthesis → report generation → user delivery → optional interactive discussion. Interchangeable scholarly data providers behind one port (candidates to evaluate: OpenAlex, Crossref, Semantic Scholar, arXiv, plus publisher/DOI resolvers); every source record carries **three independent dimensions**: **access level** (`metadata_only`, `abstract`, `full_text_accessible`) — what Misa actually examined; **publication/review status** (`preprint`, `peer_reviewed`, `published_not_peer_reviewed`, `retracted`, `unknown`) — the venue's editorial status; **independent scientific verification** (`unverified`, `replicated_by_others`, `independently_reproduced`, `contested`) — what independent work has established. None implies another: reading a full paper does not establish reproducibility, peer review does not establish replication, and a report may never claim full-paper analysis when only an abstract was examined.

**Independent judging (REQ-009 roles, schema-driven, impartial):** the *Evidence Judge* validates source existence and identity, DOI/equivalent identifier, claim-to-source correspondence, whether the full text was actually examined, metrics and experimental conditions, dataset/baseline comparability, statistical uncertainty where available, contradictory evidence, and flags unverified or unsupported claims; the *Methodology Judge* independently evaluates experimental design, leakage, validity threats and interpretation. Model agreement is never proof; deterministic checks (identifier resolution, retrieval hashes, metadata cross-checks) are used wherever possible. Verified findings, disputed claims, model interpretations and creative hypotheses are stored with distinct evidence origins.

**Research artifacts (versioned, immutable, provider-independent — same `artifacts` mechanism as the director protocol):** research brief, search strategy, literature collection, source record (identifier, retrieval date, tier, hash, access path), extracted findings, claim–evidence mapping, methodological assessment, verification report, research synthesis, final report, follow-up discussion context. Reports stay usable after provider changes; source links, retrieval dates and limitations are preserved; results become reusable context for Misa conversations and the Creativity Studio (REQ-007).

**Acceptance criteria (future):** (1) every claim in a report cites a stored source record with identifier and tier, or is labelled unverified; (2) a claim whose source cannot be resolved is marked disputed/unverified, never silently kept; (3) abstract-only sources are labelled so in the report; (4) a report regenerated with a different provider keeps the same source records and mappings; (5) a research conversation can be continued from the stored discussion context with citations resolving to artifacts; (6) no provider receives content beyond the explicitly selected sources and the research brief.

Status: **deferred** (detail in `09_RESEARCH_BACKLOG.md`). Milestone: Sprint 6 as a feature module behind the extension registry (REQ-003), reusing orchestration, approvals, artifacts and model routing; providers via REQ-008.

### ZENITH-REQ-007 — Creativity Studio (future extension)
Capture owner-generated ideas with preserved provenance (who, when, from what); connect ideas to projects and research; generate alternative hypotheses and creative directions; compare potential novelty against available evidence; transform approved ideas into research or engineering plans; optionally integrate a dedicated creative model. Ideas are owner-owned and private by default (never sent to a provider without explicit per-idea consent); a generated idea is **not** assumed scientifically novel until verified against literature (REQ-006). Status: **deferred**, designed as a plugin/feature module (ADR-006) with its own domain (`ideas`, `idea_links`, `novelty_assessments`). Milestone: Sprint 7, after REQ-006 provides verification.

### ZENITH-REQ-008 — Model Independence
Misa uses a provider-independent intelligence interface. Model selection per task considers: task type; required reasoning quality; tool and execution capabilities; privacy restrictions (data classification, cloud consent); provider availability; monetary cost; user preferences. Misa is not a single monolithic agent prompt: capabilities are separate modules with versioned contracts.

| Element | Status | Milestone |
|---|---|---|
| Provider-independent adapter ports + versioned contracts (`zenith.director.v1`) | implemented (S2) | — |
| Role → adapter assignment (`ZENITH_ROLE_*`) | implemented (S2.5) | S3-07 (`zenith.toml`) |
| Model router (task type, quality, capabilities, privacy, availability, cost, preference) | planned; cost/usage accounting exists per run | S4 (deterministic router module) |
| Additional providers (e.g. Gemini, local models) | deferred; same port, same gating | after S3-09 live validation |

### ZENITH-REQ-009 — Misa Multi-Agent Specialist Coordination
Misa is a high-level coordinator that delegates specialised work through Zenith. Specialist **roles** are provider-independent logical capabilities — not necessarily separate models or processes: Research Lead, Literature Discovery Agent, Paper Analysis Agent, Methodology Judge, Evidence Verification Agent, Research Synthesis Agent, Engineering Director, Implementation Agent, Creative Reasoning Agent. Roles are dynamically composable and provider-independent: a reasoning model (e.g. the Research Lead) may **propose** a specialist team for a task, but Zenith decides deterministically what is admitted — authorization, budgets, concurrency limits and execution boundaries are enforced by code, and any role outside the task's approved set is refused. Roles communicate only through versioned structured contracts and persisted artifacts, never free-form agent-to-agent chat. Avoid unnecessary multi-agent execution and repeated model calls: one role per stage, bounded retries, per-task budgets (REQ-008 routing picks the provider per role).

| Element | Status | Milestone |
|---|---|---|
| Role → adapter assignment, ports, contracts, staged orchestration with budgets | implemented for Engineering Director / Implementation Agent (S2–2.6) | — |
| Role registry generalised beyond engineering (manifest declares roles it can fill) | planned | S3-08 (`zenith.modules`) |
| Deterministic role planner (which roles a task needs) | planned | Sprint 6 (with research module) |
| Research roles (Lead, Discovery, Analysis, Methodology Judge, Evidence Judge, Synthesis) | deferred | Sprint 6 |
| Creative Reasoning Agent | deferred | Sprint 7 |

**Acceptance criteria (future):** a task declares required roles and Zenith refuses to invoke any other; the number of model calls per task is bounded and recorded; two roles never exchange unstructured text; the same task runs with a different provider assignment without changing artifacts' schema; cost per role is attributable in run usage.

### ZENITH-REQ-010 — Misa Persona & Communication Policy
A configurable **Persona & Communication Policy** governs user-facing communication. Default personality: intelligent and observant; natural, conversational Turkish; familiar with the owner's informal style; direct and precise; subtly witty; occasionally gently teasing; reserved in praise; constructive in criticism; respectful under all circumstances. Rules: no generic compliments ("Harika fikir!", "Mükemmel iş!") without substantive justification — praise is understated and tied to actual achievements; criticism is honest, specific, evidence-based and constructive; humour targets situations or contradictions, never personal vulnerabilities; no condescension, passive aggression, humiliation, repetitive sarcasm or forced familiarity; for security incidents, deadlines, errors, financial consequences and other important risks **clarity beats humour**; **never invent an observation to make a witty remark** — every remark must be grounded in stored evidence.

**Style separation:** the persona applies to interactive Misa conversations, Telegram notifications and creative brainstorming; internal structured agent communication, scientific research reports, scientific judges and engineering verification reports remain impartial and schema-driven. Owner-adjustable, independently: humour intensity, verbosity, notification frequency, conversational tone (stored as configuration). **Deterministic Turkish templates** are for reliable notifications and fallbacks (and any message that must be exact); Misa's **primary conversational experience** will use model-generated, context-grounded language governed by this policy — the policy constrains the model (grounding in stored evidence, no invented observations, risk-class clarity), it is not a hidden mega-prompt.

| Element | Status | Milestone |
|---|---|---|
| Identity and signature | implemented (REQ-004) | — |
| Persona policy configuration (humour, verbosity, frequency, tone) | planned | S3-07 (`zenith.toml`) |
| Deterministic Misa message templates with provenance labels (notifications, fallbacks) | planned | S3-03 |
| Model-generated conversational Misa turns governed by the persona policy (grounded, provider-independent) | planned | S4 (after REQ-008 router) |
| Persona applied to Telegram notifications under notification policy | deferred | S4 |
| Turkish-language user-facing text (engineering prompts stay English) | planned | S3-03 |

**Acceptance criteria (future):** every user-facing Misa statement about state cites evidence ids or is labelled as opinion; no praise template fires without a verified achievement reference; risk-class messages (security, deadline, error, cost) render with the humour channel disabled; a humour-intensity setting of 0 yields zero witty remarks; a Turkish-fluency review of the default templates passes the owner; tone settings change output without changing facts.

### ZENITH-REQ-011 — Project Understanding (authorized content retrieval)
Misa must eventually retrieve, selectively and on request, authorized **project-document content**, **experiment results** and **relevant source-code sections** to answer deeper questions — never by unrestricted recursive file reading and never by sending a private repository to an external provider automatically. Rules: retrieval is query-driven and bounded (owner-named `context_files`, size caps, name-based sensitive exclusion, strict policy for content); each excerpt keeps provenance (path, revision, retrieval time, hash) and is cited like any other item; cloud transmission requires the project's cloud-consent flag; experiment results are evidence with their own origin (planned `EXPERIMENT_RESULT`). Status: **planned** (the director context dir already implements the owner-selected-files pattern; conversation uses summaries only). Milestone: after the live conversation validation (S3-02b), as an extension of the context assembler.

## Vision domains

| Domain | Capability | Status |
|---|---|---|
| **Project Intelligence** | Project discovery (new directory → suggest/link) | planned |
| | Multi-source project memory | implemented (S1) |
| | Evidence-led progress | implemented (S1) |
| | Task dependencies | implemented (S1) |
| | Decision history | implemented (S2, `decisions` table) |
| | Proactive project check-ins | deferred (S4) |
| **Personal Intelligence** | Commitments, deadlines, due listings | implemented (S1, basic) |
| | Prioritization, attention/workload management | planned |
| | Daily/weekly briefings | implemented (deterministic `brief`); digests deferred (S4) |
| **Agent Intelligence** | Engineering Director | simulated (REQ-001) |
| | Multi-agent collaboration (planner→implementer→reviewer→verifier) | simulated |
| | Model routing | planned |
| | Agent lifecycle (leases, cancel, timeouts, crash recovery) | implemented |
| | Cost and permission governance | permissions implemented; cost accounting planned (live adapters) |
| **Machine Intelligence** | Multiple Linux nodes, offline reconciliation | planned (node_id recorded today) |
| | tmux, Hyprland workspace management | deferred (S3) |
| | Remote access; Claude Remote Control | deferred, optional, never a core dependency |
| **Communication Intelligence** | Telegram notifications/approvals | deferred (S4) |
| | Gmail, Google Calendar | deferred (S5) |
| **Research Intelligence** | Literature discovery, source verification, DOI references, methodology comparison, source-grounded reports, scheduled runs, research conversations | deferred — REQ-006 (`09_RESEARCH_BACKLOG.md`) |
| **Creativity Studio** | Idea capture with provenance, hypothesis generation, novelty-vs-evidence comparison, idea → plan | deferred — REQ-007 |
| **Model Independence** | Provider-independent interface, per-task model routing | ports implemented; router planned — REQ-008 |
| **Multi-Agent Coordination** | Specialist roles, deterministic role planning, bounded calls | engineering roles implemented; research/creative roles deferred — REQ-009 |
| **Persona & Communication** | Configurable Turkish persona, style separation, notification quality | identity done; persona policy planned — REQ-010 |
