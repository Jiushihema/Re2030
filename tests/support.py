"""测试共用工具：构造合法观测事件与临时场景，不改动现有基础场景。"""
from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SUBSTATION_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scenarios", "substation.json")

OBS_TIME_ORIGIN = "2026-09-09T00:00:00Z"


def load_substation_dict():
    with open(SUBSTATION_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def make_observation_event(**overrides):
    event = {
        "schema_version": "0.1.0",
        "event_id": "e-1",
        "scenario_id": "substation-001",
        "provider_id": "p-business",
        "source_type": "business",
        "source_id": "s-business",
        "timestamp": "2026-09-09T00:00:01Z",
        "observed_at": "2026-09-09T00:00:00Z",
        "time_source": "test-clock",
        "time_quality": "synced",
        "related_asset_ids": ["tap"],
        "association_basis": "configured",
        "status": "anomaly",
        "confidence": 0.9,
        "data_origin": "measured",
        "data_quality": "valid",
        "missing_reason": None,
        "raw_data_uri": "evidence/business.csv",
        "raw_data_format": "csv",
        "raw_metadata_uri": None,
        "metadata": {},
        "data": {
            "asset_id": "tap",
            "metric_name": "oil_temp_c",
            "value": 120.0,
            "normal_max": 80.0,
            "unit": "degC",
        },
    }
    event.update(overrides)
    return event


def write_jsonl(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def write_pipeline_scenario(base_dir, provider_dir, scenario_id="pipeline-001"):
    data = load_substation_dict()
    data["scenario_id"] = scenario_id
    data["recognition"] = {"enabled": True, "window_size_us": 1_000_000}
    data["defense"] = {"enabled": True, "confidence_threshold": 0.5}
    data["attacks"] = [
        {
            "attack_id": "a-network",
            "attack_type": "network",
            "target_asset_ids": ["tap"],
            "start_time_us": 0,
            "end_time_us": 100000,
            "effect_type": "reading_offset",
            "parameters": {"offset": 3.0},
        }
    ]
    # 给 tap 增加业务补偿能力，保证防御平面能提出动作。
    for device in data["devices"]:
        if device["device_id"] == "tap":
            caps = list(device.get("capabilities", []))
            caps.append({"action_type": "business_compensation", "parameter_ranges": {}, "readable_state": ["oil_temp_c"]})
            device["capabilities"] = caps
    data["observations"] = {
        "provider_dir": os.path.abspath(provider_dir),
        "time_mapping": {"origin": OBS_TIME_ORIGIN, "unit": "us"},
        "expected_sources": [{"provider_id": "p-business", "source_type": "business"}],
    }
    path = os.path.join(base_dir, f"{scenario_id}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path
