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
        self.assertEqual(config.name, "变电站场景")
        self.assertEqual(len(config.devices), 15)
        self.assertEqual(len(config.links), 17)


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
