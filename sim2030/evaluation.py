"""独立效果评估。

评估者掌握真值，但不能把真值提供给监测者或防御者。评估只从已保存记录读取数据，
按约定判定规则计算指标；没有有效样本或证据不足时返回不可评估及原因。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Sequence

from sim2030.contracts import EvaluationResult
from sim2030.records import RunReader


def _to_reader(run_records) -> RunReader:
    if isinstance(run_records, RunReader):
        return run_records
    return RunReader(str(run_records))


def _as_result(value) -> EvaluationResult:
    if isinstance(value, EvaluationResult):
        return value
    if isinstance(value, dict):
        return EvaluationResult(
            evaluation_id=value.get("evaluation_id", ""),
            scenario_id=value.get("scenario_id", ""),
            baseline_scenario_id=value.get("baseline_scenario_id"),
            recognition_metrics=dict(value.get("recognition_metrics", {})),
            defense_metrics=dict(value.get("defense_metrics", {})),
            business_metrics=dict(value.get("business_metrics", {})),
            overall_score=value.get("overall_score"),
        )
    raise TypeError(f"无法识别的评估结果类型：{type(value)}")


def _match_attacks(attacks: List[Dict[str, Any]], recognitions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把识别结果匹配到攻击真值；每项包含 attack 与最佳 recognition。"""
    matched: List[Dict[str, Any]] = []
    for attack in attacks:
        best = None
        best_score = 0.0
        for recognition in recognitions:
            if not recognition.get("attack_detected"):
                continue
            score = _overlap_score(attack, recognition)
            if score > best_score:
                best = recognition
                best_score = score
        matched.append({"attack": attack, "recognition": best, "score": best_score})
    return matched


def _overlap_score(attack: Dict[str, Any], recognition: Dict[str, Any]) -> float:
    targets = set(attack.get("target_asset_ids", []))
    detected = set(recognition.get("affected_asset_ids", [])) or set(recognition.get("target_asset_ids", []))
    if targets and detected:
        return len(targets & detected) / max(1, len(targets | detected))
    return 0.5  # 无目标信息时给中性分，仍允许按攻击类型匹配


class Evaluator:
    """从运行记录计算识别、防御与业务指标，并比较基准/防御两组结果。"""

    def __init__(self, rules: Optional[Dict[str, Any]] = None):
        self.rules = rules or {}

    def evaluate(self, run_records, rules: Optional[Dict[str, Any]] = None) -> EvaluationResult:
        rules = rules or self.rules
        reader = _to_reader(run_records)
        manifest = reader.manifest()
        scenario_id = manifest.get("scenario_id", "")

        truth = reader.read("truth")
        attacks = [r for r in truth if r.get("type") == "attack"]
        snapshots = [r for r in truth if r.get("type") == "snapshot"]
        recognitions = reader.read("recognition")
        defenses = reader.read("defense")

        recognition_metrics = self._recognition_metrics(attacks, recognitions, rules)
        defense_metrics = self._defense_metrics(attacks, defenses, rules)
        business_metrics = self._business_metrics(attacks, snapshots, rules)

        return EvaluationResult(
            evaluation_id=str(uuid.uuid4()),
            scenario_id=scenario_id,
            recognition_metrics=recognition_metrics,
            defense_metrics=defense_metrics,
            business_metrics=business_metrics,
            overall_score=self._overall_score(recognition_metrics, defense_metrics, business_metrics),
        )

    def compare(self, baseline, defended) -> EvaluationResult:
        base = _as_result(baseline)
        defended = _as_result(defended)
        metrics = dict(defended.business_metrics)
        base_deviation = _number(base.business_metrics.get("maximum_deviation"))
        defended_deviation = _number(defended.business_metrics.get("maximum_deviation"))
        if base_deviation is not None and defended_deviation is not None and base_deviation > 0:
            metrics["impact_reduction_rate"] = max(0.0, 1.0 - defended_deviation / base_deviation)
        else:
            metrics["impact_reduction_rate"] = None
            metrics["uncomparable_reason"] = "基准业务偏差缺失或为零，无法计算影响降低率"

        return EvaluationResult(
            evaluation_id=str(uuid.uuid4()),
            scenario_id=defended.scenario_id,
            baseline_scenario_id=base.scenario_id,
            recognition_metrics=dict(defended.recognition_metrics),
            defense_metrics=dict(defended.defense_metrics),
            business_metrics=metrics,
            overall_score=defended.overall_score,
        )

    # ──────────────────────────────────────────────
    # 识别指标
    # ──────────────────────────────────────────────
    def _recognition_metrics(self, attacks, recognitions, rules) -> Dict[str, Any]:
        if not attacks:
            return {"evaluable": False, "reason": "无有效攻击样本"}
        if not recognitions:
            return {"evaluable": False, "reason": "无识别结果"}

        matched = _match_attacks(attacks, recognitions)
        detected = [m for m in matched if m["recognition"] is not None]
        detection_count = len(detected)
        attack_count = len(attacks)
        metrics: Dict[str, Any] = {
            "evaluable": True,
            "attack_count": attack_count,
            "detection_count": detection_count,
            "miss_count": attack_count - detection_count,
            "detection_result": detection_count / attack_count if attack_count else 0.0,
        }

        type_correct = 0
        target_scores = []
        delays_ms = []
        for item in detected:
            attack = item["attack"]
            recognition = item["recognition"]
            if recognition.get("attack_type") == attack.get("attack_type"):
                type_correct += 1
            target_scores.append(_overlap_score(attack, recognition))
            delay = _time_delay_ms(attack.get("start_time_us"), recognition.get("start_time_us"))
            if delay is not None:
                delays_ms.append(delay)
        metrics["type_correct"] = type_correct / detection_count if detection_count else 0.0
        metrics["target_match_rate"] = sum(target_scores) / len(target_scores) if target_scores else 0.0
        metrics["detection_delay_ms"] = min(delays_ms) if delays_ms else None
        return metrics

    # ──────────────────────────────────────────────
    # 防御指标
    # ──────────────────────────────────────────────
    def _defense_metrics(self, attacks, defenses, rules) -> Dict[str, Any]:
        if not attacks:
            return {"evaluable": False, "reason": "无有效攻击样本"}
        if not defenses:
            return {"evaluable": False, "reason": "无防御执行结果"}

        defenses = self._dedupe_by_key(defenses, "action_id")
        action_count = len(defenses)
        success = sum(1 for d in defenses if d.get("status") == "succeeded")
        correct = 0
        for defense in defenses:
            if self._action_matches_any_attack(defense, attacks):
                correct += 1
        delays_ms = []
        for defense in defenses:
            attack = self._best_attack_for_action(defense, attacks)
            if attack is not None:
                delay = _time_delay_ms(attack.get("start_time_us"), defense.get("start_time"))
                if delay is not None:
                    delays_ms.append(delay)
        return {
            "evaluable": True,
            "action_triggered": action_count,
            "action_correct": correct / action_count if action_count else 0.0,
            "action_success": success / action_count if action_count else 0.0,
            "response_delay_ms": min(delays_ms) if delays_ms else None,
        }

    @staticmethod
    def _action_matches_any_attack(defense: Dict[str, Any], attacks: List[Dict[str, Any]]) -> bool:
        return Evaluator._best_attack_for_action(defense, attacks) is not None

    @staticmethod
    def _best_attack_for_action(defense, attacks):
        best = None
        best_score = 0.0
        action_targets = set(defense.get("target_asset_ids", []))
        for attack in attacks:
            targets = set(attack.get("target_asset_ids", []))
            score = 0.0
            if targets and action_targets:
                score = len(targets & action_targets) / max(1, len(targets | action_targets))
            else:
                score = 0.5
            if score > best_score:
                best = attack
                best_score = score
        return best

    # ──────────────────────────────────────────────
    # 业务指标
    # ──────────────────────────────────────────────
    def _business_metrics(self, attacks, snapshots, rules) -> Dict[str, Any]:
        if not attacks:
            return {"evaluable": False, "reason": "无有效攻击样本"}
        if not snapshots:
            return {"evaluable": False, "reason": "缺少业务状态快照"}

        metric = self._select_metric(attacks, snapshots, rules)
        if metric is None:
            return {"evaluable": False, "reason": "未找到可评估的业务量测字段"}

        asset_id, field = metric
        series = self._metric_series(snapshots, asset_id, field)
        if len(series) < 2:
            return {"evaluable": False, "reason": "业务量测样本不足"}

        baseline = series[0][1]
        deviations = [abs(value - baseline) for _time, value in series]
        maximum_deviation = max(deviations)

        attack_end_us = max((a.get("end_time_us", a.get("start_time_us", 0)) for a in attacks), default=0)
        tolerance = float(rules.get("business", {}).get("recovery_tolerance", 0.0))
        recovery_time_ms = None
        for time_us, value in series:
            if time_us >= attack_end_us and abs(value - baseline) <= tolerance:
                recovery_time_ms = (time_us - attack_end_us) // 1000
                break

        return {
            "evaluable": True,
            "metric_asset_id": asset_id,
            "metric_name": field,
            "maximum_deviation": maximum_deviation,
            "recovery_time_ms": recovery_time_ms,
            "impact_reduction_rate": None,
        }

    def _select_metric(self, attacks, snapshots, rules):
        business_rules = rules.get("business", {})
        asset_id = business_rules.get("metric_asset_id")
        field = business_rules.get("metric_name")
        if asset_id and field:
            return (asset_id, field)
        for attack in attacks:
            for target in attack.get("target_asset_ids", []):
                for snapshot in snapshots:
                    devices = snapshot.get("device_snapshots", {})
                    device = devices.get(target)
                    if not device:
                        continue
                    state = device.get("state") or {}
                    for key, value in state.items():
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            return (target, key)
        return None

    @staticmethod
    def _dedupe_by_key(records, key):
        latest = {}
        for record in records:
            value = record.get(key)
            if value is not None:
                latest[value] = record
        return list(latest.values())

    @staticmethod
    def _metric_series(snapshots, asset_id, field):
        series = []
        for snapshot in sorted(snapshots, key=lambda s: int(s.get("time_us", 0))):
            device = snapshot.get("device_snapshots", {}).get(asset_id)
            if not device:
                continue
            state = device.get("state") or {}
            if field in state and isinstance(state[field], (int, float)):
                series.append((int(snapshot.get("time_us", 0)), float(state[field])))
        return series

    @staticmethod
    def _overall_score(recognition_metrics, defense_metrics, business_metrics) -> Optional[float]:
        components = []
        if recognition_metrics.get("evaluable"):
            components.append(_number(recognition_metrics.get("detection_result"), 0.0))
            components.append(_number(recognition_metrics.get("target_match_rate"), 0.0))
        if defense_metrics.get("evaluable"):
            components.append(_number(defense_metrics.get("action_success"), 0.0))
        if not components:
            return None
        return round(sum(components) / len(components), 4)


def _number(value, default: Optional[float] = None) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _time_delay_ms(start_us: Any, end_us: Any) -> Optional[float]:
    try:
        start = float(start_us)
        end = float(end_us)
    except (TypeError, ValueError):
        return None
    return max(0.0, (end - start) / 1000.0)


__all__ = ["Evaluator"]