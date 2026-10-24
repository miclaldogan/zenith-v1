"""Multi-turn conversation engine for Misa.

One turn: resolve pending confirmation → (explicit command? handled by the REPL) → assemble a
bounded, query-aware context → ask the configured provider (or the offline composer) →
validate the reply (contract + grounding: cited ids ⊆ context ids) → stage proposed actions for
explicit confirmation → persist the exchange and the conversation state.

State kept across restarts (in `conversations.state`): language, active project (explicit
selection), topic project + previous topic (what we are talking about), pending action,
last cited ids (for "show sources"), a short derived summary. Writes never target a project
that was not confirmed by the owner in this conversation.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..agents.base import AdapterError, RunContext
from ..contracts import ContractError, validate
from ..kernel import Store, ZenithError
from . import context as ctxmod
from .conversation import OfflineComposer
from .intents import parse as parse_command
from .retrieval import Retriever

YES = {"evet", "e", "tamam", "olur", "kaydet", "onayla", "yes", "y", "ok", "okay", "sure", "confirm", "do it"}
NO = {"hayır", "hayir", "h", "yok", "vazgeç", "iptal", "no", "n", "cancel", "nope", "never mind"}
SHOW_SOURCES = ("kaynakları göster", "kaynaklari goster", "kaynak göster", "kanıt göster", "kanıtları göster",
                "show sources", "show citations", "sources?", "cite", "citations")
MAX_PROVIDER_CALLS = 2

_QUOTE = re.compile(r"[“\"]([^”\"]{6,})[”\"]")
_DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
_HASH = re.compile(r"\b([0-9a-f]{7,12})\b")
_PRESENT_VERIFIED = re.compile(r"(?i)(testler (geçiyor|geçti ve|hâlâ geçerli)|doğrulanmış durumda|şu an doğrulanmış|"
                               r"tests (pass|are passing)|currently verified|still (passes|holds|applies)|is verified)")
_PAST_MARKERS = re.compile(r"(?i)(geçmişti|ama|ancak|fakat|artık|bilmiyorum|teyit ede|passed on|but |however|no longer|can't say|"
                           r"unconfirmed|not confirmed|geçerli değil|bayat|stale)")
_DONE_REPORT = re.compile(r"(?i)(bitti|bitirdim|tamamlandı|tamamladım|hazır|done|finished|completed|shipped)")
_UNVERIFIED_MARKERS = re.compile(r"(?i)(doğrulanmış değil|doğrulanmamış|doğrulayamadım|beyan|unverified|not verified|"
                                 r"not been verified|your report|senin notun|kendi ifaden)")


def assess_grounding(reply: dict, request: dict) -> dict:
    """What the validator can and cannot establish about a reply (see docs/plans/MISA_ACCEPTANCE.md §1).

    citations_exist: every cited id is an item (necessary, never sufficient). unsupported_details: quoted
    strings, ISO dates and revision hashes that appear in NO item (proxy for fabricated specifics).
    temporal_caveat: present-tense verification while the verification item no longer applies.
    contradiction_caveat: an owner's 'done' report without an applicable passing verification, and the text
    never marks it unverified. Semantic claim support and unsupported inferences are NOT checked.
    """
    items = request["items"]
    known_ids = {i["id"] for i in items}
    cited = reply.get("cited_ids", [])
    text = reply.get("text", "")
    haystack = " ".join(f"{i.get('text', '')} {i.get('when') or ''} {i.get('head') or ''} {i.get('current_head') or ''} "
                        f"{i.get('due') or ''}" for i in items).lower()
    haystack += " " + " ".join(p["name"] for p in request.get("projects", [])).lower()
    ui_phrases = ("kaynakları göster", "show sources", "ayrıntılı", "more detail", "evet/hayır", "yes/no")
    unsupported = [f"quote:{q[:40]}" for q in _QUOTE.findall(text) if q.lower() not in haystack and q.lower() not in ui_phrases]
    unsupported += [f"date:{d}" for d in _DATE.findall(text) if d not in haystack]
    unsupported += [f"hash:{h}" for h in _HASH.findall(text) if not h.isdigit() and h not in haystack]
    ver = next((i for i in items if i["kind"] == "verification"), None)
    temporal = bool(ver and ver.get("applies") is False and _PRESENT_VERIFIED.search(text) and not _PAST_MARKERS.search(text))
    done_reports = [i for i in items if i["kind"] == "evidence" and i.get("epistemic") == "reported" and _DONE_REPORT.search(i.get("text", ""))]
    currently_verified = bool(ver and ver.get("applies"))
    # only when the reply itself speaks of completion without marking it as the owner's unverified report
    contradiction = bool(done_reports and not currently_verified and _DONE_REPORT.search(text) and not _UNVERIFIED_MARKERS.search(text))
    exist = all(c in known_ids for c in cited)
    verdict = "unsupported" if (not exist) or unsupported else "partial" if (temporal or contradiction) else "supported"
    return {"citations_exist": exist, "cited": len(cited), "unsupported_details": unsupported,
            "temporal_caveat": temporal, "contradiction_caveat": contradiction, "verdict": verdict,
            "not_checked": ["semantic claim support", "unsupported inference", "paraphrased numbers"]}


@dataclass
class TurnResult:
    text: str
    generated_by: str
    cited_ids: list[str] = field(default_factory=list)
    grounded: bool = True
    action_taken: dict | None = None


class ConversationEngine:
    def __init__(self, store: Store, provider=None, state_root: Path | None = None):
        self.store = store
        self.assembler = ctxmod.ContextAssembler(store)
        self.provider = provider              # None → offline composer
        self.offline = OfflineComposer()
        self.state_root = Path(state_root or tempfile.gettempdir())

    # ---- public ----------------------------------------------------------------------------
    def turn(self, text: str, state: dict, now: datetime | None = None) -> TurnResult:
        now = now or datetime.now(timezone.utc)
        lang = ctxmod.detect_language(text, state.get("language", "tr"))
        state["language"] = lang
        low = text.strip().lower()

        pending = state.get("pending_action")
        if pending:
            if low in YES:
                state["pending_action"] = None
                return self._execute(pending, lang, state)
            if low in NO:
                state["pending_action"] = None
                return TurnResult("Tamam, vazgeçtim." if lang == "tr" else "Okay, dropped.", "deterministic")
            # anything else cancels the pending action and is treated as a new message
            state["pending_action"] = None
        elif low in YES or low in NO:
            return TurnResult("Bekleyen bir şey yoktu." if lang == "tr" else "There was nothing pending.", "deterministic")

        if any(low.startswith(k) or low == k for k in SHOW_SOURCES):
            return self._show_sources(lang, state)

        asked = ctxmod.existence_query(text)
        if asked:
            res = self.assembler.resolve_project(asked, {})
            names = ", ".join(p["name"] for p in self.store.list_projects()[:8])
            if res.project is not None and res.how in ("explicit", "fuzzy"):
                self._set_topic(state, res.project["id"])
                return TurnResult(f"Var: {res.project['name']}. Ona geçtik; sor." if lang == "tr" else f"Yes: {res.project['name']}. We're on it; ask away.", "deterministic")
            return TurnResult((f"'{asked}' adında kayıtlı bir proje yok. Elimdekiler: {names}." if lang == "tr"
                               else f"No registered project called '{asked}'. I have: {names}."), "deterministic")

        cmd = parse_command(text, lang)
        if cmd.intent == "checkin":          # `not: …` / `not @Proje: …` typed in conversation → staged, confirmed
            if not cmd.argument:
                return TurnResult("Neyi not alayım?" if lang == "tr" else "What should I note?", "deterministic")
            if cmd.target:
                project, cands = self._resolve_name(cmd.target)
                name = project["name"] if project else cmd.target
            else:
                res = self.assembler.resolve_project(cmd.argument, state)
                name = res.project["name"] if res.project and res.how in ("explicit", "fuzzy", "active", "anaphora:topic") else None
            return self._stage({"type": "note", "project": name, "text": cmd.argument}, lang, state)

        req = self.assembler.build(text, state, now, lang)
        res = req.pop("_resolution")
        if res.how == "ambiguous" and req["question_type"] in ("today", "neglected"):
            res = ctxmod.Resolution()                      # cross-project question that names several projects
            req.pop("topic_project", None)
        if res.how == "ambiguous":
            names = ", ".join(p["name"] for p in res.candidates[:5])
            return TurnResult(f"Hangisini kastettin: {names}?" if lang == "tr" else f"Which one do you mean: {names}?",
                              "deterministic")
        if req["question_type"] in ("note", "task"):
            return self._propose_from_text(text, req, res, lang, state)
        if res.project is None and req["question_type"] not in ("today", "neglected", "general", "note", "task", "idea", "compare"):
            if not req["projects"]:
                return TurnResult("Kayıtlı proje yok; önce `zenith project add` ile bir proje ekleyelim." if lang == "tr"
                                  else "No projects registered yet; add one with `zenith project add` first.", "deterministic")
            names = ", ".join(p["name"] for p in req["projects"][:6])
            return TurnResult(f"Hangi proje? Elimdekiler: {names}." if lang == "tr" else f"Which project? I have: {names}.", "deterministic")
        if req["question_type"] == "general" and res.project is None and self.provider is None:
            return TurnResult(("Bunu tam çözemedim. Bir proje adı söyle, ya da 'nerede kaldık', 'bugün ne yapsam', "
                               "'şu projeye bakalım' gibi sor; `yardım` da var." if lang == "tr" else
                               "I didn't quite get that. Name a project, or ask things like 'where did we leave off', "
                               "'what should I do today', 'let's look at X'; `help` lists the commands."), "deterministic")

        reply, generated_by, grounded = self._ask(req, state)
        if res.project is not None:
            self._set_topic(state, res.project["id"])
        state["last_cited_ids"] = reply["cited_ids"]
        state["last_question_type"] = req["question_type"]
        self._update_summary(state, req, reply)
        text_out = reply["text"].strip()
        if reply.get("follow_up"):
            text_out += ("\n" if text_out else "") + reply["follow_up"].strip()
        if not grounded:
            text_out = ("(Modelin cevabı kayıtlarla eşleşmedi; onun yerine kayıtlardan özetliyorum.) " if lang == "tr"
                        else "(The model's answer did not match the records; summarising from the records instead.) ") + text_out
        # model-proposed actions become confirmations, never executions
        action = next((a for a in reply.get("actions", []) if a.get("type") in ("note", "task")), None)
        if action:
            return self._stage(action, lang, state, prefix=text_out)
        return TurnResult(text_out, generated_by, reply["cited_ids"], grounded)

    # ---- provider call with validation ---------------------------------------------------------
    def _ask(self, req: dict, state: dict) -> tuple[dict, str, bool]:
        request = ctxmod.validated(req)
        known = {i["id"] for i in request["items"]}
        if self.provider is None:
            reply = validate("conversation_reply", self.offline.respond(RunContext("offline", "conversation", 1), request))
            state["last_grounding"] = assess_grounding(reply, request)
            return reply, "deterministic-offline", True
        ctx_dir = None
        if getattr(self.provider.capabilities(), "live", False):
            request, refusal = self.cloud_filter(request, state)
            if refusal is not None:
                # the topic project has no cloud consent: answer locally, say so, never call the provider
                reply = validate("conversation_reply", self.offline.respond(RunContext("offline", "conversation", 1), ctxmod.validated(req)))
                reply["text"] = refusal + " " + reply["text"]
                reply["uncertainty"].append("cloud_sharing_disabled")
                state["last_grounding"] = assess_grounding(reply, ctxmod.validated(req))
                return reply, "deterministic-offline", True
        try:
            ctx_dir = self._context_dir(request)
            ctx = RunContext(run_id=f"conv-{request['generated_at']}", stage="conversation", deadline_seconds=90.0, read_root=ctx_dir)
            raw = self.provider.respond(ctx, request)
            reply = validate("conversation_reply", raw)
        except (AdapterError, ContractError, ZenithError) as exc:
            fallback = validate("conversation_reply", self.offline.respond(RunContext("offline", "conversation", 1), request))
            fallback["uncertainty"].append(f"provider_failed:{getattr(exc, 'kind', 'contract')}")
            return fallback, "deterministic-offline", True
        finally:
            if ctx_dir is not None:
                shutil.rmtree(ctx_dir, ignore_errors=True)
        cited = [c for c in reply["cited_ids"]]
        nothing = any(u in ("nothing_pending", "no_records", "no_model", "no_decision_recorded") for u in reply["uncertainty"])
        assessment = assess_grounding(reply, request)
        grounded = assessment["verdict"] != "unsupported" and (bool(cited) or not request["items"] or reply["needs_clarification"] or nothing
                                                               or request["question_type"] in ("idea", "compare", "general", "today", "neglected"))
        state["last_grounding"] = assessment
        if not grounded:
            fallback = validate("conversation_reply", self.offline.respond(RunContext("offline", "conversation", 1), request))
            state["last_grounding"] = {**assessment, "fallback": True}
            return fallback, "deterministic-offline", False
        lang = request["language"]
        if assessment["temporal_caveat"]:
            reply["text"] += (" (Not: bu doğrulama o günkü sürüm için geçerliydi; şimdiki durum teyit edilmedi.)" if lang == "tr"
                              else " (Note: that verification applied to the revision of the day; the current state is unconfirmed.)")
        if assessment["contradiction_caveat"]:
            reply["text"] += (" (Not: 'bitti' bilgisi senin beyanın; doğrulanmış bir test kaydı yok.)" if lang == "tr"
                              else " (Note: the 'done' part is your own report; there is no applicable passing verification.)")
        for a in reply["actions"]:  # only whitelisted action types survive
            if a.get("type") not in request["allowed_actions"]:
                reply["actions"] = [x for x in reply["actions"] if x is not a]
        return reply, f"model:{self.provider.name}", True

    # ---- cloud boundary ----------------------------------------------------------------------
    def cloud_filter(self, request: dict, state: dict) -> tuple[dict, str | None]:
        """Fail-closed, project-scoped sharing for live providers.

        Nothing about a project reaches an external provider unless that project's `cloud_consent`
        is set: its card, tasks, decisions, evidence, notes, observations, documents, conversation
        turns that mention it, cross-project comparisons and the derived summary. If the topic (or
        comparison) project is not consented the provider is not called at all and a local answer is
        given instead. Non-consented project names are redacted from the owner's question.
        """
        lang = request["language"]
        all_projects = self.store.list_projects()
        consent = {p["id"]: self.store.project_settings(p["id"])["cloud_consent"] for p in all_projects}
        name_to_id = {p["name"]: p["id"] for p in all_projects}
        blocked_names = sorted((n for n, i in name_to_id.items() if not consent.get(i)), key=len, reverse=True)

        def allowed(name: str | None) -> bool:
            return bool(name) and consent.get(name_to_id.get(name, ""), False)

        topic = request.get("topic_project")
        if topic and not consent.get(topic["id"]):
            return request, (f"({topic['name']} için bulut paylaşımı kapalı; bu cevabı modele sormadan, kayıtlardan verdim.)"
                             if lang == "tr" else
                             f"({topic['name']} has no cloud sharing consent; answered locally from records, no model involved.)")
        comp = request.get("compare_project")
        if comp and not consent.get(comp["id"]):
            return request, (f"({comp['name']} için bulut paylaşımı kapalı; karşılaştırmayı modele sormadım.)" if lang == "tr"
                             else f"({comp['name']} has no cloud sharing consent; the comparison was not sent to a model.)")
        out = dict(request)
        out["projects"] = [c for c in request["projects"] if consent.get(c["id"])]
        kept = [i for i in request["items"] if allowed(i.get("project")) and not (i.get("local_only") and not allowed(i.get("project")))]
        withheld = len(request["items"]) - len(kept)
        out["items"] = kept
        if "active_project" in request and not consent.get(request["active_project"]["id"]):
            out.pop("active_project")
        # conversation history is sensitive: only turns that name the consented topic and no other project
        turns = []
        for t in request.get("recent_turns", []):
            text = t["text"].lower()
            if topic and topic["name"].lower() in text and not any(n.lower() in text for n in blocked_names if n != topic["name"]):
                turns.append(t)
        out["recent_turns"] = turns
        if "summary" in out and not all(consent.values()):
            out.pop("summary")
        if "focus_ids" in out:
            keep_ids = {i["id"] for i in kept}
            out["focus_ids"] = [f for f in out["focus_ids"] if f in keep_ids]
        q = request["question"]
        for n in blocked_names:
            q = re.sub(re.escape(n), "[proje]" if lang == "tr" else "[project]", q, flags=re.I)
        out["question"] = q
        if withheld or len(out["projects"]) < len(request["projects"]):
            out["instructions"] = request["instructions"] + [
                f"{withheld} item(s) and {len(request['projects']) - len(out['projects'])} project(s) were withheld: no cloud consent."]
            state["last_withheld"] = withheld
        return out, None

    def _context_dir(self, request: dict) -> Path:
        root = Path(tempfile.mkdtemp(prefix="misa-ctx-", dir=str(self.state_root) if self.state_root.exists() else None))
        (root / "request.json").write_text(json.dumps(request, ensure_ascii=False, indent=1))
        return root

    # ---- actions -------------------------------------------------------------------------------
    def _propose_from_text(self, text: str, req: dict, res, lang: str, state: dict) -> TurnResult:
        body = re.sub(r"(?i)^(misa[,:]?\s*)?(bunu|şunu|this|that)?\s*(not al|not et|kaydet|note this|take a note|write down|remember this|"
                      r"görev ekle|görev oluştur|yeni görev|add a task|create a task|new task)[:,]?\s*", "", text.strip()).strip(" .")
        if not body:
            return TurnResult("Neyi not alayım?" if lang == "tr" else "What should I note?", "deterministic")
        action = {"type": "task" if req["question_type"] == "task" else "note",
                  "project": res.project["name"] if res.project else None, "text": body, "resolved_by": res.how}
        return self._stage(action, lang, state)

    def _stage(self, action: dict, lang: str, state: dict, prefix: str = "") -> TurnResult:
        project = None
        if action.get("project"):
            project, cands = self._resolve_name(action["project"])
            if project is None:
                names = ", ".join(c["name"] for c in cands[:5]) or action["project"]
                return TurnResult((prefix + "\n" if prefix else "") + (f"Hangi projeye? Eşleşen: {names}. `not @Proje: …` diye yaz." if lang == "tr"
                                  else f"Which project? Matches: {names}. Write `note @Project: …`."), "deterministic")
        if project is None:
            return TurnResult((prefix + "\n" if prefix else "") + ("Bunu hangi projeye kaydedeyim? Proje adını söyle." if lang == "tr"
                              else "Which project should I file this under? Name it."), "deterministic")
        # a note that points at another project — by (partial/fuzzy) name or by one of its open task titles —
        # is refused, never redirected
        others = Retriever(self.store).projects_mentioned(action["text"], exclude=project["id"])
        res = self.assembler.resolve_project(action["text"], {})
        if res.project is not None and res.how in ("explicit", "fuzzy") and res.project["id"] != project["id"]:
            others = [{**res.project, "via": "name"}] + others
        if others:
            o = others[0]
            via = ("" if o.get("via", "name") == "name" else (f" ({o['via']} görevine benziyor)" if lang == "tr" else f" (looks like its {o['via']})"))
            return TurnResult((f"Not {o['name']}'dan bahsediyor gibi{via} ama konu {project['name']}. Yanlış yere yazmayayım: "
                               f"`not @{o['name']}: …` ya da `not @{project['name']}: …` de." if lang == "tr"
                               else f"The note seems to be about {o['name']}{via}, but we're on {project['name']}. Rather than guess: "
                                    f"`note @{o['name']}: …` or `note @{project['name']}: …`."), "deterministic")
        if len(action["text"]) > 1000:
            raise ZenithError("Check-in note must be 1–1000 characters")
        action = {**action, "project_id": project["id"], "project": project["name"]}
        state["pending_action"] = action
        kind = ("not" if action["type"] == "note" else "görev") if lang == "tr" else action["type"]
        current = state.get("topic_project") or state.get("project_id")
        switch = ""
        if current and current != project["id"]:
            cur = self.assembler._safe_project(current)
            if cur:
                switch = (f"Konu {cur['name']} ama not {project['name']}'ı işaret ediyor. " if lang == "tr"
                          else f"We're on {cur['name']} but the note points at {project['name']}. ")
        ask = switch + (f"{project['name']} için {kind} olarak kaydedeyim mi: “{action['text']}” (evet/hayır)" if lang == "tr"
                        else f"Record as a {kind} for {project['name']}: “{action['text']}” (yes/no)")
        return TurnResult((prefix + "\n" if prefix else "") + ask, "deterministic")

    def _execute(self, action: dict, lang: str, state: dict) -> TurnResult:
        pid = action["project_id"]
        name = action["project"]
        if action["type"] == "note":
            out = self.store.checkin(pid, action["text"])
            later = " Dikkat: bundan sonra tarihli depo/test kaydı var; not şimdiden eski olabilir." if (out["later_evidence"] and lang == "tr") else \
                    (" Heads-up: there is later repository/test evidence; the note may already be stale." if out["later_evidence"] else "")
            text = (f"{name} için not düştüm — beyan olarak, doğrulanmış saymıyorum.{later}" if lang == "tr"
                    else f"Noted for {name} — as a report, not counted as verified.{later}")
            state["last_cited_ids"] = [out["evidence_id"]]
            return TurnResult(text, "deterministic", [out["evidence_id"]], True, {"type": "note", "evidence_id": out["evidence_id"], "project_id": pid})
        task_id = self.store.add_task(pid, action["text"][:200])
        state["last_cited_ids"] = [task_id]
        text = (f"{name} için görev açtım: “{action['text'][:200]}” (taslak; son tarih yok)." if lang == "tr"
                else f"Task created for {name}: “{action['text'][:200]}” (draft; no due date).")
        return TurnResult(text, "deterministic", [task_id], True, {"type": "task", "task_id": task_id, "project_id": pid})

    def _resolve_name(self, name: str) -> tuple[dict | None, list[dict]]:
        res = self.assembler.resolve_project(name, {})
        return res.project, res.candidates

    # ---- continuity ------------------------------------------------------------------------------
    def _set_topic(self, state: dict, pid: str) -> None:
        if state.get("topic_project") != pid:
            state["topic_previous"] = state.get("topic_project")
            state["topic_project"] = pid

    def _update_summary(self, state: dict, req: dict, reply: dict) -> None:
        """Bounded derived summary (recent question types and topics). Context, not fact."""
        topic = (req.get("topic_project") or {}).get("name")
        entry = f"{req['question_type']}@{topic or '-'}"
        hist = (state.get("summary") or "").split(" | ")
        hist = [h for h in hist if h][-7:] + [entry]
        state["summary"] = " | ".join(hist)

    def _show_sources(self, lang: str, state: dict) -> TurnResult:
        ids = state.get("last_cited_ids") or []
        if not ids:
            return TurnResult("Son cevabın dayandığı kayıt yok (ya da henüz bir şey sormadın)." if lang == "tr"
                              else "The last answer rested on no records (or you haven't asked anything yet).", "deterministic")
        lines = []
        with self.store.connection() as con:
            for i in ids:
                row = con.execute("SELECT origin, confidence, summary, observed_at FROM evidence WHERE id=?", (i,)).fetchone()
                if row:
                    lines.append(f"  [ev:{i[:8]}] {row['origin']} / {row['confidence']} · {row['observed_at'][:16].replace('T', ' ')} · {row['summary'][:120]}")
                    continue
                row = con.execute("SELECT title, state, due_at FROM tasks WHERE id=?", (i,)).fetchone()
                if row:
                    lines.append(f"  [task:{i[:8]}] {row['title']} · {row['state']}" + (f" · due {row['due_at'][:10]}" if row["due_at"] else ""))
                    continue
                row = con.execute("SELECT decision, made_by, created_at FROM decisions WHERE id=?", (i,)).fetchone()
                if row:
                    lines.append(f"  [dec:{i[:8]}] {row['decision'][:100]} · {row['made_by']} · {row['created_at'][:10]}")
                    continue
                row = con.execute("SELECT state, exit_reason, created_at FROM execution_runs WHERE id=?", (i,)).fetchone()
                if row:
                    lines.append(f"  [run:{i[:8]}] {row['state']} {row['exit_reason'] or ''} · {row['created_at'][:10]}")
                    continue
                row = con.execute("SELECT path, label FROM project_sources WHERE id=?", (i,)).fetchone()
                if row:
                    lines.append(f"  [src:{i[:8]}] {row['label'] or row['path']}")
                    continue
                if i.startswith(("commit:", "doc:")):
                    lines.append(f"  [{i}] " + ("depo metni (güvenilmez veri)" if lang == "tr" else "repository text (untrusted data)"))
        head = "Son cevabın dayandığı kayıtlar:" if lang == "tr" else "Records behind the last answer:"
        g = state.get("last_grounding") or {}
        if g:
            detail = ("alıntı/tarih/sürüm kontrolü geçti" if not g.get("unsupported_details") else f"desteksiz ayrıntı: {g['unsupported_details']}") \
                if lang == "tr" else ("quote/date/revision check passed" if not g.get("unsupported_details") else f"unsupported details: {g['unsupported_details']}")
            lines.append((f"  destek: {g.get('verdict')} · {detail} · anlamsal destek ve çıkarımlar denetlenmez" if lang == "tr"
                          else f"  support: {g.get('verdict')} · {detail} · semantic support and inferences are not checked"))
        return TurnResult(head + "\n" + "\n".join(lines), "deterministic", ids)
