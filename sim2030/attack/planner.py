"""攻击计划与作用请求。

攻击平面只持有不可写的作用计划，输出统一的 :class:`EffectRequest`；是否受理、
执行及最终是否有效由底座和独立评估分别确认。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from sim2030.contracts import EffectRequest

# 默认攻击类型 -> 底座效果类型，便于场景省略 effect_type 时给出基础映射。
ATTACK_EFFECT_MAP = {
    "electromagnetic": "reading_offset",
    "acoustic": "reading_offset",
    "wireless": "link_degradation",
    "network": "execution_delay",
}


def build_effect(step_config: Dict[str, Any]) -> EffectRequest:
    """把电磁/声波/无线/网络或组合攻击步骤转换为统一作用请求。"""
    effect_id = step_config.get("effect_id") or step_config.get("attack_id") or str(uuid.uuid4())
    target_asset_ids = list(step_config.get("target_asset_ids", []))
    target_id = step_config.get("target_id") or (target_asset_ids[0] if target_asset_ids else "")
    effect_type = step_config.get("effect_type") or ATTACK_EFFECT_MAP.get(
        step_config.get("attack_type", ""), "reading_offset"
    )
    return EffectRequest(
        effect_id=str(effect_id),
        target_id=target_id,
        effect_type=effect_type,
        parameters=dict(step_config.get("parameters", {})),
        start_time_us=int(step_config.get("start_time_us", 0)),
        end_time_us=int(step_config.get("end_time_us", 0)),
        sequence=int(step_config.get("sequence", 0)),
    )


class AttackPlanner:
    """按时间及步序依赖输出到期作用请求，并跟踪受理/执行阶段。"""

    def __init__(self) -> None:
        self._steps: List[Dict[str, Any]] = []
        self._state: Dict[str, str] = {}
        self._attacks: List[Dict[str, Any]] = []

    def load(self, plan: List[Dict[str, Any]]) -> None:
        self._steps = []
        self._state = {}
        self._attacks = []
        index: Dict[str, str] = {}
        for step in plan:
            if not isinstance(step, dict):
                continue
            effect_id = str(step.get("effect_id") or step.get("attack_id") or str(uuid.uuid4()))
            enriched = dict(step)
            enriched["_effect_id"] = effect_id
            self._attacks.append(enriched)
            for key in (step.get("effect_id"), step.get("attack_id")):
                if key is not None:
                    index[str(key)] = effect_id
            self._steps.append(enriched)
        self._steps.sort(key=lambda item: (int(item.get("sequence", 0)), int(item.get("start_time_us", 0))))
        # 解析步序依赖为 effect_id。
        for step in self._steps:
            dependencies = step.get("depends_on", []) or []
            step["_dep_effect_ids"] = [index.get(str(dep), str(dep)) for dep in dependencies]

    def due_requests(self, time_us: int) -> List[EffectRequest]:
        """返回本步到期且未提交的作用请求。"""
        due: List[EffectRequest] = []
        for step in self._steps:
            effect_id = step["_effect_id"]
            if effect_id in self._state:
                continue
            if not self._trigger_satisfied(step, time_us):
                continue
            request = build_effect(step)
            request.effect_id = effect_id
            due.append(request)
            self._state[effect_id] = "submitted"
        return due

    def record_receipt(self, receipt: Dict[str, Any]) -> None:
        """保存底座对作用请求的受理/拒绝结果。"""
        effect_id = receipt.get("effect_id") or receipt.get("attack_id")
        if not effect_id:
            return
        self._state[str(effect_id)] = str(receipt.get("status", "unknown"))

    def status(self, effect_id: str) -> Optional[str]:
        return self._state.get(str(effect_id))

    def attacks(self) -> List[Dict[str, Any]]:
        """返回带解析结果的攻击真值列表（仅应用层和评估使用）。"""
        return [dict(step) for step in self._attacks]

    def _trigger_satisfied(self, step: Dict[str, Any], time_us: int) -> bool:
        trigger = step.get("trigger", "time")
        start_us = int(step.get("start_time_us", 0))
        dependencies = step.get("_dep_effect_ids", [])
        if trigger == "after_accepted":
            if not dependencies:
                return time_us >= start_us
            return all(self._state.get(dep) in ("accepted", "executing", "completed") for dep in dependencies)
        if trigger == "after_completed":
            if not dependencies:
                return time_us >= start_us
            return all(self._state.get(dep) == "completed" for dep in dependencies)
        return time_us >= start_us


__all__ = ["AttackPlanner", "build_effect", "ATTACK_EFFECT_MAP"]