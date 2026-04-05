# WeatherPi (Meteo Station)

Proyecto open source para una estacion meteorologica local basada en Python + FastAPI + SQLite.

Estado actual: **WIP (work in progress)**. El sistema es funcional para uso local/lab, pero todavia no se considera una plataforma "production-grade".

## Objetivo

Recoger mediciones ambientales, visualizarlas en un dashboard web local, y gestionar telemetria saliente, configuracion remota pull y exportaciones CSV historicas sin exponer la estacion a Internet.

## Alcance actual

### Incluido hoy

- API HTTP con FastAPI para estado, lectura, configuracion efectiva, outbox, workers, remote-config y updates.
- Dashboard web con metricas actuales y grafico historico (Chart.js local).
- Worker de recoleccion con almacenamiento en SQLite.
- Worker de entrega saliente con cola persistente, idempotencia, reintentos y backoff.
- Worker de remote-config pull con overlay controlado.
- Worker de updates pull con descarga/verificacion de bundles.
- Worker de backup/export CSV por programacion.
- UI de configuracion para collector, telemetria, remote-config, updates, security y secret store.
- Librerias frontend servidas en local (sin dependencia de CDN).
- Cache local de dependencias Python en `third_party/python-wheels`.

### Fuera de alcance por ahora

- Autenticacion/autorizacion avanzada (OIDC, MFA, SSO, gestion centralizada).
- Observabilidad completa (metricas Prometheus, tracing, alerting).
- Drivers de sensores reales y calibracion metrologica formal.
- CI/CD y cobertura de tests completa.

## Arquitectura

El sistema se compone de 6 procesos principales:

1. API (`api/main.py`)
2. Collector (`collector/main.py`)
3. Delivery worker (`collector/delivery_worker.py`)
4. Remote config worker (`collector/remote_config_worker.py`)
5. Update worker (`collector/update_worker.py`)
6. Backup worker (`collector/backup_worker.py`)

Persistencia principal:

- SQLite en `data/meteo.db`
- Tablas: `measurements`, `settings`, `outbox`, `exports`, `worker_heartbeats`, `remote_config_state`, `update_state`, `release_history`

Flujo de datos resumido:

1. Collector lee sensor y escribe en `measurements`.
2. Si hay destinos de telemetria habilitados, encola envelopes normalizados en `outbox`.
3. Delivery worker consume `outbox`, entrega por webhook/MQTT y reintenta con backoff.
4. Remote config worker consulta manifests remotos y aplica overlays permitidos.
5. Update worker consulta releases, descarga bundles y los deja staged/verificados.
6. Backup worker genera CSV y registra metadatos en `exports`.
7. Dashboard consulta API para estado, ultima lectura, serie temporal y workers.

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

Este launcher arranca API + collector + delivery + remote_config + update + backup.

Notas:

- Si el puerto `8000` esta ocupado, usa automaticamente el siguiente libre (`8001`, `8002`, ...).
- La URL final del dashboard se imprime en consola.

### Ejecucion por procesos individuales

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
python -m collector.main
python -m collector.delivery_worker
python -m collector.remote_config_worker
python -m collector.update_worker
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
- `WEATHERPI_READER_PASS_HASH` (opcional, recomendado en produccion)
- `WEATHERPI_ADMIN_USER` (default: `admin`)
- `WEATHERPI_ADMIN_PASS` (default: `admin`)
- `WEATHERPI_ADMIN_PASS_HASH` (opcional, recomendado en produccion)
- `WEATHERPI_SESSION_SECRET` (recomendado >= 32 chars)
- `WEATHERPI_COOKIE_SECURE` (`1` en HTTPS)
- `WEATHERPI_COOKIE_SAMESITE` (`lax`, `strict` o `none`)
- `WEATHERPI_SESSION_TTL_SECONDS` (300..604800)

Si defines `*_PASS_HASH`, se usa hash PBKDF2 y se ignora `*_PASS` para ese rol.

Formato de hash soportado:

`pbkdf2_sha256$<iteraciones>$<salt_base64url>$<digest_base64url>`

Ejemplo rapido para generar hash en Python:

```powershell
python -c "import os,base64,hashlib;pwd='cambia_esta_clave';salt=os.urandom(16);it=260000;d=hashlib.pbkdf2_hmac('sha256',pwd.encode(),salt,it);enc=lambda b:base64.urlsafe_b64encode(b).decode().rstrip('=');print(f'pbkdf2_sha256${it}${enc(salt)}${enc(d)}')"
```

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
- `GET /api/config/secrets`
- `PUT /api/config/secrets`
- `GET /api/outbox`
- `POST /api/outbox/retry_failed`
- `POST /api/outbox/purge_sent`
- `GET /api/system/version`
- `GET /api/system/workers`
- `GET /api/telemetry/status`
- `GET /api/remote-config/status`
- `POST /api/remote-config/check-now`
- `GET /api/update/status`
- `POST /api/update/check-now`
- `POST /api/update/apply`
- `POST /api/update/rollback`
- `GET /api/export.csv?days=7`
- `GET /api/exports`
- `GET /api/exports/{id}`

Documentacion:

- `GET /docs` (Swagger)
- `GET /openapi.json`
- `GET /docs/API.md`

## Configuracion

La configuracion no sensible se guarda en `settings.key = 'config'` (JSON) y el overlay remoto aplicado en `settings.key = 'remote_config_overlay'`.

La configuracion sensible se guarda aparte en `data/device_secrets.json`.

Campos principales:

- `station_id`
- `sample_interval_seconds` (1..3600)
- `collector.enabled`
- `telemetry.destinations[]`
- `remote_config.*`
- `updates.*`
- `security.*`
- `exports.*`
- `ui.timezone`

Validaciones relevantes (`PUT /api/config`):

- IDs unicos en `telemetry.destinations`.
- Destinos webhook activos requieren URL `https://` o `http://` loopback.
- Destinos MQTT activos requieren `host`, `topic` y `port` valido.
- `remote_config.endpoint` y `updates.manifest_url` deben ser `https://` o loopback si estan activos.

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

- Ejecutar `python -m scripts.run_all` o `python -m scripts.release_launcher supervise -- python -m scripts.run_all` como servicio `systemd`.
- Definir politica de backup de `data/`.
- Revisar periodicamente outbox (`/outbox.html`), workers (`GET /api/system/workers`) y exports (`/exports.html`).

## Roadmap propuesto

1. Endurecer autenticacion (hash de credenciales, secret manager, rotacion).
2. Tests unitarios/integracion + CI.
3. Observabilidad (logs estructurados + metricas + alertas).
4. Drivers reales de sensores y validacion de calidad de dato.
5. Hardening de despliegue (servicio, backups, recuperacion).

## Licencia

Ver archivo `LICENSE`.
