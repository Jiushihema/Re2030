import json
from pathlib import Path
import tempfile
import unittest

from sim2030.application import Application
from sim2030.scenario import load_scenario, validate_scenario
from sim2030.presentation.server import PresentationServer

ROOT = Path(__file__).resolve().parents[1]
SCENE = ROOT / "scenarios/substation-network-attack.json"


class TestNetworkAttackPresentation(unittest.TestCase):
    def assertSettingsSubset(self, demonstration, baseline, path):
        """展示场景不得引入基准以外的设置；同名字段必须与基准一致（含嵌套对象）。"""
        if isinstance(demonstration, dict) and isinstance(baseline, dict):
            self.assertLessEqual(set(demonstration), set(baseline), f"{path} 不得引入基准以外的字段")
            for key, value in demonstration.items():
                self.assertSettingsSubset(value, baseline[key], f"{path}.{key}")
            return
        self.assertEqual(demonstration, baseline, path)

    def test_scene_keeps_all_baseline_simulation_settings(self):
        """展示场景只增加 cyber_demo 版式，不得改动基准的业务仿真设置。

        基准场景会持续演进（复数授时链路、冷却风机状态监测组、额定负荷与热时间常数
        等），而展示场景不跟随这些演进；因此这里按“仿真设置一致 + 工况字段与自动操作
        不改写基准 + 设备与链路只能是基准的子集”核对，既不放松对仿真配置的约束，
        也不把基准自身的演进误判为展示场景改了配置。
        """
        baseline = json.loads((ROOT / "scenarios/substation-live.json").read_text(encoding="utf-8"))
        demonstration = json.loads(SCENE.read_text(encoding="utf-8"))
        self.assertEqual(demonstration["layout"]["cyber_demo"]["mode"], "presentation_only")
        demonstration["layout"].pop("cyber_demo")
        for item in (baseline, demonstration):
            item.pop("scenario_id")
            item.pop("name")

        for key in ("simulation", "observation_mode", "seed"):
            self.assertEqual(demonstration.get(key), baseline.get(key), key)

        self.assertSettingsSubset(
            demonstration.get("environment") or {}, baseline.get("environment") or {}, "environment"
        )
        baseline_operations = baseline.get("operations") or []
        for operation in demonstration.get("operations") or []:
            self.assertIn(operation, baseline_operations, "展示场景不得引入基准以外的自动操作")

        baseline_devices = {device["device_id"] for device in baseline["devices"]}
        demonstration_devices = {device["device_id"] for device in demonstration["devices"]}
        self.assertLessEqual(demonstration_devices, baseline_devices, "展示场景不得引入基准以外的设备")

        baseline_links = {link["link_id"] for link in baseline["links"]}
        demonstration_links = {link["link_id"] for link in demonstration["links"]}
        self.assertLessEqual(demonstration_links, baseline_links, "展示场景不得引入基准以外的链路")

        for key in ("attacks", "recognition", "defense"):
            self.assertNotIn(key, demonstration, f"展示场景不得启用 {key}")

    def test_scene_validates_and_is_hidden_from_scenario_list(self):
        """展示场景按既有策略不进场景列表，但仍可按 ID 创建运行。"""
        self.assertEqual(validate_scenario(load_scenario(str(SCENE))), [])
        with tempfile.TemporaryDirectory() as tmp:
            server = PresentationServer(str(ROOT / "scenarios"), tmp)
            try:
                code, body, _ = server.handle_request("GET", "/api/scenarios")
                self.assertEqual(code, 200)
                self.assertNotIn(
                    "substation-network-attack",
                    {item["scenario_id"] for item in body["scenarios"]},
                )
                code, created, _ = server.handle_request(
                    "POST", "/api/runs",
                    json.dumps({"scenario_id": "substation-network-attack"}).encode("utf-8"),
                )
                self.assertEqual(code, 201)
                self.assertEqual(created["scenario_id"], "substation-network-attack")
            finally:
                server.close()

    def test_running_scene_does_not_inject_network_attacks_or_ids_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Application(output_root=tmp)
            try:
                app.load(str(SCENE))
                app.start()
                for _ in range(10):
                    app.step()
                view = app.get_view("system")
                self.assertEqual(view["topology"]["layout"]["cyber_demo"]["mode"], "presentation_only")
                self.assertEqual(view["recognition"], [])
                self.assertFalse(any(event["source_type"] == "ids" for event in view["observations"]["events"]))
                self.assertFalse(any(d["effects"] for d in view["management"].values()))
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
