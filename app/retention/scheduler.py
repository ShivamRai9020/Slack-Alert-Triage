"""
In-process background scheduler for run_retention().

A single daemon thread: waits a short startup delay, runs retention, then
sleeps retention_run_interval_hours and repeats. Uses its own DB session
per run. stop() wakes it immediately (wired to FastAPI shutdown). Not
started when alert_event_retention_days <= 0.

With several app processes (e.g. multiple uvicorn workers) each runs its
own thread; that's safe -- run_retention only rolls up rows it actually
deleted in the same transaction -- just redundant. For multi-worker
deployments you can instead disable it here (retention days stays >0 but
set RETENTION_RUN_INTERVAL_HOURS=0) and cron scripts/run_retention.py.
"""
from __future__ import annotations

import logging
import threading

from app.config import settings

logger = logging.getLogger(__name__)

STARTUP_DELAY_SECONDS = 60

_thread: threading.Thread | None = None
_stop = threading.Event()


def _run_once() -> None:
    from app.db.session import SessionLocal
    from app.retention.prune import run_retention

    session = SessionLocal()
    try:
        run_retention(session)
    except Exception:  # never let the scheduler thread die
        logger.exception("Retention run failed")
        session.rollback()
    finally:
        session.close()


def _loop(interval_seconds: float, startup_delay: float) -> None:
    if _stop.wait(startup_delay):
        return
    while not _stop.is_set():
        _run_once()
        if _stop.wait(interval_seconds):
            return


def start(startup_delay: float = STARTUP_DELAY_SECONDS) -> threading.Thread | None:
    global _thread
    if settings.alert_event_retention_days <= 0:
        logger.info("Data retention disabled (ALERT_EVENT_RETENTION_DAYS=0).")
        return None
    if settings.retention_run_interval_hours <= 0:
        logger.info("Retention scheduler disabled (RETENTION_RUN_INTERVAL_HOURS<=0); use scripts/run_retention.py.")
        return None
    if _thread and _thread.is_alive():
        return _thread
    _stop.clear()
    _thread = threading.Thread(
        target=_loop,
        args=(settings.retention_run_interval_hours * 3600, startup_delay),
        name="alert-retention",
        daemon=True,
    )
    _thread.start()
    logger.info(
        "Retention scheduler started: keep %s days, run every %sh.",
        settings.alert_event_retention_days,
        settings.retention_run_interval_hours,
    )
    return _thread


def stop() -> None:
    _stop.set()
