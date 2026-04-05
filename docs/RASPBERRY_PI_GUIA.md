# Guia Personal para Ejecutar WeatherPi en Raspberry Pi

Esta guia es para uso diario en una Raspberry Pi (Raspberry Pi OS), enfocada en:
- instalar una sola vez
- arrancar y parar el sistema
- ver si todo esta funcionando
- dejarlo en autoarranque al prender la Pi
- resolver fallos comunes

## 1. Que corre exactamente

Cuando ejecutas:

```bash
python -m scripts.run_all
```

se levantan 4 procesos:
- API (FastAPI + dashboard web)
- Collector (lee sensor y guarda mediciones)
- Outputs worker (envia webhook/MQTT)
- Backup worker (genera exportaciones)

La base de datos es SQLite en:
- `data/meteo.db`

## 2. Instalacion inicial (una sola vez)

Desde una Raspberry Pi limpia:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

Clonar repo:

```bash
git clone https://github.com/plorentera/weatherpi.git
cd weatherpi
```

Crear entorno virtual e instalar:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Inicializar base de datos:

```bash
python -m scripts.init_db
```

Opcional (config de entorno):

```bash
cp .env.example .env
nano .env
```

## 3. Arranque manual rapido

Desde la carpeta del proyecto:

```bash
cd ~/weatherpi
source .venv/bin/activate
python -m scripts.run_all
```

Si todo va bien, veras logs de launcher y la URL del dashboard.

Acceso web en la misma Pi:
- `http://127.0.0.1:8000`

Acceso desde otro equipo de la red:
- `http://IP_DE_TU_PI:8000`

Para conocer la IP de la Pi:

```bash
hostname -I
```

## 4. Parar el sistema

Si estas en primer plano con `run_all`, presiona:
- `Ctrl + C`

El launcher intenta cerrar los 4 procesos de forma ordenada.

## 5. Ver si esta vivo (health checks)

Comprobar API:

```bash
curl -u admin:admin http://127.0.0.1:8000/status
```

Comprobar ultima medicion:

```bash
curl -u admin:admin http://127.0.0.1:8000/latest
```

Ver outbox:

```bash
curl -u admin:admin http://127.0.0.1:8000/outbox
```

Nota: cambia `admin:admin` por tus credenciales reales.

## 6. Ver datos rapido en SQLite

Entrar a SQLite:

```bash
sqlite3 data/meteo.db
```

Contar mediciones:

```sql
SELECT COUNT(*) FROM measurements;
```

Ultimas 10 mediciones:

```sql
SELECT ts, temperature, humidity, pressure FROM measurements ORDER BY ts DESC LIMIT 10;
```

Salir:

```sql
.quit
```

## 7. Autoarranque con systemd (recomendado)

### 7.1 Crear servicio

Crear archivo:

```bash
sudo nano /etc/systemd/system/weatherpi.service
```

Contenido (ajusta `User` y ruta si hace falta):

```ini
[Unit]
Description=WeatherPi Launcher
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/weatherpi
Environment=PATH=/home/pi/weatherpi/.venv/bin
ExecStart=/home/pi/weatherpi/.venv/bin/python -m scripts.run_all
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 7.2 Activar e iniciar

```bash
sudo systemctl daemon-reload
sudo systemctl enable weatherpi
sudo systemctl start weatherpi
```

### 7.3 Comandos utiles de servicio

Estado:

```bash
sudo systemctl status weatherpi
```

Reiniciar:

```bash
sudo systemctl restart weatherpi
```

Parar:

```bash
sudo systemctl stop weatherpi
```

Logs en vivo:

```bash
journalctl -u weatherpi -f
```

## 8. Flujo de mantenimiento semanal

Actualizar codigo:

```bash
cd ~/weatherpi
git pull
source .venv/bin/activate
pip install -r requirements.txt
```

Verificar que arranca:

```bash
python -m scripts.init_db
python -m scripts.run_all
```

Si usas systemd:

```bash
sudo systemctl restart weatherpi
sudo systemctl status weatherpi
```

## 9. Copia de seguridad de la base de datos

Backup manual:

```bash
cp data/meteo.db data/meteo_$(date +%F_%H%M).db
```

Restaurar (con servicio parado):

```bash
sudo systemctl stop weatherpi
cp data/meteo_backup.db data/meteo.db
sudo systemctl start weatherpi
```

## 10. Problemas comunes

### Puerto 8000 ocupado

El launcher intenta automaticamente 8001, 8002, etc.

Ver puertos en uso:

```bash
ss -ltnp | grep 800
```

### No hay datos nuevos

Revisa logs:

```bash
journalctl -u weatherpi -n 200 --no-pager
```

Si ejecutas manualmente, mira consola del collector buscando:
- errores de lectura de sensor
- errores de DB

### No abre desde otro equipo

Verifica:
- que estas usando `IP_DE_TU_PI:8000`
- que la Pi y tu equipo estan en la misma red
- firewall/router

### Credenciales no funcionan

Revisa variables en `.env` o variables de sistema:
- `WEATHERPI_ADMIN_USER`
- `WEATHERPI_ADMIN_PASS`
- `WEATHERPI_READER_USER`
- `WEATHERPI_READER_PASS`

## 11. Checklist diario rapido

1. `sudo systemctl status weatherpi`
2. `curl -u admin:admin http://127.0.0.1:8000/status`
3. Abre dashboard en navegador
4. Confirma que cambia `latest`
5. Revisa que no crezca outbox con fallos

## 12. Tu comando minimo para operar

Si quieres lo mas simple posible:

```bash
cd ~/weatherpi && source .venv/bin/activate && python -m scripts.run_all
```

Con eso deberia correr todo el sistema local.
