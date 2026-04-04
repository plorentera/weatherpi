from __future__ import annotations

import subprocess
import sys
import time
import socket
from pathlib import Path

from common.db import init_db


ROOT = Path(__file__).resolve().parent.parent


def pick_free_port(start_port: int = 8000, tries: int = 20) -> int:
    for port in range(start_port, start_port + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue

    raise RuntimeError(f"No free port found in range {start_port}-{start_port + tries - 1}")


def start_process(label: str, args: list[str]) -> subprocess.Popen:
    print(f"[launcher] starting {label}...")
    return subprocess.Popen(args, cwd=ROOT)


def terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    init_db()
    api_port = pick_free_port(8000, 20)
    if api_port != 8000:
        print(f"[launcher] WARNING: port 8000 busy, using {api_port}")

    processes = [
        ("api", start_process("api", [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", str(api_port)])),
        ("collector", start_process("collector", [sys.executable, "-m", "collector.main"])),
        ("outputs", start_process("outputs", [sys.executable, "-m", "collector.outputs_worker"])),
        ("backup", start_process("backup", [sys.executable, "-m", "collector.backup_worker"])),
    ]

    print(f"[launcher] dashboard at http://127.0.0.1:{api_port}")

    try:
        while True:
            for label, proc in processes:
                code = proc.poll()
                if code is not None:
                    print(f"[launcher] {label} exited with code {code}")
                    raise SystemExit(code)
            time.sleep(1)
    except KeyboardInterrupt:
        print("[launcher] stopping processes...")
    finally:
        for _, proc in processes:
            terminate_process(proc)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())