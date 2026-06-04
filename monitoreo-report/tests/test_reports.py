"""Tests for monitoring dashboard and Excel reporting endpoints.

Author: Daniel Perez
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from tests.conftest import TEST_PASSWORD, TEST_USERNAME


def _auth_headers(password: str = TEST_PASSWORD) -> dict[str, str]:
    """Header-based auth for the JSON API endpoints."""
    return {"X-Admin-Token": password}


def _login(client: TestClient) -> TestClient:
    """POST credentials and return client with session cookie set."""
    client.post(
        "/v1/dashboard/login",
        data={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    return client


def _write_monitoring_parquet(base_dir: Path, room: str = "SALA-01") -> Path:
    path = base_dir / "parquet" / "2026" / "05" / "27" / f"{room}_2026-05-27_PC-01.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "hostname": pa.array(["PC-01", "PC-01", "PC-02"]),
            "usuario": pa.array(["estudiante", "estudiante", "estudiante"]),
            "timestamp_utc": pa.array(
                [
                    datetime(2026, 5, 27, 14, 0, tzinfo=timezone.utc),
                    datetime(2026, 5, 27, 14, 10, tzinfo=timezone.utc),
                    datetime(2026, 5, 27, 14, 20, tzinfo=timezone.utc),
                ],
                type=pa.timestamp("us", tz="UTC"),
            ),
            "nombre_proceso": pa.array(["chrome", "Code", None]),
            "nombre_ejecutable": pa.array(["chrome.exe", "Code.exe", None]),
            "ruta_ejecutable": pa.array(["C:/Chrome/chrome.exe", "C:/Code/Code.exe", None]),
            "titulo_ventana": pa.array(["Chrome", "VS Code", None]),
            "pid": pa.array([100, 200, None], type=pa.int32()),
            "memoria_mb": pa.array([210.5, 120.0, None], type=pa.float32()),
        }
    )
    pq.write_table(table, path, compression="snappy")
    return path


# ── Auth tests ─────────────────────────────────────────────────────────────────

def test_summary_requires_auth(client: TestClient) -> None:
    assert client.get("/v1/reports/summary").status_code == 401


def test_login_wrong_password_returns_401(client: TestClient) -> None:
    response = client.post(
        "/v1/dashboard/login",
        data={"username": TEST_USERNAME, "password": "wrong"},
        follow_redirects=False,
    )
    assert response.status_code == 401


def test_login_wrong_username_returns_401(client: TestClient) -> None:
    response = client.post(
        "/v1/dashboard/login",
        data={"username": "noexiste", "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 401


def test_login_sets_session_cookie(client: TestClient) -> None:
    response = client.post(
        "/v1/dashboard/login",
        data={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "monitoreo_session" in response.headers.get("set-cookie", "")


# ── Report data tests ──────────────────────────────────────────────────────────

def test_summary_reads_parquet(client: TestClient) -> None:
    config = client.app.state.config
    _write_monitoring_parquet(config.storage.base_dir)

    response = client.get("/v1/reports/summary", headers=_auth_headers())

    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["salas"] == 1
    assert data["summary"]["equipos"] == 2
    assert data["summary"]["registros_apps"] == 2
    assert data["summary"]["capturas_sin_apps"] == 1
    assert data["rooms"][0]["sala_codigo"] == "SALA-01"
    assert len(data["machines"]) == 2


def test_summary_filters_by_room(client: TestClient) -> None:
    config = client.app.state.config
    _write_monitoring_parquet(config.storage.base_dir, room="SALA-01")
    _write_monitoring_parquet(config.storage.base_dir, room="SALA-02")

    response = client.get(
        "/v1/reports/summary?sala_codigo=SALA-02",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["salas"] == 1
    assert data["rooms"][0]["sala_codigo"] == "SALA-02"


def test_excel_returns_xlsx(client: TestClient) -> None:
    config = client.app.state.config
    _write_monitoring_parquet(config.storage.base_dir)

    response = client.get("/v1/reports/excel", headers=_auth_headers())

    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    assert response.content[:2] == b"PK"


def test_dashboard_shows_login_when_unauthenticated(client: TestClient) -> None:
    response = client.get("/v1/dashboard", follow_redirects=False)
    assert response.status_code == 200
    assert "Ingresar" in response.text


def test_dashboard_accessible_after_login(client: TestClient) -> None:
    _login(client)
    response = client.get("/v1/dashboard")
    assert response.status_code == 200
    assert "Dashboard" in response.text
