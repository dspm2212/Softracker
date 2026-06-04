"""
Entry point: consolidate today's captures and send all pending files.

Workflow:
    1. Consolidate datos_dir/raw/YYYY-MM-DD.jsonl → datos_dir/pendientes/
    2. Send every Parquet in pendientes/ to the API.
    3. Delete files older than dias_retencion_local days.

Scheduled daily at 21:45 via envio.xml (Task Scheduler).
Set MOCK_API=1 to skip all HTTP calls during development.

Exit codes:
    0  all pending files sent (or nothing pending).
    1  one or more files failed to send, or a fatal error occurred.

Author: Daniel Perez
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agente.almacenamiento import consolidar_a_parquet, limpiar_antiguos
from agente.config import cargar_config
from agente.envio import procesar_pendientes
from agente.logger import configurar_logger


def main() -> int:
    """Run the daily consolidation and send cycle. Returns exit code."""
    try:
        cfg = cargar_config()
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR loading config: {exc}", file=sys.stderr)
        return 1

    datos_dir = Path(cfg["datos_dir"])
    log = configurar_logger("envio", Path(cfg["log_dir"]))
    log.info("Starting daily send")

    try:
        # 1. Consolidate today's captures to Parquet
        parquet = consolidar_a_parquet(date.today(), datos_dir)
        if parquet:
            log.info("Consolidated today's data → %s", parquet.name)
        else:
            log.info("No captures to consolidate for today")

        # 2. Send everything in pendientes/ (including what was just consolidated)
        stats = procesar_pendientes(datos_dir, cfg)
        log.info(
            "Send results — sent: %d  failed: %d  total: %d",
            stats["enviados"],
            stats["fallidos"],
            stats["total"],
        )

        # 3. Clean up files older than the retention window
        deleted = limpiar_antiguos(datos_dir, cfg["dias_retencion_local"])
        if deleted:
            log.info("Cleaned up %d old file(s)", deleted)

        return 0 if stats["fallidos"] == 0 else 1

    except Exception as exc:
        log.exception("Daily send failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
