---
title: "ZENITH: I Was Too Lazy to Manage My AI Agents, So I Built an AI to Manage Them"
published: false
tags: devchallenge, hf26challenge, opensource, ai
---

*This is a submission for the [Hacktoberfest Open-Source AI Challenge Week 3: Slow and Steady](https://dev.to/challenges/hacktoberfest-week3-2026-10-19)*

## What I Built

Every AI session I open starts from zero. I re-explain the project, I re-discover the decision we made three weeks
ago, and I re-check whether "all tests pass" was true *then* or is true *now*. I have agents that can run commands
and need babysitting, and agents that cannot and are useless. Managing them was becoming the job.

So I was lazy about it the sloth way: I built the thing that remembers, coordinates and checks, so that I don't.

**Zenith** is an open-source, local-first personal intelligence and agent-orchestration platform. It keeps a
project memory where every fact has a provenance and a freshness, it coordinates coding agents inside hard
boundaries, it verifies their claims in a sandbox, and it never acts without an explicit approval. **Misa** is the
assistant on top: you talk to it in Turkish or English, it answers from the records, and when you want natural
reasoning it hands the evidence to a **local Gemma** running on your own GPU. Nothing leaves the machine.

Who gets to be lazier: me, every morning, when I ask "where did we leave off?" and get the open tasks, the last
decision, the last *verified* test run and a warning that the source has not been scanned for nine days, instead
of scrolling through four chat histories.

## Demo

The whole demonstration runs on fictional data in a throwaway directory and takes about a minute:

```
scripts/install.sh && ollama pull gemma3:4b
zenith demo story --md transcript.md
```

Three scenes:

**Scene A — Memory (offline, deterministic).** A user returns to an abandoned project:

> **sen>** Harbor'da nerede kalmıştık?
> **misa>** Harbor için en son senden gelen not 2026-09-20 tarihli: "Harbor bitti sayılır, sadece dokümantasyon
> kaldı". Bu senin beyanın — henüz doğrulanmış değil. Testler en son 9cc634b'de geçmişti, ama commit edilmemiş
> değişiklik var; bugün de geçer mi bilmiyorum. Not: kaynak 'notes' unavailable — oradaki güncel durumu teyit
> edemiyorum. Açıkta 2 görev var; en acili "Release Harbor 1.0".

Four kinds of knowledge in one answer, kept apart: what the owner *reported*, what was *verified* and at which
revision, why that verification no longer *applies* (uncommitted changes), and what cannot be *confirmed* (a
source that disappeared). `kaynakları göster` prints the records behind the sentence.

**Scene B — Local reasoning (Gemma 3 4B via Ollama, 100 % on a laptop RTX 4050).** The same records, as a bounded,
evidence-tagged JSON context, go to the model with a JSON-schema constrained output. Here is a real reply, with
the measured latency:

> **sen>** Harbor'da neden SQLite'ta kalmıştık?
> **misa>** Harbor'da SQLite'ta kalmamızın nedeni, 'Keep SQLite, no Postgres — single-node personal use; migrations
> are simpler to audit' kararıdır. Ayrıca, 'notes' kaynağı kullanılamamaktadır ve 'Write Harbor documentation'
> adında bir görev hala gecikmektedir. Son olarak, 2026-09-20 tarihinde yaptığınız notta, "Harbor bitti sayılır,
> sadece dokümantasyon kaldı" diyordunuz ve bu henüz doğrulanmamıştır.
> *generated_by: model:ollama-conversation · grounding: supported · 3928 prompt tokens, 316 output tokens, 7.7 s
> wall, 52.6 tok/s*

And here is what happens when the model drifts. In the same transcript a 4B reply computed "355085 days ago" from
a far-future due date, and two replies carried stray JSON fragments; on earlier runs Gemma quoted a task title in
translation and the grounding validator, finding no record with that quote, replaced the reply with the
deterministic answer under a visible prefix: *(Modelin cevabı kayıtlarla eşleşmedi; onun yerine kayıtlardan
özetliyorum.)* When the reply repeats the owner's "bitti sayılır" claim, the engine appends a contradiction caveat.
The transcript keeps the raw model output next to the verdict, so you can judge the model and the validator
separately.

**Scene C — Orchestration (deterministic simulation).** The Engineering Director plans, an Implementer edits in an
isolated worktree, a sandboxed verifier runs the tests with no network and no credentials, the Director reviews,
and the owner's approval gates the whole thing. In the `false_claim` scenario the implementer reports "fixed it, all
tests pass" while the fix is wrong; the verifier disagrees and the run ends `BLOCKED / correction_required`. These
runs use **fake Director and Implementer adapters**: the orchestration, worktrees, approvals and the bubblewrap
verifier are real, the "agents" are scripted. I have not yet run the loop against a live Claude Code or Codex
agent, and I am not going to pretend otherwise.

Full transcript with raw model outputs and latencies: `docs/demo/TRANSCRIPT.md` in the repository.

## Code

{% embed https://github.com/miclaldogan/zenith-v1 %}

MIT licensed, Python standard library only (SQLite, no dependencies), 243 tests (`scripts/test.sh`). Created on
2026-10-23, inside the challenge window, and built during it.

## How I Built It

**Open-source AI at the core.** Misa's reasoning step is **Gemma 3 (4B, Q4_K_M)** served by **Ollama** on
loopback. The adapter (`src/zenith/misa/ollama.py`) refuses any non-loopback endpoint, needs no API key and no
cloud consent, caps the context (`num_ctx 8192`) and the output (`num_predict 700`), sends one request per turn
with a hard timeout, and constrains generation with the JSON schema of Zenith's reply contract. The reply then goes
through exactly the same validator as every other provider.

**The architecture is bigger than the model.**

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

- **Evidence ledger.** Every record has an origin (`HUMAN_REPORT`, `REPOSITORY_OBSERVATION`, `TEST_VERIFICATION`,
  `DOCUMENT`, `MODEL_INFERENCE`) and a confidence (`REPORTED`, `OBSERVED`, `VERIFIED`, `INFERRED`); evidence and
  events are append-only through SQLite triggers. A passing test is tied to a revision, so when the head moves the
  verification becomes *stale* instead of silently staying "green".
- **Context assembler.** For each question Misa resolves the referent ("that project", "when did we decide
  that?"), picks at most 40 items of at most 300 characters each, tags each with its epistemic status, and marks
  repository text as untrusted data.
- **Grounding validator.** Cited ids must exist; quoted strings, ISO dates and revision hashes must appear in the
  records; a present-tense "verified" over a stale verification gets a temporal caveat; a repeated "done" report
  with no applicable passing test gets a contradiction caveat. An unsupported reply is replaced, visibly.
- **Orchestration.** Durable runs with leases and idempotency keys, scope-hashed single-use approvals, immutable
  prompt artifacts, bounded retries, `RECONCILE_REQUIRED` after a crash, and a bubblewrap verifier with no network.
- **Provider independence.** One conversation port with four implementations. The Claude Code path uses the
  owner's own login through a read-only MCP server that only exposes projects explicitly marked for cloud sharing.

**Honest measurements** (RTX 4050 laptop, 6 GB VRAM, Ollama 0.30.7, details in `docs/EVALUATION.md`):

| | gemma3:4b | gemma4:e4b-it-qat | gemma3:12b |
|---|---|---|---|
| Placement | 100 % GPU, 2.75 GiB | partly offloaded | 63 % CPU / 37 % GPU |
| Per-turn wall (3–4k prompt tokens) | 6–10 s, ~50 tok/s | slower | ~6× slower |
| Acceptance checks (21, lexical) | 20/21 in each of 3 runs | 19/21 | 20/21 |
| Model replies accepted by the validator | 3 of 5 per run | 2 of 5 | 3 of 5 |

The small model drifts: it sometimes answers the question next to the one asked, once attributed a note to the
wrong source (which a lexical validator cannot catch), and once invented a citation (which it can, and did). The
12B model reasons better and is six times slower on this GPU. I kept 4B as the default and kept the validator in
front of all of them.

## Why Does Open Innovation Matter?

Because the memory is the valuable part, and it has to stay mine. Zenith's database holds every project, every
decision and every "the deploy slipped" note I ever wrote. An open-weight model on my own GPU can read all of it
with no consent form, no per-token bill and no third party in the loop. For a hosted model the same data needs an
explicit per-project consent flag, and the platform redacts everything else.

Open weights also made the honest evaluation possible: I could run three Gemma sizes against the same scenarios,
read the raw outputs, measure placement and latency on the exact hardware, and ship the transcript. With a closed
API the model is a black box behind a rate limit; with Ollama it is a process I can stop to test the failure path,
which I did.

And because the platform is open, the model is a plug: the provider port, the schema-constrained output and the
grounding validator do not care whether the reply came from Gemma, Claude or a deterministic composer.

## My Agent Session

Most of Zenith was built in pair-programming sessions with Claude Code, under rules I wrote into `AGENTS.md`: no
undeclared network calls, no credentials in code or chat, no mock presented as live, no commit without my word.
The repository's history shows the sprints; the `docs/plans/` directory holds the acceptance gates each one had to
pass.

## The Lazy Payoff

I have not measured minutes saved and will not invent a number. What I can say: the question I ask most, "where
did we leave off?", is now one command, and the answer tells me what is verified, what is only claimed, and what
has gone stale, which is the part I used to get wrong.

## Prize Categories

- **Best Use of Gemma** — Gemma 3 runs Misa's reasoning step locally through Ollama; measured, with raw outputs and
  failure cases in the repository.
