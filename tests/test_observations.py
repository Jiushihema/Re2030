"""外部观测接入：校验、固定文件源、网关去重/缺失/迟到与证据读取。"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim2030.base.observations import (
    EvidenceReader,
    FileObservationSource,
    ObservationGateway,
    associate_event,
    map_event_time,
    parse_iso_us,
    validate_observation_event,
)
from tests.support import make_observation_event, write_jsonl


class TestValidation(unittest.TestCase):
    def test_valid_event(self):
        self.assertEqual(validate_observation_event(make_observation_event()), [])

    def test_missing_required_field(self):
        event = make_observation_event()
        del event["provider_id"]
        errors = validate_observation_event(event)
        self.assertTrue(any("provider_id" in error for error in errors))

    def test_bad_schema_version(self):
        event = make_observation_event(schema_version="9.9.9")
        self.assertTrue(any("schema_version" in error for error in validate_observation_event(event)))

    def test_bad_enum_and_confidence(self):
        event = make_observation_event(source_type="nope", confidence=2.0)
        errors = validate_observation_event(event)
        joined = " ".join(errors)
        self.assertIn("source_type", joined)
        self.assertIn("confidence", joined)

    def test_missing_quality_requires_reason(self):
        event = make_observation_event(data_quality="missing", missing_reason=None)
        self.assertTrue(any("missing_reason" in error for error in validate_observation_event(event)))


class TestFileObservationSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "providers")
        os.makedirs(self.data_dir, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fixed_file_source_preserves_raw_and_does_not_deliver_future(self):
        origin = "2026-09-09T00:00:00Z"
        future = make_observation_event(
            event_id="future", observed_at="2026-09-09T00:00:05Z", timestamp="2026-09-09T00:00:06Z"
        )
        past = make_observation_event(
            event_id="past", observed_at="2026-09-09T00:00:00Z", timestamp="2026-09-09T00:00:01Z"
        )
        path = write_jsonl(os.path.join(self.data_dir, "events.jsonl"), [future, past])
        source = FileObservationSource({"path": path, "time_mapping": {"origin": origin, "unit": "us"}})

        delivered = source.read_available(0)
        self.assertEqual([item["event_id"] for item in delivered], ["past"])
        self.assertEqual(delivered[0]["observed_at"], past["observed_at"])

        delivered = source.read_available(5_000_000)
        self.assertEqual([item["event_id"] for item in delivered], ["future"])

    def test_dedup_and_no_repeat_delivery(self):
        event = make_observation_event(observed_at="2026-09-09T00:00:00Z", timestamp="2026-09-09T00:00:01Z")
        path = write_jsonl(os.path.join(self.data_dir, "events.jsonl"), [event])
        source = FileObservationSource({"path": path, "time_mapping": {"origin": "2026-09-09T00:00:00Z"}})
        self.assertEqual(len(source.read_available(10)), 1)
        self.assertEqual(source.read_available(10), [])


class TestGateway(unittest.TestCase):
    def test_ingest_dedup_reject_missing_late(self):
        gateway = ObservationGateway({
            "mapping": {"s-business": "tap"},
            "time_mapping": {"origin": "2026-09-09T00:00:00Z", "unit": "us"},
            "expected_sources": [{"provider_id": "p-business", "source_type": "business"}],
        })
        valid = make_observation_event(observed_at="2026-09-09T00:00:00Z")
        batch = gateway.ingest([valid, valid, {"schema_version": "0.1.0"}], 0)
        self.assertEqual(len(batch.events), 1)

        window = gateway.window(0, 1_000_000)
        self.assertEqual(len(window.events), 1)
        self.assertEqual(window.missing, [])
        self.assertEqual(window.late, [])

        # 时间已过窗口，应列为迟到。
        gateway.ingest([make_observation_event(event_id="e-late", observed_at="2026-09-09T00:00:00Z")], 5_000_000)
        window = gateway.window(4_000_000, 5_000_000)
        self.assertEqual(window.events, [])
        self.assertEqual(len(window.late), 2)

    def test_missing_expected_source(self):
        gateway = ObservationGateway({"expected_sources": [{"provider_id": "p-missing", "source_type": "em"}]})
        window = gateway.window(0, 1_000_000)
        self.assertEqual(len(window.missing), 1)
        self.assertEqual(window.missing[0]["reason"], "no_data_in_window")


class TestEvidenceReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_csv_and_missing_and_invalid(self):
        csv_path = os.path.join(self.base, "business.csv")
        with open(csv_path, "w", encoding="utf-8") as handle:
            handle.write("metric_name,value\n")
            handle.write("oil_temp_c,120.0\n")
        reader = EvidenceReader(self.base)
        event = make_observation_event(raw_data_uri="business.csv", raw_data_format="csv")
        segment = reader.read_segment(event, 0, 5)
        self.assertEqual(segment["status"], "ok")
        self.assertEqual(segment["records"][0]["value"], "120.0")

        missing = reader.read_segment(make_observation_event(raw_data_uri="nope.csv", raw_data_format="csv"))
        self.assertEqual(missing["status"], "missing")

        invalid = reader.read_segment(make_observation_event(raw_data_uri="../escape.csv", raw_data_format="csv"))
        self.assertEqual(invalid["status"], "invalid_path")


if __name__ == "__main__":
    unittest.main()
