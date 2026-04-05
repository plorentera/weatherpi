from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from common.config import DATA_DIR, LOCAL_AUTH_PATH
from common.security import generate_session_secret, hash_password_pbkdf2, verify_password_hash

READER_USER_ENV = "WEATHERPI_READER_USER"
READER_PASS_ENV = "WEATHERPI_READER_PASS"
READER_PASS_HASH_ENV = "WEATHERPI_READER_PASS_HASH"
ADMIN_USER_ENV = "WEATHERPI_ADMIN_USER"
ADMIN_PASS_ENV = "WEATHERPI_ADMIN_PASS"
ADMIN_PASS_HASH_ENV = "WEATHERPI_ADMIN_PASS_HASH"
SESSION_SECRET_ENV = "WEATHERPI_SESSION_SECRET"
DEFAULT_READER_USERNAME = "reader"
DEFAULT_READER_PASSWORD = "reader"
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"
DEFAULT_SESSION_SECRET = "change-me-weatherpi-session-secret"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _apply_permissions(path: Path) -> None:
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass


def _session_secret_from_env() -> str:
    env_secret = os.getenv(SESSION_SECRET_ENV, DEFAULT_SESSION_SECRET).strip() or DEFAULT_SESSION_SECRET
    if len(env_secret) >= 32 and env_secret != DEFAULT_SESSION_SECRET:
        return env_secret
    return generate_session_secret()


def _hash_from_env(password_hash_env: str, password_env: str, default_password: str) -> str:
    raw_hash = os.getenv(password_hash_env, "").strip()
    if raw_hash:
        return raw_hash
    plain_password = os.getenv(password_env, default_password).strip() or default_password
    return hash_password_pbkdf2(plain_password)


def build_initial_local_auth_store() -> Dict[str, Any]:
    return {
        "version": 1,
        "reader_username": os.getenv(READER_USER_ENV, DEFAULT_READER_USERNAME).strip() or DEFAULT_READER_USERNAME,
        "reader_password_hash": _hash_from_env(READER_PASS_HASH_ENV, READER_PASS_ENV, DEFAULT_READER_PASSWORD),
        "admin_username": os.getenv(ADMIN_USER_ENV, DEFAULT_ADMIN_USERNAME).strip() or DEFAULT_ADMIN_USERNAME,
        "admin_password_hash": _hash_from_env(ADMIN_PASS_HASH_ENV, ADMIN_PASS_ENV, DEFAULT_ADMIN_PASSWORD),
        "session_secret": _session_secret_from_env(),
        "updated_ts": int(time.time()),
    }


def save_local_auth_store(store: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_parent(LOCAL_AUTH_PATH)
    payload = {
        "version": 1,
        "reader_username": str(store.get("reader_username") or DEFAULT_READER_USERNAME).strip() or DEFAULT_READER_USERNAME,
        "reader_password_hash": str(store.get("reader_password_hash") or ""),
        "admin_username": str(store.get("admin_username") or DEFAULT_ADMIN_USERNAME).strip() or DEFAULT_ADMIN_USERNAME,
        "admin_password_hash": str(store.get("admin_password_hash") or ""),
        "session_secret": str(store.get("session_secret") or generate_session_secret()),
        "updated_ts": int(store.get("updated_ts") or int(time.time())),
    }
    LOCAL_AUTH_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    _apply_permissions(LOCAL_AUTH_PATH)
    return payload


def load_local_auth_store() -> Dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not LOCAL_AUTH_PATH.exists():
        return save_local_auth_store(build_initial_local_auth_store())

    try:
        raw = json.loads(LOCAL_AUTH_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}

    store = build_initial_local_auth_store()
    if isinstance(raw, dict):
        store.update(raw)

    if not str(store.get("session_secret") or "").strip() or str(store.get("session_secret")) == DEFAULT_SESSION_SECRET:
        store["session_secret"] = generate_session_secret()
    if not str(store.get("reader_password_hash") or "").strip():
        store["reader_password_hash"] = hash_password_pbkdf2(DEFAULT_READER_PASSWORD)
    if not str(store.get("admin_password_hash") or "").strip():
        store["admin_password_hash"] = hash_password_pbkdf2(DEFAULT_ADMIN_PASSWORD)
    store["updated_ts"] = int(store.get("updated_ts") or int(time.time()))

    normalized = {
        "version": 1,
        "reader_username": str(store.get("reader_username") or DEFAULT_READER_USERNAME).strip() or DEFAULT_READER_USERNAME,
        "reader_password_hash": str(store.get("reader_password_hash") or ""),
        "admin_username": str(store.get("admin_username") or DEFAULT_ADMIN_USERNAME).strip() or DEFAULT_ADMIN_USERNAME,
        "admin_password_hash": str(store.get("admin_password_hash") or ""),
        "session_secret": str(store.get("session_secret") or generate_session_secret()),
        "updated_ts": int(store.get("updated_ts") or int(time.time())),
    }

    if raw != normalized:
        return save_local_auth_store(normalized)
    return normalized


def _role_uses_defaults(username: str, password_hash: str, default_username: str, default_password: str) -> bool:
    if str(username or "").strip() != default_username:
        return False
    return verify_password_hash(default_password, str(password_hash or ""))


def load_local_auth_status() -> Dict[str, Any]:
    store = load_local_auth_store()
    reader_default = _role_uses_defaults(
        store.get("reader_username"),
        store.get("reader_password_hash"),
        DEFAULT_READER_USERNAME,
        DEFAULT_READER_PASSWORD,
    )
    admin_default = _role_uses_defaults(
        store.get("admin_username"),
        store.get("admin_password_hash"),
        DEFAULT_ADMIN_USERNAME,
        DEFAULT_ADMIN_PASSWORD,
    )
    session_secret = str(store.get("session_secret") or "")
    session_secret_strong = len(session_secret) >= 32 and session_secret != DEFAULT_SESSION_SECRET
    return {
        "path": str(LOCAL_AUTH_PATH),
        "exists": LOCAL_AUTH_PATH.exists(),
        "reader_username": str(store.get("reader_username") or DEFAULT_READER_USERNAME),
        "admin_username": str(store.get("admin_username") or DEFAULT_ADMIN_USERNAME),
        "reader_default": reader_default,
        "admin_default": admin_default,
        "default_credentials_active": reader_default or admin_default,
        "session_secret_strong": session_secret_strong,
        "updated_ts": int(store.get("updated_ts") or 0),
    }


def get_session_secret() -> str:
    return str(load_local_auth_store().get("session_secret") or generate_session_secret())


def authenticate_local_user(username: str, password: str) -> Optional[str]:
    store = load_local_auth_store()
    candidate_user = str(username or "").strip()
    if candidate_user == str(store.get("admin_username") or "") and verify_password_hash(
        password, str(store.get("admin_password_hash") or "")
    ):
        return "admin"
    if candidate_user == str(store.get("reader_username") or "") and verify_password_hash(
        password, str(store.get("reader_password_hash") or "")
    ):
        return "reader"
    return None


def update_local_auth_store(
    *,
    reader_username: str,
    admin_username: str,
    reader_password: str | None = None,
    admin_password: str | None = None,
) -> Dict[str, Any]:
    store = load_local_auth_store()
    store["reader_username"] = str(reader_username or DEFAULT_READER_USERNAME).strip() or DEFAULT_READER_USERNAME
    store["admin_username"] = str(admin_username or DEFAULT_ADMIN_USERNAME).strip() or DEFAULT_ADMIN_USERNAME
    if reader_password:
        store["reader_password_hash"] = hash_password_pbkdf2(reader_password)
    if admin_password:
        store["admin_password_hash"] = hash_password_pbkdf2(admin_password)
    store["updated_ts"] = int(time.time())
    return save_local_auth_store(store)
