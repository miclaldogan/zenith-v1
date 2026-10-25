# Terminal demo recording script (60–90 s, fictional data only)

Goal: one take, no narration needed, readable at 1080p. The dataset is the synthetic Harbor/Lighthouse world that
`zenith demo story` builds in a throwaway directory; nothing of yours is touched or shown.

## Setup (before recording, not in the take)

```
scripts/install.sh                                   # installed `zenith` matches the repository
ollama pull gemma3:4b; ollama run gemma3:4b "" </dev/null   # model pulled and warm (avoids a 3–5 s cold load on camera)
cd "$(mktemp -d)"; clear
```

Set the terminal to about 120×32 with a 14–16 pt font. Recording tools on this machine: `ffmpeg` is installed
(`ffmpeg -f x11grab -framerate 30 -video_size 1920x1080 -i :0.0 demo.mp4` on X11; `wf-recorder` or OBS on Wayland).
`asciinema` is not installed; `pipx install asciinema` then `asciinema rec demo.cast` gives a recording DEV can embed
from asciinema.org.

## The take

| t | Type | What the viewer sees |
|---|---|---|
| 0:00 | `zenith demo story --md transcript.md > /dev/null; sed -n '5,22p' transcript.md` | the records: Harbor's stale verification, overdue task, two decisions; Lighthouse never scanned. Pause 8 s. |
| 0:15 | `grep -A2 'Harbor.da nerede' transcript.md \| head -4` | Scene A: "Harbor'da nerede kalmıştık?" answered offline, four kinds of knowledge kept apart. Pause 10 s. |
| 0:30 | `grep -A2 'neden SQLite' transcript.md \| head -4` | Scene B: the recorded Gemma reply, `grounding: supported`, latency line. Pause 10 s. |
| 0:45 | `grep -A7 'scenario .false_claim' transcript.md` | Scene C: implementer claims "all tests pass", verifier reports exit 1, run `BLOCKED (correction_required)`. Pause 8 s. |
| 1:00 | `ollama stop gemma3:4b; zenith misa --local --once "Harbor ne âlemde?"` | the honest failure path: "local model unavailable … Continuing offline", then a records-only answer. Pause 5 s. |
| 1:15 | end | |

Total about 75 s. The first command runs three Gemma turns (about 30 s); run it before the take and start recording
at the `sed` so the file is already on disk. Say so in the caption.

## Alternative single-command take (about 45 s)

```
zenith demo story | sed -n '1,60p'
```

shows the records, Scene A and the first Gemma turn in one scroll.

## Caption for the DEV post

"Recorded on a laptop RTX 4050 with gemma3:4b via Ollama on loopback. Fictional dataset. Scene C uses fake
Director and Implementer adapters (a deterministic simulation); the orchestration, worktrees, approvals and the
sandboxed verifier are the real code paths."
