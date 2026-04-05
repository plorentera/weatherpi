from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from common.config import APP_VERSION, CURRENT_RELEASE_LINK, DATA_DIR, PREVIOUS_RELEASE_LINK, RELEASES_DIR
from common.db import fetch_worker_heartbeats, init_db
from common.update_manager import apply_staged_update, rollback_update


ROOT = Path(__file__).resolve().parent.parent


def _shared_env(data_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("WEATHERPI_DATA_DIR", str(data_dir.resolve()))
    return env


def _current_release_root() -> Path:
    if CURRENT_RELEASE_LINK.exists() and CURRENT_RELEASE_LINK.is_symlink():
        return CURRENT_RELEASE_LINK.resolve()
    return ROOT


def _workers_healthy(required_workers: list[str], stale_after_seconds: int = 120) -> bool:
    items = fetch_worker_heartbeats(stale_after_seconds=stale_after_seconds)
    by_name = {item["worker_name"]: item for item in items}
    for worker_name in required_workers:
        item = by_name.get(worker_name)
        if not item:
            return False
        if item.get("stale"):
            return False
        if item.get("status") not in {"ok", "idle"}:
            return False
    return True


def supervise(command: list[str], grace_seconds: int, data_dir: Path) -> int:
    init_db()
    env = _shared_env(data_dir)
    required_workers = ["collector", "delivery", "remote_config", "update"]

    def start_child() -> subprocess.Popen:
        cwd = _current_release_root()
        print(f"[release-launcher] starting command in {cwd}")
        return subprocess.Popen(command, cwd=cwd, env=env)

    child = start_child()
    started_ts = time.time()
    healthy = False

    while time.time() - started_ts < grace_seconds:
        if child.poll() is not None:
            break
        if _workers_healthy(required_workers):
            healthy = True
            break
        time.sleep(2)

    if healthy:
        print("[release-launcher] health gate passed")
        return child.wait()

    print("[release-launcher] health gate failed, attempting rollback")
    try:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
    except Exception:
        child.kill()

    try:
        state = rollback_update()
        print(json.dumps({"rollback": state}, indent=2, sort_keys=True))
    except Exception as exc:
        print(f"[release-launcher] rollback failed: {exc}", file=sys.stderr)
        return 1

    if not PREVIOUS_RELEASE_LINK.exists():
        return 1

    child = start_child()
    return child.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description="WeatherPi release launcher/supervisor")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    apply_parser = subparsers.add_parser("apply", help="Apply the staged update bundle")
    apply_parser.add_argument("--json", action="store_true", help="Print JSON output")

    rollback_parser = subparsers.add_parser("rollback", help="Rollback to the previous release")
    rollback_parser.add_argument("--json", action="store_true", help="Print JSON output")

    status_parser = subparsers.add_parser("status", help="Show release launcher status")
    status_parser.add_argument("--json", action="store_true", help="Print JSON output")

    supervise_parser = subparsers.add_parser("supervise", help="Start a command and rollback if health gate fails")
    supervise_parser.add_argument("--grace-seconds", type=int, default=120)
    supervise_parser.add_argument("--data-dir", default=str(DATA_DIR))
    supervise_parser.add_argument("runner", nargs=argparse.REMAINDER)

    args = parser.parse_args()

    try:
        if args.command_name == "apply":
            state = apply_staged_update()
            if args.json:
                print(json.dumps({"state": state}, indent=2, sort_keys=True))
            else:
                print(f"applied update target_version={state.get('target_version')} status={state.get('status')}")
            return 0

        if args.command_name == "rollback":
            state = rollback_update()
            if args.json:
                print(json.dumps({"state": state}, indent=2, sort_keys=True))
            else:
                print(f"rolled back current_version={state.get('current_version')} status={state.get('status')}")
            return 0

        if args.command_name == "status":
            payload = {
                "app_version": APP_VERSION,
                "releases_dir": str(RELEASES_DIR),
                "current": str(CURRENT_RELEASE_LINK.resolve()) if CURRENT_RELEASE_LINK.exists() else "",
                "previous": str(PREVIOUS_RELEASE_LINK.resolve()) if PREVIOUS_RELEASE_LINK.exists() else "",
                "workers": fetch_worker_heartbeats(),
            }
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(json.dumps(payload, indent=2, sort_keys=True))
            return 0

        if args.command_name == "supervise":
            runner = args.runner or [sys.executable, "-m", "scripts.run_all"]
            if runner and runner[0] == "--":
                runner = runner[1:]
            return supervise(runner, max(30, int(args.grace_seconds)), Path(args.data_dir))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
