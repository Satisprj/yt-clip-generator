"""Logging setup shared by the API and the Celery worker."""

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging once; safe to call multiple times."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    if not any(getattr(h, "_clipgen", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler._clipgen = True  # type: ignore[attr-defined]
        root.addHandler(handler)

    # Keep noisy libraries quiet unless debugging.
    for noisy in ("httpx", "httpcore", "urllib3", "faster_whisper"):
        logging.getLogger(noisy).setLevel(max(root.level, logging.WARNING))
