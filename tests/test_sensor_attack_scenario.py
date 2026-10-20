"""风机专用传感器与合并单元的接入测试。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim2030.application import Application
from sim2030.records import RunReader
from sim2030.scenario import load_scenario, validate_scenario


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIO_PATH = os.path.join(ROOT, "scenarios", "substation-sensor-attack.json")
LIVE_SCENARIO_PATH = os.path.join(ROOT, "scenarios", "substation-live.json")


class TestSensorAttackScenario(unittest.TestCase):
    def test_scene_is_synced_from_baseline(self):
        """除攻击节点与干扰链路外，设备、链路、工况和部署都与基准场景一致。"""
        config = load_scenario(SCENARIO_PATH)
        baseline = load_scenario(LIVE_SCENARIO_PATH)
        self.assertEqual(validate_scenario(config), [])
        self.assertEqual(
            {device.device_id for device in config.devices} - {device.device_id for device in baseline.devices},
            {"sensor_attack_node", "spectrum_detect_node"},
        )
        self.assertEqual(
            {link.link_id for link in config.links} - {link.link_id for link in baseline.links},
            {"L-sensor-fan_vibration"},
        )
        self.assertEqual(config.environment, baseline.environment)
        self.assertEqual(config.simulation, baseline.simulation)
        self.assertEqual(config.layout["layers"], baseline.layout["layers"])
        self.assertEqual(
            config.layout["deployment"]["networks"], baseline.layout["deployment"]["networks"]
        )
        self.assertEqual(
            config.layout["deployment"]["manager"], baseline.layout["deployment"]["manager"]
        )
        self.assertEqual(
            set(config.layout["deployment"]["direct_links"])
            - set(baseline.layout["deployment"]["direct_links"]),
            {"L-sensor-fan_vibration"},
        )

    def test_attack_node_targets_the_vibration_sensor(self):
        """攻击读数偏移施加在风机振动传感器上，干扰路径从攻击节点接到该传感器。"""
        config = load_scenario(SCENARIO_PATH)
        devices = {device.device_id: device for device in config.devices}
        links = {link.link_id: link for link in config.links}
        attack = devices["sensor_attack_node"]
        self.assertEqual(attack.device_type, "em_attack_source")
        self.assertEqual(attack.layer, "process")
        self.assertEqual(attack.name, "传感器攻击节点")
        self.assertEqual(attack.layout, {"x": 1140, "y": 588})
        self.assertEqual(attack.parameters["target_asset_id"], "fan_vibration")
        self.assertEqual(attack.parameters["attack_type"], "sensor")
        self.assertEqual(devices["fan_vibration"].device_type, "sensor")

        detector = devices["spectrum_detect_node"]
        self.assertEqual(detector.device_type, "spectrum_detector")
        self.assertEqual(detector.layer, "process")
        self.assertEqual(detector.layout["x"], 1260)
        self.assertEqual(detector.layout["y"], 588)
        self.assertEqual(detector.parameters["mode"], "振动信号检测")
        self.assertEqual(detector.layout["report_points"], [
            {"x": 1350, "y": 612}, {"x": 1675, "y": 612},
            {"x": 1675, "y": 142}, {"x": 1630, "y": 142},
        ])

        link = links["L-sensor-fan_vibration"]
        self.assertEqual(link.link_type, "interference")
        self.assertEqual(link.endpoint_a, ("sensor_attack_node", "emitter"))
        self.assertEqual(link.endpoint_b, ("fan_vibration", "physical"))
        display = config.layout["deployment"]["direct_links"]["L-sensor-fan_vibration"]
        self.assertEqual(display["role"], "interference")
        self.assertEqual(display["points"], [{"x": 1140, "y": 612}, {"x": 1095, "y": 612}])

    def test_fan_sensors_feed_a_dedicated_merging_unit(self):
        config = load_scenario(SCENARIO_PATH)
        self.assertEqual(validate_scenario(config), [])

        devices = {device.device_id: device for device in config.devices}
        self.assertEqual(
            [devices[key].name for key in ("fan_vibration", "fan_current", "fan_speed")],
            ["风机振动传感器", "风机电流传感器", "风机转速传感器"],
        )
        self.assertEqual(devices["fan_condition_monitor"].name, "风机状态监测采集装置")
        self.assertEqual(devices["fan_condition_monitor"].device_type, "acquisition")
        fan_sensor_links = {
            link.link_id for link in config.links
            if link.endpoint_b[0] == "fan_condition_monitor"
        }
        self.assertEqual(fan_sensor_links, {
            "L-fan-vibration-monitor",
            "L-fan-current-monitor",
            "L-fan-speed-monitor",
        })

    def test_running_fan_readings_are_collected_by_its_merging_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Application(output_root=os.path.join(tmp, "runs"))
            app.load(SCENARIO_PATH)
            app.start()
            receipt = app.submit_operation({
                "request_id": "start-fan",
                "target_asset_id": "cool",
                "action_type": "start",
            })
            self.assertEqual(receipt["status"], "queued")
            for _ in range(12):
                app.step()
            merged_samples = app.engine.get_device("fan_condition_monitor")._latest
            self.assertTrue({"fan_vibration", "fan_current", "fan_speed"}.issubset(merged_samples))
            run_id = app.run_id
            app.close()

            records = list(RunReader(os.path.join(tmp, "runs", run_id)).read("business"))
            sensor_ids = {"fan_vibration", "fan_current", "fan_speed"}
            sensor_values = {
                (record.get("payload") or {}).get("sensor_id"): (record.get("payload") or {}).get("value")
                for record in records
                if (record.get("payload") or {}).get("sensor_id") in sensor_ids
            }
            self.assertEqual(set(sensor_values), sensor_ids)
            self.assertGreater(sensor_values["fan_vibration"], 0)
            self.assertGreater(sensor_values["fan_current"], 0)
            self.assertGreater(sensor_values["fan_speed"], 0)

    def test_manual_attack_offsets_only_the_vibration_reading(self):
        """手动攻击只改变振动传感器读数，电流与转速读数保持实际值；结束攻击后恢复。"""
        with tempfile.TemporaryDirectory() as tmp:
            app = Application(output_root=os.path.join(tmp, "runs"))
            try:
                app.load(SCENARIO_PATH)
                app.start()
                app.submit_operation({
                    "request_id": "start-fan",
                    "target_asset_id": "cool",
                    "action_type": "start",
                })
                for _ in range(12):
                    app.step()

                def readings():
                    management = app.get_view("management")["management"]
                    return {
                        key: management[key]["state"].get("last_value")
                        for key in ("fan_vibration", "fan_current", "fan_speed")
                    }

                before = readings()
                self.assertEqual(before, {"fan_vibration": 3.2, "fan_current": 8.5, "fan_speed": 1480.0})

                receipt = app.submit_operation({
                    "request_id": "start-attack",
                    "target_asset_id": "sensor_attack_node",
                    "action_type": "start_attack",
                })
                self.assertEqual(receipt["status"], "accepted")
                for _ in range(2):
                    app.step()
                during = readings()
                self.assertAlmostEqual(during["fan_vibration"], before["fan_vibration"] + 40.0, places=6)
                self.assertEqual(during["fan_current"], before["fan_current"])
                self.assertEqual(during["fan_speed"], before["fan_speed"])

                app.submit_operation({
                    "request_id": "stop-attack",
                    "target_asset_id": "sensor_attack_node",
                    "action_type": "stop_attack",
                })
                for _ in range(2):
                    app.step()
                self.assertEqual(readings(), before)
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
