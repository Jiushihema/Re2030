"""设备公共接口。

所有底座设备共享同一接口，由子类实现具体业务：

- 设备只更新自身状态，通过返回消息或底座的物理模型影响其他实体。
- 简单实例用参数区分，只有业务行为明显不同才增加子类。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from sim2030.contracts import (
    ActionFeedback,
    Capability,
    DeviceSpec,
    Message,
)

logger = logging.getLogger("sim2030.base.device")


class Device:
    """底座设备公共基类。"""

    def __init__(self, spec: DeviceSpec):
        self.spec = spec
        self.asset_id: str = spec.device_id
        self.name: str = spec.name or spec.device_id
        self.layer: str = spec.layer
        self.parameters: Dict[str, Any] = dict(spec.parameters)
        self.state: Dict[str, Any] = dict(spec.initial_state)
        self._capabilities: List[Capability] = list(spec.capabilities)

        # 当前有效影响参数（底座通过 set_effects 注入，空集合表示移除临时影响）
        self._effects: Dict[str, Any] = {}

        # 由引擎装配：端口路由、环境模型、通信网络
        self._routing: Dict[str, List[tuple]] = {}
        self._environment: Optional[Any] = None
        self._network: Optional[Any] = None

        # 本步待处理消息（由 receive 写入，step 消费）
        self._inbox: List[Message] = []
        # 本步待发出消息（receive 期间排队，step 时一并返回）
        self._outbox: List[Message] = []

        self._log = logging.getLogger(f"{self.__class__.__name__}({self.asset_id})")

    # ──────────────────────────────────────────────
    # 装配
    # ──────────────────────────────────────────────
    def attach(self, routing: Dict[str, List[tuple]], environment: Any, network: Any) -> None:
        """由引擎在创建后调用，绑定端口路由与共享组件。

        ``routing`` 结构：``{port: [(neighbor_id, neighbor_port, link_id), ...]}``。
        """
        self._routing = routing
        self._environment = environment
        self._network = network

    def _neighbors(self, port: str) -> List[tuple]:
        return self._routing.get(port, [])

    def _port_for(self, neighbor_id: str) -> str:
        """查找通往指定邻居的本地端口。"""
        for port, targets in self._routing.items():
            for nid, _nport, _lid in targets:
                if nid == neighbor_id:
                    return port
        return ""

    # ──────────────────────────────────────────────
    # 消息收发（仅返回消息，不直接调用总线）
    # ──────────────────────────────────────────────
    def receive(self, message: Message) -> None:
        """接收并校验已送达的业务消息，放入待处理队列。"""
        self._inbox.append(message)

    def _drain_inbox(self) -> List[Message]:
        messages = self._inbox
        self._inbox = []
        return messages

    def _queue(self, messages: List[Message]) -> None:
        self._outbox.extend(messages)

    def _drain_outbox(self) -> List[Message]:
        messages = self._outbox
        self._outbox = []
        return messages

    def _new_message(
        self,
        port: str,
        business_type: str,
        payload: Dict[str, Any],
        time_us: int,
        related_request: str = "",
        receiver_id: Optional[str] = None,
        target_port: Optional[str] = None,
    ) -> List[Message]:
        """按端口路由生成待发送消息，发送方为 ``self.asset_id``。"""
        messages: List[Message] = []
        if receiver_id is not None:
            targets = [(receiver_id, target_port or "", "")]
        else:
            targets = self._neighbors(port)

        for neighbor_id, neighbor_port, link_id in targets:
            messages.append(
                Message(
                    message_id=str(uuid.uuid4()),
                    sender_id=self.asset_id,
                    receiver_id=neighbor_id,
                    business_type=business_type,
                    source_port=port,
                    target_port=neighbor_port,
                    related_request=related_request,
                    created_time_us=time_us,
                    deliver_time_us=time_us,
                    payload=payload,
                )
            )
        return messages

    # ──────────────────────────────────────────────
    # 影响参数
    # ──────────────────────────────────────────────
    def set_effects(self, modifiers: Dict[str, Any]) -> None:
        """设置当前有效的影响参数；空集合表示移除临时影响。"""
        self._effects = dict(modifiers or {})

    def _effect(self, key: str, default: Any = None) -> Any:
        return self._effects.get(key, default)

    # ──────────────────────────────────────────────
    # 生命周期接口（子类实现业务）
    # ──────────────────────────────────────────────
    def step(self, time_us: int, dt_us: int, inputs: Dict[str, Any]) -> List[Message]:
        """按采样/控制周期更新本设备，返回待发送消息。"""
        self._drain_inbox()
        return self._drain_outbox()

    def capabilities(self) -> List[Capability]:
        return list(self._capabilities)

    def request_action(self, request: Dict[str, Any]) -> ActionFeedback:
        """接收底座分派的动作，检查能力与联锁，登记执行及反馈时序。"""
        return ActionFeedback(
            action_id=request.get("action_id", ""),
            time_us=request.get("time_us", 0),
            status="rejected",
            failure_reason=f"设备 {self.asset_id} 不支持动作 {request.get('action_type', '')}",
        )

    def read_management(self) -> Dict[str, Any]:
        """返回管理接口实际可读的状态，供防御查询。"""
        return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        """输出模型内部状态，供记录、评估及标明来源的 UI 视图使用。"""
        return {
            "asset_id": self.asset_id,
            "device_type": self.spec.device_type,
            "layer": self.layer,
            "state": dict(self.state),
            "effects": dict(self._effects),
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.asset_id}>"