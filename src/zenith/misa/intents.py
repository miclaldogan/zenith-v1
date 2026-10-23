"""Deterministic command grammar and language detection for Misa (Turkish / English).

Grammar (first match wins):
  1. Leading command forms:  proje|project|seç|select|switch|geç <ref>   → select
                             not|note|checkin|check-in [@Target]: <text> → checkin
                             kaynaklar|sources (detay|detail|tam|full)    → sources (detail)
  2. Digits only                                                        → number (listing-bound)
  3. Known phrases anywhere in the message (e.g. "nerede kaldım")
  4. Single-word commands: a message of at most 4 words containing a known stem. Turkish stems
     accept a short Turkish suffix (görev-ler, kaynak-lar); English stems accept only s/es.
  5. Otherwise unknown. Short unknown input (≤ 3 words) may still be a project name; the REPL
     resolves it and never guesses when it is ambiguous.
No prefix matching on free text: "scandal" is not "scan", "proje Durum Raporu" is a selection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_TR_CHARS = set("çğıöşüÇĞİÖŞÜ")
_TR_LOWER = "abcçdefgğhıijklmnoöprsştuüvyz"
_TR_WORDS = {"ne", "nerede", "nasıl", "proje", "projeler", "durum", "görev", "kanıt", "karar", "kaynak", "onay",
             "yardım", "çık", "kaldım", "kalmıştım", "yaptım", "bitmeli", "sırada", "hafta", "bugün", "yarın",
             "geçmiş", "not", "kaydet", "güncelleme", "bayat", "tarama", "seç", "evet", "hayır", "var", "yok",
             "son", "en", "mi", "mı", "mu", "mü", "bu", "şu", "için", "ile", "ve", "bir", "neden", "hangi", "detay", "tam"}
_EN_WORDS = {"what", "where", "how", "project", "projects", "status", "task", "tasks", "evidence", "decision",
             "decisions", "source", "sources", "approval", "approvals", "help", "exit", "quit", "last", "left",
             "next", "week", "today", "history", "run", "runs", "stale", "scan", "select", "switch", "show",
             "list", "the", "is", "are", "my", "did", "i", "me", "do", "which", "why", "due", "deadline",
             "note", "done", "finished", "started", "fix", "fixed", "draft", "half", "please", "and", "of", "detail", "full"}

INTENTS = ("help", "quit", "projects", "select", "number", "status", "last", "evidence", "decisions", "tasks",
           "sources", "runs", "approvals", "next", "checkin", "unknown")

_PHRASES: list[tuple[str, tuple[str, ...]]] = [
    ("last", ("nerede kaldım", "nerede kalmıştım", "en son ne", "son ne yaptım", "where did i leave", "where was i",
              "left off", "last time", "what did i last", "last thing")),
    ("next", ("sırada ne", "ne yapmalıyım", "ne yapayım", "what should i", "what now", "what\'s next", "what is next")),
    ("tasks", ("bu hafta", "ne bitmeli", "this week", "upcoming deadlines", "son tarih")),
    ("help", ("ne yapabilirsin", "what can you do")),
    ("approvals", ("bekleyen onay", "pending approval", "awaiting approval")),
    ("status", ("nasıl gidiyor", "how is it going", "how\'s it going")),
]
_STEMS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {   # intent -> (turkish stems, english stems)
    "quit": (("çık", "çıkış", "kapat", "görüşürüz", "hoşçakal"), ("exit", "quit", "bye")),
    "help": (("yardım", "komutlar"), ("help", "commands")),
    "projects": (("projeler",), ("projects",)),
    "status": (("durum", "özet"), ("status", "summary", "brief")),
    "evidence": (("kanıt", "kayıt"), ("evidence",)),
    "decisions": (("karar",), ("decision",)),
    "tasks": (("görev", "gecik", "deadline"), ("task", "deadline", "overdue", "upcoming", "due")),
    "sources": (("kaynak", "tarama", "bayat"), ("source", "stale", "scan")),
    "runs": (("geçmiş", "çalıştırma", "koşu"), ("history", "run", "execution")),
    "approvals": (("onay",), ("approval", "pending", "awaiting")),
    "next": (("öneri", "sırada"), ("next", "recommend")),
}
_SELECT_VERBS = ("proje", "project", "seç", "select", "switch", "geç")
_CHECKIN_VERBS = ("not", "note", "checkin", "check-in")
_DETAIL_WORDS = ("detay", "detail", "tam", "full", "ayrıntı")


@dataclass
class Parsed:
    intent: str
    language: str                      # "tr" | "en"
    argument: str = ""                 # project reference, note text, or raw short input
    target: str = ""                   # explicit @Target for check-ins
    detail: bool = False
    raw: str = ""
    words: list[str] = field(default_factory=list)


def detect_language(text: str, default: str = "tr") -> str:
    if any(ch in _TR_CHARS for ch in text):
        return "tr"
    words = set(re.findall(r"[a-zA-Z\']+", text.lower()))
    tr, en = len(words & _TR_WORDS), len(words & _EN_WORDS)
    if tr == en == 0:
        return default
    return "tr" if tr > en else "en" if en > tr else default


def _stem_hit(word: str, stem: str, turkish: bool) -> bool:
    if word == stem:
        return True
    if not word.startswith(stem):
        return False
    rest = word[len(stem):]
    if turkish:
        return 1 <= len(rest) <= 6 and all(c in _TR_LOWER for c in rest)   # görev-lerim, kaynak-larda
    return rest in ("s", "es")


def parse(text: str, default_language: str = "tr") -> Parsed:
    raw = text.strip()
    low = raw.lower()
    lang = detect_language(raw, default_language)
    words = low.split()
    if not raw:
        return Parsed("unknown", lang, raw=raw)
    if low in ("q", "x"):
        return Parsed("quit", lang, raw=raw)
    if low in ("?", "h"):
        return Parsed("help", lang, raw=raw)

    # 1. leading command forms
    first = words[0].rstrip(":")
    if first in _SELECT_VERBS and len(words) >= 2:
        ref = raw.split(None, 1)[1].strip()
        ref = re.sub(r"(?i)\s+(projesine|projesi|projesini|olarak|seç)$", "", ref).strip()
        return Parsed("select", lang, ref, raw=raw, words=words)
    if first in _CHECKIN_VERBS or any(low.startswith(v + ":") for v in _CHECKIN_VERBS):
        body = re.sub(r"(?i)^(check-in|checkin|note|not)\s*", "", raw, count=1)
        target = ""
        m = re.match(r"\s*@([^:]+?)\s*:\s*(.*)$", body, re.S)
        if m:
            target, body = m.group(1).strip(), m.group(2)
        else:
            body = body[1:] if body.startswith(":") else body
        return Parsed("checkin", lang, body.strip(), target=target, raw=raw, words=words)
    if first in ("kaynaklar", "sources", "kaynak", "source") and len(words) == 2 and words[1] in _DETAIL_WORDS:
        return Parsed("sources", lang, detail=True, raw=raw, words=words)

    # 2. numbers refer to the last listing shown in this session
    if low.isdigit():
        return Parsed("number", lang, low, raw=raw, words=words)

    # 3. phrases
    for intent, keys in _PHRASES:
        if any(k in low for k in keys):
            return Parsed(intent, lang, raw=raw, words=words)

    # 4. short single-word commands with Turkish suffix tolerance
    if len(words) <= 4:
        clean = [re.sub(r"[^\wçğıöşü]", "", w) for w in words]
        for intent, (tr_stems, en_stems) in _STEMS.items():
            for w in clean:
                if any(_stem_hit(w, s, True) for s in tr_stems) or any(_stem_hit(w, s, False) for s in en_stems):
                    return Parsed(intent, lang, raw=raw, words=words)

    # 5. unknown; a short input may be a project name (resolved, never guessed, by the REPL)
    return Parsed("unknown", lang, raw if len(words) <= 3 else "", raw=raw, words=words)
