"""
Local storage management for the monitoring agent.

Handles daily JSONL append, Parquet consolidation, file rotation between
pending/sent folders, and retention-based cleanup.

Author: Daniel Perez
"""

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

_log = logging.getLogger(__name__)

_PARQUET_SCHEMA = pa.schema([
    pa.field("hostname", pa.string()),
    pa.field("usuario", pa.string()),
    pa.field("timestamp_utc", pa.timestamp("us", tz="UTC")),
    pa.field("nombre_proceso", pa.string()),
    pa.field("nombre_ejecutable", pa.string()),
    pa.field("ruta_ejecutable", pa.string()),
    pa.field("titulo_ventana", pa.string()),
    pa.field("pid", pa.int32()),
    pa.field("memoria_mb", pa.float32()),
])


def guardar_muestra(muestra: dict, datos_dir: Path) -> None:
    """Append one monitoring snapshot as a JSONL line for today.

    Args:
        muestra: Snapshot dict produced by construir_muestra().
        datos_dir: Root data directory. The file is written to
            datos_dir/raw/YYYY-MM-DD.jsonl using the current local date.
    """
    raw_dir = datos_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = raw_dir / f"{date.today().isoformat()}.jsonl"
    with jsonl_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(muestra, ensure_ascii=False) + "\n")
        fh.flush()


def consolidar_a_parquet(fecha: date, datos_dir: Path) -> Path | None:
    """Consolidate a daily JSONL file into a flat Parquet file.

    Reads datos_dir/raw/YYYY-MM-DD.jsonl and produces a flat table with
    one row per (sample, app). Samples with no apps generate one row with
    app fields as null so that idle periods are not lost.

    Reusable: called by agente_envio.py (today) and agente_retry.py
    (orphan dates from previous days).

    Args:
        fecha: The date whose JSONL file should be consolidated.
        datos_dir: Root data directory.

    Returns:
        Path to the generated Parquet file, or None if the JSONL
        file is missing or contains no valid samples.
    """
    jsonl_path = datos_dir / "raw" / f"{fecha.isoformat()}.jsonl"
    if not jsonl_path.exists():
        return None

    samples = _read_jsonl(jsonl_path)
    if not samples:
        return None

    rows: list[dict] = []
    for sample in samples:
        rows.extend(_expand_sample(sample))

    hostname = samples[0].get("hostname", "unknown")
    output_path = datos_dir / "pendientes" / f"{fecha.isoformat()}_{hostname}.parquet"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pq.write_table(_build_parquet_table(rows), output_path, compression="snappy")
    return output_path


def mover_a_enviados(archivo_parquet: Path, datos_dir: Path) -> Path:
    """Move a Parquet file from pendientes/ to enviados/YYYY/MM/.

    Args:
        archivo_parquet: Path of the file inside datos_dir/pendientes/.
        datos_dir: Root data directory.

    Returns:
        New path inside datos_dir/enviados/YYYY/MM/.
    """
    file_date = date.fromisoformat(archivo_parquet.stem[:10])
    dest_dir = (
        datos_dir / "enviados" / f"{file_date.year:04d}" / f"{file_date.month:02d}"
    )
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / archivo_parquet.name
    archivo_parquet.rename(dest)
    return dest


def listar_pendientes(datos_dir: Path) -> list[Path]:
    """Return .parquet files in pendientes/ sorted chronologically by filename.

    Args:
        datos_dir: Root data directory.

    Returns:
        Sorted list of Parquet paths; empty if the directory is missing.
    """
    pending_dir = datos_dir / "pendientes"
    if not pending_dir.exists():
        return []
    return sorted(pending_dir.glob("*.parquet"))


def limpiar_antiguos(datos_dir: Path, dias_retencion_local: int) -> int:
    """Delete files older than dias_retencion_local days from raw/, pendientes/, enviados/.

    Age is determined by the YYYY-MM-DD prefix in each filename, which is
    more reliable than mtime for files that may have been copied.

    Args:
        datos_dir: Root data directory.
        dias_retencion_local: Number of days to retain files.

    Returns:
        Number of files deleted.
    """
    cutoff = date.today() - timedelta(days=dias_retencion_local)
    deleted = 0
    for subdir in ("raw", "pendientes", "enviados"):
        target = datos_dir / subdir
        if not target.exists():
            continue
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            file_date = _parse_file_date(path)
            if file_date is not None and file_date < cutoff:
                path.unlink()
                deleted += 1
    return deleted

def _read_jsonl(path: Path) -> list[dict]:
    """Read all valid JSON lines from a JSONL file, skipping malformed ones."""
    samples: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                samples.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                _log.warning("Skipping malformed line %d in %s: %s", lineno, path, exc)
    return samples


def _expand_sample(sample: dict) -> list[dict]:
    """Flatten one sample into one row per app, or one null-app row if apps is empty."""
    base = {
        "hostname": sample.get("hostname"),
        "usuario": sample.get("usuario"),
        "timestamp_utc": sample.get("timestamp_utc"),
    }
    apps = sample.get("apps", [])
    if not apps:
        return [{
            **base,
            "nombre_proceso": None,
            "nombre_ejecutable": None,
            "ruta_ejecutable": None,
            "titulo_ventana": None,
            "pid": None,
            "memoria_mb": None,
        }]
    return [
        {
            **base,
            "nombre_proceso": app.get("nombre_proceso"),
            "nombre_ejecutable": app.get("nombre_ejecutable"),
            "ruta_ejecutable": app.get("ruta_ejecutable"),
            "titulo_ventana": app.get("titulo_ventana"),
            "pid": app.get("pid"),
            "memoria_mb": app.get("memoria_mb"),
        }
        for app in apps
    ]


def _build_parquet_table(rows: list[dict]) -> pa.Table:
    """Convert a list of flat row dicts into a typed PyArrow Table."""
    timestamps = [
        datetime.fromisoformat(r["timestamp_utc"])
        if isinstance(r["timestamp_utc"], str)
        else r["timestamp_utc"]
        for r in rows
    ]
    data: dict[str, pa.Array] = {
        "hostname": pa.array([r["hostname"] for r in rows], type=pa.string()),
        "usuario": pa.array([r["usuario"] for r in rows], type=pa.string()),
        "timestamp_utc": pa.array(timestamps, type=pa.timestamp("us", tz="UTC")),
        "nombre_proceso": pa.array([r["nombre_proceso"] for r in rows], type=pa.string()),
        "nombre_ejecutable": pa.array([r["nombre_ejecutable"] for r in rows], type=pa.string()),
        "ruta_ejecutable": pa.array([r["ruta_ejecutable"] for r in rows], type=pa.string()),
        "titulo_ventana": pa.array([r["titulo_ventana"] for r in rows], type=pa.string()),
        "pid": pa.array([r["pid"] for r in rows], type=pa.int32()),
        "memoria_mb": pa.array([r["memoria_mb"] for r in rows], type=pa.float32()),
    }
    return pa.table(data, schema=_PARQUET_SCHEMA)


def _parse_file_date(path: Path) -> date | None:
    """Extract a date from a filename stem starting with YYYY-MM-DD."""
    try:
        return date.fromisoformat(path.stem[:10])
    except ValueError:
        return None
