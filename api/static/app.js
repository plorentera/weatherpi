function setStatus(ok, text) {
  const dot = document.getElementById("statusDot");
  const label = document.getElementById("statusText");
  dot.style.background = ok ? "#22c55e" : "#ef4444";
  dot.style.boxShadow = ok ? "0 0 0 0.35rem rgba(34, 197, 94, 0.12)" : "0 0 0 0.35rem rgba(239, 68, 68, 0.12)";
  label.textContent = text;
}

function fmtTs(epochSeconds) {
  if (!epochSeconds) return "--";
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleString();
}

async function refresh() {
  try {
    const [statusRes, latestRes] = await Promise.all([
      fetch("/api/status", { cache: "no-store" }),
      fetch("/api/latest", { cache: "no-store" }),
    ]);

    if (!statusRes.ok) throw new Error(`status HTTP ${statusRes.status}`);
    if (!latestRes.ok) throw new Error(`latest HTTP ${latestRes.status}`);

    const payload = await latestRes.json();
    const status = await statusRes.json();

    const data = payload.data;
    if (!data) {
      setStatus(true, `sin datos aún · ${status.records ?? 0} registros`);
      return;
    }

    document.getElementById("temp").textContent = data.temp_c ?? "--";
    document.getElementById("hum").textContent = data.humidity_pct ?? "--";
    document.getElementById("pres").textContent = data.pressure_hpa ?? "--";
    document.getElementById("lastTs").textContent = fmtTs(data.ts);

    document.getElementById("raw").textContent = JSON.stringify(data, null, 2);
    const lastSeen = status.last_timestamp ? fmtTs(status.last_timestamp) : "sin lecturas";
    setStatus(true, `OK · ${status.records ?? 0} lecturas · última ${lastSeen}`);
  } catch (e) {
    setStatus(false, `error: ${e.message}`);
  }
}

refresh();
setInterval(refresh, 5000);
