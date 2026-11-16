from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    app: str
    version: str
    environment: str


class CheckResult(BaseModel):
    status: Literal["ok", "error"]
    latency_ms: float
    error: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, CheckResult]


class WorkerStatus(BaseModel):
    alive: bool
    last_seen_seconds: float | None  # None = no heartbeat file (worker never started)
