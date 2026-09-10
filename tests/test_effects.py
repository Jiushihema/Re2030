"""攻击作用、链路影响与到期恢复测试。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim2030.base.engine import SimulationEngine
from sim2030.contracts import Capability, DefenseRequest, DeviceSpec, EffectRequest, LinkSpec


def sensor_receiver_engine():
    devices = [
        DeviceSpec(device_id="s1", device_type="sensor", layer="process", ports={"up": "up"},
                   parameters={"input_key": "ambient_temp_c", "unit": "C",
                               "sample_interval_us": 100000, "up_port": "up"}),
        DeviceSpec(device_id="acq", device_type="acquisition", layer="process", ports={"in": "in", "out": "out"},
                   parameters={"report_interval_us": 100000, "out_port": "out"}),
    ]
    links = [
        LinkSpec(link_id="L-s-acq", endpoint_a=("s1", "up"), endpoint_b=("acq", "in"),
                 link_type="wired", parameters={"delay_us": 0}),
    ]
    engine = SimulationEngine()
    engine.build(devices, links, {"ambient_temp_c": 30.0})
    return engine


class TestEffects(unittest.TestCase):
    def test_reading_offset_applied_to_sensor(self):
        engine = sensor_receiver_engine()
        engine.submit_effect(EffectRequest(
            effect_id="e1", target_id="s1", effect_type="reading_offset",
            parameters={"offset": 5.0}, start_time_us=0, end_time_us=1_000_000,
        ))
        engine.step(0, 100000)
        engine.step(100000, 100000)
        self.assertAlmostEqual(engine._devices["acq"]._latest["s1"]["value"], 35.0)

    def test_reading_offset_expires(self):
        engine = sensor_receiver_engine()
        engine.submit_effect(EffectRequest(
            effect_id="e1", target_id="s1", effect_type="reading_offset",
            parameters={"offset": 5.0}, start_time_us=0, end_time_us=100000,
        ))
        engine.step(0, 100000)
        self.assertEqual(engine._devices["s1"]._effects, {"reading_offset": 5.0})
        engine.step(100000, 100000)
        engine.step(200000, 100000)
        self.assertEqual(engine._devices["s1"]._effects, {})

    def test_execution_delay_delays_action(self):
        devices = [
            DeviceSpec(device_id="brk", device_type="switch", layer="process", ports={"control": "control"},
                       parameters={"control_port": "control", "default_duration_ms": 0},
                       capabilities=[Capability(action_type="close")]),
        ]
        engine = SimulationEngine()
        engine.build(devices, [], {"ambient_temp_c": 30.0})
        engine.submit_effect(EffectRequest(
            effect_id="e1", target_id="brk", effect_type="execution_delay",
            parameters={"delay_us": 500000}, start_time_us=0, end_time_us=2_000_000,
        ))
        engine.step(0, 100000)
        feedback = engine.submit_defense(DefenseRequest(
            action_id="a1", target_id="brk", action_type="close",
            parameters={}, valid_from_us=0,
        ))
        self.assertEqual(feedback.status, "accepted")
        self.assertEqual(feedback.feedback["scheduled_us"], 500000)

        for time_us in range(100000, 500000, 100000):
            engine.step(time_us, 100000)
        self.assertEqual(engine._devices["brk"].state["position"], "open")
        engine.step(500000, 100000)
        self.assertEqual(engine._devices["brk"].state["position"], "closed")

    def test_link_degradation_extra_delay(self):
        devices = [
            DeviceSpec(device_id="ts", device_type="time_service", layer="station", ports={"sync": "sync"},
                       parameters={"sync_port": "sync", "sync_interval_us": 1_000_000}),
            DeviceSpec(device_id="rx", device_type="acquisition", layer="process", ports={"in": "in", "out": "out"},
                       parameters={"report_interval_us": 100000, "out_port": "out"}),
        ]
        links = [
            LinkSpec(link_id="L-sync", endpoint_a=("ts", "sync"), endpoint_b=("rx", "in"),
                     link_type="wired", parameters={"delay_us": 1000}),
        ]
        engine = SimulationEngine()
        engine.build(devices, links, {"ambient_temp_c": 30.0})
        engine.submit_effect(EffectRequest(
            effect_id="e1", target_id="L-sync", effect_type="link_degradation",
            parameters={"extra_delay_us": 10000, "extra_loss_rate": 0.0},
            start_time_us=0, end_time_us=1_000_000,
        ))
        result = engine.step(0, 100000)
        sync_message = [m for m in result.messages if m.business_type == "sync"]
        self.assertTrue(sync_message)
        self.assertEqual(sync_message[0].deliver_time_us, 11000)


if __name__ == "__main__":
    unittest.main()
