function fmtTs(epochSeconds) {
  if (!epochSeconds) return "-";
  const date = new Date(Number(epochSeconds) * 1000);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString();
}

function esc(value) {
  return (value ?? "").toString()
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function setFeedback(text, tone = "secondary") {
  const alert = document.getElementById("feedbackAlert");
  const body = document.getElementById("feedbackText");
  body.textContent = text;
  alert.className = "alert";
  if (tone === "success") {
    alert.classList.add("alert-success");
  } else if (tone === "danger") {
    alert.classList.add("alert-danger");
  } else if (tone === "warning") {
    alert.classList.add("alert-warning");
  } else {
    alert.classList.add("alert-secondary");
  }
  alert.classList.remove("d-none");
}

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (err) {
    throw new Error(`Respuesta invalida en ${url}`);
  }
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || data.detail || `HTTP ${res.status}`);
  }
  return data;
}

function badgeForStatus(status, stale = false) {
  if (stale) return "text-bg-warning";
  if (status === "ok" || status === "sent") return "text-bg-success";
  if (status === "idle" || status === "pending") return "text-bg-secondary";
  if (status === "leased" || status === "blocked") return "text-bg-warning";
  if (status === "failed") return "text-bg-danger";
  return "text-bg-info";
}

function renderWorkers(items) {
  const tbody = document.getElementById("workersBody");
  tbody.innerHTML = "";

  if (!Array.isArray(items) || !items.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="4" class="text-secondary">Sin heartbeats todavia.</td>';
    tbody.appendChild(tr);
    return;
  }

  items.forEach((item) => {
    const tr = document.createElement("tr");
    const stale = !!item.stale;
    const label = stale ? "sin actualizar" : (item.status || "unknown");
    tr.innerHTML = `
      <td>${esc(item.worker_name)}</td>
      <td><span class="badge ${badgeForStatus(item.status, stale)}">${esc(label)}</span></td>
      <td>${fmtTs(item.updated_ts)}</td>
      <td>${esc(JSON.stringify(item.details || {}))}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderOutbox(data) {
  const summary = data.summary || {};
  const items = Array.isArray(data.items) ? data.items : [];
  document.getElementById("summaryPending").textContent = summary.pending ?? 0;
  document.getElementById("summaryFailed").textContent = summary.failed ?? 0;
  document.getElementById("summarySent").textContent = summary.sent ?? 0;

  const lastSent = items.find((item) => item.status === "sent");
  const lastFailed = items.find((item) => item.status === "failed");
  document.getElementById("summaryLastSuccess").textContent = lastSent
    ? `${fmtTs(lastSent.last_attempt_ts || lastSent.next_attempt_ts)} · ${lastSent.destination_id || lastSent.destination || "-"}`
    : "Sin envios correctos recientes";
  document.getElementById("summaryLastError").textContent = lastFailed
    ? `Ultimo error: ${lastFailed.last_error || "sin detalle"}`
    : "No hay errores recientes en la cola.";

  document.getElementById("items").textContent = JSON.stringify(items, null, 2);

  const tbody = document.getElementById("tbody");
  tbody.innerHTML = "";
  if (!items.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="8" class="text-secondary">No hay mensajes en la cola con este filtro.</td>';
    tbody.appendChild(tr);
    return;
  }

  items.forEach((item) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${item.id}</td>
      <td><span class="badge ${badgeForStatus(item.status)}">${esc(item.status)}</span></td>
      <td>${esc(item.destination_id || item.destination || "-")}</td>
      <td>${esc(item.data_class || "-")}</td>
      <td>${item.attempts ?? 0}</td>
      <td>${fmtTs(item.last_attempt_ts)}</td>
      <td>${fmtTs(item.next_attempt_ts)}</td>
      <td title="${esc(item.last_error || "")}" style="max-width: 26rem;">${esc(item.last_error || "-")}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function loadOutboxPage() {
  const filter = document.getElementById("filter").value;
  const outboxUrl = filter ? `/api/outbox?limit=100&status=${encodeURIComponent(filter)}` : "/api/outbox?limit=100";
  const [outbox, workers] = await Promise.all([
    fetchJson(outboxUrl, { cache: "no-store" }),
    fetchJson("/api/system/workers", { cache: "no-store" }),
  ]);
  renderOutbox(outbox);
  renderWorkers(workers.items || []);
}

async function runAction(buttonId, url, idleLabel, busyLabel, successMessage) {
  const button = document.getElementById(buttonId);
  button.disabled = true;
  button.textContent = busyLabel;
  try {
    await fetchJson(url, { method: "POST" });
    await loadOutboxPage();
    setFeedback(successMessage, "success");
  } finally {
    button.disabled = false;
    button.textContent = idleLabel;
  }
}

document.getElementById("retryBtn").addEventListener("click", () => {
  runAction("retryBtn", "/api/outbox/retry_failed", "Reintentar errores", "Reintentando...", "Mensajes fallidos reenviados a la cola.")
    .catch((err) => setFeedback(`No se pudieron reintentar los errores. ${err.message}`, "danger"));
});

document.getElementById("purgeBtn").addEventListener("click", () => {
  runAction("purgeBtn", "/api/outbox/purge_sent?keep_last=1000", "Limpiar enviados antiguos", "Limpiando...", "Historial de enviados antiguos limpiado.")
    .catch((err) => setFeedback(`No se pudieron limpiar los enviados. ${err.message}`, "danger"));
});

document.getElementById("filter").addEventListener("change", () => {
  loadOutboxPage().catch((err) => setFeedback(`No se pudo recargar el outbox. ${err.message}`, "danger"));
});

loadOutboxPage()
  .then(() => setFeedback("Estado del outbox cargado.", "secondary"))
  .catch((err) => setFeedback(`No se pudo cargar el outbox. ${err.message}`, "danger"));

setInterval(() => {
  loadOutboxPage().catch(() => {});
}, 5000);
