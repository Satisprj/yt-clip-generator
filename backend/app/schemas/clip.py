import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ClipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    rank: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    first_sentence: int
    last_sentence: int
    transcript_text: str
    category: str
    viral_score: float
    scores: dict[str, int]
    hook: str
    reason: str
    title: str
    description: str
    hashtags: list[str]
    render_status: str  # pending | ready | failed
    render_error: str | None
    layout: str | None  # track (follows the face) | fit (full frame on blurred background)
    file_size_bytes: int | None
    created_at: datetime
