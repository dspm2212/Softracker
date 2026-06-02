"""Tests for app/logger.py — rotating file logger setup.

Author: Daniel Perez
"""

import logging
from pathlib import Path

import pytest

from app.logger import configure_logger


def test_logger_creates_log_file(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "test.log"
    log = configure_logger(f"t.create.{tmp_path.name}", log_file)
    log.info("hello")
    assert log_file.exists()


def test_logger_creates_parent_directories(tmp_path: Path) -> None:
    log_file = tmp_path / "deep" / "nested" / "dir" / "app.log"
    configure_logger(f"t.dirs.{tmp_path.name}", log_file)
    assert log_file.parent.exists()


def test_logger_level_is_set(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    log = configure_logger(f"t.level.{tmp_path.name}", log_file, level="WARNING")
    assert log.level == logging.WARNING


def test_logger_writes_message_to_file(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    log = configure_logger(f"t.write.{tmp_path.name}", log_file)
    log.info("sentinel_message_xyz")
    assert "sentinel_message_xyz" in log_file.read_text(encoding="utf-8")


def test_logger_does_not_add_duplicate_handlers(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    name = f"t.dedup.{tmp_path.name}"
    log = configure_logger(name, log_file)
    handler_count = len(log.handlers)
    configure_logger(name, log_file)  # second call — same logger
    assert len(log.handlers) == handler_count


def test_invalid_level_falls_back_to_info(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    log = configure_logger(f"t.fallback.{tmp_path.name}", log_file, level="NOTAREAL")
    assert log.level == logging.INFO
