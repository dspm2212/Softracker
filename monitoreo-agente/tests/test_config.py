"""
Tests for agente.config module.

Covers: successful load, defaults, overrides, missing required keys,
and file-not-found error.

Author: Daniel Perez
"""

import json
from pathlib import Path

import pytest

from agente.config import DEFAULTS, REQUIRED_KEYS, cargar_config


def _write_config(path: Path, data: dict) -> Path:
    """Helper: write data as JSON to path and return path."""
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def minimal_config(tmp_path: Path) -> Path:
    """A config.json with only the required keys."""
    return _write_config(
        tmp_path / "config.json",
        {
            "api_url": "http://test.server/v1/upload",
            "sala_codigo": "SALA-TEST",
            "datos_dir": str(tmp_path / "datos"),
        },
    )


# ── happy path ────────────────────────────────────────────────────────────────

def test_load_returns_required_values(minimal_config: Path) -> None:
    cfg = cargar_config(minimal_config)
    assert cfg["api_url"] == "http://test.server/v1/upload"
    assert cfg["token"] == DEFAULTS["token"]
    assert cfg["sala_codigo"] == "SALA-TEST"


def test_defaults_applied_for_optional_keys(minimal_config: Path) -> None:
    cfg = cargar_config(minimal_config)
    for key, expected in DEFAULTS.items():
        assert cfg[key] == expected, f"Default for '{key}' not applied"


def test_explicit_value_overrides_default(tmp_path: Path) -> None:
    data = {
        "api_url": "http://x.com",
        "token": "T",
        "sala_codigo": "S",
        "datos_dir": str(tmp_path),
        "reintentos_envio": 99,
        "dias_retencion_local": 30,
    }
    cfg = cargar_config(_write_config(tmp_path / "config.json", data))
    assert cfg["reintentos_envio"] == 99
    assert cfg["dias_retencion_local"] == 30


def test_all_required_keys_present_in_result(minimal_config: Path) -> None:
    cfg = cargar_config(minimal_config)
    for key in REQUIRED_KEYS:
        assert key in cfg


# ── error cases ───────────────────────────────────────────────────────────────

def test_missing_single_required_key_raises(tmp_path: Path) -> None:
    data = {"api_url": "http://x.com", "sala_codigo": "S"}
    # datos_dir is missing
    with pytest.raises(ValueError, match="datos_dir"):
        cargar_config(_write_config(tmp_path / "config.json", data))


def test_missing_multiple_required_keys_raises(tmp_path: Path) -> None:
    data = {"api_url": "http://x.com"}
    with pytest.raises(ValueError, match="Missing required config keys"):
        cargar_config(_write_config(tmp_path / "config.json", data))


def test_nonexistent_explicit_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        cargar_config(tmp_path / "does_not_exist.json")
