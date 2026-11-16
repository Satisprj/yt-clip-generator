from app.models import Job, JobStatus
from app.worker.recovery import INTERRUPTED_MESSAGE, fail_interrupted_jobs


def _job(db, status):
    job = Job(youtube_url="https://www.youtube.com/watch?v=jNQXAC9IVRw",
              youtube_video_id="jNQXAC9IVRw", status=status)
    db.add(job)
    db.commit()
    return job


def test_marks_only_running_jobs_failed(db):
    running = [_job(db, s) for s in (JobStatus.FETCHING_INFO, JobStatus.DOWNLOADING,
                                     JobStatus.EXTRACTING_AUDIO, JobStatus.RENDERING)]
    queued = _job(db, JobStatus.QUEUED)
    done = _job(db, JobStatus.COMPLETED)

    assert fail_interrupted_jobs() == 4
    db.expire_all()
    for job in running:
        job = db.get(Job, job.id)
        assert job.status == JobStatus.FAILED
        assert job.error_message == INTERRUPTED_MESSAGE
        assert job.completed_at is not None
    assert db.get(Job, queued.id).status == JobStatus.QUEUED  # still waiting in the broker
    assert db.get(Job, done.id).status == JobStatus.COMPLETED
    assert fail_interrupted_jobs() == 0
