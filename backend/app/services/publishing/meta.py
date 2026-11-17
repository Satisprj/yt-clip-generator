"""Facebook Page Reels and Instagram Reels through one Meta app (Graph API).

Sign-in (Facebook Login for Business) gives a user token, which is exchanged for a
long-lived one; the Page token derived from it does not expire. The same Page token
publishes to the Page and to the Instagram professional account linked to that Page.

Flows (Meta docs, "Reels Publishing API" and "Instagram Content Publishing"):
- Facebook: POST /{page}/video_reels upload_phase=start -> upload bytes to rupload ->
  POST /{page}/video_reels upload_phase=finish video_state=PUBLISHED|DRAFT.
- Instagram: POST /{ig-user}/media media_type=REELS upload_type=resumable -> upload bytes to
  rupload -> poll the container until FINISHED -> POST /{ig-user}/media_publish.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError, ExternalServiceError
from app.services.publishing import http
from app.services.publishing.captions import PostContent

logger = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com"
RUPLOAD = "https://rupload.facebook.com"
DIALOG = "https://www.facebook.com"
SCOPES = ["pages_show_list", "pages_read_engagement", "pages_manage_posts",
          "instagram_basic", "instagram_content_publish", "business_management"]
POLL_INTERVAL = 5
POLL_TIMEOUT = 600


class MetaError(ExternalServiceError):
    code = "meta_error"


class NotConfiguredError(AppError):
    status_code = 400
    code = "meta_not_configured"


@dataclass(frozen=True)
class MetaConnection:
    page_id: str
    page_name: str
    page_token: str
    page_picture: str | None
    instagram_id: str | None
    instagram_username: str | None
    instagram_picture: str | None


def _v() -> str:
    return get_settings().meta_graph_version


def _credentials() -> tuple[str, str]:
    s = get_settings()
    if not s.meta_app_id or s.meta_app_secret is None:
        raise NotConfiguredError(
            "Facebook/Instagram isn't set up yet: add META_APP_ID and META_APP_SECRET to "
            "backend\\.env (see docs/PUBLISHING.md) and restart the API.")
    return s.meta_app_id, s.meta_app_secret.get_secret_value()


def _error(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return f"HTTP {response.status_code}"
    message = error.get("error_user_msg") or error.get("message") or f"HTTP {response.status_code}"
    return f"{message} (code {error.get('code')})" if error.get("code") else message


def _check(response: httpx.Response, action: str) -> dict:
    if response.status_code == 401 or (response.status_code == 400 and
                                       '"code":190' in response.text.replace(" ", "")):
        raise MetaError(f"Meta access expired or was revoked while {action}. Reconnect in "
                        "Settings.", code="account_reconnect_required")
    if response.status_code >= 400:
        raise MetaError(f"Meta error while {action}: {_error(response)}")
    return response.json()


# --- Connect -----------------------------------------------------------------------------


def authorization_url() -> str:
    app_id, _ = _credentials()
    params = {"client_id": app_id, "redirect_uri": get_settings().meta_redirect_uri,
              "state": http.make_state("meta"), "scope": ",".join(SCOPES),
              "response_type": "code"}
    return f"{DIALOG}/{_v()}/dialog/oauth?{urlencode(params)}"


def connect(code: str, state: str) -> MetaConnection:
    """Exchange the code, then find the Page (and its Instagram account) to post to."""
    http.read_state(state, "meta")
    app_id, app_secret = _credentials()
    redirect = get_settings().meta_redirect_uri
    with http.client() as c:
        short = _check(c.get(f"{GRAPH}/{_v()}/oauth/access_token", params={
            "client_id": app_id, "client_secret": app_secret, "redirect_uri": redirect,
            "code": code}), "signing in")["access_token"]
        long_lived = _check(c.get(f"{GRAPH}/{_v()}/oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
            "fb_exchange_token": short}), "signing in")["access_token"]
        pages = _check(c.get(f"{GRAPH}/{_v()}/me/accounts", params={
            "fields": "id,name,access_token,picture{url},"
                      "instagram_business_account{id,username,profile_picture_url}",
            "access_token": long_lived}), "listing your Pages").get("data", [])

    if not pages:
        raise MetaError("No Facebook Page was shared with the app. Create a Page (or allow access "
                        "to it during sign-in) and connect again.")
    wanted = get_settings().meta_page_id
    page = (next((p for p in pages if p["id"] == wanted), None) if wanted else None) \
        or next((p for p in pages if p.get("instagram_business_account")), None) or pages[0]
    if wanted and page["id"] != wanted:
        raise MetaError(f"META_PAGE_ID {wanted} is not among the Pages you shared.")
    ig = page.get("instagram_business_account") or {}
    return MetaConnection(
        page_id=page["id"], page_name=page["name"], page_token=page["access_token"],
        page_picture=((page.get("picture") or {}).get("data") or {}).get("url"),
        instagram_id=ig.get("id"), instagram_username=ig.get("username"),
        instagram_picture=ig.get("profile_picture_url"),
    )


# --- Upload helpers ----------------------------------------------------------------------


def _rupload(c: httpx.Client, url: str, token: str, video: Path,
             on_progress: Callable[[float], None] | None) -> None:
    """Send the file to Meta's upload host. Resumes once from the confirmed offset."""
    size = video.stat().st_size
    data = video.read_bytes()
    offset = 0
    for attempt in range(2):
        try:
            response = c.post(url, content=data[offset:], headers={
                "Authorization": f"OAuth {token}", "offset": str(offset),
                "file_size": str(size), "Content-Type": "application/octet-stream"})
        except httpx.TransportError as exc:
            if attempt:
                raise MetaError(f"Upload to Meta failed: {exc}") from exc
            continue
        if response.status_code < 400:
            if on_progress:
                on_progress(0.8)
            return
        if attempt or response.status_code < 500:
            raise MetaError(f"Upload to Meta failed: {_error(response)}")
        # Ask how much arrived, then continue from there.
        status = c.get(url, headers={"Authorization": f"OAuth {token}"})
        offset = int((status.json() if status.status_code < 400 else {}).get("offset", 0) or 0)


def upload_facebook_reel(page_id: str, token: str, video: Path, content: PostContent, *,
                         privacy: str, on_progress: Callable[[float], None] | None = None
                         ) -> tuple[str, str]:
    """Publish a Reel on the Page. 'private' saves it as a draft on the Page instead."""
    with http.client(timeout=300) as c:
        start = _check(c.post(f"{GRAPH}/{_v()}/{page_id}/video_reels", data={
            "upload_phase": "start", "access_token": token}), "starting the Facebook upload")
        video_id = start["video_id"]
        upload_url = start.get("upload_url") or f"{RUPLOAD}/video-upload/{_v()}/{video_id}"
        _rupload(c, upload_url, token, video, on_progress)
        _check(c.post(f"{GRAPH}/{_v()}/{page_id}/video_reels", data={
            "upload_phase": "finish", "video_id": video_id, "access_token": token,
            "video_state": "DRAFT" if privacy == "private" else "PUBLISHED",
            "title": content.title, "description": content.caption,
        }), "publishing the Facebook Reel")
    if on_progress:
        on_progress(1.0)
    return video_id, f"https://www.facebook.com/reel/{video_id}"


def upload_instagram_reel(ig_user_id: str, token: str, video: Path, content: PostContent, *,
                          on_progress: Callable[[float], None] | None = None,
                          sleep: Callable[[float], None] = time.sleep) -> tuple[str, str]:
    """Publish a Reel to the Instagram professional account (Instagram has no private posts)."""
    with http.client(timeout=300) as c:
        container = _check(c.post(f"{GRAPH}/{_v()}/{ig_user_id}/media", data={
            "media_type": "REELS", "upload_type": "resumable", "caption": content.caption,
            "share_to_feed": "true", "access_token": token}), "starting the Instagram upload")
        container_id = container["id"]
        upload_url = container.get("uri") or f"{RUPLOAD}/ig-api-upload/{_v()}/{container_id}"
        _rupload(c, upload_url, token, video, on_progress)

        # Instagram processes the video before it can be published.
        waited = 0.0
        while True:
            status = _check(c.get(f"{GRAPH}/{_v()}/{container_id}", params={
                "fields": "status_code,status", "access_token": token}),
                "checking the Instagram upload")
            code = status.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise MetaError(f"Instagram couldn't process the video: "
                                f"{status.get('status') or code}")
            if waited >= POLL_TIMEOUT:
                raise MetaError("Instagram is still processing the video after 10 minutes. "
                                "Retry later.")
            sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL

        media_id = _check(c.post(f"{GRAPH}/{_v()}/{ig_user_id}/media_publish", data={
            "creation_id": container_id, "access_token": token}),
            "publishing the Instagram Reel")["id"]
        permalink = c.get(f"{GRAPH}/{_v()}/{media_id}", params={
            "fields": "permalink", "access_token": token})
        url = (permalink.json().get("permalink") if permalink.status_code < 400 else None) \
            or "https://www.instagram.com/"
    if on_progress:
        on_progress(1.0)
    return media_id, url
