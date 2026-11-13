from app.worker.tasks import ping


def test_ping_task_runs_locally():
    # .apply() executes the task in-process without needing Redis or a worker.
    assert ping.apply().get() == "pong"
