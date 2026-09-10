"""单域及关联识别检测器接口。

检测器只消费观测窗口、证据读取入口和配置基线，不接收仿真引擎、完整场景配置或
攻击真值。这里的规则基线与统计基线用于调通流程，输出必须标明方法与版本。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from sim2030.contracts import ObservationBatch, RecognitionResult


def _method_label() -> str:
    return "rule_baseline_v0.1"


def _event_key(event) -> str:
    return event.key()


def _make_result(
    scenario_id: str,
    attack_type: str,
    attack_behavior: str,
    target_asset_ids: List[str],
    affected_asset_ids: List[str],
    evidence_event_ids: List[str],
    confidence: float,
    severity: str,
    details: Optional[Dict[str, Any]] = None,
) -> RecognitionResult:
    return RecognitionResult(
        recognition_id=str(uuid.uuid4()),
        scenario_id=scenario_id,
        attack_detected=True,
        attack_type=attack_type,
        attack_behavior=attack_behavior,
        recognition_status="suspected",
        target_asset_ids=sorted(set(target_asset_ids)),
        affected_asset_ids=sorted(set(affected_asset_ids)),
        evidence_event_ids=sorted(set(evidence_event_ids)),
        confidence=min(1.0, max(0.0, float(confidence))),
        severity=severity,
        recognition_details=dict(details or {}),
        recommended_actions=[],
    )


def _related(event) -> List[str]:
    return list(event.raw.get("related_asset_ids", [])) if event.raw else []


class Detector:
    """统一检测接口，输出判断或证据不足；不强制每个窗口都产生告警。"""

    name: str = "base"
    source_types: tuple = ()

    def detect(
        self,
        batch: ObservationBatch,
        evidence_reader: Any = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[RecognitionResult]:
        raise NotImplementedError


class SignalDetector(Detector):
    """电磁/声波单域检测：按状态、信噪比或功率标志识别异常。"""

    name = "signal"
    source_types = ("em", "acoustic")

    def detect(self, batch, evidence_reader=None, context=None) -> List[RecognitionResult]:
        context = context or {}
        config = context.get("config", {})
        scenario_id = context.get("scenario_id", "")
        snr_threshold = float(config.get("snr_threshold_db", 6.0))
        events = [e for e in batch.events if e.source_type in self.source_types]
        candidates = []
        for event in events:
            data = event.raw.get("data", {})
            status = event.raw.get("status", "unknown")
            snr = data.get("snr_db")
            power = data.get("power_dbm")
            anomalous = status == "anomaly"
            if snr is not None and float(snr) > snr_threshold:
                anomalous = True
            if power is not None and float(power) > float(config.get("power_threshold_dbm", -40.0)):
                anomalous = True
            if anomalous:
                candidates.append(event)
        if not candidates:
            return []

        attack_type = "electromagnetic" if any(e.source_type == "em" for e in candidates) else "acoustic"
        behaviors = []
        for event in candidates:
            pattern = (event.raw.get("data") or {}).get("signal_pattern")
            if pattern:
                behaviors.append(pattern)
        targets = []
        for event in candidates:
            targets.extend(_related(event))
        confidences = [float(e.raw.get("confidence")) for e in candidates if e.raw.get("confidence") is not None]
        confidence = max(confidences) if confidences else 0.6
        details = {
            "method": _method_label(),
            "signal_pattern": behaviors[0] if behaviors else "unknown",
            "evidence_count": len(candidates),
        }
        return [_make_result(
            scenario_id=scenario_id,
            attack_type=attack_type,
            attack_behavior=behaviors[0] if behaviors else "unknown",
            target_asset_ids=targets,
            affected_asset_ids=targets,
            evidence_event_ids=[_event_key(e) for e in candidates],
            confidence=confidence,
            severity="medium" if confidence >= 0.7 else "low",
            details=details,
        )]


class WirelessDetector(Detector):
    """无线SDR单域检测：链路状态、丢包率或声明身份异常。"""

    name = "wireless"
    source_types = ("wireless",)

    def detect(self, batch, evidence_reader=None, context=None) -> List[RecognitionResult]:
        context = context or {}
        config = context.get("config", {})
        scenario_id = context.get("scenario_id", "")
        loss_threshold = float(config.get("packet_loss_threshold", 0.3))
        events = [e for e in batch.events if e.source_type == "wireless"]
        candidates = []
        for event in events:
            data = event.raw.get("data", {})
            status = event.raw.get("status", "unknown")
            link_state = data.get("link_state")
            loss = data.get("packet_loss_rate")
            anomalous = status == "anomaly" or link_state in ("degraded", "down")
            if loss is not None and float(loss) > loss_threshold:
                anomalous = True
            if anomalous:
                candidates.append(event)
        if not candidates:
            return []
        targets = []
        for event in candidates:
            targets.extend(_related(event))
            receiver_id = event.raw.get("data", {}).get("receiver_id")
            if receiver_id:
                targets.append(receiver_id)
        behavior = "communication_jamming"
        if any((e.raw.get("data") or {}).get("claimed_transmitter_id") for e in candidates):
            behavior = "identity_spoofing"
        confidences = [float(e.raw.get("confidence")) for e in candidates if e.raw.get("confidence") is not None]
        confidence = max(confidences) if confidences else 0.6
        return [_make_result(
            scenario_id=scenario_id,
            attack_type="wireless",
            attack_behavior=behavior,
            target_asset_ids=targets,
            affected_asset_ids=targets,
            evidence_event_ids=[_event_key(e) for e in candidates],
            confidence=confidence,
            severity="high" if confidence >= 0.7 else "medium",
            details={"method": _method_label(), "behavior": behavior, "evidence_count": len(candidates)},
        )]


class NetworkDetector(Detector):
    """网络IDS单域检测：告警事件或状态异常。"""

    name = "network"
    source_types = ("ids",)

    def detect(self, batch, evidence_reader=None, context=None) -> List[RecognitionResult]:
        context = context or {}
        scenario_id = context.get("scenario_id", "")
        events = [e for e in batch.events if e.source_type == "ids"]
        candidates = []
        for event in events:
            data = event.raw.get("data", {})
            status = event.raw.get("status", "unknown")
            if status == "anomaly" or data.get("event_type") == "alert":
                candidates.append(event)
        if not candidates:
            return []
        targets = []
        for event in candidates:
            data = event.raw.get("data", {})
            for field in ("src_asset_id", "dst_asset_id"):
                value = data.get(field)
                if value:
                    targets.append(value)
            targets.extend(_related(event))
        alert_types = [e.raw.get("data", {}).get("alert_type") for e in candidates if e.raw.get("data", {}).get("alert_type")]
        confidences = [float(e.raw.get("confidence")) for e in candidates if e.raw.get("confidence") is not None]
        confidence = max(confidences) if confidences else 0.6
        return [_make_result(
            scenario_id=scenario_id,
            attack_type="network",
            attack_behavior=alert_types[0] if alert_types else "anomalous_traffic",
            target_asset_ids=targets,
            affected_asset_ids=targets,
            evidence_event_ids=[_event_key(e) for e in candidates],
            confidence=confidence,
            severity="high" if confidence >= 0.7 else "medium",
            details={"method": _method_label(), "alert_types": alert_types, "evidence_count": len(candidates)},
        )]


class BusinessDetector(Detector):
    """业务量测单域检测：突变、越限或控制状态偏差。"""

    name = "business"
    source_types = ("business",)

    def detect(self, batch, evidence_reader=None, context=None) -> List[RecognitionResult]:
        context = context or {}
        scenario_id = context.get("scenario_id", "")
        events = [e for e in batch.events if e.source_type == "business"]
        candidates = []
        for event in events:
            data = event.raw.get("data", {})
            status = event.raw.get("status", "unknown")
            value = data.get("value")
            normal_min = data.get("normal_min")
            normal_max = data.get("normal_max")
            deviation = data.get("deviation")
            anomalous = status == "anomaly"
            if isinstance(value, (int, float)):
                if normal_min is not None and value < normal_min:
                    anomalous = True
                if normal_max is not None and value > normal_max:
                    anomalous = True
            if deviation is not None and abs(float(deviation)) > 1e-9:
                anomalous = True
            if anomalous:
                candidates.append(event)
        if not candidates:
            return []
        targets = []
        metrics = []
        for event in candidates:
            data = event.raw.get("data", {})
            asset_id = data.get("asset_id")
            if asset_id:
                targets.append(asset_id)
            metrics.append(data.get("metric_name"))
            targets.extend(_related(event))
        confidences = [float(e.raw.get("confidence")) for e in candidates if e.raw.get("confidence") is not None]
        confidence = max(confidences) if confidences else 0.5
        return [_make_result(
            scenario_id=scenario_id,
            attack_type="unknown",
            attack_behavior="business_anomaly",
            target_asset_ids=targets,
            affected_asset_ids=targets,
            evidence_event_ids=[_event_key(e) for e in candidates],
            confidence=confidence,
            severity="medium" if confidence >= 0.6 else "low",
            details={"method": _method_label(), "metrics": sorted(set(filter(None, metrics))), "evidence_count": len(candidates)},
        )]


class CorrelationDetector:
    """把多个单域判断组合为协同攻击判断。"""

    name = "correlation"

    def correlate(
        self,
        results: List[RecognitionResult],
        batch: ObservationBatch,
        topology: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[RecognitionResult]:
        detected = [r for r in results if r.attack_detected]
        if len(detected) < 2:
            return None
        attack_types = {r.attack_type for r in detected}
        if len(attack_types) < 2:
            return None
        context = context or {}
        scenario_id = context.get("scenario_id", "")
        targets: List[str] = []
        affected: List[str] = []
        evidence: List[str] = []
        behaviors: List[str] = []
        for result in detected:
            targets.extend(result.target_asset_ids)
            affected.extend(result.affected_asset_ids)
            evidence.extend(result.evidence_event_ids)
            if result.attack_behavior:
                behaviors.append(result.attack_behavior)
        confidence = min(r.confidence for r in detected)
        return _make_result(
            scenario_id=scenario_id,
            attack_type="coordinated",
            attack_behavior="coordinated:" + "+".join(sorted(behaviors)),
            target_asset_ids=targets,
            affected_asset_ids=affected,
            evidence_event_ids=evidence,
            confidence=confidence,
            severity="critical" if confidence >= 0.7 else "high",
            details={"method": _method_label(), "components": sorted(attack_types)},
        )


__all__ = [
    "Detector",
    "SignalDetector",
    "WirelessDetector",
    "NetworkDetector",
    "BusinessDetector",
    "CorrelationDetector",
]