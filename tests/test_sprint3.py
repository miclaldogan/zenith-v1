"""Sprint 3 (offline): Misa terminal session — intents, retrieval, rendering, persistence, CLI."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from zenith import sandbox
from zenith.demo import make_repo
from zenith.kernel import Store, ZenithError
from zenith.misa import intents, render
from zenith.misa.repl import MisaSession
from zenith.misa.retrieval import Retriever

SRC = str(Path(__file__).resolve().parents[1] / "src")


class IntentTests(unittest.TestCase):
    def test_language_detection_follows_the_owner(self):
        self.assertEqual(intents.detect_language("nerede kaldım"), "tr")
        self.assertEqual(intents.detect_language("where did I leave off?"), "en")
        self.assertEqual(intents.detect_language("görevler"), "tr")
        self.assertEqual(intents.detect_language("projects"), "en")
        self.assertEqual(intents.detect_language("1", default="en"), "en")   # neutral input keeps the session language
        self.assertEqual(intents.detect_language("1", default="tr"), "tr")

    def test_intents_tr_and_en(self):
        cases = {"nerede kaldım": "last", "en son ne yaptım": "last", "where was I": "last", "last time?": "last",
                 "projeler": "projects", "projects": "projects", "durum": "status", "status please": "status",
                 "görevler": "tasks", "bu hafta ne bitmeli": "tasks", "upcoming deadlines": "tasks",
                 "kaynaklar bayat mı": "sources", "which sources are stale": "sources", "kararlar": "decisions",
                 "kanıt": "evidence", "evidence": "evidence", "onaylar": "approvals", "pending approvals": "approvals",
                 "geçmiş": "runs", "execution history": "runs", "sırada ne var": "next", "what should I do next": "next",
                 "yardım": "help", "help": "help", "?": "help", "çık": "quit", "exit": "quit", "q": "quit"}
        for text, expected in cases.items():
            self.assertEqual(intents.parse(text).intent, expected, text)
        p = intents.parse("not: killpg düzeltmesi derleniyor")
        self.assertEqual((p.intent, p.argument, p.language), ("checkin", "killpg düzeltmesi derleniyor", "tr"))
        p = intents.parse("note: rebuttal half done")
        self.assertEqual((p.intent, p.argument, p.language), ("checkin", "rebuttal half done", "en"))
        p = intents.parse("proje Stabilimentum")
        self.assertEqual((p.intent, p.argument), ("select", "Stabilimentum"))
        self.assertEqual(intents.parse("project 2").argument, "2")
        self.assertEqual(intents.parse("foo bar baz").intent, "unknown")
        self.assertEqual(intents.parse("").intent, "unknown")


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root / "z.db")
        self.store.init()
        self.out: list[str] = []
        self.env = {**os.environ, "ZENITH_DB_PATH": str(self.root / "z.db"), "PYTHONPATH": SRC,
                    "ZENITH_STATE_DIR": str(self.root / "state"), "ZENITH_ASSISTANT_NAME": "Misa"}

    def tearDown(self):
        self.temp.cleanup()

    def session(self) -> MisaSession:
        return MisaSession(self.store, out=self.out.append)

    def populate(self):
        repo = self.root / "repo"
        make_repo(repo)
        gone = self.root / "gone"
        gone.mkdir()
        self.p1 = self.store.add_project("Stabilimentum", str(repo))
        self.src_gone = self.store.add_source(self.p1, str(gone), label="notes")
        other = self.root / "other"
        other.mkdir()
        self.p2 = self.store.add_project("Drosophila", str(other))
        self.store.scan_sources(self.p1)
        shutil.rmtree(gone)
        self.store.scan_source(self.src_gone)   # now unavailable
        self.store.add_task(self.p1, "Finish killpg fix", risk="L2", due_at="2020-01-01")   # overdue
        self.store.add_task(self.p1, "Write rebuttal", due_at="2999-01-01")
        t = self.store.add_task(self.p2, "Delta export", risk="L1")
        for s in ("CONTEXT_READY", "PLANNED", "AWAITING_APPROVAL"):
            self.store.transition(t, s)
        self.store.checkin(self.p1, "killpg compiles, not pushed")


class EmptyDatabaseTests(Base):
    def test_greeting_and_answers_on_empty_db(self):
        s = self.session()
        text = s.greet()
        self.assertIn("deterministik", text)
        self.assertIn("Kayıtlı proje yok", text)
        reply, keep = s.handle("nerede kaldım")
        self.assertTrue(keep)
        self.assertIn("Kayıtlı proje yok", reply)             # nothing to invent
        reply, _ = s.handle("projects")
        self.assertIn("No projects registered", reply)      # English in, English out
        reply, _ = s.handle("görevler")
        self.assertIn("Açık görev yok", reply)
        reply, keep = s.handle("çık")
        self.assertFalse(keep)
        self.assertEqual(len(self.store.list_turns()), 8)

    def test_bad_and_incomplete_commands(self):
        s = self.session()
        reply, keep = s.handle("foo bar baz")
        self.assertTrue(keep)
        self.assertIn("çözemedim", reply)
        self.assertIn("yardım", reply)
        reply, _ = s.handle("not:")
        self.assertIn("not", reply.lower())                   # "Neyi not alayım?" / "Notu da yaz"
        reply, _ = s.handle("not: bir şeyler")
        self.assertIn("hangi projeye", reply.lower())     # no active project: ask, never guess
        self.populate()
        s.handle("proje Stabilimentum")
        reply, _ = s.handle("not:")
        self.assertIn("not", reply.lower())                    # "Neyi not alayım?"
        reply, _ = s.handle("proje nonexistent")
        self.assertIn("eşleşmedi", reply)
        reply, _ = s.handle("evet")
        self.assertIn("Bekleyen bir şey yoktu", reply)
        reply, _ = s.handle("help")
        self.assertIn("deterministic", reply)


class PopulatedTests(Base):
    def setUp(self):
        super().setUp()
        self.populate()

    def test_opening_surfaces_real_state_only(self):
        s = self.session()
        text = s.greet()
        self.assertIn("2 proje", text)
        self.assertIn("Geciken: Stabilimentum — Finish killpg fix", text)
        self.assertIn("Onayını bekleyen 1", text)
        self.assertIn("ulaşılamıyor", text)

    def test_where_did_i_leave_is_grounded_and_labels_reports(self):
        s = self.session()
        s.handle("proje Stabilimentum")
        reply, _ = s.handle("nerede kaldım")                 # natural question → conversation engine (prose, no ids)
        self.assertIn("killpg compiles, not pushed", reply)
        self.assertIn("senin beyanın", reply)
        self.assertIn("doğrulanmış değil", reply)
        self.assertIn("teyit edemiyorum", reply)              # the unavailable source is flagged, never pretended fresh
        self.assertNotIn("[ev:", reply)                       # ids only on request
        reply, _ = s.handle("kaynakları göster")
        self.assertIn("[ev:", reply)
        self.assertIn("HUMAN_REPORT / REPORTED", reply)

    def test_sources_tasks_approvals_next_in_english(self):
        s = self.session()
        s.handle("project Stabilimentum")
        reply, _ = s.handle("which sources are stale?")
        self.assertIn("notes: unavailable", reply)
        self.assertIn("zenith scan", reply)
        reply, _ = s.handle("tasks")                          # bare command keeps the structured listing
        self.assertIn("[OVERDUE] Finish killpg fix", reply)
        self.assertIn("Write rebuttal", reply)
        reply, _ = s.handle("what should I do next")
        self.assertIn("Stabilimentum", reply)
        self.assertIn("overdue", reply)
        self.assertIn("Finish killpg fix", reply)
        reply, _ = s.handle("pending approvals")
        self.assertIn("Nothing awaiting", reply)             # project-scoped: Stabilimentum has none
        s.handle("project Drosophila")
        reply, _ = s.handle("approvals")
        self.assertIn("Delta export", reply)
        self.assertIn("zenith task approve", reply)

    def test_checkin_is_recorded_as_report(self):
        s = self.session()
        s.handle("proje Stabilimentum")
        before = len(self.store.list_evidence(self.p1))
        reply, _ = s.handle("not: rebuttal taslağı yarıya geldi")
        self.assertIn("Stabilimentum için not olarak kaydedeyim mi", reply)
        self.assertEqual(len(self.store.list_evidence(self.p1)), before)       # nothing written before confirmation
        reply, _ = s.handle("evet")
        self.assertIn("Stabilimentum için not düştüm", reply)
        self.assertIn("doğrulanmış saymıyorum", reply)
        ev = self.store.list_evidence(self.p1)
        self.assertEqual(len(ev), before + 1)
        self.assertEqual((ev[0]["origin"], ev[0]["confidence"]), ("HUMAN_REPORT", "REPORTED"))
        self.assertEqual(self.store.brief(self.p1)["projects"][0]["status"]["label"], "OBSERVED")

    def test_evidence_decisions_history_views(self):
        s = self.session()
        reply, _ = s.handle("2")
        self.assertIn("önce `projeler`", reply)             # numbers are bound to a listing shown in this session
        s.handle("projeler")
        s.handle("2")                                        # listing is sorted by name: 2 = Stabilimentum
        self.assertEqual(s.project_id, self.p1)
        reply, _ = s.handle("kanıt")
        self.assertIn("HUMAN_REPORT", reply)
        reply, _ = s.handle("kararlar")
        self.assertIn("Kayıtlı karar yok", reply)
        reply, _ = s.handle("geçmiş")
        self.assertIn("Hiç çalıştırma", reply)

    def test_persistence_across_restarts(self):
        s1 = self.session()
        s1.handle("proje Drosophila")
        s1.handle("status")                                  # switches language to English
        s2 = MisaSession(Store(self.store.db_path), out=self.out.append)   # a new process would do the same
        self.assertEqual(s2.project_id, self.p2)
        self.assertEqual(s2.language, "en")
        text = s2.greet()
        self.assertIn("Active project: Drosophila (your last selection)", text)
        turns = self.store.list_turns()
        self.assertEqual([t["role"] for t in turns], ["owner", "misa", "owner", "misa"])
        self.assertTrue(all(t["generated_by"] == "deterministic" for t in turns if t["role"] == "misa"))
        with self.assertRaises(Exception):                  # the turn log is append-only
            import sqlite3
            sqlite3.connect(self.store.db_path, isolation_level=None).execute("DELETE FROM conversation_turns")

    def test_keyboard_interrupt_and_eof_end_cleanly(self):
        s = self.session()

        def lines():
            yield "projeler"
            yield "proje 1"
            raise KeyboardInterrupt
        self.assertEqual(s.run(lines=lines()), 0)
        self.assertIn("Durumu sakladım", self.out[-1])
        self.assertEqual(Store(self.store.db_path).conversation_state()["project_id"], self.p2)   # "1" = Drosophila
        s2 = self.session()
        self.assertEqual(s2.run(lines=iter([])), 0)         # EOF immediately


class CliTests(Base):
    def run_cli(self, *args, stdin=None):
        return subprocess.run([sys.executable, "-m", "zenith", *args], env=self.env, input=stdin, capture_output=True, text=True)

    def test_default_command_is_misa_and_resumes(self):
        self.populate()
        r = self.run_cli(stdin="projeler\nproje Drosophila\nçık\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Projeler:", r.stdout)
        self.assertIn("Tamam, Drosophila", r.stdout)
        self.assertIn("Durumu sakladım", r.stdout)
        r = self.run_cli(stdin="where did I leave off?\nexit\n")
        self.assertIn("Aktif proje: Drosophila (son seçtiğin)", r.stdout)   # banner uses the previous session's language
        self.assertIn("Delta export", r.stdout)            # English in, English out; Drosophila's own task, nothing invented
        self.assertNotIn("killpg compiles", r.stdout)   # Stabilimentum's note never bleeds into Drosophila's answer
        r = self.run_cli("misa", "--once", "görevler")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Delta export", r.stdout)

    def test_uninitialised_db_gives_clean_error(self):
        env = {**self.env, "ZENITH_DB_PATH": str(self.root / "none.db")}
        r = subprocess.run([sys.executable, "-m", "zenith"], env=env, input="çık\n", capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("zenith init", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
