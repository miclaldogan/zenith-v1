# 05 — Security, Approval & Threat Model

## 1. Security goals
Zenith can eventually read private source code, family project notes, email/calendar metadata and execute coding agents. Treat it as a high-privilege local application. Minimize privileges and avoid creating a general-purpose remote shell.

## 2. Authority levels
| Level | Allowed without new approval? | Examples |
|---|---|---|
| L0 | Yes, only approved sources | Read registered Git metadata; show due date |
| L1 | Usually after cloud/privacy consent | Summarize approved documents with model; generate plan |
| L2 | Scoped project/worktree approval | Edit isolated worktree, run allowlisted tests |
| L3 | Separate explicit actionable approval | Commit, alter primary working tree, write calendar event |
| L4 | Per-action confirmation + policy | Push/merge/deploy, send email, destructive changes, money movement |

UI suggestion is not permission. Telegram buttons must reference one task ID, scope hash, expiry and authenticated user identity. An approval is one-time or limited; never permanent, vague, silent or reusable across changed plans.

## 3. Threat scenarios & mitigations
| Threat | Mitigation |
|---|---|
| Prompt injection in README, email, issue, test logs | Treat as quoted data, never tool-control instruction; policy out of LLM |
| Malicious generated shell command | Executor command allowlist, args arrays, no shell expansion, sandbox restrictions |
| Secret exfiltration via assistant | Path blocklist, token redaction, no secret content in prompts/logs, egress control |
| Agent modifies unrelated repo | Unique worktree, explicit approved root, pre/post diff scope checks |
| Endless Claude–Codex loop | max turns, retries, budget and wall-time, circuit breaker |
| Replay of remote approval | short expiry, nonce, scope hash, identity check, transactionally mark consumed |
| Compromised remote node | Node-scoped tokens, revocation, capability and project permissions |
| Duplicate task on crash | idempotency key, durable leases and reconciliation; no blind retry |
| Misleading progress report | event/evidence ledger, provenance labels, independent verification |
| Tampering with the local ledger | `events`/`evidence` append-only triggers stop ordinary code paths; **not** cryptographic — a privileged local user can drop them. Tamper-evidence (hash chaining, remote copies) is a Hub-era item |
| Ambiguous path ownership | one active source per directory, no nesting (code + trigger), allowlist re-checked at scan time |
| Overly broad Google scopes | OAuth least privilege, read-only first, write scopes require explicit escalation |
| Log retention leaks | Redact, encrypt remote backups, cap log size, configurable TTL |

## 4. Execution guardrails
- Default no network actions, no agents executed in the starter.
- Separate permission to **read**, **analyze externally**, **write worktree**, **run tests**, **push**, **deploy** and **send messages**.
- Never include secrets in tool environment beyond necessary provider-specific credential channel.
- Set subprocess timeout, max output bytes, resource bounds and runtime/credential checks.
- Bash is not a trustworthy sandbox; use OS-level restrictions for untrusted code execution (containers/isolated OS users/VM if needed).
- Keep `tmux` detached sessions non-privileged. Windows/workspaces are display only.
- External repository code and test commands can be malicious: review before execution or use isolated environment.

## 5. OAuth / messaging policy
- Tokens from a secure secrets backend with filesystem permissions and rotation; never plaintext `.env` in repo.
- Telegram bot webhook/polling: restrict to explicit allowlisted Telegram user/chat IDs, bind approvals to logged-in owner, throttle notifications.
- Gmail: read-only scopes before mutations; never automatically send; message content is untrusted and may contain malicious instructions.
- Calendar: drafts/suggestions first; write only after event details reviewed.
- GitHub: prefer read-only token until PR creation justified. No automatic push/merge.

## 6. Privacy & data flow classification
- Public project data; personal projects; family sensitive data; credentials; academic unpublished research; external communications.
- Every source gets data classification and cloud-processing consent flag.
- Model-router must refuse off-device processing of restricted material unless explicitly enabled.
- Audit each cloud payload by digest/reference, provider and purpose without storing plaintext secrets.

## 7. Required negative tests before autonomous execution
1. Embedded `ignore previous instructions` in README cannot elevate permissions.
2. An expired approval never permits execution.
3. Plan drift changes scope hash and invalidates approval.
4. Process timeout and failure never mark a task completed.
5. Worktree cannot write outside registered roots.
6. Two writers never lease same worktree.
7. False agent `tests passed` result cannot override actual failing test.
8. Replayed Telegram approval is refused.
9. Missing/invalid credentials do not trigger insecure fallback.
10. Stale device status is never shown as live.

## 7b. Execution threat model (Sprint 2, implemented baseline)
**Trust zones.** (1) Zenith orchestration code and the verifier: trusted. (2) Provider adapters: semi-trusted — their output is validated, never obeyed. (3) Repository code, tests and build scripts: **untrusted**, may be malicious. (4) Worktrees: disposable, the only writable location for an implementer. (5) Secrets, credentials, `$HOME`, SSH agent, unrelated repositories: never reachable from zone 3.

**Baseline (bubblewrap, fail closed).** `sandbox.detect()` probes the real kernel/runtime; if the probe does not prove user namespaces, hidden `/home`, a closed network and a read-only root, `run_isolated` **refuses** — there is no host fallback and no opt-out flag. Inside: all namespaces unshared, `--ro-bind /usr`, `--tmpfs /tmp`, `--tmpfs /home/sandbox`, `--bind <worktree> /work`, `--clearenv` with a fixed `PATH/HOME/LANG`, `--die-with-parent`, `--new-session`, `--remount-ro /`, `rlimit` CPU and file size, output cap (a truncated run is never a pass), process-group kill on timeout, argv only (no shell). `systemd-run --user` is available on this machine and is the planned lifecycle/accounting layer (`MemoryMax`, `CPUQuota`); it is **not** a substitute for the namespace sandbox.

| Threat | Mitigation | Test |
|---|---|---|
| Test/build script reads `~/.ssh`, tokens, other repos | no `/home`, cleared env, ro root | `test_home_credentials_and_network_are_unreachable` |
| Exfiltration over the network | `--unshare-net` | same |
| Writes outside the worktree / RAM exhaustion via tmpfs root | `--remount-ro /` (found by the test suite during this sprint and fixed) | `test_only_the_worktree_is_writable` |
| Fork bomb / runaway process | process group kill on timeout, CPU rlimit, `--die-with-parent` | `test_timeout_kills_process_group_and_output_is_capped` |
| Shell injection through acceptance commands | argv arrays, validated at task creation, no shell anywhere | `test_no_shell_interpolation` |
| Repository hooks run during checkout | `core.hooksPath=/dev/null`, `core.fsmonitor=false` | `test_repo_hooks_do_not_run` |
| Implementer edits outside scope / commits / pushes | diff-scope check, HEAD must equal base, no credentials, `can_push=false` stamped by Zenith | `test_out_of_scope_change_is_blocked_with_revised_prompt` |
| False "tests passed" claims | verifier runs commands itself; claims mismatch recorded | `test_false_claims_never_complete` |
| Model grants itself permissions | strict contracts reject unknown fields; approvals only from owner CLI; scope stamped by Zenith | `test_model_output_cannot_grant_permissions` |
| Approval replay / reuse / expiry / revocation / plan drift | atomic consumption with bounded `use_count`, immutable expiry, revocation, scope hash incl. task version | `ApprovalTests` |
| Worker crash re-executes side effects | leases → `RECONCILE_REQUIRED`; owner decides; retry needs a fresh approval | `test_worker_crash_leaves_stale_lease_then_reconciled` |
| Two workers on one repository | one live worktree per source (DB unique index) checked before anything is consumed | `test_crash_during_implementation_requires_reconciliation` |

**Residual risks (documented, not mitigated yet).** User namespaces are kernel attack surface; no seccomp filter; no memory limit until `systemd-run` properties are wired; the fake implementer runs in-process (trusted by construction) — a live implementer CLI needs its own network policy decision (provider API reachable, everything else closed), to be made in S2-09 with an ADR; the DB append-only triggers are not cryptographic (ADR-004).

## 7c. Live provider execution (Sprint 2.5, prepared — no live call made)
Design and rationale: ADR-007. Summary of the enforced boundary:
- **Provider profile** (`sandbox.SandboxProfile("provider")`): network namespace unshared; egress only through `netproxy.AllowlistProxy` (CONNECT, `host:port` allowlist, audited); credential file bound read-only at the CLI's expected path; tmpfs `HOME`; cleared env; CLI binary bound read-only under `/opt/zenith/bin`; `/work` rw for the implementer, ro for the director.
- **Verification profile**: no credentials, no proxy, no `/run/zenith` — untrusted code never meets a secret.
- **Claude Code**: `--bare --restricted --permission-prompts none`, explicit `--tools`, forbidden tools denied *and* watched in the stream (a forbidden `tool_use` kills the process group → `BLOCKED/policy_violation`). **Codex**: `exec -s read-only --ephemeral --ignore-user-config --ignore-rules`; director only.
- **Credentials**: owner-named files only (`ZENITH_ANTHROPIC_API_KEY_FILE`, `ZENITH_CODEX_AUTH_FILE`), 0600 enforced, never read by Zenith, never auto-discovered from `~/.claude` or `~/.codex`.
- **Gates**: `ZENITH_ENABLED_ADAPTERS` per live adapter; `zenith smoke --authorize-live-call` + `ZENITH_LIVE_SMOKE_ACK`; the test suite uses mock executables only.
- **Unsupported**: `codex-implementer` (refuses). Threat table and residual risks: `plans/SPRINT_2_5_READINESS.md` §3.
- **Sprint 2.6 additions**: credential files are read inside the sandbox by the forwarder (`--env-file`), never by Zenith; Claude `oauth` mode (`--safe-mode`) for subscription logins and `api_key` mode (`--bare`); Codex `--disable shell_tool --disable unified_exec` + `command_execution` kill switch; directors read a minimal context directory instead of the repository (`scope.context_files` is the owner's opt-in for file contents); worktrees containing credential-like names are refused before any implementer runs; the proxy blocks private/loopback destinations, connects to vetted resolved addresses and never logs headers. Full audit: `plans/SPRINT_2_6_VALIDATION.md` §3–§5.

## 8. Supplier/licensing constraints
The Claude Code CLI has noninteractive interfaces; **do not** use personal Claude subscription login as a credential proxy for a third-party generic API, and don't make unverified assumptions about unattended automation under a subscription. For product-like programmatic Agent SDK calls use authorized API-key/provider integrations consistent with terms. Codex integrations must follow their current authentication and CLI sandbox docs. Confirm at actual integration time.

References: https://code.claude.com/docs/en/legal-and-compliance ; https://code.claude.com/docs/en/agent-sdk/overview ; https://developers.openai.com/codex/developer-commands
