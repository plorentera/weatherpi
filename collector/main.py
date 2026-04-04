import time

from collector.sensors.mock import MockSensorDriver
from common.db import (
    delete_measurements_older_than,
    enqueue_outbox,
    get_config,
    init_db,
    insert_measurement,
)


def main() -> None:
    init_db()
    driver = MockSensorDriver()

    cfg = get_config()
    interval = int(cfg.get("sample_interval_seconds", 5))
    current_rev = int(cfg.get("_rev", 0))
    was_paused = False

    print(f"[collector] started. interval={interval}s")

    retention_seconds = 7 * 24 * 3600
    cleanup_every_seconds = 3600
    last_cleanup = 0
    last_webhook_warn_ts = 0

    while True:
        now_ts = int(time.time())
        cfg = get_config()
        collector_cfg = cfg.get("collector", {}) if isinstance(cfg.get("collector", {}), dict) else {}
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
                was_paused = True
            time.sleep(5)
            continue

        if was_paused:
            print("[collector] resumed by config")
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

            outputs = cfg.get("outputs", {})
            payload = {
                "ts": now_ts,
                "station_id": cfg.get("station_id", "meteo-001"),
                "metrics": metrics,
            }

            wh = outputs.get("webhook", {})
            wh_url = str(wh.get("url", "")).strip()
            if wh.get("enabled") and wh_url.startswith(("http://", "https://")):
                enqueue_outbox("webhook", payload, now_ts)
            elif wh.get("enabled") and wh_url:
                if now_ts - last_webhook_warn_ts >= 60:
                    print("[collector] WARN: webhook enabled but URL must start with http:// or https://")
                    last_webhook_warn_ts = now_ts

            mq = outputs.get("mqtt", {})
            if mq.get("enabled") and mq.get("host") and mq.get("topic"):
                enqueue_outbox("mqtt", payload, now_ts)

            print(f"[collector] ts={now_ts} metrics={metrics}")

        except Exception as e:
            print(f"[collector] ERROR: {e}")

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
