"""Logging configuration for the monitoring server.

Provides a factory function that creates rotating file loggers and optionally
adds a stdout handler when running in an interactive terminal.

Author: Daniel Perez
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def configure_logger(
    name: str,
    log_file: Path,
    level: str = "INFO",
    rotate_mb: int = 50,
    backups: int = 5,
) -> logging.Logger:
    """Create and configure a rotating file logger.

    Handlers are added only once per logger name; repeated calls return the
    cached logger without duplicating handlers.

    Args:
        name: Logger name used as identifier in log records.
        log_file: Destination log file. Parent directories are created if needed.
        level: Logging level string — DEBUG, INFO, WARNING, ERROR, or CRITICAL.
        rotate_mb: Maximum file size in megabytes before rotating.
        backups: Number of rotated backup files to retain.

    Returns:
        Configured Logger instance.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    log = logging.getLogger(name)
    log.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not log.handlers:
        _add_file_handler(log, log_file, rotate_mb, backups)
        if sys.stdout.isatty():
            _add_stdout_handler(log)

    return log


def _add_file_handler(
    log: logging.Logger,
    log_file: Path,
    rotate_mb: int,
    backups: int,
) -> None:
    handler = RotatingFileHandler(
        log_file,
        maxBytes=rotate_mb * 1024 * 1024,
        backupCount=backups,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    log.addHandler(handler)


def _add_stdout_handler(log: logging.Logger) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    log.addHandler(handler)
