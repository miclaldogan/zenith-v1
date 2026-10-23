# ADR-007 — Live agent isolation: trusted control plane, sandboxed provider clients, allowlisted egress
**Proposed 2026-10-23 (Sprint 2.5). Needs owner acceptance before the first live call.**

## Context
Sprint 2 proved the workflow with fake adapters. Running Claude Code and Codex for real adds two things the fake adapters never had: a provider credential and a network path. Both must stay unreachable from untrusted repository code, and the model's own tool use must not become an unmediated shell. The decision had to rest on how the two CLIs actually execute, verified on the installed versions (Claude Code 2.1.309, Codex 0.162.1).

## What the CLIs can and cannot do (verified via `--help`, 2026-10-23)
| | Claude Code | Codex |
|---|---|---|
| How tools run | in-process file tools (Read/Edit/Write…); Bash and other code-running tools spawn children of the CLI process | every model-generated shell command runs as a child inside Codex's own Linux sandbox (`-s read-only` / `workspace-write`) |
| Can code execution be switched off? | **Yes**: `--restricted` removes Bash/PowerShell/code-running tools and WebFetch, confines file tools to the working dirs, refuses bypass; `--tools` and `--disallowedTools` narrow further; `--permission-prompts none` denies anything that would prompt | **By feature flag** (0.162.1): `--disable shell_tool --disable unified_exec` removes the shell tools; no sandbox *mode* is command-free (`read-only` still executes read-only commands). Runtime effect of the flags is unverified until the live smoke test; Zenith kills the run on any `command_execution` item |
| Config/hook surface | `--bare`: no hooks, plugins, CLAUDE.md, keychain; auth strictly `ANTHROPIC_API_KEY` or `apiKeyHelper` via `--settings`; `--strict-mcp-config` | `--ignore-user-config --ignore-rules --ephemeral`; `CODEX_HOME` picks the auth file |
| Structured output | `--output-format stream-json` + `--json-schema` | `exec --json` + `--output-schema` + `-o` |
| Budget | `--max-budget-usd` | none (Zenith wall clock + usage events) |
| Sessions | `--session-id`, `--resume`, `--no-session-persistence` | `exec resume <id>` exists; not used (lease semantics unverified) |
| Interceptable by Zenith | argv, environment, filesystem view, network path, stream events | same |
| **Not** interceptable | the CLI's own HTTPS traffic content (TLS); what the model reads from files it is allowed to read | same, plus the commands Codex runs inside its sandbox |

## Options considered
1. **Trusted controller with mediated sandbox tools** — run the CLI with no code tools, expose a Zenith MCP tool ("run acceptance tests") that executes in the credential-free verification sandbox. Best long-term mediation; needs an MCP server and more surface. Deferred, compatible with option 2.
2. **Fully sandboxed provider client with scoped credential brokering** (chosen) — the CLI runs inside bubblewrap: read-only `/usr` and binary, `/work` bound rw (implementer) or ro (director), the credential file bound read-only exactly where the CLI expects it, a tmpfs `HOME`, cleared environment, **network namespace unshared** with the only egress being Zenith's allowlisting CONNECT proxy (unix socket bound in, loopback forwarder inside). Code-running tools are removed from the model (Claude) or confined to Codex's read-only sandbox (Codex director). Zenith runs acceptance commands itself in a separate sandbox that has **no credentials and no network**.
3. **SDK/API integration with an external execution environment** — cleanest separation (credentials never on this machine's untrusted path), but requires API-key billing and a remote executor; keep as the Hub-era option.

## Decision
- Adopt option 2 now; keep 1 and 3 on the roadmap.
- **Trust zones**: (Z1) Zenith core + verifier + orchestrator: trusted. (Z2) Provider CLI process: semi-trusted; holds its own credential; may read `/work`; may write only `/work` (implementer); talks only to allowlisted hosts. (Z3) Untrusted repository code: executes only in the verification sandbox (no credential, no network) or, for Codex, inside Codex's read-only sandbox *within* Z2 (caveat below). (Z4) Credentials: owner-chosen files, bound read-only, never read by Zenith, never present in Z3.
- **Credential brokering** = the owner names the file (`ZENITH_ANTHROPIC_API_KEY_FILE`, `ZENITH_CODEX_AUTH_FILE`); Zenith checks existence and 0600 mode only; the in-sandbox forwarder reads the file into the CLI's environment variable (`ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`); Codex reads `auth.json` via `CODEX_HOME`. No auto-discovery of `~/.claude` or `~/.codex`. (Superseded the earlier `apiKeyHelper` design, which needed a shell command.)
- **Egress**: `AllowlistProxy` (CONNECT only, host:port allowlist, every decision audited to `run_events`); defaults `api.anthropic.com:443` and the OpenAI/ChatGPT API hosts; TLS untouched.
- **Support matrix**: `claude-code-director` supported; `claude-code-implementer` supported (edit-only, Zenith verifies); `codex-director` supported command-free (shell tools disabled by flag, `command_execution` kill switch, context directory instead of the repository; residual caveat only if the flags prove ineffective live); `codex-implementer` **unsupported** for autonomous execution (would run untrusted code next to the credential) and refuses with `kind=unsupported`.
- **Policy enforcement in the stream**: a `tool_use` of a forbidden tool (Bash, WebFetch, …) kills the process group and ends the run `BLOCKED/policy_violation`.
- **Live calls are gated three times**: adapter named in `ZENITH_ENABLED_ADAPTERS`, `--authorize-live-call`, and `ZENITH_LIVE_SMOKE_ACK` for the smoke test; the test suite never calls a provider.

## Consequences
The implementer cannot run tests itself; the verify → review → revise loop compensates and keeps verification independent. Claude Code's honouring of `HTTPS_PROXY` inside `--bare` and the exact stream-json/`--json-schema` shapes are **assumptions to be confirmed by the first smoke test**; the parsers are tolerant and every deviation is a classified `invalid_output`, never a silent pass. Residual risks are listed in `05_SECURITY_POLICY.md` §7c.

## Amendments 2026-10-23 (Sprint 2.6)
1. **Two Claude auth modes.** `api_key` (`--bare`, Console billing) and `oauth` (`--safe-mode`, subscription token from `claude setup-token`; `--bare` cannot use OAuth). The credential file is read **inside the sandbox** by the forwarder into `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN`; `apiKeyHelper` (a shell command) is no longer used. Zenith never reads the file.
2. **Codex director is command-free by flag.** `--disable shell_tool --disable unified_exec` (stable feature flags) plus a kill switch on any `command_execution` item. Runtime effect of the flags is unverified until the live smoke test.
3. **Directors never see the repository.** They read a Zenith-built context directory (documents, name/size listing, owner-selected `context_files`); the implementer's worktree is pre-scanned and refused if it contains credential-like files.
4. **Proxy hardening.** Private/loopback/link-local destinations are blocked even when allowlisted; connections go to the vetted resolved address (anti-rebinding); IPv6 supported; audit logs never contain request headers.
