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

let historyChart = null;

function ensureChart() {
  if (historyChart || typeof Chart === "undefined") return;

  const canvas = document.getElementById("historyChart");
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  historyChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Temperatura (C)",
          data: [],
          borderColor: "#ef4444",
          backgroundColor: "rgba(239,68,68,0.12)",
          pointRadius: 0,
          tension: 0.22,
          yAxisID: "yTemp",
        },
        {
          label: "Humedad (%)",
          data: [],
          borderColor: "#0ea5e9",
          backgroundColor: "rgba(14,165,233,0.12)",
          pointRadius: 0,
          tension: 0.22,
          yAxisID: "yHum",
        },
        {
          label: "Presion (hPa)",
          data: [],
          borderColor: "#22c55e",
          backgroundColor: "rgba(34,197,94,0.12)",
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

  const labels = items.map((r) => {
    const d = new Date((r.ts || 0) * 1000);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  });

  historyChart.data.labels = labels;
  historyChart.data.datasets[0].data = items.map((r) => r.temp_c ?? null);
  historyChart.data.datasets[1].data = items.map((r) => r.humidity_pct ?? null);
  historyChart.data.datasets[2].data = items.map((r) => r.pressure_hpa ?? null);
  historyChart.update("none");
}

async function refresh() {
  try {
    ensureChart();

    const [statusRes, latestRes, seriesRes] = await Promise.all([
      fetch("/api/status", { cache: "no-store" }),
      fetch("/api/latest", { cache: "no-store" }),
      fetch("/api/series?limit=288", { cache: "no-store" }),
    ]);

    if (!statusRes.ok) throw new Error(`status HTTP ${statusRes.status}`);
    if (!latestRes.ok) throw new Error(`latest HTTP ${latestRes.status}`);
    if (!seriesRes.ok) throw new Error(`series HTTP ${seriesRes.status}`);

    const payload = await latestRes.json();
    const status = await statusRes.json();
    const series = await seriesRes.json();

    const data = payload.data;
    updateChart(series.items || []);

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
setInterval(refresh, 1000);
