"""Phase 7: worker heartbeat, cleanup and duplicate-job protection."""

import json
import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.models import Job, JobStatus
from app.services import cleanup, storage, system_checks
from app.worker import heartbeat
from tests.test_jobs import _queued_job, enqueued  # noqa: F401

ID = "jNQXAC9IVRw"


# --- Heartbeat ---------------------------------------------------------------


def test_heartbeat_roundtrip_and_staleness(storage_dir):
    assert heartbeat.heartbeat_age() is None and not heartbeat.worker_alive()
    with pytest.raises(RuntimeError, match="No worker is running"):
        system_checks.check_worker()

    heartbeat.write_heartbeat(started_at="2026-01-01T00:00:00+00:00")
    assert heartbeat.read_heartbeat()["pid"] == os.getpid()
    assert heartbeat.heartbeat_age() < 5 and heartbeat.worker_alive()
    system_checks.check_worker()  # no error

    path = heartbeat.heartbeat_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["unix"] = time.time() - 300
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not heartbeat.worker_alive()
    with pytest.raises(RuntimeError, match="stopped responding 30"):
        system_checks.check_worker()


def test_corrupt_heartbeat_counts_as_no_worker(storage_dir):
    storage_dir.mkdir(parents=True, exist_ok=True)
    heartbeat.heartbeat_path().write_text("{not json", encoding="utf-8")
    assert heartbeat.heartbeat_age() is None


def test_maintenance_thread_writes_heartbeat_and_cleans_up_on_stop(storage_dir, monkeypatch):
    monkeypatch.setattr(heartbeat, "HEARTBEAT_INTERVAL", 0.05)
    thread = heartbeat.MaintenanceThread()
    thread.start()
    deadline = time.time() + 5
    while not heartbeat.worker_alive() and time.time() < deadline:
        time.sleep(0.05)
    assert heartbeat.worker_alive()
    thread.stop()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert heartbeat.heartbeat_age() is None  # file removed on shutdown


def test_readiness_reports_worker(client, monkeypatch):
    for name in ("check_database", "check_queue", "check_ffmpeg", "check_claude_cli"):
        monkeypatch.setattr(system_checks, name, lambda: None)
    body = client.get("/api/health/ready").json()
    assert body["status"] == "degraded"
    assert "No worker is running" in body["checks"]["worker"]["error"]


# --- Duplicate jobs ------------------------------------------------------------


def test_duplicate_active_job_is_rejected_with_its_id(client, db, enqueued):  # noqa: F811
    first = client.post("/api/jobs", json={"url": f"https://youtu.be/{ID}"}).json()
    second = client.post("/api/jobs", json={"url": f"https://www.youtube.com/watch?v={ID}"})
    assert second.status_code == 409
    error = second.json()["error"]
    assert error["code"] == "duplicate_job" and error["details"]["job_id"] == first["id"]
    assert enqueued == [first["id"]]


def test_same_video_allowed_again_once_finished(client, db, enqueued):  # noqa: F811
    first = client.post("/api/jobs", json={"url": f"https://youtu.be/{ID}"}).json()
    job = db.get(Job, __import__("uuid").UUID(first["id"]))
    job.status = JobStatus.COMPLETED
    db.commit()
    assert client.post("/api/jobs", json={"url": f"https://youtu.be/{ID}"}).status_code == 201


# --- Cleanup -------------------------------------------------------------------


def _finished_job(db, *, days_ago: float, status=JobStatus.COMPLETED) -> Job:
    job = _queued_job(db)
    job.status = status
    job.completed_at = datetime.now(UTC) - timedelta(days=days_ago)
    db.commit()
    files = storage.job_files(job.id)
    files.clips_dir.mkdir(parents=True)
    files.source_video.write_bytes(b"v" * 1000)
    files.audio.write_bytes(b"a" * 500)
    (files.clips_dir / "01.mp4").write_bytes(b"c" * 200)
    return job


def test_cleanup_removes_old_sources_but_keeps_clips(db, storage_dir, monkeypatch):
    monkeypatch.setenv("SOURCE_RETENTION_DAYS", "7")
    old = _finished_job(db, days_ago=10)
    recent = _finished_job(db, days_ago=2)
    running = _finished_job(db, days_ago=30, status=JobStatus.RENDERING)

    report = cleanup.run_cleanup()

    assert report.sources_removed == 1 and report.bytes_freed == 1500
    old_files = storage.job_files(old.id)
    assert not old_files.source_video.exists() and not old_files.audio.exists()
    assert (old_files.clips_dir / "01.mp4").is_file()
    assert storage.job_files(recent.id).source_video.is_file()
    assert storage.job_files(running.id).source_video.is_file()  # never touch running jobs
    assert cleanup.run_cleanup().sources_removed == 0  # idempotent


def test_cleanup_deletes_old_jobs_when_enabled(db, storage_dir, monkeypatch):
    monkeypatch.setenv("JOB_RETENTION_DAYS", "30")
    old = _finished_job(db, days_ago=45, status=JobStatus.FAILED)
    kept = _finished_job(db, days_ago=5)
    old_id = old.id

    report = cleanup.run_cleanup()

    assert report.jobs_deleted == 1
    db.expire_all()
    assert db.get(Job, old_id) is None and not storage.job_files(old_id).dir.exists()
    assert db.get(Job, kept.id) is not None


def test_cleanup_disabled_by_zero(db, storage_dir, monkeypatch):
    monkeypatch.setenv("SOURCE_RETENTION_DAYS", "0")
    job = _finished_job(db, days_ago=365)
    assert not cleanup.run_cleanup().changed
    assert storage.job_files(job.id).source_video.is_file()


def test_cleanup_removes_orphan_folders_and_stale_temp_zips(db, storage_dir, monkeypatch,
                                                            tmp_path):
    import uuid

    orphan = storage_dir / "jobs" / str(uuid.uuid4())
    orphan.mkdir(parents=True)
    (orphan / "source.mp4").write_bytes(b"x" * 100)
    fresh_orphan = storage_dir / "jobs" / str(uuid.uuid4())
    fresh_orphan.mkdir()
    not_a_job = storage_dir / "jobs" / "keep-me"
    not_a_job.mkdir()
    old_time = time.time() - 2 * 86400
    os.utime(orphan, (old_time, old_time))

    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(cleanup.tempfile, "gettempdir", lambda: str(temp))
    stale = temp / "clipgen-old.zip"
    stale.write_bytes(b"z" * 10)
    os.utime(stale, (old_time, old_time))
    current = temp / "clipgen-new.zip"
    current.write_bytes(b"z")

    report = cleanup.run_cleanup()

    assert report.orphan_dirs_removed == 1 and not orphan.exists()
    assert fresh_orphan.exists() and not_a_job.exists()
    assert report.temp_files_removed == 1 and not stale.exists() and current.exists()


def test_cleanup_skips_locked_files(db, storage_dir, monkeypatch):
    from app.core.exceptions import ConflictError

    monkeypatch.setenv("SOURCE_RETENTION_DAYS", "1")
    _finished_job(db, days_ago=3)

    def locked(job_id):
        raise ConflictError("source.mp4 is in use")

    monkeypatch.setattr(storage, "delete_source_files", locked)
    report = cleanup.run_cleanup()
    assert report.sources_removed == 0 and "in use" in report.skipped[0]


def test_worker_status_endpoint(client, storage_dir):
    assert client.get("/api/health/worker").json() == {"alive": False, "last_seen_seconds": None}
    heartbeat.write_heartbeat(started_at="x")
    body = client.get("/api/health/worker").json()
    assert body["alive"] is True and body["last_seen_seconds"] < 5
