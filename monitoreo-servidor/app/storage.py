"""Parquet file storage management for the monitoring server.

Handles atomic writes, duplicate detection, rejected-file quarantine,
and storage statistics for the local filesystem hierarchy.

Author: Daniel Perez
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def save_parquet(
    file_bytes: bytes,
    room_code: str,
    original_filename: str,
    base_dir: Path,
) -> Path:
    """Save a Parquet file atomically under ``base_dir/parquet/YYYY/MM/DD/``.

    Atomic write: data is written to a ``.tmp`` file then renamed, so a
    partial write is never visible as a complete file.  If the target name
    already exists, a ``_dupN`` suffix is appended and a WARNING is logged.

    Args:
        file_bytes: Raw Parquet bytes to store.
        room_code: Room identifier, prepended to the filename.
        original_filename: Filename as sent by the agent.
        base_dir: Root storage directory from config.

    Returns:
        Absolute path of the saved file.
    """
    today = datetime.now(timezone.utc).date()
    dest_dir = (
        base_dir
        / "parquet"
        / f"{today.year:04d}"
        / f"{today.month:02d}"
        / f"{today.day:02d}"
    )
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / f"{room_code}_{original_filename}"
    dest_path = _unique_path(dest_path)

    _atomic_write(file_bytes, dest_path)
    logger.info("Saved %s (%d bytes)", dest_path, len(file_bytes))
    return dest_path


def save_rejected(
    file_bytes: bytes,
    room_code: str,
    reason: str,
    base_dir: Path,
) -> Path:
    """Save a file that failed validation for forensic inspection.

    Args:
        file_bytes: Raw bytes of the rejected upload.
        room_code: Room code from the request.
        reason: Short slug describing the rejection cause (e.g. "invalid_parquet").
        base_dir: Root storage directory from config.

    Returns:
        Absolute path of the saved file.
    """
    today = datetime.now(timezone.utc).date()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    dest_dir = base_dir / "rejected" / str(today)
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / f"{room_code}_{ts}_{reason}.bin"
    dest_path.write_bytes(file_bytes)
    logger.warning("Rejected file saved: %s (%d bytes, reason=%s)", dest_path, len(file_bytes), reason)
    return dest_path


def storage_stats(base_dir: Path) -> dict:
    """Return counts and total size for accepted and rejected files.

    Args:
        base_dir: Root storage directory from config.

    Returns:
        Dict with keys ``total_files``, ``total_size_mb``, ``rejected_files``.
    """
    parquet_dir = base_dir / "parquet"
    rejected_dir = base_dir / "rejected"

    total_files = 0
    total_bytes = 0
    if parquet_dir.exists():
        for f in parquet_dir.rglob("*.parquet"):
            if f.is_file():
                total_files += 1
                total_bytes += f.stat().st_size

    rejected_files = 0
    if rejected_dir.exists():
        rejected_files = sum(1 for f in rejected_dir.rglob("*") if f.is_file())

    return {
        "total_files": total_files,
        "total_size_mb": round(total_bytes / (1024 * 1024), 2),
        "rejected_files": rejected_files,
    }


def _unique_path(path: Path) -> Path:
    """Return a path that does not yet exist, appending _dupN if needed."""
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_dup{counter}{suffix}"
        if not candidate.exists():
            logger.warning("Duplicate filename detected, using %s", candidate.name)
            return candidate
        counter += 1


def _atomic_write(data: bytes, dest: Path) -> None:
    """Write data to dest atomically via a .tmp intermediary."""
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
