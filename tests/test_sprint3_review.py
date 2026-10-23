"""Sprint 3 independent review: selection semantics, check-in targeting, verification freshness,
command grammar, storage atomicity, terminal safety, path presentation."""
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zenith import sandbox
from zenith.demo import make_repo
from zenith.kernel import Store, ZenithError
from zenith.misa import intents, render
from zenith.misa.repl import MisaSession
from zenith.misa.retrieval import Retriever


def git(path, *args):
    return subprocess.run(["git", "-c", "user.email=t@e.invalid", "-c", "user.name=t", *args], cwd=path, check=True,
                          capture_output=True, text=True).stdout.strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root / "z.db")
        self.store.init()
        self.out = []

    def tearDown(self):
        self.temp.cleanup()

    def session(self):
        return MisaSession(self.store, out=self.out.append)

    def two_projects(self):
        """Alpha: selected earlier, no recent evidence. Zeta: never selected, newest evidence."""
        a = self.root / "alpha"; a.mkdir()
        z = self.root / "zeta"; z.mkdir()
        self.alpha = self.store.add_project("Alpha", str(a))
        self.zeta = self.store.add_project("Zeta", str(z))
        self.store.checkin(self.zeta, "zeta moved last")


class SelectionSemanticsTests(Base):
    def test_active_project_is_explicit_and_recency_is_informational(self):
        self.two_projects()
        s = self.session()
        s.handle("proje Alpha")
        s2 = MisaSession(Store(self.store.db_path), out=self.out.append)
        text = s2.greet()
        self.assertIn("Aktif proje: Alpha (son seçtiğin)", text)
        self.assertIn("En son hareket: Zeta", text)
        self.assertIn("seçmedikçe ona geçmem", text)
        reply, _ = s2.handle("nerede kaldım")
        self.assertIn("Alpha", reply)                               # the answer names the active project
        self.assertNotIn("zeta moved last", reply)
        self.assertEqual(s2.project_id, self.alpha)

    def test_numbers_bind_to_the_listing_shown_in_this_session(self):
        self.two_projects()
        s = self.session()
        reply, _ = s.handle("1")
        self.assertIn("önce `projeler`", reply)
        self.assertIsNone(s.project_id)
        reply, _ = s.handle("proje 2")
        self.assertIn("önce `projeler`", reply)
        s.handle("projeler")
        s.handle("2")
        self.assertEqual(s.project_id, self.zeta)
        reply, _ = s.handle("9")
        self.assertIn("1–2", reply)
        self.assertEqual(s.project_id, self.zeta)                  # unchanged on a bad number
        s3 = MisaSession(Store(self.store.db_path), out=self.out.append)   # listing does not survive restart
        reply, _ = s3.handle("1")
        self.assertIn("önce `projeler`", reply)

    def test_ambiguous_references_are_never_guessed(self):
        (self.root / "p1").mkdir(); (self.root / "p2").mkdir()
        self.store.add_project("Research Alpha", str(self.root / "p1"))
        self.store.add_project("Research Beta", str(self.root / "p2"))
        s = self.session()
        reply, _ = s.handle("proje Research")
        self.assertIn("birden fazla", reply)
        self.assertIn("Research Alpha", reply)
        self.assertIsNone(s.project_id)
        reply, _ = s.handle("Research")                            # bare short text → conversation engine, same rule
        self.assertIn("Research Alpha", reply)
        self.assertIn("Research Beta", reply)
        self.assertIsNone(s.project_id)
        reply, _ = s.handle("proje Durum Raporu")                  # a project name containing a command word
        self.assertIn("eşleşmedi", reply)                          # selection attempted, not `status`
        s.handle("proje Research Beta")
        self.assertIsNotNone(s.project_id)


class CheckinTargetingTests(Base):
    def test_notes_never_land_on_the_wrong_project(self):
        self.two_projects()
        s = self.session()
        reply, _ = s.handle("not: bir şeyler yaptım")
        self.assertIn("hangi projeye", reply.lower())
        self.assertEqual(len(self.store.list_evidence(self.alpha)), 0)
        s.handle("proje Alpha")
        reply, _ = s.handle("not: Zeta için rebuttal yarıya geldi")   # names another project → confirm for THAT one, flagged
        self.assertIn("Konu Alpha ama not Zeta", reply)
        self.assertIn("Zeta için not olarak kaydedeyim mi", reply)
        self.assertEqual(len(self.store.list_evidence(self.alpha)), 0)
        self.assertEqual(len(self.store.list_evidence(self.zeta)), 1)  # nothing written yet
        reply, _ = s.handle("hayır")
        self.assertEqual(len(self.store.list_evidence(self.zeta)), 1)
        reply, _ = s.handle("not @Zeta: rebuttal yarıya geldi")        # fully explicit target → direct write
        self.assertIn("Zeta için not düştüm", reply)
        self.assertEqual(len(self.store.list_evidence(self.zeta)), 2)
        self.assertEqual(s.project_id, self.alpha)                  # explicit target does not switch the active project
        reply, _ = s.handle("not: alpha'da testler yazıldı")           # implicit target → confirmation first
        self.assertIn("Alpha için not olarak kaydedeyim mi", reply)
        reply, _ = s.handle("evet")
        self.assertIn("Alpha için not düştüm", reply)
        ev = self.store.list_evidence(self.alpha)[0]
        self.assertEqual((ev["origin"], ev["confidence"]), ("HUMAN_REPORT", "REPORTED"))
        # a note that names another project's OPEN TASK (not the project) is refused too
        self.store.add_task(self.zeta, "Export field data to CSV")
        reply, _ = s.handle("not: CSV export için yarın zaman ayıracağım")
        self.assertIn("Zeta'dan bahsediyor", reply)
        self.assertIn("Export field data", reply)
        self.assertEqual(len(self.store.list_evidence(self.alpha)), 1)      # nothing new on Alpha
        reply, _ = s.handle("not: alpha'da refactor bitti")                 # unrelated words still go through (after confirmation)
        self.assertIn("kaydedeyim mi", reply)
        s.handle("evet")
        reply, _ = s.handle("not @Nope: x")
        self.assertIn("eşleşmedi", reply)
        reply, _ = s.handle("note @Zet: english target by prefix")
        self.assertIn("Noted for Zeta", reply)


class VerificationFreshnessTests(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.root / "repo"
        make_repo(self.repo)
        self.notes = self.root / "notes"
        self.notes.mkdir()
        self.p = self.store.add_project("Core", str(self.repo))
        self.repo_src = self.store.list_sources(self.p)[0]["id"]
        self.notes_src = self.store.add_source(self.p, str(self.notes), label="notes")
        self.store.scan_sources(self.p)
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.ev = self.store.record_test_verification(self.p, self.repo_src, "unit", 0, self.head)

    def brief(self):
        return self.store.brief(self.p)["projects"][0]

    def test_unrelated_unavailable_source_does_not_downgrade(self):
        shutil.rmtree(self.notes)
        self.store.scan_source(self.notes_src)
        b = self.brief()
        self.assertEqual(b["status"]["label"], "VERIFIED")
        self.assertEqual(b["latest_verification"]["applicability"], "current")
        s = self.session()
        s.handle("proje Core")
        reply, _ = s.handle("nerede kaldım")
        self.assertIn(f"{self.head[:7]} sürümünde geçmişti", reply)
        self.assertIn("hâlâ geçerli", reply)
        self.assertIn("notes", reply)                              # the unrelated unavailable source is still mentioned
        self.assertIn("teyit edemiyorum", reply)

    def test_verified_source_unavailable_is_not_currently_verified_but_evidence_remains(self):
        self.repo.rename(self.root / "moved")
        self.store.scan_source(self.repo_src)
        b = self.brief()
        self.assertEqual(b["status"]["label"], "VERIFICATION_STALE")
        self.assertEqual(b["latest_verification"]["applicability"], "source_unavailable")
        self.assertEqual(b["latest_verification"]["evidence_id"], self.ev)   # historical evidence preserved
        self.assertEqual(self.store.list_evidence(self.p, "TEST_VERIFICATION")[0]["details"]["passed"], True)
        s = self.session()
        s.handle("project Core")
        reply, _ = s.handle("where did I leave off?")
        self.assertIn(f"at {self.head[:7]}", reply)
        self.assertIn("currently unavailable", reply)
        self.assertIn("can't say whether they pass today", reply)

    def test_head_moved_and_scan_stale(self):
        (self.repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        git(self.repo, "commit", "-qam", "fix")
        self.store.scan_source(self.repo_src)
        b = self.brief()
        self.assertEqual(b["latest_verification"]["applicability"], "head_moved")
        self.assertEqual(b["latest_verification"]["current_head"], git(self.repo, "rev-parse", "HEAD"))
        later = datetime.now(timezone.utc) + timedelta(days=30)
        b = self.store.brief(self.p, now=later)["projects"][0]
        self.assertIn(b["latest_verification"]["applicability"], ("head_moved", "scan_stale"))
        self.assertEqual(b["status"]["label"], "VERIFICATION_STALE")


class GrammarTests(unittest.TestCase):
    def test_no_prefix_collisions_or_embedded_commands(self):
        p = intents.parse
        self.assertEqual(p("proje Durum Raporu").intent, "select")
        self.assertEqual(p("project Status Board").argument, "Status Board")
        self.assertEqual(p("scandal").intent, "unknown")
        self.assertEqual(p("running late").intent, "unknown")
        self.assertEqual(p("duel").intent, "unknown")
        self.assertEqual(p("bugün görevler hakkında uzun uzun düşündüm ama bir karar veremedim").intent, "unknown")
        self.assertEqual(p("görevler").intent, "tasks")
        self.assertEqual(p("görevlerim").intent, "tasks")
        self.assertEqual(p("kaynaklar?").intent, "sources")
        self.assertEqual(p("tasks").intent, "tasks")
        self.assertEqual(p("taskforce").intent, "unknown")
        self.assertEqual(p("nerede kaldım bu projede ya").intent, "last")
        self.assertEqual(p("42").intent, "number")
        self.assertEqual(p("Drosophila").argument, "Drosophila")
        self.assertEqual(p("kaynaklar detay").detail, True)
        self.assertEqual(p("sources full").detail, True)
        c = p("not @Stabilimentum: killpg derlendi")
        self.assertEqual((c.intent, c.target, c.argument), ("checkin", "Stabilimentum", "killpg derlendi"))
        c = p("note: plain english")
        self.assertEqual((c.intent, c.target, c.language), ("checkin", "", "en"))
        self.assertEqual(p("çıkış yap").intent, "quit")
        self.assertEqual(p("  ").intent, "unknown")

    def test_language_switching(self):
        self.assertEqual(intents.parse("görevler", "en").language, "tr")
        self.assertEqual(intents.parse("tasks", "tr").language, "en")
        self.assertEqual(intents.parse("1", "en").language, "en")
        self.assertEqual(intents.parse("Drosophila", "tr").language, "tr")


class StorageAndSafetyTests(Base):
    def test_exchange_is_atomic_and_ordered(self):
        self.two_projects()
        s = self.session()
        s.handle("projeler")
        s.handle("proje Alpha")
        turns = self.store.list_turns()
        self.assertEqual([t["role"] for t in turns], ["owner", "misa", "owner", "misa"])
        self.assertEqual([t["seq"] for t in turns], sorted(t["seq"] for t in turns))
        self.assertEqual(turns[0]["generated_by"], "owner")
        self.assertEqual(turns[1]["generated_by"], "deterministic")
        con = sqlite3.connect(self.store.db_path, isolation_level=None)
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("UPDATE conversation_turns SET text='x'")
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("DELETE FROM conversation_turns")
        # provenance column is free text: a future model identifier fits without schema change
        con.execute("INSERT INTO conversation_turns(conversation_id,role,language,intent,text,citations,generated_by,occurred_at) "
                    "VALUES ('primary','misa','tr',NULL,'t','[]','model:claude-code:claude-sonnet','2026-01-01T00:00:00+00:00')")

    def test_errors_do_not_crash_the_session_and_are_recorded(self):
        self.two_projects()
        s = self.session()
        s.handle("proje Alpha")
        reply, keep = s.handle("not: " + "x" * 1001)
        self.assertTrue(keep)
        self.assertIn("1–1000", reply)
        self.assertEqual(self.store.list_turns()[-1]["role"], "misa")
        # the active project disappears between sessions (deleted row): no crash, clear fallback
        sqlite3.connect(self.store.db_path, isolation_level=None).execute(
            "UPDATE conversations SET state=json_set(state,'$.project_id','gone')")
        s2 = MisaSession(Store(self.store.db_path), out=self.out.append)
        reply, _ = s2.handle("durum")
        self.assertIn("Önce bir proje", reply)

    def test_control_characters_and_secrets_are_neutralised(self):
        evil = self.root / "evil"; evil.mkdir()
        p = self.store.add_project("Nice\x1b[31m RED\x1b[0m\x07", str(evil))
        s = self.session()
        s.handle("projeler")
        self.assertNotIn("\x1b", "".join(self.out))
        self.assertNotIn("\x07", "".join(self.out))
        self.assertIn("Nice RED", "".join(self.out))
        s.handle("proje Nice")
        s.handle("not: token=sk-abcdefghijklmnopqrstuvwxyz0123 ve şifre: hunter2")
        s.handle("evet")
        turns = self.store.list_turns()
        blob = " ".join(t["text"] for t in turns)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", blob)
        self.assertNotIn("hunter2", blob)
        self.assertIn("[REDACTED]", blob)
        self.assertEqual(render.clean("a\x1b[2Jb\x9fc"), "abc")
        self.assertEqual(render.clean("Türkçe çğıöşü ✓\n ok"), "Türkçe çğıöşü ✓\n ok")


class PathPresentationTests(Base):
    def test_labels_by_default_full_paths_on_request(self):
        repo = self.root / "deep" / "nested" / "projdir"
        repo.mkdir(parents=True)
        p = self.store.add_project("P", str(repo))
        s = self.session()
        s.handle("proje P")
        reply, _ = s.handle("kaynaklar")
        self.assertIn("projdir:", reply)
        self.assertNotIn(str(self.root), reply)
        self.assertIn("kaynaklar detay", reply)
        reply, _ = s.handle("kaynaklar detay")
        self.assertIn(str(repo), reply)
        reply, _ = s.handle("sırada ne")
        self.assertNotIn(str(self.root), reply)                   # next-action text uses the short name
        self.assertEqual(self.store.list_sources(p)[0]["path"], str(repo))   # canonical identity untouched


if __name__ == "__main__":
    unittest.main()
