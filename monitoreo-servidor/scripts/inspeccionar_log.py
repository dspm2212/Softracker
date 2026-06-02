"""CLI tool for inspecting the ingest audit log.

Reads the JSONL audit log and prints events with optional filters.

Usage:
    python scripts/inspeccionar_log.py --last 50
    python scripts/inspeccionar_log.py --room SALA-01 --since "2026-05-26"
    python scripts/inspeccionar_log.py --errors-only

Author: Daniel Perez
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

_DEFAULT_AUDIT = _PROJECT_ROOT / "data" / "log" / "ingest.log"

_RESULT_COLORS = {
    "ok": "\033[32m",              # green
    "ok_previous_token": "\033[36m",  # cyan
    "invalid_token": "\033[31m",   # red
    "invalid_parquet": "\033[33m", # yellow
    "file_too_big": "\033[33m",    # yellow
    "error": "\033[35m",           # magenta
}
_RESET = "\033[0m"


def _colorize(result: str) -> str:
    color = _RESULT_COLORS.get(result, "")
    return f"{color}{result}{_RESET}" if color else result


def _load_events(audit_file: Path, last: int) -> list[dict]:
    if not audit_file.exists():
        print(f"Audit log not found: {audit_file}", file=sys.stderr)
        return []

    lines = audit_file.read_text(encoding="utf-8").splitlines()
    tail = lines[-last:] if last > 0 and len(lines) > last else lines

    events = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _filter_events(
    events: list[dict],
    room: str | None,
    since: datetime | None,
    errors_only: bool,
) -> list[dict]:
    result = []
    for event in events:
        if room and event.get("room_code") != room:
            continue
        if since:
            try:
                ts = datetime.fromisoformat(event["ts_utc"].replace("Z", "+00:00"))
                if ts < since:
                    continue
            except (KeyError, ValueError):
                continue
        if errors_only and event.get("result") in ("ok", "ok_previous_token"):
            continue
        result.append(event)
    return result


def _print_event(event: dict, use_color: bool) -> None:
    ts = event.get("ts_utc", "?")
    room = event.get("room_code", "?")
    result = event.get("result", "?")
    size = event.get("file_bytes", 0)
    elapsed = event.get("elapsed_ms", 0)
    ip = event.get("client_ip", "?")
    error = event.get("error_detail")

    result_str = _colorize(result) if use_color else result
    line = f"{ts}  {room:<12}  {result_str:<22}  {size:>8} B  {elapsed:>4} ms  {ip}"
    if error:
        line += f"  [{error[:60]}]"
    print(line)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect the monitoreo-servidor ingest audit log.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--last", type=int, default=50, metavar="N",
        help="Show last N events (default: 50; 0 = all).",
    )
    parser.add_argument(
        "--room", metavar="SALA_CODE",
        help="Filter events for a specific room code.",
    )
    parser.add_argument(
        "--since", metavar="YYYY-MM-DD",
        help="Show events on or after this UTC date.",
    )
    parser.add_argument(
        "--errors-only", action="store_true",
        help="Show only rejected/error events.",
    )
    parser.add_argument(
        "--log-file", type=Path, default=_DEFAULT_AUDIT, metavar="PATH",
        help=f"Path to ingest.log (default: {_DEFAULT_AUDIT}).",
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="Disable ANSI color output.",
    )

    args = parser.parse_args()

    since_dt: datetime | None = None
    if args.since:
        try:
            since_dt = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"ERROR: Invalid date format '{args.since}'. Use YYYY-MM-DD.", file=sys.stderr)
            sys.exit(1)

    events = _load_events(args.log_file, args.last)
    events = _filter_events(events, args.room, since_dt, args.errors_only)

    if not events:
        print("No events found matching the given filters.")
        return

    use_color = not args.no_color and sys.stdout.isatty()
    header = f"{'TIMESTAMP':<27}  {'ROOM':<12}  {'RESULT':<22}  {'SIZE':>8}    {'MS':>4}  IP"
    print(header)
    print("-" * len(header))
    for event in events:
        _print_event(event, use_color)

    print(f"\n{len(events)} event(s) shown.")


if __name__ == "__main__":
    main()
