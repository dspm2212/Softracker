"""Tests for app/validation.py — Parquet format, size, and room code validation.

Author: Daniel Perez
"""

import io

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.validation import validate_max_size, validate_parquet, validate_room_code


def _make_parquet_bytes() -> bytes:
    """Build a minimal valid in-memory Parquet file (snappy-compressed)."""
    table = pa.table({"col": pa.array([1, 2, 3])})
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


# --- validate_parquet ---

def test_valid_parquet_returns_true() -> None:
    ok, err = validate_parquet(_make_parquet_bytes())
    assert ok is True
    assert err is None


def test_random_bytes_returns_false_with_message() -> None:
    ok, err = validate_parquet(b"not a parquet file at all")
    assert ok is False
    assert isinstance(err, str) and len(err) > 0


def test_empty_bytes_returns_false() -> None:
    ok, _ = validate_parquet(b"")
    assert ok is False


def test_truncated_parquet_returns_false() -> None:
    valid = _make_parquet_bytes()
    ok, _ = validate_parquet(valid[:20])  # truncated footer
    assert ok is False


# --- validate_max_size ---

def test_size_within_limit() -> None:
    assert validate_max_size(b"x" * 1024, max_mb=1) is True


def test_size_at_exact_limit() -> None:
    limit = 5 * 1024 * 1024
    assert validate_max_size(b"x" * limit, max_mb=5) is True


def test_size_one_byte_over_limit() -> None:
    over = 5 * 1024 * 1024 + 1
    assert validate_max_size(b"x" * over, max_mb=5) is False


def test_empty_file_is_within_any_limit() -> None:
    assert validate_max_size(b"", max_mb=1) is True


# --- validate_room_code ---

@pytest.mark.parametrize("code", [
    "SALA-01",
    "LAB1",
    "A1B",
    "SALA-LAB-03",
    "A" * 20,       # exactly 20 chars
    "ABC",          # exactly 3 chars
])
def test_valid_room_codes(code: str) -> None:
    assert validate_room_code(code) is True


@pytest.mark.parametrize("code", [
    "ab",           # lowercase
    "AB",           # too short (2 chars)
    "A" * 21,       # too long (21 chars)
    "SALA 01",      # space not allowed
    "",             # empty
    "sala-01",      # lowercase letters
    "SALA_01",      # underscore not in pattern
    "SALA.01",      # dot not in pattern
])
def test_invalid_room_codes(code: str) -> None:
    assert validate_room_code(code) is False
