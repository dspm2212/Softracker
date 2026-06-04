"""
Configuration loader and validator for the monitoring agent.

Loads config.json from the project root or C:\\monitoreo\\config.json,
validates required keys, and applies defaults for optional settings.

The MONITOREO_CONFIG environment variable overrides all search paths —
useful for testing and alternate deployments.

Author: Daniel Perez
"""

import json
import os
from pathlib import Path

REQUIRED_KEYS: tuple[str, ...] = ("api_url", "token", "sala_codigo", "datos_dir")

DEFAULTS: dict[str, object] = {
    "log_dir": "C:\\monitoreo\\logs",
    "intervalo_captura_min": 10,
    "hora_envio": "14:00",
    "timeout_envio_seg": 60,
    "reintentos_envio": 3,
    "dias_retencion_local": 7,
}

_DEFAULT_SEARCH_PATHS: list[Path] = [
    Path(__file__).resolve().parents[1] / "config.json",  # monitoreo-agente/config.json
    Path("C:/monitoreo/config.json"),
]


def cargar_config(ruta: Path | None = None) -> dict:
    """Load and validate agent configuration from a JSON file.

    Args:
        ruta: Explicit path to config.json. If None, searches default locations.

    Returns:
        Validated configuration dictionary with defaults applied for optional keys.

    Raises:
        FileNotFoundError: If no config.json is found in any default location.
        ValueError: If a required key is missing from the configuration.
    """
    path = _resolve_config_path(ruta)
    raw = _load_json(path)
    _validate_required_keys(raw)
    return _apply_defaults(raw)


def _resolve_config_path(ruta: Path | None) -> Path:
    """Return the config file path.

    Priority: explicit ruta → MONITOREO_CONFIG env var → default search paths.
    """
    if ruta is not None:
        return ruta
    env_override = os.environ.get("MONITOREO_CONFIG")
    if env_override:
        return Path(env_override)
    for candidate in _DEFAULT_SEARCH_PATHS:
        if candidate.exists():
            return candidate
    searched = ", ".join(str(p) for p in _DEFAULT_SEARCH_PATHS)
    raise FileNotFoundError(f"config.json not found. Searched: {searched}")


def _load_json(path: Path) -> dict:
    """Read and parse a JSON file into a dict."""
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _validate_required_keys(config: dict) -> None:
    """Raise ValueError listing all absent required keys."""
    missing = [k for k in REQUIRED_KEYS if k not in config]
    if missing:
        raise ValueError(f"Missing required config keys: {missing}")


def _apply_defaults(config: dict) -> dict:
    """Return a new dict with DEFAULTS filled in for keys not present in config."""
    return {**DEFAULTS, **config}
