"""Tests for app/auth.py - token hashing and shared-token validation.

Author: Daniel Perez
"""

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.auth import SharedToken, hash_token, load_tokens, validate_token


def test_hash_token_is_deterministic() -> None:
    assert hash_token("my-token") == hash_token("my-token")


def test_hash_token_matches_sha256() -> None:
    expected = hashlib.sha256("abc".encode()).hexdigest()
    assert hash_token("abc") == expected


def test_hash_token_different_inputs_differ() -> None:
    assert hash_token("token-a") != hash_token("token-b")


def _shared(token: str, **kwargs) -> SharedToken:
    return SharedToken(
        active_hash=hash_token(token),
        active_since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        **kwargs,
    )


def test_validate_token_accepts_active_token() -> None:
    token_state = _shared("correct-token")
    assert validate_token("correct-token", token_state) == "active"


def test_validate_token_rejects_wrong_token() -> None:
    token_state = _shared("correct-token")
    assert validate_token("wrong-token", token_state) is None


def test_validate_token_accepts_any_room_with_shared_token() -> None:
    token_state = _shared("shared-token")
    assert validate_token("shared-token", token_state) == "active"


def test_validate_token_accepts_previous_within_grace() -> None:
    prev = "old-token"
    new = "new-token"
    future = datetime.now(timezone.utc) + timedelta(hours=12)
    token_state = _shared(
        new,
        previous_hash=hash_token(prev),
        previous_until=future,
    )
    assert validate_token(prev, token_state) == "previous"


def test_validate_token_rejects_previous_after_expiry() -> None:
    prev = "old-token"
    new = "new-token"
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    token_state = _shared(
        new,
        previous_hash=hash_token(prev),
        previous_until=past,
    )
    assert validate_token(prev, token_state) is None


def test_validate_token_rejects_previous_when_no_previous_until() -> None:
    prev = "old-token"
    new = "new-token"
    token_state = SharedToken(
        active_hash=hash_token(new),
        active_since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        previous_hash=hash_token(prev),
        previous_until=None,
    )
    assert validate_token(prev, token_state) is None


def test_load_tokens_reads_yaml(tmp_path: Path) -> None:
    token = "test-token-xyz"
    tokens_file = tmp_path / "tokens.yaml"
    tokens_file.write_text(
        f"""\
shared_token:
  active_hash: "{hash_token(token)}"
  active_since: "2026-01-01T00:00:00Z"
  previous_hash: null
  previous_until: null
""",
        encoding="utf-8",
    )
    result = load_tokens(tokens_file)
    assert result.active_hash == hash_token(token)


def test_load_tokens_missing_shared_token_raises(tmp_path: Path) -> None:
    tokens_file = tmp_path / "tokens.yaml"
    tokens_file.write_text("rooms: {}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_tokens(tokens_file)


def test_load_tokens_file_not_found_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_tokens(tmp_path / "missing.yaml")


def test_load_tokens_malformed_yaml_raises(tmp_path: Path) -> None:
    tokens_file = tmp_path / "tokens.yaml"
    tokens_file.write_text("{ bad: yaml: content", encoding="utf-8")
    with pytest.raises(ValueError):
        load_tokens(tokens_file)
