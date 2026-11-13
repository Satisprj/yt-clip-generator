"""Dependency checks used by the readiness endpoint.

Each check returns None on success and raises on failure.
"""

import shutil

import redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_engine


def check_database() -> None:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))


def check_redis() -> None:
    client = redis.Redis.from_url(
        get_settings().redis_url, socket_connect_timeout=3, socket_timeout=3
    )
    try:
        client.ping()
    finally:
        client.close()


def check_ffmpeg() -> None:
    settings = get_settings()
    missing = [b for b in (settings.ffmpeg_path, settings.ffprobe_path) if shutil.which(b) is None]
    if missing:
        raise RuntimeError(f"Not found: {', '.join(missing)}. Install FFmpeg or set FFMPEG_PATH.")
