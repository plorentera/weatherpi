import time
import os
import logging
from html import escape
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse, PlainTextResponse, JSONResponse

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
API_KEY_ENV_NAME = "WEATHERPI_API_KEY"
API_KEY = os.getenv(API_KEY_ENV_NAME, "").strip()


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


def require_api_key(x_api_key: Optional[str] = Header(default=None)):
    # If API key is not configured, keep local/dev flow simple.
    if not API_KEY:
        return

    if not x_api_key:
        raise HTTPException(status_code=401, detail="missing X-API-Key")

    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="invalid API key")


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
def update_config(cfg: ConfigModel, _: None = Depends(require_api_key)):
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
def retry_failed(_: None = Depends(require_api_key)):
    n = retry_failed_outbox(int(time.time()))
    return {"ok": True, "retried": n}


@app.post("/api/outbox/purge_sent")
def purge_sent(keep_last: int = 1000, _: None = Depends(require_api_key)):
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
    if API_KEY:
        logger.info("API write protection enabled via %s", API_KEY_ENV_NAME)
    else:
        logger.warning("API write protection disabled (set %s to enable)", API_KEY_ENV_NAME)
