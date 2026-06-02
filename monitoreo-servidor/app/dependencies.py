"""FastAPI dependency providers for shared request-scoped resources.

Author: Daniel Perez
"""

from __future__ import annotations

import time

from fastapi import Header, HTTPException, Request

from app.auth import RoomTokens, hash_token, load_tokens
from app.config import AppConfig


def get_config(request: Request) -> AppConfig:
    """Return the application config stored in app state at startup."""
    return request.app.state.config


def get_tokens(request: Request) -> dict[str, RoomTokens]:
    """Load room tokens fresh from disk on every request.

    Reading fresh on each request ensures token rotations are picked up
    immediately without restarting the server.
    """
    config: AppConfig = request.app.state.config
    return load_tokens(config.auth.tokens_file)


def get_uptime(request: Request) -> float:
    """Return server uptime in seconds."""
    return time.monotonic() - request.app.state.start_time


def require_admin_token(
    request: Request,
    x_admin_token: str | None = Header(None),
) -> None:
    """Dependency that enforces a valid X-Admin-Token header.

    Raises:
        HTTPException 401: If the header is missing or the hash does not match.
    """
    config: AppConfig = request.app.state.config
    if x_admin_token is None or hash_token(x_admin_token) != config.auth.admin_token_hash:
        raise HTTPException(status_code=401, detail="invalid token")
