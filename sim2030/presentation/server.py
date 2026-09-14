"""演示平面 HTTP 服务。

``PresentationServer`` 把前端静态页面、运行接口与回放接口合并到一个标准库
HTTP 服务里，不依赖第三方包。所有接口都通过 ``handle_request(method, path, body)``
返回统一响应结构，便于直接用单元测试断言；``serve`` 仅负责绑定端口并轮询。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from sim2030.application import Application
from sim2030.base.observations import EvidenceReader
from sim2030.evaluation import Evaluator
from sim2030.presentation import views as presentation_views
from sim2030.records import RunReader
from sim2030.scenario import load_scenario

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_JSON_HEADERS = {"Content-Type": "application/json; charset=utf-8"}


class PresentationServer:
    """无阻塞地组织页面接口；应用实例同时只承载一个运行。"""

    def __init__(self, scenario_dir: str = "scenarios", output_root: str = "runs") -> None:
        self.scenario_dir = os.path.abspath(scenario_dir)
        self.output_root = os.path.abspath(output_root)
        self.application = Application(output_root=self.output_root)
        self._scenario_paths: Dict[str, str] = {}
        self._seen_request_ids: Dict[str, Dict[str, Any]] = {}
        self._refresh_scenario_paths()

    def close(self) -> None:
        """关闭内部应用持有的文件句柄，便于目录清理与进程退出。"""
        self.application.close()

    # ──────────────────────────────────────────────
    # 场景与运行注册
    # ──────────────────────────────────────────────
    def _refresh_scenario_paths(self) -> None:
        paths: Dict[str, str] = {}
        if os.path.isdir(self.scenario_dir):
            for name in sorted(os.listdir(self.scenario_dir)):
                if not name.lower().endswith(".json"):
                    continue
                full = os.path.join(self.scenario_dir, name)
                if os.path.isfile(full):
                    scenario_id = self._read_scenario_id(full)
                    if scenario_id:
                        paths[scenario_id] = full
        self._scenario_paths = paths

    @staticmethod
    def _read_scenario_id(path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict):
                return str(raw.get("scenario_id", ""))
        except (OSError, ValueError, json.JSONDecodeError):
            return ""
        return ""

    def _scenario_path_for(self, scenario_id: str) -> Optional[str]:
        if scenario_id in self._scenario_paths:
            return self._scenario_paths[scenario_id]
        candidate = scenario_id
        if candidate.endswith(".json") and os.path.isfile(candidate):
            return candidate
        for path in self._scenario_paths.values():
            if os.path.basename(path) == scenario_id:
                return path
        return None

    # ──────────────────────────────────────────────
    # 路由分发
    # ──────────────────────────────────────────────
    def handle_request(self, method: str, path: str, body: Optional[bytes] = None) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        """返回 ``(状态码, 响应字典, 响应头)``。

        状态码用于 HTTP 层；响应字典始终包含 ``status``/``error`` 中的一种，便于
        前端和测试直接判断结果。
        """
        method = (method or "GET").upper()
        target = urlsplit(path or "/")
        route = target.path.rstrip("/") or "/"
        query = parse_qs(target.query, keep_blank_values=True)

        try:
            if route == "/" or route == "/index.html":
                return self._static("index.html")
            if route in ("/app.js", "/static/app.js"):
                return self._static("app.js")
            if route in ("/style.css", "/static/style.css"):
                return self._static("style.css")

            if route == "/api/scenarios" and method == "GET":
                return self._list_scenarios()
            if route == "/api/runs" and method == "POST":
                return self._create_run(self._parse_json(body))
            if route.startswith("/api/runs/") and route.endswith("/operations") and method == "POST":
                run_id = route.split("/")[3]
                return self._submit_operation(run_id, self._parse_json(body))
            if route.startswith("/api/runs/") and route.endswith("/parameters") and method == "POST":
                parts = route.split("/")
                if len(parts) == 7 and parts[4] == "devices":
                    return self._set_device_parameter(parts[3], parts[5], self._parse_json(body))
            if route.startswith("/api/runs/") and route.endswith("/controls") and method == "GET":
                parts = route.split("/")
                if len(parts) == 7 and parts[4] == "devices":
                    return self._get_device_controls(parts[3], parts[5])
            if route.startswith("/api/runs/") and route.endswith("/views") and method == "GET":
                run_id = route.split("/")[3]
                return self._views(run_id, query)
            if route.startswith("/api/runs/") and route.endswith("/evidence") and method == "GET":
                run_id = route.split("/")[3]
                return self._evidence(run_id, query)

            return 404, {"error": f"未找到接口 {method} {route}"}, _JSON_HEADERS
        except Exception as exc:  # 演示平面不因单次请求异常拖垮服务
            return 500, {"error": f"处理请求失败：{exc}"}, _JSON_HEADERS

    # ──────────────────────────────────────────────
    # 静态资源
    # ──────────────────────────────────────────────
    def _static(self, name: str) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        path = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(path):
            return 404, {"error": f"缺少静态资源 {name}"}, _JSON_HEADERS
        content_type = "text/html; charset=utf-8" if name.endswith(".html") else (
            "application/javascript; charset=utf-8" if name.endswith(".js") else "text/css; charset=utf-8"
        )
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        return 200, {"__raw__": text}, {"Content-Type": content_type, "Cache-Control": "no-store"}

    @staticmethod
    def _parse_json(body: Optional[bytes]) -> Dict[str, Any]:
        if not body:
            return {}
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            raise ValueError("请求体不是合法 JSON")
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return payload

    # ──────────────────────────────────────────────
    # 接口实现
    # ──────────────────────────────────────────────
    def _list_scenarios(self) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        self._refresh_scenario_paths()
        scenarios: List[Dict[str, Any]] = []
        for scenario_id, path in sorted(self._scenario_paths.items()):
            if scenario_id == "substation-attack-demo":
                continue
            try:
                config = load_scenario(path)
                scenarios.append({
                    "scenario_id": config.scenario_id,
                    "name": config.name,
                    "path": path,
                    "observation_mode": "file_replay",
                    "recognition_enabled": bool((config.recognition or {}).get("enabled")),
                    "defense_enabled": bool((config.defense or {}).get("enabled")),
                    "device_count": len(config.devices),
                    "attack_count": len(config.attacks),
                })
            except Exception:
                continue
        return 200, {"scenarios": scenarios}, _JSON_HEADERS

    def _create_run(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        scenario_id = body.get("scenario_id") or body.get("scenario") or body.get("path")
        if not scenario_id:
            return 400, {"error": "缺少 scenario_id"}, _JSON_HEADERS
        path = self._scenario_path_for(str(scenario_id))
        if path is None:
            return 404, {"error": f"未找到场景 {scenario_id}"}, _JSON_HEADERS
        run_id = self.application.load(path)
        self._seen_request_ids.clear()
        return 201, {
            "run_id": run_id,
            "scenario_id": self.application.config.scenario_id if self.application.config else scenario_id,
            "status": self.application.status,
            "mode": body.get("mode", "live"),
        }, _JSON_HEADERS

    def _submit_operation(self, run_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        if not self.application.run_id:
            return 404, {"error": "当前没有活动运行"}, _JSON_HEADERS
        if self.application.run_id != run_id:
            return 404, {"error": f"运行 {run_id} 不存在或已结束"}, _JSON_HEADERS

        request_id = body.get("request_id") or "req-" + str(len(self._seen_request_ids) + 1)
        body["request_id"] = request_id
        if request_id in self._seen_request_ids:
            previous = self._seen_request_ids[request_id]
            return 200, {"request_id": request_id, "status": "duplicate", "previous": previous}, _JSON_HEADERS

        receipt = self.application.submit_operation(body)
        self._seen_request_ids[request_id] = receipt
        return 200, receipt, _JSON_HEADERS

    def _get_device_controls(self, run_id: str, device_id: str) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        if not self.application.run_id:
            return 404, {"error": "当前没有活动运行"}, _JSON_HEADERS
        if self.application.run_id != run_id:
            return 404, {"error": f"运行 {run_id} 不存在或已结束"}, _JSON_HEADERS
        result = self.application.get_device_controls(device_id)
        if result.get("status") == "not_found":
            return 404, {"error": result.get("reason", "目标不存在")}, _JSON_HEADERS
        return 200, result, _JSON_HEADERS

    def _set_device_parameter(self, run_id: str, device_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        if not self.application.run_id:
            return 404, {"error": "当前没有活动运行"}, _JSON_HEADERS
        if self.application.run_id != run_id:
            return 404, {"error": f"运行 {run_id} 不存在或已结束"}, _JSON_HEADERS
        key = body.get("key") or body.get("parameter")
        if not key:
            return 400, {"error": "缺少参数名 key"}, _JSON_HEADERS
        if "value" not in body:
            return 400, {"error": "缺少参数值 value"}, _JSON_HEADERS
        result = self.application.set_device_parameter(device_id, key, body["value"])
        if result.get("status") == "not_found":
            return 404, {"error": result.get("reason", "目标不存在")}, _JSON_HEADERS
        return 200, result, _JSON_HEADERS

    def _views(self, run_id: str, query: Dict[str, List[str]]) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        view_name = self._first(query, "view", "system")

        # 快照视图必须真正按 time_us 读取历史记录，而不是返回当前状态。
        if view_name == "snapshot":
            time_us = int(self._first(query, "time_us", 0) or 0)
            if self.application.run_id == run_id:
                replayed = self.application.replay(run_id, time_us)
                data = {
                    "time_us": replayed.get("time_us", time_us),
                    "snapshots": replayed.get("device_snapshots", {}),
                }
                return 200, {"run_id": run_id, "view": view_name, "data": data}, _JSON_HEADERS
            run_dir = os.path.join(self.output_root, run_id)
            if not os.path.isdir(run_dir):
                return 404, {"error": f"运行 {run_id} 不存在"}, _JSON_HEADERS
            return 200, {"run_id": run_id, "view": view_name,
                         "data": self._replay_view(view_name, run_dir, query)}, _JSON_HEADERS

        if self.application.run_id == run_id:
            options: Dict[str, Any] = {}
            if "target_ids" in query:
                options["target_ids"] = query["target_ids"]
            data = self.application.get_view(view_name, options)
            if view_name == "evaluation":
                data = self._evaluation_payload(data)
            return 200, {"run_id": run_id, "view": view_name, "data": data}, _JSON_HEADERS

        run_dir = os.path.join(self.output_root, run_id)
        if not os.path.isdir(run_dir):
            return 404, {"error": f"运行 {run_id} 不存在"}, _JSON_HEADERS
        return 200, {"run_id": run_id, "view": view_name, "data": self._replay_view(view_name, run_dir, query)}, _JSON_HEADERS

    def _replay_view(self, view_name: str, run_dir: str, query: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        reader = RunReader(run_dir)
        manifest = reader.manifest()
        if view_name == "status":
            snapshot = reader.latest("truth")
            return {"run_id": manifest.get("run_id"), "scenario_id": manifest.get("scenario_id"),
                    "time_us": snapshot.get("time_us", 0) if snapshot else 0, "finished": True}
        if view_name == "system":
            return presentation_views.build_system_view(run_dir)
        if view_name == "timeline":
            return presentation_views.build_timeline(run_dir)
        if view_name == "observations":
            return {"observations": {"events": reader.evidence()}}
        if view_name == "recognition":
            return {"recognition": reader.read("recognition")}
        if view_name == "defense":
            return {"defense": reader.read("defense")}
        if view_name == "evaluation":
            return self._evaluation_payload({"evaluation": asdict(Evaluator().evaluate(run_dir))})
        if view_name == "snapshot":
            time_us = int(self._first(query or {}, "time_us", 0) or 0)
            return {"snapshots": reader.snapshot_at(time_us)}
        return {"error": f"未知视图 {view_name}"}

    @staticmethod
    def _evaluation_payload(data: Dict[str, Any]) -> Dict[str, Any]:
        """统一评估页协议：前端只依赖 comparison/runs/reason。"""
        evaluation = data.get("evaluation")
        if evaluation is None:
            return {"comparison": None, "runs": [], "reason": data.get("reason", "暂无评估结果")}
        return presentation_views.build_comparison([evaluation])

    def _evidence(self, run_id: str, query: Dict[str, List[str]]) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        run_dir = os.path.join(self.output_root, run_id)
        if not os.path.isdir(run_dir):
            return 404, {"error": f"运行 {run_id} 不存在"}, _JSON_HEADERS
        provider_id = self._first(query, "provider_id", None)
        event_id = self._first(query, "event_id", None)
        start = int(self._first(query, "start", 0) or 0)
        count = int(self._first(query, "count", 10) or 10)

        reader = RunReader(run_dir)
        manifest = reader.manifest()
        base_dir = manifest.get("observation_base_dir") or run_dir
        evidence_reader = EvidenceReader(base_dir)
        events = reader.evidence(provider_id=provider_id, event_id=event_id)
        segments: List[Dict[str, Any]] = []
        for event in events:
            raw_event = event.get("raw") or event
            segment = evidence_reader.read_segment(raw_event, start=start, count=count)
            segments.append({
                "provider_id": event.get("provider_id"),
                "event_id": event.get("event_id"),
                "source_type": event.get("source_type"),
                "raw_data_uri": raw_event.get("raw_data_uri"),
                "raw_data_format": raw_event.get("raw_data_format"),
                "segment": segment,
            })
        return 200, {"run_id": run_id, "evidence": segments}, _JSON_HEADERS

    @staticmethod
    def _first(query: Dict[str, List[str]], key: str, default: Any) -> Any:
        values = query.get(key)
        if not values:
            return default
        return values[0]

    # ──────────────────────────────────────────────
    # HTTP 服务入口
    # ──────────────────────────────────────────────
    def serve(self, application: Optional[Application] = None, host: str = "127.0.0.1", port: int = 8000) -> None:
        """启动阻塞式 HTTP 服务；``application`` 可选，缺省沿用内部应用。"""
        if application is not None:
            self.application = application
        self._refresh_scenario_paths()

        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self._dispatch()

            def do_POST(self):
                self._dispatch()

            def _dispatch(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(length) if length else None
                status, payload, headers = server.handle_request(self.command, self.path, body)
                raw = payload.get("__raw__")
                if raw is not None:
                    data = raw.encode("utf-8")
                else:
                    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt, *args):
                pass

        httpd = ThreadingHTTPServer((host, port), Handler)
        try:
            httpd.serve_forever()
        finally:
            httpd.server_close()


__all__ = ["PresentationServer"]
