"""`zenith demo story` is reproducible on fictional data and labels the simulated parts honestly."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from zenith.story import render, run_story


class StoryTests(unittest.TestCase):
    def test_story_runs_offline_and_labels_simulation(self):
        with tempfile.TemporaryDirectory() as tmp:
            rep = run_story(tmp, local=False, poc_scenarios=("success",))
            self.assertIn("fictional", rep["data"])
            self.assertEqual(set(rep["records"]), {"Harbor", "Lighthouse"})
            self.assertFalse(rep["records"]["Harbor"]["latest_verification"]["applies"])     # stale by construction
            self.assertEqual(len(rep["scene_a"]["turns"]), 3)
            self.assertTrue(all(t["generated_by"].startswith("deterministic") for t in rep["scene_a"]["turns"]))
            self.assertIn("Harbor", rep["scene_a"]["turns"][0]["reply"])
            self.assertFalse(rep["scene_b"]["available"])
            self.assertIn("skipped", rep["scene_b"]["note"])
            self.assertIn("DETERMINISTIC SIMULATION", rep["scene_c"]["title"])
            run = rep["scene_c"]["runs"][0]
            self.assertEqual(run["scenario"], "success")
            self.assertTrue(run["primary_tree_untouched"])
            text = render(rep)
            self.assertIn("## Scene A", text)
            self.assertIn("fake adapters", text)
            self.assertNotIn("/home/", json.dumps(rep["records"]))        # no private paths in the records section
            self.assertTrue((Path(tmp) / "world" / "zenith.db").exists())


if __name__ == "__main__":
    unittest.main()
