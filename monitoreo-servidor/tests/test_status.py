"""Tests for GET /v1/status and GET /v1/rooms.

Author: Daniel Perez
"""

from fastapi.testclient import TestClient

from tests.conftest import TEST_ADMIN_TOKEN, TEST_SALA


def _admin_headers():
    return {"X-Admin-Token": TEST_ADMIN_TOKEN}


# ── /v1/status ────────────────────────────────────────────────────────────────

def test_status_requires_admin_token(client: TestClient) -> None:
    assert client.get("/v1/status").status_code == 401


def test_status_wrong_admin_token_returns_401(client: TestClient) -> None:
    assert client.get("/v1/status", headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_status_with_valid_token_returns_200(client: TestClient) -> None:
    assert client.get("/v1/status", headers=_admin_headers()).status_code == 200


def test_status_response_schema(client: TestClient) -> None:
    data = client.get("/v1/status", headers=_admin_headers()).json()
    assert "uptime_seconds" in data
    assert "rooms_configured" in data
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


def test_status_rooms_configured_count(client: TestClient) -> None:
    data = client.get("/v1/status", headers=_admin_headers()).json()
    assert data["rooms_configured"] == 1  # only SALA-01 in test tokens


# ── /v1/rooms ─────────────────────────────────────────────────────────────────

def test_rooms_requires_admin_token(client: TestClient) -> None:
    assert client.get("/v1/rooms").status_code == 401


def test_rooms_with_valid_token_returns_200(client: TestClient) -> None:
    assert client.get("/v1/rooms", headers=_admin_headers()).status_code == 200


def test_rooms_response_schema(client: TestClient) -> None:
    data = client.get("/v1/rooms", headers=_admin_headers()).json()
    assert "rooms" in data
    assert isinstance(data["rooms"], list)


def test_rooms_lists_configured_sala(client: TestClient) -> None:
    rooms = client.get("/v1/rooms", headers=_admin_headers()).json()["rooms"]
    codes = [r["code"] for r in rooms]
    assert TEST_SALA in codes


def test_rooms_does_not_expose_hashes(client: TestClient) -> None:
    rooms = client.get("/v1/rooms", headers=_admin_headers()).json()["rooms"]
    for room in rooms:
        assert "hash" not in str(room).lower() or "active_hash" not in room
