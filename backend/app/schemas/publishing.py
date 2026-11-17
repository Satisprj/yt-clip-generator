import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import Platform, PublicationStatus


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    platform: Platform
    external_id: str
    name: str
    avatar_url: str | None
    connected_at: datetime


class IntegrationStatus(BaseModel):
    platform: Platform
    supported: bool          # implemented in this version
    configured: bool         # developer credentials present in .env
    connect_group: str       # youtube | meta (Facebook + Instagram together) | tiktok
    account: AccountRead | None
    note: str | None = None


class ConnectResponse(BaseModel):
    auth_url: str


Privacy = Literal["private", "unlisted", "public"]


class JobPublishRequest(BaseModel):
    platforms: list[Platform] = Field(min_length=1)
    privacy: Privacy | None = None


class ClipPublishRequest(JobPublishRequest):
    # Optional overrides: the same title/caption goes to every selected platform.
    title: str | None = Field(default=None, max_length=200)
    caption: str | None = Field(default=None, max_length=5000)


class PublicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    clip_id: uuid.UUID
    job_id: uuid.UUID
    platform: Platform
    status: PublicationStatus
    progress: int
    privacy: str
    title: str
    caption: str
    url: str | None
    error_message: str | None
    created_at: datetime
    published_at: datetime | None


class PublishResult(BaseModel):
    queued: list[PublicationRead]
    already_published: list[PublicationRead]  # skipped: queued, uploading or posted before
