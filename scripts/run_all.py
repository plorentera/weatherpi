from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from common.db import init_db


ROOT = Path(__file__).resolve().parent.parent


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

    processes = [
        ("api", start_process("api", [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"])),
        ("collector", start_process("collector", [sys.executable, "-m", "collector.main"])),
        ("backup", start_process("backup", [sys.executable, "-m", "collector.backup_worker"])),
    ]

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