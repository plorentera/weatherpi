from __future__ import annotations

import time

from collector.sensors.mock import MockSensorDriver
from common.db import delete_measurements_older_than, get_config, init_db, insert_measurement, upsert_worker_heartbeat
from common.telemetry import build_alert_item, build_station_status_item, enqueue_configured_event


def main() -> None:
    init_db()
    driver = MockSensorDriver()

    cfg = get_config()
    interval = int(cfg.get("sample_interval_seconds", 5))
    current_rev = int(cfg.get("_rev", 0))
    was_paused = False
    retention_seconds = 7 * 24 * 3600
    cleanup_every_seconds = 3600
    last_cleanup = 0
    last_status_emit = 0
    last_alert_emit = 0

    print(f"[collector] started. interval={interval}s")
    upsert_worker_heartbeat("collector", "starting", {"interval_seconds": interval})

    while True:
        now_ts = int(time.time())
        cfg = get_config()
        collector_cfg = cfg.get("collector", {}) if isinstance(cfg.get("collector"), dict) else {}
        collector_enabled = bool(collector_cfg.get("enabled", True))

        new_rev = int(cfg.get("_rev", current_rev))
        new_interval = int(cfg.get("sample_interval_seconds", interval))
        if new_interval != interval:
            interval = new_interval
            print(f"[collector] interval updated to {interval}s")
        current_rev = new_rev

        if not collector_enabled:
            if not was_paused:
                print("[collector] paused by config")
                enqueue_configured_event(
                    cfg,
                    data_class="station_status",
                    occurred_ts=now_ts,
                    items=[build_station_status_item("collector", "paused", {"config_rev": current_rev})],
                )
                was_paused = True
            upsert_worker_heartbeat("collector", "paused", {"config_rev": current_rev, "interval_seconds": interval})
            time.sleep(5)
            continue

        if was_paused:
            print("[collector] resumed by config")
            enqueue_configured_event(
                cfg,
                data_class="station_status",
                occurred_ts=now_ts,
                items=[build_station_status_item("collector", "resumed", {"config_rev": current_rev})],
            )
            was_paused = False

        if now_ts - last_cleanup >= cleanup_every_seconds:
            cutoff = now_ts - retention_seconds
            deleted = delete_measurements_older_than(cutoff)
            if deleted:
                print(f"[collector] retention cleanup deleted={deleted}")
            last_cleanup = now_ts

        try:
            metrics = driver.read()
            insert_measurement(now_ts, metrics)
            enqueue_configured_event(
                cfg,
                data_class="weather_measurement",
                occurred_ts=now_ts,
                items=[
                    {
                        "ts": now_ts,
                        "metrics": metrics,
                    }
                ],
            )

            status_interval = int(collector_cfg.get("status_emit_interval_seconds", 60))
            if now_ts - last_status_emit >= status_interval:
                enqueue_configured_event(
                    cfg,
                    data_class="station_status",
                    occurred_ts=now_ts,
                    items=[
                        build_station_status_item(
                            "collector",
                            "ok",
                            {
                                "config_rev": current_rev,
                                "interval_seconds": interval,
                            },
                        )
                    ],
                )
                last_status_emit = now_ts

            upsert_worker_heartbeat(
                "collector",
                "ok",
                {
                    "config_rev": current_rev,
                    "interval_seconds": interval,
                    "last_measurement_ts": now_ts,
                },
            )
            print(f"[collector] ts={now_ts} metrics={metrics}")
        except Exception as exc:
            upsert_worker_heartbeat("collector", "error", {"error": str(exc), "config_rev": current_rev})
            print(f"[collector] ERROR: {exc}")
            if now_ts - last_alert_emit >= 60:
                enqueue_configured_event(
                    cfg,
                    data_class="alert_event",
                    occurred_ts=now_ts,
                    items=[build_alert_item("collector", str(exc), context={"config_rev": current_rev})],
                )
                last_alert_emit = now_ts

        slept = 0
        while slept < interval:
            time.sleep(1)
            slept += 1
            cfg_check = get_config()
            if int(cfg_check.get("_rev", 0)) != current_rev:
                print("[collector] config changed -> applying immediately")
                break


if __name__ == "__main__":
    main()
