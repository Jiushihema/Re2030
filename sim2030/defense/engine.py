"""防御平面：策略选择与处置跟踪。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sim2030.contracts import (
    ActionFeedback,
    DefenseRequest,
    DefenseResult,
    ObservationBatch,
    RecognitionResult,
)
from sim2030.defense.strategies import (
    BusinessCompensationStrategy,
    DefenseStrategy,
    FingerprintBlockStrategy,
    FrequencyFilterStrategy,
)

DEFAULT_STRATEGIES = {
    "fingerprint_block": FingerprintBlockStrategy(),
    "frequency_filter": FrequencyFilterStrategy(),
    "business_compensation": BusinessCompensationStrategy(),
}


class DefenseEngine:
    """结合识别结果、观测、能力和可读状态生成处置，并跟踪执行与恢复。"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._strategies: Dict[str, DefenseStrategy] = {}
        self._actions: Dict[str, DefenseResult] = {}
        self._decided: set = set()

    def register(self, name: str, strategy: DefenseStrategy) -> None:
        if not name or not callable(getattr(strategy, "propose", None)):
            raise TypeError("策略必须提供 propose 方法")
        self._strategies[name] = strategy

    def reset(self) -> None:
        self._actions = {}
        self._decided = set()

    def decide(
        self,
        results: List[RecognitionResult],
        observations: Optional[ObservationBatch],
        capabilities: Dict[str, List[Any]],
        management: Dict[str, Dict[str, Any]],
    ) -> List[DefenseRequest]:
        """生成下一步处置请求；证据或能力不足时不强行动作。"""
        requests: List[DefenseRequest] = []
        for result in results:
            if not result.attack_detected:
                continue
            for strategy in self._strategies.values():
                proposed = strategy.propose(result, observations, capabilities, management, self.config)
                for request in proposed:
                    key = (request.action_type, request.target_id)
                    if key in self._decided:
                        continue
                    self._decided.add(key)
                    self._actions[request.action_id] = DefenseResult(
                        action_id=request.action_id,
                        recognition_id=request.recognition_id,
                        action_type=request.action_type,
                        target_asset_ids=[request.target_id] if request.target_id else [],
                        trigger_event_ids=list(result.evidence_event_ids),
                        trigger_confidence=result.confidence,
                        parameters=dict(request.parameters),
                        execution_mode="simulation",
                        status="planned",
                    )
                    requests.append(request)
        return requests

    def update_feedback(
        self,
        feedback: Any,
        observations: Optional[ObservationBatch] = None,
    ) -> List[DefenseResult]:
        """跟踪受理/执行/失败/超时及后续恢复，形成 DefenseResult。"""
        if isinstance(feedback, ActionFeedback):
            action_id = feedback.action_id
            status = feedback.status
            time_us = feedback.time_us
            reason = feedback.failure_reason
        elif isinstance(feedback, dict):
            action_id = feedback.get("action_id", "")
            status = feedback.get("status", "unknown")
            time_us = feedback.get("time_us", 0)
            reason = feedback.get("failure_reason") or feedback.get("reason", "")
        else:
            return []

        action = self._actions.get(action_id)
        if action is None:
            return []

        strategy = self._strategy_for(action.action_type)
        if status == "accepted":
            action.status = "executing"
            action.start_time = str(time_us)
        elif status == "rejected":
            action.status = "failed"
            action.result = {"reason": reason or "底座拒绝执行"}
            action.end_time = str(time_us)
        elif status == "completed":
            recovery = "insufficient"
            if strategy is not None:
                recovery = strategy.assess_recovery(observations, {"parameters": action.parameters})
            if recovery == "succeeded":
                action.status = "succeeded"
                action.result = {"note": "后续观测确认防护有效"}
            elif recovery == "failed":
                action.status = "failed"
                action.result = {"reason": "后续观测未确认恢复"}
            else:
                action.status = "executing"
                action.result = {"note": "执行完成，但恢复证据不足，保留未确认说明"}
            action.end_time = str(time_us)
        elif status == "failed":
            action.status = "failed"
            action.result = {"reason": reason or "执行失败"}
            action.end_time = str(time_us)
        else:
            action.status = status
            action.end_time = str(time_us)

        return [action]

    def _strategy_for(self, action_type: str) -> Optional[DefenseStrategy]:
        for strategy in self._strategies.values():
            if strategy.action_type == action_type:
                return strategy
        return None

    def action(self, action_id: str) -> Optional[DefenseResult]:
        return self._actions.get(action_id)

    def actions(self) -> List[DefenseResult]:
        return list(self._actions.values())


__all__ = ["DefenseEngine", "DEFAULT_STRATEGIES"]