"""电磁攻击场景（电流互感器读数篡改 + 频谱检测节点）测试。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim2030.application import Application
from sim2030.contracts import DeviceSpec
from sim2030.scenario import create_device, load_scenario, validate_scenario

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIO_PATH = os.path.join(ROOT, "scenarios", "substation-em-attack.json")
LIVE_SCENARIO_PATH = os.path.join(ROOT, "scenarios", "substation-live.json")


class TestEmAttackScenario(unittest.TestCase):
    def test_loads_and_validates(self):
        config = load_scenario(SCENARIO_PATH)
        self.assertEqual(config.scenario_id, "substation-em-attack")
        self.assertEqual(validate_scenario(config), [])
        self.assertEqual(len(config.devices), 27)
        self.assertEqual(len(config.links), 35)
        self.assertEqual(len(config.attacks), 0)  # 攻击改为演示平面手动触发

        devices = {d.device_id: d for d in config.devices}
        self.assertEqual(devices["em_attack_node"].device_type, "em_attack_source")
        self.assertEqual(devices["spectrum_detect_node"].device_type, "spectrum_detector")
        self.assertEqual(devices["spectrum_detect_node"].layer, "process")
        self.assertEqual(devices["spectrum_detect_node"].layout["x"], 600)
        self.assertEqual(devices["spectrum_detect_node"].layout["y"], 800)
        self.assertEqual(devices["spectrum_detect_node"].layout["report_points"], [
            {"x": 690, "y": 824}, {"x": 1675, "y": 824},
            {"x": 1675, "y": 142}, {"x": 1630, "y": 142},
        ])
        self.assertEqual(devices["em_attack_node"].layout, {"x": 465, "y": 800})
        self.assertEqual(devices["em_attack_node"].parameters.get("target_asset_id"), "ct_current")

        interference = [link for link in config.links if link.link_type == "interference"]
        self.assertEqual(len(interference), 1)
        self.assertEqual(
            frozenset((interference[0].endpoint_a[0], interference[0].endpoint_b[0])),
            frozenset(("em_attack_node", "ct_current")),
        )

    def test_scene_is_synced_from_baseline(self):
        """除攻击节点、检测节点与干扰链路外，设备、链路、工况和部署都与基准场景一致。"""
        config = load_scenario(SCENARIO_PATH)
        baseline = load_scenario(LIVE_SCENARIO_PATH)
        self.assertEqual(
            {device.device_id for device in config.devices} - {device.device_id for device in baseline.devices},
            {"em_attack_node", "spectrum_detect_node"},
        )
        self.assertEqual(
            {link.link_id for link in config.links} - {link.link_id for link in baseline.links},
            {"L-em-ct"},
        )
        self.assertEqual(config.environment, baseline.environment)
        self.assertEqual(config.simulation, baseline.simulation)
        # 攻击节点和检测节点放在过程层最下方，过程层分层带向下加高 58 px 以容纳两者。
        for key in ("external", "station", "bay"):
            self.assertEqual(
                next(layer for layer in config.layout["layers"] if layer["key"] == key),
                next(layer for layer in baseline.layout["layers"] if layer["key"] == key),
            )
        process_band = next(layer for layer in config.layout["layers"] if layer["key"] == "process")
        baseline_process = next(layer for layer in baseline.layout["layers"] if layer["key"] == "process")
        self.assertEqual({k: v for k, v in process_band.items() if k != "height"},
                         {k: v for k, v in baseline_process.items() if k != "height"})
        self.assertEqual(process_band["height"], 320)
        self.assertEqual(
            config.layout["deployment"]["networks"], baseline.layout["deployment"]["networks"]
        )
        baseline_direct = baseline.layout["deployment"]["direct_links"]
        direct = config.layout["deployment"]["direct_links"]
        self.assertEqual(set(direct) - set(baseline_direct), {"L-em-ct"})
        for link_id, display in baseline_direct.items():
            self.assertEqual(direct[link_id], display, link_id)
        self.assertEqual(
            direct["L-em-ct"]["points"],
            [{"x": 510, "y": 800}, {"x": 510, "y": 776}],
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

    def test_manual_attack_offsets_one_ct_and_trips_differential_protection(self):
        """只篡改一套电流互感器会形成差流：先看到 +40 A 读数偏移，再触发差动保护跳闸。"""
        with tempfile.TemporaryDirectory() as tmp:
            app = Application(output_root=os.path.join(tmp, "runs"))
            try:
                app.load(SCENARIO_PATH)
                app.start()
                for _ in range(15):  # t=0.0s..1.4s：攻击前基线
                    app.step()
                before = app.get_view("management")["management"]
                self.assertAlmostEqual(
                    before["ct_current"]["state"]["last_value"],
                    before["ct_current_2"]["state"]["last_value"],
                    places=6,
                )
                self.assertEqual(before["brk"]["state"]["position"], "closed")

                app.submit_operation({"request_id": "a1", "target_asset_id": "em_attack_node",
                                      "action_type": "start_attack"})
                app.step()
                during = app.get_view("management")["management"]
                self.assertAlmostEqual(
                    during["ct_current"]["state"]["last_value"]
                    - during["ct_current_2"]["state"]["last_value"],
                    40.0,
                    places=6,
                )

                for _ in range(5):  # 差动保护越限并完成跳闸
                    app.step()
                after = app.get_view("management")["management"]
                self.assertEqual(after["brk"]["state"]["position"], "open")
                self.assertEqual(after["ct_current_2"]["state"]["last_value"], 0.0)
                differential = after["diff_prot"]["overview"]["last_differential"]
                self.assertTrue(differential["exceeded"])
                self.assertAlmostEqual(differential["difference"], 40.0, places=6)
            finally:
                app.close()

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
