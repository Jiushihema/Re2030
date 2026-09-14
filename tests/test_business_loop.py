"""底座正常业务闭环测试。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim2030.base.engine import SimulationEngine
from sim2030.contracts import Capability, DeviceSpec, LinkSpec, ScenarioConfig
from sim2030.scenario import load_scenario

SCENARIO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures", "substation.json")


def run_engine(config, end_us, dt_us=100000, operations=()):
    engine = SimulationEngine()
    engine.build(config.devices, config.links, config.environment)
    for operation in operations:
        engine.submit_business_operation(operation)
    messages = []
    for time_us in range(0, end_us + dt_us, dt_us):
        result = engine.step(time_us, dt_us)
        messages.extend(result.messages)
    return engine, messages


class TestBusinessLoop(unittest.TestCase):
    def test_substation_scenario_runs_and_closes_breaker(self):
        config = load_scenario(SCENARIO_PATH)
        operations = [
            {"time_us": 0, "target_asset_id": "brk", "action_type": "close", "parameters": {}},
        ]
        engine, messages = run_engine(config, end_us=2_000_000, operations=operations)
        self.assertEqual(engine._devices["brk"].state["position"], "closed")

    def test_sense_acquire_loop_collects_samples(self):
        config = load_scenario(SCENARIO_PATH)
        engine, _messages = run_engine(config, end_us=1_000_000)
        latest = engine._devices["mu"]._latest
        self.assertIn("ct_current", latest)
        self.assertIn("vt_voltage", latest)
        self.assertIn("oil_temp", latest)

    def test_command_feedback_loop_reaches_station(self):
        config = load_scenario(SCENARIO_PATH)
        engine, messages = run_engine(
            config,
            end_us=2_000_000,
            operations=[{"time_us": 0, "target_asset_id": "brk", "action_type": "close", "parameters": {}}],
        )
        feedback = [m for m in messages if m.business_type == "feedback" and m.payload.get("status") == "completed"]
        self.assertTrue(feedback)
        # 站端应至少收到一条来自测控装置转发的执行反馈。
        feedback_to_station = [m for m in messages if m.business_type == "feedback" and m.receiver_id == "station"]
        self.assertTrue(feedback_to_station)

    def test_tap_and_cooling_operations(self):
        config = load_scenario(SCENARIO_PATH)
        operations = [
            {"time_us": 0, "target_asset_id": "tap", "action_type": "set_tap", "parameters": {"tap_position": 7}},
            {"time_us": 0, "target_asset_id": "cool", "action_type": "start", "parameters": {}},
        ]
        engine, _messages = run_engine(config, end_us=2_000_000, operations=operations)
        self.assertEqual(engine._devices["tap"].state["tap_position"], 7)
        self.assertTrue(engine._devices["cool"].state["running"])

    def test_periodic_sampling_order(self):
        config = load_scenario(SCENARIO_PATH)
        engine, messages = run_engine(config, end_us=1_000_000)
        sampling = [m for m in messages if m.business_type == "sampling" and m.sender_id == "ct_current"]
        self.assertGreaterEqual(len(sampling), 1)
        times = [m.created_time_us for m in sampling]
        self.assertEqual(times, sorted(times))


if __name__ == "__main__":
    unittest.main()
