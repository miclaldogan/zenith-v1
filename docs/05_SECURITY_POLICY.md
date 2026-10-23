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

## 8. Supplier/licensing constraints
The Claude Code CLI has noninteractive interfaces; **do not** use personal Claude subscription login as a credential proxy for a third-party generic API, and don't make unverified assumptions about unattended automation under a subscription. For product-like programmatic Agent SDK calls use authorized API-key/provider integrations consistent with terms. Codex integrations must follow their current authentication and CLI sandbox docs. Confirm at actual integration time.

References: https://code.claude.com/docs/en/legal-and-compliance ; https://code.claude.com/docs/en/agent-sdk/overview ; https://developers.openai.com/codex/developer-commands
