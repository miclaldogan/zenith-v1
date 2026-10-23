# 08 — Local Operations / Arch Linux Runbook

## Bootstrap
```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e .
zenith init
zenith doctor
scripts/test.sh            # or: python -m unittest discover -s tests -v inside the venv
```
From a clean checkout without a venv, `scripts/test.sh` runs the suite from `src/` with no installation. No CLI credentials required. Prefer dedicated unprivileged user for future headless workers; do not run workers as root.

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

Exit codes: `0` ok · `2` validation/state error (`Zenith error: …`) · `3` database error such as `database is locked` after the 5 s busy timeout, or a malformed file (nothing was committed) · `130` interrupted.

## Install and daily commands
```bash
scripts/install.sh [--editable]            # ~/.local/share/zenith/venv + ~/.local/bin/zenith + fish completions
zenith doctor [--full]                     # tools, db schema vs expected, sandbox, providers and their auth configuration, install path
zenith project discover --root DIR [--max-depth 3]      # dry run: git repos and marker dirs under an approved folder (never ~ or /)
zenith project discover --root DIR --add 1,3            # register selected candidates as projects
zenith project discover --root DIR --attach 2 --project ID   # attach one candidate as a source of an existing project
zenith project inspect --id ID             # read-only: sources (local/remote node), freshness, HEAD, verification applicability, open work
zenith project docs --id ID [--allow GLOB ...] [--cloud-consent yes|no]   # local doc permission vs cloud transmission permission (separate)
zenith misa --live-plan                    # the authorized live test, spelled out; no call
zenith misa --live-check "soru" [--authorize-live-call]   # ONE live turn on synthetic data (needs enabled adapter, key file, ACK)
```
Discovery reads directory and marker *names* only, skips hidden/build/credential directories and symlinks, and stops at depth 3 / 2000 directories. Registration keeps the Sprint 1 identity rules (canonical paths, no nesting, one owner per directory). Repository text (recent commit subjects, permitted docs) is marked untrusted and stays local unless the project has `cloud_consent`.

## Misa session (offline, deterministic)
```bash
zenith                        # opens Misa: evidence-grounded opening, then a conversation; Ctrl-C / `çık` / `exit` save state
zenith misa --once "nerede kaldım"      # one question, one answer, exit
printf 'projeler\nproje 1\ngörevler\nçık\n' | zenith    # scripted (stdin not a TTY)
```
Understands Turkish and English (reply language follows your message; internal operations stay English): `projeler`/`projects`, `proje <ad|no>`, `durum`/`status`, `nerede kaldım`/`where did I leave off`, `kanıt`/`evidence`, `kararlar`/`decisions`, `görevler`/`tasks`, `kaynaklar`/`sources`, `geçmiş`/`history`, `onaylar`/`approvals`, `sırada ne`/`next`, `not: <metin>` / `note: <text>` (check-in → HUMAN_REPORT, never verified), `yardım`/`help`, `çık`/`exit`. Every claim cites ids (`[ev:…]`, `[task:…]`, `[run:…]`); unavailable or never-scanned sources are reported as such. Conversation state and turns persist in `conversations` / `conversation_turns` (append-only, `generated_by=deterministic`; future model turns will carry `model:<provider>:<model>`).

Conversation (primary): ask naturally — "SırdaşApp'te nerede kalmıştık?", "Bu projeyi neden ertelemiştik?", "Bugün hangi işe odaklanmam mantıklı?", "Hangi projelere uzun zamandır uğramadım?", "Bunu not al, haftaya devam edeceğiz", "Peki sonra?", "O projede kaynaklar bayat mı?", "kaynakları göster". Misa resolves the project (typos and suffixes tolerated; ambiguity is asked back), retrieves a bounded set of records, and answers in prose that separates intended / reported / observed / verified / unconfirmed. Notes and tasks proposed in conversation are staged and executed only after an explicit `evet`/`yes`; approvals and agent runs stay CLI-only. Enable a model for the conversation role with `ZENITH_ROLE_CONVERSATION=claude-code-conversation` + `ZENITH_ENABLED_ADAPTERS=claude-code-conversation` (same credential/sandbox gating as other live adapters); otherwise the offline composer answers and the banner says "çevrimdışı mod".

Selection semantics: the **active project** is only ever the one you selected explicitly (`proje <ad>`, or a number from a listing shown *in this session*); the opening also names the project with the most recent evidence but never switches to it. Check-ins go to the active project or to an explicit `not @Proje: …`; a note that names another registered project is refused until you target it. Source names are shown as labels/basenames; `kaynaklar detay` / `sources detail` shows full paths. The verification line states what was verified, at which revision, when, and whether it still applies (`source fresh` / `HEAD moved` / `verified source unavailable` …); an unrelated optional source being unavailable never downgrades a valid verification.

Migration prerequisites for the live database (v1 → v5): stop any running `zenith` process; run `zenith doctor` (expect `schema_version` 1, `schema_expected` 5); run `zenith init` once — it writes `zenith.db.pre-v5-<UTC>.bak` first and migrates v1→v2→v3→v4→v5 in separate transactions; verify with `zenith doctor` (version 5, `source_overlaps []`). Rollback: restore the `.bak` over `zenith.db` (remove `-wal`/`-shm` first); the current code then refuses until `init` is run again. Rehearsed on a byte-copy on 2026-10-23.

## Engineering runs (fake adapters only — no live model)
```bash
zenith sandbox doctor                                   # isolation baseline; ok must be true to execute anything
zenith adapters                                         # discovered adapters, enablement, permissions
zenith task add --project P --title "..." --risk L2 --allow 'src/**' --exclude 'tests/**' \
   --acceptance "python -m unittest discover -s tests" [--artifact 'dist/*.whl']
zenith director plan --task T                           # context → proposal + prompt artifacts; task PLANNED
zenith task transition --id T --to AWAITING_APPROVAL
zenith task approve --id T [--minutes 60] [--max-uses 1]   # single-use, scope-hashed task.execute
zenith task transition --id T --to QUEUED
zenith run create --task T [--max-attempts 3]           # idempotent
zenith worker --run R                                   # lease + consume approval → implement → verify → review → finalize
zenith run status --run R ; zenith run list ; zenith artifacts list --task T ; zenith artifacts show --id A ; zenith decisions --project P
zenith run cancel --run R
zenith worker --reconcile                               # expired leases → RECONCILE_REQUIRED (run after a crash/reboot)
zenith task approve --id T && zenith run reconcile --run R --outcome retry --reason "..."   # or blocked|failed|cancelled
zenith run accept --run R --reason "..."                # owner overrides reviewer disagreement; needs a passing verification
zenith task revoke --approval A --reason "..."
zenith demo poc [--scenario success|out_of_scope|false_claim|disagree|crash] [--dir DIR]   # throwaway repo + DB
```
Run outcomes: `COMPLETED` (verified and reviewed), `BLOCKED/correction_required` (verification failed; a revised prompt was persisted; approve again and re-queue to retry), `BLOCKED/reviewer_disagreement` (verified, director wants changes; `run accept` or retry), `BLOCKED/review_unavailable`, `FAILED/attempts_exhausted`, `CANCELLED`, `RECONCILE_REQUIRED` (crash or lost lease; worktree kept under `$ZENITH_STATE_DIR/worktrees/<run>` for inspection; the source stays locked until reconciled). State dir: `ZENITH_STATE_DIR` or `~/.local/state/zenith`. Worktree registration writes to the source repository's `.git/worktrees/` (Git metadata only); the primary working tree is never modified.

## Live provider adapters (prepared; disabled by default)
```bash
zenith adapters list                                   # 6 adapters: 2 fake (enabled), 4 live (disabled until named below)
export ZENITH_ENABLED_ADAPTERS=claude-code-director,claude-code-implementer   # explicit enablement per adapter
export ZENITH_CLAUDE_OAUTH_TOKEN_FILE=~/.config/zenith/claude_token          # subscription: token from `claude setup-token`, 0600
#   or: export ZENITH_ANTHROPIC_API_KEY_FILE=~/.config/zenith/anthropic_api_key  # API key (Console billing) -> --bare
# Zenith never reads either file; the sandbox forwarder hands it to the CLI as an environment variable.
export ZENITH_CODEX_AUTH_FILE=~/.codex/auth.json                              # owner's explicit choice
zenith adapters probe --adapter claude-code-director [--version-check]        # preflight, exact argv, blockers; no model call
zenith smoke --adapter codex-director --repo /tmp/disposable                  # preflight only
ZENITH_LIVE_SMOKE_ACK=I-AUTHORIZE-ONE-LIVE-PROVIDER-CALL zenith smoke --adapter codex-director --repo /tmp/disposable --authorize-live-call
zenith smoke --adapter claude-code-director --implementer claude-code-implementer --full          # preflight + authorization request
ZENITH_LIVE_SMOKE_ACK=... zenith smoke --adapter claude-code-director --implementer claude-code-implementer --full --authorize-live-call --max-budget-usd 0.25
zenith identity show                                   # REQ-004: ids, display names, roles, signature, co-authors
zenith identity commit-preview --subject "..." [--body "..."] [--assistant-contributed]   # preview only; no commit
zenith director plan --task T --director codex-director                      # once enabled and authenticated
zenith worker --run R --director codex-director --implementer claude-code-implementer
```
Director context: directors never see the repository — only Zenith's documents, a name/size listing and the files named in the task's `context_files` globs (`zenith task add … --context 'src/**'` is not yet a flag; pass `scope` via the API or edit the task scope). Worktrees containing credential-like files are refused.

Optional tuning: `ZENITH_CLAUDE_BIN`, `ZENITH_CODEX_BIN`, `ZENITH_CLAUDE_ALLOWED_HOSTS`, `ZENITH_CODEX_ALLOWED_HOSTS`, `ZENITH_CLAUDE_CODE_MODEL`, `ZENITH_CODEX_MODEL`, `ZENITH_MAX_BUDGET_USD`. Full smoke procedure and pass criteria: `plans/SPRINT_2_5_READINESS.md` §6. Failure kinds in `run_events`/exit reasons: `auth`, `invalid_output`, `timeout`, `connection`, `interrupted`, `policy_violation`, `unsupported`, `budget`, `cancelled`, `crash`.

## Evidence semantics (what the labels mean)
| Origin | Confidence | Produced by |
|---|---|---|
| `HUMAN_REPORT` | `REPORTED` | `zenith checkin` / `evidence add` |
| `DOCUMENT` | `REPORTED` | `evidence add` (a claim about a document; Zenith does not read it) |
| `MODEL_INFERENCE` | `INFERRED` | `evidence add` |
| `REPOSITORY_OBSERVATION` | `OBSERVED` | `zenith scan` only |
| `TEST_VERIFICATION` | `VERIFIED` | deterministic verifier only (Sprint 2); no CLI path |

Integrity: `events` and `evidence` are append-only via SQLite triggers (`evidence.last_confirmed_at` is the only mutable column). Evidence carries a gap-free `seq` assigned inside the write transaction, so recording order is reconstructible even when timestamps tie. **This protects against application mistakes, not against a privileged local user with `sqlite3` who can drop the triggers; it is not cryptographic.**

A brief never reports `VERIFIED` unless a passing verification matches the currently observed, clean HEAD of a freshly scanned source. Sources older than 7 days are *stale*, check-ins older than 14 days are stale, and unscanned or unavailable sources are reported as *unknown*.

## Scan policy
- A source is only scanned if it was explicitly registered. `/`, the home directory itself and credential-store directories are refused. A directory can belong to one source only and sources never nest (fail closed; `doctor` lists any overlaps left over from before schema v3 — detach one of them). Set `ZENITH_ALLOWED_ROOTS=/a:/b` to restrict where sources may live; it is checked at registration **and** at every scan (`unavailable / outside_allowlist`).
- File contents are never read. Skipped and only counted: symlinks (never followed, escaping ones counted separately), `.env*`, `*.pem`, `*.key`, `id_*`, `*secret*`, `*credential*`, `.netrc`, `.ssh/`, `.aws/`, `.gnupg/` and similar. Because the metadata scanner never opens files and never records names, a *code* file whose name merely contains `secret`/`credential` (e.g. `secrets.py`, `credentials_test.go`) does count in file totals and fingerprints; `secrets.json`, `secrets.sh`, `.env*`, keys, stores and `credentials` do not. Any future feature that reads file contents must use the strict policy (`policy.sensitive_reason(..., strict=True)`, the default), which excludes `secrets.py` too.
- Git is called with `core.fsmonitor=false` and `--no-optional-locks`, so a repository's own config cannot execute commands and a scan does not touch `.git/index`. `git status` is limited to the source directory, so a sub-directory source does not count changes elsewhere in the repository. Worst case a scan spends 5 × 10 s in `git` plus 10 s walking the tree.
- If a registered directory is replaced by a symlink, the source is reported unavailable (`path_resolves_elsewhere`).
- Timestamps are microsecond UTC (ISO 8601). Values written before schema v3 have second resolution and still sort correctly against the new ones.

## Backup, upgrade and restore
```bash
zenith backup --out ~/zenith-backups/zenith-$(date +%F).db   # consistent even with WAL; refuses to overwrite
zenith init                                                   # after upgrading Zenith: backup + migrate
```
- Upgrades write `zenith.db.pre-v<N>-<timestamp>.bak` next to the database, apply each migration in one transaction, and roll back on any error (the backup path is in the error message). A process killed mid-upgrade leaves the previous version intact (SQLite journal), and `init` can simply be re-run. Zenith never deletes these backups; remove old ones yourself. If the backup itself fails, the upgrade does not start and the partial backup file is removed.
- `zenith doctor` reports `schema_version`, `schema_expected` and, when they differ, `schema_action`. The `schema.migrated` event is written right after the migration commits; a crash in that gap loses only the event.
- Opening a WAL database read-only (`doctor`, `backup`) may create `-wal`/`-shm` sidecar files; they are harmless.
- Zenith refuses to run on an older schema until `zenith init` is run, and refuses to open a database newer than itself.
- **Restore** = stop anything using the database, then point `ZENITH_DB_PATH` at the backup copy (or copy it over the live path after moving the live file aside), and run `zenith init`. Do not delete `-wal`/`-shm` files while processes are running.
- **Rollback after an upgrade** = stop Zenith; move `zenith.db` (and any `-wal`/`-shm`) aside; copy the `.pre-v<N>-*.bak` file to `zenith.db`; run the previous Zenith version (a newer schema is refused by older code, and vice versa).
- Rehearse first on a copy: `cp zenith.db /tmp/rehearsal.db && ZENITH_DB_PATH=/tmp/rehearsal.db zenith init && ZENITH_DB_PATH=/tmp/rehearsal.db zenith doctor`.

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
