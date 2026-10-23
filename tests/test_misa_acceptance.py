"""Misa conversation acceptance gate: grounding assessment, scenarios A/B/C, provider independence."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zenith.kernel import Store
from zenith.misa import acceptance
from zenith.misa.conversation import FakeConversationProvider
from zenith.misa.engine import assess_grounding
from zenith.misa.repl import MisaSession

SRC = str(Path(__file__).resolve().parents[1] / "src")


class GroundingAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.request = {"language": "tr", "question_type": "last", "projects": [{"name": "Harbor"}],
                        "items": [
                            {"id": "v1", "kind": "verification", "text": "unit passed at abc1234", "when": "2026-10-01 10:00",
                             "head": "abc1234def", "applies": False, "epistemic": "verified"},
                            {"id": "e1", "kind": "evidence", "text": "Harbor bitti sayılır, sadece dokümantasyon kaldı",
                             "when": "2026-09-20 10:00", "epistemic": "reported"},
                            {"id": "t1", "kind": "task", "text": "Release Harbor 1.0", "due": "2020-01-01 23:59", "epistemic": "intended"},
                        ]}

    def test_citation_existence_is_necessary_not_sufficient(self):
        ok = assess_grounding({"text": "Harbor'da son not “bitti sayılır” diyor ama doğrulanmış değil.", "cited_ids": ["e1"], "uncertainty": []}, self.request)
        self.assertTrue(ok["citations_exist"]); self.assertEqual(ok["verdict"], "supported")
        bad = assess_grounding({"text": "x", "cited_ids": ["zz"], "uncertainty": []}, self.request)
        self.assertFalse(bad["citations_exist"]); self.assertEqual(bad["verdict"], "unsupported")
        self.assertIn("semantic claim support", ok["not_checked"])     # honesty about limits

    def test_fabricated_specifics_are_caught(self):
        a = assess_grounding({"text": "Testler 2026-10-15'te 9f9f9f9 sürümünde geçti, notta “her şey hazır” yazıyor.",
                              "cited_ids": ["v1"], "uncertainty": []}, self.request)
        self.assertEqual(a["verdict"], "unsupported")
        self.assertIn("date:2026-10-15", a["unsupported_details"])
        self.assertIn("hash:9f9f9f9", a["unsupported_details"])
        self.assertTrue(any(u.startswith("quote:her şey hazır") for u in a["unsupported_details"]))
        b = assess_grounding({"text": "Testler 2026-10-01'de abc1234 sürümünde geçmişti ama şimdi geçerli değil.", "cited_ids": ["v1"], "uncertainty": []}, self.request)
        self.assertEqual(b["unsupported_details"], [])

    def test_temporal_and_contradiction_caveats(self):
        t = assess_grounding({"text": "Harbor doğrulanmış durumda, testler geçiyor.", "cited_ids": ["v1"], "uncertainty": []}, self.request)
        self.assertTrue(t["temporal_caveat"]); self.assertEqual(t["verdict"], "partial")
        c = assess_grounding({"text": "Harbor bitti, sadece dokümantasyon kaldı.", "cited_ids": ["e1"], "uncertainty": []}, self.request)
        self.assertTrue(c["contradiction_caveat"])
        fine = assess_grounding({"text": "Sen Harbor bitti demiştin ama bu senin beyanın; doğrulanmış değil.", "cited_ids": ["e1"], "uncertainty": []}, self.request)
        self.assertFalse(fine["contradiction_caveat"]); self.assertEqual(fine["verdict"], "supported")


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.world = acceptance.build_world(self.base)
        self.store = Store(self.world["db"])

    def tearDown(self):
        self.temp.cleanup()

    def test_scenarios_pass_offline_and_with_mock(self):
        for provider in (None, FakeConversationProvider()):
            results = acceptance.run_scenarios(self.store, provider, state_root=self.base)
            text = acceptance.render(results)
            self.assertTrue(results["all_passed"], text)
            b0 = results["B"]["turns"][0]["reply"]
            self.assertIn(self.world["head0"][:7], b0)
            self.assertIn("Harbor bitti sayılır", b0)
            self.assertIn("beyan", b0)
            self.assertNotIn("[ev:", b0)
            self.assertIn("[ev:", results["B"]["turns"][2]["reply"])      # citations on request
            self.assertIn("destek", results["B"]["turns"][2]["reply"])    # grounding verdict shown
            c1 = results["C"]["turns"][1]["reply"]
            self.assertIn("Harbor", c1); self.assertNotIn("Lighthouse", c1)
            a0 = results["A"]["turns"][0]["reply"]
            self.assertIn("Orion", a0); self.assertIn("kayıtlı bir proje yok", a0)

    def test_caveats_are_appended_to_model_text(self):
        class Overclaim(FakeConversationProvider):
            def respond(self, ctx, request):
                r = super().respond(ctx, request)
                if request["question_type"] == "status":
                    v = next((i for i in request["items"] if i["kind"] == "verification"), None)
                    r["text"] = f"Harbor doğrulanmış durumda, testler geçiyor ({v['head'][:7]}). Harbor bitti sayılır, dokümantasyon kaldı."
                    r["cited_ids"] = [v["id"]] if v else []
                return r
        out = []
        self.store.save_conversation_state("primary", {"language": "tr"})
        s = MisaSession(self.store, out=out.append, provider=Overclaim(), state_root=self.base)
        reply, _ = s.handle("Harbor ne âlemde?")
        self.assertIn("şimdiki durum teyit edilmedi", reply)        # temporal caveat appended
        self.assertIn("senin beyanın", reply)                        # contradiction caveat appended
        self.assertEqual(s.state["last_grounding"]["verdict"], "partial")

    def test_conversation_never_touches_engineering_roles(self):
        with mock.patch("zenith.orchestration.Orchestrator.plan", side_effect=AssertionError("director called")), \
             mock.patch("zenith.orchestration.Orchestrator.run_once", side_effect=AssertionError("worker called")), \
             mock.patch("zenith.sandbox.run_isolated", side_effect=AssertionError("sandbox executor called")):
            results = acceptance.run_scenarios(self.store, FakeConversationProvider(), state_root=self.base)
        self.assertTrue(results["all_passed"])

    def test_cli_eval_and_live_plan_make_no_call(self):
        env = {**os.environ, "PYTHONPATH": SRC, "ZENITH_DB_PATH": str(self.base / "unused.db")}
        env.pop("ZENITH_ENABLED_ADAPTERS", None)
        r = subprocess.run([sys.executable, "-m", "zenith", "misa", "--eval", "--provider", "fake-conversation"], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OVERALL: PASS", r.stdout)
        r = subprocess.run([sys.executable, "-m", "zenith", "misa", "--live-plan"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        plan = json.loads(r.stdout)
        self.assertIn("api.anthropic.com:443", plan["security"])
        self.assertIn("ZENITH_LIVE_SMOKE_ACK", plan["first_step"])
        self.assertTrue(plan["preflight"].get("blockers"))           # not enabled → blockers, no call
        self.assertFalse((self.base / "unused.db").exists())


if __name__ == "__main__":
    unittest.main()
