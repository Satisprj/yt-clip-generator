import os

import pytest
from yt_dlp.utils import DownloadError

from app.core.exceptions import ExternalServiceError, InvalidInputError
from app.services import youtube
from app.services.youtube import VideoInfo, extract_video_id, validate_for_processing

ID = "jNQXAC9IVRw"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={ID}",
        f"https://youtube.com/watch?v={ID}&t=42s&list=PL123",
        f"http://m.youtube.com/watch?feature=share&v={ID}",
        f"https://music.youtube.com/watch?v={ID}",
        f"https://youtu.be/{ID}",
        f"https://youtu.be/{ID}?si=abc",
        f"https://www.youtube.com/shorts/{ID}",
        f"https://www.youtube.com/embed/{ID}",
        f"https://www.youtube.com/live/{ID}?feature=share",
        f"https://www.youtube-nocookie.com/embed/{ID}",
        f"  https://www.youtube.com/watch?v={ID}  ",
    ],
)
def test_extract_video_id_accepts_common_formats(url):
    assert extract_video_id(url) == ID


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        f"ftp://youtube.com/watch?v={ID}",
        f"https://evil-youtube.com/watch?v={ID}",
        f"https://youtube.com.evil.com/watch?v={ID}",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/@channel",
        "https://vimeo.com/123456",
    ],
)
def test_extract_video_id_rejects_invalid(url):
    assert extract_video_id(url) is None


def _info(**overrides):
    values = dict(video_id=ID, title="T", channel="C", duration_seconds=600,
                  thumbnail_url=None, is_live=False)
    values.update(overrides)
    return VideoInfo(**values)


def test_validate_accepts_normal_video():
    validate_for_processing(_info())


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"is_live": True}, "Live streams"),
        ({"duration_seconds": None}, "duration"),
        ({"duration_seconds": 5}, "shorter"),
        ({"duration_seconds": 4 * 3600}, "4:00:00 long"),
    ],
)
def test_validate_rejects(overrides, message):
    with pytest.raises(InvalidInputError, match=message):
        validate_for_processing(_info(**overrides))


class _FakeYDL:
    result = None
    error = None

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download):
        assert download is False
        if self.error:
            raise self.error
        return self.result


def test_fetch_video_info_maps_fields(monkeypatch):
    _FakeYDL.result = {"id": ID, "title": "Me at the zoo", "uploader": "jawed",
                       "duration": 19.4, "thumbnail": "https://i.ytimg.com/x.jpg"}
    _FakeYDL.error = None
    monkeypatch.setattr(youtube, "YoutubeDL", _FakeYDL)
    info = youtube.fetch_video_info(f"https://youtu.be/{ID}")
    assert info == VideoInfo(ID, "Me at the zoo", "jawed", 19, "https://i.ytimg.com/x.jpg", False)


@pytest.mark.parametrize(
    ("raw", "exc_type", "message"),
    [
        (f"ERROR: [youtube] {ID}: Private video. Sign in", InvalidInputError, "private"),
        (f"ERROR: [youtube] {ID}: Video unavailable", InvalidInputError, "unavailable"),
        (f"ERROR: [youtube] {ID}: Sign in to confirm your age", InvalidInputError, "age"),
        (f"ERROR: [youtube] {ID}: Sign in to confirm you're not a bot", ExternalServiceError,
         "bot check"),
        ("ERROR: \x1b[0;31mHTTP Error 503\x1b[0m", ExternalServiceError, "YouTube error: HTTP"),
    ],
)
def test_fetch_video_info_translates_errors(monkeypatch, raw, exc_type, message):
    _FakeYDL.error = DownloadError(raw)
    monkeypatch.setattr(youtube, "YoutubeDL", _FakeYDL)
    with pytest.raises(exc_type, match=message):
        youtube.fetch_video_info(f"https://youtu.be/{ID}")
    _FakeYDL.error = None


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("RUN_YOUTUBE_TESTS") != "1",
                    reason="set RUN_YOUTUBE_TESTS=1 to call YouTube for real")
def test_real_youtube_metadata():
    info = youtube.fetch_video_info(f"https://www.youtube.com/watch?v={ID}")
    assert info.video_id == ID
    assert info.title == "Me at the zoo"
    assert info.duration_seconds == 19
    assert info.thumbnail_url
