# API - Meteo Station

Documento completo de la API HTTP de la estacion meteorologica.

## Base URL

```text
http://127.0.0.1:8000
```

## Documentacion interactiva

- Swagger UI: `/docs`
- OpenAPI JSON: `/openapi.json`

## Convenciones

- API sin autenticacion por defecto (entorno local).
- Puedes activar proteccion de escritura configurando la variable de entorno `WEATHERPI_API_KEY`.
- Con proteccion activa, los endpoints de escritura requieren cabecera `X-API-Key`.
- Respuestas de negocio en JSON (`application/json`).
- Endpoints de export devuelven CSV (`text/csv`).
- Timestamps en formato epoch (segundos UTC).

## Indice rapido

| Metodo | Ruta | Descripcion |
|---|---|---|
| GET | `/api/status` | Estado del servicio y recuento de lecturas |
| GET | `/api/latest` | Ultima medicion registrada |
| GET | `/api/config` | Lee configuracion actual |
| PUT | `/api/config` | Actualiza configuracion |
| GET | `/api/outbox` | Lista/filtra cola de envios |
| POST | `/api/outbox/retry_failed` | Reencola items fallidos |
| POST | `/api/outbox/purge_sent` | Purga enviados antiguos |
| GET | `/api/export.csv` | Descarga CSV de mediciones |
| GET | `/api/exports` | Lista historico de exports |
| GET | `/api/exports/{export_id}` | Descarga export por id |

## 1) Estado

### GET /api/status

Devuelve estado general y metadatos de base de datos.

#### Ejemplo

```bash
curl http://127.0.0.1:8000/api/status
```

#### Respuesta 200

```json
{
  "status": "ok",
  "records": 1280,
  "last_timestamp": 1712230000,
  "now": 1712230012
}
```

Campos:

- `status`: estado logico del endpoint.
- `records`: total de lecturas en `measurements`.
- `last_timestamp`: ultimo `ts` almacenado (puede ser `null` sin datos).
- `now`: tiempo actual del servidor.

## 2) Ultima lectura

### GET /api/latest

Devuelve la ultima medicion guardada.

#### Ejemplo

```bash
curl http://127.0.0.1:8000/api/latest
```

#### Respuesta 200 (con datos)

```json
{
  "data": {
    "ts": 1712230000,
    "temp_c": 22.4,
    "humidity_pct": 49.1,
    "pressure_hpa": 1013.2
  }
}
```

#### Respuesta 200 (sin datos)

```json
{
  "data": null
}
```

## 3) Configuracion

### Modelo de configuracion

Contrato base esperado:

```json
{
  "station_id": "meteo-001",
  "sample_interval_seconds": 5,
  "collector": {
    "enabled": true
  },
  "outputs": {
    "webhook": {
      "enabled": false,
      "url": "",
      "timeout_seconds": 5
    },
    "mqtt": {
      "enabled": false,
      "host": "localhost",
      "port": 1883,
      "topic": "meteo/measurements"
    }
  },
  "exports": {
    "enabled": false,
    "frequency": "daily",
    "every_days": 2,
    "keep_days": 30,
    "days_per_file": 1,
    "upload": {
      "enabled": false,
      "webhook_url": ""
    },
    "schedule": {
      "time_local": "01:00",
      "time_utc": "00:00"
    }
  },
  "ui": {
    "timezone": "UTC"
  }
}
```

### GET /api/config

Lee la configuracion activa (resultado mergeado con defaults).

```bash
curl http://127.0.0.1:8000/api/config
```

Respuesta 200:

```json
{
  "config": {
    "station_id": "meteo-001",
    "sample_interval_seconds": 5,
    "collector": {
      "enabled": true
    },
    "outputs": {
      "webhook": {
        "enabled": false,
        "url": "",
        "timeout_seconds": 5
      },
      "mqtt": {
        "enabled": false,
        "host": "localhost",
        "port": 1883,
        "topic": "meteo/measurements"
      }
    },
    "exports": {
      "enabled": false,
      "frequency": "daily",
      "every_days": 2,
      "keep_days": 30,
      "days_per_file": 1,
      "upload": {
        "enabled": false,
        "webhook_url": ""
      },
      "schedule": {
        "time_local": "01:00",
        "time_utc": "00:00"
      }
    },
    "ui": {
      "timezone": "UTC"
    },
    "_rev": 3
  }
}
```

### PUT /api/config

Actualiza configuracion.

Si `WEATHERPI_API_KEY` esta activa en el servidor:

- sin `X-API-Key` -> `401`.
- con key incorrecta -> `403`.

Reglas de validacion de API:

- `sample_interval_seconds` debe estar entre `1` y `3600`.
- Si `outputs.webhook.enabled=true`, `outputs.webhook.url` debe ser URL HTTP(S) valida.
- Si `outputs.mqtt.enabled=true`, `host` y `topic` son obligatorios y `port` debe estar entre `1` y `65535`.

#### Ejemplo

```bash
curl -X PUT http://127.0.0.1:8000/api/config \
  -H "Content-Type: application/json" \
  -H "X-API-Key: tu_clave_si_esta_activada" \
  -d '{
    "station_id": "meteo-casa",
    "sample_interval_seconds": 10,
    "collector": {"enabled": true},
    "outputs": {
      "webhook": {"enabled": true, "url": "https://example.com/hook"}
    },
    "exports": {
      "enabled": true,
      "schedule": {"time_utc": "02:00"}
    },
    "ui": {"timezone": "Europe/Madrid"}
  }'
```

#### Respuesta 200 (ok)

```json
{
  "ok": true,
  "config": {
    "station_id": "meteo-casa",
    "sample_interval_seconds": 10
  }
}
```

#### Respuesta 400 (error de validacion)

```json
{
  "ok": false,
  "error": "sample_interval_seconds debe estar entre 1 y 3600"
}
```

## 4) Outbox

La outbox almacena envios pendientes/sent/failed para destinos como webhook o MQTT.

### Estados y campos frecuentes

- Estados: `pending`, `sent`, `failed`.
- Campos por item (listado):
  - `id`
  - `created_ts`
  - `next_attempt_ts`
  - `attempts`
  - `status`
  - `destination`
  - `last_error`

### GET /api/outbox

Lista items y resumen.

Query params:

- `status` (opcional): filtra por estado.
- `limit` (opcional, default `100`): maximo de registros.

```bash
curl "http://127.0.0.1:8000/api/outbox"
curl "http://127.0.0.1:8000/api/outbox?status=failed&limit=50"
```

Respuesta 200:

```json
{
  "summary": {
    "pending": 120,
    "sent": 980,
    "failed": 5
  },
  "items": [
    {
      "id": 101,
      "created_ts": 1712230000,
      "next_attempt_ts": 1712230600,
      "attempts": 4,
      "status": "failed",
      "destination": "webhook",
      "last_error": "timeout"
    }
  ]
}
```

### POST /api/outbox/retry_failed

Reencola todos los registros en `failed` a `pending`.

```bash
curl -X POST http://127.0.0.1:8000/api/outbox/retry_failed \
  -H "X-API-Key: tu_clave_si_esta_activada"
```

Respuesta 200:

```json
{
  "ok": true,
  "retried": 5
}
```

### POST /api/outbox/purge_sent

Purga enviados antiguos y conserva los ultimos `keep_last`.

Query params:

- `keep_last` (opcional, default `1000`).

```bash
curl -X POST "http://127.0.0.1:8000/api/outbox/purge_sent?keep_last=500" \
  -H "X-API-Key: tu_clave_si_esta_activada"
```

Respuesta 200:

```json
{
  "ok": true,
  "deleted": 340,
  "keep_last": 500
}
```

## 5) Exportacion CSV puntual

### GET /api/export.csv

Genera streaming CSV para una ventana temporal de `days` dias hacia atras desde ahora.

Query params:

- `days` (opcional, default `7`).

```bash
curl -L "http://127.0.0.1:8000/api/export.csv?days=3" -o meteo_ultimos_3_dias.csv
```

Cabecera y formato CSV:

```text
ts;temp_c;humidity_pct;pressure_hpa
1712200000;22.1;50.0;1012.9
```

## 6) Historial de exports

### GET /api/exports

Lista archivos de export almacenados.

Query params:

- `limit` (opcional, default `50`).

```bash
curl "http://127.0.0.1:8000/api/exports?limit=20"
```

Respuesta 200:

```json
{
  "items": [
    {
      "id": 12,
      "created_ts": 1712230000,
      "period_from_ts": 1712143600,
      "period_to_ts": 1712230000,
      "filename": "meteo_2026_04_04.csv",
      "path": "data/exports/meteo_2026_04_04.csv"
    }
  ]
}
```

### GET /api/exports/{export_id}

Descarga un CSV por identificador.

```bash
curl -L "http://127.0.0.1:8000/api/exports/12" -o export_12.csv
```

Errores:

- `404` + `{"detail":"export not found"}` si no existe id.
- `404` + `{"detail":"export file missing"}` si falta el archivo en disco.

## Ejemplos de integracion

### JavaScript (fetch)

```js
const base = "http://127.0.0.1:8000";

async function getLatest() {
  const res = await fetch(`${base}/api/latest`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json = await res.json();
  return json.data;
}

async function updateInterval(seconds) {
  const cfgRes = await fetch(`${base}/api/config`);
  const cfg = (await cfgRes.json()).config;

  cfg.sample_interval_seconds = seconds;

  const putRes = await fetch(`${base}/api/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  });

  const out = await putRes.json();
  if (!out.ok) throw new Error(out.error || "No se pudo actualizar config");
  return out.config;
}
```

### Python (httpx)

```python
import httpx

BASE = "http://127.0.0.1:8000"

with httpx.Client(timeout=10) as client:
    status = client.get(f"{BASE}/api/status").json()
    latest = client.get(f"{BASE}/api/latest").json()

    cfg = client.get(f"{BASE}/api/config").json()["config"]
    cfg["sample_interval_seconds"] = 15

    updated = client.put(f"{BASE}/api/config", json=cfg).json()
    if not updated.get("ok"):
        raise RuntimeError(updated.get("error", "Error actualizando config"))

    print(status)
    print(latest)
```

## Troubleshooting rapido

1. `Connection refused`.
   - Verifica que este levantado el backend en puerto 8000.
2. PUT `/api/config` devuelve `ok=false`.
   - Ajusta `sample_interval_seconds` al rango 1..3600.
3. `/api/exports/{id}` da 404 `export file missing`.
   - El registro existe pero el archivo fue movido/eliminado del disco.
4. `/api/latest` devuelve `data: null`.
   - Todavia no hay mediciones insertadas.

## Flujo recomendado

1. Comprobar estado con `/api/status`.
2. Leer ultima muestra con `/api/latest`.
3. Obtener config actual (`GET /api/config`) y actualizar por merge (`PUT /api/config`).
4. Mantener outbox con `/api/outbox`, `retry_failed` y `purge_sent`.
5. Exportar con `/api/export.csv` o descargar historicos con `/api/exports/{id}`.

## 7) Ampliaciones recomendadas

Estas piezas no estan expuestas como API hoy, pero encajan bien en la siguiente iteracion del sistema:

- Catalogo de sensores: tipo, modelo, canal, estado y fecha de alta.
- Historial de mantenimiento: intervenciones, notas, repuestos y fecha de revision.
- Versionado del sistema: version de firmware, version de software y version de configuracion.
- Actualizacion remota segura: paquete firmado, verificacion de integridad y canal autenticado.
- Rollback automatico: restaurar la version anterior si el arranque o la verificacion fallan.

Una posible forma de modelarlo seria exponer endpoints como:

- `GET /api/system/version`
- `GET /api/sensors`
- `GET /api/maintenance`
- `POST /api/firmware/update`
- `POST /api/firmware/rollback`

Si se implementa, conviene mantener estos flujos separados de la telemetria normal para no mezclar lectura de datos con operaciones de administracion.