function $(id) { return document.getElementById(id); }

const state = {
  bundle: null,
  publicSecrets: null,
  telemetryStatus: null,
  updateStatus: null,
  remoteConfigStatus: null,
  accessStatus: null,
  credentialsStatus: null,
};

function deepCopy(value) {
  return JSON.parse(JSON.stringify(value ?? null));
}

function emptyObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function emptyArray(value) {
  return Array.isArray(value) ? value : [];
}

function pretty(value) {
  return JSON.stringify(value ?? {}, null, 2);
}

function asInt(value, fallback) {
  const parsed = parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function formatTs(ts) {
  if (!ts) return "-";
  const date = new Date(Number(ts) * 1000);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString();
}

function setFeedback(text, tone = "secondary") {
  const alert = $("feedbackAlert");
  const body = $("feedbackText");
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

function fetchStarted(text) {
  setFeedback(text, "secondary");
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

function configuredSecret(summary, key) {
  return !!(summary && summary[key] && summary[key].configured);
}

function maskedSecret(summary, key) {
  return summary && summary[key] && summary[key].masked ? summary[key].masked : "";
}

function telemetrySecretSummary(destinationId) {
  const root = emptyObject(state.publicSecrets && state.publicSecrets.telemetry_destinations);
  return emptyObject(root[destinationId]);
}

function serviceSecretSummary(serviceName) {
  const root = emptyObject(state.publicSecrets && state.publicSecrets.services);
  return emptyObject(root[serviceName]);
}

function setButtonGroupState(ids, busy, busyLabel, idleLabel) {
  ids.forEach((id) => {
    const button = $(id);
    if (!button) return;
    button.disabled = busy;
    button.textContent = busy ? busyLabel : idleLabel;
  });
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function zonedDateForToday(tz, hh, mm) {
  const now = new Date();
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
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  const parts = fmt.formatToParts(guess);
  const get = (type) => parts.find((item) => item.type === type)?.value;
  const shownHH = parseInt(get("hour"), 10);
  const shownMM = parseInt(get("minute"), 10);
  const deltaMinutes = (shownHH * 60 + shownMM) - (hh * 60 + mm);
  return new Date(guess.getTime() - deltaMinutes * 60 * 1000);
}

function computeUtcTimeFromLocal(tz, localHHMM) {
  const [hh, mm] = (localHHMM || "00:00").split(":").map((value) => parseInt(value, 10));
  if (!Number.isFinite(hh) || !Number.isFinite(mm)) return "00:00";
  const dt = zonedDateForToday(tz || "UTC", hh, mm);
  return `${pad2(dt.getUTCHours())}:${pad2(dt.getUTCMinutes())}`;
}

function defaultDestination(kind = "webhook_https") {
  return {
    id: kind === "mqtt" ? "simple-mqtt" : "simple-webhook",
    enabled: false,
    kind,
    data_classes: ["weather_measurement"],
    schedule: {
      mode: "realtime",
      interval_seconds: 60,
    },
    batch_max_items: 1,
    retry_policy: {
      max_attempts: 10,
    },
    auth: {
      mode: "none",
      key_id: "",
    },
    webhook: {
      url: "",
      timeout_seconds: 5,
      headers: {},
      tls: {
        ca_bundle_path: "",
        pinned_pubkey_sha256: "",
      },
    },
    mqtt: {
      host: "",
      port: 8883,
      topic: "weatherpi/measurements",
      client_id: "",
      username: "",
      keepalive_seconds: 30,
      tls: {
        enabled: true,
        ca_bundle_path: "",
      },
    },
  };
}

function pickPrimaryDestination(destinations) {
  const items = emptyArray(destinations);
  return items.length ? deepCopy(items[0]) : null;
}

function primaryDestinationId(primary, kind) {
  const currentId = String(primary && primary.id ? primary.id : "").trim();
  if (!currentId) return kind === "mqtt" ? "simple-mqtt" : "simple-webhook";
  if (currentId === "simple-webhook" || currentId === "simple-mqtt") {
    return kind === "mqtt" ? "simple-mqtt" : "simple-webhook";
  }
  return currentId;
}

function computeHttpAuth(existingAuth, typedToken, summary, fallbackKeyId) {
  const auth = emptyObject(deepCopy(existingAuth)) || {};
  const currentMode = String(auth.mode || "none").trim().toLowerCase();
  const token = (typedToken || "").trim();
  if (token) {
    auth.mode = "bearer";
    auth.key_id = String(auth.key_id || fallbackKeyId || "").trim();
    return auth;
  }
  if (currentMode === "bearer") {
    auth.mode = configuredSecret(summary, "bearer_token") ? "bearer" : "none";
    return auth;
  }
  if (currentMode === "hmac") {
    auth.mode = configuredSecret(summary, "hmac_secret") ? "hmac" : "none";
    return auth;
  }
  auth.mode = "none";
  return auth;
}

function selectedDataClasses() {
  const dataClasses = [];
  if ($("dataWeather").checked) dataClasses.push("weather_measurement");
  if ($("dataStatus").checked) {
    dataClasses.push("station_status");
    dataClasses.push("update_status");
  }
  if ($("dataAlerts").checked) dataClasses.push("alert_event");
  return dataClasses;
}

function syncConditionalFields() {
  const isMqtt = $("deliveryKind").value === "mqtt";
  const isInterval = $("sendMode").value === "interval";
  const everyNDays = $("exFrequency").value === "every_n_days";

  $("mqttFields").classList.toggle("d-none", !isMqtt);
  $("webhookFields").classList.toggle("d-none", isMqtt);
  $("sendIntervalSeconds").disabled = !isInterval;
  $("exEveryDays").disabled = !everyNDays;
}

function syncGeneralPreview() {
  $("generalStatusBadge").textContent = $("collectorEnabled").checked ? "collector activo" : "collector pausado";
  $("generalSummary").textContent = $("collectorEnabled").checked
    ? "La estacion sigue recogiendo datos localmente aunque falle el servidor remoto."
    : "El collector esta desactivado: no se generaran nuevas mediciones hasta volver a activarlo.";
}

function syncDeliveryPreview() {
  const outboxSummary = emptyObject(state.telemetryStatus && state.telemetryStatus.outbox && state.telemetryStatus.outbox.summary);
  const summaryBits = [];
  summaryBits.push($("telemetryEnabled").checked ? "Envio activo." : "Envio desactivado.");
  if ($("telemetryEnabled").checked) {
    summaryBits.push($("deliveryKind").value === "mqtt" ? "Usando MQTT." : "Usando webhook HTTPS.");
    summaryBits.push($("sendMode").value === "interval"
      ? `Agrupado cada ${asInt($("sendIntervalSeconds").value, 60)} s.`
      : "Envio inmediato.");
  }
  summaryBits.push(`Outbox: ${outboxSummary.pending || 0} pendientes, ${outboxSummary.failed || 0} con error.`);
  $("deliverySummary").textContent = summaryBits.join(" ");
}

function syncAccessSelectionPreview() {
  const allowLan = $("accessLan").checked;
  $("accessModeBadge").textContent = allowLan ? "red local" : "solo local";
  const alert = $("accessAlert");
  alert.className = "alert";
  if (allowLan) {
    alert.classList.add("alert-warning");
    alert.textContent = "La red local permite acceso desde otros dispositivos de tu LAN. No significa acceso desde Internet.";
  } else {
    alert.classList.add("alert-secondary");
    alert.textContent = "La UI solo respondera en este dispositivo.";
  }
}

function syncExportsPreview() {
  const uploadUrl = ($("exUploadUrl").value || "").trim();
  const uploadText = uploadUrl ? `Tambien se intentara subir a ${uploadUrl}.` : "Solo se guardaran en local.";
  $("exportsSummary").textContent = $("exportsEnabled").checked
    ? `Los exports se generaran ${$("exFrequency").value === "daily" ? "a diario" : $("exFrequency").value === "weekly" ? "cada semana" : `cada ${asInt($("exEveryDays").value, 2)} dias`} a las ${$("exLocalTime").value || "01:00"}. ${uploadText}`
    : "Los CSV siguen disponibles bajo demanda, pero la programacion automatica esta desactivada.";
}

function renderAccessStatus() {
  const info = emptyObject(state.accessStatus);
  const allowLan = info.allow_lan === true;
  $("accessLocalOnly").checked = !allowLan;
  $("accessLan").checked = allowLan;
  $("blockDefaults").checked = emptyObject(state.bundle && state.bundle.local_config && state.bundle.local_config.security).block_remote_when_default_local_credentials !== false;
  $("accessModeBadge").textContent = allowLan ? "red local" : "solo local";
  $("accessHost").textContent = info.runtime_bind_host || "-";
  $("accessPort").textContent = info.runtime_port || "-";

  const localLink = $("accessLocalUrl");
  localLink.textContent = info.loopback_origin || "-";
  localLink.href = info.loopback_origin || "#";

  const lanLink = $("accessLanUrl");
  lanLink.textContent = info.lan_origin || "no disponible";
  lanLink.href = info.lan_origin || "#";

  const alert = $("accessAlert");
  alert.className = "alert";
  if (!allowLan) {
    alert.classList.add("alert-secondary");
    alert.textContent = "La UI solo responde en este dispositivo.";
  } else if (info.restart_required) {
    alert.classList.add("alert-warning");
    alert.textContent = info.launcher_managed
      ? "La configuracion de acceso LAN se ha guardado. El launcher reaplicara el API en unos segundos."
      : "La configuracion de acceso LAN se ha guardado, pero debes reiniciar el API para aplicarla.";
  } else {
    alert.classList.add("alert-success");
    alert.textContent = "El acceso LAN esta activo. Si no ves la UI desde otro dispositivo, revisa firewall y puerto.";
  }

  const guidance = $("accessGuidance");
  guidance.innerHTML = "";
  emptyArray(info.guidance).forEach((line) => {
    const li = document.createElement("li");
    li.textContent = line;
    guidance.appendChild(li);
  });
}

function renderCredentialsStatus() {
  const info = emptyObject(state.credentialsStatus);
  $("credReaderUser").value = info.reader_username || "reader";
  $("credAdminUser").value = info.admin_username || "admin";
  $("credentialsSummary").textContent = info.default_credentials_active
    ? "Debes cambiar las credenciales por defecto para habilitar funciones remotas con seguridad."
    : "Credenciales locales actualizadas.";

  const alert = $("credentialsAlert");
  alert.className = "alert";
  if (info.default_credentials_active) {
    alert.classList.add("alert-warning");
    alert.textContent = "Credenciales por defecto detectadas. Cambialas cuanto antes.";
  } else {
    alert.classList.add("alert-success");
    alert.textContent = "Credenciales personalizadas activas.";
  }
}

function renderGeneral() {
  const local = emptyObject(state.bundle && state.bundle.local_config);
  const collector = emptyObject(local.collector);
  $("stationId").value = local.station_id || "meteo-001";
  $("sampleInterval").value = local.sample_interval_seconds || 5;
  $("collectorEnabled").checked = collector.enabled !== false;
  $("generalStatusBadge").textContent = $("collectorEnabled").checked ? "collector activo" : "collector pausado";
  $("generalSummary").textContent = $("collectorEnabled").checked
    ? "La estacion sigue recogiendo datos localmente aunque falle el servidor remoto."
    : "El collector esta desactivado: no se generaran nuevas mediciones hasta volver a activarlo.";
}

function renderDelivery() {
  const local = emptyObject(state.bundle && state.bundle.local_config);
  const telemetry = emptyObject(local.telemetry);
  const destinations = emptyArray(telemetry.destinations);
  const primary = pickPrimaryDestination(destinations) || defaultDestination("webhook_https");
  const kind = primary.kind === "mqtt" ? "mqtt" : "webhook_https";
  const schedule = emptyObject(primary.schedule);
  const destinationId = primaryDestinationId(primary, kind);
  const secretSummary = telemetrySecretSummary(destinationId);
  const outboxSummary = emptyObject(state.telemetryStatus && state.telemetryStatus.outbox && state.telemetryStatus.outbox.summary);

  $("telemetryEnabled").checked = !!telemetry.enabled;
  $("deliveryKind").value = kind;
  $("sendMode").value = schedule.mode === "interval" ? "interval" : "realtime";
  $("sendIntervalSeconds").value = schedule.interval_seconds || 60;
  $("webhookUrl").value = emptyObject(primary.webhook).url || "";
  $("webhookToken").value = "";
  $("mqttHost").value = emptyObject(primary.mqtt).host || "";
  $("mqttPort").value = emptyObject(primary.mqtt).port || 8883;
  $("mqttTopic").value = emptyObject(primary.mqtt).topic || "weatherpi/measurements";
  $("mqttUsername").value = emptyObject(primary.mqtt).username || "";
  $("mqttPassword").value = "";
  const dataClasses = emptyArray(primary.data_classes);
  $("dataWeather").checked = dataClasses.includes("weather_measurement");
  $("dataStatus").checked = dataClasses.includes("station_status") || dataClasses.includes("update_status");
  $("dataAlerts").checked = dataClasses.includes("alert_event");
  $("deliveryDestinationsBadge").textContent = Math.max(destinations.length - 1, 0);

  $("webhookTokenHint").textContent = configuredSecret(secretSummary, "bearer_token")
    ? `Token guardado: ${maskedSecret(secretSummary, "bearer_token")}`
    : "Sin token guardado.";
  $("mqttPasswordHint").textContent = configuredSecret(secretSummary, "password")
    ? `Password guardada: ${maskedSecret(secretSummary, "password")}`
    : "Sin password guardada.";

  const summaryBits = [];
  summaryBits.push($("telemetryEnabled").checked ? "Envio activo." : "Envio desactivado.");
  if ($("telemetryEnabled").checked) {
    summaryBits.push($("deliveryKind").value === "mqtt" ? "Usando MQTT." : "Usando webhook HTTPS.");
    summaryBits.push($("sendMode").value === "interval"
      ? `Agrupado cada ${asInt($("sendIntervalSeconds").value, 60)} s.`
      : "Envio inmediato.");
  }
  summaryBits.push(`Outbox: ${outboxSummary.pending || 0} pendientes, ${outboxSummary.failed || 0} con error.`);
  $("deliverySummary").textContent = summaryBits.join(" ");

  syncConditionalFields();
}

function renderRemoteConfig() {
  const local = emptyObject(state.bundle && state.bundle.local_config);
  const cfg = emptyObject(local.remote_config);
  const status = emptyObject(state.remoteConfigStatus && state.remoteConfigStatus.state);
  const secrets = serviceSecretSummary("remote_config");

  $("remoteConfigEnabled").checked = !!cfg.enabled;
  $("rcEndpoint").value = cfg.endpoint || "";
  $("rcPollInterval").value = cfg.poll_interval_seconds || 900;
  $("rcAutoApply").checked = cfg.auto_apply !== false;
  $("rcToken").value = "";
  $("rcTokenHint").textContent = configuredSecret(secrets, "bearer_token")
    ? `Token guardado: ${maskedSecret(secrets, "bearer_token")}`
    : "Sin token guardado.";
  $("rcLastCheck").textContent = formatTs(status.last_check_ts);
  $("rcCurrentRevision").textContent = status.current_revision || "-";
  $("rcStatus").textContent = status.status || "idle";
  $("rcError").textContent = status.last_error || "-";
}

function renderUpdates() {
  const local = emptyObject(state.bundle && state.bundle.local_config);
  const cfg = emptyObject(local.updates);
  const status = emptyObject(state.updateStatus && state.updateStatus.state);
  const secrets = serviceSecretSummary("updates");

  $("updatesEnabled").checked = !!cfg.enabled;
  $("updatesUrl").value = cfg.manifest_url || "";
  $("updatesChannel").value = cfg.channel || "stable";
  $("updatesToken").value = "";
  $("updatesTokenHint").textContent = configuredSecret(secrets, "bearer_token")
    ? `Token guardado: ${maskedSecret(secrets, "bearer_token")}`
    : "Sin token guardado.";

  $("updatesCurrentVersion").textContent = status.current_version || "-";
  $("updatesLastCheck").textContent = formatTs(status.last_check_ts);
  $("updatesStatus").textContent = status.status || "idle";
  const available = ["available", "downloading", "verified"].includes(String(status.status || ""));
  $("updatesAvailable").textContent = available
    ? `Si (${status.target_version || "nueva version"})`
    : "No";

  let summary = "La estacion consulta actualizaciones en modo pull y nunca abre acceso entrante.";
  if (status.status === "verified") {
    summary = `La version ${status.target_version || "-"} ya esta descargada y verificada.`;
  } else if (status.status === "failed" && status.last_error) {
    summary = `Ultimo error: ${status.last_error}`;
  } else if (status.status === "available") {
    summary = `Hay una actualizacion disponible: ${status.target_version || "-"}.`;
  }
  $("updatesSummary").textContent = summary;
}

function renderExports() {
  const local = emptyObject(state.bundle && state.bundle.local_config);
  const cfg = emptyObject(local.exports);
  const upload = emptyObject(cfg.upload);
  const schedule = emptyObject(cfg.schedule);

  $("exportsEnabled").checked = !!cfg.enabled;
  $("exFrequency").value = cfg.frequency || "daily";
  $("exEveryDays").value = cfg.every_days || 2;
  $("exLocalTime").value = schedule.time_local || "01:00";
  $("exKeepDays").value = cfg.keep_days || 30;
  $("exUploadUrl").value = upload.webhook_url || "";

  const uploadText = upload.enabled && upload.webhook_url
    ? `Tambien se intentara subir a ${upload.webhook_url}.`
    : "Solo se guardaran en local.";
  $("exportsSummary").textContent = $("exportsEnabled").checked
    ? `Los exports se generaran ${$("exFrequency").value === "daily" ? "a diario" : $("exFrequency").value === "weekly" ? "cada semana" : `cada ${asInt($("exEveryDays").value, 2)} dias`} a las ${$("exLocalTime").value || "01:00"}. ${uploadText}`
    : "Los CSV siguen disponibles bajo demanda, pero la programacion automatica esta desactivada.";

  syncConditionalFields();
}

function renderAdvanced() {
  const local = emptyObject(state.bundle && state.bundle.local_config);
  const telemetry = emptyObject(local.telemetry);
  $("advancedDestinations").value = pretty(telemetry.destinations || []);
  $("advancedLocalConfig").value = pretty(local);
  $("secretSummary").textContent = pretty(state.publicSecrets || {});
  $("effectiveSummary").textContent = pretty((state.bundle && state.bundle.config) || {});
  $("remoteOverlaySummary").textContent = pretty((state.bundle && state.bundle.remote_overlay) || {});
  $("remoteConfigStatusJson").textContent = pretty((state.remoteConfigStatus && state.remoteConfigStatus.state) || {});
  $("updateStateSummary").textContent = pretty((state.updateStatus && state.updateStatus.state) || {});
}

async function refreshPage() {
  const [bundle, telemetryStatus, updateStatus, remoteConfigStatus, accessStatus, credentialsStatus] = await Promise.all([
    fetchJson("/api/config", { cache: "no-store" }),
    fetchJson("/api/telemetry/status", { cache: "no-store" }),
    fetchJson("/api/update/status", { cache: "no-store" }),
    fetchJson("/api/remote-config/status", { cache: "no-store" }),
    fetchJson("/api/system/access", { cache: "no-store" }),
    fetchJson("/api/security/credentials", { cache: "no-store" }),
  ]);

  state.bundle = bundle;
  state.publicSecrets = bundle.secrets || {};
  state.telemetryStatus = telemetryStatus;
  state.updateStatus = updateStatus;
  state.remoteConfigStatus = remoteConfigStatus;
  state.accessStatus = accessStatus;
  state.credentialsStatus = credentialsStatus;

  renderGeneral();
  renderAccessStatus();
  renderDelivery();
  renderRemoteConfig();
  renderUpdates();
  renderCredentialsStatus();
  renderExports();
  renderAdvanced();
}

function buildMainConfigPayload() {
  const payload = deepCopy(state.bundle && state.bundle.local_config) || {};
  const telemetry = emptyObject(payload.telemetry);
  const currentDestinations = emptyArray(telemetry.destinations);
  const currentPrimary = pickPrimaryDestination(currentDestinations) || defaultDestination($("deliveryKind").value);
  const kind = $("deliveryKind").value === "mqtt" ? "mqtt" : "webhook_https";
  const primary = deepCopy(currentPrimary) || defaultDestination(kind);
  const destinationId = primaryDestinationId(primary, kind);
  const destinationSecrets = telemetrySecretSummary(destinationId);
  const rcSecrets = serviceSecretSummary("remote_config");
  const updateSecrets = serviceSecretSummary("updates");
  const timezone = emptyObject(payload.ui).timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const dataClasses = selectedDataClasses();

  if ($("telemetryEnabled").checked && !dataClasses.length) {
    throw new Error("Selecciona al menos un tipo de dato para enviar.");
  }

  payload.station_id = ($("stationId").value || "").trim() || "meteo-001";
  payload.sample_interval_seconds = asInt($("sampleInterval").value, 5);
  payload.collector = emptyObject(payload.collector);
  payload.collector.enabled = $("collectorEnabled").checked;

  payload.telemetry = emptyObject(payload.telemetry);
  payload.telemetry.enabled = $("telemetryEnabled").checked;
  primary.id = destinationId;
  primary.enabled = $("telemetryEnabled").checked;
  primary.kind = kind;
  primary.data_classes = dataClasses;
  primary.schedule = {
    mode: $("sendMode").value === "interval" ? "interval" : "realtime",
    interval_seconds: Math.max(5, asInt($("sendIntervalSeconds").value, 60)),
  };
  primary.batch_max_items = primary.schedule.mode === "interval" ? Math.max(5, asInt(primary.batch_max_items, 25)) : 1;
  primary.retry_policy = emptyObject(primary.retry_policy);
  primary.retry_policy.max_attempts = Math.max(1, asInt(primary.retry_policy.max_attempts, 10));
  primary.webhook = emptyObject(primary.webhook);
  primary.webhook.url = ($("webhookUrl").value || "").trim();
  primary.webhook.timeout_seconds = Math.max(1, asInt(primary.webhook.timeout_seconds, 5));
  primary.webhook.headers = emptyObject(primary.webhook.headers);
  primary.webhook.tls = emptyObject(primary.webhook.tls);
  primary.mqtt = emptyObject(primary.mqtt);
  primary.mqtt.host = ($("mqttHost").value || "").trim();
  primary.mqtt.port = Math.max(1, asInt($("mqttPort").value, 8883));
  primary.mqtt.topic = ($("mqttTopic").value || "").trim() || "weatherpi/measurements";
  primary.mqtt.username = ($("mqttUsername").value || "").trim();
  primary.mqtt.client_id = String(primary.mqtt.client_id || payload.station_id || "weatherpi").trim();
  primary.mqtt.keepalive_seconds = Math.max(5, asInt(primary.mqtt.keepalive_seconds, 30));
  primary.mqtt.tls = emptyObject(primary.mqtt.tls);
  primary.mqtt.tls.enabled = primary.mqtt.tls.enabled !== false;

  if (kind === "webhook_https") {
    primary.auth = computeHttpAuth(primary.auth, $("webhookToken").value, destinationSecrets, payload.station_id);
    if (payload.telemetry.enabled && !primary.webhook.url) {
      throw new Error("Indica la URL del webhook.");
    }
  } else {
    primary.auth = emptyObject(primary.auth);
    primary.auth.mode = "none";
    if (payload.telemetry.enabled && !primary.mqtt.host) {
      throw new Error("Indica el servidor MQTT.");
    }
  }

  payload.telemetry.destinations = [primary, ...deepCopy(currentDestinations.slice(1))];

  payload.remote_config = emptyObject(payload.remote_config);
  payload.remote_config.enabled = $("remoteConfigEnabled").checked;
  payload.remote_config.endpoint = ($("rcEndpoint").value || "").trim();
  payload.remote_config.poll_interval_seconds = Math.max(60, asInt($("rcPollInterval").value, 900));
  payload.remote_config.auto_apply = $("rcAutoApply").checked;
  payload.remote_config.auth = computeHttpAuth(payload.remote_config.auth, $("rcToken").value, rcSecrets, payload.station_id);
  if (payload.remote_config.enabled && !payload.remote_config.endpoint) {
    throw new Error("Indica el endpoint de configuracion remota.");
  }

  payload.updates = emptyObject(payload.updates);
  payload.updates.enabled = $("updatesEnabled").checked;
  payload.updates.manifest_url = ($("updatesUrl").value || "").trim();
  payload.updates.channel = ($("updatesChannel").value || "stable").trim() || "stable";
  payload.updates.auth = computeHttpAuth(payload.updates.auth, $("updatesToken").value, updateSecrets, payload.station_id);
  if (payload.updates.enabled && !payload.updates.manifest_url) {
    throw new Error("Indica la URL del servidor de updates.");
  }

  payload.security = emptyObject(payload.security);
  payload.security.allow_lan = $("accessLan").checked;
  payload.security.api_bind_host = $("accessLan").checked ? "0.0.0.0" : "127.0.0.1";
  payload.security.block_remote_when_default_local_credentials = $("blockDefaults").checked;

  payload.exports = emptyObject(payload.exports);
  payload.exports.enabled = $("exportsEnabled").checked;
  payload.exports.frequency = $("exFrequency").value;
  payload.exports.every_days = Math.max(2, asInt($("exEveryDays").value, 2));
  payload.exports.keep_days = Math.max(1, asInt($("exKeepDays").value, 30));
  payload.exports.schedule = emptyObject(payload.exports.schedule);
  payload.exports.schedule.time_local = $("exLocalTime").value || "01:00";
  payload.exports.schedule.time_utc = computeUtcTimeFromLocal(timezone, payload.exports.schedule.time_local);
  payload.exports.upload = emptyObject(payload.exports.upload);
  payload.exports.upload.enabled = !!(($("exUploadUrl").value || "").trim());
  payload.exports.upload.webhook_url = ($("exUploadUrl").value || "").trim();

  payload.ui = emptyObject(payload.ui);
  payload.ui.timezone = timezone;

  return {
    payload,
    primaryDestinationId: destinationId,
    primaryKind: kind,
  };
}

function buildMainSecretPatch(primaryDestinationId, primaryKind) {
  const patch = {};
  const destinationPatch = {};
  const webhookToken = ($("webhookToken").value || "").trim();
  const mqttPassword = ($("mqttPassword").value || "").trim();
  const rcToken = ($("rcToken").value || "").trim();
  const updatesToken = ($("updatesToken").value || "").trim();

  if (primaryKind === "webhook_https" && webhookToken) {
    destinationPatch.bearer_token = webhookToken;
  }
  if (primaryKind === "mqtt" && mqttPassword) {
    destinationPatch.password = mqttPassword;
  }
  if (Object.keys(destinationPatch).length) {
    patch.telemetry_destinations = {
      [primaryDestinationId]: destinationPatch,
    };
  }
  if (rcToken) {
    patch.remote_config = {
      bearer_token: rcToken,
    };
  }
  if (updatesToken) {
    patch.updates = {
      bearer_token: updatesToken,
    };
  }
  return patch;
}

async function saveMainSettings(options = {}) {
  const { testAfter = false } = options;
  const built = buildMainConfigPayload();
  await fetchJson("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(built.payload),
  });

  const patch = buildMainSecretPatch(built.primaryDestinationId, built.primaryKind);
  if (Object.keys(patch).length) {
    await fetchJson("/api/config/secrets", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
  }

  await refreshPage();

  if (testAfter) {
    const test = await fetchJson("/api/telemetry/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destination_id: built.primaryDestinationId }),
    });
    setFeedback(`Cambios guardados. La prueba de envio se ha encolado para ${test.destination_id}.`, "success");
    return;
  }

  setFeedback("Cambios guardados.", "success");
}

async function saveCredentials() {
  const payload = {
    reader_username: ($("credReaderUser").value || "").trim(),
    reader_password: ($("credReaderPass").value || "").trim() || null,
    reader_password_confirm: ($("credReaderPass2").value || "").trim() || null,
    admin_username: ($("credAdminUser").value || "").trim(),
    admin_password: ($("credAdminPass").value || "").trim() || null,
    admin_password_confirm: ($("credAdminPass2").value || "").trim() || null,
  };
  const data = await fetchJson("/api/security/credentials", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  $("credReaderPass").value = "";
  $("credReaderPass2").value = "";
  $("credAdminPass").value = "";
  $("credAdminPass2").value = "";
  state.credentialsStatus = data.credentials || {};
  renderCredentialsStatus();
  setFeedback("Credenciales actualizadas.", "success");
}

function parseJsonArea(id, fallback) {
  const raw = ($(id).value || "").trim();
  if (!raw) return fallback;
  return JSON.parse(raw);
}

async function saveAdvancedConfig() {
  const payload = parseJsonArea("advancedLocalConfig", {});
  await fetchJson("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await refreshPage();
  setFeedback("Configuracion avanzada guardada.", "success");
}

async function saveAdvancedDestinations() {
  const destinations = parseJsonArea("advancedDestinations", []);
  if (!Array.isArray(destinations)) {
    throw new Error("Destinations JSON debe ser un array.");
  }
  const payload = deepCopy(state.bundle && state.bundle.local_config) || {};
  payload.telemetry = emptyObject(payload.telemetry);
  payload.telemetry.destinations = destinations;
  payload.telemetry.enabled = !!payload.telemetry.enabled || destinations.some((item) => !!(item && item.enabled));
  await fetchJson("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await refreshPage();
  setFeedback("Destinos avanzados guardados.", "success");
}

async function saveAdvancedSecrets() {
  const patch = parseJsonArea("advancedSecretPatch", {});
  await fetchJson("/api/config/secrets", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  $("advancedSecretPatch").value = "";
  await refreshPage();
  setFeedback("Secrets guardados.", "success");
}

async function runAction(buttonId, url, idleLabel, busyLabel, successMessage) {
  setButtonGroupState([buttonId], true, busyLabel, idleLabel);
  try {
    await fetchJson(url, { method: "POST" });
    await refreshPage();
    setFeedback(successMessage, "success");
  } finally {
    setButtonGroupState([buttonId], false, busyLabel, idleLabel);
  }
}

function wireInputs() {
  ["stationId", "sampleInterval"].forEach((id) => {
    $(id).addEventListener("input", syncGeneralPreview);
  });
  $("collectorEnabled").addEventListener("change", syncGeneralPreview);

  [
    "telemetryEnabled",
    "deliveryKind",
    "sendMode",
    "sendIntervalSeconds",
    "dataWeather",
    "dataStatus",
    "dataAlerts",
  ].forEach((id) => {
    $(id).addEventListener("change", () => {
      syncConditionalFields();
      syncDeliveryPreview();
    });
  });

  ["accessLocalOnly", "accessLan"].forEach((id) => {
    $(id).addEventListener("change", syncAccessSelectionPreview);
  });

  [
    "exportsEnabled",
    "exFrequency",
    "exEveryDays",
    "exLocalTime",
    "exUploadUrl",
  ].forEach((id) => {
    $(id).addEventListener("change", () => {
      syncConditionalFields();
      syncExportsPreview();
    });
  });
}

function bindMainSave(buttonIds, testAfter = false) {
  buttonIds.forEach((id) => {
    $(id).addEventListener("click", () => {
      fetchStarted(testAfter ? "Guardando cambios y preparando prueba..." : "Guardando cambios...");
      const targetIds = testAfter ? ["saveAndTestBtn", "saveAndTestBottomBtn"] : ["saveSettingsBtn", "saveSettingsBottomBtn"];
      const idleLabel = testAfter ? "Guardar y probar envio" : "Guardar cambios";
      const busyLabel = "Guardando...";
      setButtonGroupState(targetIds, true, busyLabel, idleLabel);
      saveMainSettings({ testAfter })
        .catch((err) => setFeedback(`No se pudo guardar. ${err.message}`, "danger"))
        .finally(() => setButtonGroupState(targetIds, false, busyLabel, idleLabel));
    });
  });
}

bindMainSave(["saveSettingsBtn", "saveSettingsBottomBtn"], false);
bindMainSave(["saveAndTestBtn", "saveAndTestBottomBtn"], true);

$("saveCredentialsBtn").addEventListener("click", () => {
  fetchStarted("Guardando credenciales...");
  setButtonGroupState(["saveCredentialsBtn"], true, "Guardando...", "Guardar credenciales");
  saveCredentials()
    .catch((err) => setFeedback(`No se pudieron guardar las credenciales. ${err.message}`, "danger"))
    .finally(() => setButtonGroupState(["saveCredentialsBtn"], false, "Guardando...", "Guardar credenciales"));
});

$("saveAdvancedConfigBtn").addEventListener("click", () => {
  fetchStarted("Guardando configuracion avanzada...");
  setButtonGroupState(["saveAdvancedConfigBtn"], true, "Guardando...", "Guardar config JSON");
  saveAdvancedConfig()
    .catch((err) => setFeedback(`No se pudo guardar la configuracion avanzada. ${err.message}`, "danger"))
    .finally(() => setButtonGroupState(["saveAdvancedConfigBtn"], false, "Guardando...", "Guardar config JSON"));
});

$("saveDestinationsBtn").addEventListener("click", () => {
  fetchStarted("Guardando destinos avanzados...");
  setButtonGroupState(["saveDestinationsBtn"], true, "Guardando...", "Guardar destinos");
  saveAdvancedDestinations()
    .catch((err) => setFeedback(`No se pudieron guardar los destinos. ${err.message}`, "danger"))
    .finally(() => setButtonGroupState(["saveDestinationsBtn"], false, "Guardando...", "Guardar destinos"));
});

$("saveSecretsBtn").addEventListener("click", () => {
  fetchStarted("Guardando secrets...");
  setButtonGroupState(["saveSecretsBtn"], true, "Guardando...", "Guardar secrets");
  saveAdvancedSecrets()
    .catch((err) => setFeedback(`No se pudieron guardar los secrets. ${err.message}`, "danger"))
    .finally(() => setButtonGroupState(["saveSecretsBtn"], false, "Guardando...", "Guardar secrets"));
});

$("checkRemoteConfigBtn").addEventListener("click", () => {
  runAction("checkRemoteConfigBtn", "/api/remote-config/check-now", "Comprobar ahora", "Comprobando...", "Comprobacion de configuracion remota completada.")
    .catch((err) => setFeedback(`No se pudo comprobar la configuracion remota. ${err.message}`, "danger"));
});

$("checkUpdateBtn").addEventListener("click", () => {
  runAction("checkUpdateBtn", "/api/update/check-now", "Buscar ahora", "Buscando...", "Comprobacion de updates completada.")
    .catch((err) => setFeedback(`No se pudo buscar actualizaciones. ${err.message}`, "danger"));
});

$("applyUpdateBtn").addEventListener("click", () => {
  runAction("applyUpdateBtn", "/api/update/apply", "Aplicar actualizacion descargada", "Aplicando...", "Actualizacion aplicada.")
    .catch((err) => setFeedback(`No se pudo aplicar la actualizacion. ${err.message}`, "danger"));
});

$("rollbackUpdateBtn").addEventListener("click", () => {
  runAction("rollbackUpdateBtn", "/api/update/rollback", "Volver a la version anterior", "Volviendo...", "Se ha solicitado volver a la version anterior.")
    .catch((err) => setFeedback(`No se pudo volver a la version anterior. ${err.message}`, "danger"));
});

wireInputs();
refreshPage()
  .then(() => setFeedback("Configuracion cargada.", "secondary"))
  .catch((err) => setFeedback(`No se pudo cargar la configuracion. ${err.message}`, "danger"));
