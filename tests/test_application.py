"""运行调度、场景操作自动执行与记录读取测试。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim2030.application import Application, STATUS_FINISHED, STATUS_READY
from sim2030.records import RunReader

SCENARIO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures", "substation.json")


class TestApplication(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.output_root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _load(self):
        app = Application(output_root=self.output_root)
        app.load(SCENARIO_PATH)
        return app

    def test_load_creates_run_id(self):
        app = self._load()
        self.assertTrue(app.run_id)
        self.assertEqual(app.status, STATUS_READY)

    def test_load_rejects_invalid_scenario(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
            json.dump({
                "scenario_id": "bad",
                "simulation": {"dt_us": 100000, "duration_us": 1000000},
                "devices": [{"device_id": "x", "device_type": "unknown_type", "layer": "process"}],
                "links": [],
            }, handle)
            bad_path = handle.name
        try:
            app = Application(output_root=self.output_root)
            with self.assertRaises(ValueError):
                app.load(bad_path)
        finally:
            os.unlink(bad_path)

    def test_run_to_end_executes_scenario_operations(self):
        app = self._load()
        app.run_to_end()
        self.assertEqual(app.status, STATUS_FINISHED)
        self.assertEqual(app.engine._devices["brk"].state["position"], "closed")
        self.assertEqual(app.engine._devices["tap"].state["tap_position"], 7)
        self.assertTrue(app.engine._devices["cool"].state["running"])

    def test_submit_operation_rejects_unknown_target(self):
        app = self._load()
        receipt = app.submit_operation({"target_asset_id": "ghost", "action_type": "close"})
        self.assertEqual(receipt["status"], "rejected")

    def test_records_written_and_readable(self):
        app = self._load()
        app.run_to_end()
        run_dir = os.path.join(app.output_root, app.run_id)
        self.assertTrue(os.path.exists(os.path.join(run_dir, "manifest.json")))

        reader = RunReader(run_dir)
        business = reader.read("business")
        self.assertGreater(len(business), 0)

        snapshots = reader.snapshot_at(0)
        self.assertIn("brk", snapshots)


if __name__ == "__main__":
    unittest.main()
