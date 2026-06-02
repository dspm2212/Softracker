"""FastAPI application entry point for the monitoring server.

Loads configuration at startup, validates the tokens file, creates
storage directories, and registers all routers.

Author: Daniel Perez
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import load_config
from app.logger import configure_logger
from app.routers import health, status, upload

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Server startup and graceful shutdown."""
    config = load_config()
    app.state.config = config
    app.state.start_time = time.monotonic()

    api_logger = configure_logger(
        "api",
        config.logging.file,
        config.logging.level,
        config.logging.rotate_mb,
        config.logging.backups,
    )

    # Fail fast if the tokens file is missing — no point starting without auth
    if not config.auth.tokens_file.exists():
        raise FileNotFoundError(
            f"Tokens file not found: {config.auth.tokens_file}. "
            "Generate tokens with scripts/generar_tokens.py before starting."
        )

    # Ensure storage directories exist
    base_dir = config.storage.base_dir
    for subdir in (config.storage.parquet_subdir, config.storage.reject_dir, "log"):
        (base_dir / subdir).mkdir(parents=True, exist_ok=True)

    api_logger.info(
        "Server started — base_dir=%s tokens=%s",
        base_dir,
        config.auth.tokens_file,
    )

    try:
        yield
    finally:
        api_logger.info("Server shutting down.")
        # Remove file handlers so tests get a fresh logger on the next run
        for handler in api_logger.handlers[:]:
            handler.close()
            api_logger.removeHandler(handler)


app = FastAPI(
    title="Monitoreo Salas API",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(upload.router)
app.include_router(status.router)
