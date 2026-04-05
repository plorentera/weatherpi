# Architecture & Design

This document describes the architecture and design decisions in WeatherPi.

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    WEATHERPI SYSTEM                          │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │   API        │  │  Collector   │  │   Outputs    │       │
│  │ (FastAPI)    │  │   Worker     │  │    Worker    │       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘       │
│         │                 │                 │                │
│         ├─────────────────┴─────────────────┤                │
│         │                                   │                │
│         └──────────┬──────────────┬─────────┘                │
│                    │              │                          │
│              ┌─────▼─────┐   ┌────▼──────┐                  │
│              │  SQLite   │   │  Outbox   │                  │
│              │ Database  │   │   Queue   │                  │
│              └───────────┘   └───────────┘                  │
│                                                               │
│  ┌──────────────┐                                            │
│  │   Backup     │                                            │
│  │   Worker     │                                            │
│  └──────────────┘                                            │
│                                                               │
└─────────────────────────────────────────────────────────────┘
         │                    │                 │
         ├────────────────────┼─────────────────┤
         │                    │                 │
    ┌────▼───┐         ┌──────▼────┐      ┌────▼────┐
    │ Browser│         │ Webhook   │      │  MQTT   │
    │Dashboard         │ Endpoint  │      │ Broker  │
    └────────┘         └───────────┘      └─────────┘
```

## Components

### 1. API Server (`api/main.py`)

**Responsibilities:**
- HTTP REST API with FastAPI
- Authentication & authorization
- Request/response handling
- Session management
- Configuration management
- Static file serving (dashboard)

**Key Features:**
- OpenAPI/Swagger documentation
- HTTP Basic auth + session cookies
- Role-based access control (reader/admin)
- CORS headers
- Rate limiting (via Nginx)
- Health checks

**Technology:**
- Framework: FastAPI
- ASGI Server: Uvicorn
- Validation: Pydantic

### 2. Collector (`collector/main.py`)

**Responsibilities:**
- Read from sensor at regular intervals
- Store measurements in database
- Enqueue webhook/MQTT messages
- Handle configuration changes dynamically
- Manage data retention policies

**Key Features:**
- Pluggable sensor drivers
- Configurable sample interval
- Retention-based cleanup
- Dynamic reconfiguration without restart
- Error handling & fallbacks

**Sensor Abstraction:**
```python
class BaseSensorDriver:
    def read(self) -> Dict[str, float]:
        """Return {temperature, humidity, ...}"""
```

### 3. Outputs Worker (`collector/outputs_worker.py`)

**Responsibilities:**
- Process outbox queue
- Send webhooks
- Publish to MQTT
- Handle retries with backoff
- Track delivery status

**Features:**
- Exponential backoff retry
- Configurable timeout
- HTTP/HTTPS webhook support
- MQTT QoS support
- Batch processing

### 4. Backup Worker (`collector/backup_worker.py`)

**Responsibilities:**
- Generate CSV exports on schedule
- Clean old exports based on retention
- Upload exports if configured
- Update export metadata

**Features:**
- Scheduled exports (daily/weekly/monthly)
- Local time and UTC scheduling
- Configurable data retention
- Optional webhook upload

### 5. Database (`common/db.py`)

**Persistence Model:**
```sql
-- Measurements
CREATE TABLE measurements (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,           -- Unix timestamp
    temperature REAL NOT NULL,
    humidity REAL,
    pressure REAL,
    altitude REAL
);

-- Configuration
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Outbox (queue)
CREATE TABLE outbox (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    type TEXT NOT NULL,            -- webhook, mqtt
    payload TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    retries INTEGER DEFAULT 0
);

-- Exports metadata
CREATE TABLE exports (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    uploaded INTEGER DEFAULT 0
);
```

**Key Operations:**
- Insert measurements
- Query historical data
- Manage outbox queue
- Store configuration
- Track exports

## Data Flow

### Measurement Collection

```
Sensor Read
    ↓
Insert Measurement
    ↓
Check if outputs enabled
    ↓
Enqueue Webhook/MQTT (if enabled)
    ↓
Log entry
```

### Output Processing

```
Dequeue Outbox Item
    ↓
Send (HTTP/MQTT)
    ↓
Success?
├─→ Yes: Mark as sent, update status
├─→ No: Increment retries
    ├─→ Retries < max? Sleep & retry
    └─→ Else: Mark as failed
```

### Export Generation

```
Check Export Schedule
    ↓
Time to export?
├─→ No: Sleep
├─→ Yes: Query measurements
    ├─→ Generate CSV
    ├─→ Save file
    ├─→ Update metadata
    ├─→ Upload if enabled
    └─→ Log result
```

## Configuration Management

**Storage:** SQLite table `settings`

**Format:** JSON-serialized Python dict

**Example:**
```json
{
    "station_id": "meteo-001",
    "sample_interval_seconds": 5,
    "_rev": 42,
    "outputs": {
        "webhook": {
            "enabled": true,
            "url": "https://example.com/webhook"
        },
        "mqtt": {
            "enabled": false
        }
    }
}
```

**Changes:**
- Update via API: `PUT /settings`
- Increment `_rev` on each change
- Collector checks `_rev` periodically
- Apply changes without restart

## Security Model

### Authentication

**Methods:**
1. **HTTP Basic Auth**: `Authorization: Basic base64(user:pass)`
2. **Session Cookies**: `weatherpi_session=token`

**Credentials:**
```python
# Can be plaintext or PBKDF2 hash
WEATHERPI_ADMIN_PASS="admin"
# or
WEATHERPI_ADMIN_PASS_HASH="pbkdf2_sha256$100000$salt$hash"
```

### Authorization

**Roles:**
- `reader`: GET, HEAD, OPTIONS only
- `admin`: Full access including PUT, POST, DELETE

**Enforcement:**
```python
if method not in SAFE_METHODS and role != "admin":
    raise HTTPException(403, "Admin role required")
```

## Concurrency Model

### Process Model

- **API**: Single ASGI process (Uvicorn)
  - Thread-per-request (via Starlette)
  - SQLite handles concurrent reads

- **Collector**: Single process
  - Sleeps between samples
  - Non-blocking I/O for webhooks

- **Workers**: Separate processes
  - Parallel processing possible
  - Independent databases access

### Database Concurrency

SQLite with WAL mode:
- Multiple readers allowed
- Single writer
- Automatic locking
- See: `PRAGMA journal_mode=WAL`

## Resilience & Recovery

### Error Handling

**Collector:**
- Sensor read fails: Log & skip sample
- Database write fails: Retry next interval
- Config change fails: Continue with old config

**Outputs:**
- Send fails: Exponential backoff retry
- Max retries exceeded: Mark failed, log warning
- MQTT connection fails: Reconnect with backoff

**Backup:**
- Schedule check fails: Retry next interval
- CSV generation fails: Log error, continue
- Upload fails: Retry next run

### Data Persistence

- Measurements never lost (SQLite ACID)
- Outbox items stored until confirmed sent
- Exports kept for retention period
- Configuration versioned (_rev)

## Scalability Considerations

### Current Limits

- SQLite: ~1 million measurements (depends on retention)
- Outbox: Unlimited (but disk-bound)
- Webhooks: Sequential processing
- MQTT: Single connection per broker

### Future Scaling

- **PostgreSQL** option for large datasets
- **Celery** for distributed tasks
- **Redis** cache for hot data
- **Kubernetes** deployment
- **Load balancer** for API replicas

## Design Decisions

### Why SQLite?

✅ **Pros:**
- No server setup
- Simple deployment
- Perfect for single-machine IoT
- ACID guarantees
- File-based backups

❌ **Cons:**
- Limited to single machine
- Not ideal for massive scale (>10M rows)

### Why Sync Worker Processes?

✅ **Pros:**
- Simple implementation
- Predictable behavior
- Easy debugging
- Resource efficient

❌ **Cons:**
- One blocking operation delays others
- Not ideal for many outputs

### Why Sessions + Basic Auth?

✅ **Pros:**
- Web UI convenience (sessions)
- Program-friendly (Basic auth)
- Simple implementation

❌ **Cons:**
- No distributed auth
- No advanced features (OIDC, MFA)
- Future roadmap item

## Performance Optimization

### Database Indexes

```sql
CREATE INDEX idx_measurements_ts ON measurements(ts);
CREATE INDEX idx_outbox_status ON outbox(status);
```

### Query Patterns

- Time-range queries: `WHERE ts BETWEEN ? AND ?`
- Latest measurement: `ORDER BY ts DESC LIMIT 1`
- Pending outbox: `WHERE status = 'pending'`

### Caching

- API docs: Lazy cached
- Config: Cached in memory (invalidated on _rev change)
- Measurements: Not cached (always fresh)

## Testing Strategy

### Unit Tests
- Model validation (Pydantic)
- Individual functions
- Database operations

### Integration Tests
- API endpoints
- Auth/authz
- Full workflows

### End-to-End Tests
- Full system startup
- Data collection → export → delivery

## Deployment Implications

### Single Process vs Multiple

- **Recommended**: API separate from workers
- **Smallest footprint**: Combined process
- **High availability**: Multiple API replicas + workers

### Database Considerations

- SQLite: Local file, simple backups
- PostgreSQL: Traditional setup, scalable

### Monitoring Points

- API: Response time, errors, auth attempts
- Collector: Sample frequency, sensor errors
- Outputs: Delivery rate, retry count, failures
- Database: Size, query time, locks

---

**Architecture Version**: 1.0
**Last Updated**: 2024-01-01
