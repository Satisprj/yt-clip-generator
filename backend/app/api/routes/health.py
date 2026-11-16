import logging
from collections.abc import Callable
from time import perf_counter

from fastapi import APIRouter, Response

from app import __version__
from app.core.config import get_settings
from app.schemas.health import CheckResult, HealthResponse, ReadinessResponse, WorkerStatus
from app.services import system_checks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthResponse)
def liveness() -> HealthResponse:
    """The API process is up. Does not touch dependencies."""
    settings = get_settings()
    return HealthResponse(
        status="ok", app=settings.app_name, version=__version__, environment=settings.app_env
    )


@router.get("/worker", response_model=WorkerStatus)
def worker_status() -> WorkerStatus:
    """Cheap check (reads the worker's heartbeat file) for UIs that poll often."""
    from app.worker.heartbeat import STALE_AFTER, heartbeat_age

    age = heartbeat_age()
    return WorkerStatus(alive=age is not None and age <= STALE_AFTER,
                        last_seen_seconds=None if age is None else round(age, 1))


def _run_check(name: str, check: Callable[[], None]) -> CheckResult:
    start = perf_counter()
    try:
        check()
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        logger.warning("Readiness check '%s' failed: %s", name, exc)
        return CheckResult(
            status="error",
            latency_ms=round((perf_counter() - start) * 1000, 1),
            error=str(exc).strip()[:300] or exc.__class__.__name__,
        )
    return CheckResult(status="ok", latency_ms=round((perf_counter() - start) * 1000, 1))


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse, "description": "A dependency is down"}},
)
def readiness(response: Response) -> ReadinessResponse:
    """Checks every dependency the processing pipeline needs."""
    checks = {
        "database": _run_check("database", system_checks.check_database),
        "queue": _run_check("queue", system_checks.check_queue),
        "worker": _run_check("worker", system_checks.check_worker),
        "ffmpeg": _run_check("ffmpeg", system_checks.check_ffmpeg),
        "claude_cli": _run_check("claude_cli", system_checks.check_claude_cli),
    }
    healthy = all(c.status == "ok" for c in checks.values())
    if not healthy:
        response.status_code = 503
    return ReadinessResponse(status="ok" if healthy else "degraded", checks=checks)
