"""
HTTP upload module for the monitoring agent.

Sends consolidated Parquet files to the monitoring API using multipart
form data with exponential-backoff retries. Set MOCK_API=1 to skip all
network calls during development.

Author: Daniel Perez
"""

import logging
import os
import time
from pathlib import Path

import requests

from agente.almacenamiento import listar_pendientes, mover_a_enviados

_log = logging.getLogger(__name__)


def enviar_archivo(
    archivo: Path,
    api_url: str,
    token: str,
    sala_codigo: str,
    timeout_seg: int,
    reintentos: int,
) -> bool:
    """Upload a Parquet file to the monitoring API via multipart POST.

    Retries up to `reintentos` times with exponential backoff (2s, 4s, 8s…).
    When MOCK_API=1 is set, skips the network call and returns True immediately.

    Args:
        archivo: Path to the Parquet file to upload.
        api_url: Full URL of the upload endpoint.
        token: Shared agent token sent as X-Auth-Token header.
        sala_codigo: Room code sent as a form field.
        timeout_seg: Per-attempt request timeout in seconds.
        reintentos: Number of retry attempts after the first try.

    Returns:
        True if the server responded with HTTP 200 or 201.
        False if every attempt failed.
    """
    if os.environ.get("MOCK_API") == "1":
        _log.info("MOCK: skipping upload of %s to %s", archivo.name, api_url)
        return True

    for attempt in range(reintentos + 1):
        if attempt > 0:
            wait_secs = 2 ** attempt  # retry 1→2s, retry 2→4s, retry 3→8s
            _log.info("Retry %d/%d — waiting %ds", attempt, reintentos, wait_secs)
            time.sleep(wait_secs)

        _log.info(
            "Sending %s (attempt %d/%d)", archivo.name, attempt + 1, reintentos + 1
        )
        try:
            with archivo.open("rb") as fh:
                response = requests.post(
                    api_url,
                    headers={"X-Auth-Token": token},
                    files={"archivo": (archivo.name, fh, "application/octet-stream")},
                    data={"sala_codigo": sala_codigo},
                    timeout=timeout_seg,
                )
            if response.status_code in (200, 201):
                _log.info("Uploaded %s — HTTP %d", archivo.name, response.status_code)
                return True
            _log.warning(
                "Attempt %d failed — HTTP %d", attempt + 1, response.status_code
            )
        except requests.RequestException as exc:
            _log.warning("Attempt %d failed — %s", attempt + 1, exc)

    _log.error("All %d attempt(s) failed for %s", reintentos + 1, archivo.name)
    return False


def procesar_pendientes(datos_dir: Path, cfg: dict) -> dict:
    """Send every pending Parquet file; move successfully sent ones to enviados/.

    Args:
        datos_dir: Root data directory (Path, not string).
        cfg: Agent configuration dict. Required keys: api_url, token,
            sala_codigo, timeout_envio_seg, reintentos_envio.

    Returns:
        Stats dict: {"enviados": int, "fallidos": int, "total": int}.
    """
    pending = listar_pendientes(datos_dir)
    stats: dict[str, int] = {"enviados": 0, "fallidos": 0, "total": len(pending)}

    for archivo in pending:
        success = enviar_archivo(
            archivo=archivo,
            api_url=cfg["api_url"],
            token=cfg["token"],
            sala_codigo=cfg["sala_codigo"],
            timeout_seg=cfg["timeout_envio_seg"],
            reintentos=cfg["reintentos_envio"],
        )
        if success:
            mover_a_enviados(archivo, datos_dir)
            stats["enviados"] += 1
        else:
            stats["fallidos"] += 1

    return stats
