"""底座引擎：状态推进与动作执行。

引擎只负责组装与调度，不包含设备业务逻辑；正常控制、防御控制最终进入同一目标
的动作检查。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from sim2030.constants import BusinessType, LinkType
from sim2030.contracts import (
    ActionFeedback,
    Capability,
    DeviceSpec,
    EffectRequest,
    DefenseRequest,
    LinkSpec,
    Message,
    StepResult,
)
from sim2030.base.device import Device
from sim2030.base.communication import CommunicationNetwork
from sim2030.base.environment import OperatingEnvironment, get_effect_model
import sim2030.scenario as scenario_module

logger = logging.getLogger("sim2030.base.engine")


class SimulationEngine:
    """底座引擎，负责设备/网络/环境的装配与单步推进。"""

    def __init__(self):
        self._devices: Dict[str, Device] = {}
        self._network: Optional[CommunicationNetwork] = None
        self._environment: Optional[OperatingEnvironment] = None
        self._physical_coupling: Dict[str, str] = {}
        self._active_effects: List[Dict[str, Any]] = []
        self._applied_effects: set = set()
        self._entry_id: str = ""
        self._device_specs: List[DeviceSpec] = []
        self._link_specs: List[LinkSpec] = []

    # ──────────────────────────────────────────────
    # 装配
    # ──────────────────────────────────────────────
    def build(
        self,
        device_specs: List[DeviceSpec],
        link_specs: List[LinkSpec],
        environment_config: Dict[str, Any],
        entry_id: str = "",
    ) -> None:
        self._device_specs = list(device_specs)
        self._link_specs = list(link_specs)
        self._entry_id = entry_id or self._default_entry_id(device_specs)

        self._devices = {}
        for spec in device_specs:
            device = scenario_module.create_device(spec)
            self._devices[spec.device_id] = device

        seed = environment_config.get("random_seed", None)
        self._network = CommunicationNetwork(seed=seed)
        for link in link_specs:
            if link.link_type in LinkType.MESSAGE_LINK_TYPES:
                self._network.add_link(link)
            elif link.link_type == LinkType.PHYSICAL:
                a_id, b_id = link.endpoint_a[0], link.endpoint_b[0]
                self._physical_coupling[b_id] = a_id

        self._environment = OperatingEnvironment(environment_config, seed=seed)

        routing: Dict[str, Dict[str, List[tuple]]] = {did: {} for did in self._devices}
        for link in link_specs:
            if link.link_type not in LinkType.MESSAGE_LINK_TYPES:
                continue
            (a_id, a_port), (b_id, b_port) = link.endpoint_a, link.endpoint_b
            routing.setdefault(a_id, {}).setdefault(a_port, []).append((b_id, b_port, link.link_id))
            routing.setdefault(b_id, {}).setdefault(b_port, []).append((a_id, a_port, link.link_id))

        for did, device in self._devices.items():
            device.attach(routing.get(did, {}), self._environment, self._network)

        logger.info(
            "底座装配完成：%d 台设备、%d 条连接、入口=%s",
            len(self._devices), len(link_specs), self._entry_id,
        )

    @staticmethod
    def _default_entry_id(device_specs: List[DeviceSpec]) -> str:
        for spec in device_specs:
            if spec.device_type == "station_control":
                return spec.device_id
        return ""

    # ──────────────────────────────────────────────
    # 提交接口
    # ──────────────────────────────────────────────
    def submit_business_operation(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """将正常业务操作送往配置的站端/就地控制入口。"""
        request_id = operation.get("request_id") or str(uuid.uuid4())
        entry = self._devices.get(self._entry_id)
        if entry is None:
            return {"request_id": request_id, "status": "rejected", "reason": "未配置控制入口"}

        message = Message(
            message_id=str(uuid.uuid4()),
            sender_id="operator",
            receiver_id=self._entry_id,
            business_type=BusinessType.COMMAND,
            source_port="operator",
            target_port="",
            related_request=request_id,
            created_time_us=int(operation.get("time_us", 0)),
            payload={
                "request_id": request_id,
                "target_asset_id": operation.get("target_asset_id", ""),
                "action_type": operation.get("action_type", ""),
                "parameters": dict(operation.get("parameters", {})),
            },
        )
        entry.receive(message)
        return {"request_id": request_id, "status": "accepted", "reason": ""}

    def submit_effect(self, request: EffectRequest) -> Dict[str, Any]:
        """校验作用目标与效果类型，按起止时间登记。"""
        if get_effect_model(request.effect_type) is None:
            return {"effect_id": request.effect_id, "status": "rejected", "reason": f"未知效果类型 {request.effect_type}"}
        if request.target_id not in self._devices and not self._network_is_link(request.target_id):
            return {"effect_id": request.effect_id, "status": "rejected", "reason": f"未知作用目标 {request.target_id}"}
        self._active_effects.append(
            {
                "effect_id": request.effect_id,
                "target_id": request.target_id,
                "effect_type": request.effect_type,
                "parameters": dict(request.parameters),
                "start_time_us": request.start_time_us,
                "end_time_us": request.end_time_us,
            }
        )
        return {"effect_id": request.effect_id, "status": "accepted", "reason": ""}

    def _network_is_link(self, target_id: str) -> bool:
        return self._network is not None and target_id in self._network._links

    def submit_defense(self, request: DefenseRequest) -> ActionFeedback:
        """检查能力、联锁和动作冲突，将动作分派给目标设备/链路。"""
        device = self._devices.get(request.target_id)
        if device is None:
            return ActionFeedback(request.action_id, request.valid_from_us, "rejected", f"未知目标 {request.target_id}")
        return device.request_action(
            {
                "action_id": request.action_id,
                "action_type": request.action_type,
                "parameters": dict(request.parameters),
                "time_us": request.valid_from_us,
            }
        )

    def get_capabilities(self, target_ids: List[str]) -> Dict[str, List[Capability]]:
        return {tid: self._devices[tid].capabilities() for tid in target_ids if tid in self._devices}

    def get_device_controls(self, device_id: str) -> Dict[str, Any]:
        """返回单台设备在演示平面可用的控制/调节项描述。"""
        device = self._devices.get(device_id)
        if device is None:
            return {"device_id": device_id, "status": "not_found"}
        return {
            "device_id": device_id,
            "name": device.name,
            "device_type": device.spec.device_type,
            "layer": device.layer,
            "controls": device.describe_controls(),
        }

    def get_management(self, target_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        return {tid: self._devices[tid].read_management() for tid in target_ids if tid in self._devices}

    def set_device_parameter(self, device_id: str, key: str, value: Any) -> Dict[str, Any]:
        """运行期调整单个设备参数，供演示平面/防御平面复用。"""
        device = self._devices.get(device_id)
        if device is None:
            return {"device_id": device_id, "status": "not_found"}
        device.set_parameter(key, value)
        return {"device_id": device_id, "status": "updated", "key": key, "value": value}

    # ──────────────────────────────────────────────
    # 单步推进
    # ──────────────────────────────────────────────
    def step(self, time_us: int, dt_us: int) -> StepResult:
        result = StepResult(time_us=time_us)
        self._apply_effects(time_us)

        physical_outputs = {
            did: d.physical_outputs() for did, d in self._devices.items() if hasattr(d, "physical_outputs")
        }
        self._environment.update(time_us, dt_us, physical_outputs)

        for message in self._network.deliver_due(time_us):
            device = self._devices.get(message.receiver_id)
            if device is not None:
                device.receive(message)
                result.messages.append(message)

        for device in self._devices.values():
            inputs = self._build_inputs(device)
            messages = device.step(time_us, dt_us, inputs)
            for message in messages:
                self._network.send(message, time_us)
                result.messages.append(message)

        for did, device in self._devices.items():
            result.device_snapshots[did] = device.snapshot()

        result.network_state = self._network.snapshot()
        return result

    def _build_inputs(self, device: Device) -> Dict[str, Any]:
        inputs = dict(self._environment.inputs_for(device.asset_id))
        source_id = self._physical_coupling.get(device.asset_id)
        if source_id and source_id in self._devices:
            inputs.update(self._devices[source_id].physical_outputs())
        return inputs

    def _apply_effects(self, time_us: int) -> None:
        """激活/移除到期影响，并计算 modifiers 下发到设备或链路。"""
        remaining: List[Dict[str, Any]] = []
        active_targets: set = set()

        for effect in self._active_effects:
            if time_us < effect["start_time_us"] or time_us > effect["end_time_us"]:
                # 未开始或已结束；未开始且结束时间未过期的继续保留。
                if time_us <= effect["end_time_us"]:
                    remaining.append(effect)
                continue

            model = get_effect_model(effect["effect_type"])
            request = EffectRequest(
                effect_id=effect["effect_id"],
                target_id=effect["target_id"],
                effect_type=effect["effect_type"],
                parameters=effect["parameters"],
                start_time_us=effect["start_time_us"],
                end_time_us=effect["end_time_us"],
            )
            modifiers = model.evaluate(request, {})
            target = effect["target_id"]
            if target in self._devices:
                self._devices[target].set_effects(modifiers)
                active_targets.add(("device", target))
            elif self._network_is_link(target):
                self._network.set_link_effect(target, modifiers)
                active_targets.add(("link", target))

            if time_us < effect["end_time_us"]:
                remaining.append(effect)

        # 清理本步不再生效的目标，避免影响残留到后续仿真步。
        for kind, target in self._applied_effects - active_targets:
            if kind == "device" and target in self._devices:
                self._devices[target].set_effects({})
            elif kind == "link" and self._network is not None:
                self._network.set_link_effect(target, {})

        self._applied_effects = active_targets
        self._active_effects = remaining