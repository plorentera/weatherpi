from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from common.config import resolve_api_bind_host
from common.db import get_config, init_db
from common.networking import detect_primary_lan_ip


ROOT = Path(__file__).resolve().parent.parent


def pick_free_port(host: str, start_port: int = 8000, tries: int = 20) -> int:
    for port in range(start_port, start_port + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port found in range {start_port}-{start_port + tries - 1} for host {host}")


def start_process(label: str, args: list[str], env: dict[str, str] | None = None) -> subprocess.Popen:
    print(f"[launcher] starting {label}...")
    return subprocess.Popen(args, cwd=ROOT, env=env)


def start_api_process(bind_host: str, api_port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["WEATHERPI_RUNTIME_BIND_HOST"] = bind_host
    env["WEATHERPI_RUNTIME_PORT"] = str(api_port)
    env["WEATHERPI_RUNTIME_MANAGER"] = "run_all"
    return start_process(
        "api",
        [sys.executable, "-m", "uvicorn", "api.main:app", "--host", bind_host, "--port", str(api_port)],
        env=env,
    )


def announce_api_urls(bind_host: str, api_port: int) -> None:
    print(f"[launcher] dashboard local at http://127.0.0.1:{api_port}")
    if bind_host != "127.0.0.1":
        lan_ip = detect_primary_lan_ip()
        if lan_ip:
            print(f"[launcher] dashboard LAN at http://{lan_ip}:{api_port}")
        else:
            print(f"[launcher] dashboard LAN enabled on port {api_port}, but LAN IP could not be detected automatically")


def terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    init_db()
    cfg = get_config()
    bind_host = resolve_api_bind_host(cfg)
    api_port = pick_free_port(bind_host, 8000, 20)
    if api_port != 8000:
        print(f"[launcher] WARNING: port 8000 busy on {bind_host}, using {api_port}")

    processes = {
        "api": start_api_process(bind_host, api_port),
        "collector": start_process("collector", [sys.executable, "-m", "collector.main"]),
        "delivery": start_process("delivery", [sys.executable, "-m", "collector.delivery_worker"]),
        "remote_config": start_process("remote_config", [sys.executable, "-m", "collector.remote_config_worker"]),
        "update": start_process("update", [sys.executable, "-m", "collector.update_worker"]),
        "backup": start_process("backup", [sys.executable, "-m", "collector.backup_worker"]),
    }

    current_bind_host = bind_host
    current_api_port = api_port
    announce_api_urls(current_bind_host, current_api_port)

    try:
        while True:
            desired_bind_host = resolve_api_bind_host(get_config())
            if desired_bind_host != current_bind_host:
                print(
                    f"[launcher] API bind host changed from {current_bind_host} to {desired_bind_host}; restarting api..."
                )
                terminate_process(processes["api"])
                previous_port = current_api_port
                current_api_port = pick_free_port(desired_bind_host, current_api_port, 20)
                if current_api_port != previous_port:
                    print(f"[launcher] API now using port {current_api_port}")
                processes["api"] = start_api_process(desired_bind_host, current_api_port)
                current_bind_host = desired_bind_host
                announce_api_urls(current_bind_host, current_api_port)

            for label, proc in processes.items():
                code = proc.poll()
                if code is not None:
                    print(f"[launcher] {label} exited with code {code}")
                    raise SystemExit(code)
            time.sleep(1)
    except KeyboardInterrupt:
        print("[launcher] stopping processes...")
    finally:
        for proc in processes.values():
            terminate_process(proc)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
