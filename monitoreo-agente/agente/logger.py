"""
Rotating file logger setup for the monitoring agent.

Provides a per-component logger with configurable level, automatic rotation,
and optional stdout output for interactive terminal sessions.

Author: Daniel Perez
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_MAX_BYTES: int = 2 * 1024 * 1024  # 2 MB per log file
_BACKUP_COUNT: int = 3
_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def log_error_inicio(msg: str) -> None:
    """Write a fatal startup message when no logger exists yet.

    Used before configurar_logger can be called (e.g. config load failure).
    Falls back to a fixed file when sys.stderr is None (--noconsole builds).
    """
    if sys.stderr is not None:
        print(msg, file=sys.stderr)
    else:
        fallback = Path("C:/monitoreo/logs/startup_error.log")
        try:
            fallback.parent.mkdir(parents=True, exist_ok=True)
            with fallback.open("a", encoding="utf-8") as fh:
                fh.write(msg + "\n")
        except OSError:
            pass


def configurar_logger(nombre: str, log_dir: Path) -> logging.Logger:
    """Create a rotating file logger, optionally mirroring to stdout.

    Creates log_dir if it does not exist. Handlers are only attached once;
    subsequent calls with the same name return the cached logger. Log level
    is always updated to reflect the current MONITOREO_DEBUG env var.

    Args:
        nombre: Logger name, also used as the log filename stem.
        log_dir: Directory where the rotating log file will be written.

    Returns:
        Configured Logger instance.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    level = _resolve_level()

    logger = logging.getLogger(nombre)
    logger.setLevel(level)

    if not logger.handlers:
        file_handler = _build_file_handler(log_dir / f"{nombre}.log", level)
        logger.addHandler(file_handler)
        if sys.stdout is not None and sys.stdout.isatty():
            logger.addHandler(_build_stdout_handler(level))

        # Route agente.* module loggers (agente.envio, agente.almacenamiento, etc.)
        # into the same file. Each agent runs as its own process so sharing the
        # handler across the namespace is safe.
        agente_ns = logging.getLogger("agente")
        agente_ns.setLevel(level)
        if not agente_ns.handlers:
            agente_ns.addHandler(file_handler)

    return logger


def _resolve_level() -> int:
    """Return logging.DEBUG if MONITOREO_DEBUG=1 is set, else logging.INFO."""
    return logging.DEBUG if os.environ.get("MONITOREO_DEBUG") == "1" else logging.INFO


def _build_file_handler(log_path: Path, level: int) -> RotatingFileHandler:
    """Create a RotatingFileHandler writing UTF-8 encoded log lines."""
    handler = RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FORMAT))
    return handler


def _build_stdout_handler(level: int) -> logging.StreamHandler:
    """Create a StreamHandler writing to stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FORMAT))
    return handler
