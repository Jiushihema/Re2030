"""演示视图整理函数。

这些函数只从运行记录生成可展示结构，不重新执行仿真、攻击或防御，也不另算一套
评估结论。
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Optional

from sim2030.records import RunReader


def _reader(records) -> RunReader:
    if isinstance(records, RunReader):
        return records
    return RunReader(str(records))


def _last_truth_snapshot(reader: RunReader) -> Dict[str, Any]:
    snapshot = {}
    for record in reader._iter_stream("truth"):
        if record.get("type") == "snapshot":
            snapshot = record
    return snapshot


def _derive_environment(reader: RunReader, fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """从业务报文与设备快照还原环境概览。

    旧记录中 truth 快照不包含环境量，但电流/电压互感器的采样报文足够恢复
    ``line_current_a`` 与 ``bus_voltage_kv``，其余字段给稳定默认值，保证 UI
    拓扑动态文本有可读来源。
    """
    env = {
        "bus_voltage_kv": 10.0,
        "line_current_a": 0.0,
        "reactive_power_var": 0.0,
        "ambient_temp_c": 30.0,
        "cooling_on": False,
    }
    if fallback:
        env.update(fallback)

    for record in reader._iter_stream("business"):
        business_type = record.get("business_type")
        payload = record.get("payload") or {}
        if business_type == "sampling":
            sensor_id = payload.get("sensor_id") or record.get("sender_id")
            value = payload.get("value")
            if not isinstance(value, (int, float)):
                continue
            if sensor_id == "ct_current":
                env["line_current_a"] = float(value)
            elif sensor_id == "vt_voltage":
                env["bus_voltage_kv"] = float(value)

    snapshot = _last_truth_snapshot(reader)
    device_snapshots = snapshot.get("device_snapshots", {})
    cool = device_snapshots.get("cool", {})
    if isinstance(cool, dict):
        state = cool.get("state") or {}
        if "running" in state:
            env["cooling_on"] = bool(state.get("running"))
    return env


def _derive_in_flight_messages(reader: RunReader) -> List[Dict[str, Any]]:
    """旧运行没有独立持久化的在途队列，回放时返回空队列。"""
    return []


def build_system_view(records) -> Dict[str, Any]:
    """整理设备拓扑、内部业务状态及外部观测摘要，标明来源和缺失。"""
    reader = _reader(records)
    manifest = reader.manifest()
    snapshot = _last_truth_snapshot(reader)
    device_snapshots = snapshot.get("device_snapshots", {})
    devices = []
    for asset_id, info in sorted(device_snapshots.items()):
        devices.append({
            "device_id": asset_id,
            "device_type": info.get("device_type"),
            "layer": info.get("layer"),
            "name": info.get("name") or info.get("device_type") or asset_id,
            "state": info.get("state"),
            "effects": info.get("effects"),
        })

    events = reader.evidence()
    latest_batch: Dict[str, Any] = {}
    for record in reader._iter_stream("observation"):
        if record.get("type") == "observation_batch":
            latest_batch = record
    by_source: Dict[str, int] = {}
    for event in events:
        by_source[event.get("source_type", "unknown")] = by_source.get(event.get("source_type", "unknown"), 0) + 1

    environment = snapshot.get("environment") or _derive_environment(reader)
    return {
        "run_id": manifest.get("run_id"),
        "scenario_id": manifest.get("scenario_id"),
        "status": "finished",
        "time_us": snapshot.get("time_us", 0),
        "topology": {"devices": devices, "links": manifest.get("links", [])},
        "device_snapshots": device_snapshots,
        "management": device_snapshots,
        "environment": environment,
        "in_flight_messages": _derive_in_flight_messages(reader),
        "observations": {
            "event_count": len(events),
            "by_source": by_source,
            "missing": latest_batch.get("missing", []),
            "late": latest_batch.get("late", []),
        },
        "recognition": reader.read("recognition"),
        "defense": reader.read("defense"),
    }


def build_timeline(records) -> Dict[str, Any]:
    """按时间组织攻击计划/执行、识别、防御和业务响应。"""
    reader = _reader(records)
    attacks = [r for r in reader.read("truth") if r.get("type") == "attack"]
    submissions = [r for r in reader.read("attack") if r.get("type") == "attack_submission"]
    recognitions = reader.read("recognition")
    defenses = reader.read("defense")
    business = reader.read("business")

    return {
        "attacks": attacks,
        "attack_submissions": submissions,
        "recognitions": recognitions,
        "defenses": defenses,
        "business": business,
    }


def _as_eval(value) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    return asdict(value)


def build_comparison(evaluations) -> Dict[str, Any]:
    """整理多组场景指标及业务曲线，供 UI 展示既有评估结论。"""
    rows = [_as_eval(value) for value in evaluations]
    return {
        "runs": rows,
        "comparison": {
            "impact_reduction_rate": [
                row.get("business_metrics", {}).get("impact_reduction_rate") for row in rows
            ],
            "overall_score": [row.get("overall_score") for row in rows],
            "detection_result": [
                row.get("recognition_metrics", {}).get("detection_result") for row in rows
            ],
        },
    }
