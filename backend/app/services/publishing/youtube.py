"""YouTube: Google OAuth (authorization code + PKCE) and resumable video upload.

Uses plain HTTPS calls (httpx) so the whole flow is visible and testable:
- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol

Note: videos uploaded by an *unverified* Google Cloud project are locked to private until
the project passes YouTube's API audit. Each upload costs about 1,600 of the default
10,000 daily quota units (roughly 6 uploads/day).
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError, ExternalServiceError
from app.services.publishing import http
from app.services.publishing.captions import PostContent

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]

CHUNK_SIZE = 8 * 1024 * 1024  # multiple of 256 KiB, as the protocol requires
MAX_RETRIES = 5

class YouTubeError(ExternalServiceError):
    code = "youtube_error"


class NotConfiguredError(AppError):
    status_code = 400
    code = "youtube_not_configured"


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scopes: str


@dataclass(frozen=True)
class Channel:
    id: str
    title: str
    avatar_url: str | None


def _client(timeout: float = 30.0) -> httpx.Client:
    return http.client(timeout)


def _credentials() -> tuple[str, str]:
    s = get_settings()
    if not s.google_client_id or s.google_client_secret is None:
        raise NotConfiguredError(
            "YouTube isn't set up yet: add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to "
            "backend\\.env (see docs/PUBLISHING.md) and restart the API."
        )
    return s.google_client_id, s.google_client_secret.get_secret_value()


def _google_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    error = data.get("error")
    if isinstance(error, dict):  # YouTube API style
        reasons = [e.get("reason") for e in error.get("errors", []) if e.get("reason")]
        return f"{error.get('message', 'error')} ({', '.join(reasons) or error.get('code')})"
    return f"{error}: {data.get('error_description', '')}".strip(": ")


# --- OAuth -----------------------------------------------------------------------------


def authorization_url() -> str:
    """URL that sends the user to Google's consent screen.

    The PKCE verifier travels inside the encrypted, time-limited `state` value, so nothing
    needs to be stored between the redirect and the callback.
    """
    client_id, _ = _credentials()
    verifier, challenge = http.pkce_pair()
    state = http.make_state("youtube", verifier=verifier)
    params = {
        "client_id": client_id,
        "redirect_uri": get_settings().google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",        # get a refresh token
        "prompt": "consent",              # ...every time, even when reconnecting
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str, state: str) -> Tokens:
    client_id, client_secret = _credentials()
    data = http.read_state(state, "youtube")

    with _client() as c:
        response = c.post(TOKEN_URL, data={
            "code": code, "client_id": client_id, "client_secret": client_secret,
            "redirect_uri": get_settings().google_redirect_uri,
            "grant_type": "authorization_code", "code_verifier": data["verifier"],
        })
    if response.status_code != 200:
        raise YouTubeError(f"Google sign-in failed: {_google_error(response)}")
    return _tokens_from(response.json(), previous_refresh=None)


def refresh(refresh_token: str) -> Tokens:
    client_id, client_secret = _credentials()
    with _client() as c:
        response = c.post(TOKEN_URL, data={
            "refresh_token": refresh_token, "client_id": client_id,
            "client_secret": client_secret, "grant_type": "refresh_token",
        })
    if response.status_code != 200:
        raise YouTubeError(
            f"YouTube access expired or was revoked ({_google_error(response)}). "
            "Reconnect YouTube in Settings.", code="account_reconnect_required")
    return _tokens_from(response.json(), previous_refresh=refresh_token)


def revoke(token: str) -> None:
    """Best effort: tell Google to drop the grant."""
    try:
        with _client(timeout=10) as c:
            c.post(REVOKE_URL, params={"token": token})
    except httpx.HTTPError:
        logger.warning("Could not revoke the YouTube token (continuing)")


def _tokens_from(data: dict[str, Any], previous_refresh: str | None) -> Tokens:
    granted = data.get("scope", "")
    if "youtube.upload" not in granted:
        raise YouTubeError("Permission to upload videos was not granted. Connect again and "
                           "allow 'Manage your YouTube videos'.")
    return Tokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token") or previous_refresh,
        expires_at=datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 3600))),
        scopes=granted,
    )


def get_channel(access_token: str) -> Channel:
    with _client() as c:
        response = c.get(CHANNELS_URL, params={"part": "snippet", "mine": "true"},
                            headers={"Authorization": f"Bearer {access_token}"})
    if response.status_code != 200:
        raise YouTubeError(f"Could not read your YouTube channel: {_google_error(response)}")
    items = response.json().get("items") or []
    if not items:
        raise YouTubeError("This Google account has no YouTube channel. Create one first.")
    snippet = items[0].get("snippet", {})
    avatar = (snippet.get("thumbnails", {}).get("default") or {}).get("url")
    return Channel(items[0]["id"], snippet.get("title", "YouTube channel"), avatar)


# --- Upload ----------------------------------------------------------------------------


def _upload_error(response: httpx.Response) -> YouTubeError:
    detail = _google_error(response)
    lower = detail.lower()
    if "quotaexceeded" in lower:
        return YouTubeError("The daily YouTube upload quota is used up (about 6 uploads per day "
                            "for new projects). Try again tomorrow.", code="youtube_quota")
    if "uploadlimitexceeded" in lower:
        return YouTubeError("Your channel reached YouTube's upload limit for now. Try later.",
                            code="youtube_quota")
    return YouTubeError(f"YouTube upload failed: {detail}")


def upload_video(
    access_token: str,
    video: Path,
    content: PostContent,
    *,
    privacy: str,
    on_progress: Callable[[float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, str]:
    """Upload with the resumable protocol. Returns (video id, watch URL)."""
    size = video.stat().st_size
    metadata = {
        "snippet": {"title": content.title, "description": content.caption,
                    "tags": content.tags, "categoryId": get_settings().youtube_category_id},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    auth = {"Authorization": f"Bearer {access_token}"}

    with _client(timeout=120) as c:
        start = c.post(
            UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
            headers={**auth, "Content-Type": "application/json; charset=UTF-8",
                     "X-Upload-Content-Length": str(size),
                     "X-Upload-Content-Type": "video/mp4"},
            content=json.dumps(metadata).encode(),
        )
        if start.status_code == 401:
            raise YouTubeError("Unauthorized", code="unauthorized")
        if start.status_code != 200 or "location" not in start.headers:
            raise _upload_error(start)
        session_url = start.headers["location"]

        offset, retries = 0, 0
        with video.open("rb") as fh:
            while True:
                try:
                    fh.seek(offset)
                    chunk = fh.read(CHUNK_SIZE)
                    end = offset + len(chunk) - 1
                    response = c.put(session_url, content=chunk, headers={
                        **auth, "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end}/{size}"})
                except httpx.TransportError as exc:
                    response, error = None, exc
                else:
                    error = None

                if response is not None and response.status_code in (200, 201):
                    body = response.json()
                    if on_progress:
                        on_progress(1.0)
                    return body["id"], f"https://www.youtube.com/shorts/{body['id']}"
                if response is not None and response.status_code == 308:
                    offset = _next_offset(response)
                    retries = 0
                    if on_progress:
                        on_progress(offset / size)
                    continue
                if response is not None and response.status_code not in (500, 502, 503, 504):
                    raise _upload_error(response)

                # Network error or server error: wait, ask where to resume, try again.
                retries += 1
                if retries > MAX_RETRIES:
                    raise YouTubeError(f"Upload interrupted too many times: {error or response}")
                sleep(min(60, 2 ** retries))
                status = c.put(session_url, headers={
                    **auth, "Content-Length": "0", "Content-Range": f"bytes */{size}"})
                if status.status_code in (200, 201):
                    body = status.json()
                    return body["id"], f"https://www.youtube.com/shorts/{body['id']}"
                if status.status_code == 308:
                    offset = _next_offset(status)


def _next_offset(response: httpx.Response) -> int:
    """308 responses say which bytes arrived, e.g. 'Range: bytes=0-8388607'."""
    received = response.headers.get("range")
    return int(received.rsplit("-", 1)[1]) + 1 if received else 0
