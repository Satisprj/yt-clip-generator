import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import Job, JobStatus


class JobCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048,
                     examples=["https://www.youtube.com/watch?v=jNQXAC9IVRw"])


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
    failed_stage: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    # Derived (not columns)
    clip_count: int = 0
    ready_clip_count: int = 0
    storage_bytes: int = 0
    has_source_files: bool = False

    @classmethod
    def build(cls, job: Job, stats=None) -> "JobRead":
        data = cls.model_validate(job)
        if stats is not None:
            data = data.model_copy(update={
                "clip_count": stats.clip_count,
                "ready_clip_count": stats.ready_clip_count,
                "storage_bytes": stats.storage_bytes,
                "has_source_files": stats.has_source_files,
            })
        return data


class JobList(BaseModel):
    items: list[JobRead]
    total: int
    limit: int
    offset: int
    storage_bytes_total: int  # disk used by all jobs' files


class ReprocessRequest(BaseModel):
    start_from: Literal["highlights", "render"] = Field(
        description="'highlights': ask Claude for new moments; 'render': re-render the same clips"
    )


class FreeSpaceResult(BaseModel):
    freed_bytes: int
    job: JobRead
