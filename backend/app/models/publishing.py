import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import utcnow


class Platform(StrEnum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    TIKTOK = "tiktok"


class PublicationStatus(StrEnum):
    QUEUED = "queued"
    UPLOADING = "uploading"
    PUBLISHED = "published"
    FAILED = "failed"


def _enum(cls):
    return Enum(cls, native_enum=False, length=16, values_callable=lambda e: [m.value for m in e])


class PlatformAccount(Base):
    """A connected social account. Tokens are stored encrypted (services/publishing/secrets)."""

    __tablename__ = "platform_accounts"
    __table_args__ = (UniqueConstraint("platform"),)  # one account per platform for now

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    platform: Mapped[Platform] = mapped_column(_enum(Platform))
    external_id: Mapped[str] = mapped_column(String(128))   # e.g. YouTube channel id
    name: Mapped[str] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str] = mapped_column(Text, default="")
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Publication(Base):
    """One clip posted (or being posted) to one platform."""

    __tablename__ = "publications"
    __table_args__ = (
        # A clip can't be posted twice to the same account (failed attempts don't count).
        Index("uq_publications_active", "clip_id", "account_id", unique=True,
              postgresql_where=text("status <> 'failed'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"),
                                               index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"),
                                              index=True)
    # Kept (as NULL) when the account is disconnected, so the history and links remain.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("platform_accounts.id", ondelete="SET NULL"))
    platform: Mapped[Platform] = mapped_column(_enum(Platform))
    status: Mapped[PublicationStatus] = mapped_column(_enum(PublicationStatus),
                                                      default=PublicationStatus.QUEUED)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    privacy: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(255))
    caption: Mapped[str] = mapped_column(Text)
    remote_id: Mapped[str | None] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
