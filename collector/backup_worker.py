import os
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from common.db import (
    init_db,
    get_config,
    get_connection,
    get_setting,
    set_setting,
    insert_export,
    list_exports,
    delete_exports_older_than,
)

EXPORTS_DIR = Path(__file__).resolve().parent.parent / "data" / "exports"


def parse_hhmm(hhmm: str) -> tuple[int, int]:
    """
    HH:MM -> (HH, MM). Fallback 00:00 si inválido.
    """
    try:
        parts = hhmm.strip().split(":")
        h = int(parts[0])
        m = int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass
    return 0, 0


def day_bounds_utc(date_obj_utc: datetime) -> tuple[int, int]:
    """
    Día calendario completo en UTC:
    00:00:00 -> 23:59:59 UTC
    """
    start = datetime(date_obj_utc.year, date_obj_utc.month, date_obj_utc.day, 0, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return int(start.timestamp()), int(end.timestamp())


def export_csv(period_from_ts: int, period_to_ts: int) -> str:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    created_ts = int(time.time())
    filename = f"meteo_{period_from_ts}_{period_to_ts}.csv"
    path = EXPORTS_DIR / filename

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT ts, temp_c, humidity_pct, pressure_hpa
        FROM measurements
        WHERE ts BETWEEN ? AND ?
        ORDER BY ts ASC
        """,
        (period_from_ts, period_to_ts),
    )
    rows = cur.fetchall()
    conn.close()

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("ts,temp_c,humidity_pct,pressure_hpa\n")
        for r in rows:
            ts = r["ts"]
            t = r["temp_c"] if r["temp_c"] is not None else ""
            h = r["humidity_pct"] if r["humidity_pct"] is not None else ""
            p = r["pressure_hpa"] if r["pressure_hpa"] is not None else ""
            f.write(f"{ts},{t},{h},{p}\n")

    insert_export(created_ts, period_from_ts, period_to_ts, filename, str(path))
    return str(path)


def already_exported(period_from_ts: int, period_to_ts: int) -> bool:
    items = list_exports(limit=5000)
    return any(
        e["period_from_ts"] == period_from_ts and e["period_to_ts"] == period_to_ts
        for e in items
    )


def resolve_schedule_utc(now_utc: datetime, cfg: dict) -> datetime:
    ex = cfg.get("exports", {})
    sch = ex.get("schedule", {}) if isinstance(ex.get("schedule", {}), dict) else {}

    ui_timezone = sch.get("ui_timezone") or cfg.get("ui", {}).get("timezone") or "UTC"
    local_hhmm = sch.get("time_local")

    if local_hhmm:
        hh, mm = parse_hhmm(local_hhmm)
        try:
            tz = ZoneInfo(ui_timezone)
        except Exception:
            tz = timezone.utc

        now_local = now_utc.astimezone(tz)
        scheduled_local = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return scheduled_local.astimezone(timezone.utc)

    hhmm_utc = sch.get("time_utc") or "00:00"
    hh, mm = parse_hhmm(hhmm_utc)
    return now_utc.replace(hour=hh, minute=mm, second=0, microsecond=0)


def should_run_now_utc(now_utc: datetime, cfg: dict) -> bool:
    """
    Decide si toca ejecutar SEGÚN:
      - hora programada en UTC (exports.schedule.time_utc)
      - frecuencia (daily/weekly/every_n_days) basada en exports_last_run_ts
    """
    ex = cfg.get("exports", {})
    freq = ex.get("frequency", "daily")

    # coherente con la UI: every_n_days mínimo 2 normalmente
    every_days = int(ex.get("every_days", 2))

    scheduled_today = resolve_schedule_utc(now_utc, cfg)

    if now_utc < scheduled_today:
        return False

    last = get_setting("exports_last_run_ts")
    last_run = int(last) if last else 0
    now_ts = int(now_utc.timestamp())

    if last_run == 0:
        return True

    if freq == "daily":
        return now_ts - last_run >= 24 * 3600
    if freq == "weekly":
        return now_ts - last_run >= 7 * 24 * 3600
    if freq == "every_n_days":
        return now_ts - last_run >= every_days * 24 * 3600

    return now_ts - last_run >= 24 * 3600


def purge_old_exports(now_ts: int, keep_days: int) -> None:
    cutoff = now_ts - keep_days * 24 * 3600

    exports = list_exports(limit=5000)
    for e in exports:
        if e["created_ts"] < cutoff:
            try:
                os.remove(e["path"])
            except FileNotFoundError:
                pass
            except Exception as ex:
                print(f"[backup] WARN: cannot delete file {e['path']}: {ex}")

    deleted = delete_exports_older_than(cutoff)
    if deleted:
        print(f"[backup] purged exports deleted={deleted} keep_days={keep_days}")


def main() -> None:
    init_db()
    print("[backup] worker started (UTC-only schedule + day-idempotent)")

    while True:
        cfg = get_config()
        ex = cfg.get("exports", {})

        if not ex.get("enabled"):
            time.sleep(5)
            continue

        now_utc = datetime.now(timezone.utc)

        yesterday_utc = now_utc - timedelta(days=1)
        yesterday_str = yesterday_utc.date().isoformat()  # YYYY-MM-DD (UTC)
        period_from, period_to = day_bounds_utc(yesterday_utc)

        last_day = get_setting("exports_last_run_day")
        if last_day == yesterday_str:
            keep_days = int(ex.get("keep_days", 30))
            purge_old_exports(int(time.time()), keep_days=keep_days)
            time.sleep(30)
            continue

        if should_run_now_utc(now_utc, cfg):
            if already_exported(period_from, period_to):
                set_setting("exports_last_run_day", yesterday_str)
                set_setting("exports_last_run_ts", str(int(now_utc.timestamp())))
                print(f"[backup] already exported yesterday (UTC {yesterday_str}) -> mark done")
            else:
                path = export_csv(period_from, period_to)
                set_setting("exports_last_run_day", yesterday_str)
                set_setting("exports_last_run_ts", str(int(now_utc.timestamp())))
                print(f"[backup] export created (yesterday UTC {yesterday_str}): {path}")

        keep_days = int(ex.get("keep_days", 30))
        purge_old_exports(int(time.time()), keep_days=keep_days)

        time.sleep(30)


if __name__ == "__main__":
    main()
