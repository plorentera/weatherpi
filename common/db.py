import copy
import json
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "meteo.db"
MAX_ATTEMPTS = 10

DEFAULT_CONFIG = {
    "station_id": "meteo-001",
    "sample_interval_seconds": 5,
    "_rev": 0,

    "outputs": {
        "webhook": {
            "enabled": False,
            "url": "",
            "timeout_seconds": 5
        },
        "mqtt": {
            "enabled": False,
            "host": "localhost",
            "port": 1883,
            "topic": "meteo/measurements"
        },
    },
        "collector": {
            "enabled": True,
        },

    "exports": {
        "enabled": False,
        "frequency": "daily",
        "every_days": 2,
        "keep_days": 30,
        "days_per_file": 1,

        "upload": {
            "enabled": False,
            "webhook_url": "",
        },

        "schedule": {
            "time_local": "01:00",
            "time_utc": "00:00",
        },
    },

    "ui": {
        "timezone": "UTC"
    },
}



def get_connection():
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS measurements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        temp_c REAL,
        humidity_pct REAL,
        pressure_hpa REAL
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_measurements_ts
    ON measurements(ts)
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_ts INTEGER NOT NULL,
        next_attempt_ts INTEGER NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',   -- pending/sent/failed
        destination TEXT NOT NULL,                -- 'webhook' o 'mqtt'
        payload TEXT NOT NULL,                    -- JSON
        last_error TEXT
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_outbox_next
    ON outbox(status, next_attempt_ts)
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS exports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_ts INTEGER NOT NULL,
        period_from_ts INTEGER NOT NULL,
        period_to_ts INTEGER NOT NULL,
        filename TEXT NOT NULL,
        path TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_exports_created_ts
    ON exports(created_ts)
    """)

    conn.commit()
    conn.close()


def insert_measurement(ts: int, metrics: Dict[str, Any]) -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO measurements (ts, temp_c, humidity_pct, pressure_hpa)
        VALUES (?, ?, ?, ?)
        """,
        (
            ts,
            metrics.get("temp_c"),
            metrics.get("humidity_pct"),
            metrics.get("pressure_hpa"),
        ),
    )

    conn.commit()
    conn.close()


def fetch_latest() -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT ts, temp_c, humidity_pct, pressure_hpa
        FROM measurements
        ORDER BY ts DESC
        LIMIT 1
    """)

    row = cur.fetchone()
    conn.close()

    return dict(row) if row else None


def fetch_measurements_series(limit: int = 288) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT ts, temp_c, humidity_pct, pressure_hpa
        FROM measurements
        ORDER BY ts DESC
        LIMIT ?
        """,
        (limit,),
    )

    rows = cur.fetchall()
    conn.close()

    # Return ascending order for chart timelines.
    return [dict(r) for r in reversed(rows)]


def _normalize_exports_schedule(schedule: Any) -> Dict[str, Any]:
    """
    Normaliza schedule para el modelo UTC-only:
      - solo se ejecuta con time_utc
      - elimina legacy: time, timezone
      - permite metadatos de UI: ui_timezone, ui_time_local (no los usa el worker)
    """
    if not isinstance(schedule, dict):
        schedule = {}

    schedule.pop("time", None)
    schedule.pop("timezone", None)

    if not schedule.get("time_utc"):
        schedule["time_utc"] = "00:00"

    return schedule


def _merge_config(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)

    for key, value in incoming.items():
        if key not in ("outputs", "exports"):
            merged[key] = value

    if isinstance(incoming.get("outputs"), dict):
        for name, sub in incoming["outputs"].items():
            if name not in merged["outputs"] or not isinstance(sub, dict):
                merged["outputs"][name] = sub
            else:
                merged["outputs"][name].update(sub)

    if isinstance(incoming.get("exports"), dict):
        for key, value in incoming["exports"].items():
            if key != "schedule":
                merged["exports"][key] = value

        if isinstance(incoming["exports"].get("schedule"), dict):
            merged_schedule = merged["exports"].get("schedule", {})
            merged_schedule.update(incoming["exports"]["schedule"])
            merged["exports"]["schedule"] = merged_schedule

    merged["exports"]["schedule"] = _normalize_exports_schedule(merged["exports"].get("schedule"))
    return merged


def get_config():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key='config' LIMIT 1")
    row = cur.fetchone()
    conn.close()

    if not row:
        return copy.deepcopy(DEFAULT_CONFIG)

    try:
        cfg = json.loads(row["value"])
    except Exception:
        cfg = {}

    return _merge_config(DEFAULT_CONFIG, cfg)


def set_config(cfg: dict) -> None:
    current = get_config()
    rev = int(current.get("_rev", 0)) + 1

    merged = _merge_config(current, cfg)

    merged["_rev"] = rev

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings(key, value) VALUES('config', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps(merged),),
    )
    conn.commit()
    conn.close()


def enqueue_outbox(destination: str, payload: Dict[str, Any], now_ts: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO outbox (created_ts, next_attempt_ts, destination, payload)
        VALUES (?, ?, ?, ?)
        """,
        (now_ts, now_ts, destination, json.dumps(payload)),
    )
    conn.commit()
    conn.close()


def fetch_due_outbox(now_ts: int, limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, created_ts, next_attempt_ts, attempts, status, destination, payload, last_error
        FROM outbox
        WHERE status='pending' AND next_attempt_ts <= ?
        ORDER BY id ASC
        LIMIT ?
        """,
        (now_ts, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_outbox_sent(outbox_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE outbox SET status='sent', last_error=NULL WHERE id=?", (outbox_id,))
    conn.commit()
    conn.close()


def backoff_seconds(attempts: int) -> int:
    # 0->10s,1->30s,2->2m,3->10m,4->1h, luego 1h
    schedule = [10, 30, 120, 600, 3600]
    return schedule[min(attempts, len(schedule) - 1)]


def mark_outbox_failed(outbox_id: int, attempts: int, error: str, now_ts: int) -> None:
    conn = get_connection()
    cur = conn.cursor()

    if attempts >= MAX_ATTEMPTS:
        cur.execute(
            """
            UPDATE outbox
            SET attempts=?, status='failed', last_error=?
            WHERE id=?
            """,
            (attempts, error[:500], outbox_id),
        )
    else:
        delay = backoff_seconds(attempts)
        next_ts = now_ts + delay
        cur.execute(
            """
            UPDATE outbox
            SET attempts=?, next_attempt_ts=?, status='pending', last_error=?
            WHERE id=?
            """,
            (attempts, next_ts, error[:500], outbox_id),
        )

    conn.commit()
    conn.close()


def outbox_summary() -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT status, COUNT(*) AS c FROM outbox GROUP BY status")
    rows = cur.fetchall()
    conn.close()

    summary = {"pending": 0, "sent": 0, "failed": 0}
    for r in rows:
        summary[r["status"]] = r["c"]
    return summary


def fetch_outbox(status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()

    if status:
        cur.execute(
            """
            SELECT id, created_ts, next_attempt_ts, attempts, status, destination, last_error
            FROM outbox
            WHERE status = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (status, limit),
        )
    else:
        cur.execute(
            """
            SELECT id, created_ts, next_attempt_ts, attempts, status, destination, last_error
            FROM outbox
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )

    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_measurements_older_than(cutoff_ts: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM measurements WHERE ts < ?", (cutoff_ts,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def retry_failed_outbox(now_ts: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE outbox
        SET status='pending', next_attempt_ts=?, last_error=NULL
        WHERE status='failed'
        """,
        (now_ts,),
    )
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return changed


def purge_sent_outbox(keep_last: int = 1000) -> int:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        DELETE FROM outbox
        WHERE status='sent'
          AND id NOT IN (
              SELECT id FROM outbox
              WHERE status='sent'
              ORDER BY id DESC
              LIMIT ?
          )
        """,
        (keep_last,),
    )

    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def get_setting(key: str) -> Optional[str]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key=? LIMIT 1", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def insert_export(created_ts: int, period_from_ts: int, period_to_ts: int, filename: str, path: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO exports(created_ts, period_from_ts, period_to_ts, filename, path)
        VALUES (?, ?, ?, ?, ?)
        """,
        (created_ts, period_from_ts, period_to_ts, filename, path),
    )
    conn.commit()
    conn.close()


def list_exports(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, created_ts, period_from_ts, period_to_ts, filename, path
        FROM exports
        ORDER BY created_ts DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_exports_older_than(cutoff_ts: int) -> int:
    """
    Borra registros antiguos de exports (el worker borrará también el fichero).
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM exports WHERE created_ts < ?", (cutoff_ts,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted
