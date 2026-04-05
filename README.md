# WeatherPi (Meteo Station)

> Open source local weather station with Python + FastAPI + SQLite

Proyecto open source para una estacion meteorologica local basada en Python + FastAPI + SQLite.

**Estado actual**: Early Alpha (v0.1.0) - sistema funcional para uso local/lab. Roadmap a production-ready en desarrollo.

[![Tests](https://github.com/plorentera/weatherpi/actions/workflows/test.yml/badge.svg)](https://github.com/plorentera/weatherpi/actions/workflows/test.yml)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL%203.0-blue.svg)](LICENSE)

## 📋 Tabla de Contenidos

- [Características](#características)
- [Requisitos](#requisitos)
- [✨ Características

### Incluido Hón](#configuración)
- [Desarrollo](#desarrollo)
- [Testing](#testing)
- [Docker](#docker)
- [API Reference](#api-reference)
- [Contributing](#contributing)
- [Roadmap](#roadmap)
- [License](#license)

## 🎯 Objetivo

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

### Fuera de Alcance (Por Ahora)

- Autenticacion/autorizacion avanzada (OIDC, MFA, SSO, gestion centralizada).
- Observabilidad completa (metricas Prometheus, tracing, alerting).
- Drivers de sensores reales y calibracion metrologica formal.
- CI/CD y cobertura de tests completa.

## 🏗️ Arquitectura

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

## 📦 Requisitos

- **Python**: 3.11, 3.12, 3.13, 3.14
- **OS**: Windows, Linux, macOS
- **Git** (opcional, para clonación)

## 🚀 Instalación
Paso 1: Clonar el Repositorio

```bash
git clone https://github.com/plorentera/weatherpi.git
cd weatherpi
```

### Paso 2: Crear Entorno Virtual

**Windows:**
```powershell
python -m venv .venv
.venv\Scripts\activate
```

**Linux/macOS:**
```bash
python -m venv .venv
source .venv/bin/activate
```

### Paso 3: Instalar Dependencias

**Instalación Online:**
```bash
pip install -r requirements.txt
```

**Instalación Offline (con cache local):**
```bash
pip install --no-index --find-links third_party/python-wheels -r requirements.txt
```

### Paso 4: Inicializar Base de Datos

```bash
python -m scripts.init_db
```

### Paso 5 (Opcional): Copiar Configuración

```bash
cp .env.example .env
# Editar .env con tus valores
```

## 🏃 Uso Rápido

### Ejecutar Todo en Uno

```bash
python -m scripts.run_all
```

✅ Esto inicia:
- API (puerto 8000)
- Collector
- Outputs worker
- Backup worker

🌐 Acceso al dashboard: http://127.0.0.1:8000

### Ejecutar Procesos Individuales

```bash
# Terminal 1: API
python -m uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2: Collector
python -m collector.main

# Terminal 3: Outputs worker
python -m collector.outputs_worker

# Terminal 4: Backup worker
python -m collector.backup_worker
```

### Con Make (Windows/Mac/Linux)

```bash
make install       # Instalar dependencias
make run           # Ejecutar todos los servicios
make dev           # Ejecutar con auto-reload
```

## ⚙️ Configuración

### Variables de Entorno

Crear archivo `.env` (ver `.env.example`):

## 📚 API Reference

### Documentación Interactiva

- **Swagger UI**: http://127.0.0.1:8000/docs
- **ReDoc**: http://127.0.0.1:8000/redoc
- **OpenAPI JSON**: http://127.0.0.1:8000/openapi.json

Ver [API.md](docs/API.md) para referencia completa.

### Endpoints Principales

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/status` | Estado del sistema |
| GET | `/latest` | Última medición |
| GET | `/series?limit=288` | Serie histórica |
| GET | `/settings` | Configuración actual |
| PUT | `/settings` | Actualizar configuración |
| GET | `/outbox` | Estado del outbox |
| POST | `/logout` | Cerrar sesión |

## 🤝 Contributing

Ver [CONTRIBUTING.md](CONTRIBUTING.md) para:
- Workflow de desarrollo
- Guía de estilo
- Proceso de pull requests
- Reporting issues

### Quick Start:

```bash
# 1. Fork y clonar
git clone https://github.com/plorentera/weatherpi.git

# 2. Crear rama
git checkout -b feature/your-feature

# 3. Instalar dev dependencies
pip install -r requirements-dev.txt

# 4. Hacer cambios y tests
make test
make check

# 5. Commit y push
git commit -m "feature: descripcion clara"
git push origin feature/your-feature

# 6. Abrir pull request
```

## 📋 Roadmap

**v0.1.0** (Current)
- ✅ Core API y Collector
- ✅ Pydantic models y validación
- ✅ Tests básicos
- ✅ Docker support
- ✅ CI/CD con GitHub Actions

**v0.2.0** (Próximo)
- [ ] Real sensor drivers (DHT22, BMP280)
- [ ] Prometheus metrics
- [ ] Database migrations
- [ ] Advanced authentication (OIDC)
- [ ] Web UI improvements
- [ ] Performance optimizations

**v1.0.0** (Production)
- [ ] Full test coverage (>90%)
- [ ] Complete observability
- [ ] Kubernetes ready
- [ ] Commercial-grade security audit
- [ ] Official documentation en múltiples idiomas

## 📝 License

Este proyecto está licenciado bajo **GNU General Public License v3.0 or later**.

Ver [LICENSE](LICENSE) para más detalles.

## 💬 Support

- 📖 [Documentación](docs/)
- 🍓 [Guia Raspberry Pi](docs/RASPBERRY_PI_GUIA.md)
- 🐛 [Reportar Issues](https://github.com/plorentera/weatherpi/issues)
- 💡 [Sugerencias](https://github.com/plorentera/weatherpi/discussions)

## 🙏 Agradecimientos

Gracias a todos los contribuyentes y a la comunidad open source.

---

**Made with ❤️ for the IoT & Weather enthusiasts**

```env
# Core
APP_ENV=development
DEBUG=false

# API
API_HOST=127.0.0.1
API_PORT=8000

# Authentication
WEATHERPI_ADMIN_USER=admin
WEATHERPI_ADMIN_PASS=admin
WEATHERPI_SESSION_SECRET=your-secret-here

# Collector
COLLECTOR_ENABLED=true
COLLECTOR_SAMPLE_INTERVAL_SECONDS=5

# Outputs
WEBHOOK_ENABLED=false
WEBHOOK_URL=https://example.com/webhook

MQTT_ENABLED=false
MQTT_HOST=localhost
MQTT_PORT=1883
MQTT_TOPIC=meteo/measurements
```

### Archivo .env.example

Siempre disponible como referencia:

```bash
cat .env.example
```

## 👨‍💻 Desarrollo

### Setup Desarrollo

```bash
pip install -r requirements-dev.txt
pip install -e .
pre-commit install
```

### Formato de Código

```bash
make format  # black + isort
```

### Linting

```bash
make lint    # ruff + pylint
```

### Type Checking

```bash
make type    # mypy
```

### Quality Check Completo

```bash
make check   # format + lint + type
```

## 🧪 Testing

### Ejecutar Tests

```bash
make test          # Ejecutar tests
make test-cov      # Tests con coverage
```

### Cobertura

```bash
make coverage      # Genera reporte en htmlcov/
```

Tests incluidos:
- ✅ Validación de Pydantic models
- ✅ Integración de API
- ✅ Base de datos
- ✅ Autenticación

## 🐳 Docker

### Build

```bash
make docker
```

### Ejecutar

```bash
make docker-up     # Inicia servicios
make docker-down   # Detiene servicios
```

### Con Docker Compose

```bash
docker-compose up -d              # Inicia
docker-compose down               # Detiene
docker-compose logs -f weatherpi  # Ver logs
```

### Desde Docker Registry

```bash
docker pull plorentera/weatherpi:latest
docker run -p 8000:8000 -e WEATHERPI_ADMIN_PASS=tu_password plorentera/weatherpi:latest
```

## 🔐 Autenticación y Autorizació
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
