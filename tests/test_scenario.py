"""场景配置读取、校验与设备工厂注册测试。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim2030.contracts import Capability, DeviceSpec, LinkSpec, ScenarioConfig
from sim2030.scenario import (
    DEVICE_TYPES,
    create_device,
    load_scenario,
    register_device,
    validate_scenario,
)

SCENARIO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures", "substation.json")
LIVE_SCENARIO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scenarios", "substation-live.json")


def make_config() -> ScenarioConfig:
    devices = [
        DeviceSpec(device_id="brk", device_type="switch", layer="process", ports={"control": "control"},
                   capabilities=[Capability(action_type="close"), Capability(action_type="open")]),
        DeviceSpec(device_id="s1", device_type="sensor", layer="process", ports={"up": "up"}),
    ]
    links = [
        LinkSpec(link_id="l1", endpoint_a=("s1", "up"), endpoint_b=("brk", "control"),
                 link_type="hardwire", parameters={"delay_us": 0}),
    ]
    return ScenarioConfig(scenario_id="s", simulation={"dt_us": 100000, "duration_us": 1000000},
                          devices=devices, links=links)


class TestLoadScenario(unittest.TestCase):
    def test_load_substation_scenario(self):
        config = load_scenario(SCENARIO_PATH)
        self.assertEqual(config.scenario_id, "substation-001")
        self.assertEqual(len(config.devices), 15)
        self.assertEqual(len(config.links), 17)
        self.assertTrue(all(isinstance(d, DeviceSpec) for d in config.devices))
        self.assertTrue(all(isinstance(l, LinkSpec) for l in config.links))

    def test_load_resolves_relative_paths(self):
        config = load_scenario(SCENARIO_PATH)
        schema_path = config.observations.get("schema_path", "")
        self.assertTrue(os.path.isabs(schema_path))
        self.assertTrue(schema_path.endswith("monitoring-event.schema.json"))

    def test_load_live_scenario(self):
        config = load_scenario(LIVE_SCENARIO_PATH)
        self.assertEqual(config.scenario_id, "substation-live")
        self.assertEqual(config.name, "变电站正常运行场景")
        self.assertEqual(len(config.devices), 25)
        self.assertEqual(len(config.links), 34)

    def test_load_live_scenario_external_layer(self):
        """外界层应包含授时卫星与其他电站，且均通过无线链路接入站内。"""
        config = load_scenario(LIVE_SCENARIO_PATH)
        external = [d for d in config.devices if d.layer == "external"]
        self.assertEqual({d.device_id for d in external}, {"gnss_sat", "peer_station"})
        self.assertEqual({d.device_type for d in external}, {"time_satellite", "remote_substation"})
        self.assertEqual(validate_scenario(config), [])

        wireless = [link for link in config.links if link.link_type == "wireless"]
        self.assertEqual(
            {link.link_id for link in wireless},
            {"L-gnss-time_svc", "L-gnss-time_svc_2", "L-peer-station"},
        )
        endpoints = {frozenset((link.endpoint_a[0], link.endpoint_b[0])) for link in wireless}
        self.assertEqual(endpoints, {
            frozenset(("gnss_sat", "time_svc")),
            frozenset(("gnss_sat", "time_svc_2")),
            frozenset(("peer_station", "station")),
        })

    def test_live_scenario_carries_layout(self):
        """拓扑版式应随场景下发：分层带几何 + 每台设备坐标。"""
        config = load_scenario(LIVE_SCENARIO_PATH)
        layers = config.layout.get("layers") or []
        self.assertTrue(layers)
        for layer in layers:
            for key in ("x", "y", "width", "height"):
                self.assertIsInstance(layer.get(key), (int, float))
        for device in config.devices:
            self.assertIsInstance(device.layout.get("x"), (int, float))
            self.assertIsInstance(device.layout.get("y"), (int, float))

    def test_live_network_links_declare_protocols(self):
        """正常运营场景的站控网/过程网链路带 MMS、SV 或 GOOSE 协议标记。"""
        config = load_scenario(LIVE_SCENARIO_PATH)
        expected = {
            "L-station-mc": "mms",
            "L-prot-station": "mms",
            "L-diff_prot-station": "mms",
            "L-mu-mc": "sv",
            "L-mu_2-mc": "sv",
            "L-mu-prot": "sv",
            "L-mu_2-prot": "sv",
            "L-mu-diff_prot": "sv",
            "L-mu_2-diff_prot": "sv",
            "L-mc-brk": "goose",
            "L-prot-brk": "goose",
            "L-diff_prot-brk": "goose",
            "L-mc-tap": "goose",
            "L-mc-cool": "goose",
        }
        actual = {
            link.link_id: link.parameters.get("protocol")
            for link in config.links
            if link.parameters.get("protocol")
        }
        self.assertEqual(actual, expected)

    def test_synced_scenarios_keep_live_protocols_and_diff_position(self):
        """攻击/网络实验场景应与 live 保持同一套协议标记和差动保护位置。"""
        live = load_scenario(LIVE_SCENARIO_PATH)
        expected = {
            link.link_id: link.parameters.get("protocol")
            for link in live.links
            if link.parameters.get("protocol")
        }
        scenario_dir = os.path.dirname(LIVE_SCENARIO_PATH)
        for name in (
            "substation-em-attack",
            "substation-goose-lab",
            "substation-sensor-attack",
            "substation-time-attack",
        ):
            config = load_scenario(os.path.join(scenario_dir, name + ".json"))
            actual = {
                link.link_id: link.parameters.get("protocol")
                for link in config.links
                if link.parameters.get("protocol")
            }
            self.assertEqual(actual, expected, name)
            diff_prot = next(device for device in config.devices if device.device_id == "diff_prot")
            self.assertEqual(diff_prot.layout, {"x": 600, "y": 356}, name)

    def test_live_scenario_second_timing_chain(self):
        """复数授时系统与合并单元：第二条链路与首条并列成列，授时卫星居中接入两者。"""
        config = load_scenario(LIVE_SCENARIO_PATH)
        devices = {d.device_id: d for d in config.devices}
        for device_id in ("time_svc_2", "mu_2", "ct_current_2"):
            self.assertIn(device_id, devices)

        column_x = {devices[device_id].layout["x"] for device_id in ("time_svc_2", "mu_2", "ct_current_2")}
        self.assertEqual(len(column_x), 1, "新增授时链路的三台设备必须竖直成列")
        self.assertGreater(column_x.pop(), devices["time_svc"].layout["x"], "新列应位于既有授时列右侧")

        # 授时卫星移到两套授时系统中间，便于对两者广播同一时间基准
        self.assertEqual(
            devices["gnss_sat"].layout["x"],
            (devices["time_svc"].layout["x"] + devices["time_svc_2"].layout["x"]) / 2,
        )

        links = {link.link_id: link for link in config.links}
        self.assertEqual(links["L-gnss-time_svc_2"].link_type, "wireless")
        self.assertEqual(links["L-gnss-time_svc_2"].endpoint_a[0], "gnss_sat")
        self.assertEqual(links["L-gnss-time_svc_2"].endpoint_b[0], "time_svc_2")
        self.assertEqual(links["L-time_svc_2-mu_2"].endpoint_a[0], "time_svc_2")
        self.assertEqual(links["L-time_svc_2-mu_2"].endpoint_b[0], "mu_2")
        self.assertEqual(links["L-ct_current_2-mu_2"].endpoint_a[0], "ct_current_2")
        self.assertEqual(links["L-ct_current_2-mu_2"].endpoint_b[0], "mu_2")

        # 第二套合并单元与首套行为一致：量测同时上送测控装置与过流保护装置。
        self.assertEqual(links["L-mu_2-mc"].endpoint_a[0], "mu_2")
        self.assertEqual(links["L-mu_2-mc"].endpoint_b[0], "mc")
        self.assertEqual(links["L-mu_2-prot"].endpoint_a[0], "mu_2")
        self.assertEqual(links["L-mu_2-prot"].endpoint_b[0], "prot")

        process = next(n for n in config.layout["deployment"]["networks"] if n["id"] == "process")
        mu_2_access = next(c for c in process["connections"] if c["device_id"] == "mu_2")
        self.assertEqual(mu_2_access["business_ports"], ["out"])
        self.assertIn("L-mu_2-mc", process["link_ids"])
        self.assertIn("L-mu_2-prot", process["link_ids"])

    def test_live_scenario_cooling_column_at_process_right(self):
        """冷却接口与冷却风机占过程层原最右列，其右侧只留给风机状态监测组。"""
        config = load_scenario(LIVE_SCENARIO_PATH)
        devices = {d.device_id: d for d in config.devices}
        self.assertEqual(devices["ctl_cool"].layout["x"], devices["cool"].layout["x"])
        self.assertGreater(devices["cool"].layout["y"], devices["ctl_cool"].layout["y"],
                           "冷却风机应排在冷却系统控制接口下方")
        right_of = {
            device.device_id for device in config.devices
            if device.layer == "process" and device.layout["x"] > devices["ctl_cool"].layout["x"]
        }
        self.assertEqual(right_of, {"fan_vibration", "fan_current", "fan_speed", "fan_condition_monitor"},
                         "冷却列右侧应只有风机状态监测组")

        # 变压器与油温传感器同行相邻，热耦合直接画成直线，不再绕到节点下方。
        coupling = config.layout["deployment"]["direct_links"]["L-tap-oil"]
        self.assertEqual(coupling["role"], "physical")
        self.assertNotIn("points", coupling, "相邻成排时热耦合走直线，不需要显式折线")
        between = [
            device.device_id for device in config.devices
            if device.layer == "process"
            and device.layout["y"] == devices["tap"].layout["y"]
            and devices["tap"].layout["x"] + 90 <= device.layout["x"] < devices["oil_temp"].layout["x"]
        ]
        self.assertEqual(between, [], "变压器与油温传感器之间不应再夹着设备，直线才不会穿过节点")

    def test_live_scenario_fan_condition_monitoring(self):
        """冷却风机附属传感器与风机状态监测采集装置：位置、读数来源与接入方式。"""
        config = load_scenario(LIVE_SCENARIO_PATH)
        devices = {d.device_id: d for d in config.devices}
        cool = devices["cool"]
        for device_id, name in (
            ("fan_vibration", "风机振动传感器"),
            ("fan_current", "风机电流传感器"),
            ("fan_speed", "风机转速传感器"),
            ("fan_condition_monitor", "风机状态监测采集装置"),
        ):
            self.assertIn(device_id, devices)
            self.assertEqual(devices[device_id].name, name)
            self.assertEqual(devices[device_id].layer, "process")
            self.assertGreater(devices[device_id].layout["x"], cool.layout["x"], "风机附属设备应在冷却风机右侧")

        # 三台传感器竖直成列，采集装置在它们右侧、与中间那台同排。
        sensors = ("fan_vibration", "fan_current", "fan_speed")
        column_x = {devices[device_id].layout["x"] for device_id in sensors}
        self.assertEqual(len(column_x), 1, "三台风机传感器必须竖直成列")
        rows = [devices[device_id].layout["y"] for device_id in sensors]
        self.assertEqual(rows, sorted(rows), "三台风机传感器自上而下排列")
        self.assertEqual(len(set(rows)), 3)
        self.assertGreater(devices["fan_condition_monitor"].layout["x"], column_x.pop())
        self.assertEqual(devices["fan_condition_monitor"].layout["y"], devices["fan_current"].layout["y"])

        # 风机额定参数驱动读数：三台传感器经物理耦合读取风机的振动、电流与转速。
        self.assertEqual(cool.parameters["rated_speed_rpm"], 1480)
        self.assertEqual(cool.parameters["vibration_running_mm_s"], 3.2)
        self.assertEqual(cool.parameters["motor_current_running_a"], 8.5)
        for device_id, (input_key, unit) in (
            ("fan_vibration", ("vibration_mm_s", "mm/s")),
            ("fan_current", ("motor_current_a", "A")),
            ("fan_speed", ("rotation_speed_rpm", "r/min")),
        ):
            self.assertEqual(devices[device_id].parameters["input_key"], input_key)
            self.assertEqual(devices[device_id].parameters["unit"], unit)
            self.assertIn("physical", devices[device_id].ports)

        # 物理耦合 + 本地采集接线；采集装置的输出保持悬空，只做本地监测。
        links = {link.link_id: link for link in config.links}
        for link_id, sensor in (
            ("L-cool-fan-vibration", "fan_vibration"),
            ("L-cool-fan-current", "fan_current"),
            ("L-cool-fan-speed", "fan_speed"),
        ):
            self.assertEqual(links[link_id].link_type, "physical")
            self.assertEqual(links[link_id].endpoint_a, ("cool", "physical"))
            self.assertEqual(links[link_id].endpoint_b, (sensor, "physical"))
        for link_id, sensor in (
            ("L-fan-vibration-monitor", "fan_vibration"),
            ("L-fan-current-monitor", "fan_current"),
            ("L-fan-speed-monitor", "fan_speed"),
        ):
            self.assertEqual(links[link_id].endpoint_a, (sensor, "up"))
            self.assertEqual(links[link_id].endpoint_b, ("fan_condition_monitor", "in"))
        self.assertEqual(
            [link.link_id for link in config.links if link.endpoint_a == ("fan_condition_monitor", "out")],
            [],
            "采集装置输出未接入业务链路：本场景只做本地监测",
        )

        # 采集装置接入过程网，端口号接在冷却接口之后；采集接线不占过程网业务。
        process = next(n for n in config.layout["deployment"]["networks"] if n["id"] == "process")
        access = next(c for c in process["connections"] if c["device_id"] == "fan_condition_monitor")
        self.assertEqual(access["switch_port"], "P9")
        self.assertEqual(access["business_ports"], ["out"])
        self.assertNotIn("L-fan-vibration-monitor", process["link_ids"])

    def test_live_scenario_cooling_affects_transformer_heat(self):
        """冷却风机启停要能作用到油温：声明额定负荷与足够短的热时间常数。"""
        config = load_scenario(LIVE_SCENARIO_PATH)
        devices = {d.device_id: d for d in config.devices}
        self.assertEqual(config.environment["electrical"]["rated_load_mw"], 4.0)
        self.assertEqual(config.environment["electrical"]["cooling_asset"], "cool")
        thermal = devices["tap"].parameters["thermal"]
        self.assertEqual(thermal["max_heat_rise_c"], 50.0)
        self.assertEqual(thermal["cooling_factor"], 0.45)
        self.assertLessEqual(thermal["time_constant_s"], 30.0, "热时间常数需足够短，演示中才看得出变化")

        # 负载率 0.5 时的稳态油温：不通风机 42.5 ℃、通风机 35.625 ℃。
        load_pu = config.environment["load_active_mw"] / config.environment["electrical"]["rated_load_mw"]
        rise = thermal["max_heat_rise_c"] * load_pu ** 2
        self.assertAlmostEqual(config.environment["ambient_temp_c"] + rise, 42.5, places=6)
        self.assertAlmostEqual(
            config.environment["ambient_temp_c"] + rise * thermal["cooling_factor"], 35.625, places=6
        )

    def test_live_scenario_differential_protection(self):
        """差动保护装置：间隔层保护，接收两套合并单元量测并控制断路器智能终端。"""
        config = load_scenario(LIVE_SCENARIO_PATH)
        devices = {d.device_id: d for d in config.devices}
        diff_prot = devices["diff_prot"]
        self.assertEqual(diff_prot.layer, "bay")
        self.assertEqual(diff_prot.device_type, "differential_protection")
        self.assertEqual(sorted(diff_prot.ports), ["in", "out", "up"])
        self.assertEqual(diff_prot.parameters["value_key"], "ct_current")
        self.assertEqual(diff_prot.parameters["compare_mode"], "absolute")
        self.assertEqual(diff_prot.parameters["differential_threshold_a"], 10.0)

        links = {link.link_id: link for link in config.links}
        for link_id, source in (("L-mu-diff_prot", "mu"), ("L-mu_2-diff_prot", "mu_2")):
            self.assertEqual(links[link_id].endpoint_a, (source, "out"))
            self.assertEqual(links[link_id].endpoint_b, ("diff_prot", "in"))
        self.assertEqual(links["L-diff_prot-brk"].endpoint_a, ("diff_prot", "out"))
        self.assertEqual(links["L-diff_prot-brk"].endpoint_b, ("ctl_brk", "up"))
        self.assertEqual(links["L-diff_prot-station"].endpoint_a, ("diff_prot", "up"))
        self.assertEqual(links["L-diff_prot-station"].endpoint_b, ("station", "diff_prot"))

        networks = {n["id"]: n for n in config.layout["deployment"]["networks"]}
        process_access = next(c for c in networks["process"]["connections"] if c["device_id"] == "diff_prot")
        self.assertEqual(sorted(process_access["business_ports"]), ["in", "out"])
        for link_id in ("L-mu-diff_prot", "L-mu_2-diff_prot", "L-diff_prot-brk"):
            self.assertIn(link_id, networks["process"]["link_ids"])
        station_access = next(c for c in networks["station"]["connections"] if c["device_id"] == "diff_prot")
        self.assertEqual(station_access["business_ports"], ["up"])
        self.assertIn("L-diff_prot-station", networks["station"]["link_ids"])

    def test_invalid_layout_is_rejected(self):
        config = load_scenario(SCENARIO_PATH)
        config.devices[0].layout = {"x": "left", "y": 0}
        errors = validate_scenario(config)
        self.assertTrue(any("layout.x" in error for error in errors))


class TestValidateScenario(unittest.TestCase):
    def test_valid_config(self):
        self.assertEqual(validate_scenario(make_config()), [])

    def test_duplicate_device_id(self):
        config = make_config()
        config.devices.append(DeviceSpec(device_id="brk", device_type="sensor", layer="process"))
        errors = validate_scenario(config)
        self.assertTrue(any("device_id 重复" in e for e in errors))

    def test_unknown_device_type(self):
        config = make_config()
        config.devices.append(DeviceSpec(device_id="x", device_type="not_a_device", layer="process"))
        self.assertTrue(any("类型未知" in e for e in validate_scenario(config)))

    def test_unknown_endpoint(self):
        config = make_config()
        config.links.append(LinkSpec(link_id="bad", endpoint_a=("ghost", "up"),
                                     endpoint_b=("s1", "up"), link_type="wired"))
        self.assertTrue(any("引用未知设备" in e for e in validate_scenario(config)))

    def test_message_link_requires_ports(self):
        config = make_config()
        config.links.append(LinkSpec(link_id="no_port", endpoint_a=("s1", ""),
                                     endpoint_b=("brk", ""), link_type="wired"))
        self.assertTrue(any("都必须填写端口" in e for e in validate_scenario(config)))

    def test_bad_loss_rate(self):
        config = make_config()
        config.links.append(LinkSpec(link_id="lossy", endpoint_a=("s1", "up"),
                                     endpoint_b=("brk", "control"), link_type="wired",
                                     parameters={"loss_rate": 1.5}))
        self.assertTrue(any("loss_rate" in e for e in validate_scenario(config)))

    def test_bad_dt(self):
        config = make_config()
        config.simulation["dt_us"] = 0
        self.assertTrue(any("dt_us" in e for e in validate_scenario(config)))

    def test_bad_operation_time(self):
        config = make_config()
        config.operations.append({"time_us": -1})
        self.assertTrue(any("operations[0]" in e for e in validate_scenario(config)))

    def test_duplicate_capability_action(self):
        config = make_config()
        config.devices[0].capabilities.append(Capability(action_type="close"))
        self.assertTrue(any("能力动作重复" in e for e in validate_scenario(config)))


class TestDeviceFactory(unittest.TestCase):
    def tearDown(self):
        DEVICE_TYPES.pop("test_device", None)

    def test_create_known_device(self):
        device = create_device(DeviceSpec(device_id="brk", device_type="switch", layer="process"))
        self.assertEqual(device.asset_id, "brk")

    def test_unknown_device_raises(self):
        with self.assertRaises(KeyError):
            create_device(DeviceSpec(device_id="x", device_type="no_such", layer="process"))

    def test_register_device(self):
        from sim2030.base.device import Device

        def factory(spec):
            device = Device(spec)
            device.state["custom"] = True
            return device

        register_device("test_device", factory)
        device = create_device(DeviceSpec(device_id="c", device_type="test_device", layer="process"))
        self.assertTrue(device.state.get("custom"))


if __name__ == "__main__":
    unittest.main()
