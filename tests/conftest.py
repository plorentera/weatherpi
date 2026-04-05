"""Pytest configuration and fixtures."""

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.close()
        yield db_path


@pytest.fixture
def monkeypatch_env(monkeypatch):
    """Fixture to monkeypatch environment variables."""

    def set_env(key: str, value: str):
        monkeypatch.setenv(key, value)

    return set_env


@pytest.fixture
def client():
    """FastAPI test client."""
    from api.main import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    """Setup test environment variables before each test."""
    # Set test credentials
    monkeypatch.setenv("WEATHERPI_READER_USER", "test_reader")
    monkeypatch.setenv("WEATHERPI_READER_PASS", "test_reader_pass")
    monkeypatch.setenv("WEATHERPI_ADMIN_USER", "test_admin")
    monkeypatch.setenv("WEATHERPI_ADMIN_PASS", "test_admin_pass")
    monkeypatch.setenv("WEATHERPI_SESSION_SECRET", "test-secret-key")
    monkeypatch.setenv("DEBUG", "false")
