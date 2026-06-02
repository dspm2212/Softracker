"""Tests for app/audit.py — JSONL audit log write and read.

Author: Daniel Perez
"""

import json
from pathlib import Path

from app.audit import log_event, tail_events

_EVENT = {
    "ts_utc": "2026-05-26T21:48:12.345Z",
    "client_ip": "10.20.1.5",
    "endpoint": "/v1/upload",
    "room_code": "SALA-01",
    "result": "ok",
    "file_bytes": 47821,
    "saved_path": "parquet/2026/05/26/SALA-01_test.parquet",
    "error_detail": None,
    "elapsed_ms": 42,
}


def test_log_event_creates_file(tmp_path: Path) -> None:
    audit_file = tmp_path / "log" / "ingest.log"
    log_event(_EVENT, audit_file)
    assert audit_file.exists()


def test_log_event_creates_parent_dirs(tmp_path: Path) -> None:
    audit_file = tmp_path / "deep" / "path" / "ingest.log"
    log_event(_EVENT, audit_file)
    assert audit_file.parent.exists()


def test_log_event_writes_valid_json(tmp_path: Path) -> None:
    audit_file = tmp_path / "ingest.log"
    log_event(_EVENT, audit_file)
    line = audit_file.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["result"] == "ok"
    assert parsed["room_code"] == "SALA-01"


def test_log_event_appends_multiple(tmp_path: Path) -> None:
    audit_file = tmp_path / "ingest.log"
    for _ in range(5):
        log_event(_EVENT, audit_file)
    lines = [l for l in audit_file.read_text(encoding="utf-8").splitlines() if l]
    assert len(lines) == 5


def test_tail_events_returns_last_n(tmp_path: Path) -> None:
    audit_file = tmp_path / "ingest.log"
    for i in range(10):
        log_event({**_EVENT, "elapsed_ms": i}, audit_file)
    events = tail_events(audit_file, n=3)
    assert len(events) == 3
    assert events[-1]["elapsed_ms"] == 9


def test_tail_events_returns_all_when_n_0(tmp_path: Path) -> None:
    audit_file = tmp_path / "ingest.log"
    for _ in range(5):
        log_event(_EVENT, audit_file)
    assert len(tail_events(audit_file, n=0)) == 5


def test_tail_events_empty_file(tmp_path: Path) -> None:
    audit_file = tmp_path / "ingest.log"
    audit_file.write_text("", encoding="utf-8")
    assert tail_events(audit_file) == []


def test_tail_events_missing_file(tmp_path: Path) -> None:
    assert tail_events(tmp_path / "nonexistent.log") == []
