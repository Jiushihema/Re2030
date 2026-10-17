"""授时攻击的端到端因果链和保护边界，不依赖外部报文包。"""
import math
from pathlib import Path
import tempfile
import unittest

from sim2030.application import Application
from sim2030.contracts import Message
from sim2030.presentation.views import build_system_view
from sim2030.scenario import create_device, load_scenario
from sim2030.timing import TimingProtection

SCENE = str(Path(__file__).resolve().parents[1] / "scenarios/substation-time-attack.json")


class TestTimingDifferential(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Application(output_root=self.tmp.name)
        self.addCleanup(self.app.close)
        self.app.load(SCENE)
        self.app.start()
        self.advance(15)
        self.protection = self.app.engine.get_device("diff_prot")

    def advance(self, count=10):
        for _ in range(count):
            self.app.step()

    def attack(self):
        receipt = self.app.submit_operation({"target_asset_id": "time_attack_node", "action_type": "start_attack"})
        self.assertEqual(receipt["status"], "accepted")
        self.advance()

    def set_param(self, key, value):
        result = self.app.set_device_parameter("diff_prot", key, value)
        self.assertEqual(result["status"], "updated", result)

    def test_normal_currents_cancel_and_numbering_wraps(self):
        self.advance(10)
        report = self.protection.report
        self.assertAlmostEqual(report["id_a"], 0, places=9)
        self.assertGreater(report["ir_a"], 200)
        self.assertEqual(self.protection.trip_count, 0)
        self.assertEqual(self.app.engine.get_device("brk").state["position"], "closed")
        self.assertGreater(report["pair"]["epoch"], 0)
        self.assertLess(report["pair"]["smpCnt"], 4000)
        for row in self.protection.wave:
            self.assertAlmostEqual(row["i1"], -row["i2"], places=9)
            self.assertEqual(row["true_1_us"], row["true_2_us"])

    def test_delay_causes_phasor_difference_and_real_trip_chain(self):
        self.attack()
        trip = self.protection.last_trip
        self.assertIsNotNone(trip)
        expected = trip["ir_a"] * math.sin(math.pi * 50 * .002)
        self.assertAlmostEqual(trip["id_a"], expected, places=6)
        self.assertEqual(trip["pair"]["skew_us"], 2000)
        self.assertTrue(all(g["passed"] for g in trip["gates"] if g["implemented"]))
        self.assertTrue(self.app.engine.get_device("brk").state["tripped"])
        self.assertEqual(self.protection.trip_count, 1)
        events = list(self.protection.events)
        trip_at = next(e["time_us"] for e in events if e["text"] == "差动保护出口跳闸")
        terminal_at = next(e["time_us"] for e in events if "智能终端接收" in e["text"])
        open_at = next(e["time_us"] for e in events if e["text"] == "断路器位置：open" and e["time_us"] > trip_at)
        self.assertLess(trip_at, terminal_at)
        self.assertLess(terminal_at, open_at)
        self.assertEqual(self.app.engine.get_device("time_svc").state["timing_offset_us"], 0)
        self.assertEqual(self.app.engine.get_device("mu_2").state["clock_offset_us"], -2000)

    def test_recovery_does_not_reclose_and_evidence_survives(self):
        self.attack()
        self.app.submit_operation({"target_asset_id": "time_attack_node", "action_type": "stop_attack"})
        self.advance(12)
        self.assertEqual(self.app.engine.get_device("mu_2").state["clock_offset_us"], 0)
        self.assertEqual(self.app.engine.get_device("brk").state["position"], "open")
        self.assertTrue(self.protection.baseline)
        self.assertTrue(self.protection.trip_wave)
        self.assertEqual(self.protection.trip_wave[-1], self.protection.last_trip["pair"])
        self.assertAlmostEqual(self.protection.report["id_a"], 0, places=9)
        self.app.stop()
        replay = build_system_view(Path(self.tmp.name) / self.app.run_id)["timing_lab"]
        self.assertEqual(replay["last_trip"]["pair"]["skew_us"], 2000)
        self.assertTrue(replay["trip_waveform"])
        self.assertTrue(replay["events"])

    def test_minimum_gate_blocks_until_disabled(self):
        self.set_param("minimum_a", 1000)
        self.attack()
        self.assertEqual(self.protection.trip_count, 0)
        self.set_param("minimum_enabled", False)
        self.advance()
        self.assertEqual(self.protection.trip_count, 1)

    def test_manual_reclose_after_recovery_is_stable(self):
        self.attack()
        self.app.submit_operation({"target_asset_id": "time_attack_node", "action_type": "stop_attack"})
        self.advance()
        self.app.submit_operation({"target_asset_id": "brk", "action_type": "close"})
        self.advance(15)
        self.assertEqual(self.app.engine.get_device("brk").state["position"], "closed")
        self.assertEqual(self.protection.trip_count, 1)
        self.assertAlmostEqual(self.protection.report["id_a"], 0, places=9)

    def test_restraint_gate_blocks_until_disabled(self):
        self.set_param("slope", .8)
        self.attack()
        self.assertEqual(self.protection.trip_count, 0)
        self.set_param("restraint_enabled", False)
        self.advance()
        self.assertEqual(self.protection.trip_count, 1)

    def test_all_gates_disabled_small_nonzero_difference_operates(self):
        self.set_param("minimum_enabled", False)
        self.set_param("restraint_enabled", False)
        self.app.set_device_parameter("time_attack_node", "delay_us", 250)
        self.assertEqual(self.protection.trip_count, 0)
        self.attack()
        self.assertLess(self.protection.last_trip["id_a"], 10)
        self.assertEqual(self.protection.trip_count, 1)

    def test_reserved_switches_are_explicit_and_do_not_block(self):
        for key in ("direction_enabled", "ct_enabled"):
            self.set_param(key, True)
        self.attack()
        self.assertEqual(self.protection.trip_count, 1)
        reserved = [g for g in self.protection.last_trip["gates"] if not g["implemented"]]
        self.assertTrue(all(g["enabled"] and g["passed"] is None for g in reserved))

    def test_zero_delay_and_zero_current_do_not_trip(self):
        self.app.set_device_parameter("time_attack_node", "delay_us", 0)
        self.set_param("minimum_enabled", False)
        self.set_param("restraint_enabled", False)
        self.attack()
        self.assertEqual(self.protection.trip_count, 0)
        self.app.submit_operation({"target_asset_id": "time_attack_node", "action_type": "stop_attack"})
        self.app.submit_operation({"target_asset_id": "brk", "action_type": "open"})
        self.advance()
        self.app.set_device_parameter("time_attack_node", "delay_us", 2000)
        self.attack()
        self.assertEqual(self.protection.trip_count, 0)

    def test_invalid_parameters_rejected_without_mutating(self):
        for key, value in (("slope", float('nan')), ("minimum_a", -1), ("ct_enabled", 1)):
            result = self.app.set_device_parameter("diff_prot", key, value)
            self.assertEqual(result["status"], "rejected")
        for delay in (123, -250, float('inf'), True):
            self.assertEqual(self.app.set_device_parameter("time_attack_node", "delay_us", delay)["status"], "rejected")
        self.attack()
        self.assertEqual(self.app.set_device_parameter("time_attack_node", "delay_us", 4000)["status"], "rejected")

    def test_legacy_scene_keeps_existing_device_models(self):
        config = load_scenario(str(Path(SCENE).with_name("substation-live.json")))
        for spec in config.devices:
            self.assertNotIsInstance(create_device(spec), TimingProtection)


class TestTimingPairing(unittest.TestCase):
    def make_protection(self):
        spec = next(d for d in load_scenario(SCENE).devices if d.device_id == "diff_prot")
        return create_device(spec)

    def feed(self, protection, source, keys, evidence_shift=0):
        rows = [{"epoch": key // 4000, "smpCnt": key % 4000, "local_time_us": key * 250,
                 "true_time_us": key * 250 + evidence_shift,
                 "value": (1 if source == "mu" else -1) * math.sqrt(2) * 100 * math.sin(2 * math.pi * key / 80)}
                for key in keys]
        protection.receive(Message(message_id=source, sender_id=source, receiver_id="diff_prot", business_type="sampling",
                                   payload={"acquisition_id": source, "timing_samples": rows}))

    def test_missing_or_discontinuous_data_cannot_supply_complete_window(self):
        p = self.make_protection()
        self.feed(p, "mu", range(80))
        self.assertEqual(p.evaluate_differential(), [])
        self.assertEqual(p.report, {})
        self.feed(p, "mu_2", list(range(39)) + list(range(40,80)))
        self.assertEqual(p.evaluate_differential(), [])
        self.assertEqual(p.report, {})

    def test_evidence_timestamps_do_not_influence_protection(self):
        p = self.make_protection()
        self.feed(p, "mu", range(80))
        self.feed(p, "mu_2", range(80), evidence_shift=999999)
        p.evaluate_differential()
        self.assertAlmostEqual(p.report["id_a"], 0, places=9)
        self.assertEqual(p.trip_count, 0)
        self.assertEqual(p.report["pair"]["skew_us"], 999999)

    def test_second_rollover_does_not_mix_equal_counts(self):
        p = self.make_protection()
        self.feed(p, "mu", range(80))
        self.feed(p, "mu_2", range(4000,4080))
        p.evaluate_differential()
        self.assertEqual(p.report, {})
        self.feed(p, "mu", range(4000,4080))
        p.evaluate_differential()
        self.assertEqual(p.report["pair"]["epoch"], 1)
        self.assertEqual(p.trip_count, 0)
