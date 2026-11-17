"""TikTok: Login Kit (OAuth v2) and the Content Posting API (Direct Post, FILE_UPLOAD).

Flow (TikTok docs, "Content Posting API - Direct Post"):
creator_info/query -> video/init (FILE_UPLOAD, chunk plan) -> PUT chunks to upload_url
-> poll status/fetch until PUBLISH_COMPLETE.

Unaudited apps can only direct-post to *private* TikTok accounts. For a public account the
video is sent to the creator's TikTok inbox instead ("Upload" API, video.upload scope): the
creator gets a notification and posts it from the TikTok app, publicly if they like.
"""

import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError, ExternalServiceError
from app.services.publishing import http
from app.services.publishing.captions import PostContent

logger = logging.getLogger(__name__)

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com"
SCOPES = ["user.info.basic", "video.publish", "video.upload", "video.list"]
CHUNK = 10 * 1024 * 1024      # 10 MB chunks (TikTok allows 5-64 MB; last may be larger)
SINGLE_CHUNK_MAX = 64 * 1024 * 1024
POLL_INTERVAL = 5
POLL_TIMEOUT = 600

# Our privacy choices -> TikTok privacy levels (in order of preference).
PRIVACY = {
    "public": ["PUBLIC_TO_EVERYONE"],
    "unlisted": ["MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR"],
    "private": ["SELF_ONLY"],
}


class TikTokError(ExternalServiceError):
    code = "tiktok_error"


class NotConfiguredError(AppError):
    status_code = 400
    code = "tiktok_not_configured"


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scopes: str
    open_id: str


@dataclass(frozen=True)
class Profile:
    open_id: str
    display_name: str
    avatar_url: str | None


def _credentials() -> tuple[str, str]:
    s = get_settings()
    if not s.tiktok_client_key or s.tiktok_client_secret is None:
        raise NotConfiguredError(
            "TikTok isn't set up yet: add TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET to "
            "backend\\.env (see docs/PUBLISHING.md) and restart the API.")
    return s.tiktok_client_key, s.tiktok_client_secret.get_secret_value()


def _api_error(response: httpx.Response) -> tuple[str, str]:
    try:
        body = response.json()
    except ValueError:
        return "http_error", f"HTTP {response.status_code}"
    error = body.get("error") or {}
    if isinstance(error, dict):
        return error.get("code", "error"), error.get("message") or f"HTTP {response.status_code}"
    return str(error), body.get("error_description", "")


def _check(response: httpx.Response, action: str) -> dict:
    code, message = _api_error(response)
    if code == "scope_not_authorized":
        raise TikTokError(f"TikTok didn't grant the permission needed for {action}. In the "
                          "TikTok developer portal (Sandbox → Scopes), add video.upload and "
                          "video.list and click 'Apply changes'; then Disconnect and Connect "
                          "TikTok in Settings.", code="scope")
    if response.status_code == 401 or code in ("access_token_invalid", "invalid_token"):
        raise TikTokError("Unauthorized", code="unauthorized")
    if code == "unaudited_client_can_only_post_to_private_accounts":
        raise TikTokError("TikTok only allows private posts until your app passes TikTok's "
                          "audit. Publish with visibility 'Private'.", code="unaudited")
    if code == "spam_risk_too_many_posts":
        raise TikTokError("TikTok's daily posting limit for this account was reached.")
    if response.status_code >= 400 or code not in ("ok", "error") and code:
        raise TikTokError(f"TikTok error while {action}: {message} ({code})")
    return response.json()


# --- OAuth -------------------------------------------------------------------------------


def authorization_url() -> str:
    client_key, _ = _credentials()
    params = {"client_key": client_key, "scope": ",".join(SCOPES), "response_type": "code",
              "redirect_uri": get_settings().tiktok_redirect_uri,
              "state": http.make_state("tiktok")}
    return f"{AUTH_URL}?{urlencode(params)}"


def _token_request(data: dict[str, str]) -> Tokens:
    client_key, client_secret = _credentials()
    with http.client() as c:
        response = c.post(f"{API}/v2/oauth/token/", data={
            "client_key": client_key, "client_secret": client_secret, **data},
            headers={"Content-Type": "application/x-www-form-urlencoded"})
    body = response.json() if response.content else {}
    if response.status_code >= 400 or "access_token" not in body:
        detail = body.get("error_description") or body.get("error") or response.status_code
        raise TikTokError(f"TikTok sign-in failed or expired ({detail}). Reconnect TikTok in "
                          "Settings.", code="account_reconnect_required")
    if "video.publish" not in body.get("scope", ""):
        raise TikTokError("Permission to post videos was not granted. Connect again and allow it.")
    return Tokens(body["access_token"], body.get("refresh_token"),
                  datetime.now(UTC) + timedelta(seconds=int(body.get("expires_in", 86400))),
                  body.get("scope", ""), body.get("open_id", ""))


def exchange_code(code: str, state: str) -> Tokens:
    http.read_state(state, "tiktok")
    return _token_request({"code": code, "grant_type": "authorization_code",
                           "redirect_uri": get_settings().tiktok_redirect_uri})


def refresh(refresh_token: str) -> Tokens:
    return _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})


def revoke(token: str) -> None:
    try:
        client_key, client_secret = _credentials()
        with http.client(timeout=10) as c:
            c.post(f"{API}/v2/oauth/revoke/", data={"client_key": client_key,
                                                    "client_secret": client_secret,
                                                    "token": token})
    except (httpx.HTTPError, AppError):
        logger.warning("Could not revoke the TikTok token (continuing)")


def get_profile(access_token: str) -> Profile:
    with http.client() as c:
        response = c.get(f"{API}/v2/user/info/",
                         params={"fields": "open_id,display_name,avatar_url"},
                         headers={"Authorization": f"Bearer {access_token}"})
    user = _check(response, "reading your profile").get("data", {}).get("user", {})
    return Profile(user.get("open_id", ""), user.get("display_name") or "TikTok account",
                   user.get("avatar_url"))


# --- Posting -----------------------------------------------------------------------------


def chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) per TikTok's rules: whole file if small, else 10 MB
    chunks with the remainder merged into the last chunk."""
    if size <= SINGLE_CHUNK_MAX:
        return size, 1
    return CHUNK, max(1, math.floor(size / CHUNK))


def _privacy_level(privacy: str, options: list[str]) -> str:
    for level in PRIVACY.get(privacy, []) + ["SELF_ONLY"]:
        if level in options:
            return level
    if not options:
        raise TikTokError("TikTok didn't offer any privacy option for this account.")
    return options[0]


def upload_video(access_token: str, video: Path, content: PostContent, *, privacy: str,
                 on_progress: Callable[[float], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> tuple[str, str | None, bool]:
    """Post a video. Returns (post or publish id, URL if known, sent_to_drafts).

    Tries a direct post first. Unaudited apps may only direct-post to private accounts, so for
    a public account the video goes to the creator's TikTok inbox (drafts) instead.
    """
    auth = {"Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8"}
    size = video.stat().st_size
    chunk_size, chunks = chunk_plan(size)
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size,
              "total_chunk_count": chunks}

    with http.client(timeout=300) as c:
        def direct(privacy_level: str) -> dict:
            return _check(c.post(f"{API}/v2/post/publish/video/init/", headers=auth,
                                 content=json.dumps({
                                     "post_info": {"title": content.caption,
                                                   "privacy_level": privacy_level,
                                                   "disable_duet": False, "disable_comment": False,
                                                   "disable_stitch": False},
                                     "source_info": source,
                                 }).encode()), "starting the TikTok upload")["data"]

        creator = _check(c.post(f"{API}/v2/post/publish/creator_info/query/", headers=auth,
                                content=b"{}"), "checking your TikTok account")["data"]
        level = _privacy_level(privacy, creator.get("privacy_level_options") or [])
        drafted = False
        try:
            init = direct(level)
        except TikTokError as exc:
            if exc.code != "unaudited":
                raise
            logger.info("TikTok refused a direct post (unaudited app, public account); "
                        "sending the video to the creator's inbox instead")
            init = _check(c.post(f"{API}/v2/post/publish/inbox/video/init/", headers=auth,
                                 content=json.dumps({"source_info": source}).encode()),
                          "sending the video to your TikTok inbox")["data"]
            drafted = True
        publish_id, upload_url = init["publish_id"], init["upload_url"]

        with video.open("rb") as fh:
            for index in range(chunks):
                first = index * chunk_size
                last = size - 1 if index == chunks - 1 else first + chunk_size - 1
                fh.seek(first)
                body = fh.read(last - first + 1)
                response = c.put(upload_url, content=body, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(body)),
                    "Content-Range": f"bytes {first}-{last}/{size}"})
                if response.status_code not in (200, 201, 206):
                    raise TikTokError(f"TikTok upload failed (HTTP {response.status_code}).")
                if on_progress:
                    on_progress(0.8 * (last + 1) / size)

        waited = 0.0
        while True:
            status = _check(c.post(f"{API}/v2/post/publish/status/fetch/", headers=auth,
                                   content=json.dumps({"publish_id": publish_id}).encode()),
                            "checking the TikTok post")["data"]
            state = status.get("status")
            if state == "SEND_TO_USER_INBOX" or (drafted and state == "PUBLISH_COMPLETE"):
                if on_progress:
                    on_progress(1.0)
                return publish_id, None, True
            if state == "PUBLISH_COMPLETE":
                post_ids = status.get("publicaly_available_post_id") or []
                if on_progress:
                    on_progress(1.0)
                post_id = str(post_ids[0]) if post_ids else publish_id
                url = f"https://www.tiktok.com/video/{post_ids[0]}" if post_ids else None
                return post_id, url, False
            if state == "FAILED":
                raise TikTokError(f"TikTok rejected the video: {status.get('fail_reason')}")
            if waited >= POLL_TIMEOUT:
                raise TikTokError("TikTok is still processing the video after 10 minutes. "
                                  "Check the TikTok app.")
            sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def find_posted_video(access_token: str, publish_id: str, *, after: datetime,
                      hints: list[str], exclude: set[str]) -> tuple[str, str] | None:
    """Find the post a creator made from an inbox draft. Returns (video id, share URL).

    TikTok doesn't link a draft to the post the creator makes from it, so: ask the status
    endpoint first, then look through the creator's recent videos (video.list scope) for one
    posted after the draft was sent, preferring one whose caption starts like ours.
    """
    auth = {"Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8"}
    with http.client() as c:
        status = _check(c.post(f"{API}/v2/post/publish/status/fetch/", headers=auth,
                               content=json.dumps({"publish_id": publish_id}).encode()),
                        "checking the TikTok post")["data"]
        post_ids = [str(i) for i in status.get("publicaly_available_post_id") or []]
        if post_ids:  # TikTok linked the draft to its post: no need to search
            return post_ids[0], f"https://www.tiktok.com/video/{post_ids[0]}"
        response = c.post(f"{API}/v2/video/list/",
                          params={"fields": "id,share_url,create_time,title,video_description"},
                          headers=auth, content=json.dumps({"max_count": 20}).encode())
    videos = _check(response, "reading your TikTok videos").get("data", {}).get("videos") or []

    since = after.timestamp() - 120
    fresh = sorted((v for v in videos if (v.get("create_time") or 0) >= since
                    and str(v.get("id")) not in exclude),
                   key=lambda v: v.get("create_time") or 0)
    if not fresh:
        return None
    wanted = [w for w in (_normalize(h)[:30] for h in hints) if w]

    def text(v: dict) -> str:
        return _normalize(f"{v.get('title') or ''} {v.get('video_description') or ''}")

    match = next((v for v in fresh if any(w in text(v) for w in wanted)), None)
    video = match or fresh[0]
    return str(video["id"]), video.get("share_url") or f"https://www.tiktok.com/video/{video['id']}"
