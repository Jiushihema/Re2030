"""识别平面：观测窗口与识别调用。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sim2030.contracts import ObservationBatch, ObservationEvent, RecognitionResult
from sim2030.recognition.detectors import (
    BusinessDetector,
    CorrelationDetector,
    Detector,
    NetworkDetector,
    SignalDetector,
    WirelessDetector,
)

DEFAULT_DETECTORS = {
    "signal": SignalDetector(),
    "wireless": WirelessDetector(),
    "network": NetworkDetector(),
    "business": BusinessDetector(),
}


class RecognitionEngine:
    """维护有限长度的在线观测窗口，调用单域检测和关联检测。

    输入仅包含观测、约定可获得的拓扑信息、检测配置及模型基线；接口不接收仿真
    引擎、完整场景配置、攻击计划或真值记录读取器。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, topology: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.topology = topology or {}
        self._detectors: Dict[str, Detector] = {}
        self._correlation = CorrelationDetector()
        self._events: List[ObservationEvent] = []
        self._window_size_us: int = int(self.config.get("window_size_us", 1_000_000))
        self._last_results: List[RecognitionResult] = []

    def register(self, name: str, detector: Detector) -> None:
        if not name or not callable(getattr(detector, "detect", None)):
            raise TypeError("检测器必须提供 detect 方法")
        self._detectors[name] = detector

    def reset(self) -> None:
        self._events = []
        self._last_results = []

    def update(self, batch: ObservationBatch, evidence_reader: Any = None) -> List[RecognitionResult]:
        self._append_events(batch)
        window = self._build_window(batch.end_us)
        context = {
            "scenario_id": self.config.get("scenario_id", ""),
            "config": self.config,
            "topology": self.topology,
        }
        results: List[RecognitionResult] = []
        for detector in self._detectors.values():
            try:
                results.extend(detector.detect(window, evidence_reader, context))
            except Exception:
                # 单个检测器异常不阻断整条识别流水线。
                continue
        correlated = self._correlation.correlate(results, window, self.topology, context)
        if correlated is not None:
            results.append(correlated)
        self._last_results = results
        return results

    def _append_events(self, batch: ObservationBatch) -> None:
        self._events.extend(batch.events)
        cutoff = batch.end_us - self._window_size_us
        self._events = [e for e in self._events if e.received_time_us >= cutoff]

    def _build_window(self, end_us: int) -> ObservationBatch:
        start_us = end_us - self._window_size_us
        return ObservationBatch(start_us=start_us, end_us=end_us, events=list(self._events))

    def last_results(self) -> List[RecognitionResult]:
        return list(self._last_results)


__all__ = ["RecognitionEngine", "DEFAULT_DETECTORS"]