from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from common.config import DATA_DIR, SECRETS_PATH
from common.security import mask_secret


DEFAULT_SECRET_STORE: Dict[str, Any] = {
    "telemetry_destinations": {},
    "remote_config": {},
    "updates": {},
}


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _apply_permissions(path: Path) -> None:
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass


def load_secret_store() -> Dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SECRETS_PATH.exists():
        return json.loads(json.dumps(DEFAULT_SECRET_STORE))

    try:
        raw = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}

    store = json.loads(json.dumps(DEFAULT_SECRET_STORE))
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(store.get(key), dict) and isinstance(value, dict):
                store[key].update(value)
            else:
                store[key] = value
    return store


def save_secret_store(store: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_parent(SECRETS_PATH)
    payload = json.loads(json.dumps(DEFAULT_SECRET_STORE))
    if isinstance(store, dict):
        for key, value in store.items():
            if isinstance(payload.get(key), dict) and isinstance(value, dict):
                payload[key].update(value)
            else:
                payload[key] = value
    SECRETS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    _apply_permissions(SECRETS_PATH)
    return payload


def update_secret_store(patch: Dict[str, Any]) -> Dict[str, Any]:
    current = load_secret_store()
    for key, value in patch.items():
        if isinstance(current.get(key), dict) and isinstance(value, dict):
            current[key].update(value)
        else:
            current[key] = value
    return save_secret_store(current)


def get_destination_secrets(destination_id: str) -> Dict[str, Any]:
    store = load_secret_store()
    bundle = store.get("telemetry_destinations", {}).get(destination_id, {})
    return bundle if isinstance(bundle, dict) else {}


def set_destination_secrets(destination_id: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
    store = load_secret_store()
    telemetry = store.setdefault("telemetry_destinations", {})
    telemetry[destination_id] = bundle or {}
    return save_secret_store(store)


def get_service_secrets(service_name: str) -> Dict[str, Any]:
    store = load_secret_store()
    bundle = store.get(service_name, {})
    return bundle if isinstance(bundle, dict) else {}


def set_service_secrets(service_name: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
    store = load_secret_store()
    store[service_name] = bundle or {}
    return save_secret_store(store)


def build_public_secret_view() -> Dict[str, Any]:
    store = load_secret_store()
    telemetry = store.get("telemetry_destinations", {})
    public_destinations = {}
    for destination_id, bundle in telemetry.items():
        if not isinstance(bundle, dict):
            continue
        public_destinations[destination_id] = {
            key: {
                "configured": bool(value),
                "masked": mask_secret(str(value)) if isinstance(value, str) else "",
            }
            for key, value in bundle.items()
            if isinstance(value, (str, int, float)) and key not in {"timestamp_override"}
        }

    public_services = {}
    for name in ("remote_config", "updates"):
        bundle = store.get(name, {})
        if not isinstance(bundle, dict):
            continue
        public_services[name] = {
            key: {
                "configured": bool(value),
                "masked": mask_secret(str(value)) if isinstance(value, str) else "",
            }
            for key, value in bundle.items()
            if isinstance(value, (str, int, float))
        }

    return {
        "path": str(SECRETS_PATH),
        "exists": SECRETS_PATH.exists(),
        "telemetry_destinations": public_destinations,
        "services": public_services,
    }
