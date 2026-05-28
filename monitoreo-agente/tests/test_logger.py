"""
Tests for agente.logger module.

Covers: logger creation, file creation, directory auto-creation, log level
selection, and message persistence to disk.

Author: Daniel Perez
"""

import logging
from pathlib import Path

import pytest

from agente.logger import configurar_logger


@pytest.fixture(autouse=True)
def _cleanup_test_loggers():
    """Remove handlers from test loggers after each test to avoid pollution."""
    yield
    for name, obj in list(logging.Logger.manager.loggerDict.items()):
        if name.startswith("_test_") and isinstance(obj, logging.Logger):
            for handler in obj.handlers[:]:
                handler.close()
                obj.removeHandler(handler)


def _unique(suffix: str) -> str:
    """Return a unique logger name that the cleanup fixture will collect."""
    return f"_test_{suffix}"


# ── creation ──────────────────────────────────────────────────────────────────

def test_returns_logger_instance(tmp_path: Path) -> None:
    logger = configurar_logger(_unique("instance"), tmp_path)
    assert isinstance(logger, logging.Logger)


def test_log_file_created(tmp_path: Path) -> None:
    name = _unique("file_created")
    configurar_logger(name, tmp_path)
    assert (tmp_path / f"{name}.log").exists()


def test_log_dir_created_if_missing(tmp_path: Path) -> None:
    deep = tmp_path / "deep" / "nested" / "logs"
    configurar_logger(_unique("deep"), deep)
    assert deep.exists()


# ── log level ─────────────────────────────────────────────────────────────────

def test_default_level_is_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONITOREO_DEBUG", raising=False)
    logger = configurar_logger(_unique("info_level"), tmp_path)
    assert logger.level == logging.INFO


def test_debug_level_when_env_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONITOREO_DEBUG", "1")
    logger = configurar_logger(_unique("debug_level"), tmp_path)
    assert logger.level == logging.DEBUG


# ── message persistence ───────────────────────────────────────────────────────

def test_info_message_written_to_file(tmp_path: Path) -> None:
    name = _unique("write_info")
    logger = configurar_logger(name, tmp_path)
    logger.info("sentinel message abc123")
    content = (tmp_path / f"{name}.log").read_text(encoding="utf-8")
    assert "sentinel message abc123" in content


def test_debug_message_not_written_at_info_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MONITOREO_DEBUG", raising=False)
    name = _unique("no_debug")
    logger = configurar_logger(name, tmp_path)
    logger.debug("should not appear")
    content = (tmp_path / f"{name}.log").read_text(encoding="utf-8")
    assert "should not appear" not in content


def test_log_format_contains_level_and_name(tmp_path: Path) -> None:
    name = _unique("format_check")
    logger = configurar_logger(name, tmp_path)
    logger.info("format test")
    content = (tmp_path / f"{name}.log").read_text(encoding="utf-8")
    assert "[INFO]" in content
    assert name in content
