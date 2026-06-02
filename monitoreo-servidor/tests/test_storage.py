"""Tests for app/storage.py — atomic writes, duplicates, rejected, stats.

Author: Daniel Perez
"""

from pathlib import Path

import pytest

from app.storage import save_parquet, save_rejected, storage_stats

_FAKE_PARQUET = b"PAR1" + b"\x00" * 100  # not a real parquet, fine for storage tests


def test_save_parquet_creates_correct_path(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    from unittest.mock import patch

    fixed_date = datetime(2026, 5, 26, tzinfo=timezone.utc)
    with patch("app.storage.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_date
        saved = save_parquet(_FAKE_PARQUET, "SALA-01", "test.parquet", tmp_path)

    assert saved.parts[-4] == "2026"
    assert saved.parts[-3] == "05"
    assert saved.parts[-2] == "26"
    assert saved.name == "SALA-01_test.parquet"


def test_save_parquet_creates_directories(tmp_path: Path) -> None:
    saved = save_parquet(_FAKE_PARQUET, "SALA-01", "test.parquet", tmp_path)
    assert saved.parent.exists()


def test_save_parquet_file_is_readable(tmp_path: Path) -> None:
    saved = save_parquet(_FAKE_PARQUET, "SALA-01", "test.parquet", tmp_path)
    assert saved.read_bytes() == _FAKE_PARQUET


def test_save_parquet_no_overwrite_adds_dup1(tmp_path: Path) -> None:
    first = save_parquet(_FAKE_PARQUET, "SALA-01", "test.parquet", tmp_path)
    second = save_parquet(b"different", "SALA-01", "test.parquet", tmp_path)
    assert first.exists()
    assert second.exists()
    assert second.name.endswith("_dup1.parquet")
    assert second.read_bytes() == b"different"


def test_save_parquet_dup_increments(tmp_path: Path) -> None:
    save_parquet(_FAKE_PARQUET, "SALA-01", "test.parquet", tmp_path)
    save_parquet(_FAKE_PARQUET, "SALA-01", "test.parquet", tmp_path)
    third = save_parquet(_FAKE_PARQUET, "SALA-01", "test.parquet", tmp_path)
    assert third.name.endswith("_dup2.parquet")


def test_save_parquet_atomic_no_tmp_left(tmp_path: Path) -> None:
    saved = save_parquet(_FAKE_PARQUET, "SALA-01", "test.parquet", tmp_path)
    tmp_file = saved.with_suffix(".tmp")
    assert not tmp_file.exists()


def test_save_rejected_goes_to_rejected_dir(tmp_path: Path) -> None:
    saved = save_rejected(_FAKE_PARQUET, "SALA-01", "invalid_parquet", tmp_path)
    assert "rejected" in saved.parts
    assert saved.suffix == ".bin"
    assert "SALA-01" in saved.name
    assert "invalid_parquet" in saved.name


def test_save_rejected_file_contents(tmp_path: Path) -> None:
    data = b"corrupted data"
    saved = save_rejected(data, "SALA-01", "invalid_parquet", tmp_path)
    assert saved.read_bytes() == data


def test_storage_stats_empty(tmp_path: Path) -> None:
    stats = storage_stats(tmp_path)
    assert stats["total_files"] == 0
    assert stats["total_size_mb"] == 0.0
    assert stats["rejected_files"] == 0


def test_storage_stats_counts_files(tmp_path: Path) -> None:
    # Use >5.5 KB files so rounding to 2 decimal places yields > 0.0 MB
    large_fake = b"x" * 6000
    save_parquet(large_fake, "SALA-01", "a.parquet", tmp_path)
    save_parquet(large_fake, "SALA-01", "b.parquet", tmp_path)
    stats = storage_stats(tmp_path)
    assert stats["total_files"] == 2
    assert stats["total_size_mb"] > 0


def test_storage_stats_counts_rejected(tmp_path: Path) -> None:
    save_rejected(_FAKE_PARQUET, "SALA-01", "invalid_parquet", tmp_path)
    stats = storage_stats(tmp_path)
    assert stats["rejected_files"] == 1
