"""单相授时攻击教学模型。批量传送 4 kHz 样本，不改变全站调度步长。

CT 的工频有效值在每个调度块内零阶保持，MU 合成带方向的 50 Hz 波形。
保护只用本地秒序号与 smpCnt 配对，真实时刻仅供仿真证据展示。
"""
from collections import deque
import math

from sim2030.base.bay import DifferentialProtectionDevice
from sim2030.base.process import AcquisitionDevice
from sim2030.base.station import TimeService
from sim2030.base.security import ElectromagneticAttackNode
from sim2030.constants import BusinessType

SAMPLE_US = 250
SAMPLES_PER_SECOND = 4000
WINDOW = 80
EPSILON = 1e-6


def number(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("参数必须为有限数值")
    if not minimum <= value <= maximum:
        raise ValueError(f"参数须在 {minimum}–{maximum} 之间")
    return float(value)


def parameter(key, label, value, unit="", minimum=0, maximum=10000, step=1):
    return {"kind": "parameter", "key": key, "label": label, "value": value,
            "unit": unit, "input": {"min": minimum, "max": maximum, "step": step}}


class TimingAttack(ElectromagneticAttackNode):
    def build_effect_request(self, time_us):
        request = super().build_effect_request(time_us)
        request.parameters = {"offset_us": -int(self.parameters.get("delay_us", 2000))}
        return request

    def set_parameter(self, key, value):
        if key != "delay_us":
            raise ValueError("未知授时攻击参数")
        value = number(value, 0, 10000)
        if value % SAMPLE_US:
            raise ValueError("延迟须为 250 μs 的整数倍")
        if self.emitting:
            raise ValueError("请先结束攻击，再调整延迟")
        super().set_parameter(key, int(value))

    def describe_controls(self):
        return [*super().describe_controls(), parameter("delay_us", "时钟落后量（结束攻击后可调）",
                          self.parameters.get("delay_us", 2000), "μs", maximum=10000, step=250)]


class TimingService(TimeService):
    def step(self, time_us, dt_us, inputs):
        messages = super().step(time_us, dt_us, inputs)
        self.state.update(reference_time_us=time_us,
                          local_time_us=time_us + self.state["timing_offset_us"])
        return messages


class TimingMU(AcquisitionDevice):
    def __init__(self, spec):
        super().__init__(spec)
        self.state.update(clock_offset_us=0, synced=False)

    def receive(self, message):
        if message.business_type == BusinessType.SYNC:
            self._inbox.append(message)
        else:
            super().receive(message)

    def step(self, time_us, dt_us, inputs):
        # The preceding interval was sampled with the previous clock setting.
        offset = int(self.state["clock_offset_us"])
        was_synced = self.state["synced"]
        for message in self._drain_inbox():
            if message.business_type == BusinessType.SYNC:
                self.state.update(clock_offset_us=int(message.payload["timing_offset_us"]),
                                  synced=True, sync_received_us=time_us)
        self.state.update(reference_time_us=time_us,
                          local_time_us=time_us + self.state["clock_offset_us"])
        messages = self._drain_outbox()
        if time_us == 0 or not self._latest or not was_synced:
            return messages
        rms = float(self._latest.get("ct_current", {}).get("value", 0))
        sign = self.parameters.get("polarity", 1)
        samples = []
        for true_us in range(max(0, time_us - dt_us), time_us, SAMPLE_US):
            local_tick = (true_us + offset) // SAMPLE_US
            if local_tick < 0:
                continue
            samples.append({"epoch": local_tick // SAMPLES_PER_SECOND,
                            "smpCnt": local_tick % SAMPLES_PER_SECOND,
                            "true_time_us": true_us, "local_time_us": local_tick * SAMPLE_US,
                            "value": sign * math.sqrt(2) * rms * math.sin(2 * math.pi * 50 * true_us / 1e6)})
        if not samples:
            return messages
        payload = {"acquisition_id": self.asset_id, "samples": {"ct_current": rms},
                   "units": {"ct_current": "A RMS"}, "sample_time_us": time_us,
                   "timing_samples": samples, "sample_rate_hz": 4000,
                   "clock_offset_us": offset}
        self.state.update(last_sample=samples[-1], sample_count=len(samples), rms_a=rms)
        messages.extend(self._new_message(self.out_port, BusinessType.SAMPLING, payload, time_us))
        return messages


class TimingProtection(DifferentialProtectionDevice):
    def __init__(self, spec):
        super().__init__(spec)
        self.pending = {"mu": {}, "mu_2": {}}
        self.window = deque(maxlen=WINDOW)
        self.wave = deque(maxlen=1200)
        self.baseline = []
        self.trip_wave = []
        self.history = deque(maxlen=100)
        self.events = deque(maxlen=100)
        self.last_pair = -1
        self.report = {}
        self.trip_count = 0
        self.last_trip = None
        self._now = 0
        self._observed = {}

    def receive(self, message):
        source = (message.payload or {}).get("acquisition_id")
        if message.business_type == BusinessType.SAMPLING and source in self.pending:
            for sample in message.payload.get("timing_samples", []):
                key = sample["epoch"] * SAMPLES_PER_SECOND + sample["smpCnt"]
                if key > self.last_pair:
                    self.pending[source][key] = dict(sample)
        super().receive(message)

    def step(self, time_us, dt_us, inputs):
        self._now = time_us
        return super().step(time_us, dt_us, inputs)

    def gates(self, differential, restraint):
        minimum = float(self.parameters.get("minimum_a", 10))
        slope = float(self.parameters.get("slope", .2))
        return [
            {"key": "minimum_enabled", "label": "最小动作门槛", "enabled": bool(self.parameters.get("minimum_enabled", True)),
             "implemented": True, "passed": differential > minimum, "threshold": minimum},
            {"key": "restraint_enabled", "label": "比例制动门槛", "enabled": bool(self.parameters.get("restraint_enabled", True)),
             "implemented": True, "passed": differential > slope * restraint, "threshold": slope * restraint},
            {"key": "direction_enabled", "label": "方向／内部故障许可", "enabled": bool(self.parameters.get("direction_enabled", False)), "implemented": False, "passed": None},
            {"key": "ct_enabled", "label": "CT 故障闭锁", "enabled": bool(self.parameters.get("ct_enabled", False)), "implemented": False, "passed": None},
        ]

    def evaluate_differential(self):
        actions = []
        left, right = self.pending["mu"], self.pending["mu_2"]
        for key in sorted(left.keys() & right.keys()):
            a, b = left.pop(key), right.pop(key)
            if key <= self.last_pair:
                continue
            if key != self.last_pair + 1:
                self.window.clear()
            self.last_pair = key
            pair = {"epoch": a["epoch"], "smpCnt": a["smpCnt"], "local_time_us": a["local_time_us"],
                    "true_1_us": a["true_time_us"], "true_2_us": b["true_time_us"],
                    "skew_us": b["true_time_us"] - a["true_time_us"],
                    "i1": a["value"], "i2": b["value"], "id": a["value"] + b["value"]}
            self.window.append(pair)
            self.wave.append(pair)
            if len(self.window) < WINDOW or key % WINDOW != WINDOW - 1:
                continue
            phasors = []
            for field in ("i1", "i2"):
                phasors.append(math.sqrt(2) / WINDOW * sum(
                    row[field] * complex(math.cos(2 * math.pi * j / WINDOW),
                                         -math.sin(2 * math.pi * j / WINDOW))
                    for j, row in enumerate(self.window)))
            differential = abs(sum(phasors))
            restraint = sum(abs(p) for p in phasors)
            gates = self.gates(differential, restraint)
            active = differential > EPSILON and all(g["passed"] for g in gates if g["implemented"] and g["enabled"])
            self.report = {"valid": True, "id_a": differential, "ir_a": restraint,
                           "i1_rms_a": abs(phasors[0]), "i2_rms_a": abs(phasors[1]),
                           "gates": gates, "operate": active, "pair": pair,
                           "window_end_us": max(a["true_time_us"], b["true_time_us"])}
            if abs(pair["skew_us"]) == 0 and restraint > EPSILON and differential < EPSILON:
                self.baseline = list(self.wave)
            if not active:
                self._latched = False
            elif not self._latched:
                self._latched = True
                self.trip_count += 1
                self.last_trip = {**self.report, "time_us": self._now, "trip_no": self.trip_count}
                self.trip_wave = list(self.window)
                self.events.append({"time_us": self._now, "text": "差动保护出口跳闸", "id_a": differential,
                                    "ir_a": restraint, "skew_us": pair["skew_us"]})
                actions.append({"action_type": "trip", "source": "timing_differential",
                                "request_id": f"timing-trip-{self._now}-{self.trip_count}",
                                "parameters": {"source": "timing_differential"},
                                "differential": dict(self.last_trip)})
        # Bound unmatched data to two seconds; repeated / old counts cannot be reused.
        for queue in self.pending.values():
            newest = max(queue, default=self.last_pair)
            for key in list(queue):
                if key <= self.last_pair or key < newest - 8000:
                    del queue[key]
        if self.report:
            self.history.append({"time_us": self._now, "id_a": self.report["id_a"], "ir_a": self.report["ir_a"]})
        return actions

    def record_chain_event(self, time_us, text, device_id=""):
        """由 :class:`TimingLab` 在全站视图中观察到链路事件后回调写入。

        设备只负责保存自己的事件；不再改写 ``StepResult`` 快照。
        """
        event = {"time_us": time_us, "text": text}
        if device_id:
            event["device_id"] = device_id
        self.events.append(event)

    def set_parameter(self, key, value):
        if key in ("minimum_enabled", "restraint_enabled", "direction_enabled", "ct_enabled"):
            if not isinstance(value, bool):
                raise ValueError("开关必须为布尔值")
        elif key == "minimum_a":
            value = number(value, 0, 10000)
        elif key == "slope":
            value = number(value, 0, 1)
        else:
            raise ValueError("未知差动保护参数")
        super().set_parameter(key, value)

    def describe_controls(self):
        controls = [parameter("minimum_a", "最小动作电流 Imin", self.parameters.get("minimum_a", 10), "A RMS", step=.1),
                    parameter("slope", "比例制动系数 k", self.parameters.get("slope", .2), maximum=1, step=.01)]
        controls.extend({"kind": "toggle", "key": gate["key"], "label": gate["label"],
                         "value": gate["enabled"], "reserved": not gate["implemented"]}
                        for gate in self.gates(0, 0))
        return controls

    def snapshot(self):
        snap = super().snapshot()
        snap["timing"] = {"report": self.report, "waveform": list(self.wave), "baseline": self.baseline,
                          "trip_waveform": self.trip_wave, "last_trip": self.last_trip,
                          "history": list(self.history), "events": list(self.events), "trip_count": self.trip_count,
                          "settings": {"minimum_a": self.parameters.get("minimum_a", 10), "slope": self.parameters.get("slope", .2)},
                          "gates": self.gates(self.report.get("id_a", 0), self.report.get("ir_a", 0))}
        return snap


FACTORIES = {"clock": TimingService, "mu": TimingMU, "protection": TimingProtection, "attack": TimingAttack}

# 授时实验场景必须声明的设备 ID；用于加载期校验，避免运行期 KeyError。
REQUIRED_DEVICE_KEYS = (
    "protection_device_id", "terminal_device_id", "breaker_device_id",
    "reference_mu_id", "lagging_mu_id", "lagging_clock_id",
)

# 观察链路事件时监控的设备及其状态字段：(配置键, 状态字段, 显示标签)
OBSERVED_DEVICES = (
    ("lagging_clock_id", "timing_offset_us", "授时系统-2偏移"),
    ("lagging_mu_id", "clock_offset_us", "合并单元-2时钟偏移"),
    ("breaker_device_id", "position", "断路器位置"),
)


class TimingLab:
    """授时实验观察器：独立对象，只读全站结果，不改写设备或 ``StepResult``。

    与 :class:`sim2030.goose_lab.GooseLab` 结构对称：``Application`` 只持有一个
    lab 对象，调度时调用它的 ``observe``/``view``，不直接触碰具体设备的私有方法。
    """

    def __init__(self, settings, device_ids):
        if not isinstance(settings, dict):
            raise ValueError("layout.timing_lab 必须是对象")
        missing = [key for key in REQUIRED_DEVICE_KEYS if not settings.get(key)]
        if missing:
            raise ValueError(f"timing_lab 缺少配置项：{', '.join(missing)}")
        unknown = [settings[key] for key in REQUIRED_DEVICE_KEYS if settings[key] not in device_ids]
        if unknown:
            raise ValueError(f"timing_lab 引用了不存在的设备：{', '.join(sorted(set(unknown)))}")
        self.settings = dict(settings)
        self.ids = {key: settings[key] for key in REQUIRED_DEVICE_KEYS}
        self._observed = {}

    def _protection(self, engine):
        return engine.get_device(self.ids["protection_device_id"])

    def observe(self, time_us, engine, result):
        """记录差动跳闸指令的送达与关键设备状态变化；只读 ``result``。"""
        protection = self._protection(engine)
        if protection is None:
            return
        terminal_id = self.ids["terminal_device_id"]
        for message in result.messages:
            payload = message.payload or {}
            if (message.receiver_id != terminal_id or message.business_type != BusinessType.COMMAND
                    or payload.get("parameters", {}).get("source") != "timing_differential"
                    or message.message_id in self._observed):
                continue
            # Engine results contain sends as well as deliveries; delivered messages
            # also generate a downstream command whose sender is the terminal.
            if not any(m.sender_id == terminal_id and m.related_request == message.related_request
                       for m in result.messages):
                continue
            self._observed[message.message_id] = True
            protection.record_chain_event(time_us, "智能终端接收差动跳闸指令，转发控制硬接线")
        for key, field_name, label in OBSERVED_DEVICES:
            device_id = self.ids[key]
            value = result.device_snapshots.get(device_id, {}).get("state", {}).get(field_name)
            previous = self._observed.get(device_id)
            if value is not None and previous != value:
                self._observed[device_id] = value
                protection.record_chain_event(time_us, f"{label}：{value}", device_id=device_id)

    def view(self, engine):
        """现取保护装置的授时快照；设备不存在时返回 ``None`` 而不是抛错。"""
        protection = self._protection(engine)
        if protection is None:
            return None
        return protection.snapshot().get("timing")


def _timing_override(spec):
    """设备覆写钩子：声明 ``timing_role`` 的设备改用授时实验实现。"""
    role = spec.parameters.get("timing_role")
    if not role:
        return None
    if role not in FACTORIES:
        raise ValueError(f"未知授时实验角色：{role}")
    return FACTORIES[role](spec)


_installed = False


def install():
    """注册授时实验的设备覆写钩子；重复调用不会重复注册。"""
    global _installed
    if _installed:
        return
    from sim2030.scenario import register_device_override

    register_device_override(_timing_override)
    _installed = True
