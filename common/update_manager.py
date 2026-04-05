from __future__ import annotations

import json
import os
import shutil
import tarfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple
from urllib.parse import urljoin

import httpx

from common.config import (
    CURRENT_RELEASE_LINK,
    DOWNLOADS_DIR,
    PREVIOUS_RELEASE_LINK,
    RELEASES_DIR,
    RUNTIME_STATE_DIR,
    SECRETS_PATH,
    VERSIONS_DIR,
)
from common.db import add_release_history, get_config, get_update_state, set_update_state
from common.secrets import get_service_secrets
from common.security import build_http_auth_headers, remote_operations_block_reason, sha256_hex, verify_signature


def _version_tuple(value: str) -> Tuple[int, ...]:
    if not value:
        return (0,)
    parts = []
    for chunk in str(value).replace("-", ".").split("."):
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            digits = "".join(ch for ch in chunk if ch.isdigit())
            if digits:
                parts.append(int(digits))
    return tuple(parts or [0])


def _is_newer_version(current_version: str, target_version: str) -> bool:
    return _version_tuple(target_version) > _version_tuple(current_version)


def _get_verify_value(tls_cfg: Dict[str, Any]) -> str | bool:
    ca_bundle_path = str((tls_cfg or {}).get("ca_bundle_path") or "").strip()
    return ca_bundle_path or True


def _http_get_json(
    *,
    url: str,
    station_id: str,
    auth_cfg: Dict[str, Any],
    tls_cfg: Dict[str, Any],
    secrets_bundle: Dict[str, Any],
) -> Dict[str, Any]:
    headers = build_http_auth_headers(
        auth_cfg=auth_cfg,
        secret_bundle=secrets_bundle,
        station_id=station_id,
        method="GET",
        url=url,
        body_bytes=b"",
        idempotency_key="",
    )
    with httpx.Client(timeout=30, verify=_get_verify_value(tls_cfg)) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("update endpoint must return a JSON object")
    return payload


def _download_binary(
    *,
    url: str,
    station_id: str,
    auth_cfg: Dict[str, Any],
    tls_cfg: Dict[str, Any],
    secrets_bundle: Dict[str, Any],
    target_path: Path,
) -> bytes:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    headers = build_http_auth_headers(
        auth_cfg=auth_cfg,
        secret_bundle=secrets_bundle,
        station_id=station_id,
        method="GET",
        url=url,
        body_bytes=b"",
        idempotency_key="",
    )
    with httpx.Client(timeout=60, verify=_get_verify_value(tls_cfg)) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        content = response.content
    target_path.write_bytes(content)
    return content


def _ensure_within(base: Path, candidate: Path) -> None:
    if not candidate.resolve().is_relative_to(base.resolve()):
        raise RuntimeError(f"unsafe archive entry outside {base}")


def _extract_archive(artifact_path: Path, version: str) -> Path:
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    version_dir = (VERSIONS_DIR / version).resolve()
    _ensure_within(VERSIONS_DIR, version_dir)
    if version_dir.exists():
        shutil.rmtree(version_dir)
    version_dir.mkdir(parents=True, exist_ok=True)

    if zipfile.is_zipfile(artifact_path):
        with zipfile.ZipFile(artifact_path, "r") as archive:
            for member in archive.infolist():
                target = version_dir / member.filename
                _ensure_within(version_dir, target)
            archive.extractall(version_dir)
        return version_dir

    if tarfile.is_tarfile(artifact_path):
        with tarfile.open(artifact_path, "r:*") as archive:
            for member in archive.getmembers():
                target = version_dir / member.name
                _ensure_within(version_dir, target)
            archive.extractall(version_dir)
        return version_dir

    raise RuntimeError("unsupported update artifact format; expected zip or tar archive")


def _symlink_target_name(path: Path) -> str:
    try:
        return path.resolve().name
    except Exception:
        return path.name


def _write_runtime_flag(filename: str, payload: Dict[str, Any]) -> None:
    RUNTIME_STATE_DIR.mkdir(parents=True, exist_ok=True)
    (RUNTIME_STATE_DIR / filename).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _swap_release_links(version_dir: Path) -> None:
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    current_target = CURRENT_RELEASE_LINK.resolve() if CURRENT_RELEASE_LINK.is_symlink() else None

    if current_target:
        if PREVIOUS_RELEASE_LINK.exists() or PREVIOUS_RELEASE_LINK.is_symlink():
            PREVIOUS_RELEASE_LINK.unlink()
        PREVIOUS_RELEASE_LINK.symlink_to(current_target, target_is_directory=True)

    if CURRENT_RELEASE_LINK.exists() or CURRENT_RELEASE_LINK.is_symlink():
        CURRENT_RELEASE_LINK.unlink()
    CURRENT_RELEASE_LINK.symlink_to(version_dir, target_is_directory=True)


def check_for_updates(force: bool = False) -> Dict[str, Any]:
    cfg = get_config()
    station_id = str(cfg.get("station_id") or "meteo-001")
    updates_cfg = cfg.get("updates", {}) if isinstance(cfg.get("updates"), dict) else {}
    now_ts = int(time.time())
    security_cfg = cfg.get("security", {}) if isinstance(cfg.get("security"), dict) else {}

    if not updates_cfg.get("enabled", False):
        return set_update_state(status="idle", last_error="", last_check_ts=now_ts)

    block_reason = remote_operations_block_reason(
        enforce=bool(security_cfg.get("block_remote_when_default_local_credentials", True)),
        secret_store_exists=SECRETS_PATH.exists(),
    )
    if block_reason:
        return set_update_state(status="failed", last_error=block_reason, last_check_ts=now_ts)

    manifest_url = str(updates_cfg.get("manifest_url") or "").strip()
    if not manifest_url:
        return set_update_state(status="failed", last_error="updates.manifest_url is empty", last_check_ts=now_ts)

    current_state = get_update_state()
    current_version = str(current_state.get("current_version") or "")
    secrets_bundle = get_service_secrets("updates")

    try:
        manifest = _http_get_json(
            url=manifest_url,
            station_id=station_id,
            auth_cfg=updates_cfg.get("auth", {}),
            tls_cfg=updates_cfg.get("tls", {}),
            secrets_bundle=secrets_bundle,
        )
        version = str(manifest.get("version") or "").strip()
        channel = str(manifest.get("channel") or updates_cfg.get("channel") or "stable").strip()
        artifact_url = str(manifest.get("artifact_url") or "").strip()
        expected_sha256 = str(manifest.get("sha256") or "").strip()
        if not version or not artifact_url:
            raise RuntimeError("update manifest requires version and artifact_url")
        if channel and channel != str(updates_cfg.get("channel") or "stable"):
            return set_update_state(
                status="idle",
                channel=channel,
                target_version=version,
                artifact_url=artifact_url,
                last_check_ts=now_ts,
                last_error="",
            )
        if not force and not _is_newer_version(current_version, version):
            return set_update_state(
                status="idle",
                channel=channel,
                target_version=version,
                artifact_url=artifact_url,
                last_check_ts=now_ts,
                last_error="",
            )

        next_state = set_update_state(
            status="available",
            channel=channel,
            target_version=version,
            artifact_url=artifact_url,
            last_check_ts=now_ts,
            last_error="",
        )

        if not bool(updates_cfg.get("auto_download", True)):
            return next_state

        set_update_state(
            status="downloading",
            channel=channel,
            target_version=version,
            artifact_url=artifact_url,
            last_check_ts=now_ts,
            last_error="",
        )
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        extension = Path(artifact_url).suffix or ".bundle"
        download_path = DOWNLOADS_DIR / f"{version}{extension}"
        content = _download_binary(
            url=urljoin(manifest_url, artifact_url),
            station_id=station_id,
            auth_cfg=updates_cfg.get("auth", {}),
            tls_cfg=updates_cfg.get("tls", {}),
            secrets_bundle=secrets_bundle,
            target_path=download_path,
        )
        actual_sha256 = sha256_hex(content)
        if expected_sha256 and actual_sha256 != expected_sha256:
            raise RuntimeError("update artifact sha256 mismatch")

        signing_cfg = updates_cfg.get("signing", {}) if isinstance(updates_cfg.get("signing"), dict) else {}
        signature_algorithm = str(manifest.get("signature_algorithm") or signing_cfg.get("algorithm") or "ed25519")
        signature_value = str(manifest.get("signature") or "").strip()
        signature_required = bool(signing_cfg.get("required", True))
        signature_ok = True
        if signature_required:
            signature_ok, reason = verify_signature(
                data_bytes=content,
                signature=signature_value,
                algorithm=signature_algorithm,
                public_key=str(signing_cfg.get("public_key") or ""),
                shared_secret=str(secrets_bundle.get("signature_secret") or secrets_bundle.get("hmac_secret") or ""),
            )
            if not signature_ok:
                raise RuntimeError(reason or "update signature verification failed")

        return set_update_state(
            status="verified",
            channel=channel,
            target_version=version,
            artifact_url=artifact_url,
            download_path=str(download_path),
            sha256=actual_sha256,
            signature_ok=signature_ok,
            last_check_ts=now_ts,
            last_error="",
        )
    except Exception as exc:
        return set_update_state(status="failed", last_check_ts=now_ts, last_error=str(exc))


def apply_staged_update() -> Dict[str, Any]:
    state = get_update_state()
    version = str(state.get("target_version") or "").strip()
    download_path = str(state.get("download_path") or "").strip()
    if not version:
        raise RuntimeError("no target version staged")
    if not download_path:
        raise RuntimeError("no downloaded artifact staged")
    artifact_path = Path(download_path)
    if not artifact_path.exists():
        raise RuntimeError("downloaded artifact is missing")

    version_dir = _extract_archive(artifact_path, version)
    if os.name == "nt":
        add_release_history("apply", version, "blocked", {"reason": "linux-only runtime flow"})
        raise RuntimeError("apply is supported on Linux/Raspberry runtime only")

    _swap_release_links(version_dir)
    _write_runtime_flag(
        "restart_required.json",
        {
            "action": "apply",
            "version": version,
            "version_dir": str(version_dir),
            "requested_ts": int(time.time()),
        },
    )
    add_release_history("apply", version, "ok", {"version_dir": str(version_dir)})
    return set_update_state(
        current_version=version,
        status="applied",
        applied_ts=int(time.time()),
        last_error="",
    )


def rollback_update() -> Dict[str, Any]:
    if os.name == "nt":
        add_release_history("rollback", "", "blocked", {"reason": "linux-only runtime flow"})
        raise RuntimeError("rollback is supported on Linux/Raspberry runtime only")
    if not PREVIOUS_RELEASE_LINK.exists() or not PREVIOUS_RELEASE_LINK.is_symlink():
        raise RuntimeError("no previous release staged for rollback")

    previous_target = PREVIOUS_RELEASE_LINK.resolve()
    if CURRENT_RELEASE_LINK.exists() or CURRENT_RELEASE_LINK.is_symlink():
        CURRENT_RELEASE_LINK.unlink()
    CURRENT_RELEASE_LINK.symlink_to(previous_target, target_is_directory=True)
    previous_version = _symlink_target_name(previous_target)
    _write_runtime_flag(
        "restart_required.json",
        {
            "action": "rollback",
            "version": previous_version,
            "version_dir": str(previous_target),
            "requested_ts": int(time.time()),
        },
    )
    add_release_history("rollback", previous_version, "ok", {"version_dir": str(previous_target)})
    return set_update_state(
        current_version=previous_version,
        target_version=previous_version,
        status="rollback",
        applied_ts=int(time.time()),
        last_error="",
    )
