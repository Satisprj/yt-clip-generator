"""Smart download mode: audio first, then only the video seconds each clip needs.

YouTube is replaced, but segment cutting and rendering are real FFmpeg runs on a local
110 s test video (FFmpeg reads local files exactly like the remote stream URLs).
"""

import shutil

import pytest

from app.models import Clip, JobStatus
from app.services import media, storage, youtube
from app.services.media import MediaError
from app.worker import pipeline
from tests.test_jobs import (  # noqa: F401  (fixtures are used by name)
    _moment,
    _queued_job,
    _reload,
    fake_youtube,
    long_speech,
)


@pytest.fixture
def smart(monkeypatch, fake_youtube, long_video):  # noqa: F811
    monkeypatch.setenv("DOWNLOAD_MODE", "smart")
    calls = fake_youtube
    calls.update(audio=0, resolve=0, segments=0)

    def fake_download_audio(url, directory, *, on_progress=None):
        calls["audio"] += 1
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "audio_source.mp4"  # any container with an audio track works
        shutil.copy(long_video, target)
        return target

    def fake_resolve(url):
        calls["resolve"] += 1
        return youtube.Stream(str(long_video), {}), None

    real_fetch = media.fetch_segment

    def counting_fetch(*args, **kwargs):
        calls["segments"] += 1
        return real_fetch(*args, **kwargs)

    monkeypatch.setattr(youtube, "download_audio", fake_download_audio)
    monkeypatch.setattr(youtube, "resolve_streams", fake_resolve)
    monkeypatch.setattr(media, "fetch_segment", counting_fetch)
    calls["words"] = long_speech()
    return calls


def test_smart_mode_downloads_audio_and_only_clip_segments(db, storage_dir, smart):
    smart["moments"] = [_moment(1, 3), _moment(7, 9, "funny")]  # ~50 s of a 600 s video
    job = _queued_job(db)

    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    files = storage.job_files(job.id)
    assert smart["audio"] == 1 and smart["download"] == 0  # never downloaded the full video
    assert smart["resolve"] == 1 and smart["segments"] == 2  # one fetch per clip
    assert not files.source_video.exists()
    assert sorted(p.name for p in files.clips_dir.glob("*.mp4")) == ["01.mp4", "02.mp4"]
    assert not list(files.clips_dir.glob("*.source.mp4"))  # temp segments removed

    for clip in db.query(Clip).filter_by(job_id=job.id):
        info = media.probe(files.clips_dir / clip.video_file)
        assert clip.render_status == "ready"
        assert abs(info.duration_seconds - clip.duration_seconds) < 0.3  # exact cut
    assert _reload(db, job).stage_message.startswith("2 clips ready")


def test_segment_fetch_failure_falls_back_to_full_download(db, storage_dir, smart, monkeypatch):
    def broken_fetch(*args, **kwargs):
        raise MediaError("FFmpeg failed (exit code 1): Connection reset")

    monkeypatch.setattr(media, "fetch_segment", broken_fetch)
    smart["moments"] = [_moment(1, 3), _moment(7, 9, "funny")]
    job = _queued_job(db)

    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    assert smart["download"] == 1  # full video downloaded once, then reused for clip 2
    assert all(c.render_status == "ready" for c in db.query(Clip).filter_by(job_id=job.id))


def test_full_download_when_clips_cover_most_of_the_video(db, storage_dir, smart):
    job = _queued_job(db)
    job.title, job.duration_seconds = "Short video", 60  # 25 s clip = 45% of the video
    db.commit()
    smart["moments"] = [_moment(1, 3)]

    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    assert smart["segments"] == 0 and smart["download"] == 1


def test_retry_reuses_downloaded_audio(db, storage_dir, smart):
    job = _queued_job(db)
    pipeline.run_pipeline(job.id)
    job = _reload(db, job)
    job.status = JobStatus.QUEUED
    db.commit()
    pipeline.run_pipeline(job.id)
    assert smart["audio"] == 1


def test_free_space_includes_audio_only_downloads(client, db, storage_dir, smart):
    smart["moments"] = [_moment(1, 3)]
    job = _queued_job(db)
    pipeline.run_pipeline(job.id)
    body = client.get(f"/api/jobs/{job.id}").json()
    assert body["has_source_files"] is True
    freed = client.post(f"/api/jobs/{job.id}/free-space").json()
    files = storage.job_files(job.id)
    assert freed["freed_bytes"] > 0 and files.audio_source is None and not files.audio.exists()
    assert freed["job"]["has_source_files"] is False


def test_fetch_segment_cuts_exactly(long_video, tmp_path):
    out = media.fetch_segment(str(long_video), tmp_path / "seg.mp4", start=20.0, duration=12.5)
    info = media.probe(out)
    assert info.has_video and info.has_audio
    assert abs(info.duration_seconds - 12.5) < 0.2
    assert not (tmp_path / "seg.partial.mp4").exists()
