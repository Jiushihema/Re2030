"""独立入口：运行时添加标签页/资源路由，原 UI 源文件保持原样。"""
from pathlib import Path
from urllib.parse import urlsplit

from sim2030.presentation.server import PresentationServer
from acoustic_demo.application import AcousticApplication, SCENARIO


class AcousticServer(PresentationServer):
    def __init__(self, scenario_dir="scenarios", output_root="runs"):
        super().__init__(scenario_dir, output_root)
        self.application = AcousticApplication(self.output_root)

    def _refresh_scenario_paths(self):
        # 专用入口只展示声波场景；公共服务仍按原有逻辑列出其他课题场景。
        self._scenario_paths = {"substation-acoustic-demo": str(SCENARIO)}

    def _static(self, name):
        status, data, headers = super()._static(name)
        if name == "index.html" and status == 200:
            html = data["__raw__"].replace('</head>', '<link rel="stylesheet" href="/acoustic.css"></head>')
            html = html.replace('<div class="tabs">', '<div class="tabs"><button class="tab" data-view="acoustic">声波联动</button>', 1)
            html = html.replace('</body>', '<script src="/acoustic.js"></script></body>')
            data = {"__raw__": html}
        return status, data, headers

    def handle_request(self, method, path, body=None):
        route = urlsplit(path).path
        if method.upper() == "GET" and route in {"/acoustic.js", "/acoustic.css"}:
            file = Path(__file__).with_name("static") / route[1:]
            mime = "application/javascript" if route.endswith(".js") else "text/css"
            return 200, {"__raw__": file.read_text(encoding="utf-8")}, {
                "Content-Type": mime + "; charset=utf-8", "Cache-Control": "no-store"}
        if method.upper() == "POST" and route == "/api/acoustic/advance":
            headers = {"Content-Type": "application/json; charset=utf-8"}
            try:
                request = self._parse_json(body)
                seconds = float(request.get("seconds", 1))
                if not 0 < seconds <= 60:
                    raise ValueError("每次推进秒数须在 0 到 60 之间")
                app = self.application
                with app._lock:
                    if app.acoustic is None or app.run_id != request.get("run_id"):
                        return 409, {"error": "请先创建本次声波场景运行"}, headers
                    if app.status == "finished":
                        return 409, {"error": "运行已结束，请新建运行"}, headers
                    previous = app.status
                    app.start()
                    for _ in range(round(seconds * 1e6 / app.dt_us)):
                        app.step()
                    if previous != "running":
                        app.pause()
                    app.recorder.flush()
                    return 200, {"data": app.get_view("acoustic")}, headers
            except (TypeError, ValueError) as exc:
                return 400, {"error": str(exc)}, headers
        return super().handle_request(method, path, body)
