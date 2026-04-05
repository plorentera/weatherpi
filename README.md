# WeatherPi (Meteo Station)

Proyecto open source para una estacion meteorologica local basada en Python + FastAPI + SQLite.

Estado actual: **WIP (work in progress)**. El sistema es funcional para uso local/lab, pero todavia no se considera una plataforma "production-grade".

## Objetivo

Recoger mediciones ambientales, visualizarlas en un dashboard web, y gestionar salidas (webhook/MQTT) y exportaciones CSV historicas.

## Alcance actual

### Incluido hoy

- API HTTP con FastAPI para estado, lectura, configuracion, outbox y exportaciones.
- Dashboard web con metricas actuales y grafico historico (Chart.js local).
- Worker de recoleccion con almacenamiento en SQLite.
- Worker de salidas con cola de reintentos (outbox).
- Worker de backup/export CSV por programacion.
- UI de configuracion para collector, outputs y exports.
- Librerias frontend servidas en local (sin dependencia de CDN).
- Cache local de dependencias Python en `third_party/python-wheels`.

### Fuera de alcance por ahora

- Autenticacion/autorizacion avanzada (OIDC, MFA, SSO, gestion centralizada).
- Observabilidad completa (metricas Prometheus, tracing, alerting).
- Drivers de sensores reales y calibracion metrologica formal.
- CI/CD y cobertura de tests completa.

## Arquitectura

El sistema se compone de 4 procesos principales:

1. API (`api/main.py`)
2. Collector (`collector/main.py`)
3. Outputs worker (`collector/outputs_worker.py`)
4. Backup worker (`collector/backup_worker.py`)

Persistencia principal:

- SQLite en `data/meteo.db`
- Tablas: `measurements`, `settings`, `outbox`, `exports`

Flujo de datos resumido:

1. Collector lee sensor y escribe en `measurements`.
2. Si hay outputs habilitados, encola mensajes en `outbox`.
3. Outputs worker consume `outbox`, envia y reintenta con backoff.
4. Backup worker genera CSV y registra metadatos en `exports`.
5. Dashboard consulta API para estado, ultima lectura y serie temporal.

## Requisitos

- Python 3.11+ (probado tambien en 3.14).
- Windows/Linux/macOS.

## Instalacion

### 1) Entorno virtual

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\activate
```

Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2) Dependencias

Instalacion online:

```powershell
pip install -r requirements.txt
```

Instalacion offline (si ya existe cache local):

```powershell
pip install --no-index --find-links third_party/python-wheels -r requirements.txt
```

### 3) Inicializar base de datos

```powershell
python -m scripts.init_db
```

## Ejecucion

### Opcion recomendada (todo en uno)

```powershell
python -m scripts.run_all
```

Este launcher arranca API + collector + outputs + backup.

Notas:

- Si el puerto `8000` esta ocupado, usa automaticamente el siguiente libre (`8001`, `8002`, ...).
- La URL final del dashboard se imprime en consola.

### Ejecucion por procesos individuales

```powershell
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
python -m collector.main
python -m collector.outputs_worker
python -m collector.backup_worker
```

## Autenticacion y autorizacion

Todo el webserver y toda la API estan protegidos.

Para acceso web (navegador):

- `GET /login` muestra pantalla de login.
- Login correcto crea sesion por cookie `HttpOnly`.
- `POST /logout` cierra sesion.

Para integraciones API (scripts/servicios):

- Se mantiene soporte HTTP Basic en cabecera `Authorization`.

Roles:

- `reader`: solo lectura (`GET`, `HEAD`, `OPTIONS`).
- `admin`: lectura y escritura (PUT/POST/DELETE).

Variables de entorno (opcionales, con defaults):

- `WEATHERPI_READER_USER` (default: `reader`)
- `WEATHERPI_READER_PASS` (default: `reader`)
- `WEATHERPI_ADMIN_USER` (default: `admin`)
- `WEATHERPI_ADMIN_PASS` (default: `admin`)

Ejemplo PowerShell:

```powershell
$env:WEATHERPI_READER_USER = "viewer"
$env:WEATHERPI_READER_PASS = "viewer_strong_pass"
$env:WEATHERPI_ADMIN_USER = "admin"
$env:WEATHERPI_ADMIN_PASS = "admin_strong_pass"
python -m scripts.run_all
```

Comportamiento:

- Sin credenciales o invalidas: `401`.
- Credenciales `reader` en endpoint de escritura: `403`.
- Credenciales `admin` validas: acceso completo.

## UI y rutas

Pantallas:

- `/` dashboard
- `/settings.html` configuracion
- `/outbox.html` outbox
- `/exports.html` exports
- `/api.html` guia rapida API

API util:

- `GET /api/status`
- `GET /api/latest`
- `GET /api/series?limit=288`
- `GET /api/config`
- `PUT /api/config`
- `GET /api/outbox`
- `POST /api/outbox/retry_failed`
- `POST /api/outbox/purge_sent`
- `GET /api/export.csv?days=7`
- `GET /api/exports`
- `GET /api/exports/{id}`

Documentacion:

- `GET /docs` (Swagger)
- `GET /openapi.json`
- `GET /docs/API.md`

## Configuracion

Se guarda en `settings.key = 'config'` (JSON), mergeada con defaults.

Campos principales:

- `station_id`
- `sample_interval_seconds` (1..3600)
- `collector.enabled`
- `outputs.webhook.*`
- `outputs.mqtt.*`
- `exports.*`
- `ui.timezone`

Validaciones relevantes (`PUT /api/config`):

- Webhook habilitado requiere URL HTTP(S) valida.
- MQTT habilitado requiere `host`, `topic` y `port` en rango 1..65535.

## Librerias en local

Frontend local vendorizado:

- `api/static/vendor/bootstrap/bootstrap.min.css`
- `api/static/vendor/bootstrap/bootstrap.bundle.min.js`
- `api/static/vendor/chart/chart.umd.min.js`

Dependencias Python cacheadas:

- `third_party/python-wheels/*.whl`

## Limitaciones conocidas

- Sin test suite completa todavia.
- Sensor por defecto mock; pendiente integrar drivers reales estables.
- SQLite adecuado para edge/local, no para carga multiusuario alta.

## Operacion recomendada (Raspberry/Linux)

- Ejecutar `python -m scripts.run_all` como servicio `systemd`.
- Definir politica de backup de `data/`.
- Revisar periodicamente outbox (`/outbox.html`) y exports (`/exports.html`).

## Roadmap propuesto

1. Endurecer autenticacion (hash de credenciales, secret manager, rotacion).
2. Tests unitarios/integracion + CI.
3. Observabilidad (logs estructurados + metricas + alertas).
4. Drivers reales de sensores y validacion de calidad de dato.
5. Hardening de despliegue (servicio, backups, recuperacion).

## Licencia

Ver archivo `LICENSE`.
