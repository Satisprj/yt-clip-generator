import shutil
import uuid

import pytest

from app.core.exceptions import ExternalServiceError, InvalidInputError, ServiceUnavailableError
from app.models import Clip, Job, JobStatus, Transcript
from app.services import storage, transcription, youtube
from app.services.sentences import Word
from app.services.youtube import VideoInfo
from app.worker import pipeline, stages, tasks
from app.worker.context import StageContext

ID = "jNQXAC9IVRw"
INFO = VideoInfo(ID, "Me at the zoo", "jawed", 600, "https://i.ytimg.com/x.jpg", False)
WORDS = (
    Word(0.0, 0.4, " Hello", 0.9), Word(0.5, 0.9, " everyone.", 0.95),
    Word(1.2, 1.5, " This", 0.9), Word(1.6, 1.9, " is", 0.9), Word(2.0, 2.6, " great!", 0.8),
)


@pytest.fixture
def enqueued(monkeypatch):
    """Capture enqueued job IDs instead of sending them to the broker."""
    sent: list[str] = []
    monkeypatch.setattr(tasks.process_job, "delay", lambda job_id: sent.append(job_id))
    return sent


@pytest.fixture
def fake_youtube(monkeypatch, sample_video):
    """Replace network calls: metadata returns INFO, download copies a real sample video."""
    calls = {"download": 0}

    def fake_download(url, destination, *, on_progress=None, check_space=None):
        calls["download"] += 1
        if check_space:
            check_space(1_000_000)
        if on_progress:
            on_progress(0.5, "Downloading video: 50%")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(sample_video, destination)
        return destination

    def fake_transcribe(audio_path, *, duration_seconds=None, on_progress=None):
        calls["transcribe"] += 1
        assert audio_path.is_file()  # the real extracted audio is passed in
        if on_progress:
            on_progress(0.5)
        return transcription.TranscriptionResult(
            language="en", language_probability=0.99, duration_seconds=3.0, model="fake",
            words=list(calls["words"]),
        )

    class FakeClaude:
        def generate_json(self, *, system, prompt, schema, model=None, effort=None):
            calls["llm"] += 1
            if calls["llm_error"]:
                raise calls["llm_error"]
            if "moments" in schema["properties"]:
                return {"moments": calls["moments"]}
            return {"clips": [{"index": i, "title": f"Clip {i}", "description": "d",
                               "hashtags": ["tag"]} for i in range(10)]}

    calls.update(transcribe=0, llm=0, llm_error=None, words=list(WORDS), moments=[])
    monkeypatch.setattr(youtube, "fetch_video_info", lambda url: INFO)
    monkeypatch.setattr(youtube, "download_video", fake_download)
    monkeypatch.setattr(transcription, "get_model", lambda: None)
    monkeypatch.setattr(transcription, "transcribe", fake_transcribe)
    monkeypatch.setattr(stages, "ClaudeCliClient", FakeClaude)
    return calls


def long_speech(sentence_count=12, words_per_sentence=10, word_seconds=0.8):
    """Words forming sentences of ~8 s each, so 3 sentences make a 20-30 s clip."""
    words, t = [], 0.0
    for s in range(sentence_count):
        for w in range(words_per_sentence):
            text = f" Point{s}." if w == words_per_sentence - 1 else f" word{w}"
            words.append(Word(round(t, 3), round(t + word_seconds - 0.1, 3), text, 0.9))
            t += word_seconds
        t += 0.4
    return words


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


def test_retry_failed_job(client, db, enqueued):
    job = _queued_job(db)
    job.status, job.error_message, job.progress = JobStatus.FAILED, "boom", 40
    db.commit()

    response = client.post(f"/api/jobs/{job.id}/retry")
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["progress"], body["error_message"]) == ("queued", 0, None)
    assert body["completed_at"] is None
    assert enqueued == [str(job.id)]


def test_retry_rejects_non_failed_job(client, db, enqueued):
    job = _queued_job(db)
    response = client.post(f"/api/jobs/{job.id}/retry")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert enqueued == []
    assert client.post(f"/api/jobs/{uuid.uuid4()}/retry").status_code == 404


# --- Pipeline --------------------------------------------------------------


def _queued_job(db) -> Job:
    job = Job(youtube_url=f"https://www.youtube.com/watch?v={ID}", youtube_video_id=ID)
    db.add(job)
    db.commit()
    return job


def _reload(db, job: Job) -> Job:
    db.expire_all()
    return db.get(Job, job.id)


def test_pipeline_downloads_and_extracts_audio(db, storage_dir, fake_youtube):
    job = _queued_job(db)

    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    job = _reload(db, job)
    assert job.status == JobStatus.COMPLETED
    assert job.progress == 100
    assert (job.title, job.channel, job.duration_seconds) == ("Me at the zoo", "jawed", 600)
    assert job.completed_at is not None and job.error_message is None

    files = storage.job_files(job.id)
    assert files.dir.parent == storage_dir / "jobs"
    assert files.source_video.is_file()
    assert files.audio.is_file()


def test_pipeline_reuses_existing_files_on_retry(db, storage_dir, fake_youtube):
    job = _queued_job(db)
    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    audio_mtime = storage.job_files(job.id).audio.stat().st_mtime_ns

    job = _reload(db, job)
    job.status = JobStatus.QUEUED  # as if retried
    db.commit()
    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    assert fake_youtube["download"] == 1  # second run skipped the download
    assert storage.job_files(job.id).audio.stat().st_mtime_ns == audio_mtime


def test_pipeline_rejects_video_without_audio(db, storage_dir, fake_youtube, monkeypatch,
                                               silent_video):
    def download_silent(url, destination, **kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(silent_video, destination)
        return destination

    monkeypatch.setattr(youtube, "download_video", download_silent)
    job = _queued_job(db)
    assert pipeline.run_pipeline(job.id) == JobStatus.FAILED
    assert "no audio track" in _reload(db, job).error_message


def test_pipeline_fails_when_disk_is_full(db, storage_dir, fake_youtube, monkeypatch):
    def no_space(directory, required_bytes):
        raise ServiceUnavailableError("Not enough disk space: 0.1 GB free.")

    monkeypatch.setattr(storage, "ensure_free_space", no_space)
    job = _queued_job(db)
    assert pipeline.run_pipeline(job.id) == JobStatus.FAILED
    job = _reload(db, job)
    assert job.error_message.startswith("Not enough disk space")
    assert job.title == "Me at the zoo"


def test_pipeline_keeps_info_when_video_rejected(db, storage_dir, fake_youtube, monkeypatch):
    too_long = VideoInfo(ID, "Very long stream", "c", 5 * 3600, None, False)
    monkeypatch.setattr(youtube, "fetch_video_info", lambda url: too_long)
    job = _queued_job(db)

    assert pipeline.run_pipeline(job.id) == JobStatus.FAILED
    job = _reload(db, job)
    assert "limit" in job.error_message
    assert job.title == "Very long stream"  # details kept for the user
    assert fake_youtube["download"] == 0


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (InvalidInputError("This video is private."), "This video is private."),
        (ExternalServiceError("YouTube error: HTTP 503"), "YouTube error: HTTP 503"),
        (RuntimeError("secret internals"), "Unexpected error while downloading video"),
    ],
)
def test_pipeline_records_download_failures(db, storage_dir, fake_youtube, monkeypatch,
                                            error, expected):
    def boom(url, destination, **kwargs):
        raise error

    monkeypatch.setattr(youtube, "download_video", boom)
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


def test_process_job_task_runs_pipeline(db, storage_dir, fake_youtube):
    job = _queued_job(db)
    assert tasks.process_job.apply(args=[str(job.id)]).get() == "completed"


# --- Progress reporting ----------------------------------------------------


def test_stage_context_maps_and_throttles_progress(db):
    job = _queued_job(db)
    ctx = StageContext(db, job, start_percent=10, span_percent=50, min_interval=60)

    ctx.report(0.5, "half")       # first report is saved
    assert (job.progress, job.stage_message) == (35, "half")
    ctx.report(0.8, "throttled")  # within min_interval: ignored
    assert (job.progress, job.stage_message) == (35, "half")
    ctx.report(1.0, "done")       # completion is always saved
    assert (job.progress, job.stage_message) == (60, "done")
    assert _reload(db, job).progress == 60


# --- Transcription ---------------------------------------------------------


def test_pipeline_stores_transcript_and_sentences(db, storage_dir, fake_youtube):
    job = _queued_job(db)
    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED

    t = db.query(Transcript).filter_by(job_id=job.id).one()
    assert (t.language, t.word_count, t.sentence_count, t.model) == ("en", 5, 2, "fake")
    assert [s["text"] for s in t.sentences] == ["Hello everyone.", "This is great!"]
    assert t.sentences[1] == {"id": 1, "start": 1.2, "end": 2.6, "text": "This is great!",
                              "first_word": 2, "last_word": 4}
    assert t.words[0] == {"start": 0.0, "end": 0.4, "text": " Hello", "probability": 0.9}


def test_retry_reuses_existing_transcript(db, storage_dir, fake_youtube):
    job = _queued_job(db)
    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    job = _reload(db, job)
    job.status = JobStatus.QUEUED
    db.commit()
    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    assert fake_youtube["transcribe"] == 1
    assert db.query(Transcript).filter_by(job_id=job.id).count() == 1


def test_pipeline_fails_when_no_speech(db, storage_dir, fake_youtube, monkeypatch):
    silent = transcription.TranscriptionResult("en", 0.5, 3.0, "fake", [])
    monkeypatch.setattr(transcription, "transcribe", lambda *a, **k: silent)
    job = _queued_job(db)
    assert pipeline.run_pipeline(job.id) == JobStatus.FAILED
    assert "No speech" in _reload(db, job).error_message
    assert db.query(Transcript).count() == 0


def test_transcript_api(client, db, storage_dir, fake_youtube):
    job = _queued_job(db)
    not_ready = client.get(f"/api/jobs/{job.id}/transcript")
    assert not_ready.status_code == 404
    assert not_ready.json()["error"]["code"] == "transcript_not_ready"
    assert client.get(f"/api/jobs/{uuid.uuid4()}/transcript").json()["error"]["code"] == \
        "not_found"

    pipeline.run_pipeline(job.id)
    body = client.get(f"/api/jobs/{job.id}/transcript").json()
    assert body["sentence_count"] == 2 and "words" not in body
    assert body["sentences"][0]["text"] == "Hello everyone."
    with_words = client.get(f"/api/jobs/{job.id}/transcript",
                            params={"include_words": "true"}).json()
    assert len(with_words["words"]) == 5


# --- Highlight detection ---------------------------------------------------

SCORES = {"hook": 9, "standalone": 8, "payoff": 8, "emotion": 7, "shareability": 8}


def _moment(a, b, category="educational", scores=SCORES):
    return {"start_sentence": a, "end_sentence": b, "category": category, "hook": f"Hook {a}",
            "reason": "Good", "scores": scores}


def test_pipeline_selects_and_stores_clips(db, storage_dir, fake_youtube):
    fake_youtube["words"] = long_speech()
    fake_youtube["moments"] = [_moment(1, 3), _moment(7, 9, "funny", {k: 5 for k in SCORES})]
    job = _queued_job(db)

    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    clips = db.query(Clip).filter_by(job_id=job.id).order_by(Clip.rank).all()
    assert [(c.rank, c.first_sentence, c.last_sentence) for c in clips] == [(1, 1, 3), (2, 7, 9)]
    first = clips[0]
    assert 20 <= first.duration_seconds <= 30
    assert first.end_seconds - first.start_seconds == pytest.approx(first.duration_seconds)
    assert (first.title, first.hashtags, first.category) == ("Clip 0", ["#tag"], "educational")
    assert first.viral_score > clips[1].viral_score
    assert first.transcript_text.startswith("word0")
    assert fake_youtube["llm"] == 2  # moments + metadata
    assert storage.job_files(job.id).highlights.is_file()
    assert _reload(db, job).stage_message.startswith("2 clips selected")


def test_retry_reuses_selected_clips(db, storage_dir, fake_youtube):
    fake_youtube["words"] = long_speech()
    fake_youtube["moments"] = [_moment(1, 3)]
    job = _queued_job(db)
    pipeline.run_pipeline(job.id)
    job = _reload(db, job)
    job.status = JobStatus.QUEUED
    db.commit()
    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    assert fake_youtube["llm"] == 2  # no new Claude calls
    assert db.query(Clip).filter_by(job_id=job.id).count() == 1


def test_no_usable_moments_completes_with_message(db, storage_dir, fake_youtube):
    job = _queued_job(db)  # default transcript is ~3 s long: nothing fits 20-30 s
    fake_youtube["moments"] = [_moment(0, 1)]
    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    assert _reload(db, job).stage_message.startswith("No moments")
    assert db.query(Clip).count() == 0


def test_claude_failure_fails_job_and_retry_resumes(db, storage_dir, fake_youtube):
    from app.services.llm import LLMError

    fake_youtube["words"] = long_speech()
    fake_youtube["moments"] = [_moment(1, 3)]
    fake_youtube["llm_error"] = LLMError("Claude CLI returned an error: usage limit reached")
    job = _queued_job(db)
    assert pipeline.run_pipeline(job.id) == JobStatus.FAILED
    assert "usage limit" in _reload(db, job).error_message

    fake_youtube["llm_error"] = None
    job = _reload(db, job)
    job.status = JobStatus.QUEUED
    db.commit()
    assert pipeline.run_pipeline(job.id) == JobStatus.COMPLETED
    assert fake_youtube["transcribe"] == 1  # transcript was reused
    assert db.query(Clip).filter_by(job_id=job.id).count() == 1


def test_clips_api(client, db, storage_dir, fake_youtube):
    fake_youtube["words"] = long_speech()
    fake_youtube["moments"] = [_moment(1, 3)]
    job = _queued_job(db)
    assert client.get(f"/api/jobs/{job.id}/clips").json() == []
    pipeline.run_pipeline(job.id)

    clips = client.get(f"/api/jobs/{job.id}/clips").json()
    assert len(clips) == 1
    assert clips[0]["rank"] == 1 and clips[0]["title"] == "Clip 0"
    assert set(clips[0]["scores"]) == set(SCORES)
    assert client.get(f"/api/jobs/{uuid.uuid4()}/clips").status_code == 404
