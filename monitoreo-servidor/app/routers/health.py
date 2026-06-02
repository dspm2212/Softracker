"""Health check endpoint for liveness probes and load balancers.

Author: Daniel Perez
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import get_uptime

router = APIRouter()

_VERSION = "1.0.0"


@router.get("/v1/health")
def health(uptime: float = Depends(get_uptime)) -> dict:
    """Return server liveness status.

    No authentication required. Used by load balancers and monitoring.
    """
    return {
        "status": "ok",
        "version": _VERSION,
        "uptime_seconds": int(uptime),
    }
