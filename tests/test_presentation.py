"""演示平面接口与视图一致性测试。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim2030.application import Application
from sim2030.presentation import views as presentation_views
from sim2030.presentation.server import PresentationServer
from sim2030.records import RunReader
from tests.support import make_observation_event, write_jsonl, write_pipeline_scenario

SUBSTATION_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures", "substation.json")


class TestPresentationServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        self.provider_dir = os.path.join(self.base, "providers")
        os.makedirs(os.path.join(self.provider_dir, "evidence"), exist_ok=True)
        write_jsonl(os.path.join(self.provider_dir, "events.jsonl"), [make_observation_event()])
        with open(os.path.join(self.provider_dir, "evidence", "business.csv"), "w", encoding="utf-8") as handle:
            handle.write("metric_name,value\n")
            handle.write("oil_temp_c,120.0\n")
        self.scenario_path = write_pipeline_scenario(self.base, self.provider_dir)
        self.server = PresentationServer(scenario_dir=self.base, output_root=os.path.join(self.base, "runs"))

    def tearDown(self):
        if hasattr(self, 'server'):
            self.server.close()
        self.tmp.cleanup()

    def test_scenarios_endpoint(self):
        status, payload, _ = self.server.handle_request("GET", "/api/scenarios")
        self.assertEqual(status, 200)
        self.assertTrue(any(s["scenario_id"] == "pipeline-001" for s in payload["scenarios"]))

    def test_create_run_and_idempotent_operation(self):
        status, payload, _ = self.server.handle_request("POST", "/api/runs", json.dumps({"scenario_id": "pipeline-001"}).encode())
        self.assertEqual(status, 201)
        run_id = payload["run_id"]
        self.assertEqual(payload["status"], "ready")

        op = {"request_id": "req-1", "target_asset_id": "brk", "action_type": "close"}
        status, receipt, _ = self.server.handle_request("POST", f"/api/runs/{run_id}/operations", json.dumps(op).encode())
        self.assertEqual(status, 200)
        self.assertEqual(receipt["status"], "queued")

        status, duplicate, _ = self.server.handle_request("POST", f"/api/runs/{run_id}/operations", json.dumps(op).encode())
        self.assertEqual(duplicate["status"], "duplicate")

    def test_active_views_endpoint(self):
        _, created, _ = self.server.handle_request("POST", "/api/runs", json.dumps({"scenario_id": "pipeline-001"}).encode())
        run_id = created["run_id"]
        status, payload, _ = self.server.handle_request("GET", f"/api/runs/{run_id}/views?view=system")
        self.assertEqual(status, 200)
        self.assertEqual(payload["view"], "system")
        self.assertIn("topology", payload["data"])

    def test_evidence_endpoint_for_finished_run(self):
        app = Application(output_root=os.path.join(self.base, "runs"))
        app.load(self.scenario_path)
        app.run_to_end()
        run_id = app.run_id

        status, payload, _ = self.server.handle_request(
            "GET", f"/api/runs/{run_id}/evidence?provider_id=p-business&event_id=e-1&start=0&count=5"
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["evidence"]), 1)
        segment = payload["evidence"][0]["segment"]
        self.assertEqual(segment["status"], "ok")
        self.assertEqual(segment["records"][0]["value"], "120.0")


class TestPresentationViews(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        self.provider_dir = os.path.join(self.base, "providers")
        os.makedirs(self.provider_dir, exist_ok=True)
        write_jsonl(os.path.join(self.provider_dir, "events.jsonl"), [make_observation_event()])
        self.scenario_path = write_pipeline_scenario(self.base, self.provider_dir)

    def tearDown(self):
        if hasattr(self, 'server'):
            self.server.close()
        self.tmp.cleanup()

    def test_views_match_records(self):
        app = Application(output_root=os.path.join(self.base, "runs"))
        app.load(self.scenario_path)
        app.run_to_end()
        run_dir = os.path.join(self.base, "runs", app.run_id)

        system = presentation_views.build_system_view(run_dir)
        reader = RunReader(run_dir)
        self.assertEqual(system["observations"]["event_count"], len(reader.evidence()))
        self.assertGreaterEqual(len(system["topology"]["devices"]), 1)
        self.assertIn("management", system)
        self.assertIn("environment", system)
        self.assertIn("in_flight_messages", system)
        self.assertTrue(any(device.get("name") for device in system["topology"]["devices"]))

        timeline = presentation_views.build_timeline(run_dir)
        self.assertGreaterEqual(len(timeline["attacks"]), 1)
        self.assertGreaterEqual(len(timeline["recognitions"]), 1)

        comparison = presentation_views.build_comparison([{"scenario_id": "x", "business_metrics": {"impact_reduction_rate": 0.5}, "overall_score": 0.6}])
        self.assertEqual(comparison["runs"][0]["scenario_id"], "x")


if __name__ == "__main__":
    unittest.main()
