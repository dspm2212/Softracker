"""Tests for GET /v1/status and GET /v1/rooms.

Author: Daniel Perez
"""

from fastapi.testclient import TestClient

from tests.conftest import TEST_ADMIN_TOKEN


def _admin_headers():
    return {"X-Admin-Token": TEST_ADMIN_TOKEN}


def test_status_requires_admin_token(client: TestClient) -> None:
    assert client.get("/v1/status").status_code == 401


def test_status_wrong_admin_token_returns_401(client: TestClient) -> None:
    assert client.get("/v1/status", headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_status_with_valid_token_returns_200(client: TestClient) -> None:
    assert client.get("/v1/status", headers=_admin_headers()).status_code == 200


def test_status_response_schema(client: TestClient) -> None:
    data = client.get("/v1/status", headers=_admin_headers()).json()
    assert "uptime_seconds" in data
    assert data["auth_mode"] == "shared_token"
    assert "token_active_since" in data
    assert "token_in_rotation" in data
    assert "storage" in data
    assert "last_24h" in data


def test_status_storage_keys(client: TestClient) -> None:
    storage = client.get("/v1/status", headers=_admin_headers()).json()["storage"]
    assert "total_files" in storage
    assert "total_size_mb" in storage
    assert "rejected_files" in storage


def test_status_last_24h_keys(client: TestClient) -> None:
    last = client.get("/v1/status", headers=_admin_headers()).json()["last_24h"]
    assert "uploads_ok" in last
    assert "uploads_rejected" in last
    assert "uploads_by_room" in last


def test_status_shared_token_mode(client: TestClient) -> None:
    data = client.get("/v1/status", headers=_admin_headers()).json()
    assert data["auth_mode"] == "shared_token"


def test_rooms_requires_admin_token(client: TestClient) -> None:
    assert client.get("/v1/rooms").status_code == 401


def test_rooms_with_valid_token_returns_200(client: TestClient) -> None:
    assert client.get("/v1/rooms", headers=_admin_headers()).status_code == 200


def test_rooms_response_schema(client: TestClient) -> None:
    data = client.get("/v1/rooms", headers=_admin_headers()).json()
    assert data["auth_mode"] == "shared_token"
    assert "rooms" in data
    assert isinstance(data["rooms"], list)
    assert data["rooms_configured"] == "dynamic"


def test_rooms_are_dynamic_not_preconfigured(client: TestClient) -> None:
    rooms = client.get("/v1/rooms", headers=_admin_headers()).json()["rooms"]
    assert rooms == []


def test_rooms_does_not_expose_hashes(client: TestClient) -> None:
    data = client.get("/v1/rooms", headers=_admin_headers()).json()
    assert "hash" not in str(data).lower()
