"""Shared pytest fixtures for monitoreo-report tests.

Author: Daniel Perez
"""

from __future__ import annotations

import io
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password

# ── Test credentials ──────────────────────────────────────────────────────────
TEST_USERNAME = "admin"
TEST_PASSWORD = "test-password-123"
TEST_SALA     = "SALA-01"


# ── Config fixture ────────────────────────────────────────────────────────────

@pytest.fixture
def config_yaml(tmp_path: Path) -> Path:
    """Write a minimal valid config.yaml to a temp directory."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""\
server:
  host: "0.0.0.0"
  port: 8081
  max_upload_mb: 50

storage:
  base_dir: "./data"
  parquet_subdir: "parquet"
  audit_log_file: "log/ingest.log"
  reject_dir: "rejected"

credentials:
  username: "{TEST_USERNAME}"
  password_hash: "{hash_password(TEST_PASSWORD)}"

logging:
  level: "INFO"
  file: "./logs/report.log"
  rotate_mb: 50
  backups: 5
""",
        encoding="utf-8",
    )
    return cfg


# ── HTTP client fixture ───────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient wired to a fully configured in-memory app instance."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""\
server:
  host: "0.0.0.0"
  port: 8081
  max_upload_mb: 50

storage:
  base_dir: "{(tmp_path / 'data').as_posix()}"
  parquet_subdir: "parquet"
  audit_log_file: "log/ingest.log"
  reject_dir: "rejected"

credentials:
  username: "{TEST_USERNAME}"
  password_hash: "{hash_password(TEST_PASSWORD)}"

logging:
  level: "WARNING"
  file: "{(tmp_path / 'logs' / 'report.log').as_posix()}"
  rotate_mb: 5
  backups: 1
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("MONITOREO_CONFIG", str(config_file))

    from app.main import app
    with TestClient(app) as c:
        yield c


# ── Parquet helper ────────────────────────────────────────────────────────────

@pytest.fixture
def parquet_bytes() -> bytes:
    """Return bytes of a minimal valid snappy-compressed Parquet file."""
    table = pa.table({
        "hostname": pa.array(["PC01"]),
        "usuario": pa.array(["estudiante"]),
        "nombre_proceso": pa.array(["Code"]),
    })
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()
