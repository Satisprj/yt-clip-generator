import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import utcnow
from app.models.transcript import JsonList


class Clip(Base):
    """A selected highlight. Rendering (Phase 5) adds the output files."""

    __tablename__ = "clips"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    rank: Mapped[int] = mapped_column(Integer)

    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[float] = mapped_column(Float)
    first_sentence: Mapped[int] = mapped_column(Integer)
    last_sentence: Mapped[int] = mapped_column(Integer)
    transcript_text: Mapped[str] = mapped_column(Text)

    category: Mapped[str] = mapped_column(String(32))
    viral_score: Mapped[float] = mapped_column(Float)
    scores: Mapped[dict[str, Any]] = mapped_column(JsonList)
    hook: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)

    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    hashtags: Mapped[list[str]] = mapped_column(JsonList)

    # --- Rendering (files live in storage/jobs/<job_id>/clips/) ---
    render_status: Mapped[str] = mapped_column(String(16), default="pending",
                                               server_default="pending")
    render_error: Mapped[str | None] = mapped_column(Text)
    layout: Mapped[str | None] = mapped_column(String(16))  # "track" or "fit"
    video_file: Mapped[str | None] = mapped_column(String(255))
    subtitle_file: Mapped[str | None] = mapped_column(String(255))
    thumbnail_file: Mapped[str | None] = mapped_column(String(255))
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
