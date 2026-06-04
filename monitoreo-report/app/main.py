"""FastAPI entry point for the monitoring report application.

Loads the shared monitoring config and exposes dashboard/report endpoints
against the Parquet files produced by monitoreo-servidor.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import load_config
from app.logger import configure_logger
from app.routers import reports

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Report app startup and graceful shutdown."""
    config = load_config()
    app.state.config = config
    app.state.start_time = time.monotonic()

    report_logger = configure_logger(
        "report",
        config.logging.file,
        config.logging.level,
        config.logging.rotate_mb,
        config.logging.backups,
    )

    base_dir = config.storage.base_dir
    (base_dir / config.storage.parquet_subdir).mkdir(parents=True, exist_ok=True)

    report_logger.info(
        "Report app started - base_dir=%s",
        base_dir,
    )

    try:
        yield
    finally:
        report_logger.info("Report app shutting down.")
        for handler in report_logger.handlers[:]:
            handler.close()
            report_logger.removeHandler(handler)


app = FastAPI(
    title="Monitoreo Salas Reports",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(reports.router)
