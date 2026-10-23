"""Misa terminal session: presentation + persisted conversation state.

Selection semantics (explicit, never inferred from activity):
  active project        = the project the owner last selected explicitly (persisted; shown in the banner)
  number N              = the Nth row of the listing shown *in this session* (no listing → ask for one)
  most recent activity  = the project with the newest evidence; shown separately, never auto-selected
Check-ins go only to the active project, or to an explicit `not @Proje: …` target; a note that
names another registered project is refused and the owner is asked to target it explicitly.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Callable, Iterable

from .. import identity
from ..kernel import Store, ZenithError
from . import render
from .engine import ConversationEngine
from .intents import parse
from .retrieval import Retriever

CONVERSATION_ID = "primary"


class MisaSession:
    def __init__(self, store: Store, out: Callable[[str], None] | None = None, provider=None,
                 state_root: Path | None = None):
        self.store = store
        self.retriever = Retriever(store)
        self.identity = identity.load()
        if provider is None:
            provider = self._configured_provider()
        self.provider = provider
        self.engine = ConversationEngine(store, provider, state_root)
        raw_out = out or (lambda s: print(s, flush=True))
        self.out = lambda s: raw_out(render.clean(s))
        self.state = store.conversation_state(CONVERSATION_ID)
        self.language = self.state.get("language", "tr")
        self.project_id = self.state.get("project_id")
        self.resumed = bool(self.state.get("turns", 0))
        self.listing: list[str] = []          # project ids in the order last shown, this session only

    def _configured_provider(self):
        """The conversation role's adapter, only if explicitly enabled (ZENITH_ENABLED_ADAPTERS)."""
        from ..extensions import load
        name = self.identity.roles.get("conversation", "")
        if not name or name not in {x.strip() for x in os.environ.get("ZENITH_ENABLED_ADAPTERS", "").split(",")}:
            return None
        try:
            return load(name, "conversation", script={})
        except ZenithError:
            return None

    # ---- state ----------------------------------------------------------------------------
    def _project(self) -> dict | None:
        if not self.project_id:
            return None
        try:
            return self.store.get_project(self.project_id)
        except ZenithError:
            self.project_id = None
            return None

    def _state_dict(self) -> dict:
        return {**{k: v for k, v in self.state.items() if k != "turns"}, "language": self.language, "project_id": self.project_id}

    # ---- turns ----------------------------------------------------------------------------
    def greet(self) -> str:
        project = self._project()
        text = render.banner(self.language, self.identity.assistant.display_name, self.resumed,
                             project["name"] if project else None, model=self.provider.name if self.provider else None)
        text += "\n" + render.opening(self.language, self.retriever.opening(self.project_id))
        self.out(text)
        return text

    def handle(self, line: str) -> tuple[str, bool]:
        """Answer one owner message. Owner turn, reply and state are persisted atomically."""
        parsed = parse(line, self.language)
        self.language = parsed.language
        generated_by = "deterministic"
        cites: list[str] = []
        try:
            if self._is_command(parsed) and not self.state.get("pending_action"):
                reply, keep = self._dispatch(parsed)
                cites = re.findall(r"\[((?:ev|task|run|dec):[0-9a-f]{8})", reply)
            else:
                result = self.engine.turn(line, self.state)
                self.language = self.state.get("language", self.language)
                reply, keep, generated_by, cites = result.text, True, result.generated_by, result.cited_ids
                if result.action_taken and result.action_taken.get("project_id"):
                    pass  # writes never change the active project
        except ZenithError as exc:
            reply, keep = render.error(self.language, str(exc)), True
        except sqlite3.Error as exc:
            reply, keep = render.error(self.language, f"database: {exc}"), True
        self.store.record_exchange(CONVERSATION_ID, self.language, parsed.intent, line, reply, cites, self._state_dict(),
                                   generated_by=generated_by)
        self.out(reply)
        return reply, keep

    def _is_command(self, parsed) -> bool:
        """Explicit grammar wins only for terse, unambiguous commands; everything else is conversation."""
        if parsed.intent in ("quit", "help", "projects", "number", "select"):
            return True
        if parsed.intent == "checkin":
            return bool(parsed.target)      # `not @Proje: …` is unambiguous; anything else is staged and confirmed
        if parsed.intent == "unknown":
            return False
        if parsed.intent == "sources" and parsed.detail:
            return True
        return len(parsed.words) == 1   # bare "görevler" / "status" / "kaynaklar"; any phrasing → conversation engine

    def _select(self, project: dict, lang: str) -> str:
        self.project_id = project["id"]
        self.state["topic_project"] = project["id"]
        return render.selected(lang, project) + "\n" + render.status(lang, self.retriever.status(project["id"]))

    def _dispatch(self, parsed) -> tuple[str, bool]:
        lang, intent = parsed.language, parsed.intent
        r = self.retriever
        if intent == "quit":
            return render.goodbye(lang), False
        if intent == "help":
            return render.HELP[lang], True
        if intent == "projects":
            ans = r.projects()
            self.listing = [row["id"] for row in ans.data.get("rows", [])]
            return render.projects(lang, ans), True
        if intent == "select" and parsed.argument.isdigit():
            intent, parsed.argument = "number", parsed.argument
        if intent == "number":
            n = int(parsed.argument)
            if not self.listing:
                return render.need_listing(lang), True
            if not 1 <= n <= len(self.listing):
                return render.bad_number(lang, len(self.listing)), True
            return self._select(self.store.get_project(self.listing[n - 1]), lang), True
        if intent == "select" or (intent == "unknown" and parsed.argument):
            project, candidates = r.resolve_project(parsed.argument)
            if project is not None:
                return self._select(project, lang), True
            if intent == "select" or candidates:
                return render.ambiguous(lang, parsed.argument, candidates), True
            return render.unknown(lang, parsed.raw), True
        if intent == "checkin":
            return self._checkin(parsed), True
        if intent in ("tasks", "approvals") and not self.project_id:
            ans = r.tasks(None) if intent == "tasks" else r.approvals(None)
            return (render.tasks if intent == "tasks" else render.approvals)(lang, ans), True
        project = self._project()
        if project is None:
            return render.no_project(lang), True
        pid = project["id"]
        if intent == "status":
            return render.status(lang, r.status(pid)), True
        if intent == "last":
            return render.last(lang, r.last(pid)), True
        if intent == "evidence":
            return render.facts_list(lang, r.evidence(pid), "kanıtlar", "evidence"), True
        if intent == "decisions":
            return render.facts_list(lang, r.decisions(pid), "kararlar", "decisions"), True
        if intent == "tasks":
            return render.tasks(lang, r.tasks(pid)), True
        if intent == "sources":
            return render.sources(lang, r.sources(pid), detail=parsed.detail), True
        if intent == "runs":
            return render.facts_list(lang, r.runs(pid), "çalıştırma geçmişi", "execution history"), True
        if intent == "approvals":
            return render.approvals(lang, r.approvals(pid)), True
        if intent == "next":
            return render.next_actions(lang, r.next_actions(pid)), True
        return render.unknown(lang, parsed.raw), True

    def _checkin(self, parsed) -> str:
        """Write only to an unambiguous target; echo the target in the confirmation."""
        lang = parsed.language
        if not parsed.argument:
            return render.checkin_needs_text(lang)
        if parsed.target:
            project, candidates = self.retriever.resolve_project(parsed.target)
            if project is None:
                return render.ambiguous(lang, parsed.target, candidates)
        else:
            project = self._project()
            if project is None:
                return render.checkin_needs_project(lang)
            mentioned = self.retriever.projects_mentioned(parsed.argument, exclude=project["id"])
            if mentioned:
                return render.checkin_mentions_other(lang, project, mentioned)
        return render.checkin(lang, self.retriever.checkin(project["id"], parsed.argument), project)

    # ---- loop -----------------------------------------------------------------------------
    def run(self, lines: Iterable[str] | None = None) -> int:
        """Interactive loop (stdin) or scripted (iterable of lines). Ctrl-C / EOF end cleanly."""
        self.greet()
        source = iter(lines) if lines is not None else None
        try:
            while True:
                try:
                    line = next(source) if source is not None else input("sen> " if self.language == "tr" else "you> ")
                except StopIteration:
                    break
                except EOFError:
                    self.out("")
                    break
                if not line.strip():
                    continue
                _, keep = self.handle(line)
                if not keep:
                    return 0
        except KeyboardInterrupt:
            self.out("")
        self.store.save_conversation_state(CONVERSATION_ID, self._state_dict())
        self.out(render.goodbye(self.language))
        return 0


def main(store: Store, once: str | None = None) -> int:
    session = MisaSession(store)
    if once is not None:
        session.handle(once)
        return 0
    if not sys.stdin.isatty():
        return session.run(lines=[l.rstrip("\n") for l in sys.stdin])
    return session.run()
