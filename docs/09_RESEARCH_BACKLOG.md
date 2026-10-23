# 09 — Research Intelligence (future module, deliberately deferred)

## Product request
On demand, Zenith should research current academic literature on the user's project topic, compare methods and results, identify what's new, propose credible extensions and produce a concise, cited Turkish briefing (optionally English academic report). This resembles deep-research workflows, but Zenith must not claim to possess ChatGPT Deep Research capabilities directly.

## Proposed future workflow
1. Owner issues scoped research request: research question, date window, depth, language, databases, budget.
2. Search licensed/public scholarly sources: Crossref, OpenAlex, Semantic Scholar, arXiv, PubMed where relevant, publisher landing pages, institutional repositories; obey API licenses/rate limits.
3. Deduplicate by DOI/arXiv ID, venue, version, date and author; find retractions/corrections and publication status.
4. Read abstracts/open-access papers **only when accessible**, cite exact source metadata; do not claim full-text analysis from an abstract alone.
5. Use strong model to build evidence matrix: task, datasets, evaluation protocols, metrics, limitations, reproducibility, extensions.
6. Show retrieval timestamp and publication/event dates separately. Explicitly distinguish preprint, peer-reviewed work and unverified claims.
7. Produce short digest and optional full Markdown/PDF report with links, quotations limits and uncertainty notes.
8. Save report and connect to project memory with source provenance, user approval required before it changes implementation priorities.

## Not in v1 implementation
Automatic weekly paper scraping, unsupervised literature claims, bypassing paywalls, pretending inaccessible papers were read, and auto-installing third-party web tools. Schedule/update automation only after separate user opt-in.

## Acceptance gates
- Every paper claim has a real, resolvable source record.
- At least one deduplication and one retraction/check test.
- No invented publication data; absence of accessible full text is noted.
- A report identifies what was actually retrieved and when.

## Registry link (2026-10-23)
This backlog is the detail behind **ZENITH-REQ-006 — Research Intelligence** in `11_REQUIREMENTS_REGISTRY.md`. Added by the Misa Intelligence directive: publication and source verification, DOI/persistent references, methodology comparison, findings/limitations/evidence assessment, delivery through a configured channel, continuation into an interactive research conversation, optional scheduling. Hard rules: verified findings are stored with a distinct evidence origin from speculative ideas/hypotheses (REQ-007); sources, citations and findings are never invented — unverifiable claims are labelled as such.

## Specialised workflow, judges and artifacts (directive 2026-10-23)
Pipeline: request → planning → literature discovery → source acquisition → paper analysis → methodological review → independent evidence verification → synthesis → report → delivery → optional discussion. Providers (to evaluate, interchangeable behind one port): OpenAlex, Crossref, Semantic Scholar, arXiv; DOI resolution deterministic. Per source, three independent dimensions: access level (metadata_only · abstract · full_text_accessible); publication/review status (preprint · peer_reviewed · published_not_peer_reviewed · retracted · unknown); independent scientific verification (unverified · replicated_by_others · independently_reproduced · contested). None implies another. Judges: Evidence Judge (identity, identifier, claim↔source, full-text examined?, metrics/conditions, dataset/baseline comparability, uncertainty, contradictions, unsupported claims) and Methodology Judge (design, leakage, validity threats, interpretation) — schema-driven, impartial, deterministic where possible; model agreement is not proof. Artifacts: brief, search strategy, collection, source record, findings, claim–evidence mapping, methodological assessment, verification report, synthesis, final report, discussion context — all versioned/immutable via Zenith's `artifacts`. Acceptance criteria: see REQ-006 in `11_REQUIREMENTS_REGISTRY.md`.
