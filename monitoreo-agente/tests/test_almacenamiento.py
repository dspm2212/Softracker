"""
Tests for agente.almacenamiento module.

Covers: JSONL append, Parquet consolidation (schema, rows, compression),
file rotation, pending listing, and retention-based cleanup.

Author: Daniel Perez
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from agente.almacenamiento import (
    consolidar_a_parquet,
    guardar_muestra,
    limpiar_antiguos,
    listar_pendientes,
    mover_a_enviados,
)

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


# ── test fixtures / helpers ───────────────────────────────────────────────────


def _sample(
    hostname: str = "PC-01",
    usuario: str = "estudiante",
    ts: str = "2026-05-27T18:00:00+00:00",
    apps: list | None = None,
) -> dict:
    """Build a minimal monitoring snapshot dict."""
    if apps is None:
        apps = [_app()]
    return {
        "hostname": hostname,
        "usuario": usuario,
        "timestamp_utc": ts,
        "cantidad_apps": len(apps),
        "apps": apps,
    }


def _app(
    nombre: str = "Code",
    exe: str = "Code.exe",
    ruta: str | None = "C:\\Code.exe",
    titulo: str = "VS Code",
    pid: int = 1234,
    mb: float = 100.0,
) -> dict:
    """Build a minimal app entry dict."""
    return {
        "nombre_proceso": nombre,
        "nombre_ejecutable": exe,
        "ruta_ejecutable": ruta,
        "titulo_ventana": titulo,
        "pid": pid,
        "memoria_mb": mb,
    }


def _write_jsonl(path: Path, samples: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for s in samples:
            fh.write(json.dumps(s) + "\n")


def _jsonl_path(datos_dir: Path, d: date) -> Path:
    return datos_dir / "raw" / f"{d.isoformat()}.jsonl"


# ── guardar_muestra ───────────────────────────────────────────────────────────


class TestGuardarMuestra:
    def test_creates_file_in_raw_dir(self, tmp_path: Path) -> None:
        guardar_muestra(_sample(), tmp_path)
        assert _jsonl_path(tmp_path, TODAY).exists()

    def test_creates_raw_directory_if_missing(self, tmp_path: Path) -> None:
        guardar_muestra(_sample(), tmp_path)
        assert (tmp_path / "raw").is_dir()

    def test_each_call_appends_new_line(self, tmp_path: Path) -> None:
        for _ in range(4):
            guardar_muestra(_sample(), tmp_path)
        lines = [
            l for l in _jsonl_path(tmp_path, TODAY).read_text(encoding="utf-8").splitlines() if l
        ]
        assert len(lines) == 4

    def test_each_line_is_valid_json(self, tmp_path: Path) -> None:
        guardar_muestra(_sample(hostname="PC-X"), tmp_path)
        guardar_muestra(_sample(hostname="PC-Y"), tmp_path)
        lines = [
            l for l in _jsonl_path(tmp_path, TODAY).read_text(encoding="utf-8").splitlines() if l
        ]
        hostnames = [json.loads(l)["hostname"] for l in lines]
        assert hostnames == ["PC-X", "PC-Y"]


# ── consolidar_a_parquet ──────────────────────────────────────────────────────


class TestConsolidarAParquet:
    def test_returns_none_for_missing_jsonl(self, tmp_path: Path) -> None:
        assert consolidar_a_parquet(YESTERDAY, tmp_path) is None

    def test_returns_none_for_empty_jsonl(self, tmp_path: Path) -> None:
        p = _jsonl_path(tmp_path, YESTERDAY)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
        assert consolidar_a_parquet(YESTERDAY, tmp_path) is None

    def test_returns_path_on_success(self, tmp_path: Path) -> None:
        _write_jsonl(_jsonl_path(tmp_path, YESTERDAY), [_sample()])
        result = consolidar_a_parquet(YESTERDAY, tmp_path)
        assert result is not None
        assert result.exists()

    def test_output_is_inside_pendientes(self, tmp_path: Path) -> None:
        _write_jsonl(_jsonl_path(tmp_path, YESTERDAY), [_sample()])
        result = consolidar_a_parquet(YESTERDAY, tmp_path)
        assert result.parent == tmp_path / "pendientes"

    def test_filename_contains_date_and_hostname(self, tmp_path: Path) -> None:
        _write_jsonl(_jsonl_path(tmp_path, YESTERDAY), [_sample(hostname="SALA01-PC05")])
        result = consolidar_a_parquet(YESTERDAY, tmp_path)
        assert YESTERDAY.isoformat() in result.name
        assert "SALA01-PC05" in result.name

    def test_schema_has_expected_columns(self, tmp_path: Path) -> None:
        _write_jsonl(_jsonl_path(tmp_path, YESTERDAY), [_sample()])
        result = consolidar_a_parquet(YESTERDAY, tmp_path)
        cols = set(pq.read_table(result).schema.names)
        assert cols == {
            "hostname", "usuario", "timestamp_utc",
            "nombre_proceso", "nombre_ejecutable", "ruta_ejecutable",
            "titulo_ventana", "pid", "memoria_mb",
        }

    def test_pid_column_is_int32(self, tmp_path: Path) -> None:
        _write_jsonl(_jsonl_path(tmp_path, YESTERDAY), [_sample()])
        result = consolidar_a_parquet(YESTERDAY, tmp_path)
        assert pq.read_table(result).schema.field("pid").type == pa.int32()

    def test_memoria_column_is_float32(self, tmp_path: Path) -> None:
        _write_jsonl(_jsonl_path(tmp_path, YESTERDAY), [_sample()])
        result = consolidar_a_parquet(YESTERDAY, tmp_path)
        assert pq.read_table(result).schema.field("memoria_mb").type == pa.float32()

    def test_one_row_per_app(self, tmp_path: Path) -> None:
        sample = _sample(apps=[_app("A"), _app("B"), _app("C")])
        _write_jsonl(_jsonl_path(tmp_path, YESTERDAY), [sample])
        result = consolidar_a_parquet(YESTERDAY, tmp_path)
        assert len(pq.read_table(result)) == 3

    def test_empty_apps_produces_one_null_row(self, tmp_path: Path) -> None:
        _write_jsonl(_jsonl_path(tmp_path, YESTERDAY), [_sample(apps=[])])
        result = consolidar_a_parquet(YESTERDAY, tmp_path)
        table = pq.read_table(result)
        assert len(table) == 1
        assert table["nombre_proceso"][0].as_py() is None
        assert table["hostname"][0].as_py() == "PC-01"

    def test_multiple_samples_flattened_correctly(self, tmp_path: Path) -> None:
        # s1: 2 apps, s2: idle (0 apps → 1 null row), s3: 1 app → total 4 rows
        s1 = _sample(apps=[_app("A"), _app("B")])
        s2 = _sample(apps=[])
        s3 = _sample(apps=[_app("C")])
        _write_jsonl(_jsonl_path(tmp_path, YESTERDAY), [s1, s2, s3])
        result = consolidar_a_parquet(YESTERDAY, tmp_path)
        assert len(pq.read_table(result)) == 4

    def test_uses_snappy_compression(self, tmp_path: Path) -> None:
        _write_jsonl(_jsonl_path(tmp_path, YESTERDAY), [_sample()])
        result = consolidar_a_parquet(YESTERDAY, tmp_path)
        meta = pq.read_metadata(result)
        for rg_idx in range(meta.num_row_groups):
            rg = meta.row_group(rg_idx)
            for col_idx in range(rg.num_columns):
                assert rg.column(col_idx).compression == "SNAPPY"

    def test_null_ruta_ejecutable_allowed(self, tmp_path: Path) -> None:
        _write_jsonl(
            _jsonl_path(tmp_path, YESTERDAY),
            [_sample(apps=[_app(ruta=None)])],
        )
        result = consolidar_a_parquet(YESTERDAY, tmp_path)
        table = pq.read_table(result)
        assert table["ruta_ejecutable"][0].as_py() is None

    def test_idempotent_overwrites_existing(self, tmp_path: Path) -> None:
        _write_jsonl(_jsonl_path(tmp_path, YESTERDAY), [_sample(apps=[_app("A")])])
        first = consolidar_a_parquet(YESTERDAY, tmp_path)
        _write_jsonl(_jsonl_path(tmp_path, YESTERDAY), [_sample(apps=[_app("A"), _app("B")])])
        second = consolidar_a_parquet(YESTERDAY, tmp_path)
        assert first == second
        assert len(pq.read_table(second)) == 2


# ── mover_a_enviados ──────────────────────────────────────────────────────────


class TestMoverAEnviados:
    def _make_pending(self, tmp_path: Path, d: date = YESTERDAY) -> Path:
        src = tmp_path / "pendientes" / f"{d.isoformat()}_PC-01.parquet"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"fake-parquet")
        return src

    def test_file_lands_in_correct_subdir(self, tmp_path: Path) -> None:
        src = self._make_pending(tmp_path)
        result = mover_a_enviados(src, tmp_path)
        expected = (
            tmp_path / "enviados"
            / f"{YESTERDAY.year:04d}"
            / f"{YESTERDAY.month:02d}"
            / src.name
        )
        assert result == expected

    def test_destination_file_exists(self, tmp_path: Path) -> None:
        src = self._make_pending(tmp_path)
        result = mover_a_enviados(src, tmp_path)
        assert result.exists()

    def test_source_is_gone_after_move(self, tmp_path: Path) -> None:
        src = self._make_pending(tmp_path)
        mover_a_enviados(src, tmp_path)
        assert not src.exists()

    def test_destination_directory_auto_created(self, tmp_path: Path) -> None:
        src = self._make_pending(tmp_path)
        result = mover_a_enviados(src, tmp_path)
        assert result.parent.is_dir()


# ── listar_pendientes ─────────────────────────────────────────────────────────


class TestListarPendientes:
    def test_empty_when_dir_missing(self, tmp_path: Path) -> None:
        assert listar_pendientes(tmp_path) == []

    def test_returns_only_parquet_files(self, tmp_path: Path) -> None:
        pending = tmp_path / "pendientes"
        pending.mkdir()
        (pending / "2026-05-25_PC-01.parquet").write_bytes(b"x")
        (pending / "notes.txt").write_text("x")
        result = listar_pendientes(tmp_path)
        assert len(result) == 1
        assert result[0].suffix == ".parquet"

    def test_sorted_chronologically(self, tmp_path: Path) -> None:
        pending = tmp_path / "pendientes"
        pending.mkdir()
        d1 = pending / "2026-05-25_PC-01.parquet"
        d2 = pending / "2026-05-27_PC-01.parquet"
        d3 = pending / "2026-05-26_PC-01.parquet"
        for p in (d2, d3, d1):  # create in non-order
            p.write_bytes(b"x")
        assert listar_pendientes(tmp_path) == [d1, d3, d2]

    def test_empty_when_no_parquet_files(self, tmp_path: Path) -> None:
        (tmp_path / "pendientes").mkdir()
        assert listar_pendientes(tmp_path) == []





class TestLimpiarAntiguos:
    _OLD = (TODAY - timedelta(days=10)).isoformat()
    _RECENT = (TODAY - timedelta(days=2)).isoformat()
    _RETENTION = 7

    def _setup(self, datos_dir: Path) -> dict[str, Path]:
        files = {
            "old_raw": datos_dir / "raw" / f"{self._OLD}.jsonl",
            "recent_raw": datos_dir / "raw" / f"{self._RECENT}.jsonl",
            "old_pending": datos_dir / "pendientes" / f"{self._OLD}_PC.parquet",
            "recent_pending": datos_dir / "pendientes" / f"{self._RECENT}_PC.parquet",
            "old_sent": datos_dir / "enviados" / "2026" / "01" / f"{self._OLD}_PC.parquet",
        }
        for path in files.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        return files

    def test_deletes_old_raw_file(self, tmp_path: Path) -> None:
        files = self._setup(tmp_path)
        limpiar_antiguos(tmp_path, self._RETENTION)
        assert not files["old_raw"].exists()

    def test_deletes_old_pending_file(self, tmp_path: Path) -> None:
        files = self._setup(tmp_path)
        limpiar_antiguos(tmp_path, self._RETENTION)
        assert not files["old_pending"].exists()

    def test_deletes_old_sent_file(self, tmp_path: Path) -> None:
        files = self._setup(tmp_path)
        limpiar_antiguos(tmp_path, self._RETENTION)
        assert not files["old_sent"].exists()

    def test_keeps_recent_files(self, tmp_path: Path) -> None:
        files = self._setup(tmp_path)
        limpiar_antiguos(tmp_path, self._RETENTION)
        assert files["recent_raw"].exists()
        assert files["recent_pending"].exists()

    def test_returns_count_of_deleted_files(self, tmp_path: Path) -> None:
        self._setup(tmp_path)
        count = limpiar_antiguos(tmp_path, self._RETENTION)
        assert count == 3  # old_raw + old_pending + old_sent

    def test_returns_zero_when_nothing_to_delete(self, tmp_path: Path) -> None:
        recent = (TODAY - timedelta(days=1)).isoformat()
        p = tmp_path / "raw" / f"{recent}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        assert limpiar_antiguos(tmp_path, self._RETENTION) == 0

    def test_handles_missing_subdirs_gracefully(self, tmp_path: Path) -> None:
        assert limpiar_antiguos(tmp_path, self._RETENTION) == 0
