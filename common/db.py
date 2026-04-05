from __future__ import annotations

import copy
import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

from common.alerts import alert_config_hash, build_local_revision, normalize_alerts_config
from common.config import (
    APP_VERSION,
    DATA_DIR,
    DB_PATH,
    MAX_ATTEMPTS,
    OUTBOX_LEASE_SECONDS,
    build_effective_config,
    deep_merge,
    normalize_local_config,
    sanitize_remote_overlay,
)
from common.security import canonical_json_bytes, short_stable_id
from common.telemetry import coerce_payload_to_envelope


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _column_exists(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row["name"] == column for row in cur.fetchall())


def _ensure_column(cur: sqlite3.Cursor, table: str, column: str, definition: str) -> None:
    if not _column_exists(cur, table, column):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_singleton_rows(cur: sqlite3.Cursor) -> None:
    cur.execute("INSERT OR IGNORE INTO remote_config_state(id, status, signature_ok) VALUES (1, 'idle', 0)")
    cur.execute(
        "INSERT OR IGNORE INTO update_state(id, current_version, channel, status, signature_ok) VALUES (1, ?, 'stable', 'idle', 0)",
        (APP_VERSION,),
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO alert_config_state(
            id, desired_revision, applied_revision, source, sync_status, applied_hash, last_error
        )
        VALUES (1, '', '', 'local', 'in_sync', '', '')
        """
    )


def _migrate_legacy_config_if_needed(cur: sqlite3.Cursor) -> None:
    cur.execute("SELECT value FROM settings WHERE key='config' LIMIT 1")
    row = cur.fetchone()
    if not row:
        return
    try:
        current = json.loads(row["value"])
    except Exception:
        current = {}
    normalized = normalize_local_config(current)
    if normalized != current:
        cur.execute(
            "UPDATE settings SET value=? WHERE key='config'",
            (json.dumps(normalized),),
        )


def _ensure_alert_state_from_config(cur: sqlite3.Cursor) -> None:
    cur.execute("SELECT value FROM settings WHERE key='config' LIMIT 1")
    row = cur.fetchone()
    try:
        current = json.loads(row["value"]) if row and row["value"] else {}
    except Exception:
        current = {}

    normalized = normalize_local_config(current)
    alerts_cfg = normalize_alerts_config(normalized.get("alerts"))
    applied_hash = alert_config_hash(alerts_cfg)
    applied_revision = build_local_revision(int(normalized.get("_rev", 0)))

    cur.execute("SELECT desired_revision, applied_revision, source, sync_status, applied_hash FROM alert_config_state WHERE id=1")
    state = cur.fetchone()
    if not state:
        cur.execute(
            """
            INSERT INTO alert_config_state(
                id, desired_revision, applied_revision, source, sync_status, applied_hash, last_error
            )
            VALUES (1, '', ?, 'local', 'in_sync', ?, '')
            """,
            (applied_revision, applied_hash),
        )
        return

    desired_revision = str(state["desired_revision"] or "")
    source = str(state["source"] or "local")
    sync_status = str(state["sync_status"] or "in_sync")
    state_applied_hash = str(state["applied_hash"] or "")
    state_applied_revision = str(state["applied_revision"] or "")

    if not state_applied_hash or not state_applied_revision:
        cur.execute(
            """
            UPDATE alert_config_state
            SET applied_revision=?, applied_hash=?, source=?, sync_status=?
            WHERE id=1
            """,
            (
                state_applied_revision or applied_revision,
                state_applied_hash or applied_hash,
                source or "local",
                sync_status or ("in_sync" if not desired_revision else "local_override"),
            ),
        )


def _migrate_legacy_outbox_rows(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        SELECT id, destination, payload, destination_id, delivery_kind, data_class, idempotency_key
        FROM outbox
        WHERE destination_id IS NULL OR delivery_kind IS NULL OR data_class IS NULL OR idempotency_key IS NULL
        """
    )
    rows = cur.fetchall()
    for row in rows:
        destination = str(row["destination"] or "").strip() or "legacy"
        delivery_kind = "mqtt" if destination == "mqtt" else "webhook_https"
        destination_id = "legacy-mqtt" if destination == "mqtt" else "legacy-webhook"
        try:
            payload = json.loads(row["payload"])
        except Exception:
            payload = {}
        envelope = coerce_payload_to_envelope(payload)
        idempotency_key = str(
            row["idempotency_key"]
            or short_stable_id(
                destination_id,
                row["id"],
                envelope.get("idempotency_key") or canonical_json_bytes(envelope).decode("utf-8"),
                length=64,
            )
        )
        data_class = str(row["data_class"] or envelope.get("data_class") or "weather_measurement")
        cur.execute(
            """
            UPDATE outbox
            SET destination_id=?, delivery_kind=?, data_class=?, idempotency_key=?, payload_version=?
            WHERE id=?
            """,
            (
                destination_id,
                delivery_kind,
                data_class,
                idempotency_key,
                int(envelope.get("schema_version") or 1),
                row["id"],
            ),
        )


def init_db() -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            temp_c REAL,
            humidity_pct REAL,
            pressure_hpa REAL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_measurements_ts ON measurements(ts)")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_ts INTEGER NOT NULL,
            next_attempt_ts INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            destination TEXT NOT NULL DEFAULT '',
            payload TEXT NOT NULL,
            last_error TEXT
        )
        """
    )
    _ensure_column(cur, "outbox", "destination_id", "TEXT")
    _ensure_column(cur, "outbox", "delivery_kind", "TEXT")
    _ensure_column(cur, "outbox", "data_class", "TEXT")
    _ensure_column(cur, "outbox", "idempotency_key", "TEXT")
    _ensure_column(cur, "outbox", "payload_version", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(cur, "outbox", "response_code", "INTEGER")
    _ensure_column(cur, "outbox", "last_attempt_ts", "INTEGER")
    _ensure_column(cur, "outbox", "lease_until_ts", "INTEGER")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_outbox_next ON outbox(status, next_attempt_ts)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_outbox_lease ON outbox(status, lease_until_ts)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_outbox_destination ON outbox(destination_id, status, next_attempt_ts)")
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_destination_idempotency ON outbox(destination_id, idempotency_key)"
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS exports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_ts INTEGER NOT NULL,
            period_from_ts INTEGER NOT NULL,
            period_to_ts INTEGER NOT NULL,
            filename TEXT NOT NULL,
            path TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_exports_created_ts ON exports(created_ts)")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS worker_heartbeats (
            worker_name TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            updated_ts INTEGER NOT NULL,
            details_json TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_config_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            desired_revision TEXT,
            applied_revision TEXT,
            source TEXT NOT NULL DEFAULT 'local',
            sync_status TEXT NOT NULL DEFAULT 'in_sync',
            applied_at INTEGER,
            desired_hash TEXT,
            applied_hash TEXT,
            last_error TEXT,
            last_remote_check_ts INTEGER,
            last_report_ts INTEGER,
            remote_endpoint TEXT,
            status_endpoint TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_rule_state (
            rule_id TEXT PRIMARY KEY,
            active INTEGER NOT NULL DEFAULT 0,
            condition_since_ts INTEGER,
            last_fired_ts INTEGER,
            last_resolved_ts INTEGER,
            last_value TEXT,
            last_evaluated_ts INTEGER,
            last_status TEXT,
            last_message TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS remote_config_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            current_revision TEXT,
            staged_revision TEXT,
            status TEXT NOT NULL DEFAULT 'idle',
            last_check_ts INTEGER,
            sha256 TEXT,
            signature_ok INTEGER NOT NULL DEFAULT 0,
            applied_ts INTEGER,
            last_error TEXT,
            endpoint TEXT
        )
        """
    )
    _ensure_column(cur, "remote_config_state", "last_check_ts", "INTEGER")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS update_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            current_version TEXT,
            target_version TEXT,
            channel TEXT,
            status TEXT NOT NULL DEFAULT 'idle',
            last_check_ts INTEGER,
            download_path TEXT,
            sha256 TEXT,
            signature_ok INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            artifact_url TEXT,
            applied_ts INTEGER
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS release_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_ts INTEGER NOT NULL,
            action TEXT NOT NULL,
            version TEXT,
            status TEXT NOT NULL,
            details_json TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_release_history_created_ts ON release_history(created_ts)")

    _ensure_singleton_rows(cur)
    _migrate_legacy_config_if_needed(cur)
    _migrate_legacy_outbox_rows(cur)
    _ensure_alert_state_from_config(cur)

    conn.commit()
    conn.close()


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
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def delete_setting(key: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM settings WHERE key=?", (key,))
    conn.commit()
    conn.close()


def _load_json_setting(key: str, fallback: Dict[str, Any] | None = None) -> Dict[str, Any]:
    raw = get_setting(key)
    if not raw:
        return copy.deepcopy(fallback or {})
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else copy.deepcopy(fallback or {})
    except Exception:
        return copy.deepcopy(fallback or {})


def get_local_config() -> Dict[str, Any]:
    return normalize_local_config(_load_json_setting("config", {}))


def get_remote_overlay() -> Dict[str, Any]:
    return sanitize_remote_overlay(_load_json_setting("remote_config_overlay", {}))


def _effective_revision(local_rev: int, remote_revision: str) -> int:
    if not remote_revision:
        return int(local_rev)
    remote_component = int(short_stable_id("remote-revision", remote_revision, length=8), 16)
    return int(local_rev) * 100_000_000 + remote_component


def get_config() -> Dict[str, Any]:
    local_config = get_local_config()
    remote_overlay = get_remote_overlay()
    effective = build_effective_config(local_config, remote_overlay)
    effective["_rev"] = _effective_revision(int(local_config.get("_rev", 0)), str(get_remote_config_state().get("current_revision") or ""))
    return effective


def get_config_bundle() -> Dict[str, Any]:
    local_config = get_local_config()
    remote_overlay = get_remote_overlay()
    effective = build_effective_config(local_config, remote_overlay)
    remote_state = get_remote_config_state()
    alert_state = get_alert_config_state()
    effective["_rev"] = _effective_revision(int(local_config.get("_rev", 0)), str(remote_state.get("current_revision") or ""))
    return {
        "config": effective,
        "effective_config": effective,
        "local_config": local_config,
        "remote_overlay": remote_overlay,
        "sources": {
            "local_rev": int(local_config.get("_rev", 0)),
            "remote_revision": remote_state.get("current_revision"),
            "remote_status": remote_state.get("status"),
            "alerts_applied_revision": alert_state.get("applied_revision"),
            "alerts_sync_status": alert_state.get("sync_status"),
        },
    }


def _update_alert_state_for_local_change(previous_cfg: Dict[str, Any], new_cfg: Dict[str, Any], *, local_revision: int) -> None:
    previous_alerts = normalize_alerts_config(previous_cfg.get("alerts"))
    current_alerts = normalize_alerts_config(new_cfg.get("alerts"))
    if previous_alerts == current_alerts:
        return

    state = get_alert_config_state()
    applied_hash = alert_config_hash(current_alerts)
    desired_hash = str(state.get("desired_hash") or "")
    desired_revision = str(state.get("desired_revision") or "")
    sync_status = "local_override" if desired_hash and desired_hash != applied_hash else "in_sync"
    applied_revision = desired_revision if desired_hash and desired_hash == applied_hash and desired_revision else build_local_revision(local_revision)
    set_alert_config_state(
        applied_revision=applied_revision,
        source="local",
        sync_status=sync_status,
        applied_at=int(time.time()),
        applied_hash=applied_hash,
        last_error="",
    )
    prune_alert_rule_states([str(rule.get("id") or "") for rule in current_alerts.get("rules", [])])


def set_config(cfg: Dict[str, Any], *, change_source: str = "local") -> None:
    current = get_local_config()
    rev = int(current.get("_rev", 0)) + 1
    merged = deep_merge(current, cfg if isinstance(cfg, dict) else {})
    normalized = normalize_local_config(merged)
    normalized["_rev"] = rev
    set_setting("config", json.dumps(normalized))
    if change_source == "local":
        _update_alert_state_for_local_change(current, normalized, local_revision=rev)


def set_remote_overlay(
    overlay: Dict[str, Any],
    *,
    revision: str,
    sha256: str = "",
    signature_ok: bool = False,
    status: str = "applied",
    endpoint: str = "",
    last_error: str = "",
) -> None:
    sanitized = sanitize_remote_overlay(overlay)
    set_setting("remote_config_overlay", json.dumps(sanitized))
    set_remote_config_state(
        current_revision=str(revision or ""),
        staged_revision=str(revision or ""),
        status=status,
        sha256=sha256,
        signature_ok=signature_ok,
        applied_ts=int(time.time()),
        last_error=last_error,
        endpoint=endpoint,
    )


def clear_remote_overlay(*, status: str = "idle", last_error: str = "") -> None:
    delete_setting("remote_config_overlay")
    set_remote_config_state(status=status, last_error=last_error)


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
    cur.execute(
        """
        SELECT ts, temp_c, humidity_pct, pressure_hpa
        FROM measurements
        ORDER BY ts DESC
        LIMIT 1
        """
    )
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
    return [dict(row) for row in reversed(rows)]


def delete_measurements_older_than(cutoff_ts: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM measurements WHERE ts < ?", (cutoff_ts,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def enqueue_outbox(destination: str, payload: Dict[str, Any], now_ts: int) -> None:
    envelope = coerce_payload_to_envelope(payload)
    destination_id = "legacy-mqtt" if destination == "mqtt" else "legacy-webhook"
    delivery_kind = "mqtt" if destination == "mqtt" else "webhook_https"
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO outbox (
            created_ts, next_attempt_ts, attempts, status, destination, destination_id, delivery_kind,
            data_class, idempotency_key, payload, payload_version
        )
        VALUES (?, ?, 0, 'pending', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now_ts,
            now_ts,
            destination,
            destination_id,
            delivery_kind,
            envelope.get("data_class"),
            envelope.get("idempotency_key"),
            json.dumps(envelope),
            int(envelope.get("schema_version") or 1),
        ),
    )
    conn.commit()
    conn.close()


def enqueue_delivery_item(
    *,
    destination: Dict[str, Any],
    envelope: Dict[str, Any],
    now_ts: int,
    next_attempt_ts: Optional[int] = None,
) -> bool:
    due_ts = int(next_attempt_ts or now_ts)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO outbox (
            created_ts, next_attempt_ts, attempts, status, destination, destination_id, delivery_kind,
            data_class, idempotency_key, payload, payload_version
        )
        VALUES (?, ?, 0, 'pending', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now_ts,
            due_ts,
            str(destination.get("id") or destination.get("kind") or ""),
            str(destination.get("id") or ""),
            str(destination.get("kind") or ""),
            str(envelope.get("data_class") or ""),
            str(envelope.get("idempotency_key") or ""),
            json.dumps(envelope),
            int(envelope.get("schema_version") or 1),
        ),
    )
    inserted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return inserted


def fetch_due_outbox(now_ts: int, limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM outbox
        WHERE status='pending'
          AND next_attempt_ts <= ?
          AND (lease_until_ts IS NULL OR lease_until_ts <= ?)
        ORDER BY next_attempt_ts ASC, id ASC
        LIMIT ?
        """,
        (now_ts, now_ts, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def lease_due_outbox(now_ts: int, limit: int = 50, lease_seconds: int = OUTBOX_LEASE_SECONDS) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    cur.execute(
        """
        SELECT id
        FROM outbox
        WHERE status='pending'
          AND next_attempt_ts <= ?
          AND (lease_until_ts IS NULL OR lease_until_ts <= ?)
        ORDER BY next_attempt_ts ASC, id ASC
        LIMIT ?
        """,
        (now_ts, now_ts, limit),
    )
    ids = [row["id"] for row in cur.fetchall()]
    if not ids:
        conn.commit()
        conn.close()
        return []

    placeholders = ",".join("?" for _ in ids)
    params = [now_ts + lease_seconds, now_ts] + ids
    cur.execute(
        f"""
        UPDATE outbox
        SET status='leased', lease_until_ts=?, last_attempt_ts=?
        WHERE id IN ({placeholders})
        """,
        params,
    )
    cur.execute(f"SELECT * FROM outbox WHERE id IN ({placeholders}) ORDER BY next_attempt_ts ASC, id ASC", ids)
    rows = [dict(row) for row in cur.fetchall()]
    conn.commit()
    conn.close()
    return rows


def release_outbox_lease(outbox_id: int, next_attempt_ts: int, error: str = "") -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE outbox
        SET status='pending', next_attempt_ts=?, lease_until_ts=NULL, last_error=?
        WHERE id=?
        """,
        (next_attempt_ts, error[:500] or None, outbox_id),
    )
    conn.commit()
    conn.close()


def mark_outbox_sent(outbox_id: int, response_code: Optional[int] = None) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE outbox
        SET status='sent', response_code=?, last_error=NULL, lease_until_ts=NULL, last_attempt_ts=?
        WHERE id=?
        """,
        (response_code, int(time.time()), outbox_id),
    )
    conn.commit()
    conn.close()


def backoff_seconds(attempts: int) -> int:
    schedule = [10, 30, 120, 600, 3600]
    return schedule[min(attempts, len(schedule) - 1)]


def mark_outbox_failed(
    outbox_id: int,
    attempts: int,
    error: str,
    now_ts: int,
    *,
    response_code: Optional[int] = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> None:
    conn = get_connection()
    cur = conn.cursor()
    if attempts >= max_attempts:
        cur.execute(
            """
            UPDATE outbox
            SET attempts=?, status='failed', last_error=?, response_code=?, last_attempt_ts=?, lease_until_ts=NULL
            WHERE id=?
            """,
            (attempts, error[:500], response_code, now_ts, outbox_id),
        )
    else:
        next_ts = now_ts + backoff_seconds(attempts)
        cur.execute(
            """
            UPDATE outbox
            SET attempts=?, status='pending', next_attempt_ts=?, last_error=?, response_code=?, last_attempt_ts=?, lease_until_ts=NULL
            WHERE id=?
            """,
            (attempts, next_ts, error[:500], response_code, now_ts, outbox_id),
        )
    conn.commit()
    conn.close()


def outbox_summary() -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT status, COUNT(*) AS c FROM outbox GROUP BY status")
    rows = cur.fetchall()
    cur.execute("SELECT COUNT(*) AS c FROM outbox WHERE status='pending'")
    pending_count = cur.fetchone()["c"]
    conn.close()
    summary = {"pending": 0, "leased": 0, "sent": 0, "failed": 0, "total_pending": pending_count}
    for row in rows:
        summary[row["status"]] = row["c"]
    return summary


def fetch_outbox(status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    if status:
        cur.execute(
            """
            SELECT id, created_ts, next_attempt_ts, attempts, status, destination, destination_id, delivery_kind,
                   data_class, idempotency_key, response_code, last_attempt_ts, last_error
            FROM outbox
            WHERE status=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (status, limit),
        )
    else:
        cur.execute(
            """
            SELECT id, created_ts, next_attempt_ts, attempts, status, destination, destination_id, delivery_kind,
                   data_class, idempotency_key, response_code, last_attempt_ts, last_error
            FROM outbox
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def retry_failed_outbox(now_ts: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE outbox
        SET status='pending', next_attempt_ts=?, lease_until_ts=NULL, last_error=NULL
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


def telemetry_delivery_summary() -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COALESCE(destination_id, destination) AS destination_id, status, COUNT(*) AS c
        FROM outbox
        GROUP BY COALESCE(destination_id, destination), status
        """
    )
    rows = cur.fetchall()
    conn.close()
    by_destination: Dict[str, Dict[str, int]] = {}
    for row in rows:
        destination_id = str(row["destination_id"] or "unknown")
        bucket = by_destination.setdefault(destination_id, {"pending": 0, "leased": 0, "sent": 0, "failed": 0})
        bucket[row["status"]] = row["c"]
    return {
        "summary": outbox_summary(),
        "destinations": by_destination,
    }


def oldest_unsent_outbox_age_seconds(now_ts: Optional[int] = None) -> int:
    reference_ts = int(now_ts or time.time())
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT MIN(created_ts) AS oldest_created_ts
        FROM outbox
        WHERE status IN ('pending', 'leased', 'failed')
        """
    )
    row = cur.fetchone()
    conn.close()
    oldest_created_ts = row["oldest_created_ts"] if row else None
    if not oldest_created_ts:
        return 0
    return max(0, reference_ts - int(oldest_created_ts))


def upsert_worker_heartbeat(worker_name: str, status: str, details: Dict[str, Any] | None = None) -> None:
    payload = json.dumps(details or {}, sort_keys=True)
    now_ts = int(time.time())
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO worker_heartbeats(worker_name, status, updated_ts, details_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(worker_name) DO UPDATE SET
            status=excluded.status,
            updated_ts=excluded.updated_ts,
            details_json=excluded.details_json
        """,
        (worker_name, status, now_ts, payload),
    )
    conn.commit()
    conn.close()


def fetch_worker_heartbeats(stale_after_seconds: int = 120) -> List[Dict[str, Any]]:
    now_ts = int(time.time())
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT worker_name, status, updated_ts, details_json FROM worker_heartbeats ORDER BY worker_name ASC")
    rows = cur.fetchall()
    conn.close()
    items = []
    for row in rows:
        try:
            details = json.loads(row["details_json"] or "{}")
        except Exception:
            details = {}
        updated_ts = int(row["updated_ts"])
        items.append(
            {
                "worker_name": row["worker_name"],
                "status": row["status"],
                "updated_ts": updated_ts,
                "stale": (now_ts - updated_ts) > stale_after_seconds,
                "details": details,
            }
        )
    return items


def get_alert_config_state() -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM alert_config_state WHERE id=1")
    row = cur.fetchone()
    conn.close()
    if not row:
        return {
            "desired_revision": "",
            "applied_revision": "",
            "source": "local",
            "sync_status": "in_sync",
            "applied_at": None,
            "desired_hash": "",
            "applied_hash": "",
            "config_hash": "",
            "last_error": "",
            "last_remote_check_ts": None,
            "last_report_ts": None,
            "remote_endpoint": "",
            "status_endpoint": "",
        }
    result = dict(row)
    result["config_hash"] = str(result.get("applied_hash") or "")
    result["last_change_source"] = str(result.get("source") or "local")
    return result


def set_alert_config_state(**updates: Any) -> Dict[str, Any]:
    current = get_alert_config_state()
    current.update(updates)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO alert_config_state(
            id, desired_revision, applied_revision, source, sync_status, applied_at, desired_hash,
            applied_hash, last_error, last_remote_check_ts, last_report_ts, remote_endpoint, status_endpoint
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            desired_revision=excluded.desired_revision,
            applied_revision=excluded.applied_revision,
            source=excluded.source,
            sync_status=excluded.sync_status,
            applied_at=excluded.applied_at,
            desired_hash=excluded.desired_hash,
            applied_hash=excluded.applied_hash,
            last_error=excluded.last_error,
            last_remote_check_ts=excluded.last_remote_check_ts,
            last_report_ts=excluded.last_report_ts,
            remote_endpoint=excluded.remote_endpoint,
            status_endpoint=excluded.status_endpoint
        """,
        (
            current.get("desired_revision", ""),
            current.get("applied_revision", ""),
            current.get("source", "local"),
            current.get("sync_status", "in_sync"),
            current.get("applied_at"),
            current.get("desired_hash", ""),
            current.get("applied_hash", ""),
            current.get("last_error", ""),
            current.get("last_remote_check_ts"),
            current.get("last_report_ts"),
            current.get("remote_endpoint", ""),
            current.get("status_endpoint", ""),
        ),
    )
    conn.commit()
    conn.close()
    current["config_hash"] = str(current.get("applied_hash") or "")
    current["last_change_source"] = str(current.get("source") or "local")
    return current


def list_alert_rule_states() -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT rule_id, active, condition_since_ts, last_fired_ts, last_resolved_ts, last_value,
               last_evaluated_ts, last_status, last_message
        FROM alert_rule_state
        ORDER BY rule_id ASC
        """
    )
    rows = cur.fetchall()
    conn.close()
    items = []
    for row in rows:
        item = dict(row)
        item["active"] = bool(item.get("active", 0))
        raw_last_value = item.get("last_value")
        if isinstance(raw_last_value, str):
            try:
                item["last_value"] = json.loads(raw_last_value)
            except Exception:
                item["last_value"] = raw_last_value
        items.append(item)
    return items


def alert_rule_state_map() -> Dict[str, Dict[str, Any]]:
    return {str(item.get("rule_id") or ""): item for item in list_alert_rule_states()}


def upsert_alert_rule_state(rule_id: str, state: Dict[str, Any]) -> None:
    payload = dict(state or {})
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO alert_rule_state(
            rule_id, active, condition_since_ts, last_fired_ts, last_resolved_ts, last_value,
            last_evaluated_ts, last_status, last_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(rule_id) DO UPDATE SET
            active=excluded.active,
            condition_since_ts=excluded.condition_since_ts,
            last_fired_ts=excluded.last_fired_ts,
            last_resolved_ts=excluded.last_resolved_ts,
            last_value=excluded.last_value,
            last_evaluated_ts=excluded.last_evaluated_ts,
            last_status=excluded.last_status,
            last_message=excluded.last_message
        """,
        (
            rule_id,
            1 if payload.get("active") else 0,
            payload.get("condition_since_ts"),
            payload.get("last_fired_ts"),
            payload.get("last_resolved_ts"),
            json.dumps(payload.get("last_value")),
            payload.get("last_evaluated_ts"),
            payload.get("last_status"),
            payload.get("last_message", ""),
        ),
    )
    conn.commit()
    conn.close()


def prune_alert_rule_states(valid_rule_ids: List[str]) -> int:
    items = [str(rule_id) for rule_id in valid_rule_ids if str(rule_id)]
    conn = get_connection()
    cur = conn.cursor()
    if items:
        placeholders = ",".join("?" for _ in items)
        cur.execute(f"DELETE FROM alert_rule_state WHERE rule_id NOT IN ({placeholders})", items)
    else:
        cur.execute("DELETE FROM alert_rule_state")
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def apply_remote_alerts_config(
    alerts_cfg: Dict[str, Any],
    *,
    revision: str,
    desired_hash: str = "",
    remote_endpoint: str = "",
    status_endpoint: str = "",
) -> Dict[str, Any]:
    current = get_local_config()
    rev = int(current.get("_rev", 0)) + 1
    merged = deep_merge(current, {"alerts": alerts_cfg})
    normalized = normalize_local_config(merged)
    normalized["_rev"] = rev
    normalized_alerts = normalize_alerts_config(normalized.get("alerts"))
    applied_hash = desired_hash or alert_config_hash(normalized_alerts)
    set_setting("config", json.dumps(normalized))
    set_alert_config_state(
        desired_revision=str(revision or ""),
        applied_revision=str(revision or ""),
        source="remote",
        sync_status="in_sync",
        applied_at=int(time.time()),
        desired_hash=applied_hash,
        applied_hash=applied_hash,
        last_error="",
        remote_endpoint=remote_endpoint,
        status_endpoint=status_endpoint,
    )
    prune_alert_rule_states([str(rule.get("id") or "") for rule in normalized_alerts.get("rules", [])])
    return get_alert_config_state()


def get_remote_config_state() -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM remote_config_state WHERE id=1")
    row = cur.fetchone()
    conn.close()
    if not row:
        return {
            "current_revision": None,
            "staged_revision": None,
            "status": "idle",
            "last_check_ts": None,
            "sha256": "",
            "signature_ok": False,
            "applied_ts": None,
            "last_error": "",
            "endpoint": "",
        }
    result = dict(row)
    result["signature_ok"] = bool(result.get("signature_ok", 0))
    return result


def set_remote_config_state(**updates: Any) -> Dict[str, Any]:
    current = get_remote_config_state()
    current.update(updates)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO remote_config_state(
            id, current_revision, staged_revision, status, last_check_ts, sha256, signature_ok, applied_ts, last_error, endpoint
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            current_revision=excluded.current_revision,
            staged_revision=excluded.staged_revision,
            status=excluded.status,
            last_check_ts=excluded.last_check_ts,
            sha256=excluded.sha256,
            signature_ok=excluded.signature_ok,
            applied_ts=excluded.applied_ts,
            last_error=excluded.last_error,
            endpoint=excluded.endpoint
        """,
        (
            current.get("current_revision"),
            current.get("staged_revision"),
            current.get("status", "idle"),
            current.get("last_check_ts"),
            current.get("sha256", ""),
            1 if current.get("signature_ok") else 0,
            current.get("applied_ts"),
            current.get("last_error", ""),
            current.get("endpoint", ""),
        ),
    )
    conn.commit()
    conn.close()
    current["signature_ok"] = bool(current.get("signature_ok"))
    return current


def get_update_state() -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM update_state WHERE id=1")
    row = cur.fetchone()
    conn.close()
    if not row:
        return {
            "current_version": APP_VERSION,
            "target_version": None,
            "channel": "stable",
            "status": "idle",
            "last_check_ts": None,
            "download_path": "",
            "sha256": "",
            "signature_ok": False,
            "last_error": "",
            "artifact_url": "",
            "applied_ts": None,
        }
    result = dict(row)
    result["signature_ok"] = bool(result.get("signature_ok", 0))
    return result


def set_update_state(**updates: Any) -> Dict[str, Any]:
    current = get_update_state()
    current.update(updates)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO update_state(
            id, current_version, target_version, channel, status, last_check_ts, download_path,
            sha256, signature_ok, last_error, artifact_url, applied_ts
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            current_version=excluded.current_version,
            target_version=excluded.target_version,
            channel=excluded.channel,
            status=excluded.status,
            last_check_ts=excluded.last_check_ts,
            download_path=excluded.download_path,
            sha256=excluded.sha256,
            signature_ok=excluded.signature_ok,
            last_error=excluded.last_error,
            artifact_url=excluded.artifact_url,
            applied_ts=excluded.applied_ts
        """,
        (
            current.get("current_version", APP_VERSION),
            current.get("target_version"),
            current.get("channel", "stable"),
            current.get("status", "idle"),
            current.get("last_check_ts"),
            current.get("download_path", ""),
            current.get("sha256", ""),
            1 if current.get("signature_ok") else 0,
            current.get("last_error", ""),
            current.get("artifact_url", ""),
            current.get("applied_ts"),
        ),
    )
    conn.commit()
    conn.close()
    current["signature_ok"] = bool(current.get("signature_ok"))
    return current


def add_release_history(action: str, version: str, status: str, details: Dict[str, Any] | None = None) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO release_history(created_ts, action, version, status, details_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (int(time.time()), action, version, status, json.dumps(details or {}, sort_keys=True)),
    )
    conn.commit()
    conn.close()


def list_release_history(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, created_ts, action, version, status, details_json
        FROM release_history
        ORDER BY created_ts DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["details"] = json.loads(item.pop("details_json") or "{}")
        except Exception:
            item["details"] = {}
        items.append(item)
    return items


def request_control_action(name: str, payload: Dict[str, Any] | None = None) -> None:
    set_setting(f"control:{name}", json.dumps({"requested_ts": int(time.time()), "payload": payload or {}}))


def pop_control_action(name: str) -> Optional[Dict[str, Any]]:
    key = f"control:{name}"
    raw = get_setting(key)
    if not raw:
        return None
    delete_setting(key)
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"payload": {}}
    except Exception:
        return {"payload": {}}


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
    return [dict(row) for row in rows]


def delete_exports_older_than(cutoff_ts: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM exports WHERE created_ts < ?", (cutoff_ts,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted
