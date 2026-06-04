"""Integration tests for POST /v1/upload.

Author: Daniel Perez
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import TEST_ADMIN_TOKEN, TEST_SALA, TEST_TOKEN


def _multipart(
    client: TestClient,
    token: str | None,
    sala: str,
    file_bytes: bytes,
    filename: str = "test.parquet",
):
    headers = {"X-Auth-Token": token} if token else {}
    return client.post(
        "/v1/upload",
        headers=headers,
        data={"sala_codigo": sala},
        files={"archivo": (filename, file_bytes, "application/octet-stream")},
    )


def test_upload_ok_returns_201(client: TestClient, parquet_bytes: bytes) -> None:
    response = _multipart(client, TEST_TOKEN, TEST_SALA, parquet_bytes)
    assert response.status_code == 201


def test_upload_ok_response_schema(client: TestClient, parquet_bytes: bytes) -> None:
    data = _multipart(client, TEST_TOKEN, TEST_SALA, parquet_bytes).json()
    assert data["status"] == "ok"
    assert "saved_as" in data
    assert isinstance(data["size_bytes"], int)


def test_upload_ok_file_written_to_disk(client: TestClient, parquet_bytes: bytes) -> None:
    response = _multipart(client, TEST_TOKEN, TEST_SALA, parquet_bytes)
    assert response.status_code == 201
    saved_as = response.json()["saved_as"]
    config = client.app.state.config
    assert (config.storage.base_dir / saved_as).exists()


def test_upload_missing_token_returns_401(client: TestClient, parquet_bytes: bytes) -> None:
    response = _multipart(client, None, TEST_SALA, parquet_bytes)
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"


def test_upload_wrong_token_returns_401(client: TestClient, parquet_bytes: bytes) -> None:
    response = _multipart(client, "completely-wrong-token", TEST_SALA, parquet_bytes)
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"


def test_upload_new_room_with_shared_token_returns_201(
    client: TestClient, parquet_bytes: bytes
) -> None:
    response = _multipart(client, TEST_TOKEN, "SALA-99", parquet_bytes)
    assert response.status_code == 201


def test_upload_admin_token_returns_401(
    client: TestClient, parquet_bytes: bytes
) -> None:
    response = _multipart(client, TEST_ADMIN_TOKEN, TEST_SALA, parquet_bytes)
    assert response.status_code == 401


def test_upload_invalid_room_code_format_returns_400(
    client: TestClient, parquet_bytes: bytes
) -> None:
    response = _multipart(client, TEST_TOKEN, "sala con espacios", parquet_bytes)
    assert response.status_code == 400
    assert "room code" in response.json()["detail"]


def test_upload_non_parquet_returns_400(client: TestClient) -> None:
    response = _multipart(client, TEST_TOKEN, TEST_SALA, b"not a parquet file")
    assert response.status_code == 400
    assert "parquet" in response.json()["detail"]


def test_upload_non_parquet_saved_to_rejected(client: TestClient) -> None:
    _multipart(client, TEST_TOKEN, TEST_SALA, b"garbage bytes")
    config = client.app.state.config
    rejected_dir = config.storage.base_dir / config.storage.reject_dir
    rejected_files = list(rejected_dir.rglob("*.bin"))
    assert len(rejected_files) == 1


def test_upload_file_too_large_returns_413(client: TestClient) -> None:
    big = b"x" * (1 * 1024 * 1024 + 1)
    response = _multipart(client, TEST_TOKEN, TEST_SALA, big)
    assert response.status_code == 413
    assert "large" in response.json()["detail"]


def test_upload_ok_creates_audit_entry(client: TestClient, parquet_bytes: bytes) -> None:
    import json

    _multipart(client, TEST_TOKEN, TEST_SALA, parquet_bytes)
    config = client.app.state.config
    audit_file = config.storage.base_dir / config.storage.audit_log_file
    assert audit_file.exists()
    events = [
        json.loads(l)
        for l in audit_file.read_text(encoding="utf-8").splitlines()
        if l
    ]
    assert any(e["result"] in ("ok", "ok_previous_token") for e in events)


def test_upload_failure_creates_audit_entry(
    client: TestClient, parquet_bytes: bytes
) -> None:
    import json

    _multipart(client, "bad-token", TEST_SALA, parquet_bytes)
    config = client.app.state.config
    audit_file = config.storage.base_dir / config.storage.audit_log_file
    assert audit_file.exists()
    events = [
        json.loads(l)
        for l in audit_file.read_text(encoding="utf-8").splitlines()
        if l
    ]
    assert any(e["result"] == "invalid_token" for e in events)
