# ADR-002 — Independent task contract and evidence
**Accepted 2026-10-23.** Zenith owns task state, approval, evidence and verification. Claude Code and Codex generate advisory claims; deterministic test/Git evidence confirms completion. Agents communicate through `zenith.acp.v1` contracts and a task scheduler, not an unbounded prompt-passing loop. No single provider is a system of record.
