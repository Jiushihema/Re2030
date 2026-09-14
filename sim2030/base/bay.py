"""间隔层设备：状态监测、保护、测控。

三者都继承 ``Device``，通过消息与过程层/站控层交互。
"""
from __future__ import annotations

from typing import Any, Dict, List

from sim2030.constants import BusinessType
from sim2030.contracts import DeviceSpec, Message
from sim2030.base.device import Device


class ConditionMonitor(Device):
    """状态监测装置：汇集设备状态与诊断结果，向站端上报异常和告警。"""

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.up_port: str = self.parameters.get("up_port", "up")
        self.in_port: str = self.parameters.get("in_port", "in")
        self.report_interval_us: int = int(self.parameters.get("report_interval_us", 1_000_000))
        self._latest: Dict[str, Dict[str, Any]] = {}
        self._next_report_us: int = 0

    def receive(self, message: Message) -> None:
        if message.business_type in (BusinessType.STATUS, BusinessType.SAMPLING):
            self._latest[message.sender_id] = message.payload or {}
        else:
            self._inbox.append(message)

    def summarize(self, measurements: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "monitor_id": self.asset_id,
            "measurements": measurements,
            "alarm": self.parameters.get("alarm_rule", ""),
        }

    def step(self, time_us: int, dt_us: int, inputs: Dict[str, Any]) -> List[Message]:
        self._drain_inbox()
        messages = self._drain_outbox()
        if time_us < self._next_report_us or not self._latest:
            return messages
        self._next_report_us = time_us + self.report_interval_us
        messages.extend(
            self._new_message(self.up_port, BusinessType.STATUS, self.summarize(dict(self._latest)), time_us)
        )
        return messages


class ProtectionDevice(Device):
    """保护装置：根据电气采样值判断故障，发出跳闸指令并上报保护事件。"""

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.out_port: str = self.parameters.get("out_port", "out")
        self.in_port: str = self.parameters.get("in_port", "in")
        self.up_port: str = self.parameters.get("up_port", "up")
        self.eval_interval_us: int = int(self.parameters.get("eval_interval_us", 1_000_000))
        self._next_eval_us: int = 0
        self._latest_sample: Dict[str, Any] = {}
        self._window: List[Dict[str, Any]] = []

    def receive(self, message: Message) -> None:
        if message.business_type == BusinessType.SAMPLING:
            self._latest_sample = message.payload or {}
        else:
            self._inbox.append(message)

    def evaluate_protection(self, samples: Dict[str, Any], time_us: int) -> List[Dict[str, Any]]:
        """返回需要执行的保护动作列表（如 trip）。"""
        return []

    def step(self, time_us: int, dt_us: int, inputs: Dict[str, Any]) -> List[Message]:
        self._drain_inbox()
        messages = self._drain_outbox()
        if time_us < self._next_eval_us:
            return messages
        self._next_eval_us = time_us + self.eval_interval_us
        if not self._latest_sample:
            return messages

        for action in self.evaluate_protection(self._latest_sample, time_us):
            payload = {
                "request_id": action.get("request_id", f"prot-{time_us}"),
                "action_type": action["action_type"],
                "parameters": action.get("parameters", {}),
            }
            messages.extend(
                self._new_message(self.out_port, BusinessType.COMMAND, payload, time_us, related_request=payload["request_id"])
            )
            messages.extend(
                self._new_message(self.up_port, BusinessType.PROTECTION, {"action": action["action_type"], "sample": self._latest_sample}, time_us)
            )
        return messages


class OverCurrentProtectionDevice(ProtectionDevice):
    """线路过流/过压保护示例实现，按配置阈值与持续时间判断。"""

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self._persistent_ticks: int = 0

    def evaluate_protection(self, samples: Dict[str, Any], time_us: int) -> List[Dict[str, Any]]:
        threshold = float(self.parameters.get("overcurrent_threshold_a", 1000.0))
        duration_ticks = int(self.parameters.get("duration_ticks", 3))
        value = self._read_value(samples)
        if value is None:
            return []
        if value > threshold:
            self._persistent_ticks += 1
        else:
            self._persistent_ticks = 0
        if self._persistent_ticks >= duration_ticks:
            self._persistent_ticks = 0
            return [{"action_type": "trip", "parameters": {}}]
        return []

    def _read_value(self, samples: Dict[str, Any]):
        key = self.parameters.get("value_key", "current_a")
        s = samples.get("samples", samples)
        if isinstance(s, dict):
            return s.get(key)
        return None

    def describe_controls(self) -> List[Dict[str, Any]]:
        return [{
            "kind": "parameter",
            "key": "overcurrent_threshold_a",
            "label": "过流判断阈值",
            "unit": "A",
            "value": float(self.parameters.get("overcurrent_threshold_a", 1000.0)),
            "input": {"type": "number", "min": 0, "step": 1},
        }]

    def snapshot(self) -> Dict[str, Any]:
        snap = super().snapshot()
        snap.setdefault("overview", {})["overcurrent_threshold_a"] = float(
            self.parameters.get("overcurrent_threshold_a", 1000.0)
        )
        return snap


class MeasurementControlDevice(Device):
    """测控装置：形成业务量测，检查并下发操作命令，跟踪执行状态。"""

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.up_port: str = self.parameters.get("up_port", "up")
        self.mu_port: str = self.parameters.get("mu_port", "mu")
        self.command_ports: Dict[str, str] = self.parameters.get("command_ports", {"*": "control"})
        self.report_interval_us: int = int(self.parameters.get("report_interval_us", 1_000_000))
        self._latest_sample: Dict[str, Any] = {}
        self._last_commands: List[Dict[str, Any]] = []
        self._last_feedback: List[Dict[str, Any]] = []
        self._next_report_us: int = 0

    def receive(self, message: Message) -> None:
        if message.business_type == BusinessType.SAMPLING:
            self._latest_sample = message.payload or {}
        elif message.business_type == BusinessType.COMMAND:
            payload = message.payload or {}
            ok, reason = self.check_command(payload)
            self._last_commands.append({
                "time_us": message.created_time_us,
                "action": payload.get("action_type", ""),
                "status": "dispatched" if ok else "rejected",
                "reason": reason,
                "request_id": payload.get("request_id", ""),
                "target": (payload.get("parameters") or {}).get("target", ""),
            })
            self._last_commands = self._last_commands[-8:]
            if not ok:
                self._queue(
                    self._new_message(
                        self.up_port, BusinessType.FEEDBACK,
                        {"request_id": payload.get("request_id", ""), "status": "rejected", "reason": reason},
                        message.created_time_us, related_request=payload.get("request_id", ""),
                    )
                )
            else:
                action_type = payload.get("action_type", "")
                port = self.command_ports.get(action_type, self.command_ports.get("*", "control"))
                self._queue(
                    self._new_message(port, BusinessType.COMMAND, payload, message.created_time_us,
                                      related_request=payload.get("request_id", ""))
                )
        elif message.business_type == BusinessType.FEEDBACK:
            self._last_feedback.append({
                "time_us": message.created_time_us,
                "request_id": message.related_request or (message.payload or {}).get("request_id", ""),
                "payload": message.payload or {},
            })
            self._last_feedback = self._last_feedback[-8:]
            self._queue(
                self._new_message(self.up_port, BusinessType.FEEDBACK, message.payload, message.created_time_us,
                                  related_request=message.related_request)
            )
        else:
            self._inbox.append(message)

    def check_command(self, command: Dict[str, Any]):
        """检查操作条件；返回 (是否允许, 拒绝原因)。"""
        action_type = command.get("action_type", "")
        if not action_type:
            return False, "缺少 action_type"
        allowed = self.parameters.get("allowed_actions", None)
        if allowed is not None and action_type not in allowed:
            return False, f"不允许动作 {action_type}"
        return True, ""

    def build_telemetry(self) -> Dict[str, Any]:
        return {"measurement_id": self.asset_id, "sample": self._latest_sample}

    def snapshot(self) -> Dict[str, Any]:
        data = super().snapshot()
        data["overview"] = {
            "latest_sample": self._latest_sample,
            "last_commands": list(self._last_commands),
            "last_feedback": list(self._last_feedback),
        }
        return data

    def step(self, time_us: int, dt_us: int, inputs: Dict[str, Any]) -> List[Message]:
        self._drain_inbox()
        messages = self._drain_outbox()
        if time_us >= self._next_report_us and self._latest_sample:
            self._next_report_us = time_us + self.report_interval_us
            messages.extend(self._new_message(self.up_port, BusinessType.STATUS, self.build_telemetry(), time_us))
        return messages