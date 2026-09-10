"""场景配置读取、校验与设备创建。"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List

from sim2030.constants import Layer, LinkType
from sim2030.contracts import DeviceSpec, LinkSpec, ScenarioConfig
from sim2030.base.device import Device
from sim2030.base.process import (
    PrimaryEquipment,
    SwitchEquipment,
    TransformerEquipment,
    CompensationEquipment,
    AuxiliaryEquipment,
    SensorDevice,
    AcquisitionDevice,
    ControlInterfaceDevice,
)
from sim2030.base.bay import (
    ConditionMonitor,
    ProtectionDevice,
    OverCurrentProtectionDevice,
    MeasurementControlDevice,
)
from sim2030.base.station import StationControlSystem, TimeService
from sim2030.base.communication import WirelessTerminal, WirelessGateway


DeviceFactory = Callable[[DeviceSpec], Device]

DEVICE_TYPES: Dict[str, DeviceFactory] = {
    "primary_equipment": PrimaryEquipment,
    "switch": SwitchEquipment,
    "switch_equipment": SwitchEquipment,
    "transformer": TransformerEquipment,
    "transformer_equipment": TransformerEquipment,
    "compensation": CompensationEquipment,
    "compensation_equipment": CompensationEquipment,
    "auxiliary": AuxiliaryEquipment,
    "auxiliary_equipment": AuxiliaryEquipment,
    "sensor": SensorDevice,
    "sensor_device": SensorDevice,
    "acquisition": AcquisitionDevice,
    "acquisition_device": AcquisitionDevice,
    "control_interface": ControlInterfaceDevice,
    "control_interface_device": ControlInterfaceDevice,
    "condition_monitor": ConditionMonitor,
    "condition_monitor_device": ConditionMonitor,
    "protection": ProtectionDevice,
    "protection_device": ProtectionDevice,
    "overcurrent_protection": OverCurrentProtectionDevice,
    "overcurrent_protection_device": OverCurrentProtectionDevice,
    "measurement_control": MeasurementControlDevice,
    "measurement_control_device": MeasurementControlDevice,
    "station_control": StationControlSystem,
    "station_control_system": StationControlSystem,
    "time_service": TimeService,
    "wireless_terminal": WirelessTerminal,
    "wireless_gateway": WirelessGateway,
}


def register_device(type_name: str, factory: DeviceFactory) -> None:
    if not type_name:
        raise ValueError("设备类型名不能为空")
    if not callable(factory):
        raise TypeError("设备工厂必须是可调用对象")
    DEVICE_TYPES[type_name] = factory


def create_device(spec: DeviceSpec) -> Device:
    factory = DEVICE_TYPES.get(spec.device_type)
    if factory is None:
        raise KeyError(f"未知设备类型 {spec.device_type!r}（device_id={spec.device_id}）")
    return factory(spec)


def load_scenario(path: str) -> ScenarioConfig:
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("场景配置顶层必须是对象")
    base_dir = os.path.dirname(os.path.abspath(path))
    raw = _resolve_relative_paths(raw, base_dir)
    devices = [DeviceSpec.from_dict(item) for item in raw.get("devices", [])]
    links = [LinkSpec.from_dict(item) for item in raw.get("links", [])]
    config = ScenarioConfig(
        scenario_id=raw.get("scenario_id", ""),
        name=raw.get("name", ""),
        simulation=dict(raw.get("simulation", {})),
        devices=devices,
        links=links,
        environment=dict(raw.get("environment", {})),
        operations=list(raw.get("operations", [])),
        attacks=list(raw.get("attacks", [])),
        recognition=dict(raw.get("recognition", {})),
        defense=dict(raw.get("defense", {})),
        observations=dict(raw.get("observations", {})),
        evaluation=dict(raw.get("evaluation", {})),
        references=dict(raw.get("references", {})),
    )
    return config


def _resolve_relative_paths(raw: Dict[str, Any], base_dir: str) -> Dict[str, Any]:
    data = dict(raw)
    observations = data.get("observations")
    if isinstance(observations, dict):
        resolved = dict(observations)
        for key in ("data_dir", "schema_path", "provider_dir"):
            value = resolved.get(key)
            if isinstance(value, str) and value and not os.path.isabs(value):
                resolved[key] = os.path.normpath(os.path.join(base_dir, value))
        data["observations"] = resolved
    return data


def validate_scenario(config: ScenarioConfig) -> List[str]:
    errors: List[str] = []
    if not config.scenario_id:
        errors.append("缺少 scenario_id")

    device_ids = [d.device_id for d in config.devices]
    _check_unique(device_ids, "设备 device_id 重复", errors)
    device_map = {d.device_id: d for d in config.devices}

    for device in config.devices:
        if not device.device_id:
            errors.append("设备缺少 device_id")
        if not device.device_type:
            errors.append(f"设备 {device.device_id} 缺少 device_type")
        elif device.device_type not in DEVICE_TYPES:
            errors.append(f"设备 {device.device_id} 类型未知：{device.device_type}")
        if device.layer and device.layer not in (Layer.PROCESS, Layer.BAY, Layer.STATION, Layer.COMMUNICATION):
            errors.append(f"设备 {device.device_id} 层级未知：{device.layer}")

        seen_actions = set()
        for cap in device.capabilities:
            if not cap.action_type:
                errors.append(f"设备 {device.device_id} 存在空 action_type 能力")
                continue
            if cap.action_type in seen_actions:
                errors.append(f"设备 {device.device_id} 能力动作重复：{cap.action_type}")
            seen_actions.add(cap.action_type)
        _validate_device_ports(device, device_map, errors)

    link_ids = [link.link_id for link in config.links]
    _check_unique(link_ids, "连接 link_id 重复", errors)

    for link in config.links:
        if not link.link_id:
            errors.append("连接缺少 link_id")
        if link.link_type not in (
            LinkType.PHYSICAL, LinkType.ELECTRICAL, LinkType.HARDWIRE,
            LinkType.WIRED, LinkType.WIRELESS,
        ):
            errors.append(f"连接 {link.link_id} 类型未知：{link.link_type}")
            continue
        _validate_endpoint(link.link_id, link.endpoint_a, device_map, errors)
        _validate_endpoint(link.link_id, link.endpoint_b, device_map, errors)
        if link.link_type in LinkType.MESSAGE_LINK_TYPES:
            if not link.endpoint_a[1] or not link.endpoint_b[1]:
                errors.append(f"消息连接 {link.link_id} 两端都必须填写端口")
            params = link.parameters
            if "delay_us" in params and not isinstance(params["delay_us"], (int, float)):
                errors.append(f"连接 {link.link_id} 的 delay_us 必须是数值")
            if "loss_rate" in params:
                rate = params["loss_rate"]
                if not isinstance(rate, (int, float)) or not (0.0 <= float(rate) <= 1.0):
                    errors.append(f"连接 {link.link_id} 的 loss_rate 必须在 0..1")

    simulation = config.simulation
    dt_us = simulation.get("dt_us")
    if dt_us is None:
        errors.append("simulation.dt_us 缺失")
    elif isinstance(dt_us, bool) or not isinstance(dt_us, int) or int(dt_us) <= 0:
        errors.append("simulation.dt_us 必须是正整数")
    duration_us = simulation.get("duration_us")
    if duration_us is not None and (isinstance(duration_us, bool) or not isinstance(duration_us, int) or int(duration_us) <= 0):
        errors.append("simulation.duration_us 必须是正整数")

    for index, operation in enumerate(config.operations):
        if not isinstance(operation, dict):
            errors.append(f"operations[{index}] 必须是对象")
            continue
        time_us = operation.get("time_us")
        if time_us is None or isinstance(time_us, bool) or not isinstance(time_us, int) or int(time_us) < 0:
            errors.append(f"operations[{index}].time_us 必须是非负整数")

        target_id = operation.get("target_asset_id", "")
        if target_id not in device_map:
            errors.append(f"operations[{index}] 引用未知目标设备：{target_id}")
            continue
        action_type = operation.get("action_type", "")
        device = device_map[target_id]
        if device.capabilities and action_type not in {c.action_type for c in device.capabilities}:
            errors.append(f"operations[{index}] 动作 {action_type} 不在设备 {target_id} 的能力范围内")

    _validate_observation_references(config, errors)
    return errors


def _check_unique(items: List[str], prefix: str, errors: List[str]) -> None:
    seen = set()
    for item in items:
        if item in seen:
            errors.append(f"{prefix}：{item}")
        seen.add(item)


def _validate_endpoint(link_id: str, endpoint: tuple, device_map: Dict[str, DeviceSpec], errors: List[str]) -> None:
    device_id, port = endpoint
    if device_id not in device_map:
        errors.append(f"连接 {link_id} 引用未知设备：{device_id}")
        return
    device = device_map[device_id]
    if port and port not in device.ports and device.ports:
        errors.append(f"连接 {link_id} 的设备 {device_id} 未声明端口 {port}")


def _validate_device_ports(device: DeviceSpec, device_map: Dict[str, DeviceSpec], errors: List[str]) -> None:
    for port, target in device.ports.items():
        if not isinstance(target, str) or not target:
            errors.append(f"设备 {device.device_id} 端口 {port} 的声明不完整")


def _validate_observation_references(config: ScenarioConfig, errors: List[str]) -> None:
    observations = config.observations or {}
    schema_path = observations.get("schema_path")
    if schema_path and isinstance(schema_path, str) and not os.path.exists(schema_path):
        errors.append(f"observations.schema_path 不存在：{schema_path}")

