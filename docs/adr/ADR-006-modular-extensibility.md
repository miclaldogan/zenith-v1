# ADR-006 — Modular extensibility: hexagonal core, entry-point adapters, no plugin platform yet
**Proposed 2026-10-23 (REQ-003). Needs owner acceptance.**

## Context
REQ-003 requires independently developed, installable, enable/disable-able capabilities without touching core business logic, across engineering, research, personal, communication and machine intelligence. Sprint 2 was reviewed against it.

## Compatibility review of the Sprint 2 design
- **Ports already exist** for the two provider roles (`DirectorAdapter`, `ImplementerAdapter`): Protocol-typed, dict-in/dict-out, no kernel imports, validated by versioned contracts (`zenith.director.v1`). The orchestrator depends on the port, not on the fake.
- **Core stays generic**: projects, sources, tasks, approvals, evidence, events, artifacts, decisions, policy. Nothing in the kernel assumes a project is a repository; engineering-specific logic (worktrees, verification, sandbox, orchestration) is a feature module that *uses* the core. It currently lives in-package; moving it under `zenith.engineering` is a mechanical, behaviour-neutral change scheduled for the next sprint.
- **Gaps found**: adapters were selected by a hard-coded dict; no manifest, no enablement, no contract-version check. Fixed by `zenith.extensions` (this sprint).

## Decision
1. **Deployment**: modular monolith; one process, one SQLite database; clean ports so Node/Hub can split later (ADR-001).
2. **Layers** (REQ-003 §1–6): (a) core domain — stable, never a plugin; (b) first-party feature modules — importable packages that call core APIs and register their own CLI/schema migrations; (c) provider adapters — implement a port, discovered by entry points; (d) optional plugins — the same entry-point mechanism, installed as separate distributions; (e) MCP — a later exposure surface over the core API, subject to the same policy engine; (f) out-of-process workers — the required home for anything untrusted or privileged (today: sandboxed commands; later: a JSON-over-stdio worker protocol).
3. **Mechanism**: Python package **entry points** (`importlib.metadata`, stdlib) in group `zenith.adapters`, yielding an `AdapterManifest` (name, kind, contract version, permissions, live flag, version). Built-ins cannot be shadowed. **Pluggy** was evaluated: it is the right tool once feature modules need *hook* semantics (e.g. `on_task_state_changed`, `on_evidence_recorded`); for discovery alone it adds a dependency without benefit, so it is deferred, not rejected. No hot reloading, no marketplace, no distributed plugin platform.
4. **Security**: dynamic import is *not* isolation — a plugin runs with Zenith's privileges. Therefore: live or privileged adapters must be enabled explicitly (`ZENITH_ENABLED_ADAPTERS`); every plugin goes through the kernel for approvals, evidence, events and policy (there is no other API); permissions are declared in the manifest and displayed by `zenith adapters`; a plugin that needs more than the port offers runs out-of-process.
5. **Contracts and testing**: extension contracts are versioned (`contracts.SCHEMA_VERSION`); an adapter whose contract version differs is refused; the fake-adapter test suite is the contract test every adapter must pass before enablement.
6. **Configuration and lifecycle**: environment variables now; a `zenith.toml` with per-module `enabled`, `config` and `permissions` sections is the next step; lifecycle is `discover → validate manifest → enable → instantiate per use`; failure isolation = a broken entry point is reported as `broken:<name>` and never crashes discovery.

## Consequences
Replaceable providers without touching the orchestrator; a clear place for future modules (research, communication, machine); a documented path for plugins that does not pretend to be a security boundary. Cost: one more module, and a planned package move.
