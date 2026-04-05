function setStatus(ok, text) {
  const dot = document.getElementById("statusDot");
  const label = document.getElementById("statusText");
  dot.style.background = ok ? "#198754" : "#dc3545";
  label.textContent = text;
}

function fmtTs(epochSeconds) {
  if (!epochSeconds) return "--";
  const date = new Date(Number(epochSeconds) * 1000);
  return Number.isNaN(date.getTime()) ? "--" : date.toLocaleString();
}

let historyChart = null;

async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" });
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

function ensureChart() {
  if (historyChart || typeof Chart === "undefined") return;
  const canvas = document.getElementById("historyChart");
  if (!canvas) return;

  historyChart = new Chart(canvas.getContext("2d"), {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Temperatura (C)",
          data: [],
          borderColor: "#dc3545",
          backgroundColor: "rgba(220,53,69,0.12)",
          pointRadius: 0,
          tension: 0.22,
          yAxisID: "yTemp",
        },
        {
          label: "Humedad (%)",
          data: [],
          borderColor: "#0d6efd",
          backgroundColor: "rgba(13,110,253,0.12)",
          pointRadius: 0,
          tension: 0.22,
          yAxisID: "yHum",
        },
        {
          label: "Presion (hPa)",
          data: [],
          borderColor: "#198754",
          backgroundColor: "rgba(25,135,84,0.12)",
          pointRadius: 0,
          tension: 0.22,
          yAxisID: "yPress",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 10 },
        },
        yTemp: {
          type: "linear",
          position: "left",
          title: { display: true, text: "C" },
        },
        yHum: {
          type: "linear",
          position: "right",
          title: { display: true, text: "%" },
          grid: { drawOnChartArea: false },
        },
        yPress: {
          type: "linear",
          position: "right",
          title: { display: true, text: "hPa" },
          grid: { drawOnChartArea: false },
          display: false,
        },
      },
      plugins: {
        legend: { position: "bottom" },
      },
    },
  });
}

function updateChart(items) {
  if (!historyChart || !Array.isArray(items)) return;

  historyChart.data.labels = items.map((row) => {
    const date = new Date((row.ts || 0) * 1000);
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  });
  historyChart.data.datasets[0].data = items.map((row) => row.temp_c ?? null);
  historyChart.data.datasets[1].data = items.map((row) => row.humidity_pct ?? null);
  historyChart.data.datasets[2].data = items.map((row) => row.pressure_hpa ?? null);
  historyChart.update("none");
}

function renderSystemCards(access, telemetry, updates, credentials) {
  document.getElementById("accessMode").textContent = access.allow_lan ? "Red local activa" : "Solo este dispositivo";
  document.getElementById("accessOrigin").textContent = access.allow_lan
    ? (access.lan_origin || access.loopback_origin || "-")
    : (access.loopback_origin || "-");

  const outbox = telemetry.outbox || {};
  const summary = outbox.summary || {};
  document.getElementById("queuePending").textContent = `${summary.pending || 0} pendientes`;
  document.getElementById("queueFailed").textContent = `${summary.failed || 0} con error`;

  const updateState = updates.state || {};
  document.getElementById("updateState").textContent = updateState.status || "idle";
  document.getElementById("updateVersion").textContent = updateState.target_version
    ? `Objetivo: ${updateState.target_version}`
    : `Actual: ${updateState.current_version || "-"}`;

  document.getElementById("securityState").textContent = credentials.default_credentials_active
    ? "Credenciales por defecto"
    : "Credenciales personalizadas";
  document.getElementById("securityHint").textContent = credentials.default_credentials_active
    ? "Cambia reader/admin desde Configuracion."
    : "Acceso local protegido.";
}

async function refresh() {
  try {
    ensureChart();

    const [status, latest, series, access, telemetry, updates, credentials] = await Promise.all([
      fetchJson("/api/status"),
      fetchJson("/api/latest"),
      fetchJson("/api/series?limit=288"),
      fetchJson("/api/system/access"),
      fetchJson("/api/telemetry/status"),
      fetchJson("/api/update/status"),
      fetchJson("/api/security/credentials"),
    ]);

    updateChart(series.items || []);
    renderSystemCards(access, telemetry, updates, credentials);

    const data = latest.data;
    if (!data) {
      setStatus(true, `Sin datos todavia · ${status.records ?? 0} registros`);
      return;
    }

    document.getElementById("temp").textContent = data.temp_c ?? "--";
    document.getElementById("hum").textContent = data.humidity_pct ?? "--";
    document.getElementById("pres").textContent = data.pressure_hpa ?? "--";
    document.getElementById("lastTs").textContent = fmtTs(data.ts);
    document.getElementById("raw").textContent = JSON.stringify(data, null, 2);

    const lastSeen = status.last_timestamp ? fmtTs(status.last_timestamp) : "sin lecturas";
    setStatus(true, `OK · ${status.records ?? 0} lecturas · ultima ${lastSeen}`);
  } catch (err) {
    setStatus(false, `Error: ${err.message}`);
  }
}

refresh();
setInterval(refresh, 5000);
