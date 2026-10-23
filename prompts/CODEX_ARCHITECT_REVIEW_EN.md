# Codex — Independent Zenith v1.0 architectural review (EN)

You are a senior systems architect conducting an **independent, read-only** review of Zenith before large-scale coding. Do not modify files, run any destructive commands or invoke another coding model.

## Review corpus
`README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/01`–`docs/09`, ADRs, `src/zenith/`, `tests/`, and `prompts/CLAUDE_IMPLEMENTATION_EN.md`.

## Objectives
1. Check whether the architecture meets the owner's goals: independent core, code-aware project memory, two-way Claude/Codex coordination, multi-PC awareness, tmux/Hyprland UI, Telegram updates, OAuth sources and future research intelligence.
2. Find contradictions between requirements, schema, states, approval policies, provider authentication, worktree permissions and roadmap.
3. Assess real agent capabilities and version sensitivity using official docs only when necessary. Never propose re-routing consumer subscription credentials as a generic third-party API.
4. Produce **concrete design findings** prioritized as P0 (blocking safety), P1 (MVP functional), P2 (can defer); include a file/section and a minimal remedy for each.
5. Check for risks: false verification, prompt injection, stale multi-device context, unlimited recursive agents, double execution, approval replay, secret exfiltration, incorrect Codex JSONL parsing, CLI version drift, crash recovery.
6. Propose an improved acceptance-test matrix with exact test names and preconditions.
7. Reconcile design for personal terminal workflows vs unattended service execution with allowed providers.

## Required output (English Markdown)
- Executive assessment: ready/not ready for Sprint 1 and why.
- A concise **system boundary table** for Core, Node, Hub, Orchestrator, adapters and policy/verifier.
- Architecture review findings table: priority, evidence, risk, recommendation.
- Recommended fixes to the Sprint 1 backlog, ordered and estimated by relative complexity (S/M/L; not time promises).
- Explicit confirmation that academic Research Intelligence is **deferred**, not accidentally in MVP.
- Questions only when a truly blocking unresolved architectural decision exists; otherwise state assumptions.

Do not implement edits. Do not merely rephrase the documents or invent completed capabilities.
