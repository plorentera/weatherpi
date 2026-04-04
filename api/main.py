import time
from html import escape
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse, PlainTextResponse

from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from common.db import (
    get_connection,
    fetch_latest,
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
    docs_url=None,
    openapi_url=None,
    redoc_url=None,
)
static_dir = Path(__file__).resolve().parent / "static"
docs_dir = Path(__file__).resolve().parent.parent / "docs"


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


class ConfigModel(BaseModel):
    station_id: str = "meteo-001"
    sample_interval_seconds: int = 5
    collector: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    exports: Dict[str, Any] = Field(default_factory=dict)
    ui: Dict[str, Any] = Field(default_factory=dict)



@app.get("/api/config")
def read_config():
    return {"config": get_config()}


@app.put("/api/config")
def update_config(cfg: ConfigModel):
    if cfg.sample_interval_seconds < 1 or cfg.sample_interval_seconds > 3600:
        return {"ok": False, "error": "sample_interval_seconds debe estar entre 1 y 3600"}

    set_config(cfg.model_dump())
    return {"ok": True, "config": get_config()}


@app.get("/api/outbox")
def api_outbox(status: Optional[str] = None, limit: int = 100):
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
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
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
