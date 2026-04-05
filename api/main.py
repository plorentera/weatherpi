import time
import os
import logging
import base64
import binascii
import secrets
import json
import hmac
import hashlib
from html import escape
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse, PlainTextResponse, JSONResponse, RedirectResponse

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
ADMIN_USER_ENV = "WEATHERPI_ADMIN_USER"
ADMIN_PASS_ENV = "WEATHERPI_ADMIN_PASS"
SESSION_SECRET_ENV = "WEATHERPI_SESSION_SECRET"
SESSION_COOKIE_NAME = "weatherpi_session"
SESSION_TTL_SECONDS = 12 * 3600
READER_USER = os.getenv(READER_USER_ENV, "reader").strip() or "reader"
READER_PASS = os.getenv(READER_PASS_ENV, "reader").strip() or "reader"
ADMIN_USER = os.getenv(ADMIN_USER_ENV, "admin").strip() or "admin"
ADMIN_PASS = os.getenv(ADMIN_PASS_ENV, "admin").strip() or "admin"
SESSION_SECRET = os.getenv(SESSION_SECRET_ENV, "change-me-weatherpi-session-secret").strip() or "change-me-weatherpi-session-secret"


def _role_for_credentials(username: str, password: str) -> Optional[str]:
    if secrets.compare_digest(username, ADMIN_USER) and secrets.compare_digest(password, ADMIN_PASS):
        return "admin"
    if secrets.compare_digest(username, READER_USER) and secrets.compare_digest(password, READER_PASS):
        return "reader"
    return None


def _sign_session_payload(payload_b64: str) -> str:
    return hmac.new(SESSION_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()


def _create_session_token(username: str, role: str) -> str:
    payload = {
        "u": username,
        "r": role,
        "exp": int(time.time()) + SESSION_TTL_SECONDS,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
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
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
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
def login_page(next: str = "/", error: int = 0):
        err_html = "<div class='alert alert-danger mt-3 mb-0 py-2'>Credenciales invalidas.</div>" if error else ""
        safe_next = escape(next)
        html = f"""<!doctype html>
<html lang=\"es\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
    <title>Login - Meteo Station</title>
    <link href=\"/vendor/bootstrap/bootstrap.min.css\" rel=\"stylesheet\">
    <link rel=\"stylesheet\" href=\"/style.css\" />
</head>
<body class=\"bg-body-tertiary\">
    <main class=\"container py-5\" style=\"max-width:560px;\">
        <section class=\"card shadow-sm\">
            <div class=\"card-body p-4 p-lg-5\">
                <div class=\"text-uppercase text-secondary small fw-semibold mb-2\">Acceso protegido</div>
                <h1 class=\"h3 fw-semibold mb-3\">Inicia sesion en Meteo Station</h1>
                <p class=\"text-secondary mb-4\">Usa credenciales reader para lectura o admin para operaciones de escritura.</p>
                <form method=\"post\" action=\"/login\" class=\"vstack gap-3\">
                    <input type=\"hidden\" name=\"next\" value=\"{safe_next}\">
                    <div>
                        <label class=\"form-label\" for=\"username\">Usuario</label>
                        <input id=\"username\" name=\"username\" type=\"text\" class=\"form-control form-control-lg\" autocomplete=\"username\" required>
                    </div>
                    <div>
                        <label class=\"form-label\" for=\"password\">Contrasena</label>
                        <input id=\"password\" name=\"password\" type=\"password\" class=\"form-control form-control-lg\" autocomplete=\"current-password\" required>
                    </div>
                    <button class=\"btn btn-primary btn-lg\" type=\"submit\">Entrar</button>
                </form>
                {err_html}
            </div>
        </section>
    </main>
</body>
</html>"""
        return HTMLResponse(content=html)


@app.post("/login", include_in_schema=False)
async def login_submit(request: Request):
    body = (await request.body()).decode("utf-8", errors="ignore")
    fields = parse_qs(body)
    username = (fields.get("username", [""])[0] or "").strip()
    password = fields.get("password", [""])[0] or ""
    next = fields.get("next", ["/"])[0] or "/"

    role = _role_for_credentials(username, password)
    if not role:
        return RedirectResponse(url=f"/login?error=1&next={next}", status_code=303)

    token = _create_session_token(username=username, role=role)
    response = RedirectResponse(url=next or "/", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=SESSION_TTL_SECONDS,
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

    content = doc_path.read_text(encoding="utf-8")
    html = f"""<!doctype html>
<html lang="es">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>API - Meteo Station</title>
    <link href="/vendor/bootstrap/bootstrap.min.css" rel="stylesheet">
    <style>
        body {{ background: #f8f9fa; }}
        .doc-shell {{ max-width: 1100px; }}
        .doc-body {{ white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; }}
    </style>
</head>
<body>
    <main class="container doc-shell py-4 py-lg-5">
        <div class="d-flex flex-wrap justify-content-between align-items-center gap-3 mb-4">
            <div>
                <div class="text-uppercase text-secondary small fw-semibold mb-2">Documentacion consultable</div>
                <h1 class="h3 fw-semibold mb-0">docs/API.md</h1>
            </div>
            <a class="btn btn-outline-secondary" href="/docs/API.md?raw=1" target="_blank" rel="noreferrer">Abrir raw</a>
        </div>
        <section class="card shadow-sm">
            <div class="card-body p-4 p-lg-5 doc-body">{escape(content)}</div>
        </section>
    </main>
</body>
</html>"""

    return HTMLResponse(content=html)


app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


@app.on_event("startup")
def startup_notice() -> None:
    logger.info(
        "Basic auth enabled: reader=%s admin=%s",
        READER_USER,
        ADMIN_USER,
    )
    if READER_USER == "reader" and READER_PASS == "reader":
        logger.warning("Reader credentials are using defaults; set %s/%s", READER_USER_ENV, READER_PASS_ENV)
    if ADMIN_USER == "admin" and ADMIN_PASS == "admin":
        logger.warning("Admin credentials are using defaults; set %s/%s", ADMIN_USER_ENV, ADMIN_PASS_ENV)
    if SESSION_SECRET == "change-me-weatherpi-session-secret":
        logger.warning("Session secret is using default; set %s", SESSION_SECRET_ENV)
