---
title: "ZENITH: I Was Too Lazy to Manage My AI Agents, So I Built an AI to Manage Them"
published: false
tags: devchallenge, hf26challenge, opensource, ai
---

*This is a submission for the [Hacktoberfest Open-Source AI Challenge Week 3: Slow and Steady](https://dev.to/challenges/hacktoberfest-week3-2026-10-19)*

## What I Built

Every AI session I open starts from zero. I re-explain the project, I re-discover the decision we made three weeks
ago, and I re-check whether "all tests pass" was true *then* or is still true *now*. The agents that can run
commands need babysitting; the ones that cannot are useless. Managing them had become the job.

So I did the sloth thing: I built the part that remembers and the part that checks, so that I don't have to.

**Zenith** is an open-source, local-first personal intelligence and agent-orchestration platform. Two ideas carry
it:

1. **Evidence-grounded memory.** Every fact about a project has an origin and a confidence. A test that passed is
   tied to the revision it passed on; when the code moves, the verification goes *stale* instead of staying green.
   A note I wrote is a *report*, never a *verification*. A source that disappeared is *unconfirmed*, not "done".
2. **Trustworthy orchestration.** Coding agents work inside hard boundaries: a scope-hashed, single-use approval
   per task, an isolated worktree, a sandboxed verifier with no network and no credentials, and a reviewer that
   compares what the agent *claimed* with what the verifier *saw*.

**Misa** is the assistant on top. You talk to it in Turkish or English. It answers from the records, offline, by
default. When you want natural reasoning it hands the evidence to **Gemma running locally on your own GPU**, and
then checks the model's answer against the records before you see it. Nothing leaves the machine.

Who gets to be lazier: me, every morning, when "where did we leave off?" returns the open tasks, the last decision,
the last *verified* test run and a warning that one source has not been scanned, instead of four chat histories.

## Demo

*(Video: 71-second promo narrated by Misa and Zenith, `docs/submission/media/zenith-promo-duo-en.mp4` — upload to
YouTube and replace this line with `{% youtube <id> %}` before publishing.)*

![Misa answers "Where did we leave off in Harbor?" and lists the records behind the answer](https://raw.githubusercontent.com/miclaldogan/zenith-v1/main/docs/submission/media/01-misa-memory.png)

![Misa reasoning with local Gemma 3 4B via Ollama on loopback](https://raw.githubusercontent.com/miclaldogan/zenith-v1/main/docs/submission/media/02-misa-local-gemma.png)

![Misa refuses to invent a project called Orion, then answers an English question](https://raw.githubusercontent.com/miclaldogan/zenith-v1/main/docs/submission/media/04-misa-refusal-and-english.png)

![Orchestration simulation: a false "all tests pass" claim is blocked by the sandboxed verifier](https://raw.githubusercontent.com/miclaldogan/zenith-v1/main/docs/submission/media/05-orchestration-simulation.png)

The whole demonstration runs on fictional data in a throwaway directory and is reproducible with two commands:

```
scripts/install.sh && ollama pull gemma3:4b
zenith demo story --md transcript.md
```

The full recorded transcript, with raw model outputs and latencies, is in the repository:
[docs/demo/TRANSCRIPT.md](https://github.com/miclaldogan/zenith-v1/blob/main/docs/demo/TRANSCRIPT.md). Everything
quoted below is copied from it verbatim.

**Scene A — Memory (offline, deterministic, no model).** A user comes back to a project called Harbor that has a
passing test run on an old revision, uncommitted changes since, an overdue release task, an optimistic note from
September and a source directory that no longer exists.

> **sen>** Harbor'da nerede kalmıştık?
> **misa>** Harbor için en son senden gelen not 2026-09-20 tarihli: “Harbor bitti sayılır, sadece dokümantasyon
> kaldı”. Bu senin beyanın — henüz doğrulanmış değil. Testler en son 2026-10-25'de 9cc634b'de geçmişti, ama commit
> edilmemiş değişiklik var; bugün de geçer mi bilmiyorum. Not: kaynak 'notes' unavailable — oradaki güncel durumu
> teyit edemiyorum. Açıkta 2 görev var; en acili “Release Harbor 1.0” — 2020-01-01'de bitmeliydi. Kaynakları görmek
> istersen “kaynakları göster” de.

Four kinds of knowledge in one answer, kept apart: what I *reported*, what was *verified* and on which revision,
why that verification no longer *applies* (uncommitted changes), and what cannot be *confirmed* (a source that
disappeared). `kaynakları göster` lists the four records the sentence rests on, with their provenance.

**Scene B — Local reasoning (Gemma 3 4B via Ollama, 100 % on a laptop RTX 4050).** The same records go to the model
as a bounded, evidence-tagged JSON document with a JSON-schema constrained output. This is a recorded, successful
reply, accepted by the validator as it stands:

> **sen>** Harbor'da neden SQLite'ta kalmıştık?
> **misa>** Harbor'da SQLite'ta kalmamızın nedeni, ‘Keep SQLite, no Postgres — single-node personal use; migrations
> are simpler to audit’ kararıdır Ayrıca, ‘notes’ kaynağı kullanılamamaktadır ve ‘Write Harbor documentation’
> adında bir görev hala gecikmektedir Son olarak, 2026-09-20 tarihinde yaptığınız notta, "Harbor bitti sayılır,
> sadece dokümantasyon kaldı" diyordunuz ve bu henüz doğrulanmamıştır.
> *generated_by: model:ollama-conversation · grounding: supported · cited: 4 records · 7.73 s wall (3928 prompt
> tokens in 1.14 s, 316 output tokens in 6.0 s = 52.6 tok/s)*

It quotes the recorded decision verbatim, names the unavailable source and the open task, and keeps my note marked
as unverified. The prose has two missing full stops; that is the 4B model, and I left it as recorded.

The turn before it, in the same transcript, shows the other side. The model produced a reply with a stray JSON
fragment and an impossible "355085 gün önce" computed from a placeholder due date. The validator could not check
arithmetic, but because the reply repeated my "bitti sayılır" note as a fact it appended a contradiction caveat:
*(Not: 'bitti' bilgisi senin beyanın; doğrulanmış bir test kaydı yok.)* In other runs the same model invented a
citation id, and quoted a task title in translation; both were caught, and the owner saw the deterministic answer
under a visible prefix: *(Modelin cevabı kayıtlarla eşleşmedi; onun yerine kayıtlardan özetliyorum.)* The
transcript keeps every raw model output next to its verdict, so you can judge the model and the validator
separately.

**Scene C — Orchestration (deterministic simulation).** The Engineering Director plans, an Implementer edits in an
isolated worktree, a bubblewrap-sandboxed verifier runs the tests with no network and no credentials, the Director
reviews, and my approval gates the whole thing. In the `false_claim` scenario the implementer reports "Fixed it,
all tests pass" while the fix is wrong; the verifier reports `unit: exit 1, expected 0`, the reviewer records the
mismatch, and the run ends `BLOCKED (correction_required)`. These runs use **fake Director and Implementer
adapters**: the orchestration, worktrees, approvals and the sandboxed verifier are the real code paths, the
"agents" are scripted. I have not yet run this loop against a live Claude Code or Codex agent, and I am not going
to pretend otherwise.

## Code

{% embed https://github.com/miclaldogan/zenith-v1 %}

MIT licensed, Python standard library only (SQLite, no dependencies), 243 tests (`scripts/test.sh`). The repository
was created on 2026-10-23, inside the challenge window, and everything in it was built during the window.

## How I Built It

**The open pieces are what make it work.** Misa's reasoning step is **Gemma 3 (4B, Q4_K_M)** served by **Ollama**
on loopback. The adapter (`src/zenith/misa/ollama.py`) refuses any non-loopback endpoint, needs no API key and no
consent flag, caps the context (`num_ctx 8192`) and the output (`num_predict 700`), sends one request per turn with
a hard timeout, and constrains generation with the JSON schema of Zenith's reply contract. The reply then goes
through exactly the same validator as every other provider.

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
  events are append-only through SQLite triggers.
- **Freshness.** A verification carries the revision it ran on. Head moved, uncommitted changes, a stale scan or an
  unavailable source each turn `VERIFIED` into `VERIFICATION_STALE` with the reason attached.
- **Context assembler.** For each question Misa resolves the referent ("that project", "when did we decide
  that?"), picks at most 40 records of at most 300 characters, tags each with its epistemic status, and marks
  repository text as untrusted data the model must not take instructions from.
- **Grounding validator.** Cited ids must exist; quoted strings, ISO dates and revision hashes must appear in the
  records; a present-tense "verified" over a stale verification gets a temporal caveat; a repeated "done" report
  with no applicable passing test gets a contradiction caveat. An unsupported reply is replaced, visibly. It does
  not check paraphrase fidelity or arithmetic; that is a limit, not a feature.
- **Orchestration.** Durable runs with leases and idempotency keys, scope-hashed single-use approvals, immutable
  prompt artifacts, bounded retries, `RECONCILE_REQUIRED` after a crash, and a bubblewrap verifier with no network.
  A model can only *propose* a note or a task; Zenith asks me before anything is written.
- **Provider independence.** One conversation port, four implementations. The Claude Code path uses my own login
  through a read-only MCP server that exposes only projects I explicitly marked for cloud sharing.

**Measurements** (laptop RTX 4050, 6 GB VRAM, Ollama 0.30.7; full tables and the failure cases in
[docs/EVALUATION.md](https://github.com/miclaldogan/zenith-v1/blob/main/docs/EVALUATION.md)):

| | gemma3:4b | gemma4:e4b-it-qat | gemma3:12b |
|---|---|---|---|
| Placement | 100 % GPU, 2.6 GiB VRAM | partly offloaded | 63 % CPU / 37 % GPU |
| Wall per turn (3.6–4k prompt tokens) | 7.7–11.2 s, 50–53 tok/s | slower | about 6× slower |
| Deterministic acceptance checks (21) | 20/21 in each of 3 runs | 19/21 | 20/21 |
| Model replies accepted as-is by the validator | 3 of 5 model turns per run | 2 of 5 | 3 of 5 |

Read the numbers for what they are. "20/21" counts lexical checks on the answers the owner sees (does the reply
name the project, mention the open task, avoid a false "completed"), not semantic accuracy; the deterministic
fallback passes most of them on its own. "3 of 5" is the share of Gemma's own replies that reached the owner
unchanged; the other two were replaced by the records-only answer. The small model drifts: it sometimes answers the
neighbouring question, once attributed my note to a name from an earlier turn (which a lexical validator cannot
catch), and once invented a citation (which it can, and did). The 12B model reasoned better and was six times
slower on this GPU. I kept 4B as the default and kept the validator in front of all of them.

## Why Does Open Innovation Matter?

Because the memory is the valuable part, and it has to stay mine. Zenith's database holds every project, every
decision and every "the deploy slipped" note I ever wrote. An open-weight model on my own GPU can read all of it
with no consent form, no per-token bill and no third party in the loop. For a hosted model the same data needs an
explicit per-project consent flag, and the platform withholds everything else.

Open weights also made the honest evaluation possible: I could run three Gemma sizes against the same scenarios,
read the raw outputs, measure placement and latency on the exact hardware, and ship the transcript. With a closed
API the model is a black box behind a rate limit; with Ollama it is a process I can stop to test the failure path,
which I did: Misa prints "local model unavailable … Continuing offline" and answers from the records.

And because the platform is open, the model is a plug: the provider port, the schema-constrained output and the
grounding validator do not care whether the reply came from Gemma, a hosted model or a deterministic composer.

## My Agent Session

AI disclosure: most of Zenith's code was written in pair-programming sessions with Claude Code, under rules I
keep in the repository's `AGENTS.md`: no undeclared network calls, no credentials in code or chat, no mock
presented as live, no commit without my word. The design, the acceptance gates in `docs/plans/` and the decisions
about what counts as evidence are mine; the measurements above were taken on my machine and recorded as they came.

## The Lazy Payoff

I have not measured minutes saved and will not invent a number. What I can say: the question I ask most, "where
did we leave off?", is now one command, and the answer tells me what is verified, what is only claimed, and what
has gone stale, which is the part I used to get wrong.

## Prize Categories

- **Best Use of Gemma** — Gemma 3 runs Misa's reasoning step locally through Ollama, with recorded outputs,
  measured latency and documented failure cases in the repository.
