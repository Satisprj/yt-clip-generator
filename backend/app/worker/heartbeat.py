"""Worker heartbeat and periodic maintenance.

The Celery worker on Windows runs one task at a time (--pool=solo), and the PostgreSQL
broker doesn't support Celery's remote "ping". So a small background thread in the worker
writes `storage/worker_heartbeat.json` every few seconds; the API reads it to tell whether
a worker is running. The same thread runs the cleanup job periodically.
"""

import json
import logging
import os
import socket
import threading
import time
from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 15.0  # seconds between writes
STALE_AFTER = 60.0         # older than this = no worker running


def heartbeat_path():
    return get_settings().storage_dir / "worker_heartbeat.json"


def write_heartbeat(started_at: str) -> None:
    path = heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "host": socket.gethostname(), "started_at": started_at,
               "updated_at": datetime.now(UTC).isoformat(), "unix": time.time()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    try:
        tmp.replace(path)
    except PermissionError:  # the API is reading it right now (Windows); next tick succeeds
        tmp.unlink(missing_ok=True)


def read_heartbeat() -> dict[str, Any] | None:
    try:
        return json.loads(heartbeat_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def heartbeat_age() -> float | None:
    """Seconds since the last heartbeat, or None if there has never been one."""
    data = read_heartbeat()
    if not data or "unix" not in data:
        return None
    return max(0.0, time.time() - float(data["unix"]))


def worker_alive() -> bool:
    age = heartbeat_age()
    return age is not None and age <= STALE_AFTER


class MaintenanceThread(threading.Thread):
    """Writes the heartbeat and runs cleanup every CLEANUP_INTERVAL_HOURS."""

    def __init__(self) -> None:
        super().__init__(name="clipgen-maintenance", daemon=True)
        self._stop_event = threading.Event()
        self._started_at = datetime.now(UTC).isoformat()

    def run(self) -> None:
        from app.services.cleanup import run_cleanup

        interval = get_settings().cleanup_interval_hours * 3600
        next_cleanup = time.monotonic() + 60  # first cleanup shortly after start
        while not self._stop_event.is_set():
            try:
                write_heartbeat(self._started_at)
            except OSError:
                logger.exception("Could not write the worker heartbeat")
            if interval > 0 and time.monotonic() >= next_cleanup:
                try:
                    run_cleanup()
                except Exception:  # never let maintenance kill the worker
                    logger.exception("Cleanup failed")
                next_cleanup = time.monotonic() + interval
            self._stop_event.wait(HEARTBEAT_INTERVAL)

    def stop(self) -> None:
        self._stop_event.set()
        heartbeat_path().unlink(missing_ok=True)
