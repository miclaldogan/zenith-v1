# Zenith

**I was too lazy to manage my AI agents, so I built an AI to manage them.**

Zenith is an open-source, **local-first personal intelligence and agent-orchestration platform**. It remembers
your projects with evidence and provenance, tells you where you left off, coordinates coding agents inside hard
boundaries, verifies their claims independently, and never acts without your approval. **Misa** is the assistant
you talk to on top of it, in Turkish or English, offline by default, with an optional **local open-weight model
(Google Gemma via Ollama)** for natural reasoning and your own **Claude Code** login for engineering work.

Everything is a single Python package with **zero dependencies** (SQLite, stdlib only), tested by 243 unit and
integration tests, running on Linux.

```
                 you  ──────────────  "Harbor'da nerede kalmıştık?"
                  │
          ┌───────▼────────┐   conversation port (provider-independent)
          │     MISA       │──► offline composer (deterministic, always available)
          │  intents ·     │──► Gemma via Ollama  (local, open-weight, nothing leaves the machine)
          │  context ·     │──► your Claude Code login (native CLI, read-only MCP tools)
          │  grounding     │──► Anthropic API adapter (sandboxed; present, disabled)
          └───────┬────────┘
                  │ bounded, evidence-tagged context          grounding validator ⇐ every model reply
          ┌───────▼─────────────────────────────────────────────────────────┐
          │                        ZENITH CORE  (SQLite, append-only)      │
          │ projects · sources · evidence ledger (VERIFIED/OBSERVED/        │
          │ REPORTED/INFERRED) · tasks & dependencies · decisions · briefs  │
          │ execution runs · leases · approvals (scope-hashed, single-use)  │
          └───────┬─────────────────────────────────────────────────────────┘
                  │ orchestration (Engineering Director protocol)
   Director (plan/review) → Implementer (worktree) → Verifier (bubblewrap, no network) → owner approval
```

## Why

Every AI session starts from zero. You re-explain the project, re-discover last month's decision, and re-check
whether "tests pass" was true then or is true now. Agents that can run commands need supervision; agents that
cannot are useless. Zenith's thesis: **the memory, the evidence, the permissions and the verification belong to
a platform that is larger than any one model.** Models plug in; the platform remembers and checks.

## What Zenith does

- **Project registry with local sources.** Register directories (or discover them safely under a folder you
  name); Zenith scans Git metadata only, never file contents, and never follows symlinks into credentials.
- **Evidence-aware memory.** Every fact has an origin (`HUMAN_REPORT`, `REPOSITORY_OBSERVATION`,
  `TEST_VERIFICATION`, `DOCUMENT`, `MODEL_INFERENCE`) and a confidence (`REPORTED`, `OBSERVED`, `VERIFIED`,
  `INFERRED`). Evidence and events are append-only (SQLite triggers).
- **Source freshness and historical verification.** A passing test run is tied to a revision; when the head moves
  or uncommitted changes appear, the verification becomes *stale*, and the brief says so.
- **Versioned decisions and tasks** with dependencies, deadlines (Europe/Istanbul), blocked and overdue states.
- **Multi-turn Misa conversation** that resolves referents ("that project", "when did we decide that?"), builds a
  bounded context (≤ 40 items, 300 chars each), and validates every model reply: cited ids must exist, quoted
  strings, dates and revisions must appear in the records, stale verifications get a caveat, "done" claims get a
  contradiction caveat. Unsupported replies are replaced by the deterministic answer, visibly.
- **Provider-independent model interface.** One conversation port, four implementations (offline, Gemma/Ollama,
  native Claude Code, Anthropic API). One director/implementer protocol with fake, Claude Code and Codex adapters.
- **Permission-controlled orchestration.** Scope-hashed, single-use approvals; risk levels; a task that an agent
  touches must have been approved for exactly that scope.
- **Engineering Director and Implementer abstractions**, immutable prompt artifacts, bounded execution and
  retries, crash recovery (`RECONCILE_REQUIRED`, leases) and a persistent execution history.
- **Sandboxed verification** with bubblewrap: no network, no credentials, read-only source, writable worktree only.

## Install (Linux, Python ≥ 3.11)

```
git clone https://github.com/miclaldogan/zenith-v1 && cd zenith-v1
scripts/install.sh            # venv in ~/.local/share/zenith/venv, `zenith` linked into ~/.local/bin, fish completions
fish_add_path ~/.local/bin    # fish, once (bash/zsh: add ~/.local/bin to PATH)
zenith doctor --full          # tools, database schema, sandbox baseline, providers (nothing is changed)
zenith init                   # creates a NEW database; it never migrates an existing one
zenith                        # Misa (offline, deterministic)
```

Optional pieces, each independent:

| Piece | What it is | License | Needed for |
|---|---|---|---|
| **Zenith** | this repository | MIT | everything |
| **Ollama** | local inference runtime ([ollama.com](https://ollama.com)) | MIT | `zenith misa --local` |
| **Gemma** | Google's open-weight model, pulled with `ollama pull gemma3:4b` | [Gemma Terms of Use](https://ai.google.dev/gemma/terms) | `zenith misa --local` |
| **bubblewrap** (`bwrap`) | user-namespace sandbox | LGPL | sandboxed verification and provider adapters |
| **Claude Code** | Anthropic's proprietary CLI with your own login | proprietary | `zenith misa --claude`, engineering adapters |

## Try it in five minutes (fictional data, nothing of yours is touched)

```
zenith demo story                    # Scene A memory (offline) · Scene B local Gemma · Scene C orchestration simulation
zenith demo story --md transcript.md # same, saved as Markdown
zenith misa --eval                   # acceptance scenarios A/B/C, offline
zenith misa --eval --provider ollama-conversation   # the same scenarios reasoned by Gemma, checked by Zenith
```

Scene C runs the real orchestrator, worktrees, approvals and the bubblewrap verifier, but with **fake Director and
Implementer adapters**. It is a deterministic simulation of the workflow, labelled as such in its output.

## Everyday flow

```
zenith project discover --root ~/projects        # dry run, bounded, names only
zenith project discover --root ~/projects --add 1,3
zenith scan --project <id> --all                 # Git metadata, no file contents
zenith checkin <id> "deploy slipped to Monday"   # your own report: REPORTED, never VERIFIED
zenith brief --format text                       # evidence-cited status with freshness
zenith                                           # Misa, offline
zenith misa --local                              # Misa reasoning with Gemma on your GPU/CPU
zenith misa --claude                             # Misa inside your own Claude Code session (read-only Zenith MCP tools)
```

`zenith project inspect --id <id>` shows sources, freshness and verification without touching anything;
`zenith project docs --id <id> --allow README.md` lets Misa read a document locally; `--cloud-consent yes` is a
*separate* permission for sending that project's context to a remote model. The local Gemma path needs no consent
because nothing leaves the machine.

## Local reasoning with Gemma

`zenith misa --local` sends Misa's bounded, evidence-tagged context to Gemma through Ollama's `/api/chat` on
`127.0.0.1:11434`, with a JSON-schema constrained output, `num_ctx 8192`, `num_predict 700`, one call per turn and a
hard timeout. The adapter refuses non-loopback endpoints, needs no API key and no cloud consent. The reply goes
through the same validator as every other provider; if Ollama is down or the model is missing, Misa says so and
answers from the records. Measured results on the development machine (RTX 4050 laptop, 6 GB VRAM) are in
[`docs/EVALUATION.md`](docs/EVALUATION.md): the small model is fast and usually grounded, sometimes drifts from the
question, and the validator catches invented citations. Nothing in that document is estimated.

## Honest capability matrix

| Component | Status |
|---|---|
| Kernel: projects, sources, evidence ledger, tasks, decisions, briefs, migrations with backup/confirmation | implemented, offline-tested |
| Misa: intents (TR/EN), context assembler, grounding validator, multi-turn state, confirmations | implemented, offline-tested |
| Orchestration: runs, leases, idempotency, approvals, worktrees, reconcile, artifacts | implemented, **mock-tested** (fake Director/Implementer) |
| Sandboxed verification (bubblewrap) | implemented, tested with the real sandbox on the dev machine |
| Gemma via Ollama conversation adapter | implemented, **live-tested** on the dev machine (see EVALUATION.md) |
| Native Claude Code session (`misa --claude`, Zenith MCP server) | implemented, tested with a fake `claude` binary; real session awaits the owner |
| Claude Code / Codex Director and Implementer adapters | implemented, tested with mock binaries only; **no live run yet** |
| Anthropic API conversation adapter | implemented and disabled; **never called** |
| Telegram, Gmail, cloud hub, multi-node sync, GUI | **not built** (future) |

## Data and privacy

- The database is `${XDG_DATA_HOME:-~/.local/share}/zenith/zenith.db` (0600). Override with `ZENITH_DB_PATH`.
- Zenith reads Git metadata and the documents you explicitly allow; it never scans your home directory, follows
  symlinks or opens credential stores.
- Local Gemma: data goes to loopback only. Claude Code: only projects you marked `--cloud-consent yes`, through a
  read-only MCP server, on your own login; Zenith never touches your credentials. Anthropic API: disabled.
- Upgrading an older database never happens implicitly: `zenith db status`, `zenith db rehearse --copy-to …`, then
  `zenith db migrate --db PATH --confirm-migrate PATH` (backup first). See `docs/08_OPERATIONS.md`.

## Repository map

`src/zenith/kernel.py` store and services · `migrations.py` · `brief.py` · `scanner.py` · `policy.py` ·
`orchestration.py` · `sandbox.py`, `netproxy.py`, `sandbox_forwarder.py` · `agents/` (ports, fake, Claude Code,
Codex) · `extensions.py` registry · `misa/` (intents, context, engine, conversation, ollama, native, mcp_server,
profile, acceptance) · `story.py`, `demo.py` demonstrations · `docs/` architecture, operations, plans and this
evaluation · `tests/` 243 tests, `scripts/test.sh`.

## Status and limitations

Zenith is a working single-user system, not a product. Known limits: lexical (not semantic) recall of past turns;
the grounding validator checks citations, quotes, dates and revisions, not paraphrase fidelity; small local models
drift; the autonomous agent loop has only run with fake adapters; one user, one machine.

## License

MIT for Zenith (see `LICENSE`). Gemma, Ollama, bubblewrap and Claude Code are separate works under their own
licenses and are not bundled.
