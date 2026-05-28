"""
Application capture module for the monitoring agent.

Detects processes with visible windows owned by the current logged-in user
using win32gui window enumeration combined with psutil process details.

Author: Daniel Perez
"""

import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil
import win32gui
import win32process


def capturar_apps_en_uso() -> list[dict]:
    """Detect processes with windows belonging to the current logged-in user.

    Enumerates all visible top-level windows, resolves each to its owning
    process, keeps only processes owned by the current user, and deduplicates
    by PID (one process may have several windows).

    Returns:
        List of dicts, one per unique process, with keys:
        nombre_proceso, nombre_ejecutable, ruta_ejecutable,
        titulo_ventana, pid, memoria_mb.
    """
    current_user = os.environ.get("USERNAME", "").lower()
    window_entries = _collect_window_entries()
    return _build_app_list(window_entries, current_user)


def construir_muestra() -> dict:
    """Build a full monitoring snapshot for the current moment.

    Returns:
        Dict with hostname, usuario, timestamp_utc (ISO 8601 UTC),
        cantidad_apps, and apps list.
    """
    apps = capturar_apps_en_uso()
    return {
        "hostname": os.environ.get("COMPUTERNAME", socket.gethostname()),
        "usuario": os.environ.get("USERNAME", ""),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "cantidad_apps": len(apps),
        "apps": apps,
    }

def _collect_window_entries() -> list[tuple[str, int]]:
    """Return (title, pid) for each visible top-level window with a non-empty title.

    The callback must return True on every branch; returning None (implicit)
    would cause win32gui.EnumWindows to stop enumeration early.
    """
    entries: list[tuple[str, int]] = []

    def _callback(hwnd: int, _: Any) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid:
                entries.append((title, pid))
        except OSError:
            pass
        return True

    win32gui.EnumWindows(_callback, None)
    return entries


def _build_app_list(
    window_entries: list[tuple[str, int]], current_user: str
) -> list[dict]:
    """Build a deduplicated app list from (title, pid) pairs for current_user only."""
    seen_pids: set[int] = set()
    apps: list[dict] = []

    for title, pid in window_entries:
        if pid in seen_pids:
            continue
        info = _extract_process_info(pid, title, current_user)
        if info is not None:
            seen_pids.add(pid)
            apps.append(info)

    return apps


def _extract_process_info(pid: int, title: str, current_user: str) -> dict | None:
    """Return process info dict if pid belongs to current_user, else None.

    Returns None on psutil.NoSuchProcess, psutil.AccessDenied, or when the
    process belongs to a different user.
    """
    try:
        proc = psutil.Process(pid)
        if proc.username().split("\\")[-1].lower() != current_user:
            return None

        exe_name = proc.name()
        return {
            "nombre_proceso": Path(exe_name).stem,
            "nombre_ejecutable": exe_name,
            "ruta_ejecutable": _safe_exe_path(proc),
            "titulo_ventana": title,
            "pid": pid,
            "memoria_mb": _safe_memory_mb(proc),
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def _safe_exe_path(proc: psutil.Process) -> str | None:
    """Return the executable path as a string, or None if access is denied."""
    try:
        return str(proc.exe())
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return None


def _safe_memory_mb(proc: psutil.Process) -> float:
    """Return RSS memory in MB rounded to 2 decimals, or 0.0 on any error."""
    try:
        return round(proc.memory_info().rss / (1024 * 1024), 2)
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return 0.0


if __name__ == "__main__":
    import json

    print(json.dumps(construir_muestra(), indent=2, ensure_ascii=False))
