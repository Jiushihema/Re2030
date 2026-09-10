"""跨层通信：无线终端、无线接入网关、站内通信网络及消息投递。

- ``CommunicationNetwork`` 不是设备，而是底座拥有的组件，管理有线/无线/硬接线消息的
  时延与丢包投递。
- ``WirelessTerminal`` / ``WirelessGateway`` 是可选无线状态通路的设备实例。
"""
from __future__ import annotations

import heapq
import random
from typing import Any, Dict, List, Optional, Tuple

from sim2030.constants import BusinessType, LinkType
from sim2030.contracts import DeviceSpec, LinkSpec, Message
from sim2030.base.device import Device


class CommunicationNetwork:
    """站内通信网络：路由消息，计算时延/丢包，维护待投递队列。"""

    def __init__(self, seed: Optional[int] = None):
        self._rng = random.Random(seed)
        self._links: Dict[str, Dict[str, Any]] = {}
        self._by_pair: Dict[Tuple[str, str], str] = {}
        self._queue: List[Tuple[int, int, Message]] = []
        self._seq = 0

    def add_link(self, link: LinkSpec) -> None:
        params = dict(link.parameters)
        a, b = link.endpoint_a, link.endpoint_b
        self._links[link.link_id] = {
            "endpoint_a": a,
            "endpoint_b": b,
            "link_type": link.link_type,
            "delay_us": int(params.get("delay_us", 0)),
            "loss_rate": float(params.get("loss_rate", 0.0)),
            "effects": {},
        }
        self._by_pair[(a[0], b[0])] = link.link_id
        self._by_pair[(b[0], a[0])] = link.link_id

    def _link_for(self, sender_id: str, receiver_id: str) -> Optional[str]:
        return self._by_pair.get((sender_id, receiver_id))

    def send(self, message: Message, time_us: int) -> bool:
        """按链路计算送达时刻并入队；无链路或丢包时返回 False。"""
        link_id = self._link_for(message.sender_id, message.receiver_id)
        if link_id is None:
            return False
        link = self._links[link_id]
        delay_us = int(link["delay_us"]) + int(link["effects"].get("extra_delay_us", 0))
        loss_rate = float(link["loss_rate"]) + float(link["effects"].get("extra_loss_rate", 0.0))
        if loss_rate > 0 and self._rng.random() < min(1.0, loss_rate):
            return False
        message.deliver_time_us = time_us + delay_us
        self._seq += 1
        heapq.heappush(self._queue, (message.deliver_time_us, self._seq, message))
        return True

    def deliver_due(self, time_us: int) -> List[Message]:
        """投递所有到期消息。"""
        due: List[Message] = []
        while self._queue and self._queue[0][0] <= time_us:
            _t, _seq, message = heapq.heappop(self._queue)
            due.append(message)
        return due

    def link_status(self, link_id: str) -> Dict[str, Any]:
        link = self._links.get(link_id)
        if link is None:
            return {"link_id": link_id, "status": "unknown"}
        return {
            "link_id": link_id,
            "link_type": link["link_type"],
            "status": "up" if link["link_type"] != LinkType.HARDWIRE else "up",
            "delay_us": link["delay_us"],
            "loss_rate": link["loss_rate"],
            "effects": dict(link["effects"]),
        }

    def set_link_effect(self, link_id: str, modifiers: Dict[str, Any]) -> None:
        """施加限时链路影响；空字典表示清除。"""
        if link_id in self._links:
            self._links[link_id]["effects"] = dict(modifiers or {})

    def snapshot(self) -> Dict[str, Any]:
        return {
            "pending_count": len(self._queue),
            "links": {lid: self.link_status(lid) for lid in self._links},
        }


class WirelessTerminal(Device):
    """无线传输终端：将采集单元的状态数据送入无线链路（按场景选配）。"""

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.up_port: str = self.parameters.get("up_port", "up")
        self.wireless_port: str = self.parameters.get("wireless_port", "wireless")

    def encode_message(self, message: Message) -> Dict[str, Any]:
        payload = dict(message.payload or {})
        payload["wireless_terminal"] = self.asset_id
        return payload

    def receive(self, message: Message) -> None:
        if message.business_type in (BusinessType.SAMPLING, BusinessType.STATUS):
            self._queue(
                self._new_message(self.wireless_port, message.business_type, self.encode_message(message),
                                  message.created_time_us, related_request=message.related_request)
            )
        else:
            self._inbox.append(message)

    def step(self, time_us: int, dt_us: int, inputs: Dict[str, Any]) -> List[Message]:
        self._drain_inbox()
        return self._drain_outbox()


class WirelessGateway(Device):
    """无线接入网关：接收无线数据，经站内网络转发至状态监测装置（按场景选配）。"""

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.wireless_port: str = self.parameters.get("wireless_port", "wireless")
        self.wired_port: str = self.parameters.get("wired_port", "wired")

    def forward_message(self, message: Message) -> None:
        self._queue(
            self._new_message(self.wired_port, message.business_type, message.payload,
                              message.created_time_us, related_request=message.related_request)
        )

    def receive(self, message: Message) -> None:
        if message.business_type in (BusinessType.SAMPLING, BusinessType.STATUS):
            self.forward_message(message)
        else:
            self._inbox.append(message)

    def step(self, time_us: int, dt_us: int, inputs: Dict[str, Any]) -> List[Message]:
        self._drain_inbox()
        return self._drain_outbox()