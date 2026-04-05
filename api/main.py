import time
import os
import logging
import base64
import binascii
import secrets
import json
import hmac
import hashlib
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse, PlainTextResponse, JSONResponse, RedirectResponse

from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from common.db import (
    get_connection,
    fetch_latest,
    fetch_measurements_series,
    get_config,
    set_config,
    outbox_summary,
    fetch_outbox,
    retry_failed_outbox,
    purge_sent_outbox,
    list_exports,
)

app = FastAPI(
    title="Meteo Station API",
    description=(
        "API HTTP para monitorizacion, configuracion, outbox y exportacion de datos "
        "de la estacion meteorologica."
    ),
    version="1.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
    redoc_url="/redoc",
)
static_dir = Path(__file__).resolve().parent / "static"
docs_dir = Path(__file__).resolve().parent.parent / "docs"
logger = logging.getLogger("weatherpi.api")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
READER_USER_ENV = "WEATHERPI_READER_USER"
READER_PASS_ENV = "WEATHERPI_READER_PASS"
READER_PASS_HASH_ENV = "WEATHERPI_READER_PASS_HASH"
ADMIN_USER_ENV = "WEATHERPI_ADMIN_USER"
ADMIN_PASS_ENV = "WEATHERPI_ADMIN_PASS"
ADMIN_PASS_HASH_ENV = "WEATHERPI_ADMIN_PASS_HASH"
SESSION_SECRET_ENV = "WEATHERPI_SESSION_SECRET"
SESSION_COOKIE_NAME = "weatherpi_session"
SESSION_TTL_SECONDS = 12 * 3600
READER_USER = os.getenv(READER_USER_ENV, "reader").strip() or "reader"
READER_PASS = os.getenv(READER_PASS_ENV, "reader").strip() or "reader"
READER_PASS_HASH = os.getenv(READER_PASS_HASH_ENV, "").strip()
ADMIN_USER = os.getenv(ADMIN_USER_ENV, "admin").strip() or "admin"
ADMIN_PASS = os.getenv(ADMIN_PASS_ENV, "admin").strip() or "admin"
ADMIN_PASS_HASH = os.getenv(ADMIN_PASS_HASH_ENV, "").strip()
SESSION_SECRET = os.getenv(SESSION_SECRET_ENV, "change-me-weatherpi-session-secret").strip() or "change-me-weatherpi-session-secret"
SESSION_COOKIE_SECURE = os.getenv("WEATHERPI_COOKIE_SECURE", "0").strip().lower() in {"1", "true", "yes", "on"}
SESSION_COOKIE_SAMESITE = os.getenv("WEATHERPI_COOKIE_SAMESITE", "lax").strip().lower()
if SESSION_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    SESSION_COOKIE_SAMESITE = "lax"
try:
    SESSION_COOKIE_MAX_AGE = int(os.getenv("WEATHERPI_SESSION_TTL_SECONDS", str(SESSION_TTL_SECONDS)).strip())
except ValueError:
    SESSION_COOKIE_MAX_AGE = SESSION_TTL_SECONDS
SESSION_COOKIE_MAX_AGE = max(300, min(SESSION_COOKIE_MAX_AGE, 7 * 24 * 3600))
PBKDF2_SCHEME = "pbkdf2_sha256"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> Optional[bytes]:
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception:
        return None


def _verify_password(plain_password: str, stored_hash: str) -> bool:
    # Format: pbkdf2_sha256$<iterations>$<salt_b64url>$<hash_b64url>
    try:
        scheme, iter_raw, salt_raw, digest_raw = stored_hash.split("$", 3)
        if scheme != PBKDF2_SCHEME:
            return False
        iterations = int(iter_raw)
        if iterations < 100_000:
            return False
    except Exception:
        return False

    salt = _b64url_decode(salt_raw)
    expected = _b64url_decode(digest_raw)
    if not salt or not expected:
        return False

    actual = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, iterations)
    return secrets.compare_digest(actual, expected)


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
    if secrets.compare_digest(username, ADMIN_USER):
        if ADMIN_PASS_HASH and _verify_password(password, ADMIN_PASS_HASH):
            return "admin"
        if not ADMIN_PASS_HASH and secrets.compare_digest(password, ADMIN_PASS):
            return "admin"

    if secrets.compare_digest(username, READER_USER):
        if READER_PASS_HASH and _verify_password(password, READER_PASS_HASH):
            return "reader"
        if not READER_PASS_HASH and secrets.compare_digest(password, READER_PASS):
            return "reader"

    return None


def _sign_session_payload(payload_b64: str) -> str:
    return hmac.new(SESSION_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()


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
    }


@app.get("/api/latest")
def latest():
    return {"data": fetch_latest()}


@app.get("/api/series")
def series(limit: int = 288):
    limit = max(10, min(limit, 2000))
    return {"items": fetch_measurements_series(limit=limit)}


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


PUBLIC_PATHS = {
    "/login",
    "/vendor/bootstrap/bootstrap.min.css",
    "/vendor/bootstrap/bootstrap.bundle.min.js",
    "/style.css",
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
    next = _safe_next_path(fields.get("next", ["/"])[0] or "/")

    role = _role_for_credentials(username, password)
    if not role:
        return RedirectResponse(url=f"/login?error=1&next={next}", status_code=303)

    token = _create_session_token(username=username, role=role)
    response = RedirectResponse(url=next or "/", status_code=303)
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


class ConfigModel(BaseModel):
    station_id: str = "meteo-001"
    sample_interval_seconds: int = 5
    collector: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    exports: Dict[str, Any] = Field(default_factory=dict)
    ui: Dict[str, Any] = Field(default_factory=dict)


def _is_valid_http_url(value: str) -> bool:
    try:
        p = urlparse(value)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def _validate_config_payload(cfg: ConfigModel) -> Optional[str]:
    if cfg.sample_interval_seconds < 1 or cfg.sample_interval_seconds > 3600:
        return "sample_interval_seconds debe estar entre 1 y 3600"

    outputs = cfg.outputs if isinstance(cfg.outputs, dict) else {}

    wh = outputs.get("webhook", {}) if isinstance(outputs.get("webhook", {}), dict) else {}
    if bool(wh.get("enabled", False)):
        wh_url = str(wh.get("url", "")).strip()
        if not wh_url:
            return "outputs.webhook.url es obligatorio cuando webhook.enabled=true"
        if not _is_valid_http_url(wh_url):
            return "outputs.webhook.url debe empezar por http:// o https:// y ser una URL valida"

    mqtt = outputs.get("mqtt", {}) if isinstance(outputs.get("mqtt", {}), dict) else {}
    if bool(mqtt.get("enabled", False)):
        host = str(mqtt.get("host", "")).strip()
        topic = str(mqtt.get("topic", "")).strip()
        try:
            port = int(mqtt.get("port", 1883))
        except Exception:
            return "outputs.mqtt.port debe ser numerico"
        if not host:
            return "outputs.mqtt.host es obligatorio cuando mqtt.enabled=true"
        if not topic:
            return "outputs.mqtt.topic es obligatorio cuando mqtt.enabled=true"
        if port < 1 or port > 65535:
            return "outputs.mqtt.port debe estar entre 1 y 65535"

    return None



@app.get("/api/config")
def read_config():
    return {"config": get_config()}


@app.put("/api/config")
def update_config(cfg: ConfigModel):
    error = _validate_config_payload(cfg)
    if error:
        return JSONResponse(status_code=400, content={"ok": False, "error": error})

    set_config(cfg.model_dump())
    return {"ok": True, "config": get_config()}


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
    match = next((x for x in items if x["id"] == export_id), None)
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
    logger.info(
        "Basic auth enabled: reader=%s admin=%s",
        READER_USER,
        ADMIN_USER,
    )
    logger.info("Credential mode: %s", "hashed" if (READER_PASS_HASH or ADMIN_PASS_HASH) else "plaintext")
    if not READER_PASS_HASH and READER_USER == "reader" and READER_PASS == "reader":
        logger.warning("Reader credentials are using defaults; set %s or %s", READER_PASS_HASH_ENV, READER_PASS_ENV)
    if not ADMIN_PASS_HASH and ADMIN_USER == "admin" and ADMIN_PASS == "admin":
        logger.warning("Admin credentials are using defaults; set %s or %s", ADMIN_PASS_HASH_ENV, ADMIN_PASS_ENV)
    if len(SESSION_SECRET) < 32 or SESSION_SECRET == "change-me-weatherpi-session-secret":
        logger.warning("Session secret is weak/default; set %s to a strong random value", SESSION_SECRET_ENV)
    logger.info(
        "Session cookie config: secure=%s samesite=%s ttl=%ss",
        SESSION_COOKIE_SECURE,
        SESSION_COOKIE_SAMESITE,
        SESSION_COOKIE_MAX_AGE,
    )
