"""python run_acoustic.py --port 8768；无需第三方 Python 包。"""
import argparse
from pathlib import Path

from acoustic_demo.server import AcousticServer


def main():
    parser = argparse.ArgumentParser(description="声波联动仿真")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    server = AcousticServer(str(root / "scenarios"), str(root / "runs"))
    print(f"声波联动仿真：http://{args.host}:{args.port}", flush=True)
    try:
        server.serve(application=server.application, host=args.host, port=args.port)
    finally:
        server.close()


if __name__ == "__main__":
    main()
