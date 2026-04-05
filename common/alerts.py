from __future__ import annotations

import copy
from typing import Any, Dict, List, Tuple

from common.security import sha256_json, short_stable_id

VALID_ALERT_OPERATORS = {">", "<", ">=", "<=", "==", "!=", "missing_for"}
VALID_ALERT_SEVERITIES = {"info", "warning", "critical"}
ALERT_METRIC_LABELS = {
    "temperature_c": "Temperatura",
    "humidity_pct": "Humedad",
    "pressure_hpa": "Presion",
    "sensor_last_seen_seconds": "Tiempo sin lecturas",
    "collector_error_count": "Errores consecutivos del collector",
    "telemetry_backlog_seconds": "Tiempo con telemetria sin enviar",
    "temperature_invalid": "Temperatura invalida o sospechosa",
    "humidity_invalid": "Humedad invalida o sospechosa",
    "pressure_invalid": "Presion invalida o sospechosa",
}
ALERT_METRIC_ALIASES = {
    "temp_c": "temperature_c",
    "temperature_c": "temperature_c",
    "humidity_pct": "humidity_pct",
    "pressure_hpa": "pressure_hpa",
    "sensor_last_seen_seconds": "sensor_last_seen_seconds",
    "collector_error_count": "collector_error_count",
    "sensor_error_count": "collector_error_count",
    "telemetry_backlog_seconds": "telemetry_backlog_seconds",
    "temperature_invalid": "temperature_invalid",
    "humidity_invalid": "humidity_invalid",
    "pressure_invalid": "pressure_invalid",
}
ALERT_SOURCE_LABELS = {
    "local": "Modificada localmente",
    "remote": "Aplicada desde servidor remoto",
}
ALERT_SYNC_STATUS_LABELS = {
    "in_sync": "Sincronizada",
    "remote_pending": "Cambios remotos pendientes",
    "applying_remote": "Aplicando cambios remotos",
    "local_override": "Modificada localmente",
    "conflict": "Conflicto",
    "apply_failed": "Error al aplicar",
}


def default_alert_rule(index: int = 0) -> Dict[str, Any]:
    return {
        "id": f"alert-rule-{index + 1}",
        "enabled": True,
        "name": f"Regla {index + 1}",
        "metric": "temperature_c",
        "operator": ">",
        "threshold": 35.0,
        "for_seconds": 60,
        "cooldown_seconds": 1800,
        "severity": "warning",
        "message_template": "",
        "source_scope": "",
    }


def default_alerts_config() -> Dict[str, Any]:
    return {
        "enabled": False,
        "rules": [],
    }


def _as_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(fallback)


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(fallback)


def _canonical_metric(metric: Any) -> str:
    raw = str(metric or "").strip()
    return ALERT_METRIC_ALIASES.get(raw, raw or "temperature_c")


def normalize_alert_rule(raw: Any, index: int = 0) -> Dict[str, Any]:
    source = copy.deepcopy(raw if isinstance(raw, dict) else {})
    base = default_alert_rule(index)
    base.update(source)
    base["id"] = str(base.get("id") or f"alert-rule-{index + 1}").strip() or f"alert-rule-{index + 1}"
    base["enabled"] = bool(base.get("enabled", True))
    base["name"] = str(base.get("name") or f"Regla {index + 1}").strip() or f"Regla {index + 1}"
    base["metric"] = _canonical_metric(base.get("metric"))
    operator = str(base.get("operator") or ">").strip()
    base["operator"] = operator if operator in VALID_ALERT_OPERATORS else ">"
    base["threshold"] = _as_float(base.get("threshold"), 0.0)
    base["for_seconds"] = max(0, min(_as_int(base.get("for_seconds"), 0), 7 * 24 * 3600))
    base["cooldown_seconds"] = max(0, min(_as_int(base.get("cooldown_seconds"), 1800), 30 * 24 * 3600))
    severity = str(base.get("severity") or "warning").strip().lower()
    base["severity"] = severity if severity in VALID_ALERT_SEVERITIES else "warning"
    base["message_template"] = str(base.get("message_template") or "").strip()
    base["source_scope"] = str(base.get("source_scope") or "").strip()
    return base


def normalize_alerts_config(raw: Any) -> Dict[str, Any]:
    source = copy.deepcopy(raw if isinstance(raw, dict) else {})
    normalized = default_alerts_config()
    normalized.update(source)
    normalized["enabled"] = bool(normalized.get("enabled", False))
    rules = normalized.get("rules")
    if not isinstance(rules, list):
        rules = []
    normalized["rules"] = [normalize_alert_rule(rule, index=index) for index, rule in enumerate(rules)]
    return normalized


def validate_alerts_config(raw: Any) -> str | None:
    try:
        cfg = normalize_alerts_config(raw)
    except Exception as exc:
        return f"alertas invalidas: {exc}"

    seen_ids = set()
    for rule in cfg.get("rules", []):
        rule_id = str(rule.get("id") or "")
        if not rule_id:
            return "alerts.rules[].id es obligatorio"
        if rule_id in seen_ids:
            return f"alerts.rules contiene ids duplicados: {rule_id}"
        seen_ids.add(rule_id)

        metric = str(rule.get("metric") or "")
        if metric not in ALERT_METRIC_LABELS:
            return f"alerts.rules[{rule_id}].metric no soportada"
        operator = str(rule.get("operator") or "")
        if operator not in VALID_ALERT_OPERATORS:
            return f"alerts.rules[{rule_id}].operator no soportado"
        severity = str(rule.get("severity") or "")
        if severity not in VALID_ALERT_SEVERITIES:
            return f"alerts.rules[{rule_id}].severity no soportada"
    return None


def alert_config_hash(alerts_cfg: Dict[str, Any] | None) -> str:
    return sha256_json(normalize_alerts_config(alerts_cfg))


def alert_rule_options() -> List[Dict[str, str]]:
    return [{"value": metric, "label": label} for metric, label in ALERT_METRIC_LABELS.items()]


def operator_options() -> List[Dict[str, str]]:
    return [{"value": operator, "label": operator} for operator in sorted(VALID_ALERT_OPERATORS)]


def severity_options() -> List[Dict[str, str]]:
    return [{"value": severity, "label": severity} for severity in ("info", "warning", "critical")]


def default_rule_state(rule_id: str) -> Dict[str, Any]:
    return {
        "rule_id": rule_id,
        "active": False,
        "condition_since_ts": None,
        "last_fired_ts": None,
        "last_resolved_ts": None,
        "last_value": None,
        "last_evaluated_ts": None,
        "last_status": "idle",
        "last_message": "",
    }


def metric_value(values: Dict[str, Any], metric: str) -> Any:
    canonical = _canonical_metric(metric)
    if canonical in values:
        return values.get(canonical)
    for raw_metric, candidate in ALERT_METRIC_ALIASES.items():
        if candidate == canonical and raw_metric in values:
            return values.get(raw_metric)
    return None


def compare_metric(current_value: Any, operator: str, threshold: float) -> bool:
    if operator == "missing_for":
        if current_value is None:
            return True
        try:
            return float(current_value) >= float(threshold)
        except Exception:
            return False

    if current_value is None:
        return False

    if operator in {">", "<", ">=", "<="}:
        try:
            current = float(current_value)
            target = float(threshold)
        except Exception:
            return False
        if operator == ">":
            return current > target
        if operator == "<":
            return current < target
        if operator == ">=":
            return current >= target
        return current <= target

    if operator == "==":
        return current_value == threshold
    if operator == "!=":
        return current_value != threshold
    return False


def build_rule_message(rule: Dict[str, Any], current_value: Any, status: str) -> str:
    template = str(rule.get("message_template") or "").strip()
    if template:
        try:
            return template.format(
                name=rule.get("name"),
                metric=rule.get("metric"),
                operator=rule.get("operator"),
                threshold=rule.get("threshold"),
                current_value=current_value,
                severity=rule.get("severity"),
                status=status,
            )
        except Exception:
            pass

    metric_label = ALERT_METRIC_LABELS.get(str(rule.get("metric") or ""), str(rule.get("metric") or "Metrica"))
    if status == "resolved":
        return f"{rule.get('name') or metric_label} se ha recuperado"
    return f"{rule.get('name') or metric_label}: {metric_label} {rule.get('operator')} {rule.get('threshold')}"


def build_rule_event(rule: Dict[str, Any], current_value: Any, status: str, *, now_ts: int, config_source: str) -> Dict[str, Any]:
    message = build_rule_message(rule, current_value, status)
    return {
        "rule_id": rule.get("id"),
        "name": rule.get("name"),
        "metric": rule.get("metric"),
        "operator": rule.get("operator"),
        "threshold": rule.get("threshold"),
        "current_value": current_value,
        "severity": rule.get("severity"),
        "message": message,
        "source": str(rule.get("source_scope") or "alerts"),
        "config_source": config_source,
        "ts": int(now_ts),
        "status": status,
    }


def evaluate_alert_rules(
    alerts_cfg: Dict[str, Any] | None,
    values: Dict[str, Any],
    existing_states: Dict[str, Dict[str, Any]],
    *,
    now_ts: int,
    config_source: str = "local",
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    normalized = normalize_alerts_config(alerts_cfg)
    next_states: Dict[str, Dict[str, Any]] = {}
    events: List[Dict[str, Any]] = []

    for rule in normalized.get("rules", []):
        rule_id = str(rule.get("id") or "")
        state = copy.deepcopy(existing_states.get(rule_id) or default_rule_state(rule_id))
        current_value = metric_value(values, str(rule.get("metric") or ""))
        condition_met = bool(rule.get("enabled")) and compare_metric(current_value, str(rule.get("operator") or ""), float(rule.get("threshold") or 0.0))

        if condition_met:
            if not state.get("condition_since_ts"):
                state["condition_since_ts"] = int(now_ts)
            held_seconds = int(now_ts) - int(state.get("condition_since_ts") or now_ts)
            ready = held_seconds >= int(rule.get("for_seconds") or 0)
            active = bool(state.get("active"))
            last_fired_ts = state.get("last_fired_ts")
            cooldown_ok = last_fired_ts is None or (int(now_ts) - int(last_fired_ts)) >= int(rule.get("cooldown_seconds") or 0)
            if ready and not active and cooldown_ok:
                events.append(build_rule_event(rule, current_value, "fired", now_ts=now_ts, config_source=config_source))
                state["active"] = True
                state["last_fired_ts"] = int(now_ts)
                state["last_status"] = "fired"
                state["last_message"] = events[-1]["message"]
        else:
            if state.get("active"):
                events.append(build_rule_event(rule, current_value, "resolved", now_ts=now_ts, config_source=config_source))
                state["active"] = False
                state["last_resolved_ts"] = int(now_ts)
                state["last_status"] = "resolved"
                state["last_message"] = events[-1]["message"]
            state["condition_since_ts"] = None
            if not state.get("last_status"):
                state["last_status"] = "idle"

        if not rule.get("enabled"):
            state["active"] = False
            state["condition_since_ts"] = None
            if not state.get("last_status") or state.get("last_status") == "fired":
                state["last_status"] = "disabled"

        state["last_value"] = current_value
        state["last_evaluated_ts"] = int(now_ts)
        next_states[rule_id] = state

    return events, next_states


def quality_flags(metrics: Dict[str, Any]) -> Dict[str, int]:
    temperature = metrics.get("temperature_c", metrics.get("temp_c"))
    humidity = metrics.get("humidity_pct")
    pressure = metrics.get("pressure_hpa")

    def invalid(value: Any, *, minimum: float, maximum: float) -> int:
        try:
            numeric = float(value)
        except Exception:
            return 1
        return 1 if numeric < minimum or numeric > maximum else 0

    return {
        "temperature_invalid": invalid(temperature, minimum=-60.0, maximum=80.0),
        "humidity_invalid": invalid(humidity, minimum=0.0, maximum=100.0),
        "pressure_invalid": invalid(pressure, minimum=850.0, maximum=1100.0),
    }


def build_local_revision(local_rev: int) -> str:
    return f"local-{max(0, int(local_rev))}"


def generated_rule_id(metric: str, operator: str, index: int = 0) -> str:
    return short_stable_id("alert-rule", metric, operator, index, length=16)
