"""Facebook Page Reels, Instagram Reels and TikTok against fake servers that follow each
platform's documented protocol (Meta Reels Publishing / Instagram Content Publishing,
TikTok Login Kit + Content Posting API)."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.models import Platform, PlatformAccount, Publication
from app.models import PublicationStatus as PS
from app.services.publishing import http, meta, secrets, service, tiktok
from tests.test_publishing import _ready_clip  # noqa: F401


class FakePlatforms:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.uploaded: dict[str, bytes] = {}
        self.with_instagram = True
        self.ig_status_sequence = ["IN_PROGRESS", "FINISHED"]
        self.tiktok_privacy_options = ["SELF_ONLY"]
        self.tiktok_init_error: str | None = None
        self.tiktok_tokens = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = urlparse(str(request.url))
        path, host = url.path, url.netloc
        query = parse_qs(url.query)
        form = parse_qs(request.content.decode(errors="ignore")) \
            if request.headers.get("content-type", "").startswith("application/x-www-form") \
            else {}

        # --- Meta -------------------------------------------------------------------------
        if host == "graph.facebook.com":
            if path.endswith("/oauth/access_token"):
                long = query.get("grant_type") == ["fb_exchange_token"]
                return httpx.Response(200, json={"access_token": "user-long" if long else
                                                 "user-short"})
            if path.endswith("/me/accounts"):
                page = {"id": "PAGE1", "name": "My Page", "access_token": "page-token",
                        "picture": {"data": {"url": "https://x/p.jpg"}}}
                if self.with_instagram:
                    page["instagram_business_account"] = {"id": "IG1", "username": "mybrand"}
                return httpx.Response(200, json={"data": [page]})
            if path.endswith("/PAGE1/video_reels"):
                if form.get("upload_phase") == ["start"]:
                    return httpx.Response(200, json={
                        "video_id": "FBV1",
                        "upload_url": "https://rupload.facebook.com/video-upload/v25.0/FBV1"})
                assert form["upload_phase"] == ["finish"]
                return httpx.Response(200, json={"success": True})
            if path.endswith("/IG1/media"):
                return httpx.Response(200, json={
                    "id": "CONT1", "uri": "https://rupload.facebook.com/ig-api-upload/v25.0/CONT1"})
            if path.endswith("/CONT1"):
                return httpx.Response(200, json={"status_code": self.ig_status_sequence.pop(0)})
            if path.endswith("/IG1/media_publish"):
                return httpx.Response(200, json={"id": "IGMEDIA1"})
            if path.endswith("/IGMEDIA1"):
                return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/abc/"})
        if host == "rupload.facebook.com":
            assert request.headers["authorization"] == "OAuth page-token"
            assert int(request.headers["file_size"]) == len(request.content)
            self.uploaded[path] = request.content
            return httpx.Response(200, json={"success": True})

        # --- TikTok -----------------------------------------------------------------------
        if host == "open.tiktokapis.com":
            if path == "/v2/oauth/token/":
                self.tiktok_tokens += 1
                return httpx.Response(200, json={
                    "access_token": f"tt-access-{self.tiktok_tokens}", "expires_in": 86400,
                    "refresh_token": "tt-refresh", "open_id": "OPEN1",
                    "scope": "user.info.basic,video.publish", "token_type": "Bearer"})
            if path == "/v2/oauth/revoke/":
                return httpx.Response(200, json={})
            if path == "/v2/user/info/":
                return httpx.Response(200, json={"data": {"user": {
                    "open_id": "OPEN1", "display_name": "Tik Creator"}},
                    "error": {"code": "ok", "message": ""}})
            if path == "/v2/post/publish/creator_info/query/":
                return httpx.Response(200, json={"data": {
                    "privacy_level_options": self.tiktok_privacy_options},
                    "error": {"code": "ok"}})
            if path == "/v2/post/publish/video/init/":
                body = json.loads(request.content)
                self.tiktok_init_body = body
                # Like the real API: unaudited apps are only refused for non-private posts.
                if self.tiktok_init_error and (
                        not self.tiktok_init_error.startswith("unaudited")
                        or body["post_info"]["privacy_level"] != "SELF_ONLY"):
                    return httpx.Response(403, json={"error": {"code": self.tiktok_init_error,
                                                               "message": "denied"}})
                return httpx.Response(200, json={"data": {
                    "publish_id": "PUB1", "upload_url": "https://open-upload.tiktokapis.com/up/1"},
                    "error": {"code": "ok"}})
            if path == "/v2/post/publish/status/fetch/":
                return httpx.Response(200, json={"data": {
                    "status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7123]},
                    "error": {"code": "ok"}})
        if host == "open-upload.tiktokapis.com":
            self.uploaded["tiktok"] = self.uploaded.get("tiktok", b"") + request.content
            return httpx.Response(201)
        return httpx.Response(404, json={"error": {"message": f"unexpected {request.url}"}})


@pytest.fixture
def platforms(monkeypatch, storage_dir):
    fake = FakePlatforms()
    monkeypatch.setattr(http, "transport", httpx.MockTransport(fake))
    monkeypatch.setattr(meta, "POLL_INTERVAL", 0)
    monkeypatch.setattr(tiktok, "POLL_INTERVAL", 0)
    for key, value in {"META_APP_ID": "meta-app", "META_APP_SECRET": "meta-secret",
                       "TIKTOK_CLIENT_KEY": "tt-key", "TIKTOK_CLIENT_SECRET": "tt-secret"}.items():
        monkeypatch.setenv(key, value)
    secrets.reset_cache()
    yield fake
    secrets.reset_cache()


@pytest.fixture
def queued(monkeypatch):
    from app.worker import tasks

    sent: list[str] = []
    monkeypatch.setattr(tasks.publish_clip, "delay", lambda pid: sent.append(pid))
    return sent


def _connect(client, group, callback_path):
    auth_url = client.get(f"/api/integrations/{group}/connect").json()["auth_url"]
    state = parse_qs(urlparse(auth_url).query)["state"][0]
    response = client.get(callback_path, params={"code": "c", "state": state},
                          follow_redirects=False)
    assert response.status_code == 303
    return auth_url, response.headers["location"]


# --- Meta ----------------------------------------------------------------------------------


def test_meta_login_connects_page_and_instagram(client, db, platforms):
    auth_url, location = _connect(client, "meta", "/api/integrations/meta/callback")
    assert auth_url.startswith("https://www.facebook.com/v25.0/dialog/oauth?")
    assert "instagram_content_publish" in parse_qs(urlparse(auth_url).query)["scope"][0]
    assert "connected=My+Page+and+%40mybrand" in location
    accounts = {a.platform: a for a in db.query(PlatformAccount)}
    assert accounts[Platform.FACEBOOK].external_id == "PAGE1"
    assert accounts[Platform.INSTAGRAM].external_id == "IG1"
    assert secrets.decrypt(accounts[Platform.INSTAGRAM].access_token_enc) == "page-token"
    assert accounts[Platform.FACEBOOK].token_expires_at is None  # Page tokens don't expire

    listing = {i["platform"]: i for i in client.get("/api/integrations").json()}
    assert listing["facebook"]["connect_group"] == "meta" and listing["facebook"]["configured"]
    assert listing["instagram"]["account"]["name"] == "@mybrand"


def test_meta_page_without_instagram(client, db, platforms):
    platforms.with_instagram = False
    _connect(client, "meta", "/api/integrations/meta/callback")
    assert [a.platform for a in db.query(PlatformAccount)] == [Platform.FACEBOOK]


def test_publish_to_facebook_and_instagram(client, db, platforms, queued):
    _connect(client, "meta", "/api/integrations/meta/callback")
    clip = _ready_clip(db)
    body = client.post(f"/api/clips/{clip.id}/publish", json={
        "platforms": ["facebook", "instagram"], "privacy": "public"}).json()
    ids = {p["platform"]: uuid.UUID(p["id"]) for p in body["queued"]}
    # Instagram has no title: it leads the caption.
    ig_caption = next(p["caption"] for p in body["queued"] if p["platform"] == "instagram")
    assert ig_caption.startswith("Big <Moment> here\n\n")

    assert service.run_publication(db, ids[Platform.FACEBOOK]) == PS.PUBLISHED
    assert service.run_publication(db, ids[Platform.INSTAGRAM]) == PS.PUBLISHED
    db.expire_all()
    fb = db.get(Publication, ids[Platform.FACEBOOK])
    ig = db.get(Publication, ids[Platform.INSTAGRAM])
    assert fb.url == "https://www.facebook.com/reel/FBV1"
    assert ig.url == "https://www.instagram.com/reel/abc/"
    video = _clip_bytes(clip)
    assert platforms.uploaded["/video-upload/v25.0/FBV1"] == video
    assert platforms.uploaded["/ig-api-upload/v25.0/CONT1"] == video
    finish = next(r for r in platforms.requests if b"upload_phase=finish" in r.content)
    assert b"video_state=PUBLISHED" in finish.content


def test_private_facebook_reel_is_saved_as_draft(client, db, platforms, queued):
    _connect(client, "meta", "/api/integrations/meta/callback")
    clip = _ready_clip(db)
    pid = uuid.UUID(client.post(f"/api/clips/{clip.id}/publish", json={
        "platforms": ["facebook"], "privacy": "private"}).json()["queued"][0]["id"])
    service.run_publication(db, pid)
    finish = next(r for r in platforms.requests if b"upload_phase=finish" in r.content)
    assert b"video_state=DRAFT" in finish.content


def test_instagram_processing_error_is_reported(client, db, platforms, queued):
    _connect(client, "meta", "/api/integrations/meta/callback")
    platforms.ig_status_sequence = ["IN_PROGRESS", "ERROR"]
    clip = _ready_clip(db)
    pid = uuid.UUID(client.post(f"/api/clips/{clip.id}/publish", json={
        "platforms": ["instagram"]}).json()["queued"][0]["id"])
    assert service.run_publication(db, pid) == PS.FAILED
    db.expire_all()
    assert "couldn't process" in db.get(Publication, pid).error_message


# --- TikTok --------------------------------------------------------------------------------


def test_tiktok_connect_and_private_post(client, db, platforms, queued):
    auth_url, location = _connect(client, "tiktok", "/api/integrations/tiktok/callback")
    query = parse_qs(urlparse(auth_url).query)
    assert query["client_key"] == ["tt-key"] and "video.publish" in query["scope"][0]
    assert "connected=Tik+Creator" in location
    account = db.query(PlatformAccount).filter_by(platform=Platform.TIKTOK).one()
    assert secrets.decrypt(account.refresh_token_enc) == "tt-refresh"

    clip = _ready_clip(db)
    pid = uuid.UUID(client.post(f"/api/clips/{clip.id}/publish", json={
        "platforms": ["tiktok"], "privacy": "public"}).json()["queued"][0]["id"])
    assert service.run_publication(db, pid) == PS.PUBLISHED
    # The unaudited app only offers SELF_ONLY, so "public" falls back to private.
    assert platforms.tiktok_init_body["post_info"]["privacy_level"] == "SELF_ONLY"
    source = platforms.tiktok_init_body["source_info"]
    assert (source["source"], source["total_chunk_count"]) == ("FILE_UPLOAD", 1)
    assert platforms.uploaded["tiktok"] == _clip_bytes(clip)
    db.expire_all()
    assert db.get(Publication, pid).url == "https://www.tiktok.com/video/7123"


def test_tiktok_public_when_audited(client, db, platforms, queued):
    platforms.tiktok_privacy_options = ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS",
                                        "SELF_ONLY"]
    _connect(client, "tiktok", "/api/integrations/tiktok/callback")
    clip = _ready_clip(db)
    pid = uuid.UUID(client.post(f"/api/clips/{clip.id}/publish", json={
        "platforms": ["tiktok"], "privacy": "public"}).json()["queued"][0]["id"])
    service.run_publication(db, pid)
    assert platforms.tiktok_init_body["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"


def test_tiktok_unaudited_public_post_falls_back_to_private(client, db, platforms, queued):
    # Seen with a real sandbox app: TikTok lists public options, then rejects public posts.
    platforms.tiktok_privacy_options = ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS",
                                        "SELF_ONLY"]
    platforms.tiktok_init_error = "unaudited_client_can_only_post_to_private_accounts"
    _connect(client, "tiktok", "/api/integrations/tiktok/callback")
    clip = _ready_clip(db)
    pid = uuid.UUID(client.post(f"/api/clips/{clip.id}/publish", json={
        "platforms": ["tiktok"], "privacy": "public"}).json()["queued"][0]["id"])
    assert service.run_publication(db, pid) == PS.PUBLISHED
    levels = [json.loads(r.content)["post_info"]["privacy_level"] for r in platforms.requests
              if r.url.path == "/v2/post/publish/video/init/"]
    assert levels == ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]


def test_tiktok_other_init_errors_are_reported(client, db, platforms, queued):
    _connect(client, "tiktok", "/api/integrations/tiktok/callback")
    platforms.tiktok_init_error = "spam_risk_too_many_posts"
    clip = _ready_clip(db)
    pid = uuid.UUID(client.post(f"/api/clips/{clip.id}/publish", json={
        "platforms": ["tiktok"]}).json()["queued"][0]["id"])
    assert service.run_publication(db, pid) == PS.FAILED
    db.expire_all()
    assert "daily posting limit" in db.get(Publication, pid).error_message


def test_tiktok_expired_token_is_refreshed(client, db, platforms, queued):
    _connect(client, "tiktok", "/api/integrations/tiktok/callback")
    account = db.query(PlatformAccount).filter_by(platform=Platform.TIKTOK).one()
    account.token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    clip = _ready_clip(db)
    pid = uuid.UUID(client.post(f"/api/clips/{clip.id}/publish", json={
        "platforms": ["tiktok"]}).json()["queued"][0]["id"])
    assert service.run_publication(db, pid) == PS.PUBLISHED
    init = next(r for r in platforms.requests if r.url.path == "/v2/post/publish/video/init/")
    assert init.headers["authorization"] == "Bearer tt-access-2"


def test_tiktok_chunk_plan():
    assert tiktok.chunk_plan(20_000_000) == (20_000_000, 1)
    size = 150 * 1024 * 1024
    chunk, count = tiktok.chunk_plan(size)
    assert chunk == 10 * 1024 * 1024 and count == 15


# --- All platforms at once -------------------------------------------------------------------


def test_one_click_to_all_connected_platforms(client, db, platforms, queued):
    _connect(client, "meta", "/api/integrations/meta/callback")
    _connect(client, "tiktok", "/api/integrations/tiktok/callback")
    clip = _ready_clip(db)
    body = client.post(f"/api/clips/{clip.id}/publish", json={
        "platforms": ["facebook", "instagram", "tiktok"], "title": "Same title",
        "caption": "Same caption #same"}).json()
    assert sorted(p["platform"] for p in body["queued"]) == ["facebook", "instagram", "tiktok"]
    assert all("Same caption #same" in p["caption"] for p in body["queued"])
    for p in body["queued"]:
        assert service.run_publication(db, uuid.UUID(p["id"])) == PS.PUBLISHED


def test_not_configured_platforms_explain_setup(client, storage_dir, monkeypatch):
    for key in ("META_APP_ID", "TIKTOK_CLIENT_KEY"):
        monkeypatch.setenv(key, "")
    meta_resp = client.get("/api/integrations/meta/connect")
    assert meta_resp.status_code == 400 and "META_APP_ID" in meta_resp.json()["error"]["message"]
    tt_resp = client.get("/api/integrations/tiktok/connect")
    assert "TIKTOK_CLIENT_KEY" in tt_resp.json()["error"]["message"]
    assert client.get("/api/integrations/myspace/connect").status_code == 404


def _clip_bytes(clip) -> bytes:
    from app.services import storage

    return (storage.job_files(clip.job_id).clips_dir / clip.video_file).read_bytes()
