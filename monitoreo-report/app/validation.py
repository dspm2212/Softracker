"""Input validation for uploaded files and request parameters.

Provides Parquet format verification, size enforcement, and room code
format checks used by the upload endpoint.

Author: Daniel Perez
"""

from __future__ import annotations

import io
import logging
import re

import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

_ROOM_CODE_RE = re.compile(r"^[A-Z0-9\-]{3,20}$")


def validate_parquet(file_bytes: bytes) -> tuple[bool, str | None]:
    """Validate that bytes represent a well-formed Parquet file.

    Reads only the Parquet footer metadata — not the full row data — to
    keep memory usage proportional to schema size rather than file size.

    Args:
        file_bytes: Raw bytes of the uploaded file.

    Returns:
        (True, None) if the file is valid Parquet.
        (False, error_message) if the file is invalid or unreadable.
    """
    try:
        pq.read_metadata(io.BytesIO(file_bytes))
        return True, None
    except Exception as exc:
        return False, str(exc)


def validate_max_size(file_bytes: bytes, max_mb: int) -> bool:
    """Check that the file does not exceed the configured size limit.

    Args:
        file_bytes: Raw file bytes.
        max_mb: Maximum allowed size in megabytes.

    Returns:
        True if the file is within the limit, False otherwise.
    """
    return len(file_bytes) <= max_mb * 1024 * 1024


def validate_room_code(code: str) -> bool:
    """Check that a room code matches the expected format.

    Valid codes match ``^[A-Z0-9-]{3,20}$`` — uppercase letters, digits,
    and hyphens only, between 3 and 20 characters.

    Args:
        code: Room code string from the upload form.

    Returns:
        True if the code is valid, False otherwise.
    """
    return bool(_ROOM_CODE_RE.match(code))
