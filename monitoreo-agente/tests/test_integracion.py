"""
Integration tests for the full monitoring agent pipeline.

Tests exercise the complete storage → consolidation → send chain without
mocking internal modules. Network calls are bypassed via MOCK_API=1.
Subprocess tests run the real entry-point scripts against a temp config,
verifying that the whole component stack works together.

Coverage:
  1. guardar_muestra → JSONL correctness
  2. consolidar_a_parquet → Parquet schema and row count
  3. procesar_pendientes (MOCK_API) → file moves, stats
  4. Orphan detection and retry via agente_retry logic
  5. limpiar_antiguos → retention policy
  6. Subprocess: agente_captura.py exit code + JSONL written
  7. Subprocess: agente_envio.py exit code + file moved to enviados/
  8. Subprocess: agente_retry.py exit code + orphan consolidated

Author: Daniel Perez
"""

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from agente.almacenamiento import (
    consolidar_a_parquet,
    guardar_muestra,
    limpiar_antiguos,
    mover_a_enviados,
)
from agente.envio import procesar_pendientes

# ── constants ─────────────────────────────────────────────────────────────────

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)

# ── shared helpers ─────────────────────────────────────────────────────────────


def _make_config(tmp_path: Path) -> Path:
    """Write a minimal config.json to tmp_path and return its path."""
    cfg = {
        "api_url": "http://localhost:9999/v1/upload",
        "token": "INTEGRATION-TOKEN",
        "sala_codigo": "SALA-INT",
        "datos_dir": str(tmp_path / "data"),
        "log_dir": str(tmp_path / "logs"),
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def _make_sample(
    hostname: str = "PC-INT",
    usuario: str = "estudiante",
    ts: str | None = None,
) -> dict:
    return {
        "hostname": hostname,
        "usuario": usuario,
        "timestamp_utc": ts or f"{TODAY.isoformat()}T12:00:00+00:00",
        "cantidad_apps": 1,
        "apps": [
            {
                "nombre_proceso": "notepad",
                "nombre_ejecutable": "notepad.exe",
                "ruta_ejecutable": "C:/Windows/System32/notepad.exe",
                "titulo_ventana": "Untitled - Notepad",
                "pid": 1234,
                "memoria_mb": 4.5,
            }
        ],
    }


def _cfg_dict(datos_dir: Path) -> dict:
    return {
        "api_url": "http://localhost:9999/v1/upload",
        "token": "INT-TOKEN",
        "sala_codigo": "SALA-INT",
        "timeout_envio_seg": 10,
        "reintentos_envio": 0,
        "datos_dir": str(datos_dir),
    }


def _run_script(script: str, config_path: Path) -> subprocess.CompletedProcess:
    """Run an entry-point script with the current Python and a temp config."""
    env = {
        **os.environ,
        "MOCK_API": "1",
        "MONITOREO_CONFIG": str(config_path),
    }
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script)],
        env=env,
        capture_output=True,
        text=True,
    )


def _script_fail_msg(name: str, result: subprocess.CompletedProcess) -> str:
    return (
        f"{name} exited {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


# ── 1. guardar_muestra → JSONL ─────────────────────────────────────────────────


class TestGuardarMuestra:
    def test_creates_jsonl_file(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)
        jsonl = datos_dir / "raw" / f"{TODAY.isoformat()}.jsonl"
        assert jsonl.exists()

    def test_jsonl_contains_valid_json(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)
        jsonl = datos_dir / "raw" / f"{TODAY.isoformat()}.jsonl"
        rows = [json.loads(line) for line in jsonl.read_text("utf-8").splitlines() if line]
        assert len(rows) == 1
        assert rows[0]["hostname"] == "PC-INT"

    def test_multiple_samples_appended(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        for _ in range(3):
            guardar_muestra(_make_sample(), datos_dir)
        jsonl = datos_dir / "raw" / f"{TODAY.isoformat()}.jsonl"
        lines = [l for l in jsonl.read_text("utf-8").splitlines() if l]
        assert len(lines) == 3

    def test_jsonl_has_required_keys(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)
        jsonl = datos_dir / "raw" / f"{TODAY.isoformat()}.jsonl"
        row = json.loads(jsonl.read_text("utf-8").strip())
        for key in ("hostname", "usuario", "timestamp_utc", "apps"):
            assert key in row, f"Missing key: {key}"


# ── 2. consolidar_a_parquet ────────────────────────────────────────────────────


class TestConsolidarAParquet:
    def test_creates_parquet_file(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)
        parquet = consolidar_a_parquet(TODAY, datos_dir)
        assert parquet is not None
        assert parquet.exists()
        assert parquet.suffix == ".parquet"

    def test_parquet_in_pendientes_dir(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)
        parquet = consolidar_a_parquet(TODAY, datos_dir)
        assert "pendientes" in str(parquet)

    def test_parquet_schema_has_all_columns(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)
        parquet = consolidar_a_parquet(TODAY, datos_dir)
        table = pq.read_table(parquet)
        expected = {
            "hostname", "usuario", "timestamp_utc", "nombre_proceso",
            "nombre_ejecutable", "ruta_ejecutable", "titulo_ventana",
            "pid", "memoria_mb",
        }
        assert set(table.schema.names) == expected

    def test_one_row_per_app(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        # 2 samples × 1 app = 2 rows
        guardar_muestra(_make_sample(), datos_dir)
        guardar_muestra(_make_sample(), datos_dir)
        parquet = consolidar_a_parquet(TODAY, datos_dir)
        table = pq.read_table(parquet)
        assert table.num_rows == 2

    def test_idle_sample_produces_null_app_row(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        idle = {**_make_sample(), "cantidad_apps": 0, "apps": []}
        guardar_muestra(idle, datos_dir)
        parquet = consolidar_a_parquet(TODAY, datos_dir)
        table = pq.read_table(parquet)
        assert table.num_rows == 1
        assert table.column("nombre_proceso")[0].as_py() is None

    def test_parquet_data_values_correct(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)
        parquet = consolidar_a_parquet(TODAY, datos_dir)
        table = pq.read_table(parquet)
        assert table.column("hostname")[0].as_py() == "PC-INT"
        assert table.column("nombre_proceso")[0].as_py() == "notepad"

    def test_returns_none_for_missing_jsonl(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        result = consolidar_a_parquet(TODAY, datos_dir)
        assert result is None

    def test_filename_contains_date_and_hostname(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(hostname="PC-TEST"), datos_dir)
        parquet = consolidar_a_parquet(TODAY, datos_dir)
        assert TODAY.isoformat() in parquet.name
        assert "PC-TEST" in parquet.name


# ── 3. procesar_pendientes (MOCK_API) ─────────────────────────────────────────


class TestProcesarPendientesIntegracion:
    def test_full_pipeline_returns_correct_stats(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MOCK_API", "1")
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)
        consolidar_a_parquet(TODAY, datos_dir)

        stats = procesar_pendientes(datos_dir, _cfg_dict(datos_dir))
        assert stats == {"enviados": 1, "fallidos": 0, "total": 1}

    def test_sent_file_removed_from_pendientes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MOCK_API", "1")
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)
        consolidar_a_parquet(TODAY, datos_dir)
        procesar_pendientes(datos_dir, _cfg_dict(datos_dir))
        assert list((datos_dir / "pendientes").glob("*.parquet")) == []

    def test_sent_file_appears_in_enviados(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MOCK_API", "1")
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)
        consolidar_a_parquet(TODAY, datos_dir)
        procesar_pendientes(datos_dir, _cfg_dict(datos_dir))
        sent = list((datos_dir / "enviados").rglob("*.parquet"))
        assert len(sent) == 1

    def test_sent_file_lands_in_year_month_subdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MOCK_API", "1")
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)
        consolidar_a_parquet(TODAY, datos_dir)
        procesar_pendientes(datos_dir, _cfg_dict(datos_dir))
        expected = datos_dir / "enviados" / f"{TODAY.year:04d}" / f"{TODAY.month:02d}"
        assert expected.exists()
        assert len(list(expected.glob("*.parquet"))) == 1

    def test_sent_parquet_is_still_readable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MOCK_API", "1")
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)
        consolidar_a_parquet(TODAY, datos_dir)
        procesar_pendientes(datos_dir, _cfg_dict(datos_dir))
        sent = list((datos_dir / "enviados").rglob("*.parquet"))[0]
        table = pq.read_table(sent)
        assert table.num_rows >= 1
        assert table.column("hostname")[0].as_py() == "PC-INT"


# ── 4. orphan retry logic ─────────────────────────────────────────────────────


class TestOrphanRetry:
    def _write_orphan_jsonl(self, datos_dir: Path, day: date) -> Path:
        """Write a JSONL for a past day that has no matching Parquet anywhere."""
        sample = _make_sample(ts=f"{day.isoformat()}T21:00:00+00:00")
        raw = datos_dir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        jsonl = raw / f"{day.isoformat()}.jsonl"
        jsonl.write_text(json.dumps(sample) + "\n", encoding="utf-8")
        return jsonl

    def test_orphan_consolidates_to_pendientes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MOCK_API", "1")
        datos_dir = tmp_path / "data"
        self._write_orphan_jsonl(datos_dir, YESTERDAY)
        parquet = consolidar_a_parquet(YESTERDAY, datos_dir)
        assert parquet is not None
        assert parquet.exists()
        assert YESTERDAY.isoformat() in parquet.name

    def test_orphan_send_moves_to_enviados(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MOCK_API", "1")
        datos_dir = tmp_path / "data"
        self._write_orphan_jsonl(datos_dir, YESTERDAY)
        consolidar_a_parquet(YESTERDAY, datos_dir)
        stats = procesar_pendientes(datos_dir, _cfg_dict(datos_dir))
        assert stats["enviados"] == 1
        assert len(list((datos_dir / "enviados").rglob("*.parquet"))) == 1

    def test_today_jsonl_not_consolidated_by_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MOCK_API", "1")
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)          # today's (must stay)
        self._write_orphan_jsonl(datos_dir, YESTERDAY)       # orphan

        # Simulate retry: only consolidate yesterday
        consolidar_a_parquet(YESTERDAY, datos_dir)
        procesar_pendientes(datos_dir, _cfg_dict(datos_dir))

        # Today's JSONL still present and not turned into Parquet
        assert (datos_dir / "raw" / f"{TODAY.isoformat()}.jsonl").exists()
        assert list((datos_dir / "pendientes").glob(f"{TODAY.isoformat()}*.parquet")) == []

    def test_multiple_orphans_all_sent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MOCK_API", "1")
        datos_dir = tmp_path / "data"
        day1 = TODAY - timedelta(days=2)
        day2 = TODAY - timedelta(days=3)
        self._write_orphan_jsonl(datos_dir, day1)
        self._write_orphan_jsonl(datos_dir, day2)
        consolidar_a_parquet(day1, datos_dir)
        consolidar_a_parquet(day2, datos_dir)
        stats = procesar_pendientes(datos_dir, _cfg_dict(datos_dir))
        assert stats["enviados"] == 2
        assert stats["fallidos"] == 0


# ── 5. limpiar_antiguos ───────────────────────────────────────────────────────


class TestLimpiarAntiguos:
    def test_deletes_files_beyond_retention(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        old = TODAY - timedelta(days=10)
        raw = datos_dir / "raw"
        raw.mkdir(parents=True)
        f = raw / f"{old.isoformat()}.jsonl"
        f.write_text("x", encoding="utf-8")
        deleted = limpiar_antiguos(datos_dir, 7)
        assert deleted == 1
        assert not f.exists()

    def test_keeps_recent_files(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        recent = TODAY - timedelta(days=3)
        raw = datos_dir / "raw"
        raw.mkdir(parents=True)
        f = raw / f"{recent.isoformat()}.jsonl"
        f.write_text("x", encoding="utf-8")
        deleted = limpiar_antiguos(datos_dir, 7)
        assert deleted == 0
        assert f.exists()

    def test_covers_raw_and_pendientes(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        old = TODAY - timedelta(days=10)
        for subdir in ("raw", "pendientes"):
            d = datos_dir / subdir
            d.mkdir(parents=True)
            (d / f"{old.isoformat()}.jsonl").write_text("x", encoding="utf-8")
        deleted = limpiar_antiguos(datos_dir, 7)
        assert deleted == 2

    def test_covers_enviados_subdirs(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        old = TODAY - timedelta(days=10)
        env_dir = datos_dir / "enviados" / f"{old.year:04d}" / f"{old.month:02d}"
        env_dir.mkdir(parents=True)
        f = env_dir / f"{old.isoformat()}_PC.parquet"
        f.write_bytes(b"fake")
        deleted = limpiar_antiguos(datos_dir, 7)
        assert deleted == 1
        assert not f.exists()


# ── 6. subprocess: agente_captura.py ─────────────────────────────────────────


class TestScriptCaptura:
    def test_exits_zero(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        result = _run_script("agente_captura.py", cfg)
        assert result.returncode == 0, _script_fail_msg("agente_captura.py", result)

    def test_creates_jsonl(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        _run_script("agente_captura.py", cfg)
        raw_dir = tmp_path / "data" / "raw"
        assert raw_dir.exists(), "raw/ directory was not created"
        jsonl_files = list(raw_dir.glob("*.jsonl"))
        assert len(jsonl_files) == 1, f"Expected 1 JSONL, found: {jsonl_files}"

    def test_jsonl_has_correct_schema(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        _run_script("agente_captura.py", cfg)
        jsonl = next((tmp_path / "data" / "raw").glob("*.jsonl"))
        sample = json.loads(jsonl.read_text("utf-8").strip())
        for key in ("hostname", "usuario", "timestamp_utc", "apps", "cantidad_apps"):
            assert key in sample, f"Missing key '{key}' in captured sample"

    def test_jsonl_apps_is_list(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        _run_script("agente_captura.py", cfg)
        jsonl = next((tmp_path / "data" / "raw").glob("*.jsonl"))
        sample = json.loads(jsonl.read_text("utf-8").strip())
        assert isinstance(sample["apps"], list)

    def test_repeated_runs_append_lines(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        _run_script("agente_captura.py", cfg)
        _run_script("agente_captura.py", cfg)
        jsonl = next((tmp_path / "data" / "raw").glob("*.jsonl"))
        lines = [l for l in jsonl.read_text("utf-8").splitlines() if l]
        assert len(lines) == 2


# ── 7. subprocess: agente_envio.py ───────────────────────────────────────────


class TestScriptEnvio:
    def test_exits_zero_with_data(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)
        cfg = _make_config(tmp_path)
        result = _run_script("agente_envio.py", cfg)
        assert result.returncode == 0, _script_fail_msg("agente_envio.py", result)

    def test_exits_zero_with_no_data(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        result = _run_script("agente_envio.py", cfg)
        assert result.returncode == 0, _script_fail_msg("agente_envio.py", result)

    def test_moves_parquet_to_enviados(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)
        cfg = _make_config(tmp_path)
        _run_script("agente_envio.py", cfg)
        sent = list((datos_dir / "enviados").rglob("*.parquet"))
        assert len(sent) == 1

    def test_parquet_nothing_left_in_pendientes(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)
        cfg = _make_config(tmp_path)
        _run_script("agente_envio.py", cfg)
        pending = list((datos_dir / "pendientes").glob("*.parquet"))
        assert pending == []


# ── 8. subprocess: agente_retry.py ───────────────────────────────────────────


class TestScriptRetry:
    def _write_orphan(self, datos_dir: Path, day: date) -> None:
        sample = _make_sample(ts=f"{day.isoformat()}T21:00:00+00:00")
        raw = datos_dir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / f"{day.isoformat()}.jsonl").write_text(
            json.dumps(sample) + "\n", encoding="utf-8"
        )

    def test_exits_zero_no_orphans(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        result = _run_script("agente_retry.py", cfg)
        assert result.returncode == 0, _script_fail_msg("agente_retry.py", result)

    def test_exits_zero_with_orphan(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        self._write_orphan(datos_dir, YESTERDAY)
        cfg = _make_config(tmp_path)
        result = _run_script("agente_retry.py", cfg)
        assert result.returncode == 0, _script_fail_msg("agente_retry.py", result)

    def test_orphan_sent_to_enviados(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        self._write_orphan(datos_dir, YESTERDAY)
        cfg = _make_config(tmp_path)
        _run_script("agente_retry.py", cfg)
        sent = list((datos_dir / "enviados").rglob("*.parquet"))
        assert len(sent) == 1, f"Expected 1 sent file, found {len(sent)}"

    def test_today_jsonl_untouched(self, tmp_path: Path) -> None:
        datos_dir = tmp_path / "data"
        guardar_muestra(_make_sample(), datos_dir)       # today's JSONL
        self._write_orphan(datos_dir, YESTERDAY)          # orphan
        cfg = _make_config(tmp_path)
        _run_script("agente_retry.py", cfg)
        # Today's raw JSONL must still exist and must NOT be in pendientes
        assert (datos_dir / "raw" / f"{TODAY.isoformat()}.jsonl").exists()
        today_parquets = list(
            (datos_dir / "pendientes").glob(f"{TODAY.isoformat()}*.parquet")
        )
        assert today_parquets == []
