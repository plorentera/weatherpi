from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from typing import Any, Dict, Optional
from urllib.parse import urlparse

PBKDF2_SCHEME = "pbkdf2_sha256"


def canonical_json_bytes(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(data: Any) -> str:
    return sha256_hex(canonical_json_bytes(data))


def short_stable_id(*parts: Any, length: int = 24) -> str:
    raw = "||".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    return "*" * max(4, len(value) - visible) + value[-visible:]


def _b64decode_loose(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _b64encode_loose(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def hash_password_pbkdf2(password: str, *, iterations: int = 260_000) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{PBKDF2_SCHEME}${iterations}${_b64encode_loose(salt)}${_b64encode_loose(digest)}"


def verify_password_hash(password: str, stored_hash: str) -> bool:
    try:
        scheme, iter_raw, salt_raw, digest_raw = stored_hash.split("$", 3)
        if scheme != PBKDF2_SCHEME:
            return False
        iterations = int(iter_raw)
        if iterations < 100_000:
            return False
        salt = _b64decode_loose(salt_raw)
        expected = _b64decode_loose(digest_raw)
    except Exception:
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return secrets.compare_digest(actual, expected)


def generate_session_secret(length: int = 48) -> str:
    return secrets.token_urlsafe(length)


def verify_signature(
    *,
    data_bytes: bytes,
    signature: str,
    algorithm: str,
    public_key: str = "",
    shared_secret: str = "",
) -> tuple[bool, str]:
    algo = (algorithm or "").strip().lower()
    if algo in {"", "none"}:
        return True, ""

    if not signature:
        return False, "signature missing"

    if algo == "hmac-sha256":
        if not shared_secret:
            return False, "missing shared secret for hmac verification"
        expected = hmac.new(shared_secret.encode("utf-8"), data_bytes, hashlib.sha256).hexdigest()
        return secrets.compare_digest(expected, signature), "" if secrets.compare_digest(expected, signature) else "invalid hmac signature"

    if algo == "ed25519":
        if not public_key:
            return False, "missing public key for ed25519 verification"
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        except Exception:
            return False, "cryptography package is required for ed25519 verification"

        try:
            if "BEGIN PUBLIC KEY" in public_key:
                key = serialization.load_pem_public_key(public_key.encode("utf-8"))
            else:
                key_bytes = _b64decode_loose(public_key)
                key = Ed25519PublicKey.from_public_bytes(key_bytes)
            sig_bytes = _b64decode_loose(signature)
            key.verify(sig_bytes, data_bytes)
            return True, ""
        except Exception as exc:
            return False, f"invalid ed25519 signature: {exc}"

    return False, f"unsupported signature algorithm: {algorithm}"


def build_http_auth_headers(
    *,
    auth_cfg: Dict[str, Any],
    secret_bundle: Dict[str, Any],
    station_id: str,
    method: str,
    url: str,
    body_bytes: bytes,
    idempotency_key: str = "",
) -> Dict[str, str]:
    mode = str((auth_cfg or {}).get("mode") or "none").strip().lower()
    key_id = str((auth_cfg or {}).get("key_id") or "").strip()
    headers: Dict[str, str] = {}

    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    headers["X-WeatherPi-Station"] = station_id
    if key_id:
        headers["X-WeatherPi-Key-Id"] = key_id

    if mode == "none":
        return headers

    if mode == "bearer":
        token = str(secret_bundle.get("bearer_token") or "").strip()
        if not token:
            raise RuntimeError("missing bearer token in secret store")
        headers["Authorization"] = f"Bearer {token}"
        return headers

    if mode == "hmac":
        secret_value = str(secret_bundle.get("hmac_secret") or "").strip()
        if not secret_value:
            raise RuntimeError("missing hmac secret in secret store")
        parsed = urlparse(url)
        body_hash = sha256_hex(body_bytes)
        timestamp = str(int(secret_bundle.get("timestamp_override") or 0) or __import__("time").time_ns() // 1_000_000_000)
        signing_input = "\n".join(
            [
                method.upper(),
                parsed.path or "/",
                parsed.query or "",
                timestamp,
                body_hash,
                station_id,
            ]
        ).encode("utf-8")
        signature = hmac.new(secret_value.encode("utf-8"), signing_input, hashlib.sha256).hexdigest()
        headers["X-WeatherPi-Timestamp"] = timestamp
        headers["X-WeatherPi-Algorithm"] = "hmac-sha256"
        headers["X-WeatherPi-Signature"] = signature
        return headers

    raise RuntimeError(f"unsupported HTTP auth mode: {mode}")


def basic_auth_tuple(secret_bundle: Dict[str, Any]) -> Optional[tuple[str, str]]:
    username = str(secret_bundle.get("username") or "").strip()
    password = str(secret_bundle.get("password") or "").strip()
    if not username and not password:
        return None
    return username, password


def local_default_credentials_active() -> bool:
    from common.local_auth import load_local_auth_status

    status = load_local_auth_status()
    return bool(status.get("default_credentials_active", True))


def remote_operations_block_reason(*, enforce: bool, secret_store_exists: bool) -> str | None:
    if not enforce:
        return None
    if local_default_credentials_active():
        return "local default credentials still active"
    if not secret_store_exists:
        return "secret store missing"
    return None
