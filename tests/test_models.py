"""Tests for Pydantic data models."""

import pytest
from pydantic import ValidationError

from common.models import (
    MeasurementMetrics,
    MeasurementRecord,
    WebhookOutput,
    MQTTOutput,
    StationConfig,
    ExportsConfig,
    ExportSchedule,
)


class TestMeasurementMetrics:
    """Test MeasurementMetrics model."""

    def test_valid_metrics(self):
        """Test valid measurement metrics."""
        metrics = MeasurementMetrics(
            temperature=22.5, humidity=55.0, pressure=1013.25, altitude=100.0
        )
        assert metrics.temperature == 22.5
        assert metrics.humidity == 55.0

    def test_invalid_humidity_too_high(self):
        """Test humidity validation (max 100)."""
        with pytest.raises(ValidationError):
            MeasurementMetrics(temperature=22.5, humidity=101.0)

    def test_invalid_humidity_negative(self):
        """Test humidity validation (min 0)."""
        with pytest.raises(ValidationError):
            MeasurementMetrics(temperature=22.5, humidity=-1.0)

    def test_optional_fields(self):
        """Test optional fields are truly optional."""
        metrics = MeasurementMetrics(temperature=22.5)
        assert metrics.humidity is None
        assert metrics.pressure is None
        assert metrics.altitude is None


class TestMeasurementRecord:
    """Test MeasurementRecord model."""

    def test_valid_record(self):
        """Test valid measurement record."""
        record = MeasurementRecord(
            id=1, ts=1700000000, metrics=MeasurementMetrics(temperature=22.5)
        )
        assert record.id == 1
        assert record.ts == 1700000000
        assert record.metrics.temperature == 22.5


class TestWebhookOutput:
    """Test WebhookOutput model."""

    def test_valid_webhook(self):
        """Test valid webhook configuration."""
        webhook = WebhookOutput(
            enabled=True, url="https://example.com/webhook", timeout_seconds=10
        )
        assert webhook.enabled is True
        assert str(webhook.url) == "https://example.com/"
        assert webhook.timeout_seconds == 10

    def test_timeout_boundaries(self):
        """Test timeout validation."""
        # Valid: 1 second
        WebhookOutput(enabled=True, url="https://example.com", timeout_seconds=1)

        # Valid: 60 seconds
        WebhookOutput(enabled=True, url="https://example.com", timeout_seconds=60)

        # Invalid: 0 seconds
        with pytest.raises(ValidationError):
            WebhookOutput(enabled=True, url="https://example.com", timeout_seconds=0)

        # Invalid: > 60 seconds
        with pytest.raises(ValidationError):
            WebhookOutput(enabled=True, url="https://example.com", timeout_seconds=61)


class TestMQTTOutput:
    """Test MQTTOutput model."""

    def test_valid_mqtt(self):
        """Test valid MQTT configuration."""
        mqtt = MQTTOutput(enabled=True, host="mqtt.example.com", port=1883, topic="meteo/data")
        assert mqtt.enabled is True
        assert mqtt.host == "mqtt.example.com"
        assert mqtt.port == 1883

    def test_mqtt_port_boundaries(self):
        """Test MQTT port validation."""
        # Invalid: port 0
        with pytest.raises(ValidationError):
            MQTTOutput(enabled=True, host="localhost", port=0)

        # Invalid: port > 65535
        with pytest.raises(ValidationError):
            MQTTOutput(enabled=True, host="localhost", port=65536)

        # Valid: port 1
        MQTTOutput(enabled=True, host="localhost", port=1)

        # Valid: port 65535
        MQTTOutput(enabled=True, host="localhost", port=65535)


class TestExportSchedule:
    """Test ExportSchedule model."""

    def test_valid_schedule(self):
        """Test valid export schedule."""
        schedule = ExportSchedule(time_local="14:30", time_utc="13:30")
        assert schedule.time_local == "14:30"
        assert schedule.time_utc == "13:30"

    def test_invalid_time_format(self):
        """Test invalid time format."""
        with pytest.raises(ValidationError):
            ExportSchedule(time_local="14:30:00")  # Has seconds

        with pytest.raises(ValidationError):
            ExportSchedule(time_local="25:00")  # Invalid hour

        with pytest.raises(ValidationError):
            ExportSchedule(time_local="14:60")  # Invalid minute

    def test_midnight_and_noon(self):
        """Test edge cases."""
        ExportSchedule(time_local="00:00", time_utc="00:00")  # Midnight
        ExportSchedule(time_local="12:00", time_utc="12:00")  # Noon
        ExportSchedule(time_local="23:59", time_utc="23:59")  # 11:59 PM


class TestStationConfig:
    """Test StationConfig model."""

    def test_default_config(self):
        """Test default configuration."""
        config = StationConfig()
        assert config.station_id == "meteo-001"
        assert config.sample_interval_seconds == 5
        assert config._rev == 0

    def test_custom_config(self):
        """Test custom configuration."""
        config = StationConfig(
            station_id="meteo-002",
            sample_interval_seconds=10,
        )
        assert config.station_id == "meteo-002"
        assert config.sample_interval_seconds == 10

    def test_invalid_interval(self):
        """Test invalid sample interval."""
        with pytest.raises(ValidationError):
            StationConfig(sample_interval_seconds=0)


class TestExportsConfig:
    """Test ExportsConfig model."""

    def test_valid_exports(self):
        """Test valid exports configuration."""
        exports = ExportsConfig(enabled=True, frequency="daily")
        assert exports.enabled is True
        assert exports.frequency == "daily"

    def test_invalid_frequency(self):
        """Test invalid frequency."""
        with pytest.raises(ValidationError):
            ExportsConfig(frequency="yearly")

    def test_valid_frequencies(self):
        """Test all valid frequencies."""
        for freq in ("daily", "weekly", "monthly"):
            ExportsConfig(frequency=freq)
