# ADR-001 — Local-first modular monolith
**Accepted 2026-10-23.** Start with Python + SQLite + XDG paths, modular core/node within one process. Separate Hub/Node network boundary later, and preserve agent/provider adapters. Avoid premature distributed infrastructure and vendor-locked state. Revisit on measured concurrency or multi-device coordination requirements.
