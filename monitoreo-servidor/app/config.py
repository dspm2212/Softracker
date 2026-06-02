"""Configuration loading and validation for the monitoring server.

Loads AppConfig from a YAML file, validates all required fields via Pydantic,
and resolves relative paths to absolute paths based on the config file location.

Author: Daniel Perez
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

_DEFAULT_PATHS: list[Path] = [
    Path("config.yaml"),
    Path("/etc/monitoreo-salas/config.yaml"),
]


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    max_upload_mb: int = 50


class StorageConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    base_dir: Path
    parquet_subdir: str = "parquet"
    audit_log_file: str = "log/ingest.log"
    reject_dir: str = "rejected"


class AuthConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    tokens_file: Path
    max_clock_skew_min: int = 30
    admin_token_hash: str


class LoggingConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    level: str = "INFO"
    file: Path
    rotate_mb: int = 50
    backups: int = 5


class AppConfig(BaseModel):
    server: ServerConfig
    storage: StorageConfig
    auth: AuthConfig
    logging: LoggingConfig


def load_config(path: Path | None = None) -> AppConfig:
    """Load and validate server configuration from a YAML file.

    Args:
        path: Explicit path to config.yaml. If None, searches default locations.

    Returns:
        Validated AppConfig with all relative paths resolved to absolute.

    Raises:
        FileNotFoundError: If no config file is found at the given or default paths.
        ValueError: If the YAML is malformed or required fields are missing/invalid.
    """
    config_path = _find_config_file(path)
    logger.debug("Loading config from %s", config_path)

    try:
        raw: dict = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse config file {config_path}: {exc}") from exc

    _resolve_raw_paths(raw, config_path.parent)

    try:
        return AppConfig.model_validate(raw)
    except Exception as exc:
        raise ValueError(f"Invalid configuration in {config_path}: {exc}") from exc


def _find_config_file(path: Path | None) -> Path:
    """Return the config file path, searching defaults if none is given.

    Search order: explicit path → MONITOREO_CONFIG env var → default locations.

    Args:
        path: Explicit path or None to trigger default search.

    Raises:
        FileNotFoundError: If no config file is found at any location.
    """
    if path is not None:
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        return path

    env_path = os.environ.get("MONITOREO_CONFIG")
    if env_path:
        p = Path(env_path)
        if not p.exists():
            raise FileNotFoundError(
                f"Config file from MONITOREO_CONFIG not found: {p}"
            )
        return p

    for candidate in _DEFAULT_PATHS:
        if candidate.exists():
            return candidate

    searched = ", ".join(str(p) for p in _DEFAULT_PATHS)
    raise FileNotFoundError(f"No config file found. Searched: {searched}")


def _resolve_path(p: Path, base: Path) -> Path:
    """Return p resolved to absolute, using base for relative paths."""
    return p if p.is_absolute() else (base / p).resolve()


def _resolve_raw_paths(raw: dict, config_dir: Path) -> None:
    """Resolve relative paths inside the raw config dict in-place.

    Only resolves paths that are present; missing fields are left for Pydantic
    to report as validation errors.
    """
    if isinstance(raw.get("storage"), dict) and "base_dir" in raw["storage"]:
        raw["storage"]["base_dir"] = str(
            _resolve_path(Path(raw["storage"]["base_dir"]), config_dir)
        )
    if isinstance(raw.get("auth"), dict) and "tokens_file" in raw["auth"]:
        raw["auth"]["tokens_file"] = str(
            _resolve_path(Path(raw["auth"]["tokens_file"]), config_dir)
        )
    if isinstance(raw.get("logging"), dict) and "file" in raw["logging"]:
        raw["logging"]["file"] = str(
            _resolve_path(Path(raw["logging"]["file"]), config_dir)
        )
