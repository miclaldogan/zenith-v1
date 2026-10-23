# Sprint 1 — Acceptance audit, hardening and verification record

**Date:** 2026-10-23 · **Scope:** independent audit of the Sprint 1 implementation, limited hardening (schema v3), Sprint 2 preparation. No agent execution was added. The owner's live database was not opened for writing (see §5).

## 1. Acceptance status

**Accepted with hardening applied.** All Sprint 1 gates from `docs/07_ROADMAP_TEST_PLAN.md` hold and are covered by tests: (1) two directories in one project, (2) manual progress never shown as verified, (3) scan comparison shows the real revision change, (4) no `.env` / symlink-escape read, (5) migration and restart tests. The audit found no P0. Six P1 and five P2 findings were fixed in this pass; the rest are documented as accepted limitations or Sprint 2 work.

## 2. Audit findings

| ID | Sev. | Finding | Disposition |
|---|---|---|---|
| F1 | P1 | Source paths could nest (parent/child) across or within projects: ambiguous ownership, double-counted observations. | **Fixed.** Code check (`policy.path_relation`) + schema v3 triggers `sources_no_overlap_*`; `doctor` reports pre-v3 overlaps. |
| F2 | P1 | Check-then-write methods read outside the write transaction, so two processes could both pass a check; a unique-index violation surfaced as a raw `sqlite3.IntegrityError` (CLI traceback). | **Fixed.** `Store.write()` issues `BEGIN IMMEDIATE` before the checks; `IntegrityError` mapped to `ZenithError`; CLI maps any `sqlite3.Error` to exit 3 without traceback. |
| F3 | P1 | `events` (the audit log) could be edited or deleted by ordinary SQL; only `evidence` was protected. | **Fixed.** v3 triggers `events_no_update` / `events_no_delete`. |
| F4 | P1 | Timestamps had 1 s resolution: a scan and a check-in in the same second could not be ordered; evidence order used `rowid`, which is not stable across `VACUUM`. | **Fixed.** Microsecond UTC timestamps (lexicographically compatible with the old values); `evidence.seq` assigned inside the write transaction, gap-free, immutable. |
| F5 | P1 | `ZENITH_ALLOWED_ROOTS` was enforced only when a source was registered, not when scanned. | **Fixed.** Scan reports `unavailable / outside_allowlist`. |
| F6 | P1 | The documented test command (`python -m unittest discover -s tests -v`) failed on a clean checkout without an editable install. | **Fixed.** `scripts/test.sh` (venv if present, otherwise source tree); docs updated. Verified from a clean copy: 75/75 with no installation and no network. |
| F7 | P2 | `git status` counted changes for the whole repository when a source was a sub-directory of it. | **Fixed.** Pathspec limited to the source directory. |
| F8 | P2 | Corrupt JSON in `snapshots.details` / `evidence.details` raised a raw `ValueError`; the brief would crash. | **Fixed.** `ZenithError("Corrupt … record")`; the brief shows `corrupt_record` and status `UNKNOWN`. |
| F9 | P2 | A failed backup could leave a partial `.db` file at the destination. | **Fixed.** Only the file that call was writing is removed; earlier backups are never deleted. |
| F10 | P2 | Conservative name exclusion hid legitimate code such as `secrets.py` from metadata fingerprints. | **Narrowed.** `sensitive_reason(strict=False)` for the metadata scanner only lets *code-suffixed* files whose name merely contains `secret`/`credential` count; `.env`, keys, stores, `credentials`, `.ssh/` stay excluded in both modes. Names never appear in output. Any future content reader must use `strict=True` (default). |
| F11 | P2 | `migrations.apply_pending` crashed on a `str` path. | **Fixed.** |
| F12 | P2 | `_parse_observed` truncated "now" to seconds (introduced by F4's fix, caught by the existing offline check-in test). | **Fixed.** Full precision. |
| L1 | accepted | Append-only triggers stop *application* mistakes; a local user with `sqlite3` can drop the triggers. | Documented in `05_SECURITY_POLICY.md`. Cryptographic chaining is a Hub-era item. |
| L2 | accepted | `approvals` has no `used_at`; one approval can satisfy several `QUEUED` transitions while unexpired. Harmless now (nothing executes), required before Sprint 2 execution. | Sprint 2 task S2-01. |
| L3 | accepted | A scan may spend up to 5 × 10 s in `git` sub-processes plus 10 s in the tree walk. | Documented. |
| L4 | accepted | `schema.migrated` event is written after the migration transaction commits; a crash in between loses the event, not the migration. | Documented. |
| L5 | accepted | Opening a WAL database read-only (e.g. `backup`, `doctor`) can create `-shm`/`-wal` sidecar files; harmless. | Documented. |
| L6 | accepted | No `restore` sub-command; restore is a documented manual step. | `08_OPERATIONS.md`. |
| L7 | accepted | `task_dependencies` rows are deletable by design (`task undepend`), with an event. | — |

## 3. Hardening change summary (schema v3)

- `src/zenith/migrations.py`: `_m003_integrity_and_ordering`; backup cleanup on failure; `str` paths; test-only pre-commit hook for crash injection.
- `src/zenith/kernel.py`: autocommit connections + `write()` (`BEGIN IMMEDIATE`); nested-path refusal; `IntegrityError` mapping; `evidence.seq`; microsecond `utcnow()`; allowlist at scan time; `source_overlaps()`; `_load_json`; `BUSY_TIMEOUT_MS`.
- `src/zenith/policy.py`: `strict` flag, `path_relation()`.
- `src/zenith/scanner.py`: non-strict name policy; `git status -- .`.
- `src/zenith/brief.py`: `corrupt_record` state; `seq` ordering.
- `src/zenith/cli.py`: `doctor` shows expected version, upgrade hint and overlaps; exit 3 for database errors, 130 on interrupt.
- `scripts/test.sh`, `tests/test_hardening.py` (25 tests), fixture updates in `tests/test_sprint1.py`.

No Sprint 0/1 behaviour changed except: nested sources are now refused (security fix, F1) and timestamps carry microseconds.

## 4. Test results

```
$ scripts/test.sh            # = .venv/bin/python -m unittest discover -s tests -v
Ran 75 tests in 6.6s  OK     (test_kernel 6 · test_sprint1 44 · test_hardening 25; 0 failures, 0 skips)
```
Also run: the same 75 from a clean copy of the tracked files with no venv and no network (`scripts/test.sh` fallback). A fresh `python -m venv` + `pip install -e .` needs network once for setuptools (Python 3.14 venvs ship none); that is the `bootstrap.sh` path.

## 5. Migration and backup verification (temporary databases only)

| Check | Evidence |
|---|---|
| Projects, tasks, approvals, events, snapshots survive v1→v3 | `test_v1_to_latest_preserves_every_table` |
| v2→v3 assigns `seq` in recording order; new rows continue it | `test_v2_to_v3_assigns_gap_free_seq_in_recording_order` |
| Backup complete, readable, integrity ok, v1 schema intact | `test_schema_upgrade_preserves_existing_state`, rehearsal below |
| Migration failure rolls back; backup kept; retry succeeds | `test_failed_migration_rolls_back_and_keeps_backup`, `test_later_failed_migration_leaves_previous_version` |
| Process killed after DDL, before COMMIT | `test_crash_before_commit_leaves_previous_version_intact` (sub-process `os._exit`): file integrity ok, still v1, retry completes |
| Newer schema refused | `test_refuses_downgrade` |
| `integrity_check` / `foreign_key_check` after migration | asserted in the tests above |
| Restore rehearsal from a backup copy | `test_restore_rehearsal_from_backup_copy` |
| Repeated `init` idempotent, no extra backups | `test_repeated_init_is_idempotent` |
| Failed backup leaves no partial file and blocks the upgrade | `test_failed_backup_leaves_no_partial_file_and_blocks_migration` |

**Rehearsal on a byte-copy of the owner's live database** (`cp` of `zenith.db`; its WAL was 0 bytes, so the copy is consistent; the live file was never opened by this session's code): v1 → v3 in one `zenith init`; backup `live-copy.db.pre-v3-20261023T125202Z.bak` written first (v1, integrity ok); after: version 3, integrity ok, no FK violations, one `schema.migrated` event; second `init` no-op. The live database contains **0 projects, 0 tasks, 0 events** — the real upgrade carries no user data. Live file SHA-256 before and after this session: `0183678c…` (unchanged).

**Owner-approved procedure (not executed):**
```bash
zenith doctor                                   # expect schema_version 1, schema_expected 3
zenith backup --out ~/zenith-backups/zenith-pre-v3-$(date +%F).db   # optional extra copy
zenith init                                     # writes zenith.db.pre-v3-<UTC>.bak, migrates
zenith doctor                                   # expect schema_version 3, source_overlaps []
```
**Rollback:** stop Zenith; move `zenith.db` (and any `-wal`/`-shm`) aside; copy the `.pre-v3-*.bak` file to `zenith.db`; run the **previous** Zenith version. Backups are never deleted by Zenith.

## 6. ADR-004 disposition

**Recommend: Accept, as amended by schema v3.** Amendments: events are now trigger-protected (ADR-004's own "consequence" item), evidence has a gap-free `seq`, nested sources are refused, timestamps carry microseconds. The non-cryptographic nature of the protection is stated explicitly. Owner sign-off required to move the status from *Proposed* to *Accepted*.

## 7. Unresolved risks and deferred improvements

- **Before any execution (Sprint 2):** approval consumption (`used_at`) in the same transaction as the worker lease; `RECONCILE_REQUIRED` state; process sandboxing is *not* provided by Git worktrees (ADR-005).
- Append-only protection is not tamper-evident against a privileged local user (L1).
- `doctor` lists overlaps but does not resolve them; the owner decides which source to detach.
- Scanner time bound is per phase, not global (L3).
- Deferred features remain in the roadmap untouched: Telegram, tmux, Hyprland, multi-machine sync, Gmail/Calendar, proactive check-ins, Research Intelligence, Claude Remote Control (optional, future).
