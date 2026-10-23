# 08 — Local Operations / Arch Linux Runbook

## Bootstrap
```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e .
zenith init
zenith doctor
python -m unittest discover -s tests -v
```
No CLI credentials required. Prefer dedicated unprivileged user for future headless workers; do not run workers as root.

## Data paths
- `ZENITH_DB_PATH` environment override; otherwise `${XDG_DATA_HOME:-~/.local/share}/zenith/zenith.db`.
- Back up SQLite with `sqlite3.Connection.backup()` API to avoid inconsistent copies during WAL use.
- Never store API credentials in project DB. Future system secret/keyring storage only.

## Useful commands
```bash
zenith init                       # create or upgrade the database (backup first)
zenith doctor                     # tools + schema_version
zenith project add --name "Demo" --path /absolute/path/to/demo
zenith project sources add --project PROJECT_ID --path /another/dir --label notes
zenith project sources list --project PROJECT_ID [--all]
zenith project sources move --source SOURCE_ID --path /new/location     # IDs stay stable
zenith project sources remove --source SOURCE_ID                         # detaches; never touches the directory
zenith project sources diff --source SOURCE_ID                           # last two distinct snapshots
zenith scan --project PROJECT_ID [--source SOURCE_ID | --all]           # read-only metadata scan
zenith checkin --project PROJECT_ID --note "short update" [--node laptop2] [--at 2026-10-20T18:00]
zenith evidence list --project PROJECT_ID [--origin HUMAN_REPORT]
zenith evidence add --project PROJECT_ID --origin MODEL_INFERENCE --summary "..."
zenith brief [--project PROJECT_ID] [--format text|json]
zenith task add --project PROJECT_ID --title "Review tests" --risk L2 --due 2026-11-01
zenith task depend --id TASK_ID --on OTHER_TASK_ID
zenith task list [--open] [--overdue] [--due-within 7] [--blocked] [--state DRAFT]
zenith task transition --id TASK_ID --to CONTEXT_READY
zenith events --limit 20
zenith backup --out /path/to/new-backup.db
```
Task state transitions are intentionally manual and execution is unavailable; **do not manually mark completion** without actual verification. `task transition` enforces the defined graph, approval gates and dependencies.

## Evidence semantics (what the labels mean)
| Origin | Confidence | Produced by |
|---|---|---|
| `HUMAN_REPORT` | `REPORTED` | `zenith checkin` / `evidence add` |
| `DOCUMENT` | `REPORTED` | `evidence add` (a claim about a document; Zenith does not read it) |
| `MODEL_INFERENCE` | `INFERRED` | `evidence add` |
| `REPOSITORY_OBSERVATION` | `OBSERVED` | `zenith scan` only |
| `TEST_VERIFICATION` | `VERIFIED` | deterministic verifier only (Sprint 2); no CLI path |

A brief never reports `VERIFIED` unless a passing verification matches the currently observed, clean HEAD of a freshly scanned source. Sources older than 7 days are *stale*, check-ins older than 14 days are stale, and unscanned or unavailable sources are reported as *unknown*.

## Scan policy
- A source is only scanned if it was explicitly registered. `/`, the home directory itself and credential-store directories are refused. Set `ZENITH_ALLOWED_ROOTS=/a:/b` to restrict where sources may live.
- File contents are never read. Skipped and only counted: symlinks (never followed, escaping ones counted separately), `.env*`, `*.pem`, `*.key`, `id_*`, `*secret*`, `*credential*`, `.netrc`, `.ssh/`, `.aws/`, `.gnupg/` and similar. Files matching those names are invisible to Zenith by design (so `secrets.py` will not appear in fingerprints).
- Git is called with `core.fsmonitor=false` and `--no-optional-locks`, so a repository's own config cannot execute commands and a scan does not touch `.git/index`.
- If a registered directory is replaced by a symlink, the source is reported unavailable (`path_resolves_elsewhere`).
- Timestamps have 1-second resolution; "newer evidence" means strictly later.

## Backup, upgrade and restore
```bash
zenith backup --out ~/zenith-backups/zenith-$(date +%F).db   # consistent even with WAL; refuses to overwrite
zenith init                                                   # after upgrading Zenith: backup + migrate
```
- Upgrades write `zenith.db.pre-v<N>-<timestamp>.bak` next to the database, apply each migration in one transaction, and roll back on any error (the backup path is in the error message). Zenith never deletes these backups; remove old ones yourself.
- Zenith refuses to run on an older schema until `zenith init` is run, and refuses to open a database newer than itself.
- **Restore** = stop anything using the database, then point `ZENITH_DB_PATH` at the backup copy (or copy it over the live path after moving the live file aside), and run `zenith init`. Do not delete `-wal`/`-shm` files while processes are running.

## Service deployment (future)
- `ops/systemd/` includes a **non-enabled** illustrative service sketch. No unattended worker is installed by this package.
- A `systemd --user` unit belongs to its normal user and may require user lingering to run after logout; do not enable lingering automatically.
- Offline work needs an online workstation; physical poweroff requires separate always-on Hub and appropriate wake support.

## Failure handling and troubleshooting
- `doctor` reports local tools available, not authenticated status.
- Test with temp paths before importing real projects.
- `database is locked`: check concurrent writers and SQLite WAL; do not delete WAL/SHM while processes are running.
- `git` failures: no crash if project is not a repo; collect `git_available` status and explain.
- Upgrading Claude/Codex/Hyprland: version-probe before using CLI flags/dispatchers; integration contract tests before auto-update.
- Remote machine stale: show timestamp and ask for human check-in; never infer live state.

## Release discipline
Versions, migrations, backup/restore tests, changelog and signed archive before any broad autonomous operation.
