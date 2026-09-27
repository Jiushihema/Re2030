"""外界层（授时卫星 / 其他电站）设备与无线交互测试。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim2030.base.engine import SimulationEngine
from sim2030.constants import BusinessType, Layer
from sim2030.contracts import DeviceSpec, LinkSpec
from sim2030.scenario import create_device, load_scenario


LIVE_SCENARIO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scenarios", "substation-live.json"
)


def _build_external_engine() -> SimulationEngine:
    devices = [
        DeviceSpec(device_id="gnss_sat", device_type="time_satellite", layer=Layer.EXTERNAL,
                   ports={"down": "down"},
                   parameters={"down_port": "down", "sync_interval_us": 100000, "constellation": "BDS"},
                   initial_state={"signal_state": "locked", "visible_satellites": 8}),
        DeviceSpec(device_id="time_svc", device_type="time_service", layer=Layer.STATION,
                   ports={"gnss": "gnss", "sync": "sync"}),
        DeviceSpec(device_id="peer_station", device_type="remote_substation", layer=Layer.EXTERNAL,
                   ports={"down": "down"},
                   parameters={"down_port": "down", "heartbeat_interval_us": 200000},
                   initial_state={"grid_state": "normal", "breaker_position": "closed", "bus_voltage_kv": 110.0}),
        DeviceSpec(device_id="station", device_type="station_control", layer=Layer.STATION,
                   ports={"peer": "peer"}),
    ]
    links = [
        LinkSpec(link_id="L-gnss-time_svc", endpoint_a=("gnss_sat", "down"),
                 endpoint_b=("time_svc", "gnss"), link_type="wireless", parameters={"delay_us": 10000}),
        LinkSpec(link_id="L-peer-station", endpoint_a=("peer_station", "down"),
                 endpoint_b=("station", "peer"), link_type="wireless", parameters={"delay_us": 5000}),
    ]
    engine = SimulationEngine()
    engine.build(devices, links, {"ambient_temp_c": 30.0}, entry_id="station")
    return engine


class TestExternalDeviceFactory(unittest.TestCase):
    def test_factory_creates_external_devices(self):
        sat = create_device(DeviceSpec(device_id="gnss_sat", device_type="time_satellite", layer=Layer.EXTERNAL))
        peer = create_device(DeviceSpec(device_id="peer_station", device_type="remote_substation", layer=Layer.EXTERNAL))
        self.assertEqual(sat.layer, Layer.EXTERNAL)
        self.assertEqual(peer.layer, Layer.EXTERNAL)

    def test_live_scenario_builds_external_devices(self):
        config = load_scenario(LIVE_SCENARIO_PATH)
        engine = SimulationEngine()
        engine.build(config.devices, config.links, config.environment, entry_id="station")
        self.assertEqual(engine._devices["gnss_sat"].layer, Layer.EXTERNAL)
        self.assertEqual(engine._devices["peer_station"].layer, Layer.EXTERNAL)


class TestExternalWirelessExchange(unittest.TestCase):
    def test_satellite_sync_delivered_to_time_service(self):
        engine = _build_external_engine()
        engine.step(0, 100000)
        result = engine.step(10000, 100000)
        delivered = [
            m for m in result.messages
            if m.receiver_id == "time_svc" and m.business_type == BusinessType.SYNC
        ]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0].sender_id, "gnss_sat")
        self.assertEqual(delivered[0].payload.get("time_source"), "satellite")

    def test_peer_status_reaches_station_overview(self):
        engine = _build_external_engine()
        engine.step(0, 100000)
        result = engine.step(5000, 100000)
        delivered = [
            m for m in result.messages
            if m.receiver_id == "station" and m.sender_id == "peer_station"
        ]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0].business_type, BusinessType.STATUS)
        overview = engine._devices["station"].snapshot()["overview"]
        self.assertIn("peer_station", overview)
        self.assertEqual(overview["peer_station"]["grid_state"], "normal")


if __name__ == "__main__":
    unittest.main()
