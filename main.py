"""命令行入口：无界面运行一个场景，或启动演示平面 UI。"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sim2030.application import Application


def parse_args():
    parser = argparse.ArgumentParser(description="运行 2030 仿真场景")
    parser.add_argument("--scenario", default=os.path.join("scenarios", "substation-live.json"),
                        help="场景 JSON 路径")
    parser.add_argument("--output", default="runs", help="运行记录输出目录")
    parser.add_argument("--ui", action="store_true", help="启动演示平面 HTTP 服务")
    parser.add_argument("--host", default="127.0.0.1", help="UI 监听地址")
    parser.add_argument("--port", type=int, default=8000, help="UI 监听端口")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.ui:
        from sim2030.presentation.server import PresentationServer
        server = PresentationServer(scenario_dir=os.path.dirname(os.path.abspath(args.scenario)),
                                    output_root=args.output)
        print(f"演示平面已启动：http://{args.host}:{args.port}")
        server.serve(host=args.host, port=args.port)
        return 0

    app = Application(output_root=args.output)
    run_id = app.load(args.scenario)
    print(f"run_id={run_id} scenario={args.scenario} status={app.status}")
    app.run_to_end()
    summary = app.get_view("status")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
