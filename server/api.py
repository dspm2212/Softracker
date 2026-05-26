"""
This module implements the Softracker ingestion API.  It accepts monitoring
samples from agents running on Windows client machines, authenticates each
request using a per-sala (room) token, and persists the data to a
PostgreSQL database.

The API is intentionally write-heavy on the ingestion side and read-only
for analysis consumers.  A psycopg connection pool handles concurrent
requests efficiently even with hundreds of agents reporting simultaneously.

Endpoints
---------
GET  /health        Liveness and database connectivity check.
POST /v1/ingest     Accept a monitoring sample from one agent.

Authentication
--------------
Every POST must include the header ``X-Auth-Token: <sala-token>``.
Tokens are stored as SHA-256 hashes; plaintext values never touch the DB.

Deployment
----------
Production (Linux systemd)::

    uvicorn api:app --host 0.0.0.0 --port 8080 --workers 2

Environment variables are loaded from ``/etc/monitoreo_salas/api.env``
by the systemd unit.  See ``systemd/api.env.example`` for required keys.

Author: Daniel Santiago Perez Madera <dsperezm@udistrital.edu.co>
"""

import hashlib
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import psycopg
from fastapi import FastAPI, Header, HTTPException, Request, status
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "5432"))
DB_NAME     = os.getenv("DB_NAME", "universidad")
DB_USER     = os.getenv("DB_USER", "monitor_apps_rw")
DB_PASSWORD = os.environ["DB_PASSWORD"]          # required; fail fast if absent
DB_SCHEMA   = os.getenv("DB_SCHEMA", "monitoreo_apps")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("softracker.api")

DSN = (
    f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} "
    f"user={DB_USER} password={DB_PASSWORD} "
    f"options='-c search_path={DB_SCHEMA},public'"
)

pool: Optional[ConnectionPool] = None


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Manage the connection-pool lifecycle tied to the FastAPI application.

    Initialises the psycopg ``ConnectionPool`` at startup and closes it
    cleanly on shutdown to release all database connections.

    Args:
        application (FastAPI): The FastAPI application instance (unused
            directly, required by the lifespan protocol).

    Yields:
        None: Control is yielded to the application while it is running.
    """
    global pool
    pool = ConnectionPool(DSN, min_size=2, max_size=20, kwargs={"autocommit": False})
    pool.wait()
    log.info("Database connection pool ready (min=2, max=20)")
    yield
    pool.close()
    log.info("Database connection pool closed")


app = FastAPI(
    title="Softracker Ingestion API",
    version="2.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class AppDetectada(BaseModel):
    """Represents a single running application detected on a client machine.

    Attributes:
        nombre_proceso: Process name as reported by the OS (e.g. ``chrome``).
        nombre_ejecutable: Filename of the executable (e.g. ``chrome.exe``).
        ruta_ejecutable: Full path to the executable, if accessible.
        titulo_ventana: Title of the main window owned by the process.
        pid: OS process identifier.
        memoria_mb: Resident set size in megabytes.
        duracion_activa_seg: Seconds the process was in the foreground
            during the reporting interval.  ``None`` for agents that do not
            support foreground tracking.
        es_app_activa: ``True`` when this process accumulated any foreground
            time during the reporting interval.
    """

    nombre_proceso: str = Field(..., max_length=255)
    nombre_ejecutable: Optional[str] = Field(None, max_length=255)
    ruta_ejecutable: Optional[str] = None
    titulo_ventana: Optional[str] = None
    pid: Optional[int] = None
    memoria_mb: Optional[float] = None
    duracion_activa_seg: Optional[float] = Field(None, ge=0)
    es_app_activa: bool = False


class MuestraIn(BaseModel):
    """Payload sent by the agent in a single POST to ``/v1/ingest``.

    Attributes:
        hostname: Client machine name, automatically upper-cased.
        timestamp_local: UTC timestamp when the agent captured the data.
        usuario_activo: Windows username of the logged-in user.
        sesion_activa: Whether an interactive user session was open.
        apps: List of running applications; capped at 500 entries.
    """

    hostname: str = Field(..., max_length=100)
    timestamp_local: datetime
    usuario_activo: Optional[str] = Field(None, max_length=100)
    sesion_activa: bool = True
    apps: list[AppDetectada] = Field(default_factory=list, max_length=500)

    @field_validator("hostname")
    @classmethod
    def normalise_hostname(cls, value: str) -> str:
        """Strip whitespace and upper-case the hostname for consistent lookup.

        Args:
            value (str): Raw hostname string from the agent.

        Returns:
            str: Trimmed, upper-cased hostname.
        """
        return value.strip().upper()


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def hash_token(token: str) -> str:
    """Compute the SHA-256 hex digest of a plaintext sala token.

    Args:
        token (str): Plaintext token string.

    Returns:
        str: 64-character lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_token(token: str, ip_origin: str) -> int:
    """Look up a sala token in the database and return its sala ID.

    Verifies that the token hash exists, the token is active, and has
    not expired.  Failed attempts are logged to ``log_ingesta_errores``
    for auditing.  On success, the ``ultimo_uso`` timestamp is updated.

    Args:
        token (str): Plaintext token from the ``X-Auth-Token`` header.
        ip_origin (str): IP address of the requesting client, stored in
            the error log when authentication fails.

    Returns:
        int: The ``sala_id`` associated with the valid token.

    Raises:
        HTTPException: HTTP 401 when the token is not found, inactive, or
            expired.
    """
    token_hash = hash_token(token)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT sala_id FROM tokens_ingesta
            WHERE token_hash = %s
              AND activo = TRUE
              AND (expira_en IS NULL OR expira_en > NOW())
            """,
            (token_hash,),
        )
        row = cur.fetchone()
        if not row:
            cur.execute(
                """
                INSERT INTO log_ingesta_errores (ip_origen, motivo, detalles)
                VALUES (%s, 'token_invalido', %s)
                """,
                (ip_origin, f"hash_prefix={token_hash[:16]}…"),
            )
            conn.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token.",
            )
        cur.execute(
            "UPDATE tokens_ingesta SET ultimo_uso = NOW() WHERE token_hash = %s",
            (token_hash,),
        )
        conn.commit()
        return row[0]


def get_or_create_equipo(sala_id: int, hostname: str, ip_origin: str) -> int:
    """Resolve the database ID of a client machine, auto-registering if new.

    New machines that appear for the first time (e.g. from a freshly
    cloned image) are inserted automatically so that no pre-provisioning
    step is required.

    Args:
        sala_id (int): Room to which this machine belongs.
        hostname (str): Normalised hostname of the client machine.
        ip_origin (str): Client IP address, updated on every contact.

    Returns:
        int: The ``equipo_id`` primary key for this machine.
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT equipo_id FROM equipos WHERE sala_id = %s AND hostname = %s",
            (sala_id, hostname),
        )
        row = cur.fetchone()
        if row:
            return row[0]

        cur.execute(
            """
            INSERT INTO equipos
              (sala_id, hostname, ip, primer_reporte, ultimo_reporte)
            VALUES (%s, %s, %s, NOW(), NOW())
            ON CONFLICT (sala_id, hostname) DO UPDATE SET ip = EXCLUDED.ip
            RETURNING equipo_id
            """,
            (sala_id, hostname, ip_origin),
        )
        equipo_id = cur.fetchone()[0]
        conn.commit()
        log.info("Auto-registered machine: sala=%d host=%s id=%d", sala_id, hostname, equipo_id)
        return equipo_id


def compute_dedupe_key(equipo_id: int, timestamp: datetime) -> str:
    """Generate a deduplication key for a (machine, timestamp) pair.

    The key is a SHA-256 digest of the equipo ID and the ISO timestamp
    string.  It is stored in the ``muestras`` table to silently discard
    duplicate submissions (e.g. from an agent that retransmits a buffered
    sample after a partial failure).

    Args:
        equipo_id (int): Database ID of the reporting machine.
        timestamp (datetime): Timestamp when the agent captured the sample.

    Returns:
        str: 64-character hexadecimal deduplification key.
    """
    raw = f"{equipo_id}|{timestamp.isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """Return the liveness and database connectivity status.

    Returns:
        dict: ``{"status": "ok", "db": "ok"}`` when healthy, or raises
        HTTP 503 when the database is unreachable.

    Raises:
        HTTPException: HTTP 503 when a database query fails.
    """
    try:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"status": "ok", "db": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")


@app.post("/v1/ingest", status_code=201)
def ingest(
    muestra: MuestraIn,
    request: Request,
    x_auth_token: str = Header(..., alias="X-Auth-Token"),
):
    """Accept a monitoring sample from a Softracker agent.

    Processing steps:
      1. Validate the sala token.
      2. Resolve (or auto-register) the client machine.
      3. Insert the sample and its application records in one transaction,
         using the deduplication key to silently discard retransmissions.
      4. Update the machine's ``ultimo_reporte`` timestamp.

    Args:
        muestra (MuestraIn): Validated request body from the agent.
        request (Request): FastAPI request object, used to read the client
            IP address.
        x_auth_token (str): Sala authentication token from the HTTP header.

    Returns:
        dict: Summary of the accepted sample, including ``muestra_id`` and
        ``apps_guardadas``, or ``{"status": "duplicado_ignorado"}`` when
        the deduplication key already exists.

    Raises:
        HTTPException: HTTP 401 for invalid tokens, HTTP 500 for database
            errors.
    """
    ip_origin = request.client.host if request.client else "unknown"

    sala_id   = validate_token(x_auth_token, ip_origin)
    equipo_id = get_or_create_equipo(sala_id, muestra.hostname, ip_origin)
    dedupe    = compute_dedupe_key(equipo_id, muestra.timestamp_local)

    try:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO muestras
                  (equipo_id, timestamp_local, usuario_activo,
                   sesion_activa, dedupe_key)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (equipo_id, dedupe_key) DO NOTHING
                RETURNING muestra_id
                """,
                (
                    equipo_id,
                    muestra.timestamp_local,
                    muestra.usuario_activo,
                    muestra.sesion_activa,
                    dedupe,
                ),
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return {"status": "duplicado_ignorado"}

            muestra_id = row[0]

            if muestra.apps:
                cur.executemany(
                    """
                    INSERT INTO aplicaciones_detectadas
                      (muestra_id, nombre_proceso, nombre_ejecutable,
                       ruta_ejecutable, titulo_ventana, pid, memoria_mb,
                       duracion_activa_seg, es_app_activa)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            muestra_id,
                            a.nombre_proceso,
                            a.nombre_ejecutable,
                            a.ruta_ejecutable,
                            a.titulo_ventana,
                            a.pid,
                            a.memoria_mb,
                            a.duracion_activa_seg,
                            a.es_app_activa,
                        )
                        for a in muestra.apps
                    ],
                )

            cur.execute(
                "UPDATE equipos SET ultimo_reporte = NOW(), ip = %s WHERE equipo_id = %s",
                (ip_origin, equipo_id),
            )
            conn.commit()

        return {
            "status": "ok",
            "muestra_id": muestra_id,
            "apps_guardadas": len(muestra.apps),
        }

    except psycopg.Error:
        log.exception("Database error while inserting sample")
        raise HTTPException(status_code=500, detail="Internal server error.")
