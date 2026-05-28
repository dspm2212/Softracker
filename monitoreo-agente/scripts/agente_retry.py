"""
Entry point: consolidate orphan JSONLs and retry all pending sends.

Designed to run at Windows session start (arranque.xml). Handles the case
where the machine was off or had no active session at 21:45.

Workflow:
    1. Scan raw/ for JSONL files dated before today that have no matching
       Parquet in pendientes/ or enviados/ (orphan detection).
    2. Consolidate each orphan JSONL → pendientes/ using the same
       consolidar_a_parquet() used by agente_envio.py.
    3. Send every Parquet in pendientes/ (including newly consolidated ones).

The current day's JSONL is never touched — it keeps accumulating captures.
A JSONL already reflected in enviados/ is not re-processed.

Set MOCK_API=1 to skip all HTTP calls during development.

Exit codes:
    0  all pending files sent (or nothing to send).
    1  one or more files failed to send, or a fatal error occurred.

Author: Daniel Perez
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agente.almacenamiento import consolidar_a_parquet
from agente.config import cargar_config
from agente.envio import procesar_pendientes
from agente.logger import configurar_logger


def _find_orphan_dates(datos_dir: Path) -> list[date]:
    """Return dates that have a JSONL in raw/ but no Parquet anywhere.

    A date is considered orphaned when its raw/YYYY-MM-DD.jsonl exists AND
    there is no matching YYYY-MM-DD_*.parquet in pendientes/ or enviados/.
    Today's date is always excluded.
    """
    raw_dir = datos_dir / "raw"
    if not raw_dir.exists():
        return []

    today = date.today()
    orphans: list[date] = []

    for jsonl_path in sorted(raw_dir.glob("*.jsonl")):
        try:
            file_date = date.fromisoformat(jsonl_path.stem)
        except ValueError:
            continue
        if file_date >= today:
            continue  # never touch today's file

        prefix = file_date.isoformat()
        has_pending = any((datos_dir / "pendientes").glob(f"{prefix}_*.parquet"))
        has_sent = any((datos_dir / "enviados").rglob(f"{prefix}_*.parquet"))

        if not has_pending and not has_sent:
            orphans.append(file_date)

    return orphans


def main() -> int:
    """Consolidate orphan JSONLs and retry all pending sends. Returns exit code."""
    try:
        cfg = cargar_config()
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR loading config: {exc}", file=sys.stderr)
        return 1

    datos_dir = Path(cfg["datos_dir"])
    log = configurar_logger("retry", Path(cfg["log_dir"]))
    log.info("Starting orphan consolidation and retry")

    try:
        # 1. Consolidate any orphan JSONL files from previous days
        orphan_dates = _find_orphan_dates(datos_dir)
        if orphan_dates:
            log.info(
                "Found %d orphan JSONL(s): %s",
                len(orphan_dates),
                [d.isoformat() for d in orphan_dates],
            )
            for orphan_date in orphan_dates:
                parquet = consolidar_a_parquet(orphan_date, datos_dir)
                if parquet:
                    log.info("Consolidated %s → %s", orphan_date, parquet.name)
                else:
                    log.warning("Skipped %s — empty or unreadable JSONL", orphan_date)
        else:
            log.info("No orphan JSONL files found")

        # 2. Send everything in pendientes/ (pre-existing + newly consolidated)
        stats = procesar_pendientes(datos_dir, cfg)
        log.info(
            "Send results — sent: %d  failed: %d  total: %d",
            stats["enviados"],
            stats["fallidos"],
            stats["total"],
        )

        return 0 if stats["fallidos"] == 0 else 1

    except Exception as exc:
        log.exception("Retry failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
