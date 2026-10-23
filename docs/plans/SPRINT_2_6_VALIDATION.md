# Sprint 2.6 — Live integration validation & product readiness

**Date:** 2026-10-23 · **Outcome:** the real-provider path is defined end to end, safety issues found were fixed or recorded as blockers, and a minimal live test is ready for the owner's explicit approval. **No authenticated provider call was made.** Owner database untouched (v1). Nothing committed.

Capability status legend used below: **mock-tested** (mock executables inside the real sandbox) · **locally verified** (real binaries/kernel, no provider call) · **authenticated-live-tested** (none yet) · **unverified**.

## 1. Baseline
Uncommitted Sprint 2/2.5 work preserved; 132 → **142 tests, 0 failures, 0 skips** (`scripts/test.sh`: kernel 6 · sprint1 44 · hardening 25 · sprint2 35 · sprint25 22 · sprint26 10). ADR-005/006/007 reviewed; all remain *Proposed* (recommendations in §8).

## 2. Authentication & capability matrix (installed: Claude Code 2.1.309, Codex 0.162.1)

| | Claude Code — API key | Claude Code — subscription (OAuth) | Codex — ChatGPT subscription | Codex — API key |
|---|---|---|---|---|
| Owner's current state (locally verified via `claude auth status`) | not configured | **logged in, `authMethod: claude.ai`, `subscriptionType: team`** | unknown (not probed; `codex login status` reads the owner's auth file) | unknown |
| Credential material | file with the key | file with the long-lived token from `claude setup-token` (requires subscription) | `~/.codex/auth.json` (OAuth tokens) | `auth.json` written by `codex login --with-api-key` |
| Zenith adapter mode | `api_key` → `--bare` | `oauth` → `--safe-mode` (`--bare` refuses OAuth: "strictly ANTHROPIC_API_KEY or apiKeyHelper") | `ZENITH_CODEX_AUTH_FILE` → `CODEX_HOME` bind | same |
| How the CLI receives it | forwarder reads the file **inside the sandbox** into `ANTHROPIC_API_KEY` | same, into `CLAUDE_CODE_OAUTH_TOKEN` | file bound ro at `/home/sandbox/.codex/auth.json` | same |
| Isolation flags | `--restricted --permission-prompts none --strict-mcp-config --disable-slash-commands --no-session-persistence` | same + `--safe-mode` (all customizations off) | `exec --json -s read-only --ephemeral --ignore-user-config --ignore-rules --disable shell_tool --disable unified_exec …` | same |
| Billing | Anthropic Console (separate arrangement) | included in the subscription; provider terms apply to the subscriber's own use | ChatGPT plan | OpenAI API |
| Risk specific to mode | key file custody | token rotation/expiry: refresh cannot be written back (read-only bind) → re-run `setup-token` when it expires | **OAuth refresh**: Codex may try to rewrite `auth.json`; the ro bind makes that fail — may force re-login; API key avoids it | none beyond custody |
| Status | mock-tested; argv locally verified | mock-tested; argv locally verified; **recommended first smoke** given the owner's login | mock-tested | mock-tested |
| Authenticated operation | **unverified** | **unverified** | **unverified** | **unverified** |

Rules kept: no key or token is ever pasted into chat, a project file or the database; Zenith checks only existence and `0600`; a `--version` success proves nothing about authentication (`preflight.authenticated_live = "unverified"`).

## 3. Engineering Director isolation (strengthened)
- Directors no longer see the repository. Zenith builds a **read-only context directory**: `context.json` (+ `prompt.json`, `implementation_report.json`, `verification_report.json` for review), `listing.txt` (names and sizes; credential-like names omitted), and `files/` holding **only** the owner-selected `scope.context_files` (strict sensitive filter, 64 KiB/file, 1 MiB total, manifest recorded). Event `task.director_context` records `repository_mounted: false`. (mock-tested)
- Codex: `--disable shell_tool --disable unified_exec` (stable feature flags, `codex features list`) plus a kill switch on any `command_execution` item. Whether the flags remove the tools at runtime is **unverified until live**; the kill switch is mock-tested. Consequence of mounting `auth.json` is now bounded: even if a command ran, it would see only the context directory, and egress is provider-only.
- A read-only filesystem is explicitly *not* treated as a command-free runtime (hence the flags and the kill switch).
- Worktree pre-scan: an implementer never starts if the worktree contains credential-like names (`.env`, keys, `credentials`…) → `BLOCKED/policy_violation`. (mock-tested)

## 4. Network security audit (locally verified)
| Check | Result |
|---|---|
| Hostname + port allowlist | enforced; `host:port` syntax validated at construction |
| Non-allowlisted destination | 403 + `network.denied` |
| Private/loopback/link-local/ULA/metadata destinations | **blocked by default** even if allowlisted (`network.private_blocked`), incl. names resolving to private addresses; connect goes to the *resolved, vetted address* (anti-rebinding). `allow_private=True` exists for tests only |
| IPv4 / IPv6 | both resolved and connected; `[::1]:port` literal parsing tested |
| Upstream connection failure | 502 + `network.upstream_failed` (no hang; `connect_timeout`) |
| Non-CONNECT request | 405; audit records the **method token only** — headers (e.g. `Proxy-Authorization`) are never logged |
| Proxy restart | socket path reused after stop; stale socket unlinked; stopped socket refuses |
| Sandbox restart | each run gets a fresh proxy socket dir; the forwarder exits non-zero if the socket is gone |
| Credential visibility | TLS pass-through: the proxy never sees provider auth headers; `connections[]`/events hold host, port, address only |
| Finding fixed this sprint | audit log previously stored the first 60 bytes of a non-CONNECT request (could include a header) |

**Data minimization / provider-boundary controls.** Allowlisting cannot stop sensitive *content* reaching an allowed provider, so: directors get only the context dir (above); implementers get a disposable worktree that must be free of credential-like files; the context package carries evidence *summaries* with ids, not raw repository text; `scope.context_files` is the owner's explicit opt-in for file contents. Residual: the implementer can read every non-sensitive file in the worktree (needed to edit); the owner controls what the repository contains.

## 5. Claude Code execution semantics (offline evidence)
- In the provider profile `HOME` is an empty tmpfs: there is **no** `~/.claude`, no settings, hooks, MCP config or CLAUDE.md to load — verified by a mock that reports `home=0` (mock-tested). `--safe-mode`/`--bare`, `--restricted`, `--strict-mcp-config`, `--disable-slash-commands` are defense in depth on top.
- The director supplies all project context explicitly through the prompt and the context dir.
- Implementation runs only in the leased worktree (`/work` rw); acceptance commands run in the separate credential-free verification sandbox; the verifier compares reality with claims. (mock-tested end to end: `test_end_to_end_with_mock_claude_implementer`)
- **Unverified until live:** exact `stream-json` event shapes, `--json-schema` structured output placement, `HTTPS_PROXY` honouring, `--restricted` + `--safe-mode` interplay, OAuth token acceptance from `CLAUDE_CODE_OAUTH_TOKEN`.

## 6. First real smoke test — ready, awaiting authorization
`zenith smoke --adapter <director> --implementer claude-code-implementer --full` generates a **synthetic calculator repository** in a temp dir (no user files), then: director plan → persisted prompt artifact → owner approval recorded → one edit-only implementation → verification in the credential-free sandbox → director review → report. `max_attempts=1`, no retries, stage deadline 180 s, budget cap `--max-budget-usd` (default 0.25, split across director and implementer), no Git writes, allowlisted egress only. Preflight prints the **authorization request** (providers, auth mode, budget, files accessible, exact operation, expected outcome). The call needs `--authorize-live-call` **and** `ZENITH_LIVE_SMOKE_ACK=I-AUTHORIZE-ONE-LIVE-PROVIDER-CALL`, and refuses while any blocker remains.

### Authorization request for the owner (recommended first configuration)
- **Providers:** director `claude-code-director` (Claude Code, OAuth subscription token) — or `codex-director` if the owner prefers Codex and provides `auth.json`; implementer `claude-code-implementer` (same Claude token).
- **Authentication:** `claude setup-token` → owner saves the token to a `0600` file → `ZENITH_CLAUDE_OAUTH_TOKEN_FILE=<path>`; Zenith never reads it; it enters the sandbox as `CLAUDE_CODE_OAUTH_TOKEN`.
- **Estimated budget:** ≤ 0.25 USD-equivalent of usage (two short structured turns + one small edit); wall clock ≤ 10 min.
- **Files accessible to the agents:** director — `context.json`, `listing.txt`, `files/src/calc.py`, `files/tests/test_calc.py` (synthetic); implementer — the synthetic worktree only. No user repository, no `$HOME`, no credentials other than its own token.
- **Exact operation:** `ZENITH_ENABLED_ADAPTERS=claude-code-director,claude-code-implementer ZENITH_LIVE_SMOKE_ACK=… zenith smoke --adapter claude-code-director --implementer claude-code-implementer --full --authorize-live-call --max-budget-usd 0.25`
- **Expected outcome:** task `COMPLETED` with a passing `TEST_VERIFICATION`, six artifacts, `network_denied=[]`; otherwise a classified failure and no changes outside the temp dir. One audit event `smoke.live_full_run` in the owner's database.

## 7. Proposed checkpoint commit (for owner approval; not executed)
Subject: `Sprint 2–2.6: durable orchestration, director protocol, sandboxed provider adapters (no live calls)`
- Schema v4; `RECONCILE_REQUIRED`; approvals v2 (`kernel.py`, `migrations.py`)
- Director protocol + artifacts (`contracts.py`), orchestration/leases/runs (`orchestration.py`), verifier (`verify.py`), worktrees (`worktree.py`)
- Sandbox profiles, forwarder with in-sandbox credential injection, allowlist proxy (`sandbox.py`, `sandbox_forwarder.py`, `netproxy.py`)
- Adapters: fakes + gated Claude Code / Codex (`agents/`), registry (`extensions.py`), identity (`identity.py`), demo + smoke (`demo.py`, `smoke.py`), CLI
- Tests: `test_sprint2.py`, `test_sprint25.py`, `test_sprint26.py` (+ fixture updates) — 142 total
- Docs: ADR-006/007, requirements registry, Sprint 2/2.5/2.6/3 plans, architecture/security/runbook/data-model/README updates
No attribution trailers unless the owner wants them (owner rule).

## 8. ADR recommendations
ADR-005: accept as amended (read-only root; no sandbox opt-in). ADR-006: accept. ADR-007: accept **with the Sprint 2.6 amendments** (two Claude auth modes with in-sandbox injection; Codex command-free via feature flags + kill switch; directors read a context dir, not the repository; private-destination blocking). None marked Accepted.

## 9. Migration readiness
Live DB still v1 (empty). `zenith init` would back up and migrate v1→v4; rehearsed on a copy in Sprint 1.5 (v1→v3) and covered by `MigrationV4Tests`. Requires separate approval.

## 10. Blockers requiring owner approval
1. Choose auth: Claude subscription token (`claude setup-token`) and/or Codex `auth.json`/API key; create the `0600` file(s); set the env vars. **Never paste a token into chat.**
2. Approve the authorization request in §6 (and the ≤ 0.25 USD budget).
3. Accept ADR-005/006/007.
4. Approve the live DB migration separately.
5. Approve the checkpoint commit in §7.
