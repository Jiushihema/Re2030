"""五类外部数据接入与证据读取。

该模块只消费协同方交付物，不向识别/防御暴露完整真值。观测事件保留提供方原字段
与原时间；固定文件回放只支持接入与离线处理，交互能力明确返回不支持。
"""
from __future__ import annotations

import base64
import csv
import heapq
import io
import json
import os
import re
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sim2030.contracts import ObservationBatch, ObservationEvent

SOURCE_TYPES = ("em", "acoustic", "wireless", "ids", "business")

_REQUIRED_FIELDS = (
    "schema_version",
    "event_id",
    "scenario_id",
    "provider_id",
    "source_type",
    "source_id",
    "timestamp",
    "observed_at",
    "time_source",
    "time_quality",
    "related_asset_ids",
    "association_basis",
    "status",
    "confidence",
    "data_origin",
    "data_quality",
    "missing_reason",
    "raw_data_uri",
    "raw_data_format",
    "raw_metadata_uri",
    "metadata",
    "data",
)

_ENUM_FIELDS: Dict[str, Tuple[str, ...]] = {
    "source_type": SOURCE_TYPES,
    "time_quality": ("synced", "unsynced", "unknown"),
    "association_basis": ("configured", "inferred", "unknown"),
    "status": ("normal", "anomaly", "offline", "unknown"),
    "data_origin": ("measured", "replay", "synthetic"),
    "data_quality": ("valid", "degraded", "missing"),
    "raw_data_format": ("sigmf", "wav", "eve_json", "pcap", "pcapng", "csv", None),
}

_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$"
)


def _parse_offset(value: str) -> timezone:
    sign = 1 if value[0] == "+" else -1
    hours = int(value[1:3])
    minutes = int(value[4:6])
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def parse_iso_us(value: str) -> int:
    """把带时区的 ISO 8601 时间转换为 Unix 微秒。"""
    match = _ISO_RE.match(value)
    if not match:
        raise ValueError(f"无法解析时间 {value!r}")
    year, month, day, hour, minute, second = (int(x) for x in match.groups()[:6])
    fraction = match.group(7)
    microsecond = int(fraction.ljust(6, "0")[:6]) if fraction else 0
    zone = match.group(8)
    tzinfo = timezone.utc if zone == "Z" else _parse_offset(zone)
    dt = datetime(year, month, day, hour, minute, second, microsecond, tzinfo=tzinfo)
    return int(dt.timestamp() * 1_000_000)


def map_event_time(value: Optional[str], time_mapping: Optional[Dict[str, Any]]) -> Optional[int]:
    """按配置把外部事件时间映射为相对场景起点的微秒。无法映射时返回 None。"""
    if not value or not time_mapping:
        return None
    origin = time_mapping.get("origin")
    if not origin:
        return None
    unit = time_mapping.get("unit", "us")
    try:
        elapsed_us = parse_iso_us(value) - parse_iso_us(origin)
    except ValueError:
        return None
    if unit in ("ms", "millisecond"):
        elapsed_us *= 1000
    elif unit in ("s", "second"):
        elapsed_us *= 1_000_000
    return elapsed_us


def validate_observation_event(raw: Dict[str, Any]) -> List[str]:
    """轻量校验五类监测事件，返回错误信息列表；空列表表示通过。"""
    errors: List[str] = []
    if not isinstance(raw, dict):
        return ["事件不是 JSON 对象"]
    for field in _REQUIRED_FIELDS:
        if field not in raw:
            errors.append(f"缺少字段 {field}")
    if raw.get("schema_version") != "0.1.0":
        errors.append("schema_version 必须为 0.1.0")
    for field, allowed in _ENUM_FIELDS.items():
        if field not in raw:
            continue
        value = raw[field]
        if field == "raw_data_format":
            if value is not None and value not in allowed:
                errors.append(f"{field} 取值非法：{value}")
        elif value not in allowed:
            errors.append(f"{field} 取值非法：{value}")
    confidence = raw.get("confidence")
    if confidence is not None and (not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0):
        errors.append("confidence 必须在 [0,1] 内")
    related = raw.get("related_asset_ids")
    if related is not None and (not isinstance(related, list) or any(not isinstance(x, str) for x in related)):
        errors.append("related_asset_ids 必须是字符串数组")
    for time_field in ("timestamp", "observed_at"):
        value = raw.get(time_field)
        if isinstance(value, str) and not _ISO_RE.match(value):
            errors.append(f"{time_field} 不是合法的带时区 ISO 8601 时间")
    if raw.get("data_quality") == "missing" and not raw.get("missing_reason"):
        errors.append("data_quality=missing 时必须填写 missing_reason")
    return errors


class ObservationSource:
    """数据源接口：返回当前时刻已经可交付的观测事件。"""

    def read_available(self, time_us: int) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def sync_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """可选交互能力；固定文件源不支持，不能据此生成新证据。"""
        return {"status": "unsupported", "reason": "固定文件回放不支持交互式工况同步"}


class FileObservationSource(ObservationSource):
    """按配置的交付时间表读取 JSONL 文件，支持固定回放。

    配置项：``data_dir``/``provider_dir``/``path``、``file_pattern``、``delay_us``、
    ``time_mapping.origin``、``schedule``。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}
        self.delay_us = int(config.get("delay_us", 0))
        self.time_mapping = config.get("time_mapping") or {}
        self.schedule: Dict[str, int] = {}
        schedule = config.get("schedule") or {}
        if isinstance(schedule, dict):
            for key, value in schedule.items():
                self.schedule[str(key)] = int(value)
        elif isinstance(schedule, list):
            for item in schedule:
                if isinstance(item, dict) and "event_id" in item:
                    self.schedule[str(item["event_id"])] = int(item.get("delivery_time_us", 0))

        self._pending: List[Tuple[int, int, Dict[str, Any]]] = []
        self._seq = 0
        self._delivered_keys: set = set()
        for raw in self._read_files(config):
            delivery = self._delivery_time_us(raw)
            self._seq += 1
            heapq.heappush(self._pending, (delivery, self._seq, raw))

    def _read_files(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        root = config.get("path") or config.get("data_dir") or config.get("provider_dir")
        if not root:
            return []
        root_path = Path(root)
        pattern = config.get("file_pattern", "*.jsonl")
        files: Iterable[Path]
        if root_path.is_dir():
            files = sorted(root_path.glob(pattern))
        else:
            files = [root_path]
        records: List[Dict[str, Any]] = []
        for path in files:
            if not path.exists() or path.suffix.lower() not in (".jsonl", ".json"):
                continue
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            records.append({"_parse_error": True, "_path": str(path), "_line": line})
                            continue
                        if isinstance(item, dict):
                            records.append(item)
            except OSError:
                continue
        return records

    def _delivery_time_us(self, raw: Dict[str, Any]) -> int:
        event_id = raw.get("event_id")
        if event_id is not None and str(event_id) in self.schedule:
            return self.schedule[str(event_id)]
        value = raw.get("observed_at") or raw.get("timestamp")
        elapsed = map_event_time(value, self.time_mapping) if isinstance(value, str) else None
        if elapsed is None:
            # 无时间映射时视为初始即已交付，但窗口映射仍保留为未知。
            return self.delay_us
        return elapsed + self.delay_us

    def read_available(self, time_us: int) -> List[Dict[str, Any]]:
        delivered: List[Dict[str, Any]] = []
        while self._pending and self._pending[0][0] <= time_us:
            _delivery, _seq, raw = heapq.heappop(self._pending)
            key = _event_key_from_raw(raw)
            if key in self._delivered_keys:
                continue
            self._delivered_keys.add(key)
            delivered.append(raw)
        return delivered

    def sync_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "unsupported", "reason": "固定文件回放不支持交互式工况同步"}


def _event_key_from_raw(raw: Dict[str, Any]) -> str:
    provider_id = raw.get("provider_id", "")
    event_id = raw.get("event_id", "")
    return json.dumps([provider_id, event_id], ensure_ascii=False, separators=(",", ":"))


def associate_event(
    event: Dict[str, Any],
    mapping: Optional[Dict[str, Any]] = None,
    time_mapping: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """按显式配置关联设备与场景时间，保留原事件、原时间及映射依据。"""
    mapping = mapping or {}
    asset_ids = list(event.get("related_asset_ids", []))
    basis = event.get("association_basis", "unknown")
    source_id = event.get("source_id")
    provider_id = event.get("provider_id")
    mapped = None
    for key in (source_id, provider_id):
        if key is None:
            continue
        value = mapping.get(key)
        if isinstance(value, str):
            mapped = value
            break
        if isinstance(value, dict) and value.get("asset_ids"):
            mapped = value["asset_ids"]
            break
    if mapped is not None:
        if isinstance(mapped, str):
            if mapped not in asset_ids:
                asset_ids.append(mapped)
        else:
            for item in mapped:
                if item not in asset_ids:
                    asset_ids.append(item)
        if basis == "unknown":
            basis = "configured"
    scene_time_us = map_event_time(event.get("observed_at") or event.get("timestamp"), time_mapping)
    return {
        "asset_ids": asset_ids,
        "basis": basis,
        "scene_time_us": scene_time_us,
        "source_id": source_id,
        "provider_id": provider_id,
    }


class ObservationGateway:
    """校验、去重并保存观测事件，提供带缺失/迟到/关联状态的查询窗口。"""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        mapping: Optional[Dict[str, Any]] = None,
        time_mapping: Optional[Dict[str, Any]] = None,
    ):
        config = config or {}
        self.mapping = mapping if mapping is not None else config.get("mapping", {})
        self.time_mapping = time_mapping if time_mapping is not None else config.get("time_mapping", {})
        self.schema_path = config.get("schema_path")
        self._events: Dict[str, ObservationEvent] = {}
        self._associations: Dict[str, Dict[str, Any]] = {}
        self._seen: set = set()
        self._rejected: List[Dict[str, Any]] = []
        self._expected: List[Dict[str, Any]] = list(config.get("expected_sources", []))

    def ingest(self, records: Sequence[Dict[str, Any]], received_at: int) -> ObservationBatch:
        batch = ObservationBatch(start_us=received_at, end_us=received_at)
        for raw in records:
            errors = validate_observation_event(raw)
            if errors:
                self._rejected.append({"raw": raw, "received_time_us": received_at, "errors": errors})
                continue
            event = ObservationEvent(
                provider_id=raw["provider_id"],
                event_id=raw["event_id"],
                source_type=raw["source_type"],
                raw=dict(raw),
                received_time_us=received_at,
            )
            key = event.key()
            if key in self._seen:
                continue
            self._seen.add(key)
            self._events[key] = event
            self._associations[key] = associate_event(raw, self.mapping, self.time_mapping)
            batch.events.append(event)
        return batch

    def window(
        self,
        start_us: int,
        end_us: int,
        source_types: Optional[Sequence[str]] = None,
    ) -> ObservationBatch:
        source_types = tuple(source_types) if source_types else None
        events: List[ObservationEvent] = []
        late: List[Dict[str, Any]] = []
        associations: Dict[str, Any] = {}
        for key, event in self._events.items():
            if source_types and event.source_type not in source_types:
                continue
            association = self._associations.get(key, {})
            associations[key] = association
            scene_time_us = association.get("scene_time_us")
            if scene_time_us is None:
                # 无法映射时间的事件保留，但不强行纳入跨域时序判断。
                continue
            if start_us <= scene_time_us <= end_us:
                events.append(event)
            elif scene_time_us < start_us:
                late.append({"event_key": key, "scene_time_us": scene_time_us, "source_type": event.source_type})
        missing = self._missing(start_us, end_us, source_types)
        return ObservationBatch(
            start_us=start_us,
            end_us=end_us,
            events=events,
            missing=missing,
            late=late,
            associations=associations,
        )

    def _missing(self, start_us: int, end_us: int, source_types: Optional[Tuple[str, ...]]) -> List[Dict[str, Any]]:
        missing: List[Dict[str, Any]] = []
        present: set = set()
        for event in self._events.values():
            association = self._associations.get(event.key(), {})
            scene_time_us = association.get("scene_time_us")
            if scene_time_us is not None and start_us <= scene_time_us <= end_us:
                present.add((event.provider_id, event.source_type))
        for expected in self._expected:
            provider_id = expected.get("provider_id", "")
            source_type = expected.get("source_type", "")
            if source_types and source_type not in source_types:
                continue
            if (provider_id, source_type) not in present:
                missing.append({
                    "provider_id": provider_id,
                    "source_type": source_type,
                    "source_id": expected.get("source_id"),
                    "reason": "no_data_in_window",
                })
        return missing

    def rejected(self) -> List[Dict[str, Any]]:
        return list(self._rejected)

    def event(self, provider_id: str, event_id: str) -> Optional[ObservationEvent]:
        key = json.dumps([provider_id, event_id], ensure_ascii=False, separators=(",", ":"))
        return self._events.get(key)

    def association(self, provider_id: str, event_id: str) -> Dict[str, Any]:
        key = json.dumps([provider_id, event_id], ensure_ascii=False, separators=(",", ":"))
        return dict(self._associations.get(key, {}))


class EvidenceReader:
    """按需读取证据描述与片段，不一次加载所有波形。"""

    def __init__(self, base_dir: str):
        self.base_dir = os.path.abspath(base_dir)

    def _resolve(self, uri: str) -> str:
        candidate = os.path.normpath(os.path.join(self.base_dir, uri))
        if os.path.commonpath([candidate, self.base_dir]) != self.base_dir:
            raise ValueError(f"证据路径越界：{uri}")
        return candidate

    def describe(self, event: Dict[str, Any]) -> Dict[str, Any]:
        uri = event.get("raw_data_uri")
        fmt = event.get("raw_data_format")
        metadata = event.get("metadata") or {}
        result: Dict[str, Any] = {
            "raw_data_uri": uri,
            "raw_data_format": fmt,
            "raw_metadata_uri": event.get("raw_metadata_uri"),
            "metadata": metadata,
        }
        if not uri:
            result["status"] = "no_evidence"
            return result
        try:
            path = self._resolve(uri)
        except ValueError as exc:
            result["status"] = "invalid_path"
            result["reason"] = str(exc)
            return result
        if not os.path.exists(path):
            result["status"] = "missing"
            result["reason"] = f"证据文件不存在：{path}"
            return result
        result["size"] = os.path.getsize(path)
        result["status"] = "available"
        if fmt == "sigmf":
            meta_uri = event.get("raw_metadata_uri")
            if meta_uri:
                try:
                    with open(self._resolve(meta_uri), "r", encoding="utf-8") as handle:
                        result["sigmf_metadata"] = json.load(handle)
                except (OSError, ValueError, json.JSONDecodeError):
                    result["sigmf_metadata"] = None
        elif fmt == "wav":
            result.update(self._wav_header(path))
        return result

    def read_segment(self, event: Dict[str, Any], start: int = 0, count: int = 10) -> Dict[str, Any]:
        if count < 0:
            count = 0
        uri = event.get("raw_data_uri")
        fmt = event.get("raw_data_format")
        if not uri:
            return {"status": "no_evidence"}
        try:
            path = self._resolve(uri)
        except ValueError as exc:
            return {"status": "invalid_path", "reason": str(exc)}
        if not os.path.exists(path):
            return {"status": "missing", "reason": f"证据文件不存在：{path}"}

        if fmt == "csv":
            return self._read_csv(path, start, count)
        if fmt == "eve_json":
            return self._read_jsonl(path, start, count)
        if fmt == "wav":
            return self._read_wav_segment(path, start, count)
        if fmt in ("sigmf", "pcap", "pcapng"):
            return self._read_raw_binary(path, start, count)
        return {"status": "unsupported", "reason": f"不支持的证据格式 {fmt}"}

    def _read_csv(self, path: str, start: int, count: int) -> Dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = [row for row in reader]
        except OSError as exc:
            return {"status": "error", "reason": str(exc)}
        segment = rows[start:start + count]
        return {"status": "ok", "format": "csv", "records": segment, "count": len(segment), "total": len(rows)}

    def _read_jsonl(self, path: str, start: int, count: int) -> Dict[str, Any]:
        records: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        records.append({"_parse_error": str(exc), "_line": line})
        except OSError as exc:
            return {"status": "error", "reason": str(exc)}
        segment = records[start:start + count]
        return {"status": "ok", "format": "eve_json", "records": segment, "count": len(segment), "total": len(records)}

    def _read_raw_binary(self, path: str, start: int, count: int) -> Dict[str, Any]:
        try:
            with open(path, "rb") as handle:
                handle.seek(start)
                data = handle.read(count)
        except OSError as exc:
            return {"status": "error", "reason": str(exc)}
        return {
            "status": "raw_binary",
            "byte_start": start,
            "byte_count": len(data),
            "bytes_base64": base64.b64encode(data).decode("ascii"),
        }

    def _wav_header(self, path: str) -> Dict[str, Any]:
        try:
            with open(path, "rb") as handle:
                head = handle.read(44)
            if len(head) < 44 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
                return {"wav_header": "unrecognized"}
            channels = struct.unpack("<H", head[22:24])[0]
            sample_rate = struct.unpack("<I", head[24:28])[0]
            bits = struct.unpack("<H", head[34:36])[0]
            return {"wav_header": {"channels": channels, "sample_rate_hz": sample_rate, "bit_depth": bits}}
        except OSError:
            return {"wav_header": None}

    def _read_wav_segment(self, path: str, start: int, count: int) -> Dict[str, Any]:
        header = self._wav_header(path)
        try:
            with open(path, "rb") as handle:
                head = handle.read(44)
                if len(head) < 44 or head[0:4] != b"RIFF":
                    return {"status": "error", "reason": "不是合法的 WAV 文件"}
                data_size = struct.unpack("<I", head[40:44])[0]
                data_offset = 44
                handle.seek(data_offset + start)
                data = handle.read(count)
        except OSError as exc:
            return {"status": "error", "reason": str(exc)}
        return {
            "status": "raw_binary",
            "format": "wav",
            "wav_header": header.get("wav_header"),
            "data_size": data_size,
            "byte_start": start,
            "byte_count": len(data),
            "bytes_base64": base64.b64encode(data).decode("ascii"),
        }


__all__ = [
    "ObservationSource",
    "FileObservationSource",
    "ObservationGateway",
    "EvidenceReader",
    "associate_event",
    "validate_observation_event",
    "map_event_time",
    "parse_iso_us",
]