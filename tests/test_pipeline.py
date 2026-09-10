"""攻击/识别/防御/演示流水线顺序与记录生成测试。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim2030.application import Application, STATUS_FINISHED
from sim2030.records import RunReader
from tests.support import make_observation_event, write_jsonl, write_pipeline_scenario


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        self.provider_dir = os.path.join(self.base, "providers")
        os.makedirs(self.provider_dir, exist_ok=True)
        write_jsonl(os.path.join(self.provider_dir, "events.jsonl"), [make_observation_event()])
        self.scenario_path = write_pipeline_scenario(self.base, self.provider_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        app = Application(output_root=os.path.join(self.base, "runs"))
        app.load(self.scenario_path)
        app.run_to_end()
        return app, RunReader(os.path.join(self.base, "runs", app.run_id))

    def test_full_pipeline_records_each_plane(self):
        app, reader = self._run()
        self.assertEqual(app.status, STATUS_FINISHED)

        attack_submissions = [r for r in reader.read("attack") if r.get("type") == "attack_submission"]
        self.assertGreaterEqual(len(attack_submissions), 1)
        self.assertEqual(attack_submissions[0]["status"], "accepted")

        recognitions = reader.read("recognition")
        self.assertGreater(len(recognitions), 0)
        self.assertTrue(any(r.get("attack_detected") for r in recognitions))

        defenses = reader.read("defense")
        self.assertGreater(len(defenses), 0)

        observation_records = reader.read("observation")
        self.assertTrue(any(r.get("type") == "observation_batch" for r in observation_records))
        events = reader.evidence()
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0]["raw"]["observed_at"], "2026-09-09T00:00:00Z")

    def test_recognition_precedes_defense_in_time(self):
        _, reader = self._run()
        recognitions = [r for r in reader.read("recognition") if r.get("attack_detected")]
        defenses = reader.read("defense")
        self.assertTrue(recognitions and defenses)
        first_recognition = min(int(r.get("time_us", 0)) for r in recognitions)
        first_defense = min(int(r.get("time_us", 0)) for r in defenses)
        self.assertLessEqual(first_recognition, first_defense)


if __name__ == "__main__":
    unittest.main()
