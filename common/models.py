"""Pydantic models for request/response validation and configuration."""

from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum

from pydantic import BaseModel, Field, HttpUrl, validator


class MeasurementMetrics(BaseModel):
    """Measurement metrics from sensor."""

    temperature: float = Field(..., description="Temperature in Celsius")
    humidity: Optional[float] = Field(None, ge=0, le=100, description="Humidity percentage")
    pressure: Optional[float] = Field(None, description="Atmospheric pressure in hPa")
    altitude: Optional[float] = Field(None, description="Altitude in meters")

    class Config:
        schema_extra = {
            "example": {
                "temperature": 22.5,
                "humidity": 55.0,
                "pressure": 1013.25,
                "altitude": 100.0,
            }
        }


class MeasurementRecord(BaseModel):
    """Complete measurement record."""

    id: int
    ts: int = Field(..., description="Unix timestamp (seconds)")
    metrics: MeasurementMetrics

    class Config:
        schema_extra = {"example": {"id": 1, "ts": 1700000000, "metrics": {}}}


class WebhookOutput(BaseModel):
    """Webhook output configuration."""

    enabled: bool = False
    url: Optional[HttpUrl] = None
    timeout_seconds: int = Field(default=5, ge=1, le=60)

    class Config:
        schema_extra = {
            "example": {
                "enabled": True,
                "url": "https://example.com/webhook",
                "timeout_seconds": 5,
            }
        }


class MQTTOutput(BaseModel):
    """MQTT output configuration."""

    enabled: bool = False
    host: Optional[str] = None
    port: int = Field(default=1883, ge=1, le=65535)
    topic: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

    class Config:
        schema_extra = {
            "example": {
                "enabled": True,
                "host": "mqtt.example.com",
                "port": 1883,
                "topic": "meteo/measurements",
            }
        }


class OutputsConfig(BaseModel):
    """All output configurations."""

    webhook: WebhookOutput = Field(default_factory=WebhookOutput)
    mqtt: MQTTOutput = Field(default_factory=MQTTOutput)


class CollectorConfig(BaseModel):
    """Collector configuration."""

    enabled: bool = True


class ExportSchedule(BaseModel):
    """Export schedule configuration."""

    time_local: str = Field(default="01:00", description="Local time HH:MM")
    time_utc: str = Field(default="00:00", description="UTC time HH:MM")

    @validator("time_local", "time_utc", check_fields=False)
    def validate_time_format(cls, v):
        """Validate HH:MM format."""
        try:
            parts = v.split(":")
            if len(parts) != 2:
                raise ValueError
            hour, minute = int(parts[0]), int(parts[1])
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError
        except (ValueError, IndexError):
            raise ValueError(f"Invalid time format: {v}. Expected HH:MM")
        return v


class ExportUpload(BaseModel):
    """Export upload configuration."""

    enabled: bool = False
    webhook_url: Optional[HttpUrl] = None


class ExportsConfig(BaseModel):
    """Exports configuration."""

    enabled: bool = False
    frequency: str = Field(default="daily", description="daily, weekly, monthly")
    every_days: int = Field(default=1, ge=1)
    keep_days: int = Field(default=30, ge=1)
    days_per_file: int = Field(default=1, ge=1)
    schedule: ExportSchedule = Field(default_factory=ExportSchedule)
    upload: ExportUpload = Field(default_factory=ExportUpload)

    @validator("frequency")
    def validate_frequency(cls, v):
        """Validate frequency value."""
        if v not in ("daily", "weekly", "monthly"):
            raise ValueError(f"frequency must be daily, weekly, or monthly, got {v}")
        return v


class StationConfig(BaseModel):
    """Complete station configuration."""

    station_id: str = Field(default="meteo-001")
    sample_interval_seconds: int = Field(default=5, ge=1)
    outputs: OutputsConfig = Field(default_factory=OutputsConfig)
    collector: CollectorConfig = Field(default_factory=CollectorConfig)
    exports: ExportsConfig = Field(default_factory=ExportsConfig)
    _rev: int = Field(default=0, description="Configuration revision number")


class StatusResponse(BaseModel):
    """API status response."""

    status: str = "ok"
    version: str = "0.1.0"
    uptime_seconds: Optional[float] = None
    timestamp: int = Field(default_factory=lambda: int(datetime.utcnow().timestamp()))


class OutboxStatus(BaseModel):
    """Outbox status information."""

    total: int
    failed: int
    pending: int
    last_processed_ts: Optional[int] = None


class ConfigurationError(BaseModel):
    """Configuration validation error."""

    field: str
    error: str
    value: Any


class ApiErrorResponse(BaseModel):
    """Standard API error response."""

    error: str
    detail: Optional[str] = None
    code: Optional[str] = None
    errors: Optional[List[ConfigurationError]] = None

    class Config:
        schema_extra = {
            "example": {
                "error": "Unauthorized",
                "detail": "Invalid credentials",
                "code": "AUTH_001",
            }
        }
