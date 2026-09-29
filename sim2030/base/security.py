"""安全相关节点：电磁攻击节点与频谱检测节点。

两者部署在一次设备附近，用于表达“攻击源”和“检测点”的位置关系：

- :class:`ElectromagneticAttackNode` 表示电流互感器附近的电磁干扰源。读数偏差本身由
  场景攻击计划与底座效果模型（``reading_offset``）施加，节点只表达自身状态。
- :class:`SpectrumDetectorNode` 是频谱检测节点的占位实现，暂不产生识别结论。
"""
from __future__ import annotations

from typing import Any, Dict, List

from sim2030.contracts import DeviceSpec, EffectRequest
from sim2030.base.device import Device


class ElectromagneticAttackNode(Device):
    """电磁攻击节点：电流互感器附近的电磁干扰发射源。

    通过演示平面手动“开始攻击/结束攻击”施加或撤销对 ``target_asset_id`` 的读数偏移；
    读数偏差由底座效果模型（默认 ``reading_offset``）计算，节点本身不直接改写设备状态。

    参数：``target_asset_id``（作用目标测点，默认 ``ct_current``）、``reading_offset``
    （读数偏移量，默认 40.0）、``frequency_mhz``（干扰中心频率）、``power_dbm``（等效发射功率）。
    """

    # 手动攻击不设到期时间，由“结束攻击”显式撤销。
    HOLD_FOREVER_US = 2 ** 62

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.target_asset_id: str = self.parameters.get("target_asset_id", "ct_current")
        self.reading_offset: float = float(self.parameters.get("reading_offset", 40.0))
        self.effect_type: str = self.parameters.get("effect_type", "reading_offset")
        self.frequency_mhz: float = float(self.parameters.get("frequency_mhz", 0.0))
        self.power_dbm: float = float(self.parameters.get("power_dbm", 0.0))
        self.state.setdefault("emitting", False)

    @property
    def effect_id(self) -> str:
        return f"manual-em-{self.asset_id}"

    @property
    def emitting(self) -> bool:
        return bool(self.state.get("emitting"))

    def set_emitting(self, emitting: bool) -> None:
        self.state["emitting"] = bool(emitting)

    def describe_controls(self) -> List[Dict[str, Any]]:
        """演示平面控制项：随攻击开关状态在“开始攻击/结束攻击”之间切换。"""
        return [{
            "kind": "command",
            "action": "stop_attack" if self.emitting else "start_attack",
            "label": "结束攻击" if self.emitting else "开始攻击",
            "params": [],
        }]

    def build_effect_request(self, time_us: int) -> EffectRequest:
        return EffectRequest(
            effect_id=self.effect_id,
            target_id=self.target_asset_id,
            effect_type=self.effect_type,
            parameters={"offset": self.reading_offset},
            start_time_us=int(time_us),
            end_time_us=self.HOLD_FOREVER_US,
        )

    def snapshot(self) -> Dict[str, Any]:
        data = super().snapshot()
        data["state"]["target_asset_id"] = self.target_asset_id
        data["state"]["frequency_mhz"] = self.frequency_mhz
        data["state"]["power_dbm"] = self.power_dbm
        return data


class SpectrumDetectorNode(Device):
    """频谱检测节点：占位实现，暂不产生检测结论（先不设计具体功能）。

    参数：``mode``（展示用检测模式，默认 ``频谱检测``）。
    """

    def __init__(self, spec: DeviceSpec):
        super().__init__(spec)
        self.state.setdefault("mode", self.parameters.get("mode", "频谱检测"))
