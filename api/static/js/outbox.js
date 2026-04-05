const MAX_ATTEMPTS = 10;

function fmtTs(epochSeconds) {
  if (!epochSeconds) return "--";
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleString();
}

function esc(s) {
  return (s ?? "").toString()
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function loadOutbox() {
  const filter = document.getElementById("filter").value;
  const url = filter ? `/api/outbox?limit=50&status=${encodeURIComponent(filter)}` : "/api/outbox?limit=50";

  const res = await fetch(url, { cache: "no-store" });
  const data = await res.json();

  document.getElementById("summary").textContent =
    `pending=${data.summary.pending}  sent=${data.summary.sent}  failed=${data.summary.failed}`;

  document.getElementById("items").textContent = JSON.stringify(data.items, null, 2);

  const tbody = document.getElementById("tbody");
  tbody.innerHTML = "";

  for (const it of data.items) {
    const isDead = (it.status === "failed" && (it.attempts ?? 0) >= MAX_ATTEMPTS);
    const statusLabel = isDead ? "MUERTO (manual)" : it.status;

    let badgeClass = "text-bg-secondary";
    if (it.status === "pending") badgeClass = "text-bg-warning";
    if (it.status === "sent") badgeClass = "text-bg-success";
    if (it.status === "failed") badgeClass = "text-bg-danger";

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${it.id}</td>
      <td><span class="badge rounded-pill ${badgeClass}">${esc(statusLabel)}</span></td>
      <td>${esc(it.destination)}</td>
      <td>${it.attempts ?? 0}</td>
      <td>${fmtTs(it.next_attempt_ts)}</td>
      <td style="max-width:520px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${esc(it.last_error)}">${esc(it.last_error)}</td>
    `;
    tbody.appendChild(tr);
  }
}

document.getElementById("retryBtn").addEventListener("click", async () => {
  document.getElementById("msg").textContent = "reintentando...";
  const res = await fetch("/api/outbox/retry_failed", { method: "POST" });
  const data = await res.json();
  if (res.ok && data.ok) {
    document.getElementById("msg").textContent = `reintentados: ${data.retried}`;
  } else {
    const err = data.detail || data.error || `HTTP ${res.status}`;
    document.getElementById("msg").textContent = `error: ${err}`;
  }
  loadOutbox();
});

document.getElementById("purgeBtn").addEventListener("click", async () => {
  document.getElementById("msg").textContent = "purgando sent...";
  const res = await fetch("/api/outbox/purge_sent?keep_last=1000", { method: "POST" });
  const data = await res.json();
  if (res.ok && data.ok) {
    document.getElementById("msg").textContent = `borrados: ${data.deleted} (keep_last=${data.keep_last})`;
  } else {
    const err = data.detail || data.error || `HTTP ${res.status}`;
    document.getElementById("msg").textContent = `error: ${err}`;
  }
  loadOutbox();
});

document.getElementById("filter").addEventListener("change", loadOutbox);

loadOutbox();
setInterval(loadOutbox, 5000);
