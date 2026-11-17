"""Publishing to YouTube against a fake Google server that follows the real protocol."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core.exceptions import InvalidInputError
from app.models import Clip, Job, JobStatus, Platform, PlatformAccount, Publication
from app.models import PublicationStatus as PS
from app.services import storage
from app.services.publishing import http, secrets, service, youtube
from app.services.publishing.captions import build_post_content, default_caption
from app.worker import tasks

UPLOAD_SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=abc"


class FakeGoogle:
    """Minimal Google OAuth + YouTube Data API behaviour."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.received = b""
        self.fail_put_once_with: int | None = None
        self.init_status = 200
        self.init_error: dict | None = None
        self.scope = " ".join(youtube.SCOPES)
        self.token_error = False
        self.access_tokens_issued = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith(youtube.TOKEN_URL):
            if self.token_error:
                return httpx.Response(400, json={"error": "invalid_grant",
                                                 "error_description": "Token has been revoked."})
            self.access_tokens_issued += 1
            form = parse_qs(request.content.decode())
            body = {"access_token": f"access-{self.access_tokens_issued}", "expires_in": 3599,
                    "scope": self.scope, "token_type": "Bearer"}
            if form["grant_type"] == ["authorization_code"]:
                body["refresh_token"] = "refresh-1"
            return httpx.Response(200, json=body)
        if url.startswith(youtube.REVOKE_URL):
            return httpx.Response(200)
        if url.startswith(youtube.CHANNELS_URL):
            return httpx.Response(200, json={"items": [{"id": "UC123", "snippet": {
                "title": "My Channel", "thumbnails": {"default": {"url": "https://x/a.jpg"}}}}]})
        if request.method == "POST" and url.startswith(youtube.UPLOAD_URL):
            if self.init_error:
                return httpx.Response(self.init_status, json=self.init_error)
            if self.init_status != 200:
                return httpx.Response(self.init_status)
            return httpx.Response(200, headers={"location": UPLOAD_SESSION})
        if request.method == "PUT" and url == UPLOAD_SESSION:
            content_range = request.headers["content-range"]
            total = int(content_range.split("/")[1])
            if content_range.startswith("bytes */"):  # status query after an error
                if len(self.received) == total:
                    return httpx.Response(200, json={"id": "vid123"})
                headers = {"range": f"bytes=0-{len(self.received) - 1}"} if self.received else {}
                return httpx.Response(308, headers=headers)
            if self.fail_put_once_with:
                code, self.fail_put_once_with = self.fail_put_once_with, None
                return httpx.Response(code)
            start = int(content_range.split(" ")[1].split("-")[0])
            assert start == len(self.received), "client must resume exactly where it stopped"
            self.received += request.content
            if len(self.received) < total:
                received = f"bytes=0-{len(self.received) - 1}"
                return httpx.Response(308, headers={"range": received})
            return httpx.Response(200, json={"id": "vid123"})
        return httpx.Response(404)


@pytest.fixture
def google(monkeypatch, storage_dir):
    fake = FakeGoogle()
    monkeypatch.setattr(http, "transport", httpx.MockTransport(fake))
    monkeypatch.setattr(youtube, "CHUNK_SIZE", 1000)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    secrets.reset_cache()
    yield fake
    secrets.reset_cache()


@pytest.fixture
def queued(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(tasks.publish_clip, "delay", lambda pid: sent.append(pid))
    return sent


def _ready_clip(db, rank=1, size=2500) -> Clip:
    job = Job(youtube_url="https://www.youtube.com/watch?v=jNQXAC9IVRw",
              youtube_video_id="jNQXAC9IVRw", status=JobStatus.COMPLETED, title="Source")
    db.add(job)
    db.flush()
    clip = Clip(job_id=job.id, rank=rank, start_seconds=0, end_seconds=25, duration_seconds=25,
                first_sentence=0, last_sentence=2, transcript_text="t", category="funny",
                viral_score=8, scores={}, hook="h", reason="r", title="Big <Moment> here",
                description="You won't believe this.", hashtags=["#funny", "#viral"],
                render_status="ready", video_file="01.mp4")
    db.add(clip)
    db.commit()
    clips_dir = storage.job_files(job.id).clips_dir
    clips_dir.mkdir(parents=True, exist_ok=True)
    (clips_dir / "01.mp4").write_bytes(bytes(range(256)) * (size // 256) + b"x" * (size % 256))
    return clip


def _connect(client, google) -> None:
    auth_url = client.get("/api/integrations/youtube/connect").json()["auth_url"]
    state = parse_qs(urlparse(auth_url).query)["state"][0]
    response = client.get("/api/integrations/youtube/callback",
                          params={"code": "auth-code", "state": state}, follow_redirects=False)
    assert response.status_code == 303, response.text


# --- Captions & secrets ----------------------------------------------------------


def test_youtube_content_respects_limits():
    content = build_post_content(Platform.YOUTUBE, title="A <b> " + "x" * 120,
                                 caption=default_caption("Great clip", ["#a", "#b"]),
                                 hashtags=["#a", "#b"] + [f"#tag{i}" for i in range(200)])
    assert "<" not in content.title and len(content.title) <= 100
    assert content.title.endswith("…")
    assert content.caption == "Great clip\n\n#a #b #Shorts"
    assert content.tags[:2] == ["a", "b"] and sum(len(t) + 1 for t in content.tags) <= 500


def test_instagram_style_caption_leads_with_title():
    content = build_post_content(Platform.INSTAGRAM, title="Title", caption="Body #x",
                                 hashtags=["#x"])
    assert content.title == "" and content.caption == "Title\n\nBody #x"


def test_secrets_roundtrip_and_ttl(storage_dir):
    secrets.reset_cache()
    token = secrets.encrypt("refresh-token")
    assert "refresh-token" not in token and secrets.decrypt(token) == "refresh-token"
    assert (storage_dir / "secret.key").is_file()
    with pytest.raises(secrets.TokenDecryptionError):
        secrets.decrypt("not-a-token")
    secrets.reset_cache()


# --- OAuth -----------------------------------------------------------------------


def test_not_configured_gives_clear_error(client, storage_dir, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    response = client.get("/api/integrations/youtube/connect")
    assert response.status_code == 400
    assert "GOOGLE_CLIENT_ID" in response.json()["error"]["message"]


def test_authorization_url_uses_pkce_and_offline_access(google):
    query = parse_qs(urlparse(youtube.authorization_url()).query)
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
    assert query["code_challenge_method"] == ["S256"]
    assert "youtube.upload" in query["scope"][0]
    state = json.loads(secrets.decrypt(query["state"][0]))
    assert state["platform"] == "youtube" and len(state["verifier"]) > 40


def test_connect_flow_saves_encrypted_tokens(client, db, google):
    _connect(client, google)
    account = db.query(PlatformAccount).one()
    assert (account.platform, account.name, account.external_id) == (
        Platform.YOUTUBE, "My Channel", "UC123")
    assert "refresh-1" not in account.refresh_token_enc  # encrypted at rest
    assert secrets.decrypt(account.refresh_token_enc) == "refresh-1"
    token_request = next(r for r in google.requests if str(r.url) == youtube.TOKEN_URL)
    assert "code_verifier=" in token_request.content.decode()

    youtube_status = next(i for i in client.get("/api/integrations").json()
                          if i["platform"] == "youtube")
    assert youtube_status["configured"] and youtube_status["account"]["name"] == "My Channel"


def test_callback_errors_redirect_to_settings(client, db, google):
    denied = client.get("/api/integrations/youtube/callback",
                        params={"error": "access_denied"}, follow_redirects=False)
    assert denied.status_code == 303
    assert denied.headers["location"].startswith("http://localhost:5173/settings?error=")
    tampered = client.get("/api/integrations/youtube/callback",
                          params={"code": "c", "state": "forged"}, follow_redirects=False)
    assert "expired+or+is+invalid" in tampered.headers["location"]
    assert db.query(PlatformAccount).count() == 0


def test_missing_upload_permission_is_rejected(client, db, google):
    google.scope = "https://www.googleapis.com/auth/youtube.readonly"
    _connect(client, google)
    assert db.query(PlatformAccount).count() == 0


# --- Upload protocol -------------------------------------------------------------


def _content():
    return build_post_content(Platform.YOUTUBE, title="T", caption="C", hashtags=["#t"])


def test_resumable_upload_sends_chunks_and_reports_progress(google, tmp_path):
    video = tmp_path / "v.mp4"
    data = bytes(range(256)) * 10  # 2560 bytes -> 3 chunks of up to 1000
    video.write_bytes(data)
    progress = []
    video_id, url = youtube.upload_video("tok", video, _content(), privacy="unlisted",
                                         on_progress=progress.append)
    assert (video_id, url) == ("vid123", "https://www.youtube.com/shorts/vid123")
    assert google.received == data
    init = next(r for r in google.requests if r.method == "POST")
    meta = json.loads(init.content)
    assert meta["status"]["privacyStatus"] == "unlisted" and meta["snippet"]["tags"] == ["t"]
    ranges = [r.headers["content-range"] for r in google.requests if r.method == "PUT"]
    assert ranges == ["bytes 0-999/2560", "bytes 1000-1999/2560", "bytes 2000-2559/2560"]
    assert progress[-1] == 1.0 and 0 < progress[0] < 1


def test_upload_resumes_after_server_error(google, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"a" * 2500)
    google.fail_put_once_with = 503
    waits = []
    video_id, _ = youtube.upload_video("tok", video, _content(), privacy="private",
                                       sleep=waits.append)
    assert video_id == "vid123" and google.received == b"a" * 2500 and waits == [2]


def test_quota_error_is_explained(google, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"a")
    google.init_status = 403
    google.init_error = {"error": {"code": 403, "message": "quota",
                                   "errors": [{"reason": "quotaExceeded"}]}}
    with pytest.raises(youtube.YouTubeError, match="quota is used up"):
        youtube.upload_video("tok", video, _content(), privacy="private")


# --- Publications ----------------------------------------------------------------


def test_publish_clip_queues_once_and_rejects_duplicates(client, db, google, queued):
    _connect(client, google)
    clip = _ready_clip(db)
    body = client.post(f"/api/clips/{clip.id}/publish",
                       json={"platforms": ["youtube"], "privacy": "unlisted"}).json()
    assert len(body["queued"]) == 1 and body["already_published"] == []
    publication = body["queued"][0]
    assert publication["title"] == "Big Moment here"  # angle brackets removed
    assert publication["caption"].endswith("#funny #viral #Shorts")
    assert queued == [publication["id"]]

    again = client.post(f"/api/clips/{clip.id}/publish", json={"platforms": ["youtube"]}).json()
    assert again["queued"] == [] and again["already_published"][0]["id"] == publication["id"]
    assert queued == [publication["id"]]  # nothing posted twice


def test_publish_requires_connection_and_supported_platform(client, db, google, queued):
    clip = _ready_clip(db)
    not_connected = client.post(f"/api/clips/{clip.id}/publish", json={"platforms": ["youtube"]})
    assert not_connected.json()["error"]["code"] == "account_not_connected"
    tiktok = client.post(f"/api/clips/{clip.id}/publish", json={"platforms": ["tiktok"]})
    assert tiktok.json()["error"]["code"] == "account_not_connected"
    unknown = client.post(f"/api/clips/{clip.id}/publish", json={"platforms": ["myspace"]})
    assert unknown.status_code == 422
    assert client.post(f"/api/clips/{clip.id}/publish",
                       json={"platforms": []}).status_code == 422


def test_same_caption_override_for_a_clip(client, db, google, queued):
    _connect(client, google)
    clip = _ready_clip(db)
    body = client.post(f"/api/clips/{clip.id}/publish", json={
        "platforms": ["youtube"], "title": "My own title", "caption": "My caption #mine"}).json()
    assert body["queued"][0]["title"] == "My own title"
    assert body["queued"][0]["caption"] == "My caption #mine #Shorts"


def test_run_publication_uploads_and_records_url(client, db, google, queued):
    _connect(client, google)
    clip = _ready_clip(db)
    pid = client.post(f"/api/clips/{clip.id}/publish",
                      json={"platforms": ["youtube"]}).json()["queued"][0]["id"]

    assert service.run_publication(db, uuid.UUID(pid)) == PS.PUBLISHED
    db.expire_all()
    publication = db.get(Publication, uuid.UUID(pid))
    assert publication.url == "https://www.youtube.com/shorts/vid123"
    assert publication.progress == 100 and publication.published_at is not None
    assert google.received == (storage.job_files(clip.job_id).clips_dir / "01.mp4").read_bytes()


def test_expired_access_token_is_refreshed(client, db, google, queued):
    _connect(client, google)
    account = db.query(PlatformAccount).one()
    account.token_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    db.commit()
    clip = _ready_clip(db)
    pid = client.post(f"/api/clips/{clip.id}/publish",
                      json={"platforms": ["youtube"]}).json()["queued"][0]["id"]
    assert service.run_publication(db, uuid.UUID(pid)) == PS.PUBLISHED
    init = next(r for r in google.requests if r.method == "POST" and "upload" in str(r.url))
    assert init.headers["authorization"] == "Bearer access-2"  # the refreshed token


def test_revoked_access_fails_with_reconnect_message_then_retry(client, db, google, queued):
    _connect(client, google)
    account = db.query(PlatformAccount).one()
    account.token_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    db.commit()
    clip = _ready_clip(db)
    pid = uuid.UUID(client.post(f"/api/clips/{clip.id}/publish",
                                json={"platforms": ["youtube"]}).json()["queued"][0]["id"])
    google.token_error = True
    assert service.run_publication(db, pid) == PS.FAILED
    db.expire_all()
    assert "Reconnect YouTube" in db.get(Publication, pid).error_message

    google.token_error = False
    retried = client.post(f"/api/publications/{pid}/retry").json()
    assert retried["status"] == "queued" and queued[-1] == str(pid)
    assert service.run_publication(db, pid) == PS.PUBLISHED


def test_publish_all_clips_of_a_job(client, db, google, queued):
    _connect(client, google)
    clip = _ready_clip(db)
    other = Clip(job_id=clip.job_id, rank=2, start_seconds=30, end_seconds=55,
                 duration_seconds=25, first_sentence=3, last_sentence=5, transcript_text="t",
                 category="story", viral_score=7, scores={}, hook="h", reason="r",
                 title="Second", description="d", hashtags=[], render_status="ready",
                 video_file="02.mp4")
    pending = Clip(job_id=clip.job_id, rank=3, start_seconds=60, end_seconds=85,
                   duration_seconds=25, first_sentence=6, last_sentence=8, transcript_text="t",
                   category="story", viral_score=6, scores={}, hook="h", reason="r",
                   title="Not rendered", description="d", hashtags=[])
    db.add_all([other, pending])
    db.commit()
    body = client.post(f"/api/jobs/{clip.job_id}/publish", json={"platforms": ["youtube"]}).json()
    assert sorted(p["title"] for p in body["queued"]) == ["Big Moment here", "Second"]
    listed = client.get(f"/api/jobs/{clip.job_id}/publications").json()
    assert len(listed) == 2


def test_disconnect_keeps_history(client, db, google, queued):
    _connect(client, google)
    clip = _ready_clip(db)
    pid = uuid.UUID(client.post(f"/api/clips/{clip.id}/publish",
                                json={"platforms": ["youtube"]}).json()["queued"][0]["id"])
    assert client.delete("/api/integrations/youtube").status_code == 409  # upload pending
    service.run_publication(db, pid)
    assert client.delete("/api/integrations/youtube").status_code == 204
    assert any(str(r.url).startswith(youtube.REVOKE_URL) for r in google.requests)
    db.expire_all()
    publication = db.get(Publication, pid)
    assert publication.account_id is None and publication.url  # history and link kept


def test_interrupted_uploads_become_retryable(db, google):
    from app.worker.recovery import fail_interrupted_jobs

    account = PlatformAccount(platform=Platform.YOUTUBE, external_id="UC1", name="c",
                              access_token_enc=secrets.encrypt("a"))
    db.add(account)
    clip = _ready_clip(db)
    publication = Publication(clip_id=clip.id, job_id=clip.job_id, account_id=account.id,
                              platform=Platform.YOUTUBE, privacy="private", title="t",
                              caption="c", status=PS.UPLOADING)
    db.add(publication)
    db.commit()
    fail_interrupted_jobs()
    db.expire_all()
    assert db.get(Publication, publication.id).status == PS.FAILED


def test_create_publications_validates_input(db, google):
    with pytest.raises(InvalidInputError, match="no rendered clips"):
        service.create_publications(db, [], [Platform.YOUTUBE])
