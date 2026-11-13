import pytest
from pydantic import ValidationError

from app.core.config import BACKEND_DIR, Settings


def test_cors_origins_accepts_comma_separated_string(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    assert Settings().cors_origins == ["http://a.test", "http://b.test"]


def test_relative_storage_dir_resolves_from_backend_dir(monkeypatch):
    monkeypatch.setenv("STORAGE_DIR", "my_storage")
    assert Settings().storage_dir == (BACKEND_DIR / "my_storage").resolve()


def test_empty_api_key_is_treated_as_missing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
    assert Settings().anthropic_api_key is None


def test_api_key_is_not_leaked_in_repr(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")
    settings = Settings()
    assert "sk-secret-value" not in repr(settings)
    assert settings.anthropic_api_key.get_secret_value() == "sk-secret-value"


def test_clip_min_greater_than_max_is_rejected(monkeypatch):
    monkeypatch.setenv("CLIP_MIN_SECONDS", "40")
    monkeypatch.setenv("CLIP_MAX_SECONDS", "30")
    with pytest.raises(ValidationError):
        Settings()


def test_broker_defaults_to_redis_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/3")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.broker_url == "redis://example:6379/3"
    assert settings.result_backend == "redis://example:6379/3"
