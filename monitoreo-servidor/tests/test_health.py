"""Tests for GET /v1/health.

Author: Daniel Perez
"""

from fastapi.testclient import TestClient


def test_health_returns_200(client: TestClient) -> None:
    response = client.get("/v1/health")
    assert response.status_code == 200


def test_health_no_auth_required(client: TestClient) -> None:
    response = client.get("/v1/health")
    assert response.status_code == 200


def test_health_response_schema(client: TestClient) -> None:
    data = client.get("/v1/health").json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "uptime_seconds" in data
    assert isinstance(data["uptime_seconds"], int)
