"""Per-stage context: database session, job, and throttled progress reporting."""

import time

from sqlalchemy.orm import Session

from app.models import Job


class StageContext:
    """Passed to every stage.

    `report(fraction, message)` maps the stage's own 0..1 progress onto the job's overall
    percentage and saves it, at most once every `min_interval` seconds (plus the final 100%).
    """

    def __init__(
        self,
        db: Session,
        job: Job,
        *,
        start_percent: float,
        span_percent: float,
        min_interval: float = 1.5,
    ) -> None:
        self.db = db
        self.job = job
        self._start = start_percent
        self._span = span_percent
        self._min_interval = min_interval
        self._last_save = 0.0

    def report(self, fraction: float, message: str | None = None) -> None:
        fraction = max(0.0, min(1.0, fraction))
        now = time.monotonic()
        if fraction < 1.0 and now - self._last_save < self._min_interval:
            return
        percent = int(self._start + self._span * fraction)
        self.job.progress = max(self.job.progress, min(percent, 99))
        if message:
            self.job.stage_message = message[:500]
        self.db.commit()
        self._last_save = now
