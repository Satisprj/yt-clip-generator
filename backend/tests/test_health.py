from app.services import system_checks


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
    for name in ("check_database", "check_redis", "check_ffmpeg"):
        monkeypatch.setattr(system_checks, name, _ok)
    response = client.get("/api/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"database", "redis", "ffmpeg"}


def test_readiness_reports_failed_dependency(client, monkeypatch):
    monkeypatch.setattr(system_checks, "check_database", _ok)
    monkeypatch.setattr(system_checks, "check_redis", _fail)
    monkeypatch.setattr(system_checks, "check_ffmpeg", _ok)
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"]["status"] == "error"
    assert "connection refused" in body["checks"]["redis"]["error"]
    assert body["checks"]["database"]["status"] == "ok"


def test_ffmpeg_check_reports_missing_binary(monkeypatch):
    monkeypatch.setenv("FFMPEG_PATH", "definitely-not-a-real-ffmpeg-binary")
    try:
        system_checks.check_ffmpeg()
    except RuntimeError as exc:
        assert "definitely-not-a-real-ffmpeg-binary" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
