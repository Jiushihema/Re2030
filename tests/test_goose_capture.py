import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from sim2030.goose_codec import parse_goose_frame
from sim2030.goose_capture import GooseCapture, read_pcap, reference_checks, normal_baseline
from sim2030.goose_lab import GooseLab, VISIBLE_RETENTION_US
from sim2030.base.observations import FileObservationSource, validate_observation_event
from sim2030.application import Application
from sim2030.constants import BusinessType
from sim2030.contracts import Message
from sim2030.presentation.server import PresentationServer
from sim2030.presentation.views import build_goose_view
from sim2030.scenario import create_device, load_scenario, validate_scenario
from tools.import_message_core import import_package

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 1789084800


def tlv(tag, value):
    length = bytes([len(value)]) if len(value) < 128 else b"\x81" + bytes([len(value)])
    return bytes([tag]) + length + value


def make_frame(st=1, sq=1, source="000c29100001", trip=False, appid=12289):
    clock = struct.pack(">I", ORIGIN) + b"\x00" * 4
    # 与真实样本一致：数据集 4 个条目，首条为 [跳闸位, 品质位, 时标]。
    quality = tlv(0x84, b"\x0d\x00\x00")

    def entry(first):
        return tlv(0xA2, first + quality + tlv(0x91, clock))

    dataset = (entry(tlv(0x83, bytes([trip]))) + entry(tlv(0x84, b"\x08\x00"))
               + entry(tlv(0x83, b"\x01")) + entry(tlv(0x83, b"\x00")))
    parts = [(0x80, b"PROT/LLN0$GO$gcbTrip"), (0x81, b"\x01\xf4"), (0x82, b"PROT/LLN0$dsTrip"),
             (0x83, b"test-goose"), (0x84, clock), (0x85, bytes([st])), (0x86, bytes([sq])),
             (0x87, b"\0"), (0x88, b"\1"), (0x89, b"\0"), (0x8A, b"\x04"), (0xAB, dataset)]
    pdu = tlv(0x61, b"".join(tlv(tag, value) for tag, value in parts))
    ethernet = bytes.fromhex("010ccd013001" + source) + struct.pack(">HHH", 0x8100, 0x8003, 0x88B8)
    return ethernet + struct.pack(">HHHH", appid, len(pdu) + 8, 0, 0) + pdu


def sample_package(path, corrupt=False):
    contents = {}
    for sample in ("normal", "forgery", "suppression", "dos"):
        packets = [(0, make_frame()), (200000, make_frame(sq=2)), (400000, make_frame(st=2))]
        if sample != "normal":
            packets.insert(1, (500, make_frame(st=7, source="000c29660066", trip=True, appid=12291)))
        pcap = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
        records = []
        for i, (us, raw) in enumerate(packets, 1):
            pcap += struct.pack("<IIII", ORIGIN, us, len(raw), len(raw)) + raw
            records.append(parse_goose_frame(raw, ORIGIN + us / 1000000, i, "test/CB-IN"))
        log = ("\n".join(json.dumps(row) for row in records) + "\n").encode()
        manifest = {"frame_count": len(records), "base_time_utc": ORIGIN, "capture_point": "test/CB-IN",
                    "goose_config": {"publisher": "保护", "subscriber": "智能终端",
                                     "gocbRef": "PROT/LLN0$GO$gcbTrip", "datSet": "PROT/LLN0$dsTrip",
                                     "confRev": 1, "appid": 12289, "vlan_id": 3},
                    "data_set": [{"name": "trip"}]}
        contents[f"communication-log-{sample}.jsonl"] = log
        contents[f"readback-{sample}.jsonl"] = log
        contents[f"goose-{sample}.pcap"] = pcap
        contents[f"manifest-{sample}.json"] = json.dumps(manifest).encode()
        contents[f"labels-{sample}.jsonl"] = b'{"truth":"DO_NOT_USE_THIS_LABEL","outcome":"unknown"}\n'
    manifest = "\n".join(f"{hashlib.sha256(data).hexdigest()} {len(data)} {name}" for name, data in contents.items())
    manifest += "\n" + "0" * 64 + " 1 absent-document.md"
    if corrupt:
        contents["goose-forgery.pcap"] += b"bad"
    with zipfile.ZipFile(path, "w") as package:
        for name, data in contents.items():
            package.writestr("message_core/" + name, data)
        package.writestr("message_core/MANIFEST_sha256.txt", manifest)


class TestGooseCapture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.archive = self.root / "samples.zip"
        sample_package(self.archive)
        self.data = self.root / "data"
        self.report = import_package(self.archive, self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def scene(self, sample="forgery"):
        scene = json.loads((ROOT / "scenarios/substation-live.json").read_text(encoding="utf-8"))
        scene["scenario_id"] = "test-goose"
        scene["simulation"]["duration_us"] = 600000
        scene["layout"]["packet_replay"] = {"sample": sample}
        scene["observations"] = {"provider_dir": str(self.data / sample), "file_pattern": "events.jsonl"}
        path = self.root / "scene.json"
        path.write_text(json.dumps(scene), encoding="utf-8")
        return path

    def test_import_preserves_packet_log_and_keeps_labels_separate(self):
        self.assertEqual(self.report["missing_manifest_entries"], ["absent-document.md"])
        capture = GooseCapture(self.data / "forgery")
        frame = capture.view(1000, frame_no=2)["frame"]
        self.assertEqual(frame["capture_time_iso"], "2026-09-11T00:00:00.000500Z")
        self.assertEqual(frame["record"]["src_mac"], "00:0c:29:66:00:66")
        self.assertTrue(frame["trip"])
        self.assertIn("来源 MAC 偏离正常样本", frame["checks"])
        for file in (self.data / "forgery").iterdir():
            self.assertNotIn(b"DO_NOT_USE_THIS_LABEL", file.read_bytes())
        self.assertIn(b"DO_NOT_USE_THIS_LABEL", (self.data / "evaluation/labels-forgery.jsonl").read_bytes())

    def test_frame_visibility_is_causal_and_pagination_bounded(self):
        capture = GooseCapture(self.data / "forgery")
        self.assertEqual(capture.view(-1)["visible_frames"], 0)
        self.assertIn("error", capture.view(0, frame_no=2))
        self.assertEqual(capture.view(500)["visible_frames"], 2)
        self.assertEqual([f["frame_no"] for f in capture.view(500000, before_frame=3)["frames"]], [2, 1])
        self.assertEqual(capture.view(0)["check_counts"], {})

    def test_source_mapping_and_batch_counts(self):
        capture = GooseCapture(self.data / "forgery")
        config = {"provider_dir": str(self.data / "forgery"), "file_pattern": "events.jsonl",
                  "time_mapping": {"origin": capture.meta["base_time_iso"]}}
        source = FileObservationSource(config)
        self.assertEqual(source.read_available(0), [])
        events = source.read_available(1000000)
        self.assertEqual(sum(e["data"]["packet_count"] for e in events), 4)
        self.assertIsNone(events[0]["data"]["appid"])
        self.assertEqual(events[0]["data"]["protocol_fields"]["appids"], [12289, 12291])
        for event in events:
            self.assertEqual(validate_observation_event(event), [])
            self.assertEqual(event["metadata"]["interface_id"], "span-process")
            self.assertEqual(event["data"]["event_type"], "stats")
            self.assertEqual(event["status"], "unknown")

    def test_reference_checks_use_only_headers_and_baseline(self):
        normal = GooseCapture(self.data / "normal")
        baseline = normal_baseline(normal.records, normal.index)
        self.assertFalse(any(reference_checks(normal.records, normal.index, baseline)[0]))
        forged = GooseCapture(self.data / "forgery")
        checks, _ = reference_checks(forged.records, forged.index, baseline)
        self.assertEqual(checks[1], ["来源 MAC 偏离正常样本"])
        self.assertEqual(checks[2], ["控制块状态号回退"])

    def test_modified_or_truncated_files_are_rejected(self):
        pcap = (self.data / "normal/capture.pcap").read_bytes()
        with self.assertRaises(ValueError): read_pcap(pcap[:-1])
        with self.assertRaises(ValueError): read_pcap(pcap + b"x")
        (self.data / "normal/capture.pcap").write_bytes(pcap + b"x")
        with self.assertRaises(ValueError): GooseCapture(self.data / "normal")

    def test_bad_checksum_aborts_before_writing_data(self):
        broken = self.root / "broken.zip"
        sample_package(broken, corrupt=True)
        with self.assertRaises(ValueError): import_package(broken, self.root / "broken-output")
        self.assertFalse((self.root / "broken-output").exists())

    def test_existing_data_is_not_overwritten(self):
        with self.assertRaises(ValueError): import_package(self.archive, self.data)

    def test_application_and_finished_record_replay(self):
        app = Application(output_root=str(self.root / "runs"))
        try:
            app.load(str(self.scene()))
            self.assertEqual(app.get_view("goose")["visible_frames"], 0)
            app.duration_us = 2_000_000
            app.run_to_end()
            view = app.get_view("system")
            self.assertEqual(view["packet_capture"]["visible_frames"], 4)
            self.assertEqual(view["recognition"], [])
            self.assertEqual(view["observations"]["late"], [])
            self.assertFalse(any(item["effects"] for item in view["management"].values()))
            replay = build_goose_view(self.root / "runs" / app.run_id)
            self.assertEqual(replay["visible_frames"], 4)
        finally:
            app.close()

    def lab(self, sample="forgery", policy="validated", origin=None):
        settings = {"sample": sample, "policy": policy, "capacity": "normal", "service_fps": 5000,
                    "queue_capacity": 64, "fault_start_us": None, "fault_current_a": 900,
                    "trip_deadline_us": 400000, "manual_dispatch": True}
        if origin is not None:
            settings["attack_origin"] = origin
        return GooseLab(GooseCapture(self.data / sample), settings)

    def test_protection_criteria_are_an_experiment_input(self):
        # 样本决定默认工况：投入故障的样本默认启用保护判据，未投入故障的样本默认停用。
        from sim2030.goose_lab import configure_lab
        from sim2030.scenario import load_scenario
        defaults = {}
        for sample in ("normal", "dos", "forgery"):
            config = load_scenario(str(ROOT / "scenarios/substation-goose-lab.json"))
            configure_lab(config, {"sample": sample})
            prot = next(d for d in config.devices if d.device_id == "prot")
            defaults[sample] = (config.layout["goose_lab"]["protection_enabled"],
                                prot.parameters["external_trip_replay"])
        self.assertEqual(defaults["normal"], (True, False))
        self.assertEqual(defaults["dos"], (True, False))
        self.assertEqual(defaults["forgery"], (False, True))

        config = load_scenario(str(ROOT / "scenarios/substation-goose-lab.json"))
        configure_lab(config, {"sample": "forgery", "protection": True})
        prot = next(d for d in config.devices if d.device_id == "prot")
        self.assertFalse(prot.parameters["external_trip_replay"])
        with self.assertRaises(ValueError):
            configure_lab(config, {"protection": "yes"})

    def test_scene_is_synced_from_baseline(self):
        """GOOSE 实验场景除攻击节点外与基准设备/链路一致，并保留实验所需的初始合闸。"""
        from sim2030.scenario import load_scenario
        scene = ROOT / "scenarios/substation-goose-lab.json"
        baseline = load_scenario(str(ROOT / "scenarios/substation-live.json"))
        config = load_scenario(str(scene))
        self.assertEqual(
            {device.device_id for device in config.devices} - {device.device_id for device in baseline.devices},
            {"net_attack_node"},
        )
        self.assertEqual({link.link_id for link in config.links}, {link.link_id for link in baseline.links})
        self.assertEqual(config.environment, baseline.environment)
        self.assertEqual(config.layout["layers"], baseline.layout["layers"])
        self.assertEqual(config.layout["deployment"], baseline.layout["deployment"])
        self.assertEqual(config.operations, [])
        breaker = next(device for device in config.devices if device.device_id == "brk")
        self.assertEqual(breaker.parameters["initial_position"], "closed")
        self.assertEqual(breaker.initial_state, {"position": "closed"})
        node = next(device for device in config.devices if device.device_id == "net_attack_node")
        self.assertEqual(node.layout, {"x": 1295, "y": 580})
        self.assertIn("packet_replay", config.layout)
        self.assertIn("goose_lab", config.layout)

    def test_attack_node_owns_the_injection_actions(self):
        # 攻击能力挂在场景声明的红色攻击节点上：交换机不再是攻击下发目标，也没有攻击控制项。
        scene = ROOT / "scenarios/substation-goose-lab.json"
        config = load_scenario(str(scene))
        self.assertEqual(validate_scenario(config), [])
        origin = config.layout["goose_lab"]["attack_origin"]
        node = next(d for d in config.devices if d.device_id == origin["asset_id"])
        self.assertEqual((node.device_type, node.name), ("network_attack_source", "网络攻击节点"))
        self.assertEqual(node.parameters["access_switch_id"], origin["switch_id"])
        self.assertEqual(create_device(node).describe_controls()[2]["params"][0]["input"]["max"], 5000)
        # 交换机是部署资产而不是设备：攻击节点必须另有实体设备承载下发命令。
        self.assertNotEqual(origin["asset_id"], origin["switch_id"])
        self.assertNotIn("sw-process", [d.device_id for d in config.devices])

        app = Application(output_root=str(self.root / "runs"))
        try:
            app.load(str(scene))
            app.start()
            app.step()
            self.assertEqual(app.get_device_controls("sw-process")["status"], "not_found")
            rejected = app.submit_operation({"request_id": "old-1", "target_asset_id": "sw-process",
                                             "action_type": "send_attack_frame"})
            self.assertEqual(rejected["status"], "rejected")
            receipt = app.submit_operation({"request_id": "atk-1", "target_asset_id": origin["asset_id"],
                                            "action_type": "send_attack_burst", "parameters": {"count": 3}})
            self.assertEqual((receipt["status"], receipt["count"]), ("accepted", 3))
            app.step()
            senders = {message["sender_id"] for message in app.get_view("system")["in_flight_messages"]}
            self.assertIn(origin["asset_id"], senders)
            self.assertNotIn("sw-process", senders)
        finally:
            app.close()

    def test_protection_trip_uses_a_channel_of_its_own(self):
        # 保护判据跳闸不应进入智能终端共享接收队列：洪泛塞满队列时它仍要能下发。
        lab = self.lab()
        lab.queue.extend((index, index) for index in range(lab.settings["queue_capacity"]))

        class Terminal:
            up_port = "up"

            def __init__(self):
                self.received = []

            def receive(self, message):
                self.received.append(message)

        class Engine:
            def __init__(self, terminal):
                self._terminal = terminal

            def get_device(self, device_id):
                return self._terminal

        terminal = Terminal()
        trip = Message(message_id="prot-1", sender_id="prot", receiver_id="ctl_brk",
                       business_type=BusinessType.COMMAND, source_port="out", target_port="up",
                       created_time_us=1000000,
                       payload={"request_id": "prot-1", "action_type": "trip", "parameters": {},
                                "protection_trip": True, "sample_value": 900.0})
        self.assertTrue(lab.receive_control(trip, 1000000, Engine(terminal)))
        self.assertEqual([message.message_id for message in terminal.received], ["prot-1"])
        self.assertEqual(lab.protection_trips, [{"time_us": 1000000, "request_id": "prot-1", "sample": 900.0}])
        self.assertEqual(lab.trip_source, "protection")
        self.assertEqual(lab.counts.get("dropped"), None)

    def test_accepted_baseline_frames_stay_off_the_topology(self):
        lab = self.lab()
        lab.advance(400000, None)
        self.assertEqual(lab.decisions[1]["status"], "accepted")
        self.assertEqual(lab.in_flight(), [])

    def test_manual_non_accepted_frame_is_published_then_expires(self):
        lab = self.lab()
        lab.advance(0, None)
        self.assertEqual(lab.dispatch("atk-1", 0, 100000)["status"], "accepted")
        lab.advance(100000, None)
        published = [message for message in lab.in_flight() if message["business_type"] == "goose_rejected"]
        self.assertEqual([message["message_id"] for message in published], ["atk-1:1"])
        # 未声明 attack_origin 时沿用过程网交换机，保持旧记录与旧场景的可比性。
        self.assertEqual(published[0]["sender_id"], "sw-process")
        self.assertNotEqual(published[0]["payload"]["receiver_status"], "accepted")
        lab.advance(100000 + VISIBLE_RETENTION_US + 1, None)
        self.assertEqual([message for message in lab.in_flight()
                          if message["business_type"] == "goose_rejected"], [])

    def test_attack_origin_decides_the_frame_source(self):
        # 攻击报文来源由场景 attack_origin 声明：报文 sender_id 与接收记录来源都跟随它。
        lab = self.lab(origin={"asset_id": "net_attack_node", "name": "网络攻击节点"})
        self.assertEqual((lab.attack_origin_id, lab.attack_origin_name), ("net_attack_node", "网络攻击节点"))
        lab.advance(0, None)
        self.assertEqual(lab.dispatch("atk-1", 0, 100000)["status"], "accepted")
        lab.advance(100000, None)
        published = [message for message in lab.in_flight() if message["business_type"] == "goose_rejected"]
        self.assertEqual(published[0]["sender_id"], "net_attack_node")
        lab.advance(200000, None)
        row = lab.receiver_row(len(lab.capture.records) + 1)
        self.assertEqual(row["source"], "网络攻击节点")
        self.assertIn("网络攻击节点", [event["text"] for event in lab.events if event["kind"] == "manual_dispatch"][0])

    def test_manual_frame_is_numbered_after_the_baseline(self):
        lab = self.lab()
        self.assertEqual(lab.dispatch("atk-1", 0, 100000)["status"], "accepted")
        self.assertEqual(list(lab.manual_frames), [len(lab.capture.records) + 1])
        lab.advance(200000, None)
        row = lab.receiver_row(len(lab.capture.records) + 1)
        self.assertEqual(row["label"], "下发 #1")
        self.assertEqual(row["source"], "交换机")

    def test_rejected_baseline_frames_are_published(self):
        # 抑制帧投毒把接收端状态号推到 50 之后，随后回退的低 stNum 基线帧都会被拒。
        lab = self.lab()
        lab.last_st, lab.last_sq, lab.last_trip = 50, 0, False
        lab.advance(500000, None)
        rejected = [message for message in lab.in_flight() if message["business_type"] == "goose_rejected"]
        self.assertEqual([message["message_id"] for message in rejected],
                         ["goose-baseline-1", "goose-baseline-3", "goose-baseline-4"])
        self.assertEqual(rejected[0]["payload"]["receiver_status"], "rejected")
        self.assertEqual(rejected[0]["sender_id"], "prot")
        self.assertEqual(rejected[0]["payload"]["stNum"], 1)
        lab.advance(2000000, None)
        self.assertEqual([message for message in lab.in_flight()
                          if message["business_type"] == "goose_rejected"], [])

    def test_failed_sample_load_preserves_existing_run(self):
        app = Application(output_root=str(self.root / "runs"))
        try:
            path = self.scene()
            previous = app.load(str(path))
            config = json.loads(path.read_text())
            config["observations"]["provider_dir"] = str(self.root / "not-imported")
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "尚未导入"): app.load(str(path))
            self.assertEqual(app.run_id, previous)
            self.assertIsNotNone(app.recorder)
        finally:
            app.close()

    def test_http_frame_query_does_not_accept_paths_or_future_frames(self):
        self.scene()
        server = PresentationServer(str(self.root), str(self.root / "runs"))
        try:
            _, result, _ = server.handle_request("POST", "/api/runs", b'{"scenario_id":"test-goose"}')
            run = result["run_id"]
            code, _, _ = server.handle_request("GET", f"/api/runs/{run}/views?view=goose&frame_no=../../secret")
            self.assertEqual(code, 400)
            _, body, _ = server.handle_request("GET", f"/api/runs/{run}/views?view=goose&frame_no=1")
            self.assertIn("error", body["data"])
            server.application.run_to_end()
            _, body, _ = server.handle_request("GET", f"/api/runs/{run}/views?view=goose&frame_no=2")
            self.assertEqual(body["data"]["frame"]["frame_no"], 2)
            server.application.load(str(self.scene("normal")))
            _, archived, _ = server.handle_request("GET", f"/api/runs/{run}/views?view=goose&frame_no=2")
            self.assertTrue(archived["data"]["frame"]["trip"])
            code, _, _ = server.handle_request("GET", f"/api/runs/{run}/views?view=goose&frame_no=invalid")
            self.assertEqual(code, 400)
        finally:
            server.close()


if __name__ == "__main__":
    unittest.main()
