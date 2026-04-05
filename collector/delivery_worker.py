from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, Iterable, List

import httpx

from common.config import MAX_ATTEMPTS, SECRETS_PATH, destination_map
from common.db import (
    get_config,
    init_db,
    lease_due_outbox,
    mark_outbox_failed,
    mark_outbox_sent,
    purge_sent_outbox,
    release_outbox_lease,
    upsert_worker_heartbeat,
)
from common.security import remote_operations_block_reason
from common.secrets import get_destination_secrets
from common.telemetry import batch_envelope_from_rows, get_adapter


def _chunked(items: List[Dict[str, object]], size: int) -> Iterable[List[Dict[str, object]]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _response_code_from_exception(exc: Exception) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return None


def main() -> None:
    init_db()
    print("[delivery] worker started")

    last_purge = 0
    purge_every_seconds = 600
    keep_last_sent = 2000

    while True:
        now_ts = int(time.time())
        cfg = get_config()
        security_cfg = cfg.get("security", {}) if isinstance(cfg.get("security"), dict) else {}

        block_reason = remote_operations_block_reason(
            enforce=bool(security_cfg.get("block_remote_when_default_local_credentials", True)),
            secret_store_exists=SECRETS_PATH.exists(),
        )

        if now_ts - last_purge >= purge_every_seconds:
            deleted = purge_sent_outbox(keep_last=keep_last_sent)
            if deleted:
                print(f"[delivery] purge sent deleted={deleted} keep_last={keep_last_sent}")
            last_purge = now_ts

        if block_reason:
            upsert_worker_heartbeat("delivery", "blocked", {"reason": block_reason})
            time.sleep(5)
            continue

        items = lease_due_outbox(now_ts, limit=100)
        if not items:
            upsert_worker_heartbeat("delivery", "idle", {"queue": "empty"})
            time.sleep(2)
            continue

        destinations = destination_map(cfg)
        grouped: Dict[str, List[Dict[str, object]]] = defaultdict(list)
        for item in items:
            grouped[str(item.get("destination_id") or item.get("destination") or "")].append(item)

        sent_count = 0
        failed_count = 0
        deferred_count = 0

        for destination_id, rows in grouped.items():
            destination = destinations.get(destination_id)
            if not destination or not bool(destination.get("enabled", False)):
                for row in rows:
                    release_outbox_lease(int(row["id"]), now_ts + 60, "destination disabled or missing")
                    deferred_count += 1
                continue

            adapter = get_adapter(str(destination.get("kind") or ""))
            try:
                adapter.validate_destination(destination)
            except Exception as exc:
                max_attempts = int(destination.get("retry_policy", {}).get("max_attempts", MAX_ATTEMPTS))
                for row in rows:
                    mark_outbox_failed(
                        int(row["id"]),
                        max_attempts,
                        str(exc),
                        now_ts,
                        response_code=None,
                        max_attempts=max_attempts,
                    )
                    failed_count += 1
                continue

            schedule = destination.get("schedule", {}) if isinstance(destination.get("schedule"), dict) else {}
            batch_max_items = max(1, int(destination.get("batch_max_items", 1)))
            if str(schedule.get("mode") or "realtime") == "interval":
                oldest_created_ts = min(int(row["created_ts"]) for row in rows)
                interval_seconds = max(5, int(schedule.get("interval_seconds", 60)))
                ready_ts = oldest_created_ts + interval_seconds
                if len(rows) < batch_max_items and now_ts < ready_ts:
                    for row in rows:
                        release_outbox_lease(int(row["id"]), ready_ts, "")
                        deferred_count += 1
                    continue

            secrets_bundle = get_destination_secrets(destination_id)
            for chunk in _chunked(sorted(rows, key=lambda item: int(item["id"])), batch_max_items):
                batch_envelope = batch_envelope_from_rows(chunk)
                try:
                    message = adapter.build_message(batch_envelope, destination)
                    response_code = adapter.send(message, destination, secrets_bundle)
                    for row in chunk:
                        mark_outbox_sent(int(row["id"]), response_code=response_code)
                        sent_count += 1
                    print(
                        f"[delivery] sent destination={destination_id} kind={destination.get('kind')} items={len(chunk)}"
                    )
                except Exception as exc:
                    response_code = _response_code_from_exception(exc)
                    for row in chunk:
                        attempts = int(row.get("attempts", 0)) + 1
                        max_attempts = int(destination.get("retry_policy", {}).get("max_attempts", MAX_ATTEMPTS))
                        mark_outbox_failed(
                            int(row["id"]),
                            attempts,
                            str(exc),
                            now_ts,
                            response_code=response_code,
                            max_attempts=max_attempts,
                        )
                        failed_count += 1
                    print(
                        f"[delivery] FAIL destination={destination_id} kind={destination.get('kind')} items={len(chunk)} err={exc}"
                    )

        upsert_worker_heartbeat(
            "delivery",
            "ok",
            {
                "leased_items": len(items),
                "sent_count": sent_count,
                "failed_count": failed_count,
                "deferred_count": deferred_count,
            },
        )
        time.sleep(0.5)


if __name__ == "__main__":
    main()
