"""跨模块数据类型契约。

本模块只包含数据对象与纯工具函数，不包含任何仿真逻辑，也不反向导入业务模块。
约定：

- 内部仿真时间统一为相对场景起点的整数微秒，参数名使用 ``time_us`` / ``dt_us``。
- 外部观测事件保留原始带时区时间字符串，不在本层改写调度时钟。
- 观测引用统一通过 :func:`event_key` 生成，避免不同提供方事件编号冲突。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
import json

# 动作受理/执行状态（依据详细开发设计 3.2）
ACTION_STATUS = ("accepted", "rejected", "executing", "completed", "failed")


@dataclass
class Capability:
    """设备实际支持的动作能力及其可读管理状态。"""
    action_type: str
    parameter_ranges: Dict[str, Any] = field(default_factory=dict)
    interlock: List[str] = field(default_factory=list)
    readable_state: List[str] = field(default_factory=list)


@dataclass
class DeviceSpec:
    """场景中的单台设备声明。"""
    device_id: str
    device_type: str
    layer: str
    name: str = ""
    ports: Dict[str, str] = field(default_factory=dict)
    parameters: Dict[str, Any] = field(default_factory=dict)
    initial_state: Dict[str, Any] = field(default_factory=dict)
    capabilities: List[Capability] = field(default_factory=list)
    references: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeviceSpec":
        caps = data.get("capabilities", [])
        return cls(
            device_id=data["device_id"],
            device_type=data["device_type"],
            layer=data.get("layer", "process"),
            name=data.get("name", ""),
            ports=dict(data.get("ports", {})),
            parameters=dict(data.get("parameters", {})),
            initial_state=dict(data.get("initial_state", {})),
            capabilities=[Capability(**c) if isinstance(c, dict) else c for c in caps],
            references=list(data.get("references", [])),
        )


@dataclass
class LinkSpec:
    """设备间连接声明。

    ``endpoint_a`` / ``endpoint_b`` 为 ``(device_id, port)``；连接类型决定由网络
    投递还是由环境模型处理：

    - ``physical`` / ``electrical``：环境模型处理，不进消息队列。
    - ``hardwire`` / ``wired`` / ``wireless``：由通信网络投递消息。
    """
    link_id: str
    endpoint_a: Tuple[str, str]
    endpoint_b: Tuple[str, str]
    link_type: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    references: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LinkSpec":
        return cls(
            link_id=data["link_id"],
            endpoint_a=tuple(data["endpoint_a"]),
            endpoint_b=tuple(data["endpoint_b"]),
            link_type=data.get("link_type", "wired"),
            parameters=dict(data.get("parameters", {})),
            references=list(data.get("references", [])),
        )


@dataclass
class Message:
    """内部设备交互消息。

    消息只描述源/目标端口、业务类型和载荷；送达时间由通信网络根据链路时延计算。
    """
    message_id: str
    sender_id: str
    receiver_id: str
    business_type: str
    source_port: str = ""
    target_port: str = ""
    related_request: str = ""
    created_time_us: int = 0
    deliver_time_us: int = 0
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EffectRequest:
    """攻击平面向底座提交的作用请求。"""
    effect_id: str
    target_id: str
    effect_type: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    start_time_us: int = 0
    end_time_us: int = 0
    sequence: int = 0


@dataclass
class DefenseRequest:
    """防御平面向底座提交的处置请求。"""
    action_id: str
    target_id: str
    action_type: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    recognition_id: str = ""
    valid_from_us: int = 0
    valid_until_us: int = 0


@dataclass
class ActionFeedback:
    """动作受理/执行反馈，不把“执行完成”等同于“防护有效”。"""
    action_id: str
    time_us: int
    status: str
    failure_reason: str = ""
    feedback: Dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StepResult:
    """单步调度结果，只交给调度、记录与独立评估。"""
    time_us: int
    device_snapshots: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    messages: List[Message] = field(default_factory=list)
    action_feedback: List[ActionFeedback] = field(default_factory=list)
    effect_records: List[Dict[str, Any]] = field(default_factory=list)
    network_state: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ObservationEvent:
    """五类外部 JSON 事件的类型化封装，保留原事件。"""
    provider_id: str
    event_id: str
    source_type: str
    raw: Dict[str, Any] = field(default_factory=dict)
    received_time_us: int = 0

    def key(self) -> str:
        return event_key(self.provider_id, self.event_id)


@dataclass
class ObservationBatch:
    """当前查询窗口内可用观测及缺失/迟到/关联状态。"""
    start_us: int
    end_us: int
    events: List[ObservationEvent] = field(default_factory=list)
    missing: List[Dict[str, Any]] = field(default_factory=list)
    late: List[Dict[str, Any]] = field(default_factory=list)
    associations: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RecognitionResult:
    """识别结果（字段沿用系统角色设计 3.5.3）。"""
    recognition_id: str
    scenario_id: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    attack_detected: bool = False
    attack_type: str = "unknown"
    attack_behavior: str = ""
    recognition_status: str = "suspected"
    target_asset_ids: List[str] = field(default_factory=list)
    affected_asset_ids: List[str] = field(default_factory=list)
    evidence_event_ids: List[str] = field(default_factory=list)
    confidence: float = 0.0
    severity: str = "low"
    recognition_details: Dict[str, Any] = field(default_factory=dict)
    recommended_actions: List[str] = field(default_factory=list)


@dataclass
class DefenseResult:
    """防御执行结果（字段沿用系统角色设计 3.5.5）。"""
    action_id: str
    recognition_id: str = ""
    action_type: str = ""
    target_asset_ids: List[str] = field(default_factory=list)
    trigger_event_ids: List[str] = field(default_factory=list)
    trigger_confidence: float = 0.0
    parameters: Dict[str, Any] = field(default_factory=dict)
    execution_mode: str = "simulation"
    status: str = "planned"
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    result: Dict[str, Any] = field(default_factory=dict)
    before_state: Dict[str, Any] = field(default_factory=dict)
    after_state: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationResult:
    """单场景评估结果（字段沿用系统角色设计 3.6）。"""
    evaluation_id: str
    scenario_id: str
    baseline_scenario_id: Optional[str] = None
    recognition_metrics: Dict[str, Any] = field(default_factory=dict)
    defense_metrics: Dict[str, Any] = field(default_factory=dict)
    business_metrics: Dict[str, Any] = field(default_factory=dict)
    overall_score: Optional[float] = None


@dataclass
class ScenarioConfig:
    """完整场景配置（仅应用层组装；识别/防御不获得完整配置）。"""
    scenario_id: str
    name: str = ""
    simulation: Dict[str, Any] = field(default_factory=dict)
    devices: List[DeviceSpec] = field(default_factory=list)
    links: List[LinkSpec] = field(default_factory=list)
    environment: Dict[str, Any] = field(default_factory=dict)
    operations: List[Dict[str, Any]] = field(default_factory=list)
    attacks: List[Dict[str, Any]] = field(default_factory=list)
    recognition: Dict[str, Any] = field(default_factory=dict)
    defense: Dict[str, Any] = field(default_factory=dict)
    observations: Dict[str, Any] = field(default_factory=dict)
    evaluation: Dict[str, Any] = field(default_factory=dict)
    references: Dict[str, Any] = field(default_factory=dict)


def event_key(provider_id: str, event_id: str) -> str:
    """观测引用统一键：二元素 JSON 数组的字符串表示。"""
    return json.dumps([provider_id, event_id], ensure_ascii=False, separators=(",", ":"))