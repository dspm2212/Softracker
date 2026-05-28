"""
Entry point: execute one monitoring capture and append to the daily JSONL.

Loads configuration, builds a snapshot of applications running under the
current user, and appends it to datos_dir/raw/YYYY-MM-DD.jsonl.

Exit codes:
    0  snapshot captured and saved successfully.
    1  fatal error (config missing, capture failed, or write error).

Author: Daniel Perez
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agente.almacenamiento import guardar_muestra
from agente.captura import construir_muestra
from agente.config import cargar_config
from agente.logger import configurar_logger


def main() -> int:
    """Run one capture cycle. Returns exit code."""
    try:
        cfg = cargar_config()
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR loading config: {exc}", file=sys.stderr)
        return 1

    log = configurar_logger("captura", Path(cfg["log_dir"]))
    log.info("Starting capture")

    try:
        muestra = construir_muestra()
        guardar_muestra(muestra, Path(cfg["datos_dir"]))
        log.info(
            "Captured %d app(s) — user: %s  host: %s",
            muestra["cantidad_apps"],
            muestra["usuario"],
            muestra["hostname"],
        )
        return 0
    except Exception as exc:
        log.exception("Capture failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
