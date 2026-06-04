"""Authentication and token validation for the monitoring server.

Implements SHA-256 token hashing and validation against a shared agent token,
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


class SharedToken(BaseModel):
    """Token state shared by all rooms."""

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


def load_tokens(path: Path) -> SharedToken:
    """Load the shared agent token definition from a YAML file.

    Args:
        path: Path to the tokens YAML file.

    Returns:
        SharedToken used by every room.

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

    try:
        return SharedToken.model_validate(raw.get("shared_token"))
    except Exception as exc:
        raise ValueError(f"Invalid shared_token entry in {path}: {exc}") from exc


def validate_token(
    token: str,
    shared_token: SharedToken,
) -> TokenMatchType | None:
    """Validate a plaintext token against the shared stored hashes.

    Checks the active token first. If that fails and a previous token exists
    within its grace window, accepts it as 'previous' to signal active rotation.

    Args:
        token: Plaintext token from the X-Auth-Token request header.
        shared_token: Loaded shared token state from load_tokens().

    Returns:
        'active'   — active token matched.
        'previous' — previous token matched within grace window.
        None       — no valid match; request must be rejected.
    """
    token_hash = hash_token(token)

    if token_hash == shared_token.active_hash:
        return "active"

    if (
        shared_token.previous_hash is not None
        and token_hash == shared_token.previous_hash
        and shared_token.previous_until is not None
        and datetime.now(timezone.utc) <= shared_token.previous_until
    ):
        logger.info("Request accepted with previous shared token (grace window active)")
        return "previous"

    return None
