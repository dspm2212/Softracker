"""Shared pytest fixtures for monitoreo-servidor tests.

Author: Daniel Perez
"""

from __future__ import annotations

import io
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from app.auth import hash_token

# ── Credentials used across upload/status tests ──────────────────────────────
TEST_SALA = "SALA-01"
TEST_TOKEN = "test-token-sala-01-abc123"
TEST_ADMIN_TOKEN = "test-admin-token-xyz789"


# ── Low-level config fixture (no HTTP client) ─────────────────────────────────

@pytest.fixture
def config_yaml(tmp_path: Path) -> Path:
    """Write a minimal valid config.yaml and tokens.yaml to a temp directory."""
    (tmp_path / "tokens.yaml").write_text("rooms: {}\n", encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """\
server:
  host: "0.0.0.0"
  port: 8080
  max_upload_mb: 50

storage:
  base_dir: "./data"
  parquet_subdir: "parquet"
  audit_log_file: "log/ingest.log"
  reject_dir: "rejected"

auth:
  tokens_file: "tokens.yaml"
  max_clock_skew_min: 30
  admin_token_hash: "deadbeef00112233445566778899aabbccddeeff00112233445566778899aabb"

logging:
  level: "INFO"
  file: "./logs/api.log"
  rotate_mb: 50
  backups: 5
""",
        encoding="utf-8",
    )
    return cfg


# ── HTTP client fixture ───────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient wired to a fully configured in-memory app instance.

    Uses max_upload_mb=1 so size-limit tests only need ~1 MB payloads.
    """
    tokens_file = tmp_path / "tokens.yaml"
    tokens_file.write_text(
        f"""\
rooms:
  {TEST_SALA}:
    active_hash: "{hash_token(TEST_TOKEN)}"
    active_since: "2026-01-01T00:00:00Z"
    previous_hash: null
    previous_until: null
""",
        encoding="utf-8",
    )

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""\
server:
  host: "0.0.0.0"
  port: 8080
  max_upload_mb: 1

storage:
  base_dir: "{(tmp_path / 'data').as_posix()}"
  parquet_subdir: "parquet"
  audit_log_file: "log/ingest.log"
  reject_dir: "rejected"

auth:
  tokens_file: "{tokens_file.as_posix()}"
  max_clock_skew_min: 30
  admin_token_hash: "{hash_token(TEST_ADMIN_TOKEN)}"

logging:
  level: "WARNING"
  file: "{(tmp_path / 'logs' / 'api.log').as_posix()}"
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
