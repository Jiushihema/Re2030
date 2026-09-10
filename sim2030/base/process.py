"""过程层四类设备。

依据开发目标与架构 2.1 归纳为四类：

- 一次设备及辅助系统：``PrimaryEquipment`` 及其子类
- 感知设备：``SensorDevice``
- 采集与处理设备：``AcquisitionDevice``
- 控制接口设备：``ControlInterfaceDevice``

四类是仿真功能归纳，不是标准规定的四种设备；类别可集成在同一实物中。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List

from sim2030.constants import BusinessType
from sim2030.contracts import (
    ActionFeedback,
    Capability,
    DeviceSpec,
    Message,
)
from sim2030.base.device import Device


class PrimaryEquipment(Device):
    """一次设备及辅助系统公共基类。

    保存物理状态、设定值和可控部件；通过 :meth:`physical_outputs` 向环境模型
    提供配置测点处的物理状态。控制指令经 ``control_port`` 进入，执行反馈也经
    该端口回送。
    """

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.control_port: str = self.parameters.get("control_port", "control")
        self._pending_actions: List[Dict[str, Any]] = []

    # ── 物理输出（供环境模型与物理耦合传感器使用） ──
    def physical_outputs(self) -> Dict[str, Any]:
        return dict(self.state)

    # ── 动作能力与联锁 ──
    def _supported_actions(self) -> List[str]:
        return [c.action_type for c in self._capabilities]

    def check_interlock(self, action_type: str, parameters: Dict[str, Any], inputs: Dict[str, Any]) -> str:
        """检查分合/调节条件，返回错误信息字符串；空串表示允许。"""
        return ""

    def _action_duration_us(self, action_type: str, parameters: Dict[str, Any]) -> int:
        duration_ms = float(parameters.get("duration_ms", self.parameters.get("default_duration_ms", 0)))
        duration_us = int(duration_ms * 1000) + int(self._effect("execution_delay_us", 0))
        return duration_us

    # ── 动作受理 ──
    def request_action(self, request: Dict[str, Any]) -> ActionFeedback:
        action_type = request.get("action_type", "")
        action_id = request.get("action_id") or str(uuid.uuid4())
        time_us = int(request.get("time_us", 0))
        parameters = dict(request.get("parameters", {}))

        if action_type not in self._supported_actions():
            return ActionFeedback(action_id, time_us, "rejected", f"不支持动作 {action_type}")

        err = self.check_interlock(action_type, parameters, {})
        if err:
            return ActionFeedback(action_id, time_us, "rejected", err)

        duration_us = self._action_duration_us(action_type, parameters)
        self._pending_actions.append(
            {
                "action_id": action_id,
                "action_type": action_type,
                "parameters": parameters,
                "complete_time_us": time_us + duration_us,
            }
        )
        return ActionFeedback(action_id, time_us, "accepted", feedback={"scheduled_us": time_us + duration_us})

    # ── 指令接收（来自控制接口设备的命令） ──
    def receive(self, message: Message) -> None:
        if message.business_type == BusinessType.COMMAND:
            payload = message.payload or {}
            request = {
                "action_id": payload.get("request_id") or payload.get("action_id") or str(uuid.uuid4()),
                "action_type": payload.get("action_type", ""),
                "parameters": dict(payload.get("parameters", {})),
                "time_us": message.created_time_us,
            }
            feedback = self.request_action(request)
            self._queue(
                self._new_message(
                    self.control_port,
                    BusinessType.FEEDBACK,
                    {"action_id": feedback.action_id, "status": feedback.status, "reason": feedback.failure_reason,
                     "state": self.physical_outputs()},
                    message.created_time_us,
                    related_request=feedback.action_id,
                )
            )
        else:
            self._inbox.append(message)

    def step(self, time_us: int, dt_us: int, inputs: Dict[str, Any]) -> List[Message]:
        self._drain_inbox()
        messages = self._drain_outbox()
        messages.extend(self._complete_actions(time_us))
        return messages

    def _complete_actions(self, time_us: int) -> List[Message]:
        """完成到期动作，更新状态并回送执行反馈。"""
        messages: List[Message] = []
        still_pending: List[Dict[str, Any]] = []
        for action in self._pending_actions:
            if time_us < action["complete_time_us"]:
                still_pending.append(action)
                continue
            self._apply_action(action["action_type"], action["parameters"], time_us)
            messages.extend(
                self._new_message(
                    self.control_port,
                    BusinessType.FEEDBACK,
                    {"action_id": action["action_id"], "status": "completed", "reason": "",
                     "state": self.physical_outputs()},
                    time_us,
                    related_request=action["action_id"],
                )
            )
        self._pending_actions = still_pending
        return messages

    def _apply_action(self, action_type: str, parameters: Dict[str, Any], time_us: int = 0) -> None:
        raise NotImplementedError


class SwitchEquipment(PrimaryEquipment):
    """开关类：断路器、隔离开关、接地开关按能力区分。

    状态：``position``（open/closed）、``operation_time_us``（最近动作完成时刻）。
    """

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.state.setdefault("position", self.parameters.get("initial_position", "open"))

    def physical_outputs(self) -> Dict[str, Any]:
        return {"position": self.state["position"], "closed": self.state["position"] == "closed"}

    def check_interlock(self, action_type: str, parameters: Dict[str, Any], inputs: Dict[str, Any]) -> str:
        interlock = self.parameters.get("interlock", {})
        if action_type in ("close", "open") and interlock.get("trip_locked", False):
            return "跳闸闭锁未解除，拒绝分合操作"
        return ""

    def _apply_action(self, action_type: str, parameters: Dict[str, Any], time_us: int = 0) -> None:
        if action_type == "close":
            self.state["position"] = "closed"
        elif action_type == "open":
            self.state["position"] = "open"
        self.state["operation_time_us"] = time_us


class TransformerEquipment(PrimaryEquipment):
    """变压器及有载分接机构。

    状态：``tap_position``、``oil_temp_c``；分接档位改变变比，热状态用简化一阶模型。
    """

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.state.setdefault("tap_position", int(self.parameters.get("nominal_tap", 5)))
        self.state.setdefault("oil_temp_c", float(self.parameters.get("initial_oil_temp_c", 40.0)))

    def physical_outputs(self) -> Dict[str, Any]:
        return {"tap_position": self.state["tap_position"], "oil_temp_c": self.state["oil_temp_c"]}

    def _apply_action(self, action_type: str, parameters: Dict[str, Any], time_us: int = 0) -> None:
        if action_type == "set_tap":
            self.state["tap_position"] = int(parameters.get("tap_position", self.state["tap_position"]))
        elif action_type in ("raise_tap", "lower_tap"):
            delta = 1 if action_type == "raise_tap" else -1
            self.state["tap_position"] = int(self.state["tap_position"]) + delta

    def step(self, time_us: int, dt_us: int, inputs: Dict[str, Any]) -> List[Message]:
        messages = super().step(time_us, dt_us, inputs)

        thermal = self.parameters.get("thermal", {})
        ambient = float(inputs.get("ambient_temp_c", self.parameters.get("ambient_temp_c", 30.0)))
        load_pu = float(inputs.get("load_pu", 0.0))
        cooling_on = bool(inputs.get("cooling_on", False))

        max_rise = float(thermal.get("max_heat_rise_c", 50.0))
        cooling_factor = float(thermal.get("cooling_factor", 0.45))
        tau_s = float(thermal.get("time_constant_s", 600.0))

        rise = max_rise * (load_pu ** 2) * (cooling_factor if cooling_on else 1.0)
        target_temp = ambient + rise
        dt_s = dt_us / 1_000_000.0
        alpha = min(1.0, dt_s / tau_s)
        self.state["oil_temp_c"] = float(self.state["oil_temp_c"]) + alpha * (target_temp - float(self.state["oil_temp_c"]))
        return messages


class CompensationEquipment(PrimaryEquipment):
    """可投切无功补偿设备（电容器组/电抗器支路）。

    状态：``connected``（bool）。
    """

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.state.setdefault("connected", bool(self.parameters.get("initial_connected", False)))

    def physical_outputs(self) -> Dict[str, Any]:
        return {"connected": bool(self.state["connected"])}

    def _apply_action(self, action_type: str, parameters: Dict[str, Any], time_us: int = 0) -> None:
        if action_type == "connect":
            self.state["connected"] = True
        elif action_type == "disconnect":
            self.state["connected"] = False


class AuxiliaryEquipment(PrimaryEquipment):
    """辅助运行类：冷却风机、油泵等。

    状态：``running``（bool）、``speed_ratio``（0..1，仅在可调速时使用）。
    """

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.state.setdefault("running", bool(self.parameters.get("initial_running", False)))
        self.state.setdefault("speed_ratio", float(self.parameters.get("initial_speed_ratio", 1.0)))

    def physical_outputs(self) -> Dict[str, Any]:
        return {"running": bool(self.state["running"]), "speed_ratio": float(self.state.get("speed_ratio", 1.0))}

    def _apply_action(self, action_type: str, parameters: Dict[str, Any], time_us: int = 0) -> None:
        if action_type == "start":
            self.state["running"] = True
        elif action_type == "stop":
            self.state["running"] = False
        elif action_type == "set_speed":
            self.state["speed_ratio"] = float(parameters.get("speed_ratio", self.state.get("speed_ratio", 1.0)))


class SensorDevice(Device):
    """感知设备：电流、电压、温度、压力或振动通过测点配置区分。

    只产生底座内部业务读数，不生成协同方的电磁 I/Q、声波 PCM 或网络 IDS 证据。
    """

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.input_key: str = self.parameters.get("input_key", "value")
        self.unit: str = self.parameters.get("unit", "")
        self.sample_interval_us: int = int(self.parameters.get("sample_interval_us", 1_000_000))
        self.up_port: str = self.parameters.get("up_port", "up")
        self._next_sample_us: int = 0

    def sample(self, physical_input: Dict[str, Any], time_us: int) -> Any:
        """读取配置测点，返回原始采样值；返回 None 表示本轮不采样。"""
        if self.input_key not in physical_input:
            return None
        value = physical_input[self.input_key]
        gain = float(self.parameters.get("gain", 1.0))
        bias = float(self.parameters.get("bias", 0.0))
        noise = float(self.parameters.get("noise_std", 0.0))
        if isinstance(value, (int, float)):
            import random
            value = value * gain + bias + float(self._effect("reading_offset", 0.0))
            if noise > 0:
                value += random.gauss(0.0, noise)
        return value

    def step(self, time_us: int, dt_us: int, inputs: Dict[str, Any]) -> List[Message]:
        self._drain_inbox()
        messages = self._drain_outbox()
        if time_us < self._next_sample_us:
            return messages
        value = self.sample(inputs, time_us)
        if value is not None:
            self._next_sample_us = time_us + self.sample_interval_us
            messages.extend(
                self._new_message(
                    self.up_port,
                    BusinessType.SAMPLING,
                    {
                        "sensor_id": self.asset_id,
                        "value": value,
                        "unit": self.unit,
                        "sample_time_us": time_us,
                    },
                    time_us,
                )
            )
        return messages


class AcquisitionDevice(Device):
    """采集与处理设备：配置为合并单元或状态采集/分析装置。

    汇集多个传感器的最近一次采样，按 ``report_interval_us`` 周期整理并发布。
    """

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.report_interval_us: int = int(self.parameters.get("report_interval_us", 1_000_000))
        self.output_business_type: str = self.parameters.get("output_business_type", BusinessType.SAMPLING)
        self.out_port: str = self.parameters.get("out_port", "out")
        self._latest: Dict[str, Dict[str, Any]] = {}
        self._next_report_us: int = 0

    def receive(self, message: Message) -> None:
        if message.business_type == BusinessType.SAMPLING:
            payload = message.payload or {}
            self._latest[message.sender_id] = payload
        else:
            self._inbox.append(message)

    def build_measurement(self, time_us: int) -> Dict[str, Any]:
        """整理通道、单位、质量和采集时间；合并单元实例保留 SV 相关字段。"""
        return {
            "acquisition_id": self.asset_id,
            "samples": {k: v.get("value") for k, v in self._latest.items()},
            "units": {k: v.get("unit") for k, v in self._latest.items()},
            "sample_time_us": time_us,
        }

    def step(self, time_us: int, dt_us: int, inputs: Dict[str, Any]) -> List[Message]:
        self._drain_inbox()
        messages = self._drain_outbox()
        if time_us < self._next_report_us:
            return messages
        if not self._latest:
            return messages
        self._next_report_us = time_us + self.report_interval_us
        messages.extend(
            self._new_message(self.out_port, self.output_business_type, self.build_measurement(time_us), time_us)
        )
        return messages


class ControlInterfaceDevice(Device):
    """智能终端、驱动控制器及执行接口。

    接收上层命令，翻译为下游一次设备动作；回读下游反馈并向上层转发。
    """

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.up_port: str = self.parameters.get("up_port", "up")
        self.down_port: str = self.parameters.get("down_port", "down")
        self.action_map: Dict[str, str] = self.parameters.get("action_map", {})

    def translate_command(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        action_type = payload.get("action_type", "")
        parameters = dict(payload.get("parameters", {}))
        mapped = self.action_map.get(action_type, action_type)
        return {"action_type": mapped, "parameters": parameters}

    def receive(self, message: Message) -> None:
        if message.business_type == BusinessType.COMMAND:
            translated = self.translate_command(message.payload or {})
            payload = dict(message.payload or {})
            payload.update(translated)
            self._queue(
                self._new_message(
                    self.down_port,
                    BusinessType.COMMAND,
                    payload,
                    message.created_time_us,
                    related_request=message.payload.get("request_id", message.message_id),
                )
            )
        elif message.business_type == BusinessType.FEEDBACK:
            self._queue(
                self._new_message(
                    self.up_port,
                    BusinessType.FEEDBACK,
                    message.payload,
                    message.created_time_us,
                    related_request=message.related_request,
                )
            )
        else:
            self._inbox.append(message)

    def step(self, time_us: int, dt_us: int, inputs: Dict[str, Any]) -> List[Message]:
        self._drain_inbox()
        return self._drain_outbox()