"""Upload endpoint — receives Parquet files from Windows agents.

Author: Daniel Perez
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile

from app.audit import log_event
from app.auth import RoomTokens, load_tokens, validate_token
from app.config import AppConfig
from app.dependencies import get_config
from app.storage import save_parquet, save_rejected
from app.validation import validate_max_size, validate_parquet, validate_room_code

logger = logging.getLogger(__name__)

router = APIRouter()

# TODO: add rate limiting middleware here if needed (e.g. slowapi)


@router.post("/v1/upload", status_code=201)
async def upload_parquet(
    request: Request,
    sala_codigo: str = Form(...),
    archivo: UploadFile = File(...),
    x_auth_token: Annotated[str | None, Header()] = None,
    config: AppConfig = Depends(get_config),
) -> dict:
    """Accept a Parquet file from an agent and store it on disk.

    Validation order (token checked before file bytes are consumed to
    avoid loading large payloads for unauthorized requests):
    1. X-Auth-Token header present.
    2. sala_codigo format valid.
    3. Room exists in token store.
    4. Token matches room (active or previous grace-window token).
    5. File size within limit.
    6. File is valid Parquet.
    7. Save and return 201.
    """
    start = time.monotonic()
    client_ip = request.client.host if request.client else "unknown"
    audit_file = config.storage.base_dir / config.storage.audit_log_file
    base_dir = config.storage.base_dir

    # 1. Auth header present
    if x_auth_token is None:
        _audit(audit_file, client_ip, sala_codigo, "invalid_token", 0, None, None, start)
        raise HTTPException(status_code=401, detail="invalid token")

    # 2. Room code format
    if not validate_room_code(sala_codigo):
        _audit(audit_file, client_ip, sala_codigo, "invalid_room_code", 0, None, None, start)
        raise HTTPException(status_code=400, detail="invalid room code")

    # 3 & 4. Load tokens and validate (token check before reading file bytes)
    try:
        tokens: dict[str, RoomTokens] = load_tokens(config.auth.tokens_file)
    except Exception as exc:
        logger.error("Failed to load tokens file: %s", exc)
        _audit(audit_file, client_ip, sala_codigo, "error", 0, None, str(exc), start)
        raise HTTPException(status_code=500, detail="internal error") from exc

    match_type = validate_token(x_auth_token, sala_codigo, tokens)
    if match_type is None:
        _audit(audit_file, client_ip, sala_codigo, "invalid_token", 0, None, None, start)
        raise HTTPException(status_code=401, detail="invalid token")

    # 5. Read file bytes (limited to max_upload_mb + 1 to detect overflow efficiently)
    max_bytes = config.server.max_upload_mb * 1024 * 1024
    file_bytes = await archivo.read(max_bytes + 1)

    if not validate_max_size(file_bytes, config.server.max_upload_mb):
        _audit(audit_file, client_ip, sala_codigo, "file_too_big", len(file_bytes), None, None, start)
        raise HTTPException(status_code=413, detail="file too large")

    # 6. Parquet validation
    is_valid, parse_error = validate_parquet(file_bytes)
    if not is_valid:
        try:
            save_rejected(file_bytes, sala_codigo, "invalid_parquet", base_dir)
        except OSError as exc:
            logger.error("Could not save rejected file: %s", exc)
        _audit(audit_file, client_ip, sala_codigo, "invalid_parquet", len(file_bytes), None, parse_error, start)
        raise HTTPException(status_code=400, detail="invalid parquet file")

    # 7. Save and audit
    filename = archivo.filename or f"{sala_codigo}_upload.parquet"
    try:
        saved_path = save_parquet(file_bytes, sala_codigo, filename, base_dir)
    except OSError as exc:
        logger.error("Storage write failed: %s", exc)
        _audit(audit_file, client_ip, sala_codigo, "error", len(file_bytes), None, str(exc), start)
        raise HTTPException(status_code=500, detail="internal error") from exc

    result = "ok" if match_type == "active" else "ok_previous_token"
    saved_relative = str(saved_path.relative_to(base_dir))
    _audit(audit_file, client_ip, sala_codigo, result, len(file_bytes), saved_relative, None, start)

    return {
        "status": "ok",
        "saved_as": saved_relative,
        "size_bytes": len(file_bytes),
    }


def _audit(
    audit_file,
    client_ip: str,
    room_code: str,
    result: str,
    file_bytes: int,
    saved_path: str | None,
    error_detail: str | None,
    start: float,
) -> None:
    """Write one event to the audit log — never raises."""
    try:
        log_event(
            {
                "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "client_ip": client_ip,
                "endpoint": "/v1/upload",
                "room_code": room_code,
                "result": result,
                "file_bytes": file_bytes,
                "saved_path": saved_path,
                "error_detail": error_detail,
                "elapsed_ms": int((time.monotonic() - start) * 1000),
            },
            audit_file,
        )
    except Exception as exc:
        logger.error("Audit log write failed: %s", exc)
