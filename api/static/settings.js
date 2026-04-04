function $(id) { return document.getElementById(id); }

function msg(text) { $("msg").textContent = text; }

function setSaving(isSaving) {
  const button = $("saveBtn");
  button.disabled = isSaving;
  button.textContent = isSaving ? "Guardando…" : "Guardar cambios";
}

function asInt(v, fallback) {
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : fallback;
}

function pad2(n) { return String(n).padStart(2, "0"); }

function updateExportsUI() {
  const freq = $("exFrequency").value;
  const everyDays = $("exEveryDays");
  everyDays.disabled = (freq !== "every_n_days");
  everyDays.closest("div").classList.toggle("opacity-50", everyDays.disabled);
}

/* =========================================================
   FLAG: evitar que loadConfig pise cambios del usuario
   ========================================================= */
let userTouchedUiTz = false;

/* =========================================================
   PREVIEW UTC: exports heredan SIEMPRE la TZ de la UI
  msg("Guardando…");
function zonedDateForToday(tz, hh, mm) {
  const now = new Date();
    .catch(e => msg("❌ Error guardando: " + e.message))
  // "guess" en UTC (hoy) a la hora hh:mm, luego corregimos según cómo lo ve esa TZ
  const guess = new Date(Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth(),
    now.getUTCDate(),
    hh,
    mm,
    0
  ));

  const fmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: tz,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    hour12: false
  });

  const parts = fmt.formatToParts(guess);
  const get = (type) => parts.find(p => p.type === type)?.value;

  const H = parseInt(get("hour"), 10);
  const M = parseInt(get("minute"), 10);

  // delta entre lo que "debería ser" hh:mm y lo que la TZ muestra para guess
  const deltaMin = (H * 60 + M) - (hh * 60 + mm);
  return new Date(guess.getTime() - deltaMin * 60 * 1000);
}

function computeUtcTimeFromLocal(tz, localHHMM) {
  if (!localHHMM || !localHHMM.includes(":")) return "00:00";

  const [hhStr, mmStr] = localHHMM.split(":");
  const hh = parseInt(hhStr, 10);
  const mm = parseInt(mmStr, 10);

  if (!Number.isFinite(hh) || !Number.isFinite(mm)) return "00:00";
  if (!tz) tz = "UTC";

  const dt = zonedDateForToday(tz, hh, mm);
  return `${pad2(dt.getUTCHours())}:${pad2(dt.getUTCMinutes())}`;
}

function updateUtcPreview() {
  // EXPORTS siempre usan la zona horaria de la UI (único selector)
  const tz = $("uiTimezone").value || "UTC";
  const localTime = $("exLocalTime").value || "00:00";

  const utc = computeUtcTimeFromLocal(tz, localTime);
  $("exTimeUtc").value = utc;
  $("exUtcPreview").textContent = utc;
}

/* =========================================================
   LOAD CONFIG
   ========================================================= */
async function loadConfig() {
  const res = await fetch("/api/config", { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);

  const data = await res.json();
  const cfg = data.config || {};

  $("stationId").value = cfg.station_id ?? "meteo-001";
  $("interval").value = cfg.sample_interval_seconds ?? 5;

  const outputs = cfg.outputs || {};
  const wh = outputs.webhook || {};
  const mq = outputs.mqtt || {};
  const collector = cfg.collector || {};
  const ex = cfg.exports || {};
  const sch = ex.schedule || {};

  // EXPORTS
  $("exEnabled").checked = !!ex.enabled;
  $("exFrequency").value = ex.frequency ?? "daily";
  $("exEveryDays").value = ex.every_days ?? 2;
  $("exKeepDays").value = ex.keep_days ?? 30;
  $("exLocalTime").value = sch.time_local ?? "00:00";

  // UI TZ (no pisar si el usuario ya la tocó)
  if (!userTouchedUiTz) {
    $("uiTimezone").value = cfg.ui?.timezone ?? "UTC";
  }

  // WEBHOOK
  $("whEnabled").checked = !!wh.enabled;
  $("whUrl").value = wh.url ?? "";
  $("whTimeout").value = wh.timeout_seconds ?? 5;

  // MQTT
  $("mqEnabled").checked = !!mq.enabled;
  $("mqHost").value = mq.host ?? "localhost";
  $("mqPort").value = mq.port ?? 1883;
  $("mqTopic").value = mq.topic ?? "meteo/measurements";

  $("collectorEnabled").checked = collector.enabled !== false;

  updateUtcPreview();
  updateExportsUI();
  msg("Configuración cargada.");
}

/* =========================================================
   SAVE CONFIG
   ========================================================= */
async function saveConfig() {
  const station_id = $("stationId").value.trim() || "meteo-001";
  const sample_interval_seconds = asInt($("interval").value, 5);

  // Asegura preview/hidden utc actualizado antes de enviar
  updateUtcPreview();

  const schedule = {
    time_local: $("exLocalTime").value || "00:00",
    time_utc: $("exTimeUtc").value || "00:00",

    // dejamos claro qué TZ se usó para calcular
    ui_timezone: $("uiTimezone").value || "UTC",
    ui_time_local: $("exLocalTime").value || "00:00",
  };

  const exports_cfg = {
    enabled: $("exEnabled").checked,
    frequency: $("exFrequency").value,
    every_days: asInt($("exEveryDays").value, 2),
    keep_days: asInt($("exKeepDays").value, 30),
    schedule
  };

  const outputs = {
    webhook: {
      enabled: $("whEnabled").checked,
      url: $("whUrl").value.trim(),
      timeout_seconds: asInt($("whTimeout").value, 5),
    },
    mqtt: {
      enabled: $("mqEnabled").checked,
      host: $("mqHost").value.trim() || "localhost",
      port: asInt($("mqPort").value, 1883),
      topic: $("mqTopic").value.trim() || "meteo/measurements",
    }
  };

  const payload = {
    station_id,
    sample_interval_seconds,
    collector: {
      enabled: $("collectorEnabled").checked,
    },
    outputs,
    exports: exports_cfg,
    ui: { timezone: $("uiTimezone").value || "UTC" }
  };

  console.log("UI TZ seleccionada:", $("uiTimezone").value);
  console.log("PAYLOAD.ui.timezone:", payload.ui.timezone);

  const res = await fetch("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (data.ok) {
    msg("✅ Configuración guardada correctamente.");
  } else {
    msg("❌ Error: " + (data.error || "desconocido"));
  }
}

/* =========================================================
   EVENTS
   ========================================================= */
$("saveBtn").addEventListener("click", () => {
  msg("Guardando…");
  setSaving(true);
  saveConfig()
    .catch(e => msg("❌ Error guardando: " + e.message))
    .finally(() => setSaving(false));
});

$("exFrequency").addEventListener("change", updateExportsUI);
$("exLocalTime").addEventListener("change", updateUtcPreview);

$("uiTimezone").addEventListener("change", () => {
  userTouchedUiTz = true;
  updateUtcPreview();
});

updateExportsUI();
loadConfig().catch(e => msg("❌ Error cargando config: " + e.message));
