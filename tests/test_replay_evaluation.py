"""回放与独立评估测试：回放不改真值，评估证据不足时可解释。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim2030.application import Application
from sim2030.evaluation import Evaluator
from sim2030.records import RunReader
from tests.support import make_observation_event, write_jsonl, write_pipeline_scenario

SUBSTATION_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures", "substation.json")


class TestReplayEvaluation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _run_pipeline(self):
        provider = os.path.join(self.base, "providers")
        os.makedirs(provider, exist_ok=True)
        write_jsonl(os.path.join(provider, "events.jsonl"), [make_observation_event()])
        scenario = write_pipeline_scenario(self.base, provider)
        app = Application(output_root=os.path.join(self.base, "runs"))
        app.load(scenario)
        app.run_to_end()
        return app

    def test_replay_does_not_rerun_attacks(self):
        app = self._run_pipeline()
        run_dir = os.path.join(self.base, "runs", app.run_id)
        before = RunReader(run_dir).read("attack")
        replay = app.replay(app.run_id, 0)
        after = RunReader(run_dir).read("attack")
        self.assertEqual(len(before), len(after))
        self.assertIn("device_snapshots", replay)

    def test_evaluator_marks_no_attack_not_evaluable(self):
        app = Application(output_root=os.path.join(self.base, "runs"))
        app.load(SUBSTATION_PATH)
        app.run_to_end()
        result = Evaluator().evaluate(os.path.join(self.base, "runs", app.run_id))
        self.assertFalse(result.recognition_metrics.get("evaluable"))
        self.assertIn("reason", result.recognition_metrics)
        self.assertIsNone(result.overall_score)

    def test_compare_computes_impact_reduction(self):
        evaluator = Evaluator()
        baseline = {
            "scenario_id": "base",
            "business_metrics": {"maximum_deviation": 10.0},
            "recognition_metrics": {},
            "defense_metrics": {},
            "overall_score": None,
        }
        defended = {
            "scenario_id": "def",
            "business_metrics": {"maximum_deviation": 3.0},
            "recognition_metrics": {},
            "defense_metrics": {},
            "overall_score": None,
        }
        result = evaluator.compare(baseline, defended)
        self.assertAlmostEqual(result.business_metrics["impact_reduction_rate"], 0.7)


if __name__ == "__main__":
    unittest.main()
