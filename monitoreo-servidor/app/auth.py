"""Authentication and token validation for the monitoring server.

Implements SHA-256 token hashing and validation against stored hashes,
including grace-period support for seamless token rotation.

Author: Daniel Perez
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)

TokenMatchType = Literal["active", "previous"]


class RoomTokens(BaseModel):
    """Token state for a single room."""

    active_hash: str
    active_since: datetime
    previous_hash: str | None = None
    previous_until: datetime | None = None


def hash_token(token: str) -> str:
    """Compute the SHA-256 hex digest of a plaintext token.

    Args:
        token: Plaintext token string.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load_tokens(path: Path) -> dict[str, RoomTokens]:
    """Load room token definitions from a YAML file.

    Args:
        path: Path to the tokens YAML file.

    Returns:
        Mapping of room code to RoomTokens.

    Raises:
        FileNotFoundError: If the tokens file does not exist.
        ValueError: If the file is malformed or contains invalid entries.
    """
    if not path.exists():
        raise FileNotFoundError(f"Tokens file not found: {path}")

    try:
        raw: dict = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse tokens file {path}: {exc}") from exc

    rooms_raw: dict = raw.get("rooms") or {}
    result: dict[str, RoomTokens] = {}

    for room_code, room_data in rooms_raw.items():
        try:
            result[room_code] = RoomTokens.model_validate(room_data)
        except Exception as exc:
            raise ValueError(
                f"Invalid token entry for room '{room_code}': {exc}"
            ) from exc

    return result


def validate_token(
    token: str,
    room_code: str,
    tokens: dict[str, RoomTokens],
) -> TokenMatchType | None:
    """Validate a plaintext token against the stored hashes for a room.

    Checks the active token first. If that fails and a previous token exists
    within its grace window, accepts it as 'previous' to signal active rotation.

    Args:
        token: Plaintext token from the X-Auth-Token request header.
        room_code: Room identifier from the upload form field.
        tokens: Loaded room tokens mapping (from load_tokens).

    Returns:
        'active'   — active token matched.
        'previous' — previous token matched within grace window.
        None       — no valid match; request must be rejected.
    """
    room = tokens.get(room_code)
    if room is None:
        return None

    token_hash = hash_token(token)

    if token_hash == room.active_hash:
        return "active"

    if (
        room.previous_hash is not None
        and token_hash == room.previous_hash
        and room.previous_until is not None
        and datetime.now(timezone.utc) <= room.previous_until
    ):
        logger.info(
            "Room %s accepted with previous token (grace window active)", room_code
        )
        return "previous"

    return None
