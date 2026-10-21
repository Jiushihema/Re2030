"""标准库模型：真实正常片段循环回放 + 连续域参考响应 + 停机热过程。"""
from __future__ import annotations

import cmath
import csv
import gzip
import json
import math
from pathlib import Path
from statistics import fmean, pstdev

DATA = Path(__file__).with_name("data")
FS = 4000
WEIGHTS = (1.0, 0.6, 0.3)


class AcousticResponse:
    """解析积分连续三阶参考模型，再以 4 kHz 读取；不先把载波降采样。

    H(s)=w0²*wc/[(s²+w0/Q*s+w0²)*(s+wc)]。
    每段输入是有限个余弦的和，指数解同时保留启动/关闭暂态。
    """

    def __init__(self, f0=5500.0, q=17.0, cutoff=1500.0):
        w0, wc = 2 * math.pi * f0, 2 * math.pi * cutoff
        real = -w0 / (2 * q)
        imag = w0 * math.sqrt(1 - 1 / (4 * q * q))
        self.poles = (complex(real, imag), complex(real, -imag), complex(-wc))
        self.residues = tuple(w0 * w0 * wc / math.prod(
            pole - other for j, other in enumerate(self.poles) if i != j
        ) for i, pole in enumerate(self.poles))
        self.carrier = 2 * math.pi * 5500
        self.ramp_s = 0.02
        self.segments = [(0.0, (0j, 0j, 0j), ())]
        self.gain = 0.0
        self.started_s = 0.0

    def _state(self, segment, time_s):
        start, initial, tones = segment
        elapsed = max(0.0, time_s - start)
        result = []
        for pole, value in zip(self.poles, initial):
            decay = cmath.exp(pole * elapsed)
            total = value * decay
            for amplitude, omega, phase in tones:
                for sign in (1, -1):
                    iw = sign * 1j * omega
                    total += (amplitude / 2 * cmath.exp(sign * 1j * phase)
                              * (cmath.exp(iw * elapsed) - decay) / (iw - pole))
            result.append(total)
        return tuple(result)

    def _segment(self, time_s):
        return next(seg for seg in reversed(self.segments) if seg[0] <= time_s + 1e-12)

    def value(self, time_s):
        segment = self._segment(time_s)
        state = self._state(segment, time_s)
        output = sum(c * z for c, z in zip(self.residues, state)).real
        elapsed = time_s - segment[0]
        excitation = sum(a * math.cos(w * elapsed + p) for a, w, p in segment[2])
        return excitation, output

    def input_at(self, time_s):
        start, _, tones = self._segment(time_s)
        return sum(a * math.cos(w * (time_s - start) + p) for a, w, p in tones)

    def switch(self, gain, time_s):
        if gain and self.gain:
            raise ValueError("请先关闭当前干扰，再选择新的档位")
        if not gain and not self.gain:
            return
        initial = self._state(self._segment(time_s), time_s)
        self.segments = [s for s in self.segments if s[0] < time_s]
        w, wr, ramp = self.carrier, math.pi / self.ramp_s, self.ramp_s
        if gain:
            self.started_s = time_s
            phase, amplitude, side = 0.0, gain, -1
        else:
            phase, amplitude, side = w * (time_s - self.started_s), self.gain, 1
        tones = ((amplitude / 2, w, phase),
                 (side * amplitude / 4, w + wr, phase),
                 (side * amplitude / 4, w - wr, phase))
        first = (time_s, initial, tones)
        self.segments.append(first)
        final = self._state(first, time_s + ramp)
        steady = ((gain, w, w * ramp),) if gain else ()
        self.segments.append((time_s + ramp, final, steady))
        self.gain = gain


class DemoModel:
    """全部物理参数都是未标定的演示假设，振动输入单位保留 count。"""

    def __init__(self, parameters=None):
        self.parameters = dict(parameters or {})
        self.thermal_time_scale = float(self.parameters.get("time_scale", 1.0))
        if not math.isfinite(self.thermal_time_scale) or self.thermal_time_scale <= 0:
            raise ValueError("热过程加速系数必须是有限正数")
        self.thermal_mode = "original_scale" if self.thermal_time_scale == 1 else "accelerated_demo"
        self.reference_capacity_j_per_k = 3600000.0
        self.effective_capacity_j_per_k = self.reference_capacity_j_per_k / self.thermal_time_scale
        self.reference_natural_tau_s = self.reference_capacity_j_per_k / 500
        self.natural_tau_s = self.reference_natural_tau_s / self.thermal_time_scale
        self.provenance = json.loads((DATA / "provenance.json").read_text(encoding="utf-8"))
        with gzip.open(DATA / "normal_xyz.csv.gz", "rt", encoding="utf-8", newline="") as handle:
            self.baseline = [tuple(map(float, row)) for row in list(csv.reader(handle))[1:]]
        self.means = tuple(fmean(row[i] for row in self.baseline) for i in range(3))
        self.scales = tuple(pstdev(row[i] for row in self.baseline) for i in range(3))
        self.response = AcousticResponse()
        self.powered = True
        self.trip_latched = False
        self.current_a, self.speed_ratio = 2.0, 1.0
        self.oil_c = self.oil_sensor_c = 55.0
        self.oil_original_c = self.oil_original_sensor_c = 55.0
        self.time_s = 0.0
        self.cutoff_s = None
        self.last_sample = 0
        self.window = [[], [], []]
        self.last_wave = []
        self.feature = None

    def advance(self, time_s):
        while time_s - self.time_s > 1e-9:
            dt = min(0.1, time_s - self.time_s)
            target = 1.0 if self.powered else 0.0
            old_speed = self.speed_ratio
            self.speed_ratio = target + (old_speed - target) * math.exp(-dt / 5.0)
            self.current_a = 2 * target + (self.current_a - 2 * target) * math.exp(-dt / 0.25)
            # C*dT/dt=P-(G0+Gf*r)*(T-Ta); mid-step r, exponential thermal update.
            speed_mid = target + (old_speed - target) * math.exp(-dt / 10.0)
            conductance = 500 + 500 * speed_mid
            equilibrium = 25 + 30000 / conductance
            self.oil_c = equilibrium + (self.oil_c - equilibrium) * math.exp(-conductance * dt / self.effective_capacity_j_per_k)
            self.oil_sensor_c += (self.oil_c - self.oil_sensor_c) * (-math.expm1(-dt / 5))
            self.oil_original_c = equilibrium + (self.oil_original_c - equilibrium) * math.exp(-conductance * dt / self.reference_capacity_j_per_k)
            self.oil_original_sensor_c += (self.oil_original_c - self.oil_original_sensor_c) * (-math.expm1(-dt / 5))
            self.time_s += dt

    def set_power(self, powered, time_s):
        self.advance(time_s)
        if self.powered and not powered:
            self.cutoff_s = time_s
        self.powered = powered

    def sample_to(self, time_s):
        """只处理截至当前仿真时刻的完整样本，1 秒去均值 RMS 窗口不重叠。"""
        end = round(time_s * FS)
        new_features = []
        for index in range(self.last_sample, end):
            t = index / FS
            raw = self.baseline[index % len(self.baseline)]
            # 降速后机械振动幅值随转速比例变化，是新增的演示假设。
            base = tuple(m + (v - m) * self.speed_ratio for m, v in zip(self.means, raw))
            excitation, response = self.response.value(t)
            delta = tuple(s * w * response for s, w in zip(self.scales, WEIGHTS))
            observed = tuple(b + d for b, d in zip(base, delta))
            for axis in range(3):
                self.window[axis].append(observed[axis])
            self.last_wave.append([t, excitation, *base, *delta, *observed])
            if (index + 1) % FS == 0:
                ratios = [pstdev(values) / scale for values, scale in zip(self.window, self.scales)]
                self.feature = {"window_end_s": (index + 1) / FS,
                                "rms_ratio_xyz": ratios, "max_ratio": max(ratios)}
                new_features.append(dict(self.feature))
                self.window = [[], [], []]
        self.last_wave = self.last_wave[-80:]  # 20 ms，保留全部 4 kHz 样本供看波形。
        self.last_sample = end
        return new_features
