import json
import time
import httpx
import paho.mqtt.client as mqtt

from common.db import get_config, fetch_due_outbox, mark_outbox_sent, mark_outbox_failed, purge_sent_outbox


def send_webhook(url: str, timeout_s: int, payload: dict) -> None:
    with httpx.Client(timeout=timeout_s) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()


def send_mqtt(host: str, port: int, topic: str, payload: dict) -> None:
    client = mqtt.Client()
    client.connect(host, port, keepalive=30)
    client.loop_start()
    info = client.publish(topic, json.dumps(payload), qos=0, retain=False)
    info.wait_for_publish()
    client.loop_stop()
    client.disconnect()


def main() -> None:
    print("[outputs] worker started")

    last_purge = 0
    PURGE_EVERY_SECONDS = 600  
    KEEP_LAST_SENT = 2000

    while True:
        now_ts = int(time.time())
        items = fetch_due_outbox(now_ts, limit=50)

        if now_ts - last_purge >= PURGE_EVERY_SECONDS:
            deleted = purge_sent_outbox(keep_last=KEEP_LAST_SENT)
            if deleted:
                print(f"[outputs] purge sent deleted={deleted} keep_last={KEEP_LAST_SENT}")
            last_purge = now_ts
            
        if not items:
            time.sleep(2)
            continue

        cfg = get_config()
        outputs = cfg.get("outputs", {})

        for item in items:
            outbox_id = item["id"]
            destination = item["destination"]
            current_attempts = int(item["attempts"])
            attempts = current_attempts + 1

            try:
                payload = json.loads(item["payload"])

                if destination == "webhook":
                    wh = outputs.get("webhook", {})
                    if not bool(wh.get("enabled", False)):
                        mark_outbox_failed(outbox_id, current_attempts, "Webhook disabled in config", now_ts)
                        print(f"[outputs] defer id={outbox_id} dest=webhook reason=disabled")
                        continue

                    url = str(wh.get("url", "")).strip()
                    if not url:
                        raise RuntimeError("Webhook URL is empty")

                    send_webhook(
                        url=url,
                        timeout_s=int(wh.get("timeout_seconds", 5)),
                        payload=payload
                    )

                elif destination == "mqtt":
                    mq = outputs.get("mqtt", {})
                    if not bool(mq.get("enabled", False)):
                        mark_outbox_failed(outbox_id, current_attempts, "MQTT disabled in config", now_ts)
                        print(f"[outputs] defer id={outbox_id} dest=mqtt reason=disabled")
                        continue

                    topic = str(mq.get("topic", "meteo/measurements")).strip()
                    if not topic:
                        raise RuntimeError("MQTT topic is empty")

                    send_mqtt(
                        host=mq.get("host", "localhost"),
                        port=int(mq.get("port", 1883)),
                        topic=topic,
                        payload=payload
                    )
                else:
                    raise RuntimeError(f"Unknown destination: {destination}")

                mark_outbox_sent(outbox_id)
                print(f"[outputs] sent id={outbox_id} dest={destination}")

            except Exception as e:
                mark_outbox_failed(outbox_id, attempts, str(e), now_ts)
                print(f"[outputs] FAIL id={outbox_id} dest={destination} err={e}")

        time.sleep(0.5)


if __name__ == "__main__":
    main()
