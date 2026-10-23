"""Deterministic Turkish/English rendering with provenance for Misa.

Tone: concise, observant, informal when the stakes are low, plain when they are not. Every
claim about state carries a short citation; unverified human reports are labelled as such.
Nothing here is model-generated; the session banner says so.
"""
from __future__ import annotations

import os
import re

from .retrieval import Answer, Fact

_CONTROL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def clean(text: str) -> str:
    """Strip ANSI escapes and control characters from anything shown on the terminal."""
    return _CONTROL.sub("", text)


def src_name(s: dict) -> str:
    """Concise source label: the owner's label, else the directory's basename (full path on request)."""
    return s.get("label") or os.path.basename(s["path"].rstrip("/")) or s["path"]


def shorten_paths(text: str, sources: list[dict]) -> str:
    for s in sorted(sources, key=lambda x: -len(x["path"])):
        text = text.replace(s["path"], src_name(s))
    return text


APPLIES = {
    "tr": {"current": "kaynak güncel, doğrulama hâlâ geçerli", "head_moved": "HEAD değişti, yeniden doğrulanmalı",
           "uncommitted_changes": "commit edilmemiş değişiklik var, yeniden doğrulanmalı",
           "scan_stale": "kaynak uzun süredir taranmadı, geçerliliği teyit edilemiyor",
           "source_unavailable": "doğrulanan kaynak şu an ulaşılamıyor; geçerliliği teyit edilemiyor",
           "source_unknown": "doğrulanan kaynağın güncel hâli bilinmiyor", "source_removed": "doğrulanan kaynak projeden çıkarılmış",
           "failed": "son doğrulama başarısız"},
    "en": {"current": "source fresh, verification still applies", "head_moved": "HEAD moved, needs re-verification",
           "uncommitted_changes": "uncommitted changes, needs re-verification",
           "scan_stale": "source not scanned recently; applicability unconfirmed",
           "source_unavailable": "the verified source is currently unavailable; applicability unconfirmed",
           "source_unknown": "current state of the verified source unknown", "source_removed": "the verified source was detached",
           "failed": "latest verification failed"},
}


def verification_line(lang: str, v: dict | None) -> str | None:
    if not v:
        return None
    head = (v.get("head") or "")[:7]
    when = (v.get("observed_at") or "")[:16].replace("T", " ")
    what = v.get("command_id") or "test"
    reason = APPLIES[lang].get(v.get("applicability") or "", v.get("applicability") or "")
    tag = ("doğrulama" if lang == "tr" else "verification")
    return f"  {tag}: {what} @{head} ({when}) [ev:{v['evidence_id'][:8]}] — {reason}"

CONF = {
    "tr": {"REPORTED": "beyan, doğrulanmamış", "OBSERVED": "gözlem", "VERIFIED": "doğrulanmış", "INFERRED": "model çıkarımı", "UNKNOWN": "bilinmiyor"},
    "en": {"REPORTED": "human report, unverified", "OBSERVED": "observed", "VERIFIED": "verified", "INFERRED": "model inference", "UNKNOWN": "unknown"},
}
STATUS = {
    "tr": {"UNKNOWN": "bilinmiyor", "REPORTED_ONLY": "yalnızca beyan var", "OBSERVED": "hareket gözlendi, doğrulanmadı",
           "VERIFIED": "doğrulanmış", "VERIFICATION_STALE": "doğrulama bayatladı", "VERIFICATION_FAILING": "doğrulama başarısız"},
    "en": {"UNKNOWN": "unknown", "REPORTED_ONLY": "reported only", "OBSERVED": "activity observed, not verified",
           "VERIFIED": "verified", "VERIFICATION_STALE": "verification stale", "VERIFICATION_FAILING": "verification failing"},
}
SRC_STATE = {
    "tr": {"fresh": "güncel", "stale": "bayat", "unavailable": "ulaşılamıyor", "never_scanned": "hiç taranmadı", "corrupt_record": "kaydı bozuk"},
    "en": {"fresh": "fresh", "stale": "stale", "unavailable": "unavailable", "never_scanned": "never scanned", "corrupt_record": "corrupt record"},
}
UNKNOWN_TEXT = {
    "tr": {"no_projects": "Kayıtlı proje yok. Boş bir defterle başlıyoruz; `zenith project add --name … --path …` ilk sayfayı açar.",
           "no_evidence": "Bu proje için hiç kanıt kaydı yok — ne tarama ne de senden bir not. Elimde uyduracak bir şey de yok.",
           "no_decisions": "Kayıtlı karar yok.", "no_open_tasks": "Açık görev yok.", "no_sources": "Projeye bağlı dizin yok.",
           "no_runs": "Hiç çalıştırma yapılmamış.", "nothing_pending": "Bekleyen onay, bloke veya uzlaştırma gerektiren bir şey yok.",
           "no_actions": "Önerecek bir sonraki adım çıkmadı."},
    "en": {"no_projects": "No projects registered yet. `zenith project add --name … --path …` opens the first page.",
           "no_evidence": "No evidence recorded for this project — no scan, no note from you. Nothing I could invent either.",
           "no_decisions": "No recorded decisions.", "no_open_tasks": "No open tasks.", "no_sources": "No directories attached.",
           "no_runs": "No runs yet.", "nothing_pending": "Nothing awaiting approval, blocked or needing reconciliation.",
           "no_actions": "No next action to suggest."},
}
HELP = {
    "tr": ("Yapabildiklerim (şimdilik deterministik, kayıtlı kanıtlardan):\n"
           "  projeler · proje <ad|no>  — listele / seç\n"
           "  durum · nerede kaldım · kanıt · kararlar · görevler · kaynaklar · geçmiş · onaylar · sırada ne\n"
           "  not: <metin> · not @Proje: <metin>  — check-in kaydet (beyan olarak, doğrulanmış sayılmaz)\n"
           "  kaynaklar detay  — tam yollar\n"
           "  yardım · çık\n"
           "İngilizce yazarsan İngilizce cevap veririm; iç işlemler her zaman İngilizce."),
    "en": ("What I can do (deterministic for now, from recorded evidence):\n"
           "  projects · project <name|no>  — list / select\n"
           "  status · where did I leave · evidence · decisions · tasks · sources · history · approvals · next\n"
           "  note: <text> · note @Project: <text>  — record a check-in (as a report, never counted as verified)\n"
           "  sources detail  — full paths\n"
           "  help · exit\n"
           "Write in Turkish and I answer in Turkish; internal operations are always English."),
}


def _cite(f: Fact) -> str:
    return f" [{f.cite}]" if f.cite else ""


def _conf(lang: str, c: str) -> str:
    return CONF[lang].get(c, c.lower()) if c else ""


def banner(lang: str, name: str, resumed: bool, project_name: str | None, model: str | None = None) -> str:
    if lang == "tr":
        head = (f"{name} · sohbet modeli: {model} (cevaplar kayıtlardan, model yorumlar)." if model
                else f"{name} · çevrimdışı mod (model yok; cevaplar kayıtlı kanıtlardan, deterministik).")
        tail = f" Aktif proje: {project_name} (son seçtiğin)." if resumed and project_name else ""
        return head + tail
    head = (f"{name} · conversation model: {model} (answers grounded in records, phrased by the model)." if model
            else f"{name} · offline mode (no model; deterministic answers from recorded evidence).")
    tail = f" Active project: {project_name} (your last selection)." if resumed and project_name else ""
    return head + tail


def opening(lang: str, ans: Answer) -> str:
    d = ans.data
    if d["project_count"] == 0:
        return UNKNOWN_TEXT[lang]["no_projects"]
    lines = []
    recent = d.get("most_recent")
    if lang == "tr":
        lines.append(f"{d['project_count']} proje kayıtlı.")
        if d.get("active"):
            lines.append(f"Aktif proje: {d['active']} (son seçtiğin).")
        if recent and recent["name"] != d.get("active"):
            lines.append(f"En son hareket: {recent['name']} ({recent['at']}) — seçmedikçe ona geçmem.")
        if d["overdue"]:
            lines.append("Geciken: " + "; ".join(f"{p} — {t['title']} ({t['due_local'][:10]})" for p, t in d["overdue"][:5]))
        if d["due_soon"]:
            lines.append("Bu hafta: " + "; ".join(f"{p} — {t['title']} ({t['due_local'][:10]})" for p, t in d["due_soon"][:5]))
        if d["reconcile"]:
            lines.append(f"Uzlaştırma bekleyen {len(d['reconcile'])} görev var — önce ona bakmak lazım.")
        if d["awaiting"]:
            lines.append(f"Onayını bekleyen {len(d['awaiting'])} görev var.")
        if d["blocked"]:
            lines.append("Bloke: " + "; ".join(f"{p} — {t['title']}" for p, t in d["blocked"][:5]))
        if d["stale"]:
            lines.append("Taze olmayan kaynak: " + "; ".join(f"{p}/{src_name(s)} ({SRC_STATE['tr'][s['state']]})" for p, s in d["stale"][:5]))
        if len(lines) == 1:
            lines.append("Geciken, bloke veya bayat bir şey yok. Sakin bir gün — ya da henüz kimse bakmadı.")
        lines.append("Bir proje seç (ad veya numara) ya da `yardım` yaz.")
    else:
        lines.append(f"{d['project_count']} projects registered.")
        if d.get("active"):
            lines.append(f"Active project: {d['active']} (your last selection).")
        if recent and recent["name"] != d.get("active"):
            lines.append(f"Most recent activity: {recent['name']} ({recent['at']}) — I won't switch unless you select it.")
        if d["overdue"]:
            lines.append("Overdue: " + "; ".join(f"{p} — {t['title']} ({t['due_local'][:10]})" for p, t in d["overdue"][:5]))
        if d["due_soon"]:
            lines.append("This week: " + "; ".join(f"{p} — {t['title']} ({t['due_local'][:10]})" for p, t in d["due_soon"][:5]))
        if d["reconcile"]:
            lines.append(f"{len(d['reconcile'])} task(s) need reconciliation — that comes first.")
        if d["awaiting"]:
            lines.append(f"{len(d['awaiting'])} task(s) await your approval.")
        if d["blocked"]:
            lines.append("Blocked: " + "; ".join(f"{p} — {t['title']}" for p, t in d["blocked"][:5]))
        if d["stale"]:
            lines.append("Not fresh: " + "; ".join(f"{p}/{src_name(s)} ({SRC_STATE['en'][s['state']]})" for p, s in d["stale"][:5]))
        if len(lines) == 1:
            lines.append("Nothing overdue, blocked or stale. A quiet day — or nobody has looked yet.")
        lines.append("Pick a project (name or number) or type `help`.")
    return "\n".join(lines)


def projects(lang: str, ans: Answer) -> str:
    if ans.unknowns:
        return UNKNOWN_TEXT[lang][ans.unknowns[0]]
    rows = ans.data["rows"]
    out = []
    for r in rows:
        st = STATUS[lang].get(r["status"], r["status"])
        if lang == "tr":
            extra = f", {r['overdue']} geciken" if r["overdue"] else ""
            out.append(f"  {r['n']}. {r['name']} — {st}; {r['sources']} kaynak, {r['open_tasks']} açık görev{extra}")
        else:
            extra = f", {r['overdue']} overdue" if r["overdue"] else ""
            out.append(f"  {r['n']}. {r['name']} — {st}; {r['sources']} source(s), {r['open_tasks']} open task(s){extra}")
    head = "Projeler:" if lang == "tr" else "Projects:"
    return head + "\n" + "\n".join(out)


def selected(lang: str, project: dict) -> str:
    return (f"Tamam, {project['name']} üzerindeyiz." if lang == "tr" else f"Okay, we're on {project['name']}.")


def no_project(lang: str) -> str:
    return ("Önce bir proje seçmeliyiz: `projeler` yaz, sonra adını ya da numarasını söyle."
            if lang == "tr" else "Pick a project first: type `projects`, then its name or number.")


def ambiguous(lang: str, ref: str, candidates: list[dict] | None = None) -> str:
    names = ", ".join(c["name"] for c in (candidates or [])[:5])
    if candidates:
        return (f"'{ref}' birden fazla projeyle eşleşiyor: {names}. Tam adını yaz." if lang == "tr"
                else f"'{ref}' matches more than one project: {names}. Use the full name.")
    return (f"'{ref}' hiçbir projeyle eşleşmedi. `projeler` ile listeye bak." if lang == "tr"
            else f"'{ref}' matched no project. Check the list with `projects`.")


def need_listing(lang: str) -> str:
    return ("Numaralar listeye göre; önce `projeler` yaz, sonra numarayı söyle." if lang == "tr"
            else "Numbers follow the listing; type `projects` first, then the number.")


def bad_number(lang: str, n: int) -> str:
    return (f"Listede {n} proje var; 1–{n} arası bir numara seç." if lang == "tr"
            else f"The listing has {n} projects; pick 1–{n}.")


def checkin_needs_text(lang: str) -> str:
    return "Notu da yaz: `not: <metin>`" if lang == "tr" else "Add the note: `note: <text>`"


def checkin_needs_project(lang: str) -> str:
    return ("Notu hangi projeye düşeceğim belli değil. Önce bir proje seç ya da `not @Proje: <metin>` yaz." if lang == "tr"
            else "I don't know which project this note belongs to. Select one first, or write `note @Project: <text>`.")


def checkin_mentions_other(lang: str, active: dict, mentioned: list[dict]) -> str:
    other = mentioned[0]["name"]
    via = mentioned[0].get("via", "name")
    how = ("" if via == "name" else (f" ({via} görevine benziyor)" if lang == "tr" else f" (looks like its {via})"))
    return (f"Not {other}'dan bahsediyor gibi{how} ama aktif proje {active['name']}. Yanlış yere yazmayayım: "
            f"`not @{other}: …` ya da `not @{active['name']}: …` diye açıkça söyle." if lang == "tr"
            else f"The note seems to be about {other}{how}, but the active project is {active['name']}. Rather than guess: "
                 f"write `note @{other}: …` or `note @{active['name']}: …` explicitly.")


def error(lang: str, detail: str) -> str:
    return (f"Olmadı: {detail}" if lang == "tr" else f"That failed: {detail}")


def status(lang: str, ans: Answer) -> str:
    b = ans.data["brief"]
    label = STATUS[lang].get(b["status"]["label"], b["status"]["label"])
    lines = [f"{ans.project['name']}: {label}" + (f" [{', '.join('ev:' + x[:8] for x in b['status']['basis'])}]" if b["status"]["basis"] else "")]
    for c in b["status"]["caveats"]:
        lines.append(("  dikkat: " if lang == "tr" else "  caveat: ") + c)
    vline = verification_line(lang, b["latest_verification"])
    if vline:
        lines.append(vline)
    for s in b["sources"]:
        name = src_name(s)
        head = f" HEAD {s['head'][:7]}" if s["head"] else ""
        seen = (f", son görülme {s['last_seen_at'][:16].replace('T', ' ')}" if s["last_seen_at"] else "") if lang == "tr" else \
               (f", seen {s['last_seen_at'][:16].replace('T', ' ')}" if s["last_seen_at"] else "")
        lines.append(f"  {('kaynak' if lang == 'tr' else 'source')} {name}: {SRC_STATE[lang][s['state']]}{head}{seen}")
    r = b["latest_human_report"]
    if r:
        lab = "son beyanın" if lang == "tr" else "your last report"
        stale = (" (eski)" if lang == "tr" else " (stale)") if r["stale"] else ""
        lines.append(f"  {lab}{stale} [{'ev:' + r['evidence_id'][:8]}]: {r['note']} — {_conf(lang, 'REPORTED')}")
    t = b["tasks"]
    lines.append((f"  görevler: {t['open']} açık, {len(t['overdue'])} geciken, {len(t['due_soon'])} yakında, {len(t['blocked'])} bloke")
                 if lang == "tr" else
                 f"  tasks: {t['open']} open, {len(t['overdue'])} overdue, {len(t['due_soon'])} due soon, {len(t['blocked'])} blocked")
    for u in b["unknowns"][:3]:
        if u.startswith("Source '"):
            continue  # already shown per source above, without the full path
        lines.append(("  bilinmeyen: " if lang == "tr" else "  unknown: ") + u)
    return "\n".join(lines)


def last(lang: str, ans: Answer) -> str:
    name = ans.project["name"]
    if "no_evidence" in ans.unknowns and not ans.facts:
        return f"{name}: " + UNKNOWN_TEXT[lang]["no_evidence"]
    lines = [(f"{name} — en son kayıtlar:" if lang == "tr" else f"{name} — latest records:")]
    labels_tr = {"REPOSITORY_OBSERVATION": "depo gözlemi", "HUMAN_REPORT": "senin notun", "TEST_VERIFICATION": "test doğrulaması",
                 "DOCUMENT": "belge", "MODEL_INFERENCE": "model çıkarımı", "decision": "karar", "run": "çalıştırma"}
    labels_en = {"REPOSITORY_OBSERVATION": "repository observation", "HUMAN_REPORT": "your note", "TEST_VERIFICATION": "test verification",
                 "DOCUMENT": "document", "MODEL_INFERENCE": "model inference", "decision": "decision", "run": "run"}
    labels = labels_tr if lang == "tr" else labels_en
    for f in ans.facts:
        when = f" ({f.when})" if f.when else ""
        lines.append(f"  {labels.get(f.key, f.key)}{when}{_cite(f)}: {f.value} — {_conf(lang, f.confidence)}")
    st = STATUS[lang].get(ans.data["status"], ans.data["status"])
    lines.append((f"  genel durum: {st}" if lang == "tr" else f"  overall: {st}"))
    vline = verification_line(lang, ans.data.get("verification"))
    if vline:
        lines.append(vline)
    if ans.data["stale_sources"]:
        names = ", ".join(src_name(s) + f" ({SRC_STATE[lang][s['state']]})" for s in ans.data["stale_sources"])
        lines.append((f"  not: şu kaynaklar taze değil — {names}. Gördüğüm, son taramadaki hâl." if lang == "tr"
                      else f"  note: these sources are not fresh — {names}. What I see is the state at the last scan."))
    return "\n".join(lines)


def facts_list(lang: str, ans: Answer, title_tr: str, title_en: str) -> str:
    if ans.unknowns and not ans.facts:
        return UNKNOWN_TEXT[lang].get(ans.unknowns[0], ans.unknowns[0])
    lines = [f"{ans.project['name']} — {title_tr if lang == 'tr' else title_en}:"]
    for f in ans.facts:
        when = f" {f.when}" if f.when else ""
        lines.append(f"  {f.key}{when}{_cite(f)}: {f.value} — {_conf(lang, f.confidence)}")
    return "\n".join(lines)


def tasks(lang: str, ans: Answer) -> str:
    if ans.unknowns:
        return UNKNOWN_TEXT[lang]["no_open_tasks"]
    g = ans.data["groups"]
    scope = ans.project["name"] if ans.project else ("tüm projeler" if lang == "tr" else "all projects")
    lines = [f"{scope} — {ans.data['total']} " + ("açık görev:" if lang == "tr" else "open task(s):")]
    names = {"tr": {"overdue": "GECİKEN", "due_soon": "bu hafta", "blocked": "bloke", "awaiting_approval": "onay bekliyor", "other": "diğer"},
             "en": {"overdue": "OVERDUE", "due_soon": "this week", "blocked": "blocked", "awaiting_approval": "awaiting approval", "other": "other"}}[lang]
    for key in ("overdue", "due_soon", "awaiting_approval", "blocked", "other"):
        for t in g[key][:8]:
            due = f" · {t['due_local']}" if t["due_local"] else ""
            dep = (f" · bağımlı: {len(t['blocked_by'])}" if lang == "tr" else f" · depends on {len(t['blocked_by'])}") if t["blocked_by"] else ""
            lines.append(f"  [{names[key]}] {t['title']} ({t['state']}){due}{dep} [task:{t['id'][:8]}]")
    return "\n".join(lines)


def sources(lang: str, ans: Answer, detail: bool = False) -> str:
    if ans.unknowns:
        return UNKNOWN_TEXT[lang]["no_sources"]
    lines = [f"{ans.project['name']} — " + ("kaynaklar:" if lang == "tr" else "sources:")]
    for s in ans.data["sources"]:
        name = (src_name(s) + f" → {s['path']}") if detail else src_name(s)
        seen = s["last_seen_at"][:16].replace("T", " ") if s["last_seen_at"] else ("hiç" if lang == "tr" else "never")
        head = f" HEAD {s['head'][:7]}" if s["head"] else ""
        lines.append(f"  {name}: {SRC_STATE[lang][s['state']]}{head} · " + ("son görülme " if lang == "tr" else "last seen ") + seen)
    if any(s["state"] != "fresh" for s in ans.data["sources"]):
        lines.append((f"  taze olmayanlar için: `zenith scan --project {ans.project['id']} --all`" if lang == "tr"
                      else f"  for the non-fresh ones: `zenith scan --project {ans.project['id']} --all`"))
    if not detail:
        lines.append(("  tam yollar için: `kaynaklar detay`" if lang == "tr" else "  full paths: `sources detail`"))
    return "\n".join(lines)


def approvals(lang: str, ans: Answer) -> str:
    if ans.unknowns:
        return UNKNOWN_TEXT[lang]["nothing_pending"]
    d = ans.data
    lines = []
    if d["reconcile"]:
        lines.append(("Uzlaştırma gerekiyor (bir çalıştırma belirsiz bitti): " if lang == "tr" else "Reconciliation required (a run ended uncertain): ")
                     + "; ".join(f"{t['title']} [task:{t['id'][:8]}]" for t in d["reconcile"]))
    if d["awaiting"]:
        lines.append(("Onayını bekleyen: " if lang == "tr" else "Awaiting your approval: ")
                     + "; ".join(f"{t['title']} [task:{t['id'][:8]}]" for t in d["awaiting"]))
    if d["blocked"]:
        lines.append(("Bloke (karar senin): " if lang == "tr" else "Blocked (your call): ")
                     + "; ".join(f"{t['title']} [task:{t['id'][:8]}]" for t in d["blocked"]))
    lines.append(("Onay ve uzlaştırma terminalden: `zenith task approve --id …`, `zenith run reconcile …`" if lang == "tr"
                  else "Approve or reconcile from the CLI: `zenith task approve --id …`, `zenith run reconcile …`"))
    return "\n".join(lines)


def next_actions(lang: str, ans: Answer) -> str:
    if ans.unknowns:
        return UNKNOWN_TEXT[lang]["no_actions"]
    lines = [(f"{ans.project['name']} için sıradakiler:" if lang == "tr" else f"Next for {ans.project['name']}:")]
    for a in ans.data["actions"][:6]:
        cite = f" [{', '.join(c[:8] for c in a['citations'])}]" if a["citations"] else ""
        lines.append(f"  → {shorten_paths(a['action'], ans.project['sources'])}{cite}")
    if lang == "tr":
        lines.append("  (Metinler İngilizce; işlemler İngilizce yürür, sohbet Türkçe.)")
    return "\n".join(lines)


def checkin(lang: str, ans: Answer, project: dict) -> str:
    if ans.unknowns:
        err = ans.unknowns[0].removeprefix("error:")
        return (f"Kaydedemedim: {err}" if lang == "tr" else f"Could not record it: {err}")
    f = ans.facts[0]
    later = ans.data["later_evidence"]
    if lang == "tr":
        out = f"{project['name']} için not düştüm{_cite(f)} — beyan olarak; doğrulanmış saymıyorum, tarama ya da test gelene kadar öyle kalır."
        if later:
            out += " Dikkat: bundan sonra tarihli depo/test kaydı var; notun eski kalmış olabilir."
        return out
    out = f"Noted for {project['name']}{_cite(f)} — as a report; I don't count it as verified until a scan or a test says so."
    if later:
        out += " Heads-up: there is repository/test evidence dated after it; the note may already be stale."
    return out


def unknown(lang: str, raw: str) -> str:
    return ((f"'{raw}' için elimde bir şey yok. `yardım` yazarsan yapabildiklerimi sayarım." if raw else "Dinliyorum.")
            if lang == "tr" else
            (f"Nothing I can do with '{raw}'. Type `help` for the list." if raw else "Listening."))


def goodbye(lang: str) -> str:
    return "Durumu sakladım; sonra kaldığımız yerden devam ederiz." if lang == "tr" else "State saved; we'll pick up where we left off."
