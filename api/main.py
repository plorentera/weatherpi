from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import secrets
import time
import hashlib
import hmac
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from common.config import APP_VERSION, deep_merge, normalize_local_config, resolve_api_bind_host, validate_local_config
from common.db import (
    enqueue_delivery_item,
    fetch_latest,
    fetch_measurements_series,
    fetch_outbox,
    fetch_worker_heartbeats,
    get_config_bundle,
    get_connection,
    get_remote_config_state,
    get_update_state,
    list_exports,
    list_release_history,
    outbox_summary,
    purge_sent_outbox,
    retry_failed_outbox,
    set_config,
    telemetry_delivery_summary,
)
from common.local_auth import (
    ADMIN_PASS_ENV,
    ADMIN_PASS_HASH_ENV,
    ADMIN_USER_ENV,
    READER_PASS_ENV,
    READER_PASS_HASH_ENV,
    READER_USER_ENV,
    SESSION_SECRET_ENV,
    authenticate_local_user,
    get_session_secret,
    load_local_auth_status,
    update_local_auth_store,
)
from common.models import ConfigModel, CredentialsUpdateModel, SecretStoreModel
from common.networking import detect_primary_lan_ip
from common.remote_config_manager import run_remote_config_check
from common.secrets import build_public_secret_view, update_secret_store
from common.telemetry import build_alert_item, build_envelope
from common.update_manager import apply_staged_update, check_for_updates, rollback_update


app = FastAPI(
    title="Meteo Station API",
    description="API HTTP local-first para monitorizacion, configuracion, telemetria saliente y updates pull.",
    version=APP_VERSION,
    docs_url="/docs",
    openapi_url="/openapi.json",
    redoc_url="/redoc",
)
static_dir = Path(__file__).resolve().parent / "static"
docs_dir = Path(__file__).resolve().parent.parent / "docs"
logger = logging.getLogger("weatherpi.api")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SESSION_COOKIE_NAME = "weatherpi_session"
SESSION_TTL_SECONDS = 12 * 3600
SESSION_COOKIE_SECURE = os.getenv("WEATHERPI_COOKIE_SECURE", "0").strip().lower() in {"1", "true", "yes", "on"}
SESSION_COOKIE_SAMESITE = os.getenv("WEATHERPI_COOKIE_SAMESITE", "lax").strip().lower()
if SESSION_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    SESSION_COOKIE_SAMESITE = "lax"
try:
    SESSION_COOKIE_MAX_AGE = int(os.getenv("WEATHERPI_SESSION_TTL_SECONDS", str(SESSION_TTL_SECONDS)).strip())
except ValueError:
    SESSION_COOKIE_MAX_AGE = SESSION_TTL_SECONDS
SESSION_COOKIE_MAX_AGE = max(300, min(SESSION_COOKIE_MAX_AGE, 7 * 24 * 3600))


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> Optional[bytes]:
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception:
        return None


def _safe_next_path(next_path: str) -> str:
    candidate = (next_path or "/").strip()
    if not candidate.startswith("/"):
        return "/"
    if candidate.startswith("//"):
        return "/"
    if candidate.startswith("/login"):
        return "/"
    return candidate


def _role_for_credentials(username: str, password: str) -> Optional[str]:
    return authenticate_local_user(username, password)


def _sign_session_payload(payload_b64: str) -> str:
    session_secret = get_session_secret()
    return hmac.new(session_secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()


def _create_session_token(username: str, role: str) -> str:
    payload = {
        "u": username,
        "r": role,
        "exp": int(time.time()) + SESSION_COOKIE_MAX_AGE,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = _b64url_encode(raw)
    sig = _sign_session_payload(payload_b64)
    return f"{payload_b64}.{sig}"


def _parse_session_token(token: Optional[str]) -> Optional[dict]:
    if not token or "." not in token:
        return None

    payload_b64, sig = token.split(".", 1)
    expected = _sign_session_payload(payload_b64)
    if not secrets.compare_digest(sig, expected):
        return None

    try:
        raw = _b64url_decode(payload_b64)
        if not raw:
            return None
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return None

    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    if payload.get("r") not in ("reader", "admin"):
        return None
    return payload


def _unauthorized(detail: str = "authentication required") -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": detail},
        headers={"WWW-Authenticate": 'Basic realm="WeatherPi"'},
    )


def _parse_basic_credentials(auth_header: Optional[str]) -> Optional[tuple[str, str]]:
    if not auth_header:
        return None

    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "basic" or not token:
        return None

    try:
        raw = base64.b64decode(token, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None

    username, sep, password = raw.partition(":")
    if not sep:
        return None
    return username, password


def _resolve_role(auth_header: Optional[str]) -> Optional[str]:
    creds = _parse_basic_credentials(auth_header)
    if not creds:
        return None
    username, password = creds
    return _role_for_credentials(username, password)


def _resolve_role_from_session_cookie(session_cookie: Optional[str]) -> Optional[str]:
    payload = _parse_session_token(session_cookie)
    if not payload:
        return None
    return str(payload.get("r"))


def _is_browser_html_request(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept.lower()


def _format_origin(scheme: str, host: str, port: int | None) -> str:
    safe_scheme = (scheme or "http").strip() or "http"
    safe_host = (host or "127.0.0.1").strip() or "127.0.0.1"
    if port and not ((safe_scheme == "http" and port == 80) or (safe_scheme == "https" and port == 443)):
        return f"{safe_scheme}://{safe_host}:{port}"
    return f"{safe_scheme}://{safe_host}"


def _runtime_access_payload(request: Request) -> Dict[str, Any]:
    cfg = get_config_bundle()["config"]
    desired_bind_host = resolve_api_bind_host(cfg)
    security_cfg = cfg.get("security", {}) if isinstance(cfg.get("security"), dict) else {}
    allow_lan = bool(security_cfg.get("allow_lan", False))
    runtime_bind_host = os.getenv("WEATHERPI_RUNTIME_BIND_HOST", "").strip() or (request.url.hostname or "127.0.0.1")
    runtime_port_raw = os.getenv("WEATHERPI_RUNTIME_PORT", "").strip()
    request_port = request.url.port
    runtime_port = int(runtime_port_raw) if runtime_port_raw.isdigit() else request_port
    runtime_scheme = request.url.scheme or "http"
    launcher_managed = os.getenv("WEATHERPI_RUNTIME_MANAGER", "").strip().lower() == "run_all"
    lan_ip = detect_primary_lan_ip()
    current_origin = str(request.base_url).rstrip("/")
    loopback_origin = _format_origin(runtime_scheme, "127.0.0.1", runtime_port)
    lan_origin = _format_origin(runtime_scheme, lan_ip, runtime_port) if lan_ip and allow_lan else ""
    restart_required = desired_bind_host != runtime_bind_host

    guidance = []
    if allow_lan:
        if launcher_managed and restart_required:
            guidance.append("La configuracion de red local se ha guardado y el launcher reaplicara el bind del API.")
        elif not launcher_managed and restart_required:
            guidance.append("La configuracion de red local se ha guardado, pero debes reiniciar el API para aplicar el nuevo bind.")
        else:
            guidance.append("La interfaz ya esta preparada para recibir conexiones desde la red local.")
        guidance.append("Si no responde desde otro dispositivo, revisa firewall, puerto y que ambos equipos esten en la misma red.")
    else:
        guidance.append("La interfaz esta limitada a este dispositivo y no acepta conexiones entrantes desde la red local.")
    guidance.append("Red local no significa Internet: no se recomienda publicar la estacion fuera de tu LAN.")

    return {
        "mode": "lan" if allow_lan else "local_only",
        "allow_lan": allow_lan,
        "desired_bind_host": desired_bind_host,
        "runtime_bind_host": runtime_bind_host,
        "runtime_port": runtime_port,
        "runtime_scheme": runtime_scheme,
        "current_origin": current_origin,
        "loopback_origin": loopback_origin,
        "lan_ip": lan_ip,
        "lan_origin": lan_origin,
        "launcher_managed": launcher_managed,
        "restart_required": restart_required,
        "guidance": guidance,
    }


def _resulting_default_combo(current_default: bool, username: str, new_password: Optional[str], default_user: str, default_password: str) -> bool:
    normalized_username = (username or "").strip()
    if normalized_username != default_user:
        return False
    if new_password is None:
        return current_default
    return new_password == default_password


def _validate_credentials_payload(payload: CredentialsUpdateModel) -> Optional[str]:
    reader_username = payload.reader_username.strip()
    admin_username = payload.admin_username.strip()
    reader_password = payload.reader_password if payload.reader_password else None
    admin_password = payload.admin_password if payload.admin_password else None
    current = load_local_auth_status()

    if not reader_username or not admin_username:
        return "Los usuarios reader y admin son obligatorios."
    if reader_username == admin_username:
        return "Los usuarios reader y admin deben ser distintos."
    if reader_password is not None:
        if reader_password != (payload.reader_password_confirm or ""):
            return "La confirmacion de password reader no coincide."
        if len(reader_password) < 8:
            return "La password reader debe tener al menos 8 caracteres."
    if admin_password is not None:
        if admin_password != (payload.admin_password_confirm or ""):
            return "La confirmacion de password admin no coincide."
        if len(admin_password) < 8:
            return "La password admin debe tener al menos 8 caracteres."

    if _resulting_default_combo(current.get("reader_default", True), reader_username, reader_password, "reader", "reader"):
        return "No se permite guardar las credenciales por defecto reader/reader."
    if _resulting_default_combo(current.get("admin_default", True), admin_username, admin_password, "admin", "admin"):
        return "No se permite guardar las credenciales por defecto admin/admin."

    return None


PUBLIC_PATHS = {
    "/login",
    "/vendor/bootstrap/bootstrap.min.css",
    "/vendor/bootstrap/bootstrap.bundle.min.js",
    "/style.css",
    "/js/login.js",
}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS:
        return await call_next(request)

    role = _resolve_role(request.headers.get("Authorization"))
    if not role:
        role = _resolve_role_from_session_cookie(request.cookies.get(SESSION_COOKIE_NAME))

    if not role:
        if request.method.upper() == "GET" and _is_browser_html_request(request):
            return RedirectResponse(url=f"/login?next={path}", status_code=303)
        return _unauthorized("invalid or missing credentials")

    if request.method.upper() not in SAFE_METHODS and role != "admin":
        return JSONResponse(status_code=403, content={"detail": "admin role required"})

    request.state.auth_role = role
    return await call_next(request)


@app.get("/login", include_in_schema=False)
def login_page():
    login_path = static_dir / "login.html"
    if not login_path.exists():
        raise HTTPException(status_code=404, detail="login page not found")
    return FileResponse(path=login_path, media_type="text/html")


@app.post("/login", include_in_schema=False)
async def login_submit(request: Request):
    body = (await request.body()).decode("utf-8", errors="ignore")
    fields = parse_qs(body)
    username = (fields.get("username", [""])[0] or "").strip()
    password = fields.get("password", [""])[0] or ""
    next_path = _safe_next_path(fields.get("next", ["/"])[0] or "/")

    role = _role_for_credentials(username, password)
    if not role:
        return RedirectResponse(url=f"/login?error=1&next={next_path}", status_code=303)

    token = _create_session_token(username=username, role=role)
    response = RedirectResponse(url=next_path or "/", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite=SESSION_COOKIE_SAMESITE,
        secure=SESSION_COOKIE_SECURE,
        max_age=SESSION_COOKIE_MAX_AGE,
    )
    return response


@app.post("/logout", include_in_schema=False)
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.get("/api/status")
def status():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS count FROM measurements")
    count = cur.fetchone()["count"]
    cur.execute("SELECT MAX(ts) AS last_ts FROM measurements")
    last_ts = cur.fetchone()["last_ts"]
    conn.close()
    return {
        "status": "ok",
        "records": count,
        "last_timestamp": last_ts,
        "now": int(time.time()),
        "version": APP_VERSION,
    }


@app.get("/api/latest")
def latest():
    return {"data": fetch_latest()}


@app.get("/api/series")
def series(limit: int = 288):
    limit = max(10, min(limit, 2000))
    return {"items": fetch_measurements_series(limit=limit)}


@app.get("/api/config")
def read_config():
    bundle = get_config_bundle()
    bundle["secrets"] = build_public_secret_view()
    return bundle


@app.put("/api/config")
def update_config(cfg: ConfigModel):
    payload = cfg.model_dump(exclude_unset=True)
    current_local = get_config_bundle()["local_config"]
    candidate = normalize_local_config(deep_merge(current_local, payload))
    error = validate_local_config(candidate)
    if error:
        return JSONResponse(status_code=400, content={"ok": False, "error": error})

    set_config(payload)
    bundle = get_config_bundle()
    bundle["secrets"] = build_public_secret_view()
    return {"ok": True, **bundle}


@app.get("/api/config/secrets")
def read_config_secrets():
    return build_public_secret_view()


@app.put("/api/config/secrets")
def update_config_secrets(payload: SecretStoreModel):
    update_secret_store(payload.model_dump(exclude_unset=True))
    return {"ok": True, "secrets": build_public_secret_view()}


@app.get("/api/outbox")
def api_outbox(status: Optional[str] = None, limit: int = 100):
    limit = max(1, min(limit, 500))
    return {
        "summary": outbox_summary(),
        "items": fetch_outbox(status=status, limit=limit),
    }


@app.post("/api/outbox/retry_failed")
def retry_failed():
    n = retry_failed_outbox(int(time.time()))
    return {"ok": True, "retried": n}


@app.post("/api/outbox/purge_sent")
def purge_sent(keep_last: int = 1000):
    deleted = purge_sent_outbox(keep_last=keep_last)
    return {"ok": True, "deleted": deleted, "keep_last": keep_last}


@app.get("/api/system/version")
def system_version():
    update_state = get_update_state()
    return {
        "app_version": APP_VERSION,
        "current_version": update_state.get("current_version"),
        "target_version": update_state.get("target_version"),
        "channel": update_state.get("channel"),
        "update_status": update_state.get("status"),
    }


@app.get("/api/system/workers")
def system_workers():
    return {"items": fetch_worker_heartbeats()}


@app.get("/api/system/access")
def system_access(request: Request):
    return _runtime_access_payload(request)


@app.get("/api/security/credentials")
def security_credentials_status():
    return load_local_auth_status()


@app.put("/api/security/credentials")
def security_credentials_update(payload: CredentialsUpdateModel):
    error = _validate_credentials_payload(payload)
    if error:
        return JSONResponse(status_code=400, content={"ok": False, "error": error})

    update_local_auth_store(
        reader_username=payload.reader_username.strip(),
        admin_username=payload.admin_username.strip(),
        reader_password=payload.reader_password if payload.reader_password else None,
        admin_password=payload.admin_password if payload.admin_password else None,
    )
    return {"ok": True, "credentials": load_local_auth_status()}


@app.get("/api/telemetry/status")
def telemetry_status():
    bundle = get_config_bundle()
    config = bundle["config"]
    telemetry = config.get("telemetry", {}) if isinstance(config.get("telemetry"), dict) else {}
    return {
        "enabled": bool(telemetry.get("enabled", False)),
        "destinations": telemetry.get("destinations", []),
        "outbox": telemetry_delivery_summary(),
    }


class TelemetryTestModel(BaseModel):
    destination_id: Optional[str] = None
    message: str = "Prueba manual enviada desde la UI local"


@app.post("/api/telemetry/test")
def telemetry_test(payload: TelemetryTestModel):
    bundle = get_config_bundle()
    config = bundle["config"]
    telemetry = config.get("telemetry", {}) if isinstance(config.get("telemetry"), dict) else {}
    destinations = telemetry.get("destinations", []) if isinstance(telemetry.get("destinations"), list) else []
    enabled_destinations = [dest for dest in destinations if bool(dest.get("enabled", False))]

    if not bool(telemetry.get("enabled", False)):
        return JSONResponse(status_code=409, content={"ok": False, "error": "La telemetria esta desactivada"})
    if not enabled_destinations:
        return JSONResponse(status_code=409, content={"ok": False, "error": "No hay destinos activos configurados"})

    destination = None
    if payload.destination_id:
        destination = next((dest for dest in enabled_destinations if str(dest.get("id")) == payload.destination_id), None)
        if not destination:
            return JSONResponse(status_code=404, content={"ok": False, "error": "destination_id no encontrado o inactivo"})
    else:
        destination = enabled_destinations[0]

    now_ts = int(time.time())
    envelope = build_envelope(
        station_id=str(config.get("station_id") or "meteo-001"),
        data_class="alert_event",
        occurred_ts=now_ts,
        items=[
            build_alert_item(
                "ui",
                payload.message.strip() or "Prueba manual enviada desde la UI local",
                level="info",
                context={"manual_test": True, "destination_id": destination.get("id")},
            )
        ],
        extra={"manual_test": True, "source": "settings-ui"},
    )
    inserted = enqueue_delivery_item(destination=destination, envelope=envelope, now_ts=now_ts, next_attempt_ts=now_ts)
    return {
        "ok": bool(inserted),
        "inserted": bool(inserted),
        "destination_id": destination.get("id"),
        "data_class": envelope.get("data_class"),
        "message_id": envelope.get("message_id"),
        "idempotency_key": envelope.get("idempotency_key"),
    }


@app.get("/api/remote-config/status")
def remote_config_status():
    return {
        "state": get_remote_config_state(),
        "overlay": get_config_bundle()["remote_overlay"],
    }


@app.post("/api/remote-config/check-now")
def remote_config_check_now():
    state = run_remote_config_check(force=True)
    return {"ok": state.get("status") != "failed", "state": state}


@app.get("/api/update/status")
def update_status():
    return {
        "state": get_update_state(),
        "history": list_release_history(limit=20),
    }


@app.post("/api/update/check-now")
def update_check_now():
    state = check_for_updates(force=True)
    return {"ok": state.get("status") != "failed", "state": state}


@app.post("/api/update/apply")
def update_apply():
    try:
        state = apply_staged_update()
        return {"ok": True, "state": state}
    except Exception as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": str(exc), "state": get_update_state()})


@app.post("/api/update/rollback")
def update_rollback():
    try:
        state = rollback_update()
        return {"ok": True, "state": state}
    except Exception as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": str(exc), "state": get_update_state()})


@app.get("/api/export.csv")
def export_csv(days: int = 7):
    now_ts = int(time.time())
    ts_from = now_ts - (days * 24 * 3600)
    ts_to = now_ts

    def iter_rows():
        yield "ts;temp_c;humidity_pct;pressure_hpa\n"
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ts, temp_c, humidity_pct, pressure_hpa
            FROM measurements
            WHERE ts BETWEEN ? AND ?
            ORDER BY ts ASC
            """,
            (ts_from, ts_to),
        )
        for ts, t, h, p in cur.fetchall():
            yield f"{ts};{t or ''};{h or ''};{p or ''}\n"
        conn.close()

    return StreamingResponse(
        iter_rows(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=meteo_last_{days}_days.csv"},
    )


@app.get("/api/exports")
def exports_list(limit: int = 50):
    limit = max(1, min(limit, 500))
    return {"items": list_exports(limit=limit)}


@app.get("/api/exports/{export_id}")
def download_export(export_id: int):
    items = list_exports(limit=5000)
    match = next((item for item in items if item["id"] == export_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="export not found")

    export_path = Path(match["path"])
    if not export_path.exists():
        raise HTTPException(status_code=404, detail="export file missing")

    return FileResponse(path=export_path, filename=match["filename"], media_type="text/csv")


@app.get("/docs/API.md", include_in_schema=False)
def api_markdown_doc(raw: bool = False):
    doc_path = docs_dir / "API.md"
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail="API documentation not found")

    if raw:
        return PlainTextResponse(content=doc_path.read_text(encoding="utf-8"), media_type="text/markdown")
    docs_view_path = static_dir / "docs_viewer.html"
    if not docs_view_path.exists():
        raise HTTPException(status_code=404, detail="docs viewer not found")
    return FileResponse(path=docs_view_path, media_type="text/html")


app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


@app.on_event("startup")
def startup_notice() -> None:
    auth_status = load_local_auth_status()
    logger.info(
        "Local auth enabled: reader=%s admin=%s store=%s",
        auth_status.get("reader_username"),
        auth_status.get("admin_username"),
        auth_status.get("path"),
    )
    if auth_status.get("reader_default"):
        logger.warning("Reader credentials are still using defaults; update them from the UI or set %s/%s before first run", READER_USER_ENV, READER_PASS_ENV)
    if auth_status.get("admin_default"):
        logger.warning("Admin credentials are still using defaults; update them from the UI or set %s/%s before first run", ADMIN_USER_ENV, ADMIN_PASS_ENV)
    if not auth_status.get("session_secret_strong"):
        logger.warning("Session secret is weak/default; set %s or let the local auth store regenerate it", SESSION_SECRET_ENV)
    logger.info(
        "Session cookie config: secure=%s samesite=%s ttl=%ss",
        SESSION_COOKIE_SECURE,
        SESSION_COOKIE_SAMESITE,
        SESSION_COOKIE_MAX_AGE,
    )
