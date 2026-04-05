from __future__ import annotations

import time

from common.config import SECRETS_PATH
from common.db import get_config, init_db, pop_control_action, upsert_worker_heartbeat
from common.security import remote_operations_block_reason
from common.telemetry import build_alert_item, build_update_status_item, enqueue_configured_event
from common.update_manager import check_for_updates


def main() -> None:
    init_db()
    print("[update] worker started")
    last_run = 0
    last_status = ""

    while True:
        now_ts = int(time.time())
        cfg = get_config()
        updates_cfg = cfg.get("updates", {}) if isinstance(cfg.get("updates"), dict) else {}
        security_cfg = cfg.get("security", {}) if isinstance(cfg.get("security"), dict) else {}

        block_reason = remote_operations_block_reason(
            enforce=bool(security_cfg.get("block_remote_when_default_local_credentials", True)),
            secret_store_exists=SECRETS_PATH.exists(),
        )

        if block_reason:
            upsert_worker_heartbeat("update", "blocked", {"reason": block_reason})
            time.sleep(5)
            continue

        if not updates_cfg.get("enabled", False):
            upsert_worker_heartbeat("update", "idle", {"enabled": False})
            time.sleep(5)
            continue

        force = pop_control_action("update_check_now") is not None
        interval_seconds = max(300, int(updates_cfg.get("poll_interval_seconds", 3600)))
        should_run = force or (now_ts - last_run) >= interval_seconds
        if not should_run:
            upsert_worker_heartbeat("update", "idle", {"next_check_in_seconds": interval_seconds - (now_ts - last_run)})
            time.sleep(5)
            continue

        state = check_for_updates(force=force)
        last_run = now_ts
        status = str(state.get("status") or "idle")
        upsert_worker_heartbeat(
            "update",
            status,
            {
                "current_version": state.get("current_version"),
                "target_version": state.get("target_version"),
                "last_error": state.get("last_error"),
            },
        )

        if status != last_status:
            enqueue_configured_event(
                cfg,
                data_class="update_status",
                occurred_ts=now_ts,
                items=[build_update_status_item(state)],
            )
            if status == "failed" and state.get("last_error"):
                enqueue_configured_event(
                    cfg,
                    data_class="alert_event",
                    occurred_ts=now_ts,
                    items=[build_alert_item("update", str(state.get("last_error")))],
                )
            last_status = status

        time.sleep(5)


if __name__ == "__main__":
    main()
