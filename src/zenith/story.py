"""`zenith demo story`: one reproducible, self-contained demonstration in three scenes.

Fictional data only (the synthetic Harbor/Lighthouse world from `misa.acceptance.build_world`),
in a throwaway directory. The owner's database and projects are never touched.

Scene A — Memory.      Offline, deterministic Misa answers "where did we leave off?" from the records.
Scene B — Reasoning.   The same records go to a local open-weight model (Gemma via Ollama); the
                        raw model output, Zenith's grounding verdict and measured latency are shown.
                        If Ollama is unavailable the scene says so and shows the offline fallback.
Scene C — Orchestration. The existing bounded Director → Implementer → Verifier → Review workflow
                        with FAKE adapters: a deterministic orchestration simulation, labelled as such.
"""
from __future__ import annotations

import json
import platform
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .demo import run_poc
from .kernel import Store
from .misa import acceptance
from .misa.repl import MisaSession

SCENE_A = ["Harbor'da nerede kalmıştık?", "Harbor'da bitmeyen ne var?", "kaynakları göster"]
SCENE_B = ["Harbor ne âlemde?", "Harbor'da neden SQLite'ta kalmıştık?", "Son doğrulama hâlâ güncel mi?",
           "Orion'da nerede kalmıştık?"]


def _records(store: Store, pid: str) -> dict:
    b = store.brief(pid)["projects"][0]
    return {"status": b["status"]["label"], "status_meaning": b["status"]["meaning"],
            "sources": [{"name": s["label"] or s["path"].rsplit("/", 1)[-1], "state": s["state"]} for s in b["sources"]],
            "latest_verification": ({k: b["latest_verification"].get(k) for k in ("summary", "head", "applies", "applicability")}
                                    if b["latest_verification"] else None),
            "latest_human_report": (b["latest_human_report"] or {}).get("summary"),
            "open_tasks": [{"title": t["title"], "state": t["state"], "due": t["due_local"], "overdue": t["overdue"],
                            "blocked_by": len(t["blocked_by"])} for t in store.list_tasks(pid, open_only=True)],
            "decisions": [{"decision": d["decision"], "rationale": d["rationale"]} for d in store.list_decisions(pid)]}


def _play(store: Store, provider, questions: list[str], base: Path, spy: list[dict] | None = None) -> list[dict]:
    store.save_conversation_state("primary", {"language": "tr"})
    session = MisaSession(store, out=lambda _t: None, provider=provider, state_root=base)
    session.state = {"language": "tr"}
    turns = []
    for q in questions:
        reply, _ = session.handle(q)
        last = store.list_turns()[-1]
        g = session.state.get("last_grounding") or {}
        turns.append({"question": q, "reply": reply, "generated_by": last["generated_by"], "cited": last["citations"],
                      "model_called": bool(spy is not None and spy and True) if spy is not None else None,
                      "grounding": {k: g.get(k) for k in ("verdict", "unsupported_details", "temporal_caveat", "contradiction_caveat", "fallback")},
                      "raw_model_output": (spy.pop(0) if spy else None)})
    return turns


def run_story(base_dir: str | None = None, local: bool = True, model: str | None = None, poc_scenarios=("success", "false_claim")) -> dict:
    base = Path(base_dir or tempfile.mkdtemp(prefix="zenith-story-")).resolve()
    world = acceptance.build_world(base / "world")
    store = Store(world["db"])
    report = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "dir": str(base),
              "data": "fictional (synthetic Harbor/Lighthouse world); no owner data",
              "records": {"Harbor": _records(store, world["harbor"]), "Lighthouse": _records(store, world["lighthouse"])}}
    report["scene_a"] = {"title": "Memory — offline, deterministic", "turns": _play(store, None, SCENE_A, base)}

    scene_b = {"title": "Local reasoning — open-weight model via Ollama", "available": False}
    if local:
        from .misa.ollama import OllamaConversation
        raw: list[dict] = []

        class Spy(OllamaConversation):
            def respond(self, ctx, request):
                try:
                    r = super().respond(ctx, request)
                except Exception as exc:          # recorded honestly: the engine then answers from the records
                    raw.append({"error": f"{type(exc).__name__}: {exc}"[:300],
                                "note": "model call failed; Zenith answered offline (provider_failed)"})
                    raise
                raw.append(json.loads(json.dumps(r)))
                return r

        prov = Spy({"model": model} if model else {})
        pre = prov.preflight()
        scene_b["preflight"] = pre
        if pre["available"]:
            scene_b["available"] = True
            turns = _play(store, prov, SCENE_B, base, spy=raw)
            metrics = list(prov.metrics)
            model_turns = [t for t in turns if t["raw_model_output"] is not None]
            for t, m in zip(model_turns, metrics):     # one metric per model call, including failed ones
                t["metrics"] = m
            scene_b["turns"] = turns
            scene_b["metrics_summary"] = {
                "model": pre["model"], "ollama_version": pre.get("ollama_version"), "calls": len(metrics),
                "wall_s_each": [m["wall_s"] for m in metrics], "prompt_tokens_each": [m["prompt_tokens"] for m in metrics],
                "output_tokens_each": [m["output_tokens"] for m in metrics],
                "tokens_per_s_each": [m["tokens_per_s"] for m in metrics]}
            try:
                with urllib.request.urlopen(prov.base_url + "/api/ps", timeout=3) as r:
                    ps = json.loads(r.read().decode())
                scene_b["runtime"] = [{"model": m.get("name"), "size_bytes": m.get("size"), "size_vram_bytes": m.get("size_vram"),
                                       "context": m.get("context_length"), "quantization": (m.get("details") or {}).get("quantization_level"),
                                       "parameters": (m.get("details") or {}).get("parameter_size"),
                                       "processor": ("GPU" if m.get("size_vram") and m["size_vram"] >= m.get("size", 0) else
                                                     "CPU" if not m.get("size_vram") else "GPU+CPU")} for m in ps.get("models", [])]
            except Exception:
                scene_b["runtime"] = None
        else:
            scene_b["note"] = f"Ollama/model unavailable ({pre['detail']}); the same questions answered offline for honesty."
            scene_b["turns"] = _play(store, None, SCENE_B, base)
    else:
        scene_b["note"] = "skipped (--no-local)"
    report["scene_b"] = scene_b

    pocs = []
    for sc in poc_scenarios:
        out = run_poc(str(base / f"poc-{sc}"), sc)
        pocs.append({"scenario": sc, "steps": out["steps"], "run": out["run"], "artifacts": [a["kind"] for a in out["artifacts"]],
                     "approvals": out["approvals"], "verification": out["verification"], "primary_tree_untouched": out["primary_tree_untouched"],
                     "sandbox_ok": out["sandbox_ok"]})
    report["scene_c"] = {"title": "Orchestration — DETERMINISTIC SIMULATION with fake adapters (no real Claude Code/Codex execution)",
                         "runs": pocs}
    report["environment"] = {"python": platform.python_version(), "platform": platform.platform()}
    return report


def render(report: dict) -> str:
    L: list[str] = []
    w = L.append
    w(f"# Zenith demo transcript\n\nGenerated {report['generated_at']} · {report['data']} · workspace `{report['dir']}`\n")
    w("## The records (what Zenith knows)\n")
    for name, r in report["records"].items():
        w(f"**{name}** — status `{r['status']}` ({r['status_meaning']})")
        w(f"- sources: " + ", ".join(f"{s['name']} ({s['state']})" for s in r["sources"]))
        v = r["latest_verification"]
        w(f"- latest verification: {v['summary']} at `{str(v['head'])[:7]}` — applies now: **{v['applies']}** ({v['applicability']})" if v else "- latest verification: none")
        w(f"- latest owner report: {r['latest_human_report']!r}" if r["latest_human_report"] else "- latest owner report: none")
        for t in r["open_tasks"]:
            w(f"- open task: {t['title']} · {t['state']} · due {t['due']} · overdue={t['overdue']} · blocked_by={t['blocked_by']}")
        for d in r["decisions"]:
            w(f"- decision: {d['decision']} — {d['rationale']}")
        w("")
    for key in ("scene_a", "scene_b"):
        sc = report[key]
        w(f"## {'Scene A' if key == 'scene_a' else 'Scene B'} — {sc['title']}\n")
        if key == "scene_b":
            pre = sc.get("preflight") or {}
            if sc.get("available"):
                ms = sc["metrics_summary"]
                w(f"Model `{ms['model']}` via Ollama {ms['ollama_version']} at `{pre['endpoint']}` (loopback only, nothing leaves the machine). "
                  f"Limits: num_ctx {pre['limits']['num_ctx']}, num_predict {pre['limits']['num_predict']}, timeout {pre['limits']['timeout_s']} s.\n")
                for rt in sc.get("runtime") or []:
                    w(f"Runtime: {rt['parameters']} parameters, quantization {rt['quantization']}, {round((rt['size_bytes'] or 0) / 2**30, 2)} GiB resident, "
                      f"{round((rt['size_vram_bytes'] or 0) / 2**30, 2)} GiB in VRAM → {rt['processor']}, context {rt['context']}.\n")
            else:
                w(f"> {sc.get('note')}\n")
        for t in sc.get("turns", []):
            w(f"**sen>** {t['question']}  ")
            w(f"**misa>** {t['reply']}  ")
            g = t["grounding"]
            w(f"_generated_by: `{t['generated_by']}` · grounding: `{g.get('verdict')}`"
              + (f" · unsupported: {g.get('unsupported_details')}" if g.get("unsupported_details") else "")
              + (" · temporal caveat" if g.get("temporal_caveat") else "") + (" · contradiction caveat" if g.get("contradiction_caveat") else "")
              + (" · **fallback to records**" if g.get("fallback") else "") + f" · cited: {len(t['cited'])} record(s)_  ")
            if t.get("raw_model_output") is not None:
                w("<details><summary>raw model output</summary>\n\n```json\n" + json.dumps(t["raw_model_output"], ensure_ascii=False, indent=1) + "\n```\n</details>  ")
            if t.get("metrics"):
                m = t["metrics"]
                w(f"_latency: {m['wall_s']} s wall ({m['prompt_tokens']} prompt tokens in {m['prompt_eval_s']} s, "
                  f"{m['output_tokens']} output tokens in {m['generate_s']} s = {m['tokens_per_s']} tok/s; load {m['load_s']} s)_  ")
            w("")
    sc = report["scene_c"]
    w(f"## Scene C — {sc['title']}\n")
    for run in sc["runs"]:
        w(f"### scenario `{run['scenario']}` → run `{run['run']['state']}` ({run['run']['exit_reason']}), task `{run['run']['task_state']}`, attempt {run['run']['attempt']}")
        for s in run["steps"]:
            w(f"- {s}" if isinstance(s, str) else f"- {json.dumps(s, ensure_ascii=False)}")
        w(f"- artifacts: {', '.join(run['artifacts'])}")
        w(f"- approvals: {run['approvals']}")
        v = run["verification"]
        if v:
            w(f"- verification report: {json.dumps({k: v.get(k) for k in ('passed', 'exit_code', 'command_id', 'head') if k in v}, ensure_ascii=False)}")
        w(f"- primary tree untouched: {run['primary_tree_untouched']} · sandbox available: {run['sandbox_ok']}\n")
    w(f"_Environment: Python {report['environment']['python']}, {report['environment']['platform']}_")
    return "\n".join(L)
