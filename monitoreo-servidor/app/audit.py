"""Audit logging for every ingest request received by the server.

Writes one JSON object per line (JSONL) to an append-only log file.
This provides a forensic trail of all uploads, accepted or rejected.

Author: Daniel Perez
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def log_event(event: dict, audit_file: Path) -> None:
    """Append a single audit event to the JSONL log file.

    Each call writes exactly one line.  On POSIX systems, append writes
    smaller than PIPE_BUF (~4 KB) are atomic, so concurrent uvicorn
    workers can safely write without an explicit lock.

    Args:
        event: Dict with the event fields (ts_utc, client_ip, result, etc.).
        audit_file: Path to the JSONL audit log (created if absent).
    """
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    try:
        with audit_file.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        logger.error("Failed to write audit event: %s", exc)


def tail_events(audit_file: Path, n: int = 100) -> list[dict]:
    """Return the last N events from the audit log.

    Args:
        audit_file: Path to the JSONL audit log.
        n: Maximum number of events to return.  Pass 0 to return all events.

    Returns:
        List of event dicts, oldest-first within the returned window.
    """
    if not audit_file.exists():
        return []

    try:
        lines = audit_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.error("Failed to read audit log: %s", exc)
        return []

    tail = lines[-n:] if n > 0 and len(lines) > n else lines

    events: list[dict] = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            logger.warning("Skipping malformed audit line: %s", exc)

    return events
