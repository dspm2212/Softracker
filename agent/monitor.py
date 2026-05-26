"""
This module implements the Softracker monitoring agent for Windows client
machines in a university computer lab environment.  It combines two
complementary monitoring strategies:

  1. Foreground tracking: polls the active (foreground) window every
     ``poll_interval`` seconds and accumulates per-application active time.
  2. Snapshot capture: at each transmission, collects all processes that
     own a visible, titled window together with their memory usage.

Both data streams are merged into a single payload and sent via HTTP POST
to the Softracker ingestion API using the sala (room) token for
authentication.  Transmission timing is staggered by sala ID so that
different rooms send at evenly spaced offsets within the reporting cycle,
preventing simultaneous bursts on a server with limited RAM.

When the server is unreachable the payload is saved to a local JSON file
buffer and retransmitted automatically on the next successful connection.

Author: Daniel Santiago Perez Madera <dsperezm@udistrital.edu.co>
"""

import hashlib
import json
import logging
import os
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Optional Windows API imports
# ---------------------------------------------------------------------------

try:
    import psutil
    import win32gui
    import win32process
    WINDOWS_AVAILABLE = True
except ImportError:
    WINDOWS_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

_LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home()))
_AGENT_DATA_DIR = _LOCAL_APP_DATA / "SoftrackerAgent"

DEFAULT_CONFIG: dict = {
    "sala_id": 0,
    "sala_token": "REPLACE_WITH_SALA_TOKEN",
    "api_url": "http://servidor.universidad.edu:8080/v1/ingest",
    "poll_interval": 5,          # seconds between foreground-window polls
    "send_interval": 1500,       # seconds between transmissions (25 min)
    "stagger_seconds": 30,       # per-sala offset step inside the cycle
    "timeout_seconds": 15,       # HTTP request timeout
    "retries": 2,                # extra POST attempts before buffering
    "min_active_seconds": 5,     # discard foreground slices shorter than this
    "buffer_dir": str(_AGENT_DATA_DIR / "buffer"),
    "log_file": str(_AGENT_DATA_DIR / "agent.log"),
    "max_buffer_files": 200,
    "log_locally": True,
}

CONFIG_FILE = Path(__file__).parent / "config.json"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load agent configuration from disk, creating defaults if absent.

    Reads ``config.json`` from the same directory as this module.  Any key
    missing from the on-disk file is filled in from ``DEFAULT_CONFIG`` so
    that existing deployments remain forward-compatible when new keys are
    added.

    Returns:
        dict: Configuration mapping with all required keys populated.
    """
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        for key, value in DEFAULT_CONFIG.items():
            cfg.setdefault(key, value)
        return cfg

    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(DEFAULT_CONFIG, fh, indent=2)
    print(
        f"[Softracker] Config created at {CONFIG_FILE}. "
        "Edit sala_token and api_url before deploying."
    )
    return DEFAULT_CONFIG.copy()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(log_file: str) -> logging.Logger:
    """Initialise file + console logging for the agent.

    Rotates the log file to ``<log_file>.old`` once it exceeds 2 MB.

    Args:
        log_file (str): Absolute path of the log file to write.

    Returns:
        logging.Logger: Configured logger instance named ``softracker``.
    """
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if log_path.exists() and log_path.stat().st_size > 2 * 1024 * 1024:
        log_path.replace(log_path.with_suffix(".log.old"))

    logger = logging.getLogger("softracker")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# ---------------------------------------------------------------------------
# Windows API helpers
# ---------------------------------------------------------------------------

def get_foreground_window() -> Optional[dict]:
    """Return metadata for the currently focused foreground window.

    Uses the Win32 ``GetForegroundWindow`` API to identify the process that
    owns the active window.  Processes running in session 0 (Windows
    services) are excluded because they are never directly driven by a
    logged-in user.

    Returns:
        Optional[dict]: Mapping with keys ``process_name``, ``exe_path``,
        ``window_title``, and ``pid``, or ``None`` when the foreground
        window cannot be identified or belongs to a system service.
    """
    if not WINDOWS_AVAILABLE:
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = psutil.Process(pid)
        if proc.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
            return None
        return {
            "process_name": proc.name(),
            "exe_path": _safe_exe(proc),
            "window_title": win32gui.GetWindowText(hwnd),
            "pid": pid,
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
        return None


def _safe_exe(proc: "psutil.Process") -> Optional[str]:
    """Return the executable path of a process without raising on access errors.

    Args:
        proc (psutil.Process): Process object to query.

    Returns:
        Optional[str]: Absolute path to the executable, or ``None`` if
        access is denied or the process no longer exists.
    """
    try:
        return proc.exe()
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return None


def get_pids_and_titles() -> dict[int, str]:
    """Enumerate all PIDs that own at least one visible, titled window.

    Walks all top-level windows via ``EnumWindows`` and builds a mapping
    of process ID to the title of the first visible window found for that
    process.

    Returns:
        dict[int, str]: Mapping ``{pid: window_title}`` for every process
        with a non-empty, visible top-level window.
    """
    result: dict[int, str] = {}

    def _callback(hwnd: int, _: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid not in result:
                result[pid] = title
        except Exception:
            pass

    win32gui.EnumWindows(_callback, None)
    return result


def get_open_apps(accumulated: dict[str, float]) -> list[dict]:
    """Build a snapshot of all running applications with visible windows.

    Calls ``get_pids_and_titles`` to find candidate processes, then
    enriches each record with psutil data (memory, executable path) and
    with the accumulated foreground-active seconds stored in *accumulated*.

    Args:
        accumulated (dict[str, float]): Mapping of ``process_name`` to
            total foreground seconds observed during the current reporting
            interval.

    Returns:
        list[dict]: Application records with keys ``nombre_proceso``,
        ``nombre_ejecutable``, ``ruta_ejecutable``, ``titulo_ventana``,
        ``pid``, ``memoria_mb``, ``duracion_activa_seg``, and
        ``es_app_activa``.
    """
    if not WINDOWS_AVAILABLE:
        return []

    pid_titles = get_pids_and_titles()
    apps: list[dict] = []

    for pid, title in pid_titles.items():
        try:
            proc = psutil.Process(pid)
            if proc.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                continue
            name = proc.name()
            active_seg = accumulated.get(name, 0.0)
            apps.append({
                "nombre_proceso": name,
                "nombre_ejecutable": name,
                "ruta_ejecutable": _safe_exe(proc),
                "titulo_ventana": title,
                "pid": pid,
                "memoria_mb": round(proc.memory_info().rss / 1_048_576, 2),
                "duracion_activa_seg": round(active_seg, 1),
                "es_app_activa": active_seg > 0,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return apps


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------

def build_sample(apps: list[dict], config: dict) -> dict:
    """Construct the full ingestion payload from a snapshot of app records.

    Args:
        apps (list[dict]): Application records produced by
            ``get_open_apps``.
        config (dict): Agent configuration; ``machine_name`` is used as
            the hostname field.

    Returns:
        dict: Complete payload ready for JSON serialisation and HTTP POST
        to ``/v1/ingest``.
    """
    return {
        "hostname": config.get("machine_name", socket.gethostname()),
        "timestamp_local": datetime.now(timezone.utc).isoformat(),
        "usuario_activo": os.environ.get("USERNAME", ""),
        "sesion_activa": True,
        "apps": apps,
    }


# ---------------------------------------------------------------------------
# Staggered transmission
# ---------------------------------------------------------------------------

def compute_stagger_wait(stagger_offset: int, send_interval: int) -> float:
    """Compute the delay before the next clock-aligned transmission slot.

    Aligns each sala's send time to a fixed offset within every
    *send_interval* cycle.  Agents that restart mid-cycle automatically
    recalculate and wait only until the next occurrence of their slot.

    Example:
        With ``send_interval=1500`` and ``stagger_offset=90`` (sala 3
        using 30 s steps), the agent sends at seconds 90, 1590, 3090 …
        of the Unix epoch, distributing load evenly across 50 slots.

    Args:
        stagger_offset (int): Target second within the cycle for this sala,
            computed as ``(sala_id * stagger_seconds) % send_interval``.
        send_interval (int): Duration of the reporting cycle in seconds.

    Returns:
        float: Seconds to sleep before the first transmission.
    """
    now_mod = time.time() % send_interval
    if now_mod <= stagger_offset:
        return stagger_offset - now_mod
    return send_interval - now_mod + stagger_offset


# ---------------------------------------------------------------------------
# Server communication
# ---------------------------------------------------------------------------

def send_to_server(sample: dict, config: dict) -> bool:
    """POST a monitoring sample to the ingestion API.

    Authenticates with the sala token via the ``X-Auth-Token`` header.
    Retries up to ``config['retries']`` times with exponential back-off
    before giving up.

    Args:
        sample (dict): Payload produced by ``build_sample``.
        config (dict): Agent configuration containing ``api_url``,
            ``sala_token``, ``timeout_seconds``, and ``retries``.

    Returns:
        bool: ``True`` if the server returned a 2xx response,
        ``False`` after all retry attempts fail.
    """
    if not REQUESTS_AVAILABLE or not config.get("api_url"):
        return False

    log = logging.getLogger("softracker")
    headers = {
        "X-Auth-Token": config["sala_token"],
        "Content-Type": "application/json",
    }
    url = config["api_url"]
    body = json.dumps(sample, ensure_ascii=False)

    for attempt in range(config.get("retries", 2) + 1):
        try:
            resp = requests.post(
                url,
                data=body,
                headers=headers,
                timeout=config.get("timeout_seconds", 15),
            )
            resp.raise_for_status()
            log.info("[Server] %d apps sent OK → %s", len(sample.get("apps", [])), url)
            return True
        except Exception as exc:
            log.warning("[Server] Attempt %d failed: %s", attempt + 1, exc)
            if attempt < config.get("retries", 2):
                time.sleep(2 * (attempt + 1))

    return False


# ---------------------------------------------------------------------------
# Local buffer
# ---------------------------------------------------------------------------

def save_to_buffer(sample: dict, config: dict) -> None:
    """Persist a sample to the local file buffer when the server is down.

    Evicts the oldest files once the buffer reaches ``max_buffer_files``
    to cap disk usage.  Each sample is stored in a timestamped JSON file.

    Args:
        sample (dict): Payload to persist.
        config (dict): Agent configuration containing ``buffer_dir`` and
            ``max_buffer_files``.
    """
    log = logging.getLogger("softracker")
    buf_dir = Path(config["buffer_dir"])
    buf_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(buf_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    max_files = config.get("max_buffer_files", 200)
    while len(files) >= max_files:
        files.pop(0).unlink(missing_ok=True)
        log.warning("[Buffer] Evicted oldest file (limit=%d)", max_files)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:6]
    path = buf_dir / f"sample_{ts}_{uid}.json"
    path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    log.info("[Buffer] Saved sample → %s", path.name)


def flush_buffer(config: dict) -> None:
    """Retransmit all buffered samples to the server in chronological order.

    Stops as soon as one transmission fails so as not to hammer an
    unreachable server.  Files that are successfully sent are deleted
    immediately; corrupted files are deleted to avoid blocking the queue.

    Args:
        config (dict): Agent configuration containing ``buffer_dir``,
            ``api_url``, ``sala_token``, and ``timeout_seconds``.
    """
    log = logging.getLogger("softracker")
    buf_dir = Path(config["buffer_dir"])
    if not buf_dir.exists():
        return

    files = sorted(buf_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return

    log.info("[Buffer] Attempting to flush %d buffered sample(s)", len(files))
    for file_path in files:
        try:
            sample = json.loads(file_path.read_text(encoding="utf-8"))
            if send_to_server(sample, config):
                file_path.unlink(missing_ok=True)
            else:
                log.warning("[Buffer] Flush stopped at %s; server unreachable", file_path.name)
                break
        except json.JSONDecodeError:
            log.error("[Buffer] Corrupted file removed: %s", file_path.name)
            file_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run() -> None:
    """Start the monitoring agent main loop.

    Execution flow:
      1. Load configuration and set up logging.
      2. Sleep for the sala stagger offset so transmission slots are evenly
         distributed across the reporting cycle.
      3. Enter the tracking loop:
           - Every ``poll_interval`` seconds: sample the foreground window
             and accumulate active time per process.
           - Every ``send_interval`` seconds: flush buffered samples,
             capture a full app snapshot, build the payload, and transmit
             (or buffer if the server is down).

    The loop runs until the process receives a ``KeyboardInterrupt``.

    Raises:
        SystemExit: When ``pywin32`` or ``psutil`` are not installed.
    """
    config = load_config()
    log = _setup_logging(config["log_file"])

    machine = config.get("machine_name", socket.gethostname())
    log.info("Softracker started | Machine: %s | Sala ID: %s", machine, config["sala_id"])

    if not WINDOWS_AVAILABLE:
        log.error("pywin32 and psutil are required. Run: pip install pywin32 psutil")
        raise SystemExit(1)

    if not REQUESTS_AVAILABLE:
        log.warning("requests not installed; data will be buffered locally only.")

    # --- Stagger delay ---------------------------------------------------
    stagger_offset = (config["sala_id"] * config["stagger_seconds"]) % config["send_interval"]
    wait = compute_stagger_wait(stagger_offset, config["send_interval"])
    if wait > 0:
        log.info(
            "Stagger wait: %.0fs (sala_id=%d, offset=%ds in %ds cycle)",
            wait, config["sala_id"], stagger_offset, config["send_interval"],
        )
        time.sleep(wait)

    # --- Tracking state --------------------------------------------------
    accumulated: dict[str, float] = {}   # process_name → foreground seconds
    current_process: Optional[str] = None
    last_poll = time.time()
    last_send = time.time()

    log.info(
        "Tracking started | poll=%ds | send=%ds",
        config["poll_interval"], config["send_interval"],
    )

    try:
        while True:
            now = time.time()
            elapsed = now - last_poll
            last_poll = now

            # Accumulate time for the process that was active last tick
            if current_process and elapsed <= config["poll_interval"] * 2:
                accumulated[current_process] = (
                    accumulated.get(current_process, 0.0) + elapsed
                )

            active = get_foreground_window()
            current_process = active["process_name"] if active else None

            # --- Transmission slot ---------------------------------------
            if now - last_send >= config["send_interval"]:
                flush_buffer(config)

                apps = get_open_apps(accumulated)
                sample = build_sample(apps, config)

                total_active = sum(a["duracion_activa_seg"] for a in apps)
                log.info(
                    "Sending sample: %d apps | %.0fs total active time",
                    len(apps), total_active,
                )

                if send_to_server(sample, config):
                    pass
                else:
                    if config.get("log_locally", True):
                        save_to_buffer(sample, config)

                accumulated.clear()
                last_send = now

            time.sleep(config["poll_interval"])

    except KeyboardInterrupt:
        log.info("Softracker stopped by user.")


if __name__ == "__main__":
    run()
