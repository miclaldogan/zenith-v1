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
