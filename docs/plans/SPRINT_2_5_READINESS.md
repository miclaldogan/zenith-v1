# Sprint 2.5 — Live agent integration readiness

> Superseded in part by `SPRINT_2_6_VALIDATION.md` (auth modes, command-free Codex director, context directories, proxy hardening).

**Date:** 2026-10-23 · **Result:** Zenith is technically prepared for a *controlled, explicitly authorized* smoke test of one provider adapter. No live provider call was made in this sprint. The owner's database is untouched (still v1).

## 1. Baseline verification
- Working tree carried Sprint 2 uncommitted; preserved. `scripts/test.sh` at start: 110 OK.
- Architecture discrepancies found before changing anything: (a) the Sprint 2 plan §2 assumed the implementer CLI could run inside the sandbox *with* the provider's normal tool set — the installed Claude Code has `--restricted` which makes an edit-only implementer expressible, so the plan was tightened rather than weakened; (b) the plan assumed Codex could be an implementer — Codex has no mode without command execution, so `codex-implementer` is marked unsupported; (c) ADR-005 §2's per-project "no sandbox" opt-in was never implemented and remains absent (fail closed).

## 2. ADR review (recommendations; nothing marked Accepted)
| ADR | Principle checked | Finding | Recommendation |
|---|---|---|---|
| 005 | durable execution + explicit reconciliation | implemented: leases, `RECONCILE_REQUIRED`, owner `reconcile`, retry needs fresh approval | **Accept as amended** (read-only root; no opt-in) |
| 005 | scoped authorization + consumption | implemented: single-use, bounded, revocable, scope hash incl. prompt revisions, atomic with lease | Accept |
| 005 | deterministic verification | implemented: verifier runs commands in a credential-free sandbox; claims audited | Accept |
| 006 | provider-independent agents | ports + 6 adapters (2 fake, 4 live-gated) share one contract version | **Accept**; add amendment: live adapters need manifest `permissions` naming hosts and credentials (done) |
| 006 | modular extensibility, plugin boundaries | entry points + manifests + enablement; no isolation claim for plugins | Accept; package move to `zenith.engineering` still pending |
| 007 (new) | live isolation design | see ADR-007 | Accept before the first smoke test |

## 3. Threat model for live execution (delta over §7b in `05_SECURITY_POLICY.md`)
| Threat | Mitigation | Test |
|---|---|---|
| Repository code reads the provider credential | code never runs in the provider sandbox for Claude (`--restricted`, forbidden-tool kill switch); verification sandbox has no `/run/zenith` and no network | `test_verification_profile_has_no_credentials_or_network`, `test_unauthorized_tool_use_*` |
| Exfiltration from the provider process | network namespace unshared; only the allowlist proxy socket; every CONNECT audited; denied → 403 | `test_egress_only_through_allowlist_proxy`, `test_exfiltration_attempt_is_denied_and_audited` |
| Host secrets leak into the CLI | `--clearenv`; fixed env; tmpfs HOME; credentials bound ro | `SECRETS []` assertion |
| Zenith learns the key | existence + mode check only; the key never appears in run events | `test_end_to_end_with_mock_claude_implementer` (no `sk-test` in events) |
| Model output impersonates a contract or grants permissions | strict `validate`, JSON Schema `additionalProperties:false`, Zenith stamps scope/authorization | Sprint 2 tests, `test_json_schema_is_strict` |
| Auth expiry / provider outage / killed process | classified `auth`, `connection`, `interrupted`; implement-stage uncertainty → `RECONCILE_REQUIRED` | `test_lost_connection_interrupted_timeout_cancel`, `test_policy_violation_and_connection_loss_in_a_run` |
| Runaway cost or wall time | `--max-budget-usd`, Zenith accumulates `provider.usage`, `budget_exceeded → FAILED`; timeouts kill the process group | `test_budget_exceeded_fails_the_run` |
| Codex runs read-only commands next to `auth.json` | documented caveat; director role only; egress provider-only; implementer unsupported | `test_codex_implementer_is_unsupported` |

**Residual risks.** Claude's `HTTPS_PROXY` support under `--bare` and the exact event shapes are unverified until the smoke test; Codex director caveat above; no seccomp; no memory cap (`systemd-run` properties not wired); user namespaces are kernel attack surface; a compromised provider endpoint is trusted by construction (TLS pass-through, no content inspection); the mock executables verify Zenith's side of the contract, not the vendors'.

## 4. Chosen credential and network isolation design
ADR-007 option 2: bubblewrap provider profile + owner-named credential file bound read-only + allowlisting CONNECT proxy over a unix socket + in-sandbox loopback forwarder (`sandbox_forwarder.py`, `python -I`, no Zenith code). Verified on this machine: loopback is up inside `--unshare-net`; a bound unix socket is reachable; the host's loopback is **not** reachable; the credential is readable by the CLI and read-only; the root is read-only.

## 5. Adapter readiness assessment
| Adapter | Flags (verified against installed CLI) | Readiness | Blocker before live use |
|---|---|---|---|
| `claude-code-director` | `-p --bare --restricted --permission-prompts none --permission-mode plan --tools Read,Glob,Grep --disallowedTools … --output-format stream-json --json-schema … --no-session-persistence --session-id … --strict-mcp-config --settings {apiKeyHelper} --max-budget-usd` | code + mock tests green | API key file; owner ack; ADR-007 acceptance |
| `claude-code-implementer` | as above with `--permission-mode acceptEdits --tools Read,Glob,Grep,Edit,Write,MultiEdit` | green | same |
| `codex-director` | `exec --json -s read-only -C /work --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check --color never --output-schema … -o …` | green (mock) | auth file; accept the read-only-commands caveat |
| `codex-implementer` | — | **unsupported** (refuses) | would need a Codex mode without command execution, or option 1/3 of ADR-007 |

Correlation: `--session-id = uuid5(run_id, stage)`; every provider event, denial and usage lands in `run_events` with the run id. Cancellation: `should_cancel` → process-group kill. Timeouts: stage deadline from the run budget. Resume: declared unsupported (`capabilities.resume=False`) until lease semantics are tested live.

## 6. Live smoke-test procedure (not executed)
```bash
# 0. Preconditions: owner accepts ADR-005/006/007; Zenith DB migrated to v4 (separate approval).
zenith sandbox doctor                                     # ok: true
# 1. Credential file chosen by the owner (never auto-discovered); mode 0600; contents never read by Zenith.
install -m 600 /dev/null ~/.config/zenith/anthropic_api_key && $EDITOR ~/.config/zenith/anthropic_api_key
export ZENITH_ANTHROPIC_API_KEY_FILE=~/.config/zenith/anthropic_api_key
export ZENITH_ENABLED_ADAPTERS=claude-code-director
# 2. Disposable repository (NOT a registered project).
git clone --depth 1 <some tiny public repo> /tmp/smoke-repo   # or `zenith demo poc` output dir's repo
# 3. Preflight only: binary, auth presence, sandbox, exact argv, blockers. No provider call.
zenith adapters probe --adapter claude-code-director --version-check
zenith smoke --adapter claude-code-director --repo /tmp/smoke-repo
# 4. ONE live call, explicitly acknowledged (cost cap 0.25 USD by default):
ZENITH_LIVE_SMOKE_ACK=I-AUTHORIZE-ONE-LIVE-PROVIDER-CALL \
zenith smoke --adapter claude-code-director --repo /tmp/smoke-repo --authorize-live-call --max-budget-usd 0.25
# 5. Inspect: outcome.ok, cost_usd, network_denied (should be empty), event_count; the audit event `smoke.live_call`.
zenith events --limit 5
```
Pass criteria: `outcome.ok=true`, a validated `architecture_proposal` + `implementation_prompt`, `network_denied=[]`, cost ≤ cap, no `provider.stderr` auth/proxy errors. Any failure is a classified error; fix the blocker, do not loosen flags.

## 7. Blockers requiring owner decisions
1. Accept ADR-005 (as amended), ADR-006, ADR-007.
2. Provide the credential files and choose which adapters to enable (`ZENITH_ENABLED_ADAPTERS`).
3. Accept the Codex director caveat or wait for ADR-007 option 1 (mediated tools).
4. Migrate the live DB (v1 → v4) — separate explicit approval.
5. REQ-004: confirm display name "Misa", the assistant's attribution email, and whether a GitHub App "Misa" should be evaluated further (no account is created by Zenith).
6. Decide when to commit this work (nothing is committed automatically).
