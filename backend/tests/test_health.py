from app.services import system_checks

ALL_CHECKS = ("check_database", "check_queue", "check_worker", "check_ffmpeg", "check_claude_cli")


def _ok() -> None:
    return None


def _fail() -> None:
    raise ConnectionError("connection refused")


def test_liveness(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "test"


def test_readiness_all_ok(client, monkeypatch):
    for name in ALL_CHECKS:
        monkeypatch.setattr(system_checks, name, _ok)
    response = client.get("/api/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"database", "queue", "worker", "ffmpeg", "claude_cli"}


def test_readiness_reports_failed_dependency(client, monkeypatch):
    for name in ALL_CHECKS:
        monkeypatch.setattr(system_checks, name, _ok)
    monkeypatch.setattr(system_checks, "check_queue", _fail)
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["queue"]["status"] == "error"
    assert "connection refused" in body["checks"]["queue"]["error"]
    assert body["checks"]["database"]["status"] == "ok"


def test_ffmpeg_check_reports_missing_binary(monkeypatch):
    monkeypatch.setenv("FFMPEG_PATH", "definitely-not-a-real-ffmpeg-binary")
    try:
        system_checks.check_ffmpeg()
    except RuntimeError as exc:
        assert "definitely-not-a-real-ffmpeg-binary" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_queue_check_rejects_unknown_scheme(monkeypatch):
    monkeypatch.setenv("CELERY_BROKER_URL", "amqp://guest@localhost//")
    try:
        system_checks.check_queue()
    except RuntimeError as exc:
        assert "amqp" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
