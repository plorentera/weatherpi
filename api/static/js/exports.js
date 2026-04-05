function fmtTs(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

async function loadExports() {
  const res = await fetch("/api/exports?limit=50", { cache: "no-store" });
  const data = await res.json();
  const list = document.getElementById("list");
  list.innerHTML = "";
  document.getElementById("msg").textContent = `exports: ${data.items.length}`;

  for (const e of data.items) {
    const div = document.createElement("div");
    div.className = "col-12 col-lg-6";
    div.innerHTML = `
      <div class="card h-100 shadow-sm">
        <div class="card-body p-4">
          <div class="d-flex justify-content-between align-items-start gap-3 mb-3">
            <div>
              <div class="small text-uppercase text-secondary fw-semibold mb-1">Archivo</div>
              <div class="fw-semibold text-break">${e.filename}</div>
            </div>
            <a class="btn btn-sm btn-primary" href="/api/exports/${e.id}">Descargar</a>
          </div>
          <div class="small text-secondary mb-2">Creado: ${fmtTs(e.created_ts)}</div>
          <div class="small text-secondary mb-0">Periodo: ${fmtTs(e.period_from_ts)} -> ${fmtTs(e.period_to_ts)}</div>
        </div>
      </div>
    `;
    list.appendChild(div);
  }
}

loadExports();
setInterval(loadExports, 10000);
