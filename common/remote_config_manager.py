from __future__ import annotations

import json
import time
from typing import Any, Dict, Tuple
from urllib.parse import urljoin

import httpx

from common.alerts import alert_config_hash, normalize_alerts_config, validate_alerts_config
from common.config import sanitize_remote_overlay
from common.config import SECRETS_PATH
from common.db import (
    apply_remote_alerts_config,
    clear_remote_overlay,
    delete_setting,
    get_alert_config_state,
    get_config,
    get_remote_config_state,
    get_setting,
    set_alert_config_state,
    set_remote_config_state,
    set_remote_overlay,
    set_setting,
)
from common.secrets import get_service_secrets
from common.security import build_http_auth_headers, remote_operations_block_reason, sha256_hex, verify_signature


def _get_verify_value(tls_cfg: Dict[str, Any]) -> str | bool:
    ca_bundle_path = str((tls_cfg or {}).get("ca_bundle_path") or "").strip()
    return ca_bundle_path or True


def _fetch_json_document(
    *,
    url: str,
    station_id: str,
    auth_cfg: Dict[str, Any],
    tls_cfg: Dict[str, Any],
    secrets_bundle: Dict[str, Any],
) -> Tuple[Dict[str, Any], bytes]:
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
        body_bytes = response.content
        payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("remote config endpoint must return a JSON object")
    return payload, body_bytes


def _post_json_document(
    *,
    url: str,
    station_id: str,
    auth_cfg: Dict[str, Any],
    tls_cfg: Dict[str, Any],
    secrets_bundle: Dict[str, Any],
    payload: Dict[str, Any],
) -> int:
    body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    headers = build_http_auth_headers(
        auth_cfg=auth_cfg,
        secret_bundle=secrets_bundle,
        station_id=station_id,
        method="POST",
        url=url,
        body_bytes=body_bytes,
        idempotency_key="",
    )
    headers["Content-Type"] = "application/json"
    with httpx.Client(timeout=30, verify=_get_verify_value(tls_cfg)) as client:
        response = client.post(url, content=body_bytes, headers=headers)
        response.raise_for_status()
        return response.status_code


def _alerts_remote_payload(manifest: Dict[str, Any]) -> tuple[Dict[str, Any] | None, str, str]:
    raw = manifest.get("alerts")
    if not isinstance(raw, dict):
        return None, "", ""

    if isinstance(raw.get("config"), dict):
        payload = raw.get("config")
    else:
        payload = {key: value for key, value in raw.items() if key not in {"revision", "status_url", "report_url"}}

    if not isinstance(payload, dict):
        return None, "", ""

    revision = str(raw.get("revision") or manifest.get("revision") or manifest.get("config_revision") or "")
    status_url = str(raw.get("status_url") or raw.get("report_url") or "").strip()
    return payload, revision, status_url


def _report_alert_sync_status(
    *,
    station_id: str,
    remote_cfg: Dict[str, Any],
    secrets_bundle: Dict[str, Any],
    status_url: str,
) -> None:
    if not status_url:
        return

    state = get_alert_config_state()
    payload = {
        "station_id": station_id,
        "desired_revision": state.get("desired_revision"),
        "applied_revision": state.get("applied_revision"),
        "sync_status": state.get("sync_status"),
        "last_change_source": state.get("source"),
        "config_hash": state.get("applied_hash"),
        "desired_hash": state.get("desired_hash"),
        "last_error": state.get("last_error"),
        "reported_at": int(time.time()),
    }
    _post_json_document(
        url=status_url,
        station_id=station_id,
        auth_cfg=remote_cfg.get("auth", {}),
        tls_cfg=remote_cfg.get("tls", {}),
        secrets_bundle=secrets_bundle,
        payload=payload,
    )
    set_alert_config_state(last_report_ts=int(time.time()), status_endpoint=status_url)


def apply_staged_remote_config() -> Dict[str, Any]:
    state = get_remote_config_state()
    raw = get_setting("remote_config_staged")
    if not raw:
        raise RuntimeError("no staged remote configuration")
    try:
        overlay = json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"invalid staged remote configuration: {exc}") from exc

    set_remote_overlay(
        overlay,
        revision=str(state.get("staged_revision") or state.get("current_revision") or ""),
        sha256=str(state.get("sha256") or ""),
        signature_ok=bool(state.get("signature_ok")),
        status="applied",
        endpoint=str(state.get("endpoint") or ""),
        last_error="",
    )
    delete_setting("remote_config_staged")
    return get_remote_config_state()


def run_remote_config_check(force: bool = False) -> Dict[str, Any]:
    cfg = get_config()
    station_id = str(cfg.get("station_id") or "meteo-001")
    remote_cfg = cfg.get("remote_config", {}) if isinstance(cfg.get("remote_config"), dict) else {}
    current_state = get_remote_config_state()
    now_ts = int(time.time())
    security_cfg = cfg.get("security", {}) if isinstance(cfg.get("security"), dict) else {}

    if not remote_cfg.get("enabled", False):
        clear_remote_overlay(status="idle", last_error="")
        return set_remote_config_state(status="idle", endpoint="", last_check_ts=now_ts)

    block_reason = remote_operations_block_reason(
        enforce=bool(security_cfg.get("block_remote_when_default_local_credentials", True)),
        secret_store_exists=SECRETS_PATH.exists(),
    )
    if block_reason:
        return set_remote_config_state(status="failed", endpoint=str(remote_cfg.get("endpoint") or ""), last_error=block_reason, last_check_ts=now_ts)

    endpoint = str(remote_cfg.get("endpoint") or "").strip()
    if not endpoint:
        return set_remote_config_state(status="failed", endpoint="", last_error="remote_config.endpoint is empty", last_check_ts=now_ts)

    secrets_bundle = get_service_secrets("remote_config")

    try:
        manifest, _ = _fetch_json_document(
            url=endpoint,
            station_id=station_id,
            auth_cfg=remote_cfg.get("auth", {}),
            tls_cfg=remote_cfg.get("tls", {}),
            secrets_bundle=secrets_bundle,
        )
        revision = str(manifest.get("revision") or manifest.get("config_revision") or "")
        if not revision:
            raise RuntimeError("remote config manifest is missing revision")
        remote_alert_state = get_alert_config_state()
        status_url = str(remote_alert_state.get("status_endpoint") or "").strip()
        alerts_payload, alerts_revision, manifest_status_url = _alerts_remote_payload(manifest)
        if manifest_status_url:
            status_url = manifest_status_url
        known_alert_revision = str(
            remote_alert_state.get("desired_revision")
            or remote_alert_state.get("applied_revision")
            or ""
        )
        alert_revision_changed = bool(alerts_payload is not None and str(alerts_revision or revision) != known_alert_revision)

        if not force and revision == str(current_state.get("current_revision") or "") and not alert_revision_changed:
            state = set_remote_config_state(
                status="idle",
                endpoint=endpoint,
                last_error="",
                staged_revision=revision,
                current_revision=revision,
                last_check_ts=now_ts,
            )
            set_alert_config_state(
                last_remote_check_ts=now_ts,
                remote_endpoint=endpoint,
                status_endpoint=status_url,
            )
            if status_url:
                try:
                    _report_alert_sync_status(
                        station_id=station_id,
                        remote_cfg=remote_cfg,
                        secrets_bundle=secrets_bundle,
                        status_url=status_url,
                    )
                except Exception as report_exc:
                    set_alert_config_state(last_error=f"report failed: {report_exc}", status_endpoint=status_url)
            return state

        if "config" in manifest:
            payload = manifest["config"]
            payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        else:
            config_url = str(manifest.get("config_url") or "").strip()
            if not config_url:
                payload = {}
                payload_bytes = b"{}"
            else:
                payload, payload_bytes = _fetch_json_document(
                    url=urljoin(endpoint, config_url),
                    station_id=station_id,
                    auth_cfg=remote_cfg.get("auth", {}),
                    tls_cfg=remote_cfg.get("tls", {}),
                    secrets_bundle=secrets_bundle,
                )

        if not isinstance(payload, dict):
            raise RuntimeError("remote config payload must be a JSON object")

        sha256_value = sha256_hex(payload_bytes)
        expected_sha256 = str(manifest.get("sha256") or "").strip()
        if expected_sha256 and expected_sha256 != sha256_value:
            raise RuntimeError("remote config sha256 mismatch")

        signing_cfg = remote_cfg.get("signing", {}) if isinstance(remote_cfg.get("signing"), dict) else {}
        signature_algorithm = str(manifest.get("signature_algorithm") or signing_cfg.get("algorithm") or "ed25519")
        signature_value = str(manifest.get("signature") or "").strip()
        signature_required = bool(signing_cfg.get("required", True))
        signature_ok = True
        if signature_required:
            signature_ok, reason = verify_signature(
                data_bytes=payload_bytes,
                signature=signature_value,
                algorithm=signature_algorithm,
                public_key=str(signing_cfg.get("public_key") or ""),
                shared_secret=str(secrets_bundle.get("signature_secret") or secrets_bundle.get("hmac_secret") or ""),
            )
            if not signature_ok:
                raise RuntimeError(reason or "remote config signature verification failed")

        sanitized = sanitize_remote_overlay(payload)
        set_setting("remote_config_staged", json.dumps(sanitized))
        set_remote_config_state(
            status="verified",
            endpoint=endpoint,
            current_revision=str(current_state.get("current_revision") or ""),
            staged_revision=revision,
            sha256=sha256_value,
            signature_ok=signature_ok,
            last_error="",
            last_check_ts=now_ts,
        )

        if alerts_payload is not None:
            alerts_error = validate_alerts_config(alerts_payload)
            desired_hash = alert_config_hash(alerts_payload)
            set_alert_config_state(
                desired_revision=str(alerts_revision or revision),
                desired_hash=desired_hash,
                sync_status="applying_remote",
                last_error="",
                last_remote_check_ts=now_ts,
                remote_endpoint=endpoint,
                status_endpoint=status_url,
            )
            if alerts_error:
                set_alert_config_state(
                    sync_status="apply_failed",
                    last_error=alerts_error,
                    last_remote_check_ts=now_ts,
                    remote_endpoint=endpoint,
                    status_endpoint=status_url,
                )
                raise RuntimeError(alerts_error)

            try:
                apply_remote_alerts_config(
                    normalize_alerts_config(alerts_payload),
                    revision=str(alerts_revision or revision),
                    desired_hash=desired_hash,
                    remote_endpoint=endpoint,
                    status_endpoint=status_url,
                )
            except Exception as alert_exc:
                set_alert_config_state(
                    desired_revision=str(alerts_revision or revision),
                    desired_hash=desired_hash,
                    sync_status="apply_failed",
                    last_error=str(alert_exc),
                    last_remote_check_ts=now_ts,
                    remote_endpoint=endpoint,
                    status_endpoint=status_url,
                )
                raise
        else:
            set_alert_config_state(
                last_remote_check_ts=now_ts,
                remote_endpoint=endpoint,
                status_endpoint=status_url,
            )

        if bool(remote_cfg.get("auto_apply", True)):
            set_remote_overlay(
                sanitized,
                revision=revision,
                sha256=sha256_value,
                signature_ok=signature_ok,
                status="applied",
                endpoint=endpoint,
                last_error="",
            )
            delete_setting("remote_config_staged")
        state = get_remote_config_state()
        if status_url:
            try:
                _report_alert_sync_status(
                    station_id=station_id,
                    remote_cfg=remote_cfg,
                    secrets_bundle=secrets_bundle,
                    status_url=status_url,
                )
            except Exception as report_exc:
                set_alert_config_state(last_error=f"report failed: {report_exc}", status_endpoint=status_url)
        return state
    except Exception as exc:
        return set_remote_config_state(
            status="failed",
            endpoint=endpoint,
            last_error=str(exc),
            last_check_ts=now_ts,
        )
