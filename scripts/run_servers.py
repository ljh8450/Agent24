from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the E2P product and raw-event monitor against one store.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--app-port", type=int, default=8000)
    parser.add_argument("--monitor-port", type=int, default=8001)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    environment = os.environ.copy()
    environment["PERSONA_RESTORER_ROOT"] = str(args.root.resolve())
    commands = [
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.asgi:app",
            "--host",
            args.host,
            "--port",
            str(args.app_port),
        ],
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.monitor:app",
            "--host",
            args.host,
            "--port",
            str(args.monitor_port),
        ],
    ]
    processes = [subprocess.Popen(command, env=environment, cwd=Path(__file__).resolve().parents[1]) for command in commands]
    print(f"E2P app:       http://{args.host}:{args.app_port}")
    print(f"Event monitor: http://{args.host}:{args.monitor_port}")
    print(f"Synced store:  {environment['PERSONA_RESTORER_ROOT']}")
    try:
        while all(process.poll() is None for process in processes):
            time.sleep(0.2)
        return next((process.returncode for process in processes if process.returncode), 0)
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
