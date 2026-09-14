"""站控层设备：站端监控系统、授时系统。"""
from __future__ import annotations

from typing import Any, Dict, List

from sim2030.constants import BusinessType
from sim2030.contracts import DeviceSpec, Message
from sim2030.base.device import Device


class StationControlSystem(Device):
    """站端监控系统：汇集量测、状态和事件，支持操作、监视、记录及对外通信。

    只做业务控制与监视；``presentation`` 才是整个研究系统的 UI。
    """

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.dispatch: Dict[str, str] = self.parameters.get("dispatch", {})
        self._overview: Dict[str, Dict[str, Any]] = {}
        self._last_received: List[Dict[str, Any]] = []
        self._last_dispatched: List[Dict[str, Any]] = []

    def update_overview(self, message: Message) -> None:
        self._overview[message.sender_id] = message.payload or {}
        self._last_received.append({
            "time_us": message.created_time_us,
            "from": message.sender_id,
            "type": message.business_type,
            "request_id": message.related_request or "",
            "payload": message.payload or {},
        })
        self._last_received = self._last_received[-8:]

    def receive(self, message: Message) -> None:
        if message.business_type == BusinessType.COMMAND:
            self.submit_operation(message)
        elif message.business_type in (BusinessType.STATUS, BusinessType.PROTECTION, BusinessType.FEEDBACK):
            self.update_overview(message)
        else:
            self._inbox.append(message)

    def _record_dispatch(self, message: Message, target: str, port: str, status: str, reason: str) -> None:
        payload = message.payload or {}
        self._last_dispatched.append({
            "time_us": message.created_time_us,
            "target": target,
            "port": port,
            "status": status,
            "reason": reason,
            "action": payload.get("action_type", ""),
            "request_id": payload.get("request_id", ""),
        })
        self._last_dispatched = self._last_dispatched[-8:]

    def submit_operation(self, message: Message) -> None:
        payload = message.payload or {}
        target = payload.get("target_asset_id", "")
        port = self.dispatch.get(target, self.dispatch.get("*", ""))
        if not port:
            reason = f"未配置目标 {target} 的转发端口"
            self._record_dispatch(message, target, "", "rejected", reason)
            self._queue(
                self._new_message(
                    "up", BusinessType.FEEDBACK,
                    {"request_id": payload.get("request_id", ""), "status": "rejected", "reason": reason},
                    message.created_time_us, related_request=payload.get("request_id", ""),
                )
            )
            return
        self._record_dispatch(message, target, port, "dispatched", "")
        self._queue(
            self._new_message(port, BusinessType.COMMAND, payload, message.created_time_us,
                              related_request=payload.get("request_id", ""))
        )

    def step(self, time_us: int, dt_us: int, inputs: Dict[str, Any]) -> List[Message]:
        self._drain_inbox()
        return self._drain_outbox()

    def snapshot(self) -> Dict[str, Any]:
        data = super().snapshot()
        data["overview"] = dict(self._overview)
        data["overview"]["last_received"] = list(self._last_received)
        data["overview"]["last_dispatched"] = list(self._last_dispatched)
        return data


class TimeService(Device):
    """授时系统：接收 GNSS 等时间源，为需要对时的设备提供时间基准。

    独立于仿真调度时钟；这里仅把仿真时刻作为时间基准输出。
    """

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.sync_port: str = self.parameters.get("sync_port", "sync")
        self.sync_interval_us: int = int(self.parameters.get("sync_interval_us", 1_000_000))
        self.time_source: str = self.parameters.get("time_source", "gnss")
        self._next_sync_us: int = 0

    def time_signal(self, time_us: int) -> Dict[str, Any]:
        return {"sync_time_us": time_us, "time_source": self.time_source, "sync_state": "synced"}

    def step(self, time_us: int, dt_us: int, inputs: Dict[str, Any]) -> List[Message]:
        self._drain_inbox()
        messages = self._drain_outbox()
        if time_us < self._next_sync_us:
            return messages
        self._next_sync_us = time_us + self.sync_interval_us
        messages.extend(self._new_message(self.sync_port, BusinessType.SYNC, self.time_signal(time_us), time_us))
        return messages