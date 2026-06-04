"""Admin status and shared-token status endpoints.

Both endpoints require a valid X-Admin-Token header.

Author: Daniel Perez
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request

from app.audit import tail_events
from app.auth import load_tokens
from app.config import AppConfig
from app.dependencies import get_config, get_uptime, require_admin_token
from app.storage import storage_stats

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin_token)])


@router.get("/v1/status")
def get_status(
    request: Request,
    uptime: float = Depends(get_uptime),
    config: AppConfig = Depends(get_config),
) -> dict:
    """Return aggregated server health and upload statistics.

    Requires X-Admin-Token header.
    """
    shared_token = load_tokens(config.auth.tokens_file)
    stats = storage_stats(config.storage.base_dir)
    last_24h = _last_24h_stats(config)

    return {
        "uptime_seconds": int(uptime),
        "auth_mode": "shared_token",
        "token_active_since": shared_token.active_since.isoformat(),
        "token_in_rotation": (
            shared_token.previous_hash is not None
            and shared_token.previous_until is not None
        ),
        "storage": stats,
        "last_24h": last_24h,
    }


@router.get("/v1/rooms")
def get_rooms(config: AppConfig = Depends(get_config)) -> dict:
    """Return room configuration mode without exposing token hashes.

    Requires X-Admin-Token header.
    """
    shared_token = load_tokens(config.auth.tokens_file)
    return {
        "auth_mode": "shared_token",
        "rooms": [],
        "rooms_configured": "dynamic",
        "token_active_since": shared_token.active_since.isoformat(),
        "token_in_rotation": (
            shared_token.previous_hash is not None
            and shared_token.previous_until is not None
        ),
    }


def _last_24h_stats(config: AppConfig) -> dict:
    """Aggregate upload counts from the audit log for the last 24 hours."""
    audit_file = config.storage.base_dir / config.storage.audit_log_file
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    # Read enough events to cover a busy 24h (up to 50 000 lines)
    events = tail_events(audit_file, n=50_000)

    uploads_ok = 0
    uploads_rejected = 0
    uploads_by_room: dict[str, int] = {}

    for event in events:
        try:
            ts = datetime.fromisoformat(event["ts_utc"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if ts < cutoff:
            continue

        result = event.get("result", "")
        room = event.get("room_code", "unknown")

        if result in ("ok", "ok_previous_token"):
            uploads_ok += 1
            uploads_by_room[room] = uploads_by_room.get(room, 0) + 1
        elif result not in ("error",):
            uploads_rejected += 1

    return {
        "uploads_ok": uploads_ok,
        "uploads_rejected": uploads_rejected,
        "uploads_by_room": uploads_by_room,
    }
