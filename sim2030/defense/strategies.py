"""三类防御策略接口与规则基线。

策略只输出建议，由底座执行；``assess_recovery`` 依据后续观测判断是否恢复，不能
把“执行完成”等同于“防护有效”。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from sim2030.contracts import DefenseRequest, RecognitionResult, ObservationBatch


def _now_us(observations: Optional[ObservationBatch]) -> int:
    if observations is not None:
        return int(observations.end_us)
    return 0


def _first_events(observations: Optional[ObservationBatch], source_types: tuple) -> List:
    if observations is None:
        return []
    return [e for e in observations.events if e.source_type in source_types]


class DefenseStrategy:
    """策略公共接口：分别给出处置建议与可观测恢复判据。"""

    name: str = "base"
    action_type: str = ""

    def propose(
        self,
        result: RecognitionResult,
        observations: Optional[ObservationBatch],
        capabilities: Dict[str, List[Any]],
        management: Dict[str, Dict[str, Any]],
        config: Optional[Dict[str, Any]] = None,
    ) -> List[DefenseRequest]:
        raise NotImplementedError

    def assess_recovery(self, observations: Optional[ObservationBatch], action: Dict[str, Any]) -> str:
        """返回 succeeded / failed / insufficient。"""
        return "insufficient"


class FingerprintBlockStrategy(DefenseStrategy):
    """射频指纹阻断：需可确认身份/会话及阻断能力，否则不强行动作。"""

    name = "fingerprint_block"
    action_type = "rf_fingerprint_block"

    def propose(self, result, observations, capabilities, management, config=None):
        config = config or {}
        if result.attack_type != "wireless":
            return []
        threshold = float(config.get("confidence_threshold", 0.6))
        if result.confidence < threshold:
            return []
        if result.attack_behavior != "identity_spoofing":
            return []
        targets = result.target_asset_ids or result.affected_asset_ids
        if not targets:
            return []
        requests: List[DefenseRequest] = []
        for target in targets:
            if not self._supports(target, self.action_type, capabilities):
                continue
            receiver_id = self._find_receiver(observations)
            requests.append(DefenseRequest(
                action_id=str(uuid.uuid4()),
                target_id=target,
                action_type=self.action_type,
                parameters={
                    "receiver_id": receiver_id,
                    "block_duration_ms": int(config.get("block_duration_ms", 60000)),
                },
                recognition_id=result.recognition_id,
                valid_from_us=_now_us(observations),
                valid_until_us=_now_us(observations) + int(config.get("valid_window_us", 1_000_000)),
            ))
        return requests

    @staticmethod
    def _supports(target: str, action_type: str, capabilities: Dict[str, List[Any]]) -> bool:
        caps = capabilities.get(target, [])
        for cap in caps:
            if getattr(cap, "action_type", None) == action_type:
                return True
        return False

    @staticmethod
    def _find_receiver(observations: Optional[ObservationBatch]) -> Optional[str]:
        for event in _first_events(observations, ("wireless",)):
            receiver = event.raw.get("data", {}).get("receiver_id")
            if receiver:
                return receiver
        return None

    def assess_recovery(self, observations, action):
        events = _first_events(observations, ("wireless",))
        if not events:
            return "insufficient"
        if all(e.raw.get("status", "unknown") in ("normal", "unknown") for e in events):
            return "succeeded"
        return "failed"


class FrequencyFilterStrategy(DefenseStrategy):
    """调频滤波规避：根据异常频段及设备能力提出参数。"""

    name = "frequency_filter"
    action_type = "frequency_filter_avoidance"

    def propose(self, result, observations, capabilities, management, config=None):
        config = config or {}
        if result.attack_type not in ("electromagnetic", "acoustic", "wireless", "coordinated"):
            return []
        threshold = float(config.get("confidence_threshold", 0.6))
        if result.confidence < threshold:
            return []
        anomaly = self._anomaly_band(result, observations)
        if anomaly is None:
            return []
        targets = result.affected_asset_ids or result.target_asset_ids
        if not targets:
            return []
        requests: List[DefenseRequest] = []
        for target in targets:
            if not self._supports(target, self.action_type, capabilities):
                continue
            old_frequency = anomaly.get("frequency_hz")
            requests.append(DefenseRequest(
                action_id=str(uuid.uuid4()),
                target_id=target,
                action_type=self.action_type,
                parameters={
                    "old_frequency_hz": old_frequency,
                    "new_frequency_hz": config.get("fallback_frequency_hz"),
                    "filter_center_frequency_hz": old_frequency,
                    "filter_bandwidth_hz": anomaly.get("bandwidth_hz"),
                },
                recognition_id=result.recognition_id,
                valid_from_us=_now_us(observations),
                valid_until_us=_now_us(observations) + int(config.get("valid_window_us", 1_000_000)),
            ))
        return requests

    @staticmethod
    def _anomaly_band(result, observations) -> Optional[Dict[str, Any]]:
        for source_type in ("em", "acoustic", "wireless"):
            for event in _first_events(observations, (source_type,)):
                data = event.raw.get("data", {})
                frequency = data.get("center_frequency_hz") or data.get("dominant_frequency_hz")
                if frequency is not None:
                    return {
                        "frequency_hz": float(frequency),
                        "bandwidth_hz": data.get("bandwidth_hz") or data.get("capture_bandwidth_hz"),
                    }
        return None

    @staticmethod
    def _supports(target, action_type, capabilities):
        for cap in capabilities.get(target, []):
            if getattr(cap, "action_type", None) == action_type:
                return True
        return False

    def assess_recovery(self, observations, action):
        events = _first_events(observations, ("em", "acoustic", "wireless"))
        if not events:
            return "insufficient"
        if all(e.raw.get("status", "unknown") in ("normal", "unknown") for e in events):
            return "succeeded"
        return "failed"


class BusinessCompensationStrategy(DefenseStrategy):
    """业务补偿修复：对允许补偿的测点/业务提出替代值及有效期。"""

    name = "business_compensation"
    action_type = "business_compensation"

    def propose(self, result, observations, capabilities, management, config=None):
        config = config or {}
        if result.attack_type not in ("unknown", "electromagnetic", "acoustic", "coordinated"):
            return []
        threshold = float(config.get("confidence_threshold", 0.5))
        if result.confidence < threshold:
            return []
        events = _first_events(observations, ("business",))
        if not events:
            return []
        targets = result.affected_asset_ids or result.target_asset_ids
        requests: List[DefenseRequest] = []
        for event in events:
            data = event.raw.get("data", {})
            asset_id = data.get("asset_id")
            if asset_id and targets and asset_id not in targets:
                continue
            if not asset_id or not self._supports(asset_id, self.action_type, capabilities):
                continue
            metric_name = data.get("metric_name")
            value = data.get("value")
            normal_min = data.get("normal_min")
            normal_max = data.get("normal_max")
            compensated_value = self._compensate(value, normal_min, normal_max, management.get(asset_id))
            requests.append(DefenseRequest(
                action_id=str(uuid.uuid4()),
                target_id=asset_id,
                action_type=self.action_type,
                parameters={
                    "asset_id": asset_id,
                    "metric_name": metric_name,
                    "compensated_value": compensated_value,
                    "unit": data.get("unit"),
                    "valid_until": _now_us(observations) + int(config.get("valid_window_us", 1_000_000)),
                },
                recognition_id=result.recognition_id,
                valid_from_us=_now_us(observations),
                valid_until_us=_now_us(observations) + int(config.get("valid_window_us", 1_000_000)),
            ))
        return requests

    @staticmethod
    def _supports(target, action_type, capabilities):
        for cap in capabilities.get(target, []):
            if getattr(cap, "action_type", None) == action_type:
                return True
        return False

    @staticmethod
    def _compensate(value, normal_min, normal_max, management):
        if normal_min is not None and normal_max is not None:
            try:
                return (float(normal_min) + float(normal_max)) / 2.0
            except (TypeError, ValueError):
                pass
        if isinstance(value, (int, float)):
            return value
        if isinstance(management, dict):
            state = management.get("state") or {}
            if state:
                return state
        return None

    def assess_recovery(self, observations, action):
        asset_id = (action.get("parameters") or {}).get("asset_id")
        events = [e for e in _first_events(observations, ("business",)) if e.raw.get("data", {}).get("asset_id") == asset_id]
        if not events:
            return "insufficient"
        for event in events:
            data = event.raw.get("data", {})
            value = data.get("value")
            if isinstance(value, (int, float)):
                normal_min = data.get("normal_min")
                normal_max = data.get("normal_max")
                if normal_min is not None and value < normal_min:
                    return "failed"
                if normal_max is not None and value > normal_max:
                    return "failed"
            if event.raw.get("status") == "anomaly":
                return "failed"
        return "succeeded"


__all__ = [
    "DefenseStrategy",
    "FingerprintBlockStrategy",
    "FrequencyFilterStrategy",
    "BusinessCompensationStrategy",
]