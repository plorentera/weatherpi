# Production Deployment Guide

This guide covers deploying WeatherPi to production environments.

## Pre-Deployment Checklist

- [ ] All tests passing: `pytest --cov`
- [ ] No linting errors: `make check`
- [ ] Security audit completed
- [ ] Environment variables configured
- [ ] Database backups configured
- [ ] Monitoring/alerting setup
- [ ] SSL/TLS certificates obtained
- [ ] Firewall rules configured

## Prerequisites

- Python 3.11+
- Systemd (for Linux services) or Windows Service wrapper
- PostgreSQL or SQLite for production database
- Nginx or Apache for reverse proxy
- Certbot for Let's Encrypt SSL/TLS

## Deployment Options

### 1. Traditional Server (Linux)

#### A. With systemd services

Create `/etc/systemd/system/weatherpi-api.service`:

```ini
[Unit]
Description=WeatherPi API Service
After=network.target

[Service]
Type=simple
User=weatherpi
WorkingDirectory=/opt/weatherpi
Environment="PATH=/opt/weatherpi/.venv/bin"
ExecStart=/opt/weatherpi/.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/weatherpi-collector.service`:

```ini
[Unit]
Description=WeatherPi Collector Service
After=network.target

[Service]
Type=simple
User=weatherpi
WorkingDirectory=/opt/weatherpi
Environment="PATH=/opt/weatherpi/.venv/bin"
ExecStart=/opt/weatherpi/.venv/bin/python -m collector.main
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable weatherpi-api weatherpi-collector
sudo systemctl start weatherpi-api weatherpi-collector
```

#### B. Configure Nginx Reverse Proxy

Create `/etc/nginx/sites-available/weatherpi`:

```nginx
upstream weatherpi {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name weather.example.com;

    # Redirect HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name weather.example.com;

    ssl_certificate /etc/letsencrypt/live/weather.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/weather.example.com/privkey.pem;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api_limit:10m rate=60r/m;
    limit_req zone=api_limit burst=100 nodelay;

    location / {
        proxy_pass http://weatherpi;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
}
```

Enable:

```bash
sudo ln -s /etc/nginx/sites-available/weatherpi /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### 2. Docker Deployment

#### Using Docker Compose (Recommended for small deployments)

```bash
# Pull latest image
docker pull plorentera/weatherpi:latest

# Start services
docker-compose up -d

# Check status
docker-compose ps

# View logs
docker-compose logs -f weatherpi
```

#### Kubernetes (For high availability)

Create deployment manifests in `k8s/`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: weatherpi-api
spec:
  replicas: 2
  selector:
    matchLabels:
      app: weatherpi-api
  template:
    metadata:
      labels:
        app: weatherpi-api
    spec:
      containers:
      - name: api
        image: plorentera/weatherpi:latest
        ports:
        - containerPort: 8000
        env:
        - name: API_HOST
          value: "0.0.0.0"
        - name: API_PORT
          value: "8000"
        livenessProbe:
          httpGet:
            path: /status
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /status
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
```

Deploy:

```bash
kubectl apply -f k8s/
```

### 3. Environment Variables (Production)

Create `.env` with production values:

```env
APP_ENV=production
DEBUG=false

# Security - MUST change these!
WEATHERPI_ADMIN_USER=your_admin_username
WEATHERPI_ADMIN_PASS_HASH=pbkdf2_sha256$...$...
WEATHERPI_SESSION_SECRET=generate-with-secrets.token_urlsafe(32)
WEATHERPI_COOKIE_SECURE=1
WEATHERPI_COOKIE_SAMESITE=strict

# Database (if using PostgreSQL)
DATABASE_URL=postgresql://user:password@dbhost:5432/weatherpi

# Logging
LOG_FORMAT=json
LOG_TO_FILE=true
LOG_FILE_PATH=/var/log/weatherpi/weatherpi.log

# Performance
UVICORN_WORKERS=4
```

Generate secure PBKDF2 hash:

```python
import hashlib, secrets, base64

password = "your_password"
iterations = 100_000
salt = secrets.token_bytes(32)
hash_digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)

salt_b64 = base64.urlsafe_b64encode(salt).decode().rstrip("=")
hash_b64 = base64.urlsafe_b64encode(hash_digest).decode().rstrip("=")

print(f"pbkdf2_sha256${iterations}${salt_b64}${hash_b64}")
```

## Monitoring & Logging

### Structured Logging

Configure JSON logging to stdout:

```env
LOG_FORMAT=json
```

Logs can be aggregated with:
- ELK Stack (Elasticsearch, Logstash, Kibana)
- Splunk
- Datadog
- CloudWatch

### Metrics

Enable Prometheus metrics:

```env
FEATURE_PROMETHEUS_METRICS=true
```

Metrics available at: `/metrics`

Scrape config for Prometheus:

```yaml
scrape_configs:
  - job_name: 'weatherpi'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
```

### Health Checks

Endpoint: `/status`

Example monitoring:

```bash
curl -u admin:password http://localhost:8000/status
```

## Database Backups

### SQLite

```bash
# Manual backup
cp data/meteo.db data/meteo.db.backup

# Automated with cron
0 2 * * * /backup/weatherpi_backup.sh
```

### PostgreSQL

```bash
# Manual backup
pg_dump weatherpi_db > weatherpi_$(date +%Y%m%d).sql

# Restore
psql weatherpi_db < weatherpi_20240101.sql

# Automated with pg_dump
0 2 * * * pg_dump -U weatherpi weatherpi_db | gzip > /backup/weatherpi_$(date +\%Y\%m\%d).sql.gz
```

## SSL/TLS Setup

### Let's Encrypt with Certbot

```bash
sudo certbot certonly --standalone -d weather.example.com

# Auto-renewal
sudo systemctl enable certbot.timer
sudo systemctl start certbot.timer
```

## Security Hardening

### Firewall Rules

```bash
# Allow only necessary ports
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp   # SSH
sudo ufw allow 80/tcp   # HTTP
sudo ufw allow 443/tcp  # HTTPS
sudo ufw enable
```

### Fail2Ban Protection

```bash
sudo apt install fail2ban

# Create /etc/fail2ban/jail.local:
[DEFAULT]
bantime = 3600

[sshd]
enabled = true
```

### API Rate Limiting

Already configured in Nginx example above.

## Troubleshooting

### API not responding

```bash
# Check service status
systemctl status weatherpi-api

# View logs
journalctl -u weatherpi-api -n 100 -f

# Test connectivity
curl -i http://127.0.0.1:8000/status
```

### Database locked

Monitor with:
```bash
lsof | grep meteo.db
```

### Performance issues

Monitor with:
```bash
watch 'du -sh data/meteo.db'
sqlite3 data/meteo.db "SELECT COUNT(*) FROM measurements;"
```

## Upgrade Procedure

1. **Backup database**
   ```bash
   cp data/meteo.db data/meteo.db.backup
   ```

2. **Stop services**
   ```bash
   systemctl stop weatherpi-api weatherpi-collector
   ```

3. **Update code**
   ```bash
   git pull origin main
   pip install -r requirements.txt
   ```

4. **Run migrations** (if any)
   ```bash
   python -m scripts.init_db
   ```

5. **Restart services**
   ```bash
   systemctl start weatherpi-api weatherpi-collector
   ```

6. **Verify**
   ```bash
   curl -u admin:password http://localhost:8000/status
   ```

## Contact & Support

For production issues:
- Check logs: `journalctl -u weatherpi-*`
- Review [docs/](../docs/)
- Open GitHub issue with `[PRODUCTION]` tag

**Last Updated**: 2024-01-01
