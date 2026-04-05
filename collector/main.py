from __future__ import annotations

import time

from collector.sensors.mock import MockSensorDriver
from common.alerts import evaluate_alert_rules, normalize_alerts_config, quality_flags
from common.db import (
    alert_rule_state_map,
    delete_measurements_older_than,
    fetch_latest,
    get_alert_config_state,
    get_config,
    init_db,
    insert_measurement,
    oldest_unsent_outbox_age_seconds,
    upsert_alert_rule_state,
    upsert_worker_heartbeat,
)
from common.telemetry import build_station_status_item, enqueue_configured_event


def _measurement_values(metrics: dict, *, now_ts: int, last_success_ts: int | None, consecutive_errors: int) -> dict:
    values = {
        "temperature_c": metrics.get("temp_c"),
        "temp_c": metrics.get("temp_c"),
        "humidity_pct": metrics.get("humidity_pct"),
        "pressure_hpa": metrics.get("pressure_hpa"),
        "sensor_last_seen_seconds": 0 if last_success_ts is None else max(0, now_ts - int(last_success_ts)),
        "collector_error_count": max(0, int(consecutive_errors)),
        "telemetry_backlog_seconds": oldest_unsent_outbox_age_seconds(now_ts),
    }
    values.update(quality_flags(values))
    return values


def _evaluate_and_enqueue_alerts(cfg: dict, *, now_ts: int, values: dict) -> int:
    alerts_cfg = normalize_alerts_config((cfg or {}).get("alerts"))
    if not alerts_cfg.get("rules"):
        return 0

    config_source = str(get_alert_config_state().get("source") or "local")
    existing_states = alert_rule_state_map()
    events, next_states = evaluate_alert_rules(
        alerts_cfg,
        values,
        existing_states,
        now_ts=now_ts,
        config_source=config_source,
    )
    for rule_id, state in next_states.items():
        upsert_alert_rule_state(rule_id, state)
    if not alerts_cfg.get("enabled", False) or not events:
        return 0
    return enqueue_configured_event(
        cfg,
        data_class="alert_event",
        occurred_ts=now_ts,
        items=events,
        extra={"engine": "local_alerts"},
    )


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
    consecutive_errors = 0
    latest = fetch_latest()
    last_success_ts = int(latest["ts"]) if latest and latest.get("ts") else None
    last_good_metrics = {
        "temp_c": latest.get("temp_c"),
        "humidity_pct": latest.get("humidity_pct"),
        "pressure_hpa": latest.get("pressure_hpa"),
    } if latest else {}

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
            consecutive_errors = 0
            last_success_ts = now_ts
            last_good_metrics = dict(metrics)
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
            _evaluate_and_enqueue_alerts(
                cfg,
                now_ts=now_ts,
                values=_measurement_values(
                    metrics,
                    now_ts=now_ts,
                    last_success_ts=last_success_ts,
                    consecutive_errors=consecutive_errors,
                ),
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
            consecutive_errors += 1
            upsert_worker_heartbeat("collector", "error", {"error": str(exc), "config_rev": current_rev})
            print(f"[collector] ERROR: {exc}")
            _evaluate_and_enqueue_alerts(
                cfg,
                now_ts=now_ts,
                values=_measurement_values(
                    last_good_metrics,
                    now_ts=now_ts,
                    last_success_ts=last_success_ts,
                    consecutive_errors=consecutive_errors,
                ),
            )

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
