"""Connected accounts (integrations) and publishing clips to them."""

import logging
import uuid
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError, NotFoundError
from app.db.session import get_db
from app.models import Clip, Platform, Publication
from app.schemas.publishing import (
    AccountRead,
    ClipPublishRequest,
    ConnectResponse,
    IntegrationStatus,
    JobPublishRequest,
    PublicationRead,
    PublishResult,
)
from app.services import jobs as job_service
from app.services.publishing import service, youtube

logger = logging.getLogger(__name__)
router = APIRouter(tags=["publishing"])

DbSession = Annotated[Session, Depends(get_db)]


# --- Integrations ------------------------------------------------------------------------


@router.get("/integrations", response_model=list[IntegrationStatus])
def list_integrations(db: DbSession) -> list[IntegrationStatus]:
    settings = get_settings()
    out = []
    for platform in Platform:
        account = service.get_account(db, platform)
        out.append(IntegrationStatus(
            platform=platform,
            supported=platform in service.SUPPORTED,
            configured=(platform is Platform.YOUTUBE and bool(settings.google_client_id)
                        and settings.google_client_secret is not None),
            account=AccountRead.model_validate(account) if account else None,
            note=service.COMING_SOON.get(platform),
        ))
    return out


@router.get("/integrations/youtube/connect", response_model=ConnectResponse)
def youtube_connect() -> ConnectResponse:
    """Google sign-in URL. The frontend sends the browser there."""
    return ConnectResponse(auth_url=youtube.authorization_url())


@router.get("/integrations/youtube/callback", include_in_schema=False)
def youtube_callback(
    db: DbSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Google redirects here after sign-in; we save the account and return to Settings."""
    target = f"{get_settings().frontend_url.rstrip('/')}/settings"
    if error or not code or not state:
        message = "YouTube access was not granted." if error == "access_denied" \
            else f"YouTube sign-in failed ({error or 'missing code'})."
        return RedirectResponse(f"{target}?{urlencode({'error': message})}", status_code=303)
    try:
        account = service.connect_youtube(db, code, state)
    except AppError as exc:
        return RedirectResponse(f"{target}?{urlencode({'error': exc.message})}", status_code=303)
    return RedirectResponse(f"{target}?{urlencode({'connected': account.name})}",
                            status_code=303)


@router.delete("/integrations/{platform}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(platform: Platform, db: DbSession) -> Response:
    service.disconnect(db, platform)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Publishing ----------------------------------------------------------------------------


def _result(queued, existing) -> PublishResult:
    return PublishResult(queued=[PublicationRead.model_validate(p) for p in queued],
                         already_published=[PublicationRead.model_validate(p) for p in existing])


@router.post("/clips/{clip_id}/publish", response_model=PublishResult)
def publish_clip(clip_id: uuid.UUID, payload: ClipPublishRequest, db: DbSession) -> PublishResult:
    """Post one clip to the chosen platforms, with the same title and caption on each."""
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise NotFoundError("Clip not found.")
    queued, existing = service.create_publications(
        db, [clip], payload.platforms, privacy=payload.privacy, title=payload.title,
        caption=payload.caption)
    return _result(queued, existing)


@router.post("/jobs/{job_id}/publish", response_model=PublishResult)
def publish_job(job_id: uuid.UUID, payload: JobPublishRequest, db: DbSession) -> PublishResult:
    """Post every rendered clip of a job (each with its own AI-written caption)."""
    clips = [c for c in job_service.list_clips(db, job_id) if c.render_status == "ready"]
    queued, existing = service.create_publications(db, clips, payload.platforms,
                                                   privacy=payload.privacy)
    return _result(queued, existing)


@router.get("/jobs/{job_id}/publications", response_model=list[PublicationRead])
def job_publications(job_id: uuid.UUID, db: DbSession) -> list[PublicationRead]:
    job_service.get_job(db, job_id)
    rows = db.scalars(select(Publication).where(Publication.job_id == job_id)
                      .order_by(Publication.created_at.desc()))
    return [PublicationRead.model_validate(p) for p in rows]


@router.post("/publications/{publication_id}/retry", response_model=PublicationRead)
def retry_publication(publication_id: uuid.UUID, db: DbSession) -> PublicationRead:
    return PublicationRead.model_validate(service.retry_publication(db, publication_id))


@router.get("/publications", response_model=list[PublicationRead])
def recent_publications(
    db: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[PublicationRead]:
    rows = db.scalars(select(Publication).order_by(Publication.created_at.desc()).limit(limit))
    return [PublicationRead.model_validate(p) for p in rows]
