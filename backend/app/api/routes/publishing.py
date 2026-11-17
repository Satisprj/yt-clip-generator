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
from app.services.publishing import meta, service, tiktok, youtube

logger = logging.getLogger(__name__)
router = APIRouter(tags=["publishing"])

DbSession = Annotated[Session, Depends(get_db)]


# --- Integrations ------------------------------------------------------------------------


def _configured(platform: Platform) -> bool:
    s = get_settings()
    group = service.CONNECT_GROUP[platform]
    if group == "youtube":
        return bool(s.google_client_id) and s.google_client_secret is not None
    if group == "meta":
        return bool(s.meta_app_id) and s.meta_app_secret is not None
    return bool(s.tiktok_client_key) and s.tiktok_client_secret is not None


@router.get("/integrations", response_model=list[IntegrationStatus])
def list_integrations(db: DbSession) -> list[IntegrationStatus]:
    out = []
    for platform in Platform:
        account = service.get_account(db, platform)
        out.append(IntegrationStatus(
            platform=platform,
            supported=platform in service.SUPPORTED,
            configured=_configured(platform),
            connect_group=service.CONNECT_GROUP[platform],
            account=AccountRead.model_validate(account) if account else None,
            note=service.REQUIREMENTS.get(platform),
        ))
    return out


AUTH_URLS = {"youtube": youtube.authorization_url, "meta": meta.authorization_url,
             "tiktok": tiktok.authorization_url}


@router.get("/integrations/{group}/connect", response_model=ConnectResponse)
def connect(group: str) -> ConnectResponse:
    """Sign-in URL for 'youtube', 'meta' (Facebook + Instagram) or 'tiktok'."""
    if group not in AUTH_URLS:
        raise NotFoundError("Unknown platform.")
    return ConnectResponse(auth_url=AUTH_URLS[group]())


def _back_to_settings(**params: str) -> RedirectResponse:
    target = f"{get_settings().frontend_url.rstrip('/')}/settings"
    return RedirectResponse(f"{target}?{urlencode(params)}", status_code=303)


def _callback(platform_name: str, connect_fn, db: Session, code: str | None,
              state: str | None, error: str | None, error_description: str | None
              ) -> RedirectResponse:
    if error or not code or not state:
        denied = error in ("access_denied", "user_denied")
        message = (f"{platform_name} access was not granted." if denied else
                   f"{platform_name} sign-in failed ({error_description or error or 'no code'}).")
        return _back_to_settings(error=message)
    try:
        names = connect_fn(db, code, state)
    except AppError as exc:
        return _back_to_settings(error=exc.message)
    return _back_to_settings(connected=names)


@router.get("/integrations/meta/callback", include_in_schema=False)
def meta_callback(db: DbSession, code: str | None = None, state: str | None = None,
                  error: str | None = None, error_description: str | None = None
                  ) -> RedirectResponse:
    def connect_fn(db, code, state):
        accounts = service.connect_meta(db, code, state)
        return " and ".join(a.name for a in accounts)
    return _callback("Facebook", connect_fn, db, code, state, error, error_description)


@router.get("/integrations/tiktok/callback", include_in_schema=False)
def tiktok_callback(db: DbSession, code: str | None = None, state: str | None = None,
                    error: str | None = None, error_description: str | None = None
                    ) -> RedirectResponse:
    return _callback("TikTok", lambda db, c, s: service.connect_tiktok(db, c, s).name,
                     db, code, state, error, error_description)


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


@router.post("/publications/{publication_id}/find-link", response_model=PublicationRead)
def find_publication_link(publication_id: uuid.UUID, db: DbSession) -> PublicationRead:
    return PublicationRead.model_validate(service.find_tiktok_link(db, publication_id))


@router.get("/publications", response_model=list[PublicationRead])
def recent_publications(
    db: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[PublicationRead]:
    rows = db.scalars(select(Publication).order_by(Publication.created_at.desc()).limit(limit))
    return [PublicationRead.model_validate(p) for p in rows]
