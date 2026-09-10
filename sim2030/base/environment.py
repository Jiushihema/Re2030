"""工况与简化影响模型。

模型为轻量规则/简化方程，仅用于底座状态传导，不宣称为高保真电磁暂态计算。
每个模型说明必须列出输入、输出、单位、参数依据及适用范围。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from sim2030.constants import LinkType
from sim2030.contracts import EffectRequest


class OperatingEnvironment:
    """共享工况模型（单母线简化电气模型 + 环境温度）。

    输入：一次设备的物理输出（开关位置、分接档位、补偿投切、冷却启停）。
    输出：母线电压、线路电流、无功、环境温度、冷却状态，供感知设备与一次设备读取。

    简化方程（单位：kV、A、Mvar、℃）：
        ratio        = 1 + tap_step_ratio * (tap_position - nominal_tap)
        bus_voltage  = nominal_voltage_kv * ratio + cap_boost_kv（补偿投入时）
        load_current = load_active_mw * 1000 / (sqrt(3) * bus_voltage)（仅断路器闭合时线路有流）
    """

    def __init__(self, config: Dict[str, Any], seed: Optional[int] = None):
        self.config = dict(config)
        self.electrical = dict(config.get("electrical", {}))
        self.nominal_voltage_kv = float(config.get("nominal_voltage_kv", 10.0))
        self.ambient_temp_c = float(config.get("ambient_temp_c", 30.0))
        self.load_active_mw = float(config.get("load_active_mw", 0.0))
        self.load_reactive_mvar = float(config.get("load_reactive_mvar", 0.0))
        self._shared: Dict[str, Any] = {
            "bus_voltage_kv": self.nominal_voltage_kv,
            "line_current_a": 0.0,
            "reactive_power_var": 0.0,
            "ambient_temp_c": self.ambient_temp_c,
            "cooling_on": False,
        }

    def _asset_state(self, physical_outputs: Dict[str, Dict[str, Any]], key: str, field: str, default: Any) -> Any:
        if key in self.electrical:
            key = self.electrical[key]
        return physical_outputs.get(key, {}).get(field, default)

    def update(self, time_us: int, dt_us: int, physical_outputs: Dict[str, Dict[str, Any]]) -> None:
        tap_asset = self.electrical.get("tap_asset", "")
        tap = self._asset_state(physical_outputs, tap_asset, "tap_position", self.electrical.get("nominal_tap", 5))
        tap_step_ratio = float(self.electrical.get("tap_step_ratio", 0.0))
        nominal_tap = int(self.electrical.get("nominal_tap", 5))
        ratio = 1.0 + tap_step_ratio * (float(tap) - nominal_tap)

        cap_connected = bool(self._asset_state(physical_outputs, self.electrical.get("compensation_asset", ""), "connected", False))
        breaker_closed = bool(self._asset_state(physical_outputs, self.electrical.get("breaker_asset", ""), "closed", False))
        cooling_on = bool(self._asset_state(physical_outputs, self.electrical.get("cooling_asset", ""), "running", False))

        bus_voltage_kv = self.nominal_voltage_kv * ratio
        if cap_connected:
            bus_voltage_kv += float(self.electrical.get("cap_voltage_boost_kv", 0.0))

        load_current_a = 0.0
        if bus_voltage_kv > 0:
            load_current_a = self.load_active_mw * 1000.0 / (math.sqrt(3.0) * bus_voltage_kv)
        line_current_a = load_current_a if breaker_closed else 0.0

        self._shared = {
            "bus_voltage_kv": bus_voltage_kv,
            "line_current_a": line_current_a,
            "reactive_power_var": self.load_reactive_mvar,
            "ambient_temp_c": self.ambient_temp_c,
            "cooling_on": cooling_on,
        }

    def inputs_for(self, asset_id: str) -> Dict[str, Any]:
        """给设备提供局部物理输入，不向算法暴露全局物理真值。"""
        return dict(self._shared)

    def snapshot(self) -> Dict[str, Any]:
        return dict(self._shared)


class EffectModel:
    """影响模型基类：按作用参数与局部条件计算 modifiers。"""

    key: str = ""

    def evaluate(self, request: EffectRequest, local_conditions: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


class ReadingOffsetEffect(EffectModel):
    """读数偏移：给目标测点施加固定偏移量。"""

    key = "reading_offset"

    def evaluate(self, request: EffectRequest, local_conditions: Dict[str, Any]) -> Dict[str, Any]:
        return {"reading_offset": float(request.parameters.get("offset", 0.0))}


class TimingOffsetEffect(EffectModel):
    """时序扰动：给目标设备施加时钟偏移（微秒）。"""

    key = "timing_offset"

    def evaluate(self, request: EffectRequest, local_conditions: Dict[str, Any]) -> Dict[str, Any]:
        return {"timing_offset_us": int(request.parameters.get("offset_us", 0))}


class ExecutionDelayEffect(EffectModel):
    """执行延迟：给目标动作施加额外延迟（微秒）。"""

    key = "execution_delay"

    def evaluate(self, request: EffectRequest, local_conditions: Dict[str, Any]) -> Dict[str, Any]:
        return {"execution_delay_us": int(request.parameters.get("delay_us", 0))}


class LinkDegradationEffect(EffectModel):
    """链路退化：给目标链路施加额外时延/丢包。"""

    key = "link_degradation"

    def evaluate(self, request: EffectRequest, local_conditions: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "extra_delay_us": int(request.parameters.get("extra_delay_us", 0)),
            "extra_loss_rate": float(request.parameters.get("extra_loss_rate", 0.0)),
        }


_EFFECT_MODELS: Dict[str, EffectModel] = {
    ReadingOffsetEffect.key: ReadingOffsetEffect(),
    TimingOffsetEffect.key: TimingOffsetEffect(),
    ExecutionDelayEffect.key: ExecutionDelayEffect(),
    LinkDegradationEffect.key: LinkDegradationEffect(),
}


def register_effect(effect_type: str, model: EffectModel) -> None:
    """注册可替换的影响模型。"""
    _EFFECT_MODELS[effect_type] = model


def get_effect_model(effect_type: str) -> Optional[EffectModel]:
    return _EFFECT_MODELS.get(effect_type)


def list_effect_types() -> List[str]:
    return sorted(_EFFECT_MODELS.keys())