"""通过既有设备注册、业务消息和记录接口接入；不修改公共模块。"""
from __future__ import annotations

import uuid
from pathlib import Path

from sim2030.application import Application
from sim2030.base.device import Device
from sim2030.base.process import AuxiliaryEquipment, PrimaryEquipment, SensorDevice, AcquisitionDevice, TransformerEquipment
from sim2030.base.bay import MeasurementControlDevice
from sim2030.base.station import StationControlSystem
from sim2030.base.engine import SimulationEngine
from sim2030.contracts import Message
from sim2030.constants import BusinessType
from sim2030.scenario import register_device

from acoustic_demo.model import DemoModel

SCENARIO = Path(__file__).with_name("scenario.json")


class AcousticEngine(SimulationEngine):
    @staticmethod
    def _default_entry_id(device_specs):
        for spec in device_specs:
            if spec.device_type == "acoustic_station":
                return spec.device_id
        return SimulationEngine._default_entry_id(device_specs)


class AcousticStation(StationControlSystem):
    """站控节点拥有判据、锁定状态与停机调度；step 返回实际业务命令。"""

    def __init__(self, spec):
        super().__init__(spec)
        self.rule = dict(self.parameters["acoustic_protection"])
        self._condition_reports = []
        self._events = []
        self.last_measurement = None
        self.last_window_s = 0
        self.consecutive = 0
        self.command_due_us = None
        self.trip_request_id = None
        self.state.update(trip_latched=False, consecutive_windows=0, last_window_s=0,
                          command_due_s=None, rule_owner=self.asset_id)

    def receive(self, message):
        super().receive(message)
        if message.sender_id == "mc" and message.business_type == BusinessType.STATUS:
            self._condition_reports.append(message)

    def _record(self, kind, time_us, **details):
        self._events.append({"kind": kind, "observed_time_s": time_us / 1e6,
                             "owner_device_id": self.asset_id, **details})

    def drain_events(self):
        events, self._events = self._events, []
        return events

    def _evaluate_received(self, message, time_us):
        sample = (message.payload or {}).get("fan_sample", {})
        vibration = sample.get("vibration", {})
        end = vibration.get("window_end_s", 0)
        if end <= self.last_window_s:
            return
        if end > time_us / 1e6 or time_us / 1e6 - end > self.rule.get("max_sample_age_s", 2):
            self.consecutive = 0
            self.state["consecutive_windows"] = 0
            self._record("stale_measurement_ignored", time_us, source_time_s=end)
            return
        if end != self.last_window_s + 1:
            self.consecutive = 0
        self.last_window_s = end
        exceeded = vibration["max_ratio"] > self.rule["threshold_ratio"]
        self.consecutive = self.consecutive + 1 if exceeded else 0
        self.last_measurement = {"received_time_us": time_us, **vibration}
        self.state.update(consecutive_windows=self.consecutive, last_window_s=end,
                          rms_ratio_xyz=list(vibration["rms_ratio_xyz"]))
        self._record("measurement_received", time_us, src=message.sender_id, dst=self.asset_id,
            source_time_s=end, rms_ratio_xyz=vibration["rms_ratio_xyz"],
            consecutive_windows=self.consecutive,
            fan_current_a=sample.get("samples", {}).get("fan_current"),
            fan_speed_rpm=sample.get("samples", {}).get("fan_speed"))
        if self.consecutive >= self.rule["consecutive_windows"] and not self.state["trip_latched"]:
            self.state["trip_latched"] = self.model.trip_latched = True
            self.command_due_us = time_us + round(self.rule["delay_s"] * 1e6)
            self.trip_request_id = "acoustic-trip-" + uuid.uuid4().hex[:12]
            self.state.update(command_due_s=self.command_due_us / 1e6,
                              trip_request_id=self.trip_request_id)
            self._record("threshold_trip", time_us, source_time_s=end,
                request_id=self.trip_request_id, command_due_s=self.command_due_us / 1e6)

    def reset_protection(self, time_us):
        measurement = self.last_measurement
        if (not measurement or measurement["max_ratio"] > self.rule["threshold_ratio"]
                or time_us - measurement["received_time_us"] > self.rule.get("max_sample_age_s", 2) * 1e6):
            return {"status": "rejected", "reason": "等待站控收到新的正常振动窗口后再复位"}
        self.state["trip_latched"] = self.model.trip_latched = False
        self.command_due_us = None
        self.consecutive = 0
        self.state.update(command_due_s=None, consecutive_windows=0)
        return {"status": "accepted"}

    def step(self, time_us, dt_us, inputs):
        reports, self._condition_reports = self._condition_reports, []
        for message in reports:
            self._evaluate_received(message, time_us)
        if self.command_due_us is not None and time_us >= self.command_due_us:
            # 节点自己形成操作意图，复用本站控设备的分派/端口检查与出站队列。
            intent = Message(message_id=str(uuid.uuid4()), sender_id=self.asset_id,
                receiver_id=self.asset_id, business_type=BusinessType.COMMAND,
                created_time_us=time_us, related_request=self.trip_request_id,
                payload={"request_id": self.trip_request_id, "target_asset_id": "cool",
                         "action_type": "stop", "parameters": {"reason": "simulated_vibration_threshold"}})
            self.submit_operation(intent)
            self._record("stop_command_issued", time_us, src=self.asset_id, dst="mc",
                         request_id=self.trip_request_id, emitted_from="AcousticStation.step")
            self.command_due_us = None
            self.state["command_due_s"] = None
        return super().step(time_us, dt_us, inputs)


class AcousticFan(AuxiliaryEquipment):
    def physical_outputs(self):
        if not hasattr(self, "model"):
            return super().physical_outputs()
        return {"running": self.model.powered, "speed_ratio": self.model.speed_ratio,
                "motor_current_a": self.model.current_a,
                "rotation_speed_rpm": 1500 * self.model.speed_ratio}

    def check_interlock(self, action_type, parameters, inputs):
        if action_type == "start" and self.model.trip_latched:
            return "振动保护已锁定，请先关闭干扰，待读数恢复后复位"
        return super().check_interlock(action_type, parameters, inputs)

    def _apply_action(self, action_type, parameters, time_us=0):
        super()._apply_action(action_type, parameters, time_us)
        if action_type in ("start", "stop"):
            self.model.set_power(action_type == "start", time_us / 1e6)

    def step(self, time_us, dt_us, inputs):
        self.model.advance(time_us / 1e6)
        messages = super().step(time_us, dt_us, inputs)
        self.state.update(self.physical_outputs())
        return messages


class AcousticTransformer(TransformerEquipment):
    def step(self, time_us, dt_us, inputs):
        # 保留分接动作能力，替换本场景中的通用热模型，避免重复计算温度。
        messages = PrimaryEquipment.step(self, time_us, dt_us, inputs)
        self.model.advance(time_us / 1e6)
        self.state["oil_temp_c"] = self.model.oil_c
        return messages

    def physical_outputs(self):
        result = super().physical_outputs()
        if hasattr(self, "model"):
            result.update(oil_temp_c=self.model.oil_c, oil_sensor_c=self.model.oil_sensor_c)
        return result


class AcousticSensor(SensorDevice):
    def step(self, time_us, dt_us, inputs):
        messages = self._drain_outbox()
        for feature in self.model.sample_to(time_us / 1e6):
            self.state.update(last_value=feature["max_ratio"], last_sample_time_us=time_us, **feature)
            messages.extend(self._new_message(self.up_port, BusinessType.SAMPLING,
                {"sensor_id": self.asset_id, "value": feature["max_ratio"],
                 "unit": "normal RMS ratio", "sample_time_us": time_us,
                 "vibration": feature}, time_us))
        return messages


class AcousticAcquisition(AcquisitionDevice):
    def build_measurement(self, time_us):
        payload = super().build_measurement(time_us)
        payload["vibration"] = dict(self._latest.get("fan_vibration", {}).get("vibration", {}))
        payload["source_samples"] = {key: dict(value) for key, value in self._latest.items()}
        return payload


class AcousticMeasurementControl(MeasurementControlDevice):
    def __init__(self, spec):
        super().__init__(spec)
        self.fan_sample = {}
        self.oil_sample = {}

    def receive(self, message):
        if message.business_type == BusinessType.STATUS and message.sender_id == "fan_condition_monitor":
            self.fan_sample = dict(message.payload or {})
            return  # 状态监测单独转报，不混入 main 的两套 SV 业务量测来源。
        if message.business_type == BusinessType.SAMPLING and message.sender_id == "mu":
            self.oil_sample = dict((message.payload or {}).get("source_samples", {}).get("oil_temp", {}))
        super().receive(message)

    def build_telemetry(self):
        return {**super().build_telemetry(), "fan_sample": self.fan_sample,
                "oil_sample": self.oil_sample}


class AcousticSource(Device):
    def describe_controls(self):
        return [{"kind": "command", "action": action, "label": label, "params": []}
                for action, label in (("start_weak", "原样本幅度 κ=0.30"),
                                      ("start_strong", "增强仿真 κ=0.90"),
                                      ("stop_attack", "关闭虚拟声波"))]


class AcousticApplication(Application):
    def __init__(self, output_root="runs"):
        for name, factory in {
            "acoustic_fan": AcousticFan, "acoustic_transformer": AcousticTransformer,
            "acoustic_sensor": AcousticSensor, "acoustic_acquisition": AcousticAcquisition,
            "acoustic_measurement_control": AcousticMeasurementControl,
            "acoustic_source": AcousticSource,
            "acoustic_station": AcousticStation,
        }.items():
            register_device(name, factory)
        super().__init__(output_root)
        self.engine = AcousticEngine()
        self.acoustic = None

    def load(self, config_path, goose_options=None):
        self.acoustic = None
        run_id = super().load(config_path, goose_options)
        if self.config.scenario_id != "substation-acoustic-demo":
            return run_id
        self.acoustic = DemoModel(self.engine.get_device("tap").parameters.get("acoustic_thermal"))
        for device in self.engine._devices.values():
            if device.spec.device_type.startswith("acoustic_"):
                device.model = self.acoustic
        self.rule = dict(self.engine.get_device("station").parameters["acoustic_protection"])
        self.events, self.history = [], []
        self.attack_preview = None
        self.last_oil_sample_us = -1
        self.last_measurement = {}
        self.engine.message_handler = self._observe_delivery
        self.recorder.append("acoustic_meta", {"time_us": 0, "provenance": self.acoustic.provenance,
            "rule": self.rule, "data_origin": "SIMULATION_NOT_HARDWARE", "model_parameters": {
                "nominal_current_a": 2, "nominal_speed_rpm": 1500,
                "current_lag_s": .25, "speed_coast_s": 5, "oil_sensor_lag_s": 5,
                "P_loss_w": 30000, "C_reference_j_per_k": 3600000, "G0_w_per_k": 500,
                "Gf_w_per_k": 500, "ambient_c": 25, "initial_oil_c": 55,
                "thermal_mode": self.acoustic.thermal_mode,
                "thermal_time_scale": self.acoustic.thermal_time_scale,
                "effective_heat_capacity_j_per_k": self.acoustic.effective_capacity_j_per_k,
                "natural_time_constant_s": self.acoustic.natural_tau_s,
                "original_natural_time_constant_s": self.acoustic.reference_natural_tau_s,
                "template": self.config.layout.get("acoustic_template", {}),
                "rule_owner_device_id": self.engine.get_device("station").asset_id,
                "all_parameters_validated": False}})
        return run_id

    def _event(self, kind, time_s, **details):
        record = {"kind": kind, "time_us": round(time_s * 1e6),
                  "observed_time_s": time_s, "data_origin": "APP_SIM_NOT_PCAP", **details}
        if kind == "measurement_received":
            self.last_measurement = record
        else:
            self.events.append(record)
        self.events = self.events[-100:]
        if self.recorder:
            self.recorder.append("acoustic_path", record)

    def _observe_delivery(self, message, time_us):
        payload, now = message.payload or {}, time_us / 1e6
        if message.business_type in (BusinessType.COMMAND, BusinessType.FEEDBACK):
            if message.sender_id in {"station", "mc", "ctl_cool", "cool"} and message.receiver_id in {"station", "mc", "ctl_cool", "cool"}:
                self._event("message_delivered", now, src=message.sender_id, dst=message.receiver_id,
                    business_type=message.business_type, message_id=message.message_id,
                    request_id=message.related_request, source_time_s=message.created_time_us / 1e6,
                    scheduled_delivery_s=message.deliver_time_us / 1e6, payload=payload)
        if message.receiver_id == "station" and message.business_type == BusinessType.STATUS:
            oil = payload.get("oil_sample", {})
            source_us = oil.get("sample_time_us", -1)
            if source_us > self.last_oil_sample_us:
                self.last_oil_sample_us = source_us
                if self.recorder:
                    self.recorder.append("acoustic_path", {"kind": "oil_measurement_received",
                        "time_us": time_us, "observed_time_s": now,
                        "src": "mc", "dst": "station", "path": ["oil_temp", "mu", "mc", "station"],
                        "source_time_s": source_us / 1e6, "value": oil.get("value"), "unit": "C",
                        "data_origin": "APP_SIM_NOT_PCAP"})
        return False  # 只旁观投递，继续交给真实的仿真设备处理。

    def _submit_operation_locked(self, request):
        if self.acoustic is not None and request.get("target_asset_id") == "sensor_attack_node":
            if self.status not in {"ready", "paused", "running"}:
                return {"status": "rejected", "reason": "运行已结束，请新建运行"}
            action = request.get("action_type")
            if action not in {"start_weak", "start_strong", "start_attack", "stop_attack", "reset_protection"}:
                return {"status": "rejected", "reason": "不支持的声波操作"}
            now = self.current_time_us / 1e6
            if action == "reset_protection":
                if self.acoustic.response.gain:
                    return {"status": "rejected", "reason": "请先关闭干扰"}
                receipt = self.engine.get_device("station").reset_protection(self.current_time_us)
                if receipt["status"] != "accepted":
                    return receipt
            else:
                gain = {"start_weak": .30, "start_strong": .90, "start_attack": .30, "stop_attack": 0}[action]
                try:
                    self.acoustic.response.switch(gain, now)
                except ValueError as exc:
                    return {"status": "rejected", "reason": str(exc)}
                self.engine.get_device("sensor_attack_node").state.update(emitting=bool(gain), normalized_gain=gain)
            self._event(action, now, request_id=request.get("request_id", ""))
            return {"status": "accepted", "action_type": action, "time_us": self.current_time_us}
        return super()._submit_operation_locked(request)

    def _record_step(self, result, *args):
        super()._record_step(result, *args)
        if self.acoustic is not None:
            for event in self.engine.get_device("station").drain_events():
                kind, time_s = event.pop("kind"), event.pop("observed_time_s")
                self._event(kind, time_s, **event)
        if self.acoustic is not None and result.time_us % 1_000_000 == 0:
            if (self.acoustic.powered and self.acoustic.response.gain and self.acoustic.last_wave
                    and any(abs(row[5]) > 1e-8 for row in self.acoustic.last_wave)):
                self.attack_preview = {"time_s": result.time_us / 1e6,
                    "gain": self.acoustic.response.gain,
                    "wave": list(self.acoustic.last_wave),
                    "source_wave": self._source_wave(self.acoustic.time_s)}
            row = {"time_s": result.time_us / 1e6, "powered": self.acoustic.powered,
                   "current_a": self.acoustic.current_a, "speed_rpm": 1500 * self.acoustic.speed_ratio,
                   "oil_c": self.acoustic.oil_sensor_c, "oil_reference_c": self.acoustic.oil_c,
                   "oil_original_c": self.acoustic.oil_original_sensor_c,
                   "thermal_mode": self.acoustic.thermal_mode,
                   "thermal_time_scale": self.acoustic.thermal_time_scale,
                   "rms_ratio_xyz": (self.acoustic.feature or {}).get("rms_ratio_xyz", [None] * 3)}
            self.history.append(row)
            self.history = self.history[-1801:]
            self.recorder.append("acoustic_series", {"time_us": result.time_us, **row})

    def _source_wave(self, time_s):
        return [[t, self.acoustic.response.input_at(t)] for t in
                (max(0, time_s - .004) + i / 88000 for i in range(352)) if t <= time_s]

    def _get_view_locked(self, view_name, query=None):
        if view_name == "acoustic":
            if self.acoustic is None:
                return {"available": False}
            oil_state = self.engine.get_device("oil_temp").state
            oil_sample_s = oil_state.get("last_sample_time_us", 0) / 1e6
            original_at_sample = next((row["oil_original_c"] for row in reversed(self.history)
                                       if row["time_s"] <= oil_sample_s + 1e-9), 55.0)
            return {"available": True, "run_id": self.run_id, "time_s": self.current_time_us / 1e6,
                "model_time_s": self.acoustic.time_s,
                "source_gain": self.acoustic.response.gain, "powered": self.acoustic.powered,
                "current_a": self.acoustic.current_a, "speed_rpm": 1500 * self.acoustic.speed_ratio,
                "oil_c": oil_state.get("last_value", 55.0), "cutoff_s": self.acoustic.cutoff_s,
                "oil_sample_time_s": oil_sample_s,
                "oil_original_c": original_at_sample,
                "thermal_mode": self.acoustic.thermal_mode,
                "thermal_time_scale": self.acoustic.thermal_time_scale,
                "natural_tau_s": self.acoustic.natural_tau_s,
                "original_natural_tau_s": self.acoustic.reference_natural_tau_s,
                "template": self.config.layout.get("acoustic_template", {}),
                "trip_latched": self.acoustic.trip_latched,
                "consecutive": self.engine.get_device("station").consecutive,
                "rule": self.rule, "feature": self.acoustic.feature,
                "source_wave": self._source_wave(self.acoustic.time_s),
                "attack_preview": self.attack_preview,
                "last_received": self.last_measurement,
                "wave_columns": ["time_s", "excitation_norm", "base_x", "base_y", "base_z",
                                 "delta_x", "delta_y", "delta_z", "observed_x", "observed_y", "observed_z"],
                "wave": self.acoustic.last_wave, "history": self.history, "events": self.events,
                "provenance": self.acoustic.provenance}
        return super()._get_view_locked(view_name, query)
