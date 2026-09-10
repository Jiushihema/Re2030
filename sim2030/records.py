"""运行记录保存与读取。

记录按流分文件保存为 JSON Lines，原始证据索引保留提供方原事件；内部快照只用于
回放与独立评估，不向识别/防御暴露完整真值。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple


class RunRecorder:
    """分流保存业务、观测、识别、防御、攻击与真值。"""

    def __init__(self, run_id: str, output_dir: str) -> None:
        self.run_id = run_id
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._handles: Dict[str, Any] = {}

    def _handle(self, stream: str):
        if stream not in self._handles:
            path = self.output_dir / f"{stream}.jsonl"
            self._handles[stream] = path.open("w", encoding="utf-8")
        return self._handles[stream]

    def append(self, stream: str, record: Dict[str, Any]) -> None:
        data = dict(record)
        data.setdefault("stream", stream)
        line = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        self._handle(stream).write(line + "\n")

    def append_many(self, stream: str, records: List[Dict[str, Any]]) -> None:
        for record in records:
            self.append(stream, record)

    def save_manifest(self, config_summary: Dict[str, Any]) -> None:
        manifest = dict(config_summary)
        manifest.setdefault("run_id", self.run_id)
        path = self.output_dir / "manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_evidence_index(self, events: List[Dict[str, Any]]) -> None:
        """保存外部观测证据索引，保留提供方原事件与原时间。"""
        self.append_many("observation", events)

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()


class RunReader:
    """按记录时间查询事件及已存状态，供 UI 回放与独立评估使用。"""

    def __init__(self, run_dir: str) -> None:
        self.run_dir = Path(run_dir)

    def streams(self) -> List[str]:
        return sorted(path.stem for path in self.run_dir.glob("*.jsonl"))

    def _iter_stream(self, stream: str) -> Iterator[Dict[str, Any]]:
        path = self.run_dir / f"{stream}.jsonl"
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    @staticmethod
    def _record_time(record: Dict[str, Any]) -> int:
        return int(record.get("time_us", record.get("received_time_us", 0)))

    def read(self, stream: str, time_range: Optional[Tuple[int, int]] = None) -> List[Dict[str, Any]]:
        start, end = time_range if time_range is not None else (None, None)
        records: List[Dict[str, Any]] = []
        for record in self._iter_stream(stream):
            time_us = self._record_time(record)
            if start is not None and time_us < start:
                continue
            if end is not None and time_us > end:
                continue
            records.append(record)
        return records

    def read_all(self, stream: str) -> List[Dict[str, Any]]:
        return self.read(stream)

    def latest(self, stream: str) -> Optional[Dict[str, Any]]:
        records = self.read(stream)
        return records[-1] if records else None

    def snapshot_at(self, time_us: int) -> Dict[str, Any]:
        latest: Dict[str, Any] = {}
        for record in self._iter_stream("truth"):
            if record.get("type") == "snapshot" and self._record_time(record) <= time_us:
                latest = record
        return dict(latest.get("device_snapshots", {}))

    def evidence(self, provider_id: Optional[str] = None, event_id: Optional[str] = None) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for record in self._iter_stream("observation"):
            if record.get("type") == "observation_batch":
                continue
            if provider_id is not None and record.get("provider_id") != provider_id:
                continue
            if event_id is not None and record.get("event_id") != event_id:
                continue
            events.append(record)
        return events

    def manifest(self) -> Dict[str, Any]:
        path = self.run_dir / "manifest.json"
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)