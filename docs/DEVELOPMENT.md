# Development Guide

This guide helps developers set up and contribute to WeatherPi.

## Project Structure

```
weatherpi/
├── api/                      # FastAPI application
│   ├── main.py              # Main API entry point
│   └── static/              # Frontend assets (HTML, CSS, JS)
├── collector/               # Data collection subsystem
│   ├── main.py             # Collector process
│   ├── outputs_worker.py   # Outputs/webhooks worker
│   ├── backup_worker.py    # Backup/export worker
│   └── sensors/            # Sensor drivers
│       ├── base.py         # Abstract sensor interface
│       └── mock.py         # Mock sensor for testing
├── common/                  # Shared code
│   ├── db.py               # Database models and operations
│   ├── config.py           # Configuration management
│   └── models.py           # Pydantic data models
├── tests/                   # Test suite
│   ├── test_models.py      # Model validation tests
│   ├── test_api_models.py  # API endpoint tests
│   └── conftest.py         # Pytest configuration
├── docs/                    # Documentation
├── scripts/                 # Utility scripts
├── pyproject.toml          # Modern Python project config
├── setup.py                # setuptools entry point
├── requirements.txt        # Production dependencies
├── requirements-dev.txt    # Development dependencies
└── Dockerfile              # Docker image definition
```

## Code Style & Quality

### Python Style

- **Formatter**: Black (line length: 100)
- **Import sorting**: isort (Black profile)
- **Linter**: ruff (errors + warnings)
- **Type checker**: mypy

### Pre-commit Hooks

Install:
```bash
pre-commit install
```

Hooks run automatically on `git commit`. To run manually:
```bash
pre-commit run --all-files
```

### Docstrings

Use Google-style docstrings:

```python
def calculate_average(values: List[float]) -> float:
    """Calculate the average of numeric values.

    Args:
        values: List of numeric values to average.

    Returns:
        The arithmetic mean of the values.

    Raises:
        ValueError: If values list is empty.

    Example:
        >>> calculate_average([1.0, 2.0, 3.0])
        2.0
    """
    if not values:
        raise ValueError("Values list cannot be empty")
    return sum(values) / len(values)
```

### Type Hints

Add type hints to all functions:

```python
from typing import Dict, List, Optional

def process_data(
    data: Dict[str, float],
    threshold: Optional[float] = None
) -> List[float]:
    """Process measurement data."""
    result: List[float] = []
    for key, value in data.items():
        if threshold is None or value > threshold:
            result.append(value)
    return result
```

## Testing

### Writing Tests

Tests should be in `tests/` directory with `test_*.py` naming:

```python
import pytest
from api.handlers import get_status

class TestStatusEndpoint:
    """Test suite for status endpoint."""

    def test_status_returns_200(self, client):
        """Test that status endpoint returns 200 OK."""
        response = client.get("/status")
        assert response.status_code == 200

    def test_status_has_required_fields(self, client):
        """Test that status response has required fields."""
        response = client.get("/status")
        data = response.json()
        assert "status" in data
        assert "version" in data

    def test_status_requires_auth(self):
        """Test that unauthenticated requests are rejected."""
        response = client.get("/status")
        assert response.status_code == 401
```

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_models.py

# Run specific test
pytest tests/test_models.py::TestMeasurementMetrics

# With coverage
pytest --cov=api --cov=collector --cov=common

# With verbose output
pytest -v

# Stop on first failure
pytest -x
```

### Coverage Goals

- **Target**: >80% code coverage
- **View report**: `htmlcov/index.html`

## Database Development

### Schema Changes

Edit `common/db.py` for schema changes:

```python
# Add new table
def create_new_table():
    """Create new measurement_metadata table."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS measurement_metadata (
            id INTEGER PRIMARY KEY,
            measurement_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            FOREIGN KEY(measurement_id) REFERENCES measurements(id)
        )
    """)
    conn.commit()
    conn.close()
```

### Testing Queries

Use pytest with temp database:

```python
def test_query_with_temp_db(temp_db, monkeypatch):
    """Test database query with temporary database."""
    monkeypatch.setattr("common.db.DB_PATH", temp_db)
    
    # Now tests use temp_db
    result = fetch_measurements()
    assert result == []
```

## API Development

### Adding Endpoints

Add handler to `api/main.py`:

```python
from fastapi import HTTPException, Depends
from common.models import MeasurementRecord

@app.get("/measurements/{measurement_id}", response_model=MeasurementRecord)
async def get_measurement(
    measurement_id: int,
    current_user: str = Depends(get_current_user)
) -> MeasurementRecord:
    """Get a specific measurement by ID.

    Args:
        measurement_id: The ID of the measurement.
        current_user: The authenticated user (from session/basic auth).

    Returns:
        The measurement record.

    Raises:
        HTTPException: If measurement not found (404).
    """
    measurement = fetch_measurement_by_id(measurement_id)
    if not measurement:
        raise HTTPException(status_code=404, detail="Measurement not found")
    return measurement
```

### Request/Response Models

Define in `common/models.py`:

```python
from pydantic import BaseModel, Field

class MeasurementInput(BaseModel):
    """Model for measurement input."""
    temperature: float = Field(..., ge=-50, le=150)
    humidity: Optional[float] = Field(None, ge=0, le=100)

    class Config:
        schema_extra = {
            "example": {
                "temperature": 22.5,
                "humidity": 55.0
            }
        }
```

### Authorization

Use role-based checks:

```python
from api.main import _role_for_credentials

@app.delete("/settings")
async def delete_settings(request: Request) -> dict:
    """Delete all settings (admin only)."""
    username, password = extract_credentials(request)
    role = _role_for_credentials(username, password)
    
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    
    # Perform deletion
    return {"deleted": True}
```

## Sensor Development

### Implementing New Sensor Driver

```python
from collector.sensors.base import BaseSensorDriver

class DHT22Driver(BaseSensorDriver):
    """DHT22 temperature/humidity sensor driver."""

    def __init__(self, pin: int = 4):
        """Initialize DHT22 sensor.

        Args:
            pin: GPIO pin number.
        """
        self.pin = pin
        # Initialize hardware here

    def read(self) -> Dict[str, float]:
        """Read sensor values.

        Returns:
            Dictionary with temperature and humidity.

        Raises:
            SensorError: If read fails.
        """
        # Read from sensor
        temp = 22.5  # Mock value
        humidity = 55.0
        return {"temperature": temp, "humidity": humidity}
```

### Testing Sensor

```python
def test_dht22_driver():
    """Test DHT22 driver."""
    driver = DHT22Driver(pin=4)
    data = driver.read()
    assert "temperature" in data
    assert "humidity" in data
```

## Performance & Optimization

### Database Optimization

```python
# Add indexes for frequent queries
def create_indexes():
    """Create database indexes."""
    conn = get_connection()
    conn.execute("CREATE INDEX IF NOT EXISTS idx_measurements_ts ON measurements(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status)")
    conn.commit()
    conn.close()
```

### Query Performance

Monitor with:
```bash
sqlite3 data/meteo.db
sqlite> .timer on
sqlite> SELECT COUNT(*) FROM measurements;
```

## Debugging

### Using Python Debugger

```python
import pdb; pdb.set_trace()

# Or with breakpoint() (Python 3.7+)
breakpoint()
```

### Using Logging

```python
import logging

logger = logging.getLogger("weatherpi.api")
logger.debug("Debug message")
logger.error("Error occurred")
```

### Viewing API Requests

```bash
# Enable debug mode
DEBUG=true python -m uvicorn api.main:app --reload

# Check with curl
curl -v -u admin:admin http://localhost:8000/status
```

## Common Tasks

### Add a New Configuration Option

1. Update `common/models.py`:
   ```python
   class StationConfig(BaseModel):
       new_option: str = Field(default="value")
   ```

2. Update `common/db.py` default config:
   ```python
   DEFAULT_CONFIG = {
       ...,
       "new_option": "value"
   }
   ```

3. Add test in `tests/test_models.py`

### Add a New API Endpoint

1. Define model in `common/models.py`
2. Add handler in `api/main.py`
3. Add test in `tests/test_api_models.py`
4. Document in `docs/API.md`

### Fix a Bug

1. Create test case that reproduces bug
2. Fix the bug
3. Verify test passes
4. Create PR with test + fix

## Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Pydantic Documentation](https://docs.pydantic.dev/)
- [Pytest Documentation](https://docs.pytest.org/)
- [Python Type Hints](https://typing.readthedocs.io/)

## Getting Help

- Check existing [GitHub Issues](https://github.com/plorentera/weatherpi/issues)
- Create [GitHub Discussion](https://github.com/plorentera/weatherpi/discussions)
- Read [CONTRIBUTING.md](../CONTRIBUTING.md)

**Happy coding! 🚀**
