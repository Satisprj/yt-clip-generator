"""Dependency checks used by the readiness endpoint.

Each check returns None on success and raises on failure.
"""

import shutil

import redis
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.session import get_engine
from app.services.llm import check_cli_version


def check_database() -> None:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))


def check_queue() -> None:
    """Check the Celery broker: Redis, or PostgreSQL via SQLAlchemy (`sqla+...`)."""
    url = get_settings().broker_url
    if url.startswith(("redis://", "rediss://")):
        client = redis.Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3)
        try:
            client.ping()
        finally:
            client.close()
    elif url.startswith("sqla+"):
        # Reuse the app engine when the broker is the app database; otherwise connect once.
        db_url = url.removeprefix("sqla+")
        if db_url == get_settings().database_url:
            check_database()
            return
        engine = create_engine(db_url, poolclass=NullPool)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            engine.dispose()
    else:
        raise RuntimeError(f"Unsupported broker URL scheme: {url.split(':', 1)[0]}")


def check_worker() -> None:
    from app.worker.heartbeat import STALE_AFTER, heartbeat_age

    age = heartbeat_age()
    if age is None:
        raise RuntimeError("No worker is running. Start it with scripts\\dev.ps1.")
    if age > STALE_AFTER:
        raise RuntimeError(f"The worker stopped responding {int(age)} s ago. Restart it.")


def check_claude_cli() -> None:
    check_cli_version(get_settings().claude_cli_path)


def check_ffmpeg() -> None:
    settings = get_settings()
    missing = [b for b in (settings.ffmpeg_path, settings.ffprobe_path) if shutil.which(b) is None]
    if missing:
        raise RuntimeError(f"Not found: {', '.join(missing)}. Install FFmpeg or set FFMPEG_PATH.")
