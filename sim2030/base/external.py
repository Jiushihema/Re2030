"""外界层设备：授时卫星与其他电站（对侧）。

位于站控层之上的“外界”平面，通过无线链路与站内设备交互：

- :class:`TimeSatellite` 作为授时系统的上游时间源，向下游广播授时基准信号。
- :class:`RemoteSubstation` 表示外部电网中的相邻变电站，向本站上报运行状态。

两者当前只表达时间源与对侧电站的状态及最小交互，不作为高保真 GNSS 或电网模型。
"""
from __future__ import annotations

from typing import Any, Dict, List

from sim2030.constants import BusinessType
from sim2030.contracts import DeviceSpec, Message
from sim2030.base.device import Device


class TimeSatellite(Device):
    """授时卫星：向下游授时系统广播时间基准信号。

    参数：``down_port``（下游端口，默认 ``down``）、``sync_interval_us``
    （广播周期，默认 1s，<=0 表示关闭）、``constellation``（星座，默认 ``BDS``）。
    """

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.down_port: str = self.parameters.get("down_port", "down")
        self.sync_interval_us: int = int(self.parameters.get("sync_interval_us", 1_000_000))
        self.constellation: str = self.parameters.get("constellation", "BDS")
        self._next_sync_us: int = 0

    def time_signal(self, time_us: int) -> Dict[str, Any]:
        return {
            "sync_time_us": time_us,
            "time_source": "satellite",
            "constellation": self.constellation,
            "signal_state": self.state.get("signal_state", "locked"),
        }

    def step(self, time_us: int, dt_us: int, inputs: Dict[str, Any]) -> List[Message]:
        self._drain_inbox()
        messages = self._drain_outbox()
        if self.sync_interval_us <= 0 or time_us < self._next_sync_us:
            return messages
        self._next_sync_us = time_us + self.sync_interval_us
        messages.extend(
            self._new_message(self.down_port, BusinessType.SYNC, self.time_signal(time_us), time_us)
        )
        return messages


class RemoteSubstation(Device):
    """其他电站（对侧）：向本站上报运行状态，并记录收到的站间交互。

    参数：``down_port``（下游端口，默认 ``down``）、``heartbeat_interval_us``
    （状态上报周期，默认 2s，<=0 表示关闭）。
    """

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.down_port: str = self.parameters.get("down_port", "down")
        self.heartbeat_interval_us: int = int(self.parameters.get("heartbeat_interval_us", 2_000_000))
        self._next_heartbeat_us: int = 0
        self._last_received: List[Dict[str, Any]] = []

    def receive(self, message: Message) -> None:
        self._last_received.append({
            "time_us": message.created_time_us,
            "from": message.sender_id,
            "type": message.business_type,
            "request_id": message.related_request or "",
            "payload": message.payload or {},
        })
        self._last_received = self._last_received[-8:]
        self._inbox.append(message)

    def status_message(self, time_us: int) -> Dict[str, Any]:
        return {
            "peer_id": self.asset_id,
            "time_us": time_us,
            "grid_state": self.state.get("grid_state", "normal"),
            "breaker_position": self.state.get("breaker_position", "closed"),
            "bus_voltage_kv": self.state.get("bus_voltage_kv", 110.0),
        }

    def step(self, time_us: int, dt_us: int, inputs: Dict[str, Any]) -> List[Message]:
        self._drain_inbox()
        messages = self._drain_outbox()
        if self.heartbeat_interval_us <= 0 or time_us < self._next_heartbeat_us:
            return messages
        self._next_heartbeat_us = time_us + self.heartbeat_interval_us
        messages.extend(
            self._new_message(self.down_port, BusinessType.STATUS, self.status_message(time_us), time_us)
        )
        return messages

    def snapshot(self) -> Dict[str, Any]:
        data = super().snapshot()
        data["last_received"] = list(self._last_received)
        return data
