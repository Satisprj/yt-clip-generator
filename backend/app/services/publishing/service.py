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
from app.services.publishing import meta, secrets, tiktok, youtube
from app.services.publishing.captions import (
    PostContent,
    build_post_content,
    default_caption,
    tags_for,
)

logger = logging.getLogger(__name__)

SUPPORTED = set(Platform)
REQUIREMENTS = {
    Platform.YOUTUBE: "Needs a YouTube channel and a Google Cloud OAuth client. Uploads stay "
                      "private until Google approves the API audit.",
    Platform.INSTAGRAM: "Needs an Instagram Business/Creator account linked to a Facebook Page, "
                        "and a Meta developer app. Connect via Facebook (one login for both).",
    Platform.FACEBOOK: "Needs a Facebook Page (apps can't post to personal profiles) and a Meta "
                       "developer app. 'Private' saves the Reel as a draft on the Page.",
    Platform.TIKTOK: "Needs a TikTok developer app. Posts are private ('only me') until TikTok "
                     "audits the app.",
}
# Facebook and Instagram are connected together with one Meta login.
CONNECT_GROUP = {Platform.FACEBOOK: "meta", Platform.INSTAGRAM: "meta",
                 Platform.YOUTUBE: "youtube", Platform.TIKTOK: "tiktok"}
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


def _save_account(db: Session, platform: Platform, *, external_id: str, name: str,
                  avatar_url: str | None, access: str, refresh: str | None,
                  expires_at, scopes: str) -> PlatformAccount:
    account = get_account(db, platform) or PlatformAccount(platform=platform)
    account.external_id, account.name, account.avatar_url = external_id, name, avatar_url
    account.access_token_enc = secrets.encrypt(access)
    account.refresh_token_enc = secrets.encrypt(refresh) if refresh else None
    account.token_expires_at, account.scopes = expires_at, scopes
    account.connected_at = utcnow()
    db.add(account)
    return account


def connect_meta(db: Session, code: str, state: str) -> list[PlatformAccount]:
    """One Meta login connects the Facebook Page and its linked Instagram account."""
    conn = meta.connect(code, state)
    scopes = ",".join(meta.SCOPES)
    accounts = [_save_account(db, Platform.FACEBOOK, external_id=conn.page_id,
                              name=conn.page_name, avatar_url=conn.page_picture,
                              access=conn.page_token, refresh=None, expires_at=None,
                              scopes=scopes)]
    if conn.instagram_id:
        accounts.append(_save_account(
            db, Platform.INSTAGRAM, external_id=conn.instagram_id,
            name=f"@{conn.instagram_username}" if conn.instagram_username else "Instagram",
            avatar_url=conn.instagram_picture, access=conn.page_token, refresh=None,
            expires_at=None, scopes=scopes))
    db.commit()
    logger.info("Connected Facebook Page %s%s", conn.page_name,
                f" and Instagram @{conn.instagram_username}" if conn.instagram_id else "")
    return accounts


def connect_tiktok(db: Session, code: str, state: str) -> PlatformAccount:
    tokens = tiktok.exchange_code(code, state)
    profile = tiktok.get_profile(tokens.access_token)
    account = _save_account(db, Platform.TIKTOK, external_id=profile.open_id or tokens.open_id,
                            name=profile.display_name, avatar_url=profile.avatar_url,
                            access=tokens.access_token, refresh=tokens.refresh_token,
                            expires_at=tokens.expires_at, scopes=tokens.scopes)
    db.commit()
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
    if platform in (Platform.YOUTUBE, Platform.TIKTOK):
        try:
            token = secrets.decrypt(account.refresh_token_enc or account.access_token_enc)
            (youtube.revoke if platform is Platform.YOUTUBE else tiktok.revoke)(token)
        except AppError:
            pass  # can't read the token: just forget the account
    db.delete(account)
    db.commit()


def _store_tokens(account: PlatformAccount, tokens) -> None:
    account.access_token_enc = secrets.encrypt(tokens.access_token)
    if tokens.refresh_token:
        account.refresh_token_enc = secrets.encrypt(tokens.refresh_token)
    account.token_expires_at = tokens.expires_at
    account.scopes = tokens.scopes


def access_token(db: Session, account: PlatformAccount, *, force_refresh: bool = False) -> str:
    """A valid access token, refreshed (and saved) when it is about to expire.

    Meta Page tokens never expire (token_expires_at is None).
    """
    expires = account.token_expires_at
    never_expires = expires is None and account.platform in (Platform.FACEBOOK,
                                                              Platform.INSTAGRAM)
    fresh = never_expires or (expires is not None
                              and expires - timedelta(seconds=120) > datetime.now(UTC))
    if fresh and not force_refresh:
        return secrets.decrypt(account.access_token_enc)
    name = account.platform.value.capitalize()
    if not account.refresh_token_enc:
        raise ConflictError(f"{name} access expired. Reconnect it in Settings.",
                            code="account_reconnect_required")
    refresher = tiktok.refresh if account.platform is Platform.TIKTOK else youtube.refresh
    tokens = refresher(secrets.decrypt(account.refresh_token_enc))
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
            raise InvalidInputError(f"Publishing to {platform.value} isn't available.")
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
            remote_id, url = _upload(account, access_token(db, account), video, content,
                                     publication.privacy, progress)
        except AppError as exc:
            if exc.code != "unauthorized" or account.platform in (Platform.FACEBOOK,
                                                                  Platform.INSTAGRAM):
                raise
            remote_id, url = _upload(account, access_token(db, account, force_refresh=True),
                                     video, content, publication.privacy, progress)
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


def _upload(account: PlatformAccount, token: str, video, content: PostContent,
            privacy: str, progress) -> tuple[str, str | None]:
    """Send one video to the account's platform. Returns (remote id, public URL)."""
    if account.platform is Platform.YOUTUBE:
        return youtube.upload_video(token, video, content, privacy=privacy,
                                    on_progress=progress)
    if account.platform is Platform.FACEBOOK:
        return meta.upload_facebook_reel(account.external_id, token, video, content,
                                         privacy=privacy, on_progress=progress)
    if account.platform is Platform.INSTAGRAM:
        return meta.upload_instagram_reel(account.external_id, token, video, content,
                                          on_progress=progress)
    return tiktok.upload_video(token, video, content, privacy=privacy, on_progress=progress)


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
