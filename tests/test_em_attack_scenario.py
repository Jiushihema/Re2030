"""电磁攻击场景（电流互感器读数篡改 + 频谱检测节点）测试。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim2030.application import Application
from sim2030.contracts import DeviceSpec
from sim2030.records import RunReader
from sim2030.scenario import create_device, load_scenario, validate_scenario

SCENARIO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scenarios",
    "substation-em-attack.json",
)


class TestEmAttackScenario(unittest.TestCase):
    def test_loads_and_validates(self):
        config = load_scenario(SCENARIO_PATH)
        self.assertEqual(config.scenario_id, "substation-em-attack")
        self.assertEqual(validate_scenario(config), [])
        self.assertEqual(len(config.devices), 19)
        self.assertEqual(len(config.links), 20)
        self.assertEqual(len(config.attacks), 0)  # 攻击改为演示平面手动触发

        devices = {d.device_id: d for d in config.devices}
        self.assertEqual(devices["em_attack_node"].device_type, "em_attack_source")
        self.assertEqual(devices["spectrum_detect_node"].device_type, "spectrum_detector")
        self.assertEqual(devices["em_attack_node"].parameters.get("target_asset_id"), "ct_current")

        interference = [link for link in config.links if link.link_type == "interference"]
        self.assertEqual(len(interference), 1)
        self.assertEqual(
            frozenset((interference[0].endpoint_a[0], interference[0].endpoint_b[0])),
            frozenset(("em_attack_node", "ct_current")),
        )

    def test_attack_node_snapshot_exposes_target(self):
        node = create_device(DeviceSpec(device_id="em_attack_node", device_type="em_attack_source",
                                        layer="process", parameters={"target_asset_id": "ct_current",
                                                                     "frequency_mhz": 900.0}))
        snap = node.snapshot()
        self.assertEqual(snap["state"]["target_asset_id"], "ct_current")
        self.assertEqual(snap["state"]["frequency_mhz"], 900.0)

    def test_detector_node_is_placeholder(self):
        node = create_device(DeviceSpec(device_id="spectrum_detect_node", device_type="spectrum_detector",
                                        layer="process", parameters={"mode": "频谱检测"}))
        self.assertEqual(node.snapshot()["state"]["mode"], "频谱检测")

    def test_controls_toggle_start_and_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Application(output_root=os.path.join(tmp, "runs"))
            app.load(SCENARIO_PATH)
            app.start()

            control = app.get_device_controls("em_attack_node")["controls"][0]
            self.assertEqual(control["action"], "start_attack")
            self.assertEqual(control["label"], "开始攻击")

            receipt = app.submit_operation({"request_id": "m1", "target_asset_id": "em_attack_node",
                                            "action_type": "start_attack"})
            self.assertEqual(receipt["status"], "accepted")
            control = app.get_device_controls("em_attack_node")["controls"][0]
            self.assertEqual(control["action"], "stop_attack")
            self.assertEqual(control["label"], "结束攻击")

            app.step()
            management = app.get_view("management")["management"]
            self.assertEqual(management["ct_current"]["effects"], {"reading_offset": 40.0})
            self.assertTrue(management["em_attack_node"]["state"]["emitting"])

            receipt = app.submit_operation({"request_id": "m2", "target_asset_id": "em_attack_node",
                                            "action_type": "stop_attack"})
            self.assertEqual(receipt["status"], "accepted")
            management = app.get_view("management")["management"]
            self.assertEqual(management["ct_current"]["effects"], {})
            self.assertFalse(management["em_attack_node"]["state"]["emitting"])
            app.close()

    def test_repeated_start_attack_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Application(output_root=os.path.join(tmp, "runs"))
            app.load(SCENARIO_PATH)
            app.start()
            app.submit_operation({"request_id": "m1", "target_asset_id": "em_attack_node",
                                  "action_type": "start_attack"})
            again = app.submit_operation({"request_id": "m2", "target_asset_id": "em_attack_node",
                                          "action_type": "start_attack"})
            self.assertEqual(again["status"], "noop")
            app.close()

    def test_manual_attack_offsets_ct_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = os.path.join(tmp, "runs")
            app = Application(output_root=run_root)
            app.load(SCENARIO_PATH)
            app.start()
            for _ in range(15):  # t=0.0s..1.4s：攻击前基线
                app.step()
            app.submit_operation({"request_id": "a1", "target_asset_id": "em_attack_node",
                                  "action_type": "start_attack"})
            for _ in range(10):  # t=1.5s..2.4s：攻击生效
                app.step()
            app.submit_operation({"request_id": "a2", "target_asset_id": "em_attack_node",
                                  "action_type": "stop_attack"})
            for _ in range(5):   # t=2.5s..2.9s：攻击结束
                app.step()
            app.close()

            reader = RunReader(os.path.join(run_root, app.run_id))
            samples = []
            for record in reader.read("business"):
                payload = record.get("payload") or {}
                value = payload.get("value")
                if payload.get("sensor_id") == "ct_current" and isinstance(value, (int, float)):
                    # 用采样时刻而非记录时刻，剔除投递延迟带来的跨窗口误差
                    sample_time = payload.get("sample_time_us", record.get("time_us", 0))
                    samples.append((int(sample_time), float(value)))
            self.assertTrue(samples, "未采集到电流互感器采样")

            def mean_in(lo_us: int, hi_us: int) -> float:
                values = [v for t, v in samples if lo_us <= t <= hi_us]
                self.assertTrue(values, f"窗口 {lo_us}-{hi_us} 无采样")
                return sum(values) / len(values)

            pre = mean_in(1_000_000, 1_400_000)
            during = mean_in(1_500_000, 2_400_000)
            post = mean_in(2_500_000, 2_900_000)
            self.assertAlmostEqual(during - pre, 40.0, places=2)
            self.assertAlmostEqual(post, pre, places=2)

    def test_sensor_reading_differs_from_actual_under_attack(self):
        """传感器上报的是“度数”，攻击期间应与工况“实际值”不同。"""
        with tempfile.TemporaryDirectory() as tmp:
            app = Application(output_root=os.path.join(tmp, "runs"))
            app.load(SCENARIO_PATH)
            app.start()
            for _ in range(15):
                app.step()

            view = app.get_view("system")
            reading = view["management"]["ct_current"]["state"]["last_value"]
            actual = view["environment"]["line_current_a"]
            self.assertAlmostEqual(reading, actual, places=6)
            self.assertIn("active_power_mw", view["environment"])

            app.submit_operation({"request_id": "s1", "target_asset_id": "em_attack_node",
                                  "action_type": "start_attack"})
            for _ in range(3):
                app.step()
            view = app.get_view("system")
            reading = view["management"]["ct_current"]["state"]["last_value"]
            actual = view["environment"]["line_current_a"]
            self.assertAlmostEqual(reading - actual, 40.0, places=2)
            app.close()


if __name__ == "__main__":
    unittest.main()
