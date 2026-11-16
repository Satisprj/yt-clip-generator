"""Phase 6: deleting, reprocessing, freeing space, ZIP export and job stats."""

import io
import tempfile
import zipfile
from pathlib import Path

import pytest

from app.models import Clip, Job, JobStatus, Transcript
from app.services import storage, youtube
from app.worker import pipeline
from tests.test_jobs import (  # noqa: F401  (fixtures are used by name)
    INFO,
    _moment,
    _queued_job,
    _reload,
    enqueued,
    fake_youtube,
    long_speech,
)


@pytest.fixture
def finished_job(db, storage_dir, fake_youtube):  # noqa: F811
    """A completed job with two rendered clips (real files)."""
    fake_youtube["words"] = long_speech()
    fake_youtube["moments"] = [_moment(1, 3), _moment(7, 9, "funny")]
    job = _queued_job(db)
    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    return _reload(db, job)


def test_job_read_includes_clip_counts_and_storage(client, db, finished_job):
    body = client.get(f"/api/jobs/{finished_job.id}").json()
    assert (body["clip_count"], body["ready_clip_count"]) == (2, 2)
    assert body["has_source_files"] is True
    assert body["storage_bytes"] == storage.directory_size(storage.job_files(finished_job.id).dir)
    listing = client.get("/api/jobs").json()
    assert listing["items"][0]["clip_count"] == 2
    assert listing["storage_bytes_total"] >= body["storage_bytes"] > 0


def test_delete_finished_job_removes_rows_and_files(client, db, finished_job):
    job_id = finished_job.id
    job_dir = storage.job_files(job_id).dir
    assert job_dir.is_dir()
    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert not job_dir.exists()
    db.expire_all()
    assert db.get(Job, job_id) is None
    assert db.query(Clip).count() == 0 and db.query(Transcript).count() == 0  # cascaded
    assert client.delete(f"/api/jobs/{job_id}").status_code == 404


def test_cannot_delete_or_reprocess_running_job(client, db):
    job = _queued_job(db)
    job.status = JobStatus.TRANSCRIBING
    db.commit()
    response = client.delete(f"/api/jobs/{job.id}")
    assert response.status_code == 409 and "transcribing" in response.json()["error"]["message"]
    assert client.post(f"/api/jobs/{job.id}/reprocess",
                       json={"start_from": "render"}).status_code == 409
    assert client.post(f"/api/jobs/{job.id}/free-space").status_code == 409


def test_delete_reports_locked_files(client, db, finished_job, monkeypatch):
    def locked(path):
        raise PermissionError("in use")

    monkeypatch.setattr(storage.shutil, "rmtree", locked)
    response = client.delete(f"/api/jobs/{finished_job.id}")
    assert response.status_code == 409 and "in use" in response.json()["error"]["message"]
    db.expire_all()
    assert db.get(Job, finished_job.id) is not None  # nothing deleted


def test_reprocess_highlights_asks_claude_again(client, db, finished_job,
                                                fake_youtube, enqueued):  # noqa: F811
    fake_youtube["moments"] = [_moment(4, 6, "story")]
    response = client.post(f"/api/jobs/{finished_job.id}/reprocess",
                           json={"start_from": "highlights"})
    assert response.status_code == 200 and response.json()["status"] == "queued"
    assert enqueued == [str(finished_job.id)]
    assert db.query(Clip).filter_by(job_id=finished_job.id).count() == 0
    assert not storage.job_files(finished_job.id).clips_dir.exists()

    before = dict(fake_youtube)
    assert pipeline.run_pipeline(finished_job.id) == JobStatus.COMPLETED
    clips = db.query(Clip).filter_by(job_id=finished_job.id).all()
    assert [(c.first_sentence, c.category, c.render_status) for c in clips] == \
        [(4, "story", "ready")]
    assert fake_youtube["llm"] == before["llm"] + 2                # new moments + titles
    assert fake_youtube["transcribe"] == before["transcribe"]      # transcript reused
    assert fake_youtube["download"] == before["download"]          # video reused


def test_reprocess_render_keeps_clips_and_renders_again(client, db, finished_job,
                                                        fake_youtube, enqueued):  # noqa: F811
    ids = {c.id for c in db.query(Clip).filter_by(job_id=finished_job.id)}
    client.post(f"/api/jobs/{finished_job.id}/reprocess", json={"start_from": "render"})
    db.expire_all()
    assert all(c.render_status == "pending" and c.video_file is None
               for c in db.query(Clip).filter_by(job_id=finished_job.id))

    llm_before = fake_youtube["llm"]
    assert pipeline.run_pipeline(finished_job.id) == JobStatus.COMPLETED
    clips = db.query(Clip).filter_by(job_id=finished_job.id).all()
    assert {c.id for c in clips} == ids and all(c.render_status == "ready" for c in clips)
    assert fake_youtube["llm"] == llm_before  # no Claude calls
    assert client.post(f"/api/jobs/{finished_job.id}/reprocess",
                       json={"start_from": "everything"}).status_code == 422


def test_reprocess_requires_transcript(client, db):
    job = _queued_job(db)
    job.status = JobStatus.FAILED
    db.commit()
    response = client.post(f"/api/jobs/{job.id}/reprocess", json={"start_from": "render"})
    assert response.status_code == 409 and "Retry" in response.json()["error"]["message"]


def test_free_space_keeps_clips_and_redownloads_when_needed(client, db, finished_job,
                                                            fake_youtube,  # noqa: F811
                                                            enqueued):  # noqa: F811
    files = storage.job_files(finished_job.id)
    expected = files.source_video.stat().st_size + files.audio.stat().st_size
    body = client.post(f"/api/jobs/{finished_job.id}/free-space").json()
    assert body["freed_bytes"] == expected
    assert body["job"]["has_source_files"] is False
    assert not files.source_video.exists() and not files.audio.exists()
    assert (files.clips_dir / "01.mp4").is_file()  # clips kept

    client.post(f"/api/jobs/{finished_job.id}/reprocess", json={"start_from": "render"})
    downloads_before = fake_youtube["download"]
    assert pipeline.run_pipeline(finished_job.id) == JobStatus.COMPLETED
    assert fake_youtube["download"] == downloads_before + 1  # source fetched again


def test_retry_skips_youtube_lookup_but_still_validates(db, storage_dir, fake_youtube,  # noqa: F811
                                                        monkeypatch):
    lookups = []
    monkeypatch.setattr(youtube, "fetch_video_info", lambda url: lookups.append(url) or INFO)
    job = _queued_job(db)
    pipeline.run_pipeline(job.id)
    assert len(lookups) == 1

    job = _reload(db, job)
    job.status = JobStatus.QUEUED
    db.commit()
    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    assert len(lookups) == 1  # saved details reused

    job = _reload(db, job)
    job.status, job.duration_seconds = JobStatus.QUEUED, 5 * 3600  # now over the limit
    db.commit()
    assert pipeline.run_pipeline(job.id) == JobStatus.FAILED
    assert "limit" in _reload(db, job).error_message


def test_download_all_clips_as_zip(client, db, finished_job):
    temp_dir = Path(tempfile.gettempdir())
    before = set(temp_dir.glob("clipgen-*.zip"))
    response = client.get(f"/api/jobs/{finished_job.id}/clips.zip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert 'filename="Me-at-the-zoo-clips.zip"' in response.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert sorted(zf.namelist()) == ["01-Clip-0.mp4", "01-Clip-0.srt", "02-Clip-1.mp4",
                                         "02-Clip-1.srt", "captions.txt"]
        captions = zf.read("captions.txt").decode("utf-8")
        assert "=== 01-Clip-0.mp4" in captions and "#tag" in captions
        clip_size = (storage.job_files(finished_job.id).clips_dir / "01.mp4").stat().st_size
        assert zf.getinfo("01-Clip-0.mp4").file_size == clip_size
    assert set(temp_dir.glob("clipgen-*.zip")) == before  # temp file cleaned up


def test_failed_stage_is_recorded_and_cleared_on_retry(client, db, storage_dir,
                                                       fake_youtube, enqueued,  # noqa: F811
                                                       monkeypatch):
    def broken_download(url, destination, **kwargs):
        raise youtube.ExternalServiceError("YouTube error: HTTP 503")

    monkeypatch.setattr(youtube, "download_video", broken_download)
    job = _queued_job(db)
    pipeline.run_pipeline(job.id)
    body = client.get(f"/api/jobs/{job.id}").json()
    assert (body["status"], body["failed_stage"]) == ("failed", "downloading")

    assert client.post(f"/api/jobs/{job.id}/retry").json()["failed_stage"] is None


def test_interrupted_job_records_its_stage(db):
    from app.worker.recovery import fail_interrupted_jobs

    job = _queued_job(db)
    job.status = JobStatus.TRANSCRIBING
    db.commit()
    fail_interrupted_jobs()
    job = _reload(db, job)
    assert (job.status, job.failed_stage) == (JobStatus.FAILED, "transcribing")


def test_zip_without_rendered_clips_is_404(client, db):
    job = _queued_job(db)
    response = client.get(f"/api/jobs/{job.id}/clips.zip")
    assert response.status_code == 404 and response.json()["error"]["code"] == "no_clips"
