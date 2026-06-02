"""Tests for app/config.py — configuration loading and validation.

Author: Daniel Perez
"""

from pathlib import Path

import pytest

from app.config import AppConfig, load_config


def test_load_config_returns_app_config(config_yaml: Path) -> None:
    cfg = load_config(config_yaml)
    assert isinstance(cfg, AppConfig)


def test_server_defaults(config_yaml: Path) -> None:
    cfg = load_config(config_yaml)
    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 8080
    assert cfg.server.max_upload_mb == 50


def test_storage_subdir_and_dirs(config_yaml: Path) -> None:
    cfg = load_config(config_yaml)
    assert cfg.storage.parquet_subdir == "parquet"
    assert cfg.storage.audit_log_file == "log/ingest.log"
    assert cfg.storage.reject_dir == "rejected"


def test_base_dir_resolved_to_absolute(config_yaml: Path) -> None:
    cfg = load_config(config_yaml)
    assert cfg.storage.base_dir.is_absolute()
    # Relative "./data" should land inside the config file's directory
    assert cfg.storage.base_dir == config_yaml.parent / "data"


def test_tokens_file_resolved_to_absolute(config_yaml: Path) -> None:
    cfg = load_config(config_yaml)
    assert cfg.auth.tokens_file.is_absolute()
    assert cfg.auth.tokens_file == config_yaml.parent / "tokens.yaml"


def test_logging_file_resolved_to_absolute(config_yaml: Path) -> None:
    cfg = load_config(config_yaml)
    assert cfg.logging.file.is_absolute()


def test_absolute_base_dir_preserved(tmp_path: Path) -> None:
    """An absolute base_dir in config should not be modified."""
    abs_data = tmp_path / "abs_data"
    (tmp_path / "tokens.yaml").write_text("rooms: {}\n", encoding="utf-8")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        f"""\
server:
  host: "0.0.0.0"
  port: 8080
  max_upload_mb: 50
storage:
  base_dir: "{abs_data.as_posix()}"
auth:
  tokens_file: "tokens.yaml"
  admin_token_hash: "abc123"
logging:
  level: "INFO"
  file: "./logs/api.log"
""",
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg.storage.base_dir == abs_data


def test_missing_admin_token_hash_raises(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        """\
server:
  host: "0.0.0.0"
  port: 8080
  max_upload_mb: 50
storage:
  base_dir: "./data"
auth:
  tokens_file: "tokens.yaml"
  # admin_token_hash intentionally omitted
logging:
  level: "INFO"
  file: "./logs/api.log"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(cfg_file)


def test_missing_base_dir_raises(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        """\
server:
  host: "0.0.0.0"
  port: 8080
  max_upload_mb: 50
storage:
  parquet_subdir: "parquet"
  # base_dir intentionally omitted
auth:
  tokens_file: "tokens.yaml"
  admin_token_hash: "abc123"
logging:
  level: "INFO"
  file: "./logs/api.log"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(cfg_file)


def test_file_not_found_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nonexistent.yaml")


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("{ this: is: not: valid: yaml", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(cfg_file)
