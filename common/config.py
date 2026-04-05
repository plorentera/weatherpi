from __future__ import annotations

import copy
import os
import socket
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse

from common.alerts import normalize_alerts_config, validate_alerts_config


BASE_DIR = Path(os.getenv("WEATHERPI_BASE_DIR", str(Path(__file__).resolve().parent.parent))).resolve()
DATA_DIR = Path(os.getenv("WEATHERPI_DATA_DIR", str(BASE_DIR / "data"))).resolve()
DB_PATH = DATA_DIR / "meteo.db"
EXPORTS_DIR = DATA_DIR / "exports"
SECRETS_PATH = DATA_DIR / "device_secrets.json"
LOCAL_AUTH_PATH = DATA_DIR / "local_auth.json"
RELEASES_DIR = DATA_DIR / "releases"
DOWNLOADS_DIR = RELEASES_DIR / "downloads"
VERSIONS_DIR = RELEASES_DIR / "versions"
CURRENT_RELEASE_LINK = RELEASES_DIR / "current"
PREVIOUS_RELEASE_LINK = RELEASES_DIR / "previous"
RUNTIME_STATE_DIR = DATA_DIR / "runtime"

APP_VERSION = os.getenv("WEATHERPI_VERSION", "1.1.0")
MAX_ATTEMPTS = 10
OUTBOX_LEASE_SECONDS = 60
TELEMETRY_SCHEMA_VERSION = 1
VALID_DATA_CLASSES = {
    "weather_measurement",
    "station_status",
    "update_status",
    "alert_event",
}
VALID_DESTINATION_KINDS = {"webhook_https", "mqtt"}


DEFAULT_CONFIG: Dict[str, Any] = {
    "station_id": "meteo-001",
    "sample_interval_seconds": 5,
    "_rev": 0,
    "collector": {
        "enabled": True,
        "status_emit_interval_seconds": 60,
    },
    "telemetry": {
        "enabled": False,
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "destinations": [],
    },
    "alerts": {
        "enabled": False,
        "rules": [],
    },
    "remote_config": {
        "enabled": False,
        "endpoint": "",
        "poll_interval_seconds": 900,
        "auto_apply": True,
        "apply_mode": "staged_apply",
        "auth": {
            "mode": "bearer",
            "key_id": "",
        },
        "tls": {
            "ca_bundle_path": "",
            "pinned_pubkey_sha256": "",
        },
        "signing": {
            "required": True,
            "algorithm": "ed25519",
            "public_key": "",
        },
    },
    "updates": {
        "enabled": False,
        "manifest_url": "",
        "poll_interval_seconds": 3600,
        "channel": "stable",
        "auto_download": True,
        "apply_strategy": "manual",
        "health_grace_seconds": 120,
        "maintenance_window": {
            "enabled": False,
            "start_local": "02:00",
            "duration_minutes": 60,
        },
        "auth": {
            "mode": "bearer",
            "key_id": "",
        },
        "tls": {
            "ca_bundle_path": "",
            "pinned_pubkey_sha256": "",
        },
        "signing": {
            "required": True,
            "algorithm": "ed25519",
            "public_key": "",
        },
    },
    "security": {
        "allow_lan": False,
        "api_bind_host": "127.0.0.1",
        "require_tls_for_remote": True,
        "block_remote_when_default_local_credentials": True,
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
        "timezone": "UTC",
    },
}


def default_destination(kind: str = "webhook_https") -> Dict[str, Any]:
    base = {
        "id": "",
        "enabled": False,
        "kind": kind if kind in VALID_DESTINATION_KINDS else "webhook_https",
        "data_classes": ["weather_measurement"],
        "schedule": {
            "mode": "realtime",
            "interval_seconds": 60,
        },
        "batch_max_items": 1,
        "retry_policy": {
            "max_attempts": MAX_ATTEMPTS,
        },
        "auth": {
            "mode": "none",
            "key_id": "",
        },
        "webhook": {
            "url": "",
            "timeout_seconds": 5,
            "headers": {},
            "tls": {
                "ca_bundle_path": "",
                "pinned_pubkey_sha256": "",
            },
        },
        "mqtt": {
            "host": "",
            "port": 8883,
            "topic": "weatherpi/measurements",
            "client_id": "",
            "username": "",
            "keepalive_seconds": 30,
            "tls": {
                "enabled": True,
                "ca_bundle_path": "",
            },
        },
    }
    return base


def deep_merge(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in incoming.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _normalize_exports_schedule(schedule: Any) -> Dict[str, Any]:
    if not isinstance(schedule, dict):
        schedule = {}

    schedule = copy.deepcopy(schedule)
    schedule.pop("time", None)
    schedule.pop("timezone", None)
    schedule["time_local"] = str(schedule.get("time_local") or "01:00")
    schedule["time_utc"] = str(schedule.get("time_utc") or "00:00")
    return schedule


def _normalize_tls(data: Any, include_enabled: bool = False) -> Dict[str, Any]:
    raw = data if isinstance(data, dict) else {}
    out = {
        "ca_bundle_path": str(raw.get("ca_bundle_path") or ""),
        "pinned_pubkey_sha256": str(raw.get("pinned_pubkey_sha256") or ""),
    }
    if include_enabled:
        out["enabled"] = bool(raw.get("enabled", True))
    return out


def _normalize_auth(data: Any, default_mode: str = "none") -> Dict[str, Any]:
    raw = data if isinstance(data, dict) else {}
    return {
        "mode": str(raw.get("mode") or default_mode),
        "key_id": str(raw.get("key_id") or ""),
    }


def _normalize_schedule(data: Any) -> Dict[str, Any]:
    raw = data if isinstance(data, dict) else {}
    mode = str(raw.get("mode") or "realtime")
    if mode not in {"realtime", "interval"}:
        mode = "realtime"
    try:
        interval_seconds = int(raw.get("interval_seconds", 60))
    except Exception:
        interval_seconds = 60
    interval_seconds = max(5, min(interval_seconds, 86400))
    return {
        "mode": mode,
        "interval_seconds": interval_seconds,
    }


def _normalize_retry_policy(data: Any) -> Dict[str, Any]:
    raw = data if isinstance(data, dict) else {}
    try:
        max_attempts = int(raw.get("max_attempts", MAX_ATTEMPTS))
    except Exception:
        max_attempts = MAX_ATTEMPTS
    return {
        "max_attempts": max(1, min(max_attempts, 50)),
    }


def normalize_destination(raw: Any, index: int = 0) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    kind = str(source.get("kind") or "webhook_https")
    if kind not in VALID_DESTINATION_KINDS:
        kind = "webhook_https"
    normalized = deep_merge(default_destination(kind), source)
    normalized["kind"] = kind
    normalized["id"] = str(normalized.get("id") or f"{kind}-{index + 1}")
    normalized["enabled"] = bool(normalized.get("enabled", False))
    normalized["schedule"] = _normalize_schedule(normalized.get("schedule"))
    normalized["retry_policy"] = _normalize_retry_policy(normalized.get("retry_policy"))
    normalized["auth"] = _normalize_auth(normalized.get("auth"), default_mode="none")
    normalized["webhook"]["timeout_seconds"] = max(1, min(int(normalized["webhook"].get("timeout_seconds", 5)), 60))
    normalized["webhook"]["url"] = str(normalized["webhook"].get("url") or "").strip()
    normalized["webhook"]["headers"] = normalized["webhook"].get("headers") if isinstance(normalized["webhook"].get("headers"), dict) else {}
    normalized["webhook"]["tls"] = _normalize_tls(normalized["webhook"].get("tls"), include_enabled=False)
    normalized["mqtt"]["host"] = str(normalized["mqtt"].get("host") or "").strip()
    normalized["mqtt"]["topic"] = str(normalized["mqtt"].get("topic") or "weatherpi/measurements").strip()
    normalized["mqtt"]["client_id"] = str(normalized["mqtt"].get("client_id") or "").strip()
    normalized["mqtt"]["username"] = str(normalized["mqtt"].get("username") or "").strip()
    normalized["mqtt"]["port"] = max(1, min(int(normalized["mqtt"].get("port", 8883)), 65535))
    normalized["mqtt"]["keepalive_seconds"] = max(5, min(int(normalized["mqtt"].get("keepalive_seconds", 30)), 3600))
    normalized["mqtt"]["tls"] = _normalize_tls(normalized["mqtt"].get("tls"), include_enabled=True)
    data_classes = normalized.get("data_classes")
    if not isinstance(data_classes, list) or not data_classes:
        data_classes = ["weather_measurement"]
    normalized["data_classes"] = [str(item) for item in data_classes if str(item)]
    normalized["batch_max_items"] = max(1, min(int(normalized.get("batch_max_items", 1)), 200))
    return normalized


def _migrate_legacy_outputs(local_cfg: Dict[str, Any]) -> Dict[str, Any]:
    cfg = copy.deepcopy(local_cfg)
    outputs = cfg.pop("outputs", None)
    telemetry = cfg.get("telemetry")
    destinations = []

    if isinstance(telemetry, dict) and isinstance(telemetry.get("destinations"), list) and telemetry.get("destinations"):
        return cfg

    if not isinstance(outputs, dict):
        return cfg

    webhook = outputs.get("webhook", {}) if isinstance(outputs.get("webhook"), dict) else {}
    mqtt = outputs.get("mqtt", {}) if isinstance(outputs.get("mqtt"), dict) else {}

    if webhook:
        destinations.append(
            normalize_destination(
                {
                    "id": "legacy-webhook",
                    "enabled": bool(webhook.get("enabled", False)),
                    "kind": "webhook_https",
                    "data_classes": ["weather_measurement"],
                    "schedule": {"mode": "realtime", "interval_seconds": 60},
                    "batch_max_items": 1,
                    "retry_policy": {"max_attempts": MAX_ATTEMPTS},
                    "auth": {"mode": "none", "key_id": "legacy-webhook"},
                    "webhook": {
                        "url": str(webhook.get("url") or ""),
                        "timeout_seconds": int(webhook.get("timeout_seconds", 5)),
                    },
                },
                index=0,
            )
        )

    if mqtt:
        destinations.append(
            normalize_destination(
                {
                    "id": "legacy-mqtt",
                    "enabled": bool(mqtt.get("enabled", False)),
                    "kind": "mqtt",
                    "data_classes": ["weather_measurement"],
                    "schedule": {"mode": "realtime", "interval_seconds": 60},
                    "batch_max_items": 1,
                    "retry_policy": {"max_attempts": MAX_ATTEMPTS},
                    "auth": {"mode": "basic", "key_id": "legacy-mqtt"},
                    "mqtt": {
                        "host": str(mqtt.get("host") or ""),
                        "port": int(mqtt.get("port", 1883)),
                        "topic": str(mqtt.get("topic") or "weatherpi/measurements"),
                        "tls": {"enabled": int(mqtt.get("port", 1883)) == 8883},
                    },
                },
                index=1,
            )
        )

    cfg["telemetry"] = deep_merge(DEFAULT_CONFIG["telemetry"], cfg.get("telemetry", {}))
    cfg["telemetry"]["destinations"] = destinations
    cfg["telemetry"]["enabled"] = any(dest.get("enabled") for dest in destinations)
    return cfg


def normalize_local_config(local_cfg: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = copy.deepcopy(local_cfg or {})
    cfg = _migrate_legacy_outputs(cfg)
    normalized = deep_merge(DEFAULT_CONFIG, cfg)
    normalized["collector"] = deep_merge(DEFAULT_CONFIG["collector"], normalized.get("collector", {}))
    normalized["telemetry"] = deep_merge(DEFAULT_CONFIG["telemetry"], normalized.get("telemetry", {}))
    normalized["alerts"] = deep_merge(DEFAULT_CONFIG["alerts"], normalized.get("alerts", {}))
    normalized["remote_config"] = deep_merge(DEFAULT_CONFIG["remote_config"], normalized.get("remote_config", {}))
    normalized["updates"] = deep_merge(DEFAULT_CONFIG["updates"], normalized.get("updates", {}))
    normalized["security"] = deep_merge(DEFAULT_CONFIG["security"], normalized.get("security", {}))
    normalized["exports"] = deep_merge(DEFAULT_CONFIG["exports"], normalized.get("exports", {}))
    normalized["exports"]["schedule"] = _normalize_exports_schedule(normalized["exports"].get("schedule"))
    normalized["ui"] = deep_merge(DEFAULT_CONFIG["ui"], normalized.get("ui", {}))

    try:
        normalized["sample_interval_seconds"] = int(normalized.get("sample_interval_seconds", 5))
    except Exception:
        normalized["sample_interval_seconds"] = 5
    normalized["sample_interval_seconds"] = max(1, min(normalized["sample_interval_seconds"], 3600))
    normalized["_rev"] = int(normalized.get("_rev", 0))
    normalized["station_id"] = str(normalized.get("station_id") or "meteo-001")
    normalized["collector"]["enabled"] = bool(normalized["collector"].get("enabled", True))
    normalized["collector"]["status_emit_interval_seconds"] = max(
        10, min(int(normalized["collector"].get("status_emit_interval_seconds", 60)), 86400)
    )
    normalized["telemetry"]["enabled"] = bool(normalized["telemetry"].get("enabled", False))
    destinations = normalized["telemetry"].get("destinations", [])
    if not isinstance(destinations, list):
        destinations = []
    normalized["telemetry"]["destinations"] = [
        normalize_destination(item, index=index) for index, item in enumerate(destinations)
    ]
    normalized["alerts"] = normalize_alerts_config(normalized.get("alerts"))
    normalized["remote_config"]["enabled"] = bool(normalized["remote_config"].get("enabled", False))
    normalized["remote_config"]["poll_interval_seconds"] = max(
        60, min(int(normalized["remote_config"].get("poll_interval_seconds", 900)), 86400)
    )
    normalized["remote_config"]["auth"] = _normalize_auth(normalized["remote_config"].get("auth"), default_mode="bearer")
    normalized["remote_config"]["tls"] = _normalize_tls(normalized["remote_config"].get("tls"))
    normalized["remote_config"]["signing"] = deep_merge(
        DEFAULT_CONFIG["remote_config"]["signing"], normalized["remote_config"].get("signing", {})
    )
    normalized["updates"]["enabled"] = bool(normalized["updates"].get("enabled", False))
    normalized["updates"]["poll_interval_seconds"] = max(
        300, min(int(normalized["updates"].get("poll_interval_seconds", 3600)), 7 * 24 * 3600)
    )
    normalized["updates"]["auth"] = _normalize_auth(normalized["updates"].get("auth"), default_mode="bearer")
    normalized["updates"]["tls"] = _normalize_tls(normalized["updates"].get("tls"))
    normalized["updates"]["signing"] = deep_merge(
        DEFAULT_CONFIG["updates"]["signing"], normalized["updates"].get("signing", {})
    )
    normalized["updates"]["maintenance_window"] = deep_merge(
        DEFAULT_CONFIG["updates"]["maintenance_window"], normalized["updates"].get("maintenance_window", {})
    )
    normalized["security"]["allow_lan"] = bool(normalized["security"].get("allow_lan", False))
    bind_host = str(normalized["security"].get("api_bind_host") or "127.0.0.1")
    normalized["security"]["api_bind_host"] = bind_host
    normalized["security"]["require_tls_for_remote"] = bool(normalized["security"].get("require_tls_for_remote", True))
    normalized["security"]["block_remote_when_default_local_credentials"] = bool(
        normalized["security"].get("block_remote_when_default_local_credentials", True)
    )
    return normalized


def sanitize_remote_overlay(overlay: Dict[str, Any] | None) -> Dict[str, Any]:
    raw = copy.deepcopy(overlay or {})
    sanitized: Dict[str, Any] = {}

    if "sample_interval_seconds" in raw:
        sanitized["sample_interval_seconds"] = raw["sample_interval_seconds"]

    collector = raw.get("collector")
    if isinstance(collector, dict):
        allowed_collector = {}
        if "enabled" in collector:
            allowed_collector["enabled"] = collector["enabled"]
        if "status_emit_interval_seconds" in collector:
            allowed_collector["status_emit_interval_seconds"] = collector["status_emit_interval_seconds"]
        if allowed_collector:
            sanitized["collector"] = allowed_collector

    telemetry = raw.get("telemetry")
    if isinstance(telemetry, dict):
        allowed_telemetry = {}
        if "enabled" in telemetry:
            allowed_telemetry["enabled"] = telemetry["enabled"]
        if "destinations" in telemetry:
            allowed_telemetry["destinations"] = telemetry["destinations"]
        if allowed_telemetry:
            sanitized["telemetry"] = allowed_telemetry

    updates = raw.get("updates")
    if isinstance(updates, dict):
        allowed_updates = {}
        for key in ("channel", "poll_interval_seconds", "auto_download", "apply_strategy", "maintenance_window"):
            if key in updates:
                allowed_updates[key] = updates[key]
        if allowed_updates:
            sanitized["updates"] = allowed_updates

    return sanitize_localish_config(sanitized)


def sanitize_localish_config(partial: Dict[str, Any]) -> Dict[str, Any]:
    merged = deep_merge({}, partial)
    if "telemetry" in merged and isinstance(merged["telemetry"], dict):
        destinations = merged["telemetry"].get("destinations")
        if isinstance(destinations, list):
            merged["telemetry"]["destinations"] = [normalize_destination(item, index=i) for i, item in enumerate(destinations)]
    if "alerts" in merged:
        merged["alerts"] = normalize_alerts_config(merged.get("alerts"))
    return merged


def build_effective_config(local_cfg: Dict[str, Any] | None, remote_overlay: Dict[str, Any] | None) -> Dict[str, Any]:
    local = normalize_local_config(local_cfg)
    overlay = sanitize_remote_overlay(remote_overlay)
    effective = deep_merge(local, overlay)
    effective = normalize_local_config(effective)
    return effective


def destination_map(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    telemetry = cfg.get("telemetry", {}) if isinstance(cfg.get("telemetry"), dict) else {}
    destinations = telemetry.get("destinations", []) if isinstance(telemetry.get("destinations"), list) else []
    return {str(dest.get("id")): normalize_destination(dest, index=index) for index, dest in enumerate(destinations)}


def is_loopback_host(hostname: str) -> bool:
    value = (hostname or "").strip().lower()
    if value in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return socket.gethostbyname(value).startswith("127.")
    except Exception:
        return False


def is_remote_https_or_loopback_http(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme == "https" and parsed.netloc:
        return True
    if parsed.scheme == "http" and parsed.hostname and is_loopback_host(parsed.hostname):
        return True
    return False


def resolve_api_bind_host(cfg: Dict[str, Any]) -> str:
    security = cfg.get("security", {}) if isinstance(cfg.get("security"), dict) else {}
    if not security.get("allow_lan", False):
        return "127.0.0.1"
    bind_host = str(security.get("api_bind_host") or "0.0.0.0").strip() or "0.0.0.0"
    return bind_host


def validate_local_config(cfg: Dict[str, Any]) -> str | None:
    try:
        normalized = normalize_local_config(cfg)
    except Exception as exc:
        return f"configuracion invalida: {exc}"

    if normalized["sample_interval_seconds"] < 1 or normalized["sample_interval_seconds"] > 3600:
        return "sample_interval_seconds debe estar entre 1 y 3600"

    destinations = normalized.get("telemetry", {}).get("destinations", [])
    seen_ids = set()
    for dest in destinations:
        dest_id = str(dest.get("id") or "")
        if not dest_id:
            return "telemetry.destinations[].id es obligatorio"
        if dest_id in seen_ids:
            return f"telemetry.destinations contiene ids duplicados: {dest_id}"
        seen_ids.add(dest_id)

        kind = dest.get("kind")
        if kind not in VALID_DESTINATION_KINDS:
            return f"telemetry.destinations[{dest_id}].kind no soportado"
        bad_classes = [item for item in dest.get("data_classes", []) if item not in VALID_DATA_CLASSES]
        if bad_classes:
            return f"telemetry.destinations[{dest_id}].data_classes contiene tipos no soportados"
        if kind == "webhook_https" and dest.get("enabled"):
            url = str(dest.get("webhook", {}).get("url") or "").strip()
            if not url:
                return f"telemetry.destinations[{dest_id}].webhook.url es obligatorio"
            if not is_remote_https_or_loopback_http(url):
                return f"telemetry.destinations[{dest_id}].webhook.url debe ser https:// o http:// loopback"
        if kind == "mqtt" and dest.get("enabled"):
            mqtt_cfg = dest.get("mqtt", {})
            if not str(mqtt_cfg.get("host") or "").strip():
                return f"telemetry.destinations[{dest_id}].mqtt.host es obligatorio"
            if not str(mqtt_cfg.get("topic") or "").strip():
                return f"telemetry.destinations[{dest_id}].mqtt.topic es obligatorio"

    remote_cfg = normalized.get("remote_config", {})
    if remote_cfg.get("enabled"):
        endpoint = str(remote_cfg.get("endpoint") or "").strip()
        if not endpoint:
            return "remote_config.endpoint es obligatorio cuando remote_config.enabled=true"
        if not is_remote_https_or_loopback_http(endpoint):
            return "remote_config.endpoint debe ser https:// o http:// loopback"

    updates = normalized.get("updates", {})
    if updates.get("enabled"):
        manifest_url = str(updates.get("manifest_url") or "").strip()
        if not manifest_url:
            return "updates.manifest_url es obligatorio cuando updates.enabled=true"
        if not is_remote_https_or_loopback_http(manifest_url):
            return "updates.manifest_url debe ser https:// o http:// loopback"

    alerts_error = validate_alerts_config(normalized.get("alerts"))
    if alerts_error:
        return alerts_error

    return None


def iter_matching_destinations(cfg: Dict[str, Any], data_class: str) -> Iterable[Dict[str, Any]]:
    telemetry = cfg.get("telemetry", {}) if isinstance(cfg.get("telemetry"), dict) else {}
    if not telemetry.get("enabled", False):
        return []
    destinations = telemetry.get("destinations", []) if isinstance(telemetry.get("destinations"), list) else []
    return [
        dest
        for dest in destinations
        if bool(dest.get("enabled", False)) and data_class in (dest.get("data_classes") or [])
    ]
