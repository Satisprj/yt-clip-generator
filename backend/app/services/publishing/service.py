"""Connected accounts and publications: what to post where, and running the uploads."""

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError, ConflictError, InvalidInputError, NotFoundError
from app.models import Clip, Platform, PlatformAccount, Publication, PublicationStatus
from app.models.job import utcnow
from app.services import storage
from app.services.publishing import secrets, youtube
from app.services.publishing.captions import (
    PostContent,
    build_post_content,
    default_caption,
    tags_for,
)

logger = logging.getLogger(__name__)

SUPPORTED = {Platform.YOUTUBE}
COMING_SOON = {
    Platform.INSTAGRAM: "Needs an Instagram Business/Creator account linked to a Facebook Page.",
    Platform.FACEBOOK: "Needs a Facebook Page (posting to personal profiles isn't possible).",
    Platform.TIKTOK: "Needs a TikTok account and developer app; posts stay private until "
                     "TikTok audits the app.",
}
PRIVACY_VALUES = {"private", "unlisted", "public"}
ACTIVE = (PublicationStatus.QUEUED, PublicationStatus.UPLOADING, PublicationStatus.PUBLISHED)


# --- Accounts ----------------------------------------------------------------------------


def get_account(db: Session, platform: Platform) -> PlatformAccount | None:
    return db.scalar(select(PlatformAccount).where(PlatformAccount.platform == platform))


def connect_youtube(db: Session, code: str, state: str) -> PlatformAccount:
    tokens = youtube.exchange_code(code, state)
    channel = youtube.get_channel(tokens.access_token)
    account = get_account(db, Platform.YOUTUBE) or PlatformAccount(platform=Platform.YOUTUBE)
    account.external_id = channel.id
    account.name = channel.title
    account.avatar_url = channel.avatar_url
    _store_tokens(account, tokens)
    account.connected_at = utcnow()
    db.add(account)
    db.commit()
    logger.info("Connected YouTube channel %s (%s)", channel.title, channel.id)
    return account


def disconnect(db: Session, platform: Platform) -> None:
    account = get_account(db, platform)
    if account is None:
        raise NotFoundError(f"No {platform.value} account is connected.")
    busy = db.scalar(select(Publication.id).where(
        Publication.account_id == account.id,
        Publication.status.in_([PublicationStatus.QUEUED, PublicationStatus.UPLOADING])))
    if busy is not None:
        raise ConflictError("Uploads to this account are still running. Wait for them first.")
    if platform is Platform.YOUTUBE:
        try:
            token = secrets.decrypt(account.refresh_token_enc or account.access_token_enc)
            youtube.revoke(token)
        except AppError:
            pass  # can't read the token: just forget the account
    db.delete(account)
    db.commit()


def _store_tokens(account: PlatformAccount, tokens: youtube.Tokens) -> None:
    account.access_token_enc = secrets.encrypt(tokens.access_token)
    if tokens.refresh_token:
        account.refresh_token_enc = secrets.encrypt(tokens.refresh_token)
    account.token_expires_at = tokens.expires_at
    account.scopes = tokens.scopes


def access_token(db: Session, account: PlatformAccount, *, force_refresh: bool = False) -> str:
    """A valid access token, refreshed (and saved) when it is about to expire."""
    expires = account.token_expires_at
    fresh = expires is not None and expires - timedelta(seconds=120) > datetime.now(UTC)
    if fresh and not force_refresh:
        return secrets.decrypt(account.access_token_enc)
    if not account.refresh_token_enc:
        raise ConflictError("YouTube access expired. Reconnect YouTube in Settings.",
                            code="account_reconnect_required")
    tokens = youtube.refresh(secrets.decrypt(account.refresh_token_enc))
    _store_tokens(account, tokens)
    db.commit()
    return tokens.access_token


# --- Publications ------------------------------------------------------------------------


def create_publications(
    db: Session,
    clips: list[Clip],
    platforms: list[Platform],
    *,
    privacy: str | None = None,
    title: str | None = None,
    caption: str | None = None,
) -> tuple[list[Publication], list[Publication]]:
    """Queue uploads. Returns (created, already existing). Nothing is posted twice."""
    if not clips:
        raise InvalidInputError("There are no rendered clips to publish.")
    if not platforms:
        raise InvalidInputError("Choose at least one platform.")
    privacy = privacy or get_settings().youtube_default_privacy
    if privacy not in PRIVACY_VALUES:
        raise InvalidInputError("Privacy must be private, unlisted or public.")

    accounts: dict[Platform, PlatformAccount] = {}
    for platform in dict.fromkeys(platforms):
        if platform not in SUPPORTED:
            raise InvalidInputError(f"Publishing to {platform.value} isn't available yet. "
                                    f"{COMING_SOON.get(platform, '')}".strip())
        account = get_account(db, platform)
        if account is None:
            raise InvalidInputError(f"Connect your {platform.value} account in Settings first.",
                                    code="account_not_connected")
        accounts[platform] = account

    created, existing = [], []
    for clip in clips:
        if clip.render_status != "ready" or not clip.video_file:
            raise InvalidInputError(f"Clip #{clip.rank} isn't rendered yet.")
        for platform, account in accounts.items():
            current = db.scalar(select(Publication).where(
                Publication.clip_id == clip.id, Publication.account_id == account.id,
                Publication.status.in_(ACTIVE)))
            if current is not None:
                existing.append(current)
                continue
            content = build_post_content(
                platform,
                title=(title or clip.title).strip(),
                caption=(caption if caption is not None
                         else default_caption(clip.description, clip.hashtags)).strip(),
                hashtags=clip.hashtags,
            )
            publication = Publication(
                clip_id=clip.id, job_id=clip.job_id, account_id=account.id, platform=platform,
                privacy=privacy, title=content.title, caption=content.caption)
            db.add(publication)
            try:
                db.commit()
            except IntegrityError:  # a parallel request queued the same post first
                db.rollback()
                existing.append(db.scalar(select(Publication).where(
                    Publication.clip_id == clip.id, Publication.account_id == account.id,
                    Publication.status.in_(ACTIVE))))
                continue
            created.append(publication)

    for publication in created:
        _enqueue(db, publication)
    return created, existing


def retry_publication(db: Session, publication_id: uuid.UUID) -> Publication:
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise NotFoundError("Publication not found.")
    if publication.status != PublicationStatus.FAILED:
        raise ConflictError("Only failed uploads can be retried.")
    if publication.account_id is None:
        raise ConflictError("The account was disconnected. Connect it and publish again.")
    publication.status, publication.progress, publication.error_message = (
        PublicationStatus.QUEUED, 0, None)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("This clip was already posted to this account again.") from exc
    _enqueue(db, publication)
    return publication


def _enqueue(db: Session, publication: Publication) -> None:
    from app.worker.tasks import publish_clip

    try:
        publish_clip.delay(str(publication.id))
    except Exception:
        logger.exception("Could not enqueue publication %s", publication.id)
        publication.status = PublicationStatus.FAILED
        publication.error_message = "The job queue is unavailable. Please try again."
        db.commit()


def run_publication(db: Session, publication_id: uuid.UUID) -> PublicationStatus | None:
    """Upload one publication (called by the worker). Never raises."""
    publication = db.get(Publication, publication_id)
    if publication is None or publication.status != PublicationStatus.QUEUED:
        return publication.status if publication else None
    clip = db.get(Clip, publication.clip_id)
    account = db.get(PlatformAccount, publication.account_id) if publication.account_id else None

    publication.status = PublicationStatus.UPLOADING
    db.commit()
    try:
        if clip is None or not clip.video_file:
            raise InvalidInputError("The clip was deleted or re-rendered; publish it again.")
        if account is None:
            raise InvalidInputError("The account was disconnected.")
        video = storage.job_files(clip.job_id).clips_dir / clip.video_file
        if not video.is_file():
            raise InvalidInputError("The clip file is missing; re-render the clips.")
        content = PostContent(publication.title, publication.caption,
                              tags_for(publication.platform, clip.hashtags))
        progress = _ProgressSaver(db, publication)
        try:
            remote_id, url = youtube.upload_video(access_token(db, account), video, content,
                                                  privacy=publication.privacy,
                                                  on_progress=progress)
        except youtube.YouTubeError as exc:
            if exc.code != "youtube_unauthorized":
                raise
            remote_id, url = youtube.upload_video(
                access_token(db, account, force_refresh=True), video, content,
                privacy=publication.privacy, on_progress=progress)
    except AppError as exc:
        return _fail(db, publication, exc.message)
    except Exception:
        logger.exception("Publication %s crashed", publication_id)
        return _fail(db, publication, "Unexpected error while uploading. See the worker log.")

    publication.status = PublicationStatus.PUBLISHED
    publication.progress = 100
    publication.remote_id, publication.url = remote_id, url
    publication.published_at = utcnow()
    db.commit()
    logger.info("Published clip %s to %s: %s", publication.clip_id, publication.platform, url)
    return publication.status


def _fail(db: Session, publication: Publication, message: str) -> PublicationStatus:
    db.rollback()
    publication = db.get(Publication, publication.id)
    publication.status = PublicationStatus.FAILED
    publication.error_message = message
    db.commit()
    logger.warning("Publication %s failed: %s", publication.id, message)
    return PublicationStatus.FAILED


class _ProgressSaver:
    """Save upload progress at most every 1.5 s."""

    def __init__(self, db: Session, publication: Publication) -> None:
        self.db, self.publication, self.last = db, publication, 0.0

    def __call__(self, fraction: float) -> None:
        now = time.monotonic()
        if fraction < 1 and now - self.last < 1.5:
            return
        self.publication.progress = min(99, int(fraction * 100))
        self.db.commit()
        self.last = now


def fail_interrupted_publications(db: Session) -> int:
    """Uploads left 'uploading' by a stopped worker become failed (retryable)."""
    result = db.execute(
        update(Publication).where(Publication.status == PublicationStatus.UPLOADING)
        .values(status=PublicationStatus.FAILED,
                error_message="The upload was interrupted because the worker stopped. Retry it."))
    db.commit()
    return result.rowcount or 0
