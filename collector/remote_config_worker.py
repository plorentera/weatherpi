from __future__ import annotations

import time

from common.config import SECRETS_PATH
from common.db import get_config, init_db, pop_control_action, upsert_worker_heartbeat
from common.remote_config_manager import run_remote_config_check
from common.security import remote_operations_block_reason
from common.telemetry import build_alert_item, build_station_status_item, enqueue_configured_event


def main() -> None:
    init_db()
    print("[remote-config] worker started")
    last_run = 0
    last_state = ""

    while True:
        now_ts = int(time.time())
        cfg = get_config()
        remote_cfg = cfg.get("remote_config", {}) if isinstance(cfg.get("remote_config"), dict) else {}
        security_cfg = cfg.get("security", {}) if isinstance(cfg.get("security"), dict) else {}

        block_reason = remote_operations_block_reason(
            enforce=bool(security_cfg.get("block_remote_when_default_local_credentials", True)),
            secret_store_exists=SECRETS_PATH.exists(),
        )

        if block_reason:
            upsert_worker_heartbeat("remote_config", "blocked", {"reason": block_reason})
            time.sleep(5)
            continue

        if not remote_cfg.get("enabled", False):
            upsert_worker_heartbeat("remote_config", "idle", {"enabled": False})
            time.sleep(5)
            continue

        force = pop_control_action("remote_config_check_now") is not None
        interval_seconds = max(60, int(remote_cfg.get("poll_interval_seconds", 900)))
        should_run = force or (now_ts - last_run) >= interval_seconds
        if not should_run:
            upsert_worker_heartbeat("remote_config", "idle", {"next_check_in_seconds": interval_seconds - (now_ts - last_run)})
            time.sleep(5)
            continue

        state = run_remote_config_check(force=force)
        last_run = now_ts
        status = str(state.get("status") or "idle")
        upsert_worker_heartbeat(
            "remote_config",
            status,
            {
                "current_revision": state.get("current_revision"),
                "staged_revision": state.get("staged_revision"),
                "last_error": state.get("last_error"),
            },
        )

        if status != last_state:
            enqueue_configured_event(
                cfg,
                data_class="station_status",
                occurred_ts=now_ts,
                items=[
                    build_station_status_item(
                        "remote_config",
                        status,
                        {
                            "current_revision": state.get("current_revision"),
                            "staged_revision": state.get("staged_revision"),
                        },
                    )
                ],
            )
            if status == "failed" and state.get("last_error"):
                enqueue_configured_event(
                    cfg,
                    data_class="alert_event",
                    occurred_ts=now_ts,
                    items=[build_alert_item("remote_config", str(state.get("last_error")))],
                )
            last_state = status

        time.sleep(5)


if __name__ == "__main__":
    main()
