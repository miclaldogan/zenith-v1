# Local Gemma evaluation (measured, 2026-10-25)

Everything below was measured on the development machine on 2026-10-25 with the commands shown. Nothing is
estimated. The data is the fictional Harbor/Lighthouse world (`zenith.misa.acceptance.build_world`), so the
numbers describe Zenith's behaviour, not any real project.

## Hardware and runtime

| Item | Value (observed) |
|---|---|
| CPU / RAM | 20 logical cores, 15 GiB RAM |
| GPU | NVIDIA GeForce RTX 4050 Laptop, 6 GiB VRAM (`nvidia-smi`) |
| Runtime | Ollama 0.30.7, listening on `127.0.0.1:11434` only (`ss -ltnp`), `OLLAMA_HOST` unset |
| Default model | `gemma3:4b` — 4.3 B parameters, quantization Q4_K_M, 2.56 GiB resident, 100 % in VRAM (`/api/ps`) |
| Context / output caps | `num_ctx 8192`, `num_predict 700`, temperature 0.2, JSON-schema constrained output |

## Latency (`zenith demo story`, Scene B, gemma3:4b)

| Turn | Prompt tokens | Output tokens | Wall | Generation rate | Validator |
|---|---|---|---|---|---|
| "Harbor ne âlemde?" (includes 3.4 s model load) | 3641 | 276 | 10.03 s | 50.3 tok/s | partial (contradiction caveat) |
| "Harbor'da neden SQLite'ta kalmıştık?" | 3928 | 316 | 7.73 s | 52.6 tok/s | supported |
| "Son doğrulama hâlâ güncel mi?" | 4012 | 487 | 11.15 s | 51.6 tok/s | partial (contradiction caveat) |
| "Orion'da nerede kalmıştık?" | — | — | — | no model call | deterministic refusal |

Cold start adds 3–5 s of model load on the first call (`load_duration`), after which the model stays
resident for 10 minutes (`keep_alive`). Prompt evaluation of ~3–4k tokens takes under 1 s on the GPU; the wall time
is dominated by generation. GPU memory during inference: ~2.9 GiB used (`nvidia-smi`).

## Correctness on the acceptance scenarios

`zenith misa --eval --provider ollama-conversation` plays three scripted conversations (A: missing information,
B: complex status, C: multi-turn reasoning) and applies 21 deterministic checks to the replies the owner sees.
The checks are lexical; they catch missing content and false claims, not paraphrase quality or semantic accuracy. "Checks passed" counts those checks on the reply the owner saw (which may be the deterministic fallback); "model replies accepted" counts the model's own replies that reached the owner unchanged.

| Model | Runs | Checks passed | Model replies accepted by the validator | Fallbacks to records | Wall per run |
|---|---|---|---|---|---|
| gemma3:4b (Q4_K_M, 100 % GPU) | 3 | 20/21, 20/21, 20/21 | 3 of 5 model turns per run | 2 per run | 46–52 s |
| gemma4:e4b-it-qat (6.1 GB, partly offloaded) | 1 | 19/21 | 2 of 5 | 3 | 80 s |
| gemma3:12b (Q4, 63 % CPU / 37 % GPU) | 1 | 20/21 | 3 of 5 | 2 | 323 s |

Scenario A (the model is never consulted: Zenith answers "no such project" deterministically before any model
call) passed every time. The failing check moved between runs for gemma3:4b: once the "why SQLite" answer named
the wrong decision (the append-only event-log decision instead of the SQLite one), once the comparison turn
pretended to compare instead of saying a model-level comparison was not grounded, once a comparison reply named
only one project. The 12 B model answered the "why" question correctly but took six times longer with the
CPU offload; it is not the default.

## Hallucination and uncertainty handling (observed, not designed on paper)

- **Invented citation caught.** On a cross-project question with one item in context, gemma3:4b cited a second,
  non-existent id. The validator marked the reply `unsupported` and the owner saw the deterministic answer with the
  visible prefix "(Modelin cevabı kayıtlarla eşleşmedi; …)". This happened in the diagnostic run and is covered by
  `tests/test_ollama.py::test_hallucinated_citation_falls_back_to_records`.
- **Translated quote caught.** The model quoted the task "Write Harbor documentation" as "Harbor dokümantasyonu yaz"
  in quotation marks; the quote check rejected it because that string exists in no record. The prompt now asks for
  verbatim quotes; paraphrase without quotes is accepted.
- **Contradiction caveat appended.** When a reply repeated the owner's "bitti sayılır" report without marking it
  unverified, the engine appended "(Not: 'bitti' bilgisi senin beyanın; doğrulanmış bir test kaydı yok.)".
- **Not caught.** In one run the model attributed the owner's note to "Orion" (a name from an earlier turn about a
  non-existent project). The validator does not check attribution; this is a known limitation of lexical checks.
- **Refusal to invent.** "Misa, Orion diye bir proje var mı?" and "Orion'da nerede kalmıştık?" are answered without
  any model call: "'Orion' adında kayıtlı bir proje yok. Elimdekiler: Harbor, Lighthouse."

## Failure when the model is unavailable

Tested with a dead loopback port, an HTTP 500 and non-JSON output (`tests/test_ollama.py`), and manually by
stopping Ollama: `zenith misa --local` prints "local model unavailable — … Continuing offline" and every turn is
answered from the records with `provider_failed:*` recorded in the turn's uncertainty. No mock answer is shown as a
model answer.

## What this does not show

No benchmark against a hosted model was run. No time-saving figure is claimed. The acceptance checks are lexical;
semantic fidelity was judged by reading the transcripts in `docs/demo/TRANSCRIPT.md`.
