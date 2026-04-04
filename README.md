# Meteo Station

Panel y backend para una estación meteorológica local con API, dashboard web, cola de envíos y exports CSV.

## Funcionalidades

- Dashboard en tiempo real con métricas de temperatura, humedad y presión.
- Configuración desde interfaz web para station ID, intervalos y salidas.
- Outbox para revisar, reintentar y purgar envíos fallidos.
- Exportación y descarga de CSV históricos.
- UI responsive con Bootstrap 5.

## Arranque rápido

1. Crear y activar el entorno virtual.

```powershell
.venv\Scripts\activate
```

2. Instalar dependencias.

```powershell
pip install -r requirements.txt
```

3. Inicializar la base de datos.

```powershell
python -m scripts.init_db
```

4. Levantar todo con un solo comando.

```powershell
python -m scripts.run_all
```

5. Abrir el panel en el navegador.

```powershell
http://127.0.0.1:8000
```

Si el puerto `8000` esta ocupado, el launcher elegira automaticamente el siguiente puerto libre y lo mostrara por consola.

## Raspberry

- El panel permite pausar o reanudar el collector con el switch de configuración.
- Para despliegue real, lo más limpio es ejecutar `python -m scripts.run_all` como servicio `systemd`.
- El launcher principal ya arranca API, collector, outputs y backup.

## Procesos individuales

```powershell
python -m collector.main
python -m collector.outputs_worker
python -m collector.backup_worker
```

## Rutas útiles

- `/` dashboard principal
- `/settings.html` configuración
- `/outbox.html` cola de envíos
- `/exports.html` exports generados
- `/api/status` estado de la estación
- `/api/latest` última lectura
