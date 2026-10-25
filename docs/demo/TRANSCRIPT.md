# Zenith demo transcript

Generated 2026-10-25T20:10:27+00:00 · fictional (synthetic Harbor/Lighthouse world); no owner data · workspace `/tmp/zenith-story-i4qd8fcv`

## The records (what Zenith knows)

**Harbor** — status `VERIFICATION_STALE` (a passing verification exists but the revision has moved or cannot be confirmed)
- sources: harbor (fresh), notes (unavailable)
- latest verification: unit exit=0 at 9cc634bbb2ad at `9cc634b` — applies now: **False** (uncommitted_changes)
- latest owner report: none
- open task: Release Harbor 1.0 · DRAFT · due 2020-01-01T23:59:59+03:00 · overdue=True · blocked_by=1
- open task: Write Harbor documentation · DRAFT · due 2999-01-01T23:59:59+03:00 · overdue=False · blocked_by=0
- decision: Event log is append-only — auditability over convenience
- decision: Keep SQLite, no Postgres — single-node personal use; migrations are simpler to audit

**Lighthouse** — status `REPORTED_ONLY` (only human/document/model claims; nothing observed or verified)
- sources: lighthouse (never_scanned)
- latest verification: none
- latest owner report: none
- open task: Lighthouse API sketch · DRAFT · due 2999-02-01T23:59:59+03:00 · overdue=False · blocked_by=0
- decision: Use Postgres for Lighthouse — multi-user access and concurrent writers

## Scene A — Memory — offline, deterministic

**sen>** Harbor'da nerede kalmıştık?  
**misa>** Harbor için en son senden gelen not 2026-09-20 tarihli: “Harbor bitti sayılır, sadece dokümantasyon kaldı”. Bu senin beyanın — henüz doğrulanmış değil. Testler en son 2026-10-25'de 9cc634b'de geçmişti, ama commit edilmemiş değişiklik var; bugün de geçer mi bilmiyorum. Not: kaynak 'notes' unavailable — oradaki güncel durumu teyit edemiyorum. Açıkta 2 görev var; en acili “Release Harbor 1.0” — 2020-01-01'de bitmeliydi. Kaynakları görmek istersen “kaynakları göster” de.  
_generated_by: `deterministic-offline` · grounding: `supported` · cited: 4 record(s)_  

**sen>** Harbor'da bitmeyen ne var?  
**misa>** Harbor için en son senden gelen not 2026-09-20 tarihli: “Harbor bitti sayılır, sadece dokümantasyon kaldı”. Bu senin beyanın — henüz doğrulanmış değil. Testler en son 2026-10-25'de 9cc634b'de geçmişti, ama commit edilmemiş değişiklik var; bugün de geçer mi bilmiyorum. Not: kaynak 'notes' unavailable — oradaki güncel durumu teyit edemiyorum. Açıkta 2 görev var; en acili “Release Harbor 1.0” — 2020-01-01'de bitmeliydi. Kaynakları görmek istersen “kaynakları göster” de.  
_generated_by: `deterministic-offline` · grounding: `supported` · cited: 4 record(s)_  

**sen>** kaynakları göster  
**misa>** Son cevabın dayandığı kayıtlar:
  [ev:93051f0d] HUMAN_REPORT / REPORTED · 2026-09-20 07:00 · Harbor bitti sayılır, sadece dokümantasyon kaldı
  [ev:936067cc] TEST_VERIFICATION / VERIFIED · 2026-10-25 20:10 · unit exit=0 at 9cc634bbb2ad
  [src:e2fe50f6] notes
  [task:95bf813f] Release Harbor 1.0 · DRAFT · due 2020-01-01
  destek: supported · alıntı/tarih/sürüm kontrolü geçti · anlamsal destek ve çıkarımlar denetlenmez  
_generated_by: `deterministic` · grounding: `supported` · cited: 4 record(s)_  

## Scene B — Local reasoning — open-weight model via Ollama

Model `gemma3:4b` via Ollama 0.30.7 at `http://127.0.0.1:11434` (loopback only, nothing leaves the machine). Limits: num_ctx 8192, num_predict 700, timeout 120.0 s.

Runtime: 4.3B parameters, quantization Q4_K_M, 2.56 GiB resident, 2.56 GiB in VRAM → GPU, context 8192.

**sen>** Harbor ne âlemde?  
**misa>** Harbor'da şu anda açıkta ‘Write Harbor documentation’ adında bir görev var. Bu görev 355085 gün önce açılmış ve hala gecikmiş. Ayrıca, ‘notes’ kaynağı kullanılamıyor. Son olarak, 2026-09-20 tarihinde yaptığın notta, "Harbor bitti sayılır, sadece dokümantasyon kaldı" diyordun. Bu henüz doğrulanmadı.”} </body> (Not: 'bitti' bilgisi senin beyanın; doğrulanmış bir test kaydı yok.)  
_generated_by: `model:ollama-conversation` · grounding: `partial` · contradiction caveat · cited: 2 record(s)_  
<details><summary>raw model output</summary>

```json
{
 "text": "Harbor'da şu anda açıkta ‘Write Harbor documentation’ adında bir görev var. Bu görev 355085 gün önce açılmış ve hala gecikmiş. Ayrıca, ‘notes’ kaynağı kullanılamıyor. Son olarak, 2026-09-20 tarihinde yaptığın notta, \"Harbor bitti sayılır, sadece dokümantasyon kaldı\" diyordun. Bu henüz doğrulanmadı.”} </body>",
 "cited_ids": [
  "92cd210c-afd0-49b4-9981-28a88fd5cef3",
  "e2fe50f6-b8f8-42a1-90fe-a48a7a495982"
 ],
 "uncertainty": [],
 "actions": [],
 "needs_clarification": false
}
```
</details>  
_latency: 10.03 s wall (3641 prompt tokens in 1.12 s, 276 output tokens in 5.49 s = 50.3 tok/s; load 3.38 s)_  

**sen>** Harbor'da neden SQLite'ta kalmıştık?  
**misa>** Harbor'da SQLite'ta kalmamızın nedeni, ‘Keep SQLite, no Postgres — single-node personal use; migrations are simpler to audit’ kararıdır Ayrıca, ‘notes’ kaynağı kullanılamamaktadır ve ‘Write Harbor documentation’ adında bir görev hala gecikmektedir Son olarak, 2026-09-20 tarihinde yaptığınız notta, "Harbor bitti sayılır, sadece dokümantasyon kaldı" diyordunuz ve bu henüz doğrulanmamıştır.  
_generated_by: `model:ollama-conversation` · grounding: `supported` · cited: 4 record(s)_  
<details><summary>raw model output</summary>

```json
{
 "text": "Harbor'da SQLite'ta kalmamızın nedeni, ‘Keep SQLite, no Postgres — single-node personal use; migrations are simpler to audit’ kararıdır Ayrıca, ‘notes’ kaynağı kullanılamamaktadır ve ‘Write Harbor documentation’ adında bir görev hala gecikmektedir Son olarak, 2026-09-20 tarihinde yaptığınız notta, \"Harbor bitti sayılır, sadece dokümantasyon kaldı\" diyordunuz ve bu henüz doğrulanmamıştır.",
 "cited_ids": [
  "92cd210c-afd0-49b4-9981-28a88fd5cef3",
  "e2fe50f6-b8f8-42a1-90fe-a48a7a495982",
  "95bf813f-f588-4a16-a9a8-1efe9d1c495b",
  "93051f0d-4709-47b4-8134-b1b2d1bc90ff"
 ],
 "uncertainty": [
  "source_unavailable",
  "no_records"
 ],
 "actions": [],
 "needs_clarification": false
}
```
</details>  
_latency: 7.73 s wall (3928 prompt tokens in 1.14 s, 316 output tokens in 6.0 s = 52.6 tok/s; load 0.39 s)_  

**sen>** Son doğrulama hâlâ güncel mi?  
**misa>** Son doğrulama hâlâ güncel mi? Harbor için en son test sonucu 2026-10-25 tarihinde 9cc634b'de geçmişti, ancak commit edilmemiş değişiklikler var. Ayrıca, ‘notes’ kaynağı kullanılamıyor ve ‘Write Harbor documentation’ adında bir görev hala gecikmiş. Son olarak, 2026-09-20 tarihinde yaptığınız notta, "Harbor bitti sayılır, sadece dokümantasyon kaldı" diyordunuz.”}ktds: (Not: 'bitti' bilgisi senin beyanın; doğrulanmış bir test kaydı yok.)  
_generated_by: `model:ollama-conversation` · grounding: `partial` · contradiction caveat · cited: 3 record(s)_  
<details><summary>raw model output</summary>

```json
{
 "text": "Son doğrulama hâlâ güncel mi? Harbor için en son test sonucu 2026-10-25 tarihinde 9cc634b'de geçmişti, ancak commit edilmemiş değişiklikler var. Ayrıca, ‘notes’ kaynağı kullanılamıyor ve ‘Write Harbor documentation’ adında bir görev hala gecikmiş. Son olarak, 2026-09-20 tarihinde yaptığınız notta, \"Harbor bitti sayılır, sadece dokümantasyon kaldı\" diyordunuz.”}ktds:",
 "cited_ids": [
  "936067cc-0ec0-403d-a3b2-710339a5ef1b",
  "92cd210c-afd0-49b4-9981-28a88fd5cef3",
  "e2fe50f6-b8f8-42a1-90fe-a48a7a495982"
 ],
 "uncertainty": [
  "source_unavailable",
  "no_records"
 ],
 "actions": [],
 "needs_clarification": false
}
```
</details>  
_latency: 11.15 s wall (4012 prompt tokens in 1.16 s, 487 output tokens in 9.44 s = 51.6 tok/s; load 0.36 s)_  

**sen>** Orion'da nerede kalmıştık?  
**misa>** 'Orion' adında kayıtlı bir proje yok. Elimdekiler: Harbor, Lighthouse.  
_generated_by: `deterministic` · grounding: `partial` · contradiction caveat · cited: 0 record(s)_  

## Scene C — Orchestration — DETERMINISTIC SIMULATION with fake adapters (no real Claude Code/Codex execution)

### scenario `success` → run `COMPLETED` (verified_and_reviewed), task `COMPLETED`, attempt 1
- 1. owner registered the project and an engineering objective (task)
- 2-3. director produced proposal a7b92351 and prompt f1b75b5e (persisted)
- 4. owner approval ea9857cd recorded (single use, scope-hashed) and task queued
- 5. run 980a7336 created; duplicate submission ignored=True
- 5-8. worker finished: run COMPLETED (verified_and_reviewed), task COMPLETED
- 9. after reopening: 6 artifacts, 9 run events, brief status VERIFIED
- artifacts: context_package, architecture_proposal, implementation_prompt, implementation_report, verification_report, review_findings
- approvals: [{'id': 'ea9857cd-b742-414f-a464-28100fee0875', 'use_count': 1, 'max_uses': 1, 'used_by': '980a7336-f2e3-4579-81a8-eb60cc624306'}]
- verification report: {"passed": true}
- primary tree untouched: True · sandbox available: True

### scenario `false_claim` → run `BLOCKED` (correction_required), task `BLOCKED`, attempt 1
- 1. owner registered the project and an engineering objective (task)
- 2-3. director produced proposal 886b1453 and prompt 3f472505 (persisted)
- 4. owner approval 451f7bd4 recorded (single use, scope-hashed) and task queued
- 5. run 8ac337b8 created; duplicate submission ignored=True
- 5-8. worker finished: run BLOCKED (correction_required), task BLOCKED
- 9. after reopening: 7 artifacts, 9 run events, brief status VERIFICATION_FAILING
- artifacts: context_package, architecture_proposal, implementation_prompt, implementation_report, verification_report, review_findings, implementation_prompt
- approvals: [{'id': '451f7bd4-a49e-4274-86de-9afd1f1d1964', 'use_count': 1, 'max_uses': 1, 'used_by': '8ac337b8-9703-436c-bdaa-8f563124580b'}]
- verification report: {"passed": false}
- primary tree untouched: True · sandbox available: True

_Environment: Python 3.14.7, Linux-7.2.2-arch1-1-x86_64-with-glibc2.44_
