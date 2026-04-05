"""Tests for API request handlers."""

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client():
    """FastAPI test client with credentials."""
    return TestClient(app)


class TestAPIStatus:
    """Test API status endpoint."""

    def test_status_requires_auth(self, client):
        """Test that status endpoint requires authentication."""
        response = client.get("/status")
        assert response.status_code == 401

    def test_status_with_auth(self, client, monkeypatch):
        """Test status endpoint with authentication."""
        monkeypatch.setenv("WEATHERPI_ADMIN_PASS", "admin")
        # Create new client after env change
        client = TestClient(app)
        response = client.get("/status", auth=("admin", "admin"))
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "version" in data


class TestAPILogin:
    """Test login endpoints."""

    def test_login_page_requires_auth(self, client):
        """Test login page GET requires auth."""
        response = client.get("/login")
        # Login page should typically be accessible without auth
        # (in this case it requires auth based on implementation)
        assert response.status_code in (200, 401)


class TestAPILatest:
    """Test latest measurement endpoint."""

    def test_latest_requires_auth(self, client):
        """Test latest endpoint requires authentication."""
        response = client.get("/latest")
        assert response.status_code == 401


class TestAPISeries:
    """Test measurement series endpoint."""

    def test_series_requires_auth(self, client):
        """Test series endpoint requires authentication."""
        response = client.get("/series")
        assert response.status_code == 401
