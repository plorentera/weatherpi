from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List

import httpx
import paho.mqtt.client as mqtt

from common.config import TELEMETRY_SCHEMA_VERSION, VALID_DATA_CLASSES, iter_matching_destinations
from common.security import build_http_auth_headers, canonical_json_bytes, short_stable_id


def build_envelope(
    *,
    station_id: str,
    data_class: str,
    occurred_ts: int,
    items: List[Dict[str, Any]],
    schema_version: int = TELEMETRY_SCHEMA_VERSION,
    extra: Dict[str, Any] | None = None,
    idempotency_seed: str = "",
) -> Dict[str, Any]:
    payload_items = items if isinstance(items, list) else []
    seed = idempotency_seed or canonical_json_bytes(
        {
            "station_id": station_id,
            "data_class": data_class,
            "occurred_ts": occurred_ts,
            "items": payload_items,
        }
    ).decode("utf-8")
    idempotency_key = short_stable_id(station_id, data_class, occurred_ts, seed, length=64)
    envelope = {
        "message_id": short_stable_id("message", idempotency_key, length=32),
        "station_id": station_id,
        "schema_version": schema_version,
        "data_class": data_class,
        "occurred_ts": int(occurred_ts),
        "sent_ts": None,
        "items": payload_items,
        "idempotency_key": idempotency_key,
    }
    if extra:
        envelope["meta"] = extra
    return envelope


def coerce_payload_to_envelope(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return build_envelope(
            station_id="meteo-001",
            data_class="alert_event",
            occurred_ts=int(time.time()),
            items=[{"message": "invalid payload"}],
            idempotency_seed="invalid-payload",
        )

    if payload.get("data_class") in VALID_DATA_CLASSES and payload.get("items") is not None:
        return payload

    ts = int(payload.get("ts") or time.time())
    station_id = str(payload.get("station_id") or "meteo-001")
    metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
    return build_envelope(
        station_id=station_id,
        data_class="weather_measurement",
        occurred_ts=ts,
        items=[{"ts": ts, "metrics": metrics}],
        idempotency_seed=short_stable_id("legacy", station_id, ts, canonical_json_bytes(metrics).decode("utf-8")),
    )


def build_station_status_item(worker: str, status: str, details: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "worker": worker,
        "status": status,
        "details": details or {},
        "ts": int(time.time()),
    }


def build_update_status_item(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": state.get("status"),
        "current_version": state.get("current_version"),
        "target_version": state.get("target_version"),
        "channel": state.get("channel"),
        "last_error": state.get("last_error"),
        "ts": int(time.time()),
    }


def build_alert_item(source: str, message: str, level: str = "error", context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "source": source,
        "level": level,
        "message": message,
        "context": context or {},
        "ts": int(time.time()),
    }


def destination_due_ts(destination: Dict[str, Any], now_ts: int) -> int:
    schedule = destination.get("schedule", {}) if isinstance(destination.get("schedule"), dict) else {}
    mode = str(schedule.get("mode") or "realtime")
    if mode == "interval":
        interval_seconds = max(5, int(schedule.get("interval_seconds", 60)))
        return now_ts + interval_seconds
    return now_ts


def enqueue_configured_event(
    cfg: Dict[str, Any],
    *,
    data_class: str,
    occurred_ts: int,
    items: List[Dict[str, Any]],
    extra: Dict[str, Any] | None = None,
) -> int:
    from common.db import enqueue_delivery_item

    station_id = str(cfg.get("station_id") or "meteo-001")
    envelope = build_envelope(
        station_id=station_id,
        data_class=data_class,
        occurred_ts=occurred_ts,
        items=items,
        extra=extra,
    )
    inserted = 0
    now_ts = int(time.time())
    for destination in iter_matching_destinations(cfg, data_class):
        if enqueue_delivery_item(
            destination=destination,
            envelope=envelope,
            now_ts=now_ts,
            next_attempt_ts=destination_due_ts(destination, now_ts),
        ):
            inserted += 1
    return inserted


def batch_envelope_from_rows(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    items = list(rows)
    first = coerce_payload_to_envelope(json.loads(items[0]["payload"])) if items else build_envelope(
        station_id="meteo-001",
        data_class="alert_event",
        occurred_ts=int(time.time()),
        items=[],
        idempotency_seed="empty-batch",
    )
    payloads = [coerce_payload_to_envelope(json.loads(item["payload"])) for item in items]
    data_class = first.get("data_class", "weather_measurement")
    occurred_ts = min(int(payload.get("occurred_ts") or time.time()) for payload in payloads)
    batch_items: List[Dict[str, Any]] = []
    component_keys: List[str] = []
    station_id = first.get("station_id", "meteo-001")
    for payload in payloads:
        station_id = str(payload.get("station_id") or station_id)
        component_keys.append(str(payload.get("idempotency_key") or payload.get("message_id") or ""))
        payload_items = payload.get("items", [])
        if isinstance(payload_items, list):
            batch_items.extend(payload_items)
    return build_envelope(
        station_id=station_id,
        data_class=data_class,
        occurred_ts=occurred_ts,
        items=batch_items,
        extra={"batched": len(payloads) > 1, "component_keys": component_keys},
        idempotency_seed="|".join(component_keys),
    )


class DeliveryAdapter(ABC):
    @abstractmethod
    def validate_destination(self, destination: Dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def build_message(self, envelope: Dict[str, Any], destination: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def send(self, message: Dict[str, Any], destination: Dict[str, Any], secrets_bundle: Dict[str, Any]) -> int:
        raise NotImplementedError


class WebhookAdapter(DeliveryAdapter):
    def validate_destination(self, destination: Dict[str, Any]) -> None:
        url = str(destination.get("webhook", {}).get("url") or "").strip()
        if not url:
            raise RuntimeError("webhook url is empty")

    def build_message(self, envelope: Dict[str, Any], destination: Dict[str, Any]) -> Dict[str, Any]:
        body = dict(envelope)
        body["sent_ts"] = int(time.time())
        return {
            "url": destination.get("webhook", {}).get("url"),
            "body": body,
            "body_bytes": canonical_json_bytes(body),
        }

    def send(self, message: Dict[str, Any], destination: Dict[str, Any], secrets_bundle: Dict[str, Any]) -> int:
        timeout_seconds = int(destination.get("webhook", {}).get("timeout_seconds", 5))
        url = str(message["url"])
        body_bytes = message["body_bytes"]
        headers = build_http_auth_headers(
            auth_cfg=destination.get("auth", {}),
            secret_bundle=secrets_bundle,
            station_id=str(message["body"].get("station_id") or ""),
            method="POST",
            url=url,
            body_bytes=body_bytes,
            idempotency_key=str(message["body"].get("idempotency_key") or ""),
        )
        extra_headers = destination.get("webhook", {}).get("headers", {})
        if isinstance(extra_headers, dict):
            headers.update({str(k): str(v) for k, v in extra_headers.items()})
        tls_cfg = destination.get("webhook", {}).get("tls", {})
        verify = str(tls_cfg.get("ca_bundle_path") or "").strip() or True
        with httpx.Client(timeout=timeout_seconds, verify=verify) as client:
            response = client.post(url, content=body_bytes, headers=headers)
            response.raise_for_status()
            return response.status_code


class MqttAdapter(DeliveryAdapter):
    def validate_destination(self, destination: Dict[str, Any]) -> None:
        mqtt_cfg = destination.get("mqtt", {})
        if not str(mqtt_cfg.get("host") or "").strip():
            raise RuntimeError("mqtt host is empty")
        if not str(mqtt_cfg.get("topic") or "").strip():
            raise RuntimeError("mqtt topic is empty")

    def build_message(self, envelope: Dict[str, Any], destination: Dict[str, Any]) -> Dict[str, Any]:
        body = dict(envelope)
        body["sent_ts"] = int(time.time())
        return {
            "topic": destination.get("mqtt", {}).get("topic"),
            "body": body,
            "payload_text": json.dumps(body, sort_keys=True, separators=(",", ":")),
        }

    def send(self, message: Dict[str, Any], destination: Dict[str, Any], secrets_bundle: Dict[str, Any]) -> int:
        mqtt_cfg = destination.get("mqtt", {})
        client_id = str(mqtt_cfg.get("client_id") or message["body"].get("station_id") or "weatherpi")
        client = mqtt.Client(client_id=client_id, clean_session=True)

        username = str(secrets_bundle.get("username") or mqtt_cfg.get("username") or "").strip()
        password = str(secrets_bundle.get("password") or "").strip()
        if username or password:
            client.username_pw_set(username=username, password=password or None)

        tls_cfg = mqtt_cfg.get("tls", {})
        if bool(tls_cfg.get("enabled", True)):
            ca_bundle_path = str(tls_cfg.get("ca_bundle_path") or "").strip()
            if ca_bundle_path:
                client.tls_set(ca_certs=ca_bundle_path)
            else:
                client.tls_set()

        client.connect(
            host=str(mqtt_cfg.get("host")),
            port=int(mqtt_cfg.get("port", 8883)),
            keepalive=int(mqtt_cfg.get("keepalive_seconds", 30)),
        )
        client.loop_start()
        info = client.publish(str(message["topic"]), message["payload_text"], qos=1, retain=False)
        info.wait_for_publish()
        rc = int(info.rc)
        client.loop_stop()
        client.disconnect()
        if rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"mqtt publish failed rc={rc}")
        return 200


def get_adapter(kind: str) -> DeliveryAdapter:
    if kind == "webhook_https":
        return WebhookAdapter()
    if kind == "mqtt":
        return MqttAdapter()
    raise RuntimeError(f"unsupported delivery kind: {kind}")
