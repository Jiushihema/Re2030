"""授时攻击场景：与基准同步的设备与链路，外加针对授时系统-2 的攻击节点与检测节点。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim2030.application import Application
from sim2030.scenario import load_scenario, validate_scenario

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_SCENARIO_PATH = os.path.join(ROOT, "scenarios", "substation-live.json")
TIME_ATTACK_SCENARIO_PATH = os.path.join(ROOT, "scenarios", "substation-time-attack.json")


class TestTimeAttackScenario(unittest.TestCase):
    def setUp(self):
        self.config = load_scenario(TIME_ATTACK_SCENARIO_PATH)
        self.baseline = load_scenario(LIVE_SCENARIO_PATH)
        self.devices = {device.device_id: device for device in self.config.devices}
        self.links = {link.link_id: link for link in self.config.links}

    def test_scenario_validates(self):
        self.assertEqual(validate_scenario(self.config), [])

    def test_scene_is_synced_from_baseline(self):
        """除攻击节点与检测节点外，设备、链路与工况设置都与基准场景一致。"""
        baseline_devices = {device.device_id for device in self.baseline.devices}
        self.assertEqual(
            set(self.devices) - baseline_devices, {"time_attack_node", "time_detect_node"}
        )
        baseline_links = {link.link_id for link in self.baseline.links}
        self.assertEqual(set(self.links) - baseline_links, {"L-time_attack-time_svc_2"})
        self.assertEqual(self.config.environment, self.baseline.environment)
        self.assertEqual(self.config.simulation, self.baseline.simulation)
        self.assertEqual(self.config.layout["layers"], self.baseline.layout["layers"])

        deployment = self.config.layout["deployment"]
        baseline_deployment = self.baseline.layout["deployment"]
        self.assertEqual(deployment["networks"], baseline_deployment["networks"])
        self.assertEqual(deployment["manager"], baseline_deployment["manager"])
        self.assertEqual(
            set(deployment["direct_links"]) - set(baseline_deployment["direct_links"]),
            {"L-time_attack-time_svc_2"},
        )
        for link_id, display in baseline_deployment["direct_links"].items():
            self.assertEqual(deployment["direct_links"][link_id], display, link_id)

    def test_attack_node_targets_the_second_time_service(self):
        """攻击节点放在授时系统-2 右侧、同一排，攻击目标指向授时系统-2 的时钟基准。"""
        attack = self.devices["time_attack_node"]
        time_svc_2 = self.devices["time_svc_2"]
        self.assertEqual(attack.device_type, "em_attack_source")
        self.assertEqual(attack.layer, "station")
        self.assertGreater(attack.layout["x"], time_svc_2.layout["x"], "攻击节点应在授时系统-2 右侧")
        self.assertEqual(attack.layout["y"], time_svc_2.layout["y"], "攻击节点与授时系统-2 同排")
        self.assertEqual(attack.parameters["target_asset_id"], "time_svc_2")
        self.assertEqual(attack.parameters["effect_type"], "timing_offset")
        self.assertEqual(attack.parameters["attack_type"], "timing")

        link = self.links["L-time_attack-time_svc_2"]
        self.assertEqual(link.link_type, "interference")
        self.assertEqual(link.endpoint_a, ("time_attack_node", "emitter"))
        self.assertEqual(link.endpoint_b, ("time_svc_2", "gnss"))

    def test_detection_node_sits_at_the_top_of_the_station_layer(self):
        """检测节点放在站控层、顶部与层带顶部对齐；干扰连线走直线，刚好从它下方穿过。"""
        detect = self.devices["time_detect_node"]
        attack = self.devices["time_attack_node"]
        time_svc_2 = self.devices["time_svc_2"]
        band = next(layer for layer in self.config.layout["layers"] if layer["key"] == "station")
        self.assertEqual(detect.device_type, "spectrum_detector")
        self.assertEqual(detect.layer, "station")
        self.assertEqual(detect.name, "检测节点")
        self.assertEqual(detect.layout["y"], band["y"], "检测节点顶部应与站控层顶部对齐")
        self.assertTrue(
            time_svc_2.layout["x"] + 90 < detect.layout["x"] < attack.layout["x"],
            "检测节点应落在授时系统-2 与攻击节点之间",
        )

        # 空档刚好够用：干扰连线不声明折线，按两端节点中心连成一条水平直线。
        display = self.config.layout["deployment"]["direct_links"]["L-time_attack-time_svc_2"]
        self.assertEqual(display["role"], "interference")
        self.assertNotIn("points", display, "两个节点之间的空档够用，干扰连线直接走直线")
        self.assertEqual(attack.layout["y"], time_svc_2.layout["y"], "两个节点同排，连线才是水平直线")
        line_y = time_svc_2.layout["y"] + 24
        clearance = line_y - (detect.layout["y"] + 48)
        self.assertGreaterEqual(clearance, 0, "直线不应穿过检测节点")
        self.assertLessEqual(clearance, 12, "空档刚好够用：直线贴着检测节点下方通过")
        self.assertAlmostEqual(
            detect.layout["x"] + 45, (attack.layout["x"] + time_svc_2.layout["x"]) / 2 + 45, delta=1,
            msg="检测节点应水平居中在连线的中点上方",
        )

        manager = self.config.layout["deployment"]["manager"]
        self.assertEqual(manager["id"], "security-manager")
        self.assertGreater(manager["x"], detect.layout["x"], "安全监测平台在检测节点右侧")

    def test_detection_node_has_no_business_links(self):
        """检测节点只做展示与上报：不接业务链路，也不进任何交换网络。"""
        self.assertEqual(
            [link.link_id for link in self.config.links
             if "time_detect_node" in (link.endpoint_a[0], link.endpoint_b[0])],
            [],
        )
        for network in self.config.layout["deployment"]["networks"]:
            self.assertNotIn("time_detect_node", [c["device_id"] for c in network["connections"]])


    def test_attack_node_spoofs_the_second_time_service(self):
        """触发攻击节点后，授时系统-2 输出被欺骗的时钟基准，授时系统 1 仍正常。"""
        with tempfile.TemporaryDirectory() as tmp:
            app = Application(output_root=os.path.join(tmp, "runs"))
            try:
                app.load(TIME_ATTACK_SCENARIO_PATH)
                app.start()
                app.step()
                control = app.get_device_controls("time_attack_node")["controls"][0]
                self.assertEqual(control["action"], "start_attack")

                receipt = app.submit_operation({"request_id": "t1", "target_asset_id": "time_attack_node",
                                                "action_type": "start_attack"})
                self.assertEqual(receipt["status"], "accepted")
                app.step()
                management = app.get_view("management")["management"]
                self.assertTrue(management["time_attack_node"]["state"]["emitting"])
                self.assertEqual(management["time_svc_2"]["state"]["sync_state"], "spoofed")
                self.assertEqual(management["time_svc_2"]["state"]["timing_offset_us"], -2000)
                self.assertEqual(management["time_svc"]["state"]["sync_state"], "synced",
                                 "第一套授时系统不应受这次攻击影响")

                app.submit_operation({"request_id": "t2", "target_asset_id": "time_attack_node",
                                      "action_type": "stop_attack"})
                app.step()
                management = app.get_view("management")["management"]
                self.assertEqual(management["time_svc_2"]["state"]["sync_state"], "synced")
                self.assertEqual(management["time_svc_2"]["state"]["timing_offset_us"], 0)
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
