# API - Meteo Station

Documento de referencia de la API HTTP local-first de la estación meteorológica.

## Base URL

```text
http://127.0.0.1:8000
```

## Convenciones

- Toda la UI y toda la API requieren autenticación.
- En navegador, el acceso recomendado es `GET /login` para crear sesión por cookie.
- Para scripts, se mantiene HTTP Basic.
- Roles:
  - `reader`: solo lectura.
  - `admin`: lectura y escritura.
- Toda integración remota se hace por conexiones salientes iniciadas por la estación.
- La configuración sensible no se devuelve en claro: se gestiona por secret store y la API solo muestra un resumen enmascarado.

## Variables de entorno de seguridad

- `WEATHERPI_READER_USER`, `WEATHERPI_READER_PASS`, `WEATHERPI_READER_PASS_HASH`
- `WEATHERPI_ADMIN_USER`, `WEATHERPI_ADMIN_PASS`, `WEATHERPI_ADMIN_PASS_HASH`
- `WEATHERPI_SESSION_SECRET`
- `WEATHERPI_COOKIE_SECURE`
- `WEATHERPI_COOKIE_SAMESITE`
- `WEATHERPI_SESSION_TTL_SECONDS`
- `WEATHERPI_DATA_DIR` para forzar un directorio persistente compartido entre releases

## Índice rápido

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/login` | Pantalla de acceso |
| POST | `/login` | Inicio de sesión |
| POST | `/logout` | Cierre de sesión |
| GET | `/api/status` | Estado base de la estación |
| GET | `/api/latest` | Última medición registrada |
| GET | `/api/series` | Serie temporal de mediciones |
| GET | `/api/config` | Config efectiva + local + overlay remoto + summary de secrets |
| PUT | `/api/config` | Actualiza solo la config local |
| GET | `/api/config/secrets` | Vista enmascarada del secret store |
| PUT | `/api/config/secrets` | Patch de secrets sensibles |
| GET | `/api/outbox` | Cola de entrega saliente |
| POST | `/api/outbox/retry_failed` | Reencola fallidos |
| POST | `/api/outbox/purge_sent` | Purga enviados antiguos |
| GET | `/api/system/version` | Versión del sistema y estado de update |
| GET | `/api/system/workers` | Heartbeats de workers |
| GET | `/api/telemetry/status` | Destinos salientes + resumen de outbox |
| GET | `/api/remote-config/status` | Estado de configuración remota |
| POST | `/api/remote-config/check-now` | Fuerza consulta remota |
| GET | `/api/update/status` | Estado del update y release history |
| POST | `/api/update/check-now` | Fuerza comprobación de updates |
| POST | `/api/update/apply` | Aplica release staged |
| POST | `/api/update/rollback` | Hace rollback |
| GET | `/api/export.csv` | Export CSV por rango de días |
| GET | `/api/exports` | Historial de exports locales |
| GET | `/api/exports/{export_id}` | Descarga un export concreto |

## Estado y métricas

### GET /api/status

Devuelve metadatos básicos de mediciones y la versión local del backend.

Respuesta ejemplo:

```json
{
  "status": "ok",
  "records": 1280,
  "last_timestamp": 1712230000,
  "now": 1712230012,
  "version": "1.1.0"
}
```

### GET /api/latest

Devuelve la última medición almacenada.

### GET /api/series?limit=288

Devuelve una serie temporal ascendente para pintar histórico en la UI.

## Configuración local, overlay remoto y secret store

### GET /api/config

Devuelve:

- `config`: configuración efectiva.
- `local_config`: configuración editable localmente.
- `remote_overlay`: overlay remoto permitido y aplicado.
- `sources`: metadatos de revisión y origen.
- `secrets`: resumen enmascarado del secret store.

Modelo local esperado, resumido:

```json
{
  "station_id": "meteo-001",
  "sample_interval_seconds": 5,
  "collector": {
    "enabled": true,
    "status_emit_interval_seconds": 60
  },
  "telemetry": {
    "enabled": true,
    "destinations": [
      {
        "id": "dest-1",
        "enabled": true,
        "kind": "webhook_https",
        "data_classes": ["weather_measurement", "station_status"],
        "schedule": {"mode": "realtime", "interval_seconds": 60},
        "batch_max_items": 1,
        "retry_policy": {"max_attempts": 10},
        "auth": {"mode": "bearer", "key_id": "dest-1"},
        "webhook": {
          "url": "https://example.com/hook",
          "timeout_seconds": 5
        }
      }
    ]
  },
  "remote_config": {
    "enabled": false,
    "endpoint": "",
    "poll_interval_seconds": 900,
    "auto_apply": true,
    "auth": {"mode": "bearer", "key_id": ""},
    "signing": {"required": true, "algorithm": "ed25519", "public_key": ""}
  },
  "updates": {
    "enabled": false,
    "manifest_url": "",
    "poll_interval_seconds": 3600,
    "channel": "stable",
    "auto_download": true,
    "apply_strategy": "manual",
    "health_grace_seconds": 120
  },
  "security": {
    "allow_lan": false,
    "api_bind_host": "127.0.0.1",
    "require_tls_for_remote": true,
    "block_remote_when_default_local_credentials": true
  }
}
```

### PUT /api/config

Actualiza solo la config local. La API valida:

- `sample_interval_seconds` entre `1` y `3600`.
- IDs únicos en `telemetry.destinations`.
- `kind` soportado: `webhook_https` o `mqtt`.
- Si un destino webhook está activo, la URL debe ser `https://` o `http://` loopback.
- Si un destino MQTT está activo, `host` y `topic` son obligatorios.
- Si `remote_config.enabled=true`, `remote_config.endpoint` debe ser `https://` o loopback.
- Si `updates.enabled=true`, `updates.manifest_url` debe ser `https://` o loopback.

Ejemplo:

```bash
curl -X PUT http://127.0.0.1:8000/api/config \
  -u admin:admin \
  -H "Content-Type: application/json" \
  -d '{
    "station_id": "meteo-casa",
    "sample_interval_seconds": 10,
    "telemetry": {
      "enabled": true,
      "destinations": [
        {
          "id": "dest-1",
          "enabled": true,
          "kind": "webhook_https",
          "data_classes": ["weather_measurement", "station_status"],
          "schedule": {"mode": "realtime", "interval_seconds": 60},
          "batch_max_items": 1,
          "auth": {"mode": "bearer", "key_id": "dest-1"},
          "webhook": {"url": "https://example.com/hook", "timeout_seconds": 5}
        }
      ]
    },
    "updates": {
      "enabled": true,
      "manifest_url": "https://updates.example.com/stable/manifest.json",
      "channel": "stable"
    }
  }'
```

### GET /api/config/secrets

Devuelve un resumen enmascarado de `data/device_secrets.json`.

### PUT /api/config/secrets

Aplica un merge de secrets sensibles. Ejemplo:

```bash
curl -X PUT http://127.0.0.1:8000/api/config/secrets \
  -u admin:admin \
  -H "Content-Type: application/json" \
  -d '{
    "telemetry_destinations": {
      "dest-1": {
        "bearer_token": "secret-token"
      }
    },
    "remote_config": {
      "bearer_token": "cfg-token"
    },
    "updates": {
      "bearer_token": "upd-token"
    }
  }'
```

## Telemetría saliente y outbox

### GET /api/telemetry/status

Devuelve:

- `enabled`
- `destinations`
- `outbox.summary`
- `outbox.destinations`

### GET /api/outbox

Lista la cola persistente de entrega. Campos habituales por item:

- `id`
- `status` (`pending`, `leased`, `sent`, `failed`)
- `destination_id`
- `delivery_kind`
- `data_class`
- `attempts`
- `next_attempt_ts`
- `response_code`
- `last_error`

### POST /api/outbox/retry_failed

Reencola todos los `failed` a `pending`.

### POST /api/outbox/purge_sent

Purga enviados antiguos conservando `keep_last`.

## Workers del sistema

### GET /api/system/version

Devuelve:

- `app_version`
- `current_version`
- `target_version`
- `channel`
- `update_status`

### GET /api/system/workers

Lista heartbeats persistentes de:

- `collector`
- `delivery`
- `remote_config`
- `update`

Cada item incluye `status`, `updated_ts`, `stale` y `details`.

## Configuración remota

### GET /api/remote-config/status

Estado de revisión remota y overlay aplicado.

### POST /api/remote-config/check-now

Lanza una comprobación inmediata del manifest/config remotos.

El flujo esperado es:

1. La estación consulta un manifest JSON remoto.
2. Si detecta una `revision` nueva, carga `config` inline o `config_url`.
3. Verifica `sha256` y la firma configurada.
4. Sanitiza el overlay y solo permite namespaces remotos seguros.
5. Aplica el overlay si `auto_apply=true`.

## Updates pull

### GET /api/update/status

Devuelve:

- `state`
- `history`

Estados de update soportados:

- `idle`
- `available`
- `downloading`
- `verified`
- `applied`
- `failed`
- `rollback`

### POST /api/update/check-now

Consulta el manifest remoto de updates y, si `auto_download=true`, descarga y verifica el bundle.

Manifest esperado:

```json
{
  "version": "1.2.0",
  "channel": "stable",
  "artifact_url": "https://updates.example.com/weatherpi-1.2.0.zip",
  "sha256": "....",
  "signature": "....",
  "signature_algorithm": "ed25519"
}
```

### POST /api/update/apply

Aplica el bundle staged. En runtime Linux:

- extrae a `data/releases/versions/<version>`
- actualiza enlaces `current` y `previous`
- deja una marca `data/runtime/restart_required.json`

En Windows/desarrollo devuelve error controlado porque el flujo de swap y rollback está pensado para Linux/Raspberry.

### POST /api/update/rollback

Vuelve el enlace `current` al target de `previous` y registra el evento en `release_history`.

## Export y backup local

### GET /api/export.csv?days=7

Export streaming CSV para una ventana temporal.

### GET /api/exports

Historial de exports locales guardados por el backup worker.

### GET /api/exports/{export_id}

Descarga un export concreto por id.

## Ejemplos rápidos

### JavaScript

```js
const auth = "Basic " + btoa("admin:admin");

async function getTelemetryStatus() {
  const res = await fetch("/api/telemetry/status", { headers: { Authorization: auth } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
```

### Python

```python
import httpx

with httpx.Client(base_url="http://127.0.0.1:8000", auth=("admin", "admin"), timeout=10) as client:
    config = client.get("/api/config").json()
    update_state = client.get("/api/update/status").json()
    print(config["sources"])
    print(update_state["state"]["status"])
```

## Troubleshooting rápido

1. Las funciones remotas aparecen bloqueadas.
   - Revisa credenciales locales por defecto y que exista `data/device_secrets.json`.
2. `PUT /api/config` devuelve validación.
   - Revisa `telemetry.destinations`, URLs HTTPS/loopback y rangos numéricos.
3. `POST /api/update/apply` devuelve conflicto.
   - Verifica que exista un bundle staged y que el runtime objetivo sea Linux/Raspberry.
4. `GET /api/system/workers` marca `stale=true`.
   - El worker correspondiente no está enviando heartbeat o dejó de ejecutarse.
