"""运行调度与五平面编排。

``Application`` 只负责组装完整运行上下文并按固定顺序推进，不包含设备业务逻辑。
攻击/识别/防御/演示平面接入后复用同一 ``step()`` 顺序。
"""
from __future__ import annotations

import os
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sim2030.attack.planner import AttackPlanner
from sim2030.base.engine import SimulationEngine
from sim2030.base.observations import FileObservationSource, ObservationGateway, EvidenceReader
from sim2030.contracts import StepResult
from sim2030.defense.engine import DEFAULT_STRATEGIES, DefenseEngine
from sim2030.evaluation import Evaluator
from sim2030.recognition.engine import DEFAULT_DETECTORS, RecognitionEngine
from sim2030.records import RunReader, RunRecorder
from sim2030.scenario import load_scenario, validate_scenario

STATUS_READY = "ready"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_FINISHED = "finished"
STATUS_ERROR = "error"

CONTROL_ACTIONS = {"start_run", "pause_run", "stop_run"}


class Application:
    """单场景运行调度器；仅同时运行一个场景。"""

    def __init__(self, output_root: str = "runs") -> None:
        self.output_root = output_root
        self.status: str = STATUS_READY
        self.run_id: str = ""
        self.config = None
        self.config_path: str = ""
        self.observation_base_dir: str = "."
        self.engine = SimulationEngine()
        self.recorder: Optional[RunRecorder] = None
        self.current_time_us: int = 0
        self.dt_us: int = 100000
        self.duration_us: int = 0
        self._device_ids: set = set()
        self._pending_operations: List[Dict[str, Any]] = []
        self._pending_defense_requests: List[Any] = []
        self._defense_feedback: List[Any] = []
        self._last_step_summary: Dict[str, Any] = {}
        self._recognition_enabled = False
        self._defense_enabled = False
        self._observation_window_size_us = 1_000_000

        self.observation_source = FileObservationSource()
        self.observation_gateway = ObservationGateway()
        self.evidence_reader = EvidenceReader(".")
        self.attack_planner = AttackPlanner()
        self.recognition_engine: Optional[RecognitionEngine] = None
        self.defense_engine: Optional[DefenseEngine] = None
        self.evaluator = Evaluator()

        self._recorded_defense_status: Dict[str, str] = {}
        self._lock = threading.RLock()

    # ──────────────────────────────────────────────
    # 装载与控制
    # ──────────────────────────────────────────────
    def load(self, config_path: str) -> str:
        """校验场景、创建底座与记录目录，返回 run_id。"""
        config = load_scenario(config_path)
        errors = validate_scenario(config)
        if errors:
            raise ValueError("场景校验失败：\n- " + "\n- ".join(errors))

        self._close_recorder()
        self.config = config
        simulation = config.simulation
        self.dt_us = int(simulation.get("dt_us", 100000))
        self.duration_us = int(simulation.get("duration_us", 0))
        self.current_time_us = 0
        self._device_ids = {d.device_id for d in config.devices}
        self._pending_operations = []
        self._pending_defense_requests = []
        self._defense_feedback = []
        self._recorded_defense_status = {}

        recognition_cfg = dict(config.recognition or {})
        self._recognition_enabled = bool(recognition_cfg.get("enabled", False))
        recognition_cfg.setdefault("scenario_id", config.scenario_id)
        defense_cfg = dict(config.defense or {})
        self._defense_enabled = bool(defense_cfg.get("enabled", False))

        observation_cfg = dict(config.observations or {})
        self._observation_window_size_us = int(
            observation_cfg.get("window_size_us", recognition_cfg.get("window_size_us", 1_000_000))
        )

        self.engine.build(config.devices, config.links, config.environment)

        self.config_path = os.path.abspath(config_path)
        base_dir = observation_cfg.get("base_dir") or observation_cfg.get("provider_dir") or observation_cfg.get("data_dir") or observation_cfg.get("path") or os.path.dirname(self.config_path)
        self.observation_base_dir = base_dir
        self.observation_source = FileObservationSource(observation_cfg)
        self.observation_gateway = ObservationGateway(observation_cfg)
        self.evidence_reader = EvidenceReader(base_dir)

        self.attack_planner = AttackPlanner()
        self.attack_planner.load(config.attacks)

        self.recognition_engine = RecognitionEngine(config=recognition_cfg, topology=self._build_topology(config))
        for name, detector in DEFAULT_DETECTORS.items():
            self.recognition_engine.register(name, detector)

        self.defense_engine = DefenseEngine(config=defense_cfg)
        for name, strategy in DEFAULT_STRATEGIES.items():
            self.defense_engine.register(name, strategy)

        self.run_id = self._make_run_id(config.scenario_id)
        self.recorder = RunRecorder(self.run_id, os.path.join(self.output_root, self.run_id))
        self.recorder.save_manifest(self._config_summary())
        self._record_attack_truth()

        for operation in config.operations:
            self.submit_operation(operation)

        self.status = STATUS_READY
        self._last_step_summary = self._summary()
        return self.run_id

    def start(self) -> None:
        if self.status in (STATUS_READY, STATUS_PAUSED):
            self.status = STATUS_RUNNING

    def pause(self) -> None:
        if self.status == STATUS_RUNNING:
            self.status = STATUS_PAUSED

    def stop(self) -> None:
        if self.status in (STATUS_RUNNING, STATUS_PAUSED):
            self.status = STATUS_FINISHED
            self._close_recorder()

    def close(self) -> None:
        """释放当前运行持有的文件句柄，供服务或测试安全清理目录。"""
        self._close_recorder()

    # ──────────────────────────────────────────────
    # 操作与查询
    # ──────────────────────────────────────────────
    def submit_operation(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """校验并受理控制/业务操作；业务操作在下一步边界交给底座执行。"""
        with self._lock:
            return self._submit_operation_locked(request)

    def set_device_parameter(self, device_id: str, key: str, value: Any) -> Dict[str, Any]:
        """运行期更新设备可调参数；不经过业务命令队列，立即作用于底座实例。"""
        with self._lock:
            return self._set_device_parameter_locked(device_id, key, value)

    def _set_device_parameter_locked(self, device_id: str, key: str, value: Any) -> Dict[str, Any]:
        if not self.run_id:
            return {"device_id": device_id, "status": "not_found", "reason": "当前没有活动运行"}
        if device_id not in self._device_ids:
            return {"device_id": device_id, "status": "not_found", "reason": f"未知目标设备 {device_id}"}
        result = self.engine.set_device_parameter(device_id, key, value)
        self._last_step_summary = self._summary()
        return result

    def get_device_controls(self, device_id: str) -> Dict[str, Any]:
        """返回指定设备在演示平面可用的控制项描述。"""
        with self._lock:
            return self._get_device_controls_locked(device_id)

    def _get_device_controls_locked(self, device_id: str) -> Dict[str, Any]:
        if not self.run_id:
            return {"device_id": device_id, "status": "not_found", "reason": "当前没有活动运行"}
        if device_id not in self._device_ids:
            return {"device_id": device_id, "status": "not_found", "reason": f"未知目标设备 {device_id}"}
        return self.engine.get_device_controls(device_id)

    def _submit_operation_locked(self, request: Dict[str, Any]) -> Dict[str, Any]:
        request_id = request.get("request_id") or str(uuid.uuid4())
        action_type = request.get("action_type", "")
        target_id = request.get("target_asset_id", "")
        operation_type = request.get("operation_type", "")

        if target_id == "run" or operation_type in ("start", "pause", "stop") or action_type in CONTROL_ACTIONS:
            return self._submit_control(request, request_id)

        if target_id not in self._device_ids:
            return {"request_id": request_id, "status": "rejected", "reason": f"未知目标设备 {target_id}"}
        if not action_type:
            return {"request_id": request_id, "status": "rejected", "reason": "缺少 action_type"}

        operation = dict(request)
        operation["request_id"] = request_id
        operation.setdefault("time_us", self.current_time_us)
        self._pending_operations.append(operation)
        return {"request_id": request_id, "status": "queued"}

    def _submit_control(self, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
        action = request.get("operation_type") or request.get("action_type", "")
        if action in ("start", "start_run"):
            self.start()
            return {"request_id": request_id, "status": "accepted", "action": "start"}
        if action in ("pause", "pause_run"):
            self.pause()
            return {"request_id": request_id, "status": "accepted", "action": "pause"}
        if action in ("stop", "stop_run"):
            self.stop()
            return {"request_id": request_id, "status": "accepted", "action": "stop"}
        if action == "step":
            summary = self._step_locked()
            return {"request_id": request_id, "status": "accepted", "action": "step", "summary": summary}
        return {"request_id": request_id, "status": "rejected", "reason": f"未知控制操作 {action}"}

    def step(self) -> Dict[str, Any]:
        """按单步运行顺序推进一次仿真，返回展示摘要。"""
        with self._lock:
            return self._step_locked()

    def _step_locked(self) -> Dict[str, Any]:
        if self.status != STATUS_RUNNING:
            return self._summary()

        # 1. 接收排队操作，提交到期攻击及上一轮防御请求。
        self._submit_due_operations()
        self._submit_due_attacks()
        self._submit_pending_defenses()

        # 2-3. 底座推进共享工况、消息投递与设备状态更新。
        result = self.engine.step(self.current_time_us, self.dt_us)

        # 4. 接入已交付的外部观测，形成识别窗口。
        ingest_batch, window = self._ingest_observations(self.current_time_us)

        # 5. 识别与防御，并检查既有动作反馈与恢复情况。
        recognition_results = []
        if self.recognition_engine is not None and self._recognition_enabled:
            recognition_results = self.recognition_engine.update(window, self.evidence_reader)

        defense_changed: List[Any] = []
        if self.defense_engine is not None:
            self._update_defense_feedback(result, window)
            if self._defense_enabled:
                target_ids = sorted(self._device_ids)
                capabilities = self.engine.get_capabilities(target_ids)
                management = self.engine.get_management(target_ids)
                requests = self.defense_engine.decide(recognition_results, window, capabilities, management)
                self._pending_defense_requests = requests
            defense_changed = self._changed_defense_actions()

        # 6. 分流保存本步结果，更新摘要，推进时钟。
        self._record_step(result, ingest_batch, window, recognition_results, defense_changed)

        self.current_time_us += self.dt_us
        if self.duration_us and self.current_time_us >= self.duration_us:
            self.status = STATUS_FINISHED
            self._close_recorder()

        self._last_step_summary = self._summary()
        return self._last_step_summary

    def run_to_end(self) -> None:
        """循环推进至 finished；用于无界面运行和测试。"""
        if self.duration_us <= 0:
            raise ValueError("duration_us 必须大于 0 才能自动运行到结束")
        self.start()
        while self.status == STATUS_RUNNING:
            self.step()

    def get_view(self, view_name: str, query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """返回限定用途的展示数据，不暴露可修改的底座对象。"""
        with self._lock:
            return self._get_view_locked(view_name, query)

    def _get_view_locked(self, view_name: str, query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        query = query or {}
        if view_name == "status":
            return self._summary()
        if view_name == "snapshot":
            target_ids = query.get("target_ids") or sorted(self._device_ids)
            return {"time_us": self.current_time_us, "snapshots": self.engine.get_management(target_ids)}
        if view_name == "capabilities":
            target_ids = query.get("target_ids") or sorted(self._device_ids)
            return {"capabilities": self.engine.get_capabilities(target_ids)}
        if view_name == "management":
            target_ids = query.get("target_ids") or sorted(self._device_ids)
            return {"management": self.engine.get_management(target_ids)}
        if view_name == "system":
            return self._system_view()
        if view_name == "timeline":
            return self._timeline_view()
        if view_name == "observations":
            return self._observation_view()
        if view_name == "recognition":
            return {"recognition": self._recognition_records()}
        if view_name == "defense":
            return {"defense": self._defense_records()}
        if view_name == "evaluation":
            return self._evaluation_view()
        return {"error": f"未知视图 {view_name}"}

    def replay(self, run_id: str, time_us: int) -> Dict[str, Any]:
        """读取已保存记录形成指定时刻视图，不重新执行攻击或防御。"""
        if self.run_id == run_id and self.recorder is not None:
            self.recorder.flush()
        run_dir = os.path.join(self.output_root, run_id)
        reader = RunReader(run_dir)
        return {
            "run_id": run_id,
            "time_us": time_us,
            "device_snapshots": reader.snapshot_at(time_us),
        }

    def _run_reader(self) -> Optional[RunReader]:
        """读取当前运行记录；运行中先刷新文件缓冲，保证能读到最新步。"""
        if not self.run_id:
            return None
        if self.recorder is not None:
            self.recorder.flush()
        return RunReader(os.path.join(self.output_root, self.run_id))

    def _stream_records(self, stream: str) -> List[Dict[str, Any]]:
        reader = self._run_reader()
        return reader.read(stream) if reader is not None else []

    def _recognition_records(self) -> List[Dict[str, Any]]:
        return self._stream_records("recognition")

    def _defense_records(self) -> List[Dict[str, Any]]:
        return self._stream_records("defense")

    def _attack_submission_records(self) -> List[Dict[str, Any]]:
        return [r for r in self._stream_records("attack") if r.get("type") == "attack_submission"]

    def _attack_truth_records(self) -> List[Dict[str, Any]]:
        return [r for r in self._stream_records("truth") if r.get("type") == "attack"]

    # ──────────────────────────────────────────────
    # 内部方法：单步调度
    # ──────────────────────────────────────────────
    def _submit_due_operations(self) -> None:
        due: List[Dict[str, Any]] = []
        remaining: List[Dict[str, Any]] = []
        for operation in self._pending_operations:
            if int(operation.get("time_us", 0)) <= self.current_time_us:
                due.append(operation)
            else:
                remaining.append(operation)
        self._pending_operations = remaining
        for operation in due:
            self.engine.submit_business_operation(operation)

    def _submit_due_attacks(self) -> None:
        for request in self.attack_planner.due_requests(self.current_time_us):
            receipt = self.engine.submit_effect(request)
            self.attack_planner.record_receipt(receipt)
            if self.recorder is not None:
                record = asdict(request)
                record.update({
                    "type": "attack_submission",
                    "status": receipt.get("status", "unknown"),
                    "reason": receipt.get("reason", ""),
                    "time_us": self.current_time_us,
                })
                self.recorder.append("attack", record)

    def _submit_pending_defenses(self) -> None:
        for request in self._pending_defense_requests:
            self._defense_feedback.append(self.engine.submit_defense(request))
        self._pending_defense_requests = []

    def _ingest_observations(self, time_us: int):
        raw = self.observation_source.read_available(time_us)
        ingest_batch = self.observation_gateway.ingest(raw, time_us)
        window_start = max(0, time_us - self._observation_window_size_us)
        window = self.observation_gateway.window(window_start, time_us)
        return ingest_batch, window

    def _update_defense_feedback(self, result: StepResult, window: Any) -> None:
        feedbacks = list(self._defense_feedback)
        self._defense_feedback = []
        for feedback in feedbacks:
            self.defense_engine.update_feedback(feedback, window)

        for message in result.messages:
            if message.business_type != "feedback":
                continue
            payload = message.payload or {}
            action_id = payload.get("action_id")
            status = payload.get("status")
            if not action_id or not status:
                continue
            if self.defense_engine.action(action_id) is None:
                continue
            self.defense_engine.update_feedback(
                {
                    "action_id": action_id,
                    "status": status,
                    "time_us": result.time_us,
                    "failure_reason": payload.get("reason", ""),
                },
                window,
            )

    def _changed_defense_actions(self) -> List[Any]:
        changed: List[Any] = []
        for action in self.defense_engine.actions():
            status = action.status
            if self._recorded_defense_status.get(action.action_id) != status:
                self._recorded_defense_status[action.action_id] = status
                changed.append(action)
        return changed

    # ──────────────────────────────────────────────
    # 内部方法：记录
    # ──────────────────────────────────────────────
    def _record_step(self, result, ingest_batch, window, recognition_results, defense_changed) -> None:
        recorder = self.recorder
        if recorder is None:
            return
        for message in result.messages:
            record = message.to_record()
            record["time_us"] = result.time_us
            recorder.append("business", record)
        for feedback in result.action_feedback:
            record = feedback.to_record()
            record["time_us"] = result.time_us
            recorder.append("business", record)

        environment = {}
        if self.engine is not None and hasattr(self.engine, "_environment"):
            environment = self.engine._environment.snapshot()

        recorder.append(
            "truth",
            {
                "type": "snapshot",
                "time_us": result.time_us,
                "device_snapshots": result.device_snapshots,
                "network_state": result.network_state,
                "environment": environment,
            },
        )

        for event in ingest_batch.events:
            record = asdict(event)
            record["time_us"] = result.time_us
            recorder.append("observation", record)
        if ingest_batch.events or window.missing or window.late:
            recorder.append(
                "observation",
                {
                    "type": "observation_batch",
                    "time_us": result.time_us,
                    "start_us": window.start_us,
                    "end_us": window.end_us,
                    "event_keys": [event.key() for event in window.events],
                    "missing": window.missing,
                    "late": window.late,
                },
            )

        for recognition in recognition_results:
            record = asdict(recognition)
            record["time_us"] = result.time_us
            record["start_time_us"] = result.time_us
            record["end_time_us"] = result.time_us
            recorder.append("recognition", record)

        for action in defense_changed:
            record = asdict(action)
            record["time_us"] = result.time_us
            recorder.append("defense", record)

    def _record_attack_truth(self) -> None:
        if self.recorder is None:
            return
        for step in self.attack_planner.attacks():
            effect_id = step.get("_effect_id") or step.get("effect_id") or step.get("attack_id")
            targets = list(step.get("target_asset_ids", []))
            if not targets and step.get("target_id"):
                targets = [step["target_id"]]
            self.recorder.append(
                "truth",
                {
                    "type": "attack",
                    "attack_id": effect_id,
                    "attack_type": step.get("attack_type", "unknown"),
                    "target_asset_ids": targets,
                    "start_time_us": int(step.get("start_time_us", 0)),
                    "end_time_us": int(step.get("end_time_us", 0)),
                    "effect_type": step.get("effect_type"),
                    "parameters": dict(step.get("parameters", {})),
                    "trigger": step.get("trigger", "time"),
                    "depends_on": list(step.get("depends_on", [])),
                    "time_us": int(step.get("start_time_us", 0)),
                },
            )

    # ──────────────────────────────────────────────
    # 内部方法：展示视图
    # ──────────────────────────────────────────────
    def _summary(self) -> Dict[str, Any]:
                # 提取在途报文流
        in_flight_messages = []
        if self.engine is not None and hasattr(self.engine, "_network"):
            for item in self.engine._network._queue:
                msg = item[2]
                in_flight_messages.append({
                    "message_id": msg.message_id,
                    "sender_id": msg.sender_id,
                    "receiver_id": msg.receiver_id,
                    "business_type": msg.business_type,
                    "deliver_time_us": msg.deliver_time_us,
                })

        # 提取全站环境物理量
        environment = {}
        if self.engine is not None and hasattr(self.engine, "_environment"):
            environment = self.engine._environment.snapshot()

        return {
            "environment": environment,
            "in_flight_messages": in_flight_messages,
            "run_id": self.run_id,
            "status": self.status,
            "time_us": self.current_time_us,
            "duration_us": self.duration_us,
            "pending_operations": len(self._pending_operations),
            "attack_count": len(self.attack_planner.attacks()),
            "recognition_count": len(self.recognition_engine.last_results()) if self.recognition_engine else 0,
            "defense_count": len(self.defense_engine.actions()) if self.defense_engine else 0,
            "observation_count": len(self.observation_gateway._events),
        }

    def _system_view(self) -> Dict[str, Any]:
        config = self.config
        devices = []
        if config is not None:
            for device in config.devices:
                devices.append({
                    "device_id": device.device_id,
                    "device_type": device.device_type,
                    "layer": device.layer,
                    "name": device.name,
                    "ports": device.ports,
                })
                # 提取在途报文流
        in_flight_messages = []
        if self.engine is not None and hasattr(self.engine, "_network"):
            for item in self.engine._network._queue:
                msg = item[2]
                in_flight_messages.append({
                    "message_id": msg.message_id,
                    "sender_id": msg.sender_id,
                    "receiver_id": msg.receiver_id,
                    "business_type": msg.business_type,
                    "deliver_time_us": msg.deliver_time_us,
                })

        # 提取全站环境物理量
        environment = {}
        if self.engine is not None and hasattr(self.engine, "_environment"):
            environment = self.engine._environment.snapshot()

        return {
            "environment": environment,
            "in_flight_messages": in_flight_messages,
            "run_id": self.run_id,
            "status": self.status,
            "time_us": self.current_time_us,
            "topology": {"devices": devices, "links": [asdict(link) for link in (config.links if config else [])]},
            "management": self.engine.get_management(sorted(self._device_ids)),
            "observations": self._observation_view().get("observations", {}),
            "recognition": [asdict(r) for r in (self.recognition_engine.last_results() if self.recognition_engine else [])],
            "defense": [asdict(a) for a in (self.defense_engine.actions() if self.defense_engine else [])],
        }

    def _timeline_view(self) -> Dict[str, Any]:
                # 提取在途报文流
        in_flight_messages = []
        if self.engine is not None and hasattr(self.engine, "_network"):
            for item in self.engine._network._queue:
                msg = item[2]
                in_flight_messages.append({
                    "message_id": msg.message_id,
                    "sender_id": msg.sender_id,
                    "receiver_id": msg.receiver_id,
                    "business_type": msg.business_type,
                    "deliver_time_us": msg.deliver_time_us,
                })

        # 提取全站环境物理量
        environment = {}
        if self.engine is not None and hasattr(self.engine, "_environment"):
            environment = self.engine._environment.snapshot()

        return {
            "environment": environment,
            "in_flight_messages": in_flight_messages,
            "run_id": self.run_id,
            "time_us": self.current_time_us,
            "attacks": self._attack_truth_records(),
            "attack_submissions": self._attack_submission_records(),
            "recognition": self._recognition_records(),
            "defense": self._defense_records(),
        }

    def _observation_view(self) -> Dict[str, Any]:
        window = self.observation_gateway.window(max(0, self.current_time_us - self._observation_window_size_us), self.current_time_us)
        return {
            "observations": {
                "events": [asdict(event) for event in window.events],
                "missing": window.missing,
                "late": window.late,
                "rejected": self.observation_gateway.rejected(),
            }
        }

    def _evaluation_view(self) -> Dict[str, Any]:
        if self.recorder is not None:
            return {"evaluation": None, "reason": "运行尚未结束，暂不评估"}
        run_dir = os.path.join(self.output_root, self.run_id)
        return {"evaluation": asdict(self.evaluator.evaluate(run_dir))}

    def _config_summary(self) -> Dict[str, Any]:
        config = self.config
        if config is None:
            return {}
        return {
            "scenario_id": config.scenario_id,
            "name": config.name,
            "dt_us": self.dt_us,
            "duration_us": self.duration_us,
            "device_count": len(config.devices),
            "link_count": len(config.links),
            "links": [{
                "link_id": link.link_id,
                "endpoint_a": list(link.endpoint_a),
                "endpoint_b": list(link.endpoint_b),
                "link_type": link.link_type,
                "parameters": dict(link.parameters),
            } for link in config.links],
            "attack_count": len(config.attacks),
            "recognition_enabled": self._recognition_enabled,
            "defense_enabled": self._defense_enabled,
            "observation_base_dir": self.observation_base_dir,
            "observation_window_size_us": self._observation_window_size_us,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _build_topology(config) -> Dict[str, Any]:
        devices = {}
        for device in config.devices:
            devices[device.device_id] = {
                "device_type": device.device_type,
                "layer": device.layer,
                "name": device.name,
                "ports": device.ports,
                "references": device.references,
            }
        links = []
        for link in config.links:
            links.append({
                "link_id": link.link_id,
                "endpoint_a": link.endpoint_a,
                "endpoint_b": link.endpoint_b,
                "link_type": link.link_type,
            })
        return {"devices": devices, "links": links}

    @staticmethod
    def _make_run_id(scenario_id: str) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"{scenario_id}-{stamp}-{uuid.uuid4().hex[:6]}"

    def _close_recorder(self) -> None:
        if self.recorder is not None:
            self.recorder.close()
            self.recorder = None
