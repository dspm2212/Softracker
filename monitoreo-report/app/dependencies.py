"""FastAPI dependency providers for shared request-scoped resources.

Author: Daniel Perez
"""

from __future__ import annotations

import time

from fastapi import Request

from app.config import AppConfig


def get_config(request: Request) -> AppConfig:
    """Return the application config stored in app state at startup."""
    return request.app.state.config


def get_uptime(request: Request) -> float:
    """Return report app uptime in seconds."""
    return time.monotonic() - request.app.state.start_time
