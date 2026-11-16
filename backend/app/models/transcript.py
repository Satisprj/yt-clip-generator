import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import utcnow

JsonList = JSON().with_variant(JSONB(), "postgresql")


class Transcript(Base):
    """Word-level transcript of a job's audio, plus the sentences built from it."""

    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), unique=True
    )
    language: Mapped[str] = mapped_column(String(16))
    language_probability: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[float] = mapped_column(Float)
    model: Mapped[str] = mapped_column(String(64))
    word_count: Mapped[int] = mapped_column(Integer)
    sentence_count: Mapped[int] = mapped_column(Integer)
    # [{"start", "end", "text", "probability"}]
    words: Mapped[list[dict[str, Any]]] = mapped_column(JsonList)
    # [{"id", "start", "end", "text", "first_word", "last_word"}]
    sentences: Mapped[list[dict[str, Any]]] = mapped_column(JsonList)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
