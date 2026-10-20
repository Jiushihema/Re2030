"""集成攻击场景：四类攻击/检测节点共用一个拓扑。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim2030.application import Application
from sim2030.scenario import load_scenario, validate_scenario

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIO_PATH = os.path.join(ROOT, "scenarios", "substation-integrated-attack.json")


class TestIntegratedAttackScenario(unittest.TestCase):
    def setUp(self):
        self.config = load_scenario(SCENARIO_PATH)
        self.devices = {device.device_id: device for device in self.config.devices}
        self.links = {link.link_id: link for link in self.config.links}

    def test_scene_validates_and_contains_all_nodes(self):
        self.assertEqual(validate_scenario(self.config), [])
        self.assertEqual(len(self.config.devices), 32)
        self.assertEqual(len(self.config.links), 37)
        self.assertEqual(
            {device_id for device_id, device in self.devices.items()
             if device.device_type in {"em_attack_source", "network_attack_source", "spectrum_detector"}},
            {
                "em_attack_node", "em_detect_node",
                "sensor_attack_node", "sensor_detect_node",
                "time_attack_node", "time_detect_node",
                "net_attack_node",
            },
        )

    def test_nodes_keep_source_positions(self):
        expected = {
            "em_attack_node": (465, 800),
            "em_detect_node": (600, 800),
            "sensor_attack_node": (1140, 728),
            "sensor_detect_node": (1260, 728),
            "time_attack_node": (1005, 180),
            "time_detect_node": (870, 150),
            "net_attack_node": (1295, 580),
        }
        for device_id, position in expected.items():
            layout = self.devices[device_id].layout
            self.assertEqual((layout["x"], layout["y"]), position, device_id)

        self.assertEqual(self.devices["em_detect_node"].parameters["mode"], "频谱检测")
        self.assertEqual(self.devices["sensor_detect_node"].parameters["mode"], "振动信号检测")
        self.assertEqual(self.devices["time_detect_node"].parameters["mode"], "授时信号检测")

    def test_interference_links_and_routes(self):
        expected = {
            "L-em-ct": (("em_attack_node", "emitter"), ("ct_current", "up")),
            "L-sensor-fan_vibration": (("sensor_attack_node", "emitter"), ("fan_vibration", "physical")),
            "L-time_attack-time_svc_2": (("time_attack_node", "emitter"), ("time_svc_2", "gnss")),
        }
        direct = self.config.layout["deployment"]["direct_links"]
        for link_id, (source, target) in expected.items():
            link = self.links[link_id]
            self.assertEqual(link.link_type, "interference")
            self.assertEqual(link.endpoint_a, source)
            self.assertEqual(link.endpoint_b, target)
            self.assertEqual(direct[link_id]["role"], "interference")
        self.assertEqual(direct["L-em-ct"]["points"], [{"x": 510, "y": 800}, {"x": 510, "y": 776}])
        self.assertEqual(direct["L-sensor-fan_vibration"]["points"],
                         [{"x": 1140, "y": 752}, {"x": 1095, "y": 752}])
        self.assertNotIn("points", direct["L-time_attack-time_svc_2"])

    def test_inherits_live_topology_and_goose_lab(self):
        protocols = {
            link.link_id: link.parameters.get("protocol")
            for link in self.config.links
            if link.parameters.get("protocol")
        }
        self.assertEqual(len(protocols), 14)
        self.assertEqual(protocols["L-station-mc"], "mms")
        self.assertEqual(protocols["L-mu-mc"], "sv")
        self.assertEqual(protocols["L-mc-brk"], "goose")
        self.assertEqual(protocols["L-mc-tap"], "goose")
        self.assertEqual(self.devices["diff_prot"].layout, {"x": 600, "y": 356})
        self.assertEqual(self.devices["fan_vibration"].layout["y"], 728)
        self.assertEqual(self.devices["fan_speed"].layout["y"], 588)
        process = next(layer for layer in self.config.layout["layers"] if layer["key"] == "process")
        self.assertEqual(process["height"], 320)
        self.assertEqual(
            self.config.layout["goose_lab"]["attack_origin"]["asset_id"], "net_attack_node"
        )
        self.assertIn("packet_replay", self.config.layout)

    def test_application_loads_nodes_and_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Application(output_root=os.path.join(tmp, "runs"))
            try:
                app.load(SCENARIO_PATH)
                self.assertIsNotNone(app.goose_lab)
                app.start()
                app.step()
                for device_id in ("em_attack_node", "sensor_attack_node", "time_attack_node"):
                    actions = [item["action"] for item in app.get_device_controls(device_id)["controls"]]
                    self.assertIn("start_attack", actions)
                net_actions = [item["action"] for item in app.get_device_controls("net_attack_node")["controls"]]
                self.assertIn("send_attack_frame", net_actions)
                self.assertEqual(app.status, "running")
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
