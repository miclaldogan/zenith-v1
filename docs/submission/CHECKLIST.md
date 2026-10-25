# Pre-publication checklist — Hacktoberfest Week 3 (deadline: October 25, 2026, 11:59 PM PDT = October 26, 06:59 UTC)

Confirm the deadline on the challenge page itself before anything else: the page and its details say 11:59 PM PDT
on October 25; the DEV API's `ends_at` field for this challenge says October 25, 06:59 UTC. If the submit flow on
the page is closed, stop.

## Before publishing
- [ ] Record the demo with `docs/submission/DEMO_RECORDING.md`; upload (YouTube unlisted or asciinema.org); replace
      the "[link to be added before publishing]" line in the Demo section with the embed
      (`{% youtube <id> %}` or `{% asciinema <id> %}`).
- [ ] Read `docs/submission/DEV_HF26_WEEK3.md` once as yourself: it is written in the first person.
- [ ] Verify the three GitHub links open publicly: README, `docs/demo/TRANSCRIPT.md`, `docs/EVALUATION.md`.
- [ ] Commit and push the three new submission files (`DEV_HF26_WEEK3.md`, `DEMO_RECORDING.md`, `CHECKLIST.md`)
      so the repository matches the post. Nothing else is pending.
- [ ] Eligibility: 18+, DEV account in good standing, not in an excluded jurisdiction, no employer-policy conflict.
- [ ] Connect your DEV account at hacktoberfest.com/my if you want the Week 3 sticker.

## Publishing on DEV
- [ ] New post, paste the Markdown. Front matter tags must include `devchallenge` and `hf26challenge` (both are in
      the draft). Keep the "This is a submission for …" line.
- [ ] Set the AI disclosure level honestly in the post settings (the text already discloses Claude Code
      pair-programming); if publishing through DevRelay, `ai_disclosure_level: some_ai`.
- [ ] Cover image optional; if used, do not use the Hacktoberfest logos in a modified form.
- [ ] Preview: the GitHub embed renders; the ASCII diagram is inside a fenced block; the two Turkish quotes keep
      their curly quotes (they are verbatim from the transcript).
- [ ] Publish before the deadline; the sponsor's clock is official.

## Known gaps, stated in the post or the repository
- The orchestration loop has only run with fake adapters (stated in Scene C and in the README matrix).
- The acceptance checks are lexical; "20/21" and "3 of 5" are explained in the post.
- The transcript header prints "latest owner report: none" for Harbor although the September note exists; the note
  is shown in the replies and in `kaynakları göster`. Cosmetic renderer gap, not a data problem.
- gemma3:4b output is nondeterministic; a reader who re-runs `zenith demo story` will see different replies.
