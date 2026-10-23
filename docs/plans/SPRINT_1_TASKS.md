# Sprint 1 — Agent-ready issue breakdown

Checked items are implemented and covered by `tests/test_sprint1.py`; see the notes under each.

- [x] **S1-01 Migrations:** establish numbered migration engine and schema tests upgrading from Sprint-0. Gate: old DB remains readable; failed migration preserves backup.
- [x] **S1-02 Multi-source registry:** project sources table and CLI add/list/remove; stable IDs; no duplicate path claims across projects. Gate: two paths in one project.
- [x] **S1-03 Safe scanner:** source allowlist, symlink boundary check, sensitive file filter, bounded file count/time, metadata fingerprint. Gate: `.env`, private keys and out-of-root symlink excluded.
- [x] **S1-04 Evidence ledger:** source refs, provenance, observed timestamp, confidence, digest. Gate: human report cannot satisfy test verification.
- [x] **S1-05 Snapshot diff:** compare metadata and Git revisions across scans; show stale/missing source and unknowns. Gate: non-Git paths still supported.
- [x] **S1-06 Commitments/dependencies:** deadline parsing, tasks blocked by a dependency, read-only due listing. Gate: deterministic Europe/Istanbul display conversion.
- [x] **S1-07 Check-ins:** store user update on offline second machine with REPORTED provenance; reconcile when later evidence arrives. Gate: stale signal visible.
- [x] **S1-08 Safe CLI briefing:** deterministic next-action report with citations to stored evidence; no real API key. Gate: missing source yields unknown rather than hallucinated state.
- [x] **S1-09 Security/restore suite:** basic path injection tests, SQLite backup/restore, read-only regression and redaction. Gate: automated tests all green.
- [x] **S1-10 Documentation:** update what actually works; no fabricated production claims.

### Execution ordering
S1-01 → S1-02 → S1-03/S1-04 → S1-05/S1-07 → S1-06/S1-08 → S1-09/S1-10. Keep changes reviewable. Stop before agent automation and Gmail integration.

### Implementation notes / deviations
- S1-02: no `project sources` duplicate check for *nested* paths across projects (exact duplicates are rejected). Sources are soft-removed.
- S1-04/S1-08: nothing can reach `VERIFIED` from the CLI because Sprint 1 has no test runner; the gate is proven through `Store.record_test_verification` (verifier-only actor).
- S1-05: comparison stores aggregate counts and a fingerprint, not a per-file manifest, so it reports *that* files changed, not which.
- S1-06: only `zenith task list` filters ("read-only due listing"); no reminders.
- S1-09: no CLI `restore`; restore is documented as pointing `ZENITH_DB_PATH` at a backup.
- Optional Textual TUI from the roadmap was not built.
- **Acceptance pass (schema v3):** nested sources refused, events immutable, evidence `seq`, microsecond timestamps, allowlist at scan time, `scripts/test.sh`. Full record: `SPRINT_1_ACCEPTANCE.md`.
