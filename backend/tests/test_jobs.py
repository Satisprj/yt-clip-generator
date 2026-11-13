import uuid

import pytest

from app.core.exceptions import ExternalServiceError, InvalidInputError
from app.models import Job, JobStatus
from app.services import youtube
from app.services.youtube import VideoInfo
from app.worker import pipeline, tasks

ID = "jNQXAC9IVRw"
INFO = VideoInfo(ID, "Me at the zoo", "jawed", 600, "https://i.ytimg.com/x.jpg", False)


@pytest.fixture
def enqueued(monkeypatch):
    """Capture enqueued job IDs instead of sending them to the broker."""
    sent: list[str] = []
    monkeypatch.setattr(tasks.process_job, "delay", lambda job_id: sent.append(job_id))
    return sent


# --- API -------------------------------------------------------------------


def test_create_job_queues_and_returns_job(client, db, enqueued):
    response = client.post("/api/jobs", json={"url": f"https://youtu.be/{ID}?si=x"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["youtube_video_id"] == ID
    assert body["youtube_url"] == f"https://www.youtube.com/watch?v={ID}"
    assert enqueued == [body["id"]]
    assert db.get(Job, uuid.UUID(body["id"])) is not None


def test_create_job_rejects_invalid_url(client, db, enqueued):
    response = client.post("/api/jobs", json={"url": "https://vimeo.com/1"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_input"
    assert enqueued == []


def test_create_job_marks_failed_when_queue_down(client, db, monkeypatch):
    def broken(job_id):
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.process_job, "delay", broken)
    response = client.post("/api/jobs", json={"url": f"https://youtu.be/{ID}"})
    assert response.status_code == 503
    job = db.query(Job).one()
    assert job.status == JobStatus.FAILED
    assert "queue" in job.error_message


def test_get_job_and_404(client, db, enqueued):
    created = client.post("/api/jobs", json={"url": f"https://youtu.be/{ID}"}).json()
    assert client.get(f"/api/jobs/{created['id']}").json()["id"] == created["id"]

    missing = client.get(f"/api/jobs/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    assert client.get("/api/jobs/not-a-uuid").status_code == 422


def test_list_jobs_newest_first_with_pagination(client, db, enqueued):
    ids = [client.post("/api/jobs", json={"url": f"https://youtu.be/{ID}"}).json()["id"]
           for _ in range(3)]
    page = client.get("/api/jobs", params={"limit": 2}).json()
    assert page["total"] == 3
    assert [j["id"] for j in page["items"]] == ids[::-1][:2]
    rest = client.get("/api/jobs", params={"limit": 2, "offset": 2}).json()
    assert [j["id"] for j in rest["items"]] == [ids[0]]
    assert client.get("/api/jobs", params={"limit": 500}).status_code == 422


# --- Pipeline --------------------------------------------------------------


def _queued_job(db) -> Job:
    job = Job(youtube_url=f"https://www.youtube.com/watch?v={ID}", youtube_video_id=ID)
    db.add(job)
    db.commit()
    return job


def _reload(db, job: Job) -> Job:
    db.expire_all()
    return db.get(Job, job.id)


def test_pipeline_fetches_info_and_completes(db, monkeypatch):
    monkeypatch.setattr(youtube, "fetch_video_info", lambda url: INFO)
    job = _queued_job(db)

    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    job = _reload(db, job)
    assert job.status == JobStatus.COMPLETED
    assert job.progress == 100
    assert (job.title, job.channel, job.duration_seconds) == ("Me at the zoo", "jawed", 600)
    assert job.completed_at is not None
    assert job.error_message is None


def test_pipeline_keeps_info_when_video_rejected(db, monkeypatch):
    too_long = VideoInfo(ID, "Very long stream", "c", 5 * 3600, None, False)
    monkeypatch.setattr(youtube, "fetch_video_info", lambda url: too_long)
    job = _queued_job(db)

    assert pipeline.run_pipeline(job.id) == JobStatus.FAILED
    job = _reload(db, job)
    assert job.status == JobStatus.FAILED
    assert "limit" in job.error_message
    assert job.title == "Very long stream"  # details kept for the user


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (InvalidInputError("This video is private."), "This video is private."),
        (ExternalServiceError("YouTube error: HTTP 503"), "YouTube error: HTTP 503"),
        (RuntimeError("secret internals"), "Unexpected error while fetching video information"),
    ],
)
def test_pipeline_records_failures(db, monkeypatch, error, expected):
    def boom(url):
        raise error

    monkeypatch.setattr(youtube, "fetch_video_info", boom)
    job = _queued_job(db)

    assert pipeline.run_pipeline(job.id) == JobStatus.FAILED
    job = _reload(db, job)
    assert job.error_message.startswith(expected)
    assert "secret internals" not in job.error_message


def test_pipeline_skips_finished_and_missing_jobs(db, monkeypatch):
    monkeypatch.setattr(youtube, "fetch_video_info", lambda url: pytest.fail("must not run"))
    job = _queued_job(db)
    job.status = JobStatus.COMPLETED
    db.commit()
    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    assert pipeline.run_pipeline(uuid.uuid4()) is None


def test_process_job_task_runs_pipeline(db, monkeypatch):
    monkeypatch.setattr(youtube, "fetch_video_info", lambda url: INFO)
    job = _queued_job(db)
    assert tasks.process_job.apply(args=[str(job.id)]).get() == "completed"
