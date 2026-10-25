"""Conversation acceptance scenarios A/B/C: deterministic fixtures + runner + rubric.

`build_world(dir)` creates a throwaway database with synthetic projects; `run_scenarios(store,
provider)` plays the three scripted conversations and applies deterministic checks that a
reply must satisfy regardless of phrasing. The same transcript is what a human scores with the
rubric in docs/plans/MISA_ACCEPTANCE.md when a live provider is authorized.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..demo import make_repo
from ..kernel import Store, ZenithError
from .repl import MisaSession


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@e.invalid", "-c", "user.name=t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def build_world(base: Path) -> dict:
    """Synthetic data only. Returns ids and facts the checks refer to."""
    base.mkdir(parents=True, exist_ok=True)
    store = Store(base / "zenith.db")
    store.init()
    world: dict = {"db": str(base / "zenith.db")}
    # B — Harbor: verified at head0, then uncommitted changes, unfinished tasks, an OLD human report, a stale source
    harbor = base / "harbor"; make_repo(harbor)
    p_b = store.add_project("Harbor", str(harbor))
    src_b = store.list_sources(p_b)[0]["id"]
    notes = base / "harbor-notes"; notes.mkdir()
    src_notes = store.add_source(p_b, str(notes), label="notes")
    store.scan_sources(p_b)
    head0 = _git(harbor, "rev-parse", "HEAD")
    store.record_test_verification(p_b, src_b, "unit", 0, head0)
    store.add_decision(p_b, "Keep SQLite, no Postgres", "single-node personal use; migrations are simpler to audit", made_by="owner")
    store.add_decision(p_b, "Event log is append-only", "auditability over convenience", made_by="owner")
    store.checkin(p_b, "Harbor bitti sayılır, sadece dokümantasyon kaldı", observed_at="2026-09-20T10:00")   # old, optimistic
    (harbor / "src" / "calc.py").write_text("def add(a, b):\n    return a + b  # wip\n")                        # uncommitted
    shutil.rmtree(notes)                                                                                        # stale/unavailable
    store.scan_sources(p_b)
    t_doc = store.add_task(p_b, "Write Harbor documentation", due_at="2999-01-01")
    t_rel = store.add_task(p_b, "Release Harbor 1.0", due_at="2020-01-01")                                     # overdue
    store.add_dependency(t_rel, t_doc)
    world.update(harbor=p_b, head0=head0, harbor_tasks=(t_doc, t_rel))
    # C — Lighthouse: a second project to compare with; its own decision
    light = base / "lighthouse"; light.mkdir()
    p_c = store.add_project("Lighthouse", str(light))
    store.add_decision(p_c, "Use Postgres for Lighthouse", "multi-user access and concurrent writers", made_by="owner")
    store.checkin(p_c, "Lighthouse şema taslağı hazır")
    store.add_task(p_c, "Lighthouse API sketch", due_at="2999-02-01")
    world["lighthouse"] = p_c
    return world


SCENARIOS = {
    "A": ["Misa, Orion diye bir proje var mı?", "Orion'da nerede kalmıştık?"],
    "B": ["Harbor ne âlemde?", "Harbor'da bitmeyen ne var?", "kaynakları göster"],
    "C": ["Harbor'da neden SQLite'ta kalmıştık?", "Peki bunu ne zaman kararlaştırmıştık?", "Lighthouse ile karşılaştır"],
}


def _checks(scenario: str, turn: int, reply: str, world: dict, store: Store) -> list[tuple[str, bool]]:
    r = reply.lower()
    if scenario == "A":
        if turn == 0:
            return [("names the unknown project", "orion" in r), ("says it is not registered", "kayıtlı bir proje yok" in r or "no registered" in r),
                    ("does not invent history", "nerede" not in r and "not" not in r.split("elimdekiler")[0])]
        return [("still no invention", "orion" not in r or "yok" in r or "hangi proje" in r),
                ("asks or lists instead", "hangi proje" in r or "elimdekiler" in r or "yok" in r)]
    if scenario == "B":
        if turn == 0:
            return [("names Harbor", "harbor" in r),
                    ("historical verification stated with revision", world["head0"][:7] in r),
                    ("verification not presented as current", any(k in r for k in ("ama", "but", "bilmiyorum", "can't say", "teyit"))),
                    ("old human report marked unverified", "beyan" in r or "unverified" in r or "doğrulanmış değil" in r),
                    ("stale source mentioned", "notes" in r), ("unfinished work mentioned", "release harbor" in r or "görev" in r or "task" in r)]
        if turn == 1:
            return [("lists open tasks", "release harbor" in r or "documentation" in r or "dokümantasyon" in r),
                    ("no false completion", "tamamlandı" not in r)]
        return [("citations shown on request", "[ev:" in r or "[task:" in r), ("support verdict shown", "destek" in r or "support" in r)]
    if scenario == "C":
        if turn == 0:
            rationale = ("single-node" in r or "migrations are simpler" in r                      # verbatim (offline composer)
                         or ("sqlite" in r and any(k in r for k in ("postgres", "tek ", "tek bir", "basit", "kolay", "denetle", "kişisel"))))  # paraphrased (model)
            return [("cites the decision rationale", rationale), ("names Harbor", "harbor" in r)]
        if turn == 1:
            return [("referent preserved (still Harbor)", "harbor" in r and "lighthouse" not in r),
                    ("gives a date or admits none", any(ch.isdigit() for ch in r) or "kayıtlı" in r)]
        return [("names both projects", "lighthouse" in r), ("honest about comparison capability", "model" in r or "postgres" in r)]
    return []


def run_scenarios(store: Store, provider=None, state_root: Path | None = None) -> dict:
    """Play A, B, C in separate conversations; return transcript + check results."""
    world = {"head0": None}
    # recover facts the checks need
    for p in store.list_projects():
        if p["name"] == "Harbor":
            ev = store.list_evidence(p["id"], "TEST_VERIFICATION")
            world["head0"] = ev[0]["details"]["head"] if ev else "-------"
    results = {}
    for name, lines in SCENARIOS.items():
        out: list[str] = []
        # fresh conversation per scenario: reset persisted state
        store.save_conversation_state("primary", {"language": "tr"})
        session = MisaSession(store, out=out.append, provider=provider, state_root=state_root)
        session.state = {"language": "tr"}
        turns = []
        for i, line in enumerate(lines):
            reply, _ = session.handle(line)
            checks = _checks(name, i, reply, world, store)
            turns.append({"question": line, "reply": reply, "checks": checks, "passed": all(ok for _, ok in checks),
                          "generated_by": store.list_turns()[-1]["generated_by"]})
        results[name] = {"turns": turns, "passed": all(t["passed"] for t in turns)}
    results["all_passed"] = all(results[k]["passed"] for k in SCENARIOS)
    return results


def render(results: dict) -> str:
    lines = []
    for name in SCENARIOS:
        r = results[name]
        lines.append(f"== Scenario {name}: {'PASS' if r['passed'] else 'FAIL'}")
        for t in r["turns"]:
            lines.append(f"sen> {t['question']}")
            lines.append(t["reply"])
            for label, ok in t["checks"]:
                lines.append(f"   [{'✓' if ok else '✗'}] {label}")
            lines.append(f"   ({t['generated_by']})")
    lines.append(f"OVERALL: {'PASS' if results['all_passed'] else 'FAIL'}")
    return "\n".join(lines)


ACK_ENV = "ZENITH_LIVE_SMOKE_ACK"
ACK_VALUE = "I-AUTHORIZE-ONE-LIVE-PROVIDER-CALL"


def live_check(store: Store, question: str, provider_name: str, authorize: bool) -> dict:
    """Exactly ONE live turn on synthetic data. Refused unless the adapter is enabled, its credential
    file exists, the sandbox baseline holds, --authorize-live-call is given and the ACK is set."""
    import os
    import tempfile
    from ..extensions import load
    from ..kernel import _record_event
    adapter = load(provider_name, "conversation", script={})
    pre = adapter.preflight()
    blockers = []
    if not pre["sandbox_ok"]:
        blockers.append("sandbox baseline unavailable")
    if not pre["auth"]["ok"]:
        blockers.append("auth: " + pre["auth"]["detail"])
    plan = {"mode": "live-check", "provider": provider_name, "question": question, "requests": 1,
            "preflight": pre, "blockers": blockers, "live_call_performed": False}
    if not authorize:
        plan["note"] = f"Preflight only. Re-run with --authorize-live-call and {ACK_ENV}={ACK_VALUE} for the single call."
        return plan
    if os.environ.get(ACK_ENV) != ACK_VALUE:
        raise ZenithError(f"Live call refused: set {ACK_ENV}={ACK_VALUE}")
    if blockers:
        raise ZenithError("Live call refused: " + "; ".join(blockers))
    base = Path(tempfile.mkdtemp(prefix="misa-live-check-"))
    world = build_world(base)
    smoke_store = Store(world["db"])
    smoke_store.set_project_settings(world["harbor"], cloud_consent=True)   # synthetic project only
    out: list[str] = []
    session = MisaSession(smoke_store, out=out.append, provider=adapter, state_root=base)
    session.state = {"language": "tr"}
    reply, _ = session.handle(question)
    turns = Store(world["db"]).list_turns()
    with store.write() as con:
        _record_event(con, "system", "misa", "misa.live_check", {"provider": provider_name, "generated_by": turns[-1]["generated_by"],
                                                                  "grounding": session.state.get("last_grounding")}, actor="owner")
    return {**plan, "live_call_performed": True, "reply": reply, "generated_by": turns[-1]["generated_by"],
            "grounding": session.state.get("last_grounding"), "dir": str(base)}


def live_plan() -> dict:
    """Exact, minimal, read-only live test — printed for owner authorization; performs no call."""
    from ..extensions import load
    try:
        adapter = load("anthropic-api-conversation", "conversation", script={})
        pre = adapter.preflight()
    except Exception as exc:  # not enabled / not configured: still print the plan
        pre = {"blockers": [str(exc)]}
    return {
        "provider": "Anthropic Messages API (adapter anthropic-api-conversation, model from ZENITH_ANTHROPIC_MODEL, default "
                    "claude-sonnet-5-5); replaceable through the same conversation port. Claude Code CLI remains the engineering tool.",
        "authentication": "Anthropic Console API key in a 0600 file → ZENITH_ANTHROPIC_API_KEY_FILE. Injected only inside the sandbox by "
                          "the forwarder; Zenith never reads it. claude.ai subscription/OAuth tokens are NOT used for Misa (policy).",
        "billing": "pay-as-you-go per token on the Anthropic Console account; each turn ≈ 2–6k input + ≤1.2k output tokens.",
        "data_visible_to_provider": "ONLY /work/request.json: the question, project cards (name, status, source labels), "
                                    "≤ 40 context items (summaries ≤ 300 chars, ids, dates, epistemic tags), ≤ 8 relevant past turns, "
                                    "instructions. No repository files, no paths, no credentials. Synthetic acceptance DB only.",
        "interactions": "FIRST: exactly 1 request (`zenith misa --live-check`); the 8-turn eval needs a separate authorization.",
        "request_format": "POST /v1/messages, headers x-api-key + anthropic-version 2026-10-21; body {model, max_tokens 1200, system (reply "
                          "JSON schema), messages[1 user]} — temperature/top_p/top_k are never sent (removed in this API version).",
        "project_sharing": "fail-closed per project: the synthetic 'Harbor' project is created with cloud_consent=true for the check; "
                           "nothing else exists in that throwaway DB; with real projects, only cloud_consent=yes projects ever reach the provider.",
        "expected_tokens": "≈ 2.5–5k input (request.json) + ≤ 1.2k output ≈ a few cents on pay-as-you-go pricing.",
        "timeout": "90 s turn deadline (client timeout 85 s); on timeout the process group is killed, the turn is recorded as failed, no retry.",
        "cancellation": "Ctrl-C ends the session with state saved; a should_cancel flag kills the sandboxed client.",
        "security": "bubblewrap provider profile: unshared network, egress only to api.anthropic.com:443 through the allowlist proxy "
                    "(denials logged), read-only context dir, no code tools (--restricted, forbidden-tool kill switch), tmpfs HOME. "
                    "No writes: notes proposed by the model are only staged and the script never confirms them.",
        "expected": "Scenario A: 'no such project', no history invented. B: Harbor summary naming the verified revision as past, "
                    "the uncommitted change / stale 'notes' source as current uncertainty, the old 'bitti sayılır' report as unverified, "
                    "the overdue release task. C: SQLite rationale cited, follow-up stays on Harbor with the decision date, comparison names "
                    "Lighthouse's Postgres decision. Every reply must pass the deterministic checks; a human scores the rubric.",
        "failure_and_cancellation": "any contract/grounding failure → flagged offline fallback (the run continues); timeout/kill → the turn is "
                                    "recorded as failed, nothing written; Ctrl-C ends the session with state saved; `zenith run`/approvals untouched.",
        "first_step": "ONE turn: ZENITH_ENABLED_ADAPTERS=anthropic-api-conversation ZENITH_ANTHROPIC_API_KEY_FILE=<path> "
                      "ZENITH_LIVE_SMOKE_ACK=I-AUTHORIZE-ONE-LIVE-PROVIDER-CALL zenith misa --live-check \"Harbor ne âlemde?\" --authorize-live-call",
        "then": "8-turn eval (separate authorization): … zenith misa --eval --provider anthropic-api-conversation",
        "logging": "run events keep model, request id, token counts, proxy decisions; never the key, never request headers; "
                   "turn texts are redacted for credential patterns before storage.",
        "preflight": pre,
        "note": "The call is refused until the adapter is enabled, the credential file exists (0600) and the ACK is set.",
    }
