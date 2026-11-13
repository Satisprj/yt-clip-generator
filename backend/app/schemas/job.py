import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import JobStatus


class JobCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048, examples=["https://www.youtube.com/watch?v=jNQXAC9IVRw"])


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    youtube_url: str
    youtube_video_id: str
    title: str | None
    channel: str | None
    duration_seconds: int | None
    thumbnail_url: str | None
    status: JobStatus
    progress: int
    stage_message: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class JobList(BaseModel):
    items: list[JobRead]
    total: int
    limit: int
    offset: int
