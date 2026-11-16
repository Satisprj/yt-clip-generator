"""Application settings, loaded from environment variables and backend/.env."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    app_name: str = "YouTube AI Clip Generator"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api"
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # --- Database / queue ---
    database_url: str = "postgresql+pg8000://clipgen:clipgen@localhost:5432/clipgen"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    # --- Claude (via the Claude Code CLI, using the logged-in subscription) ---
    claude_cli_path: str = "claude"
    claude_cli_timeout_seconds: int = Field(default=600, gt=0)
    claude_model: str = "claude-opus-5-5"
    claude_fast_model: str = "claude-haiku-5-5"

    # --- Transcription ---
    whisper_model: str = "small"
    whisper_device: Literal["cpu", "cuda", "auto"] = "cpu"
    whisper_compute_type: str = "int8"
    # None = auto-detect. Set e.g. "en" or "ne" to skip detection and avoid mistakes.
    whisper_language: str | None = None
    # 1 = greedy decoding (fastest on CPU); 5 = beam search (slightly more accurate, slower).
    whisper_beam_size: int = Field(default=1, ge=1, le=10)
    # 0 = let CTranslate2 decide.
    whisper_cpu_threads: int = Field(default=0, ge=0)

    # --- Media tools ---
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    ytdlp_cookies_file: Path | None = None
    # JavaScript runtime yt-dlp uses for YouTube challenges (Node.js); empty to disable.
    js_runtime_path: str | None = "node"

    # --- Storage and limits ---
    storage_dir: Path = Path("storage")
    max_video_duration_seconds: int = Field(default=3 * 3600, gt=0)
    max_download_height: int = Field(default=1080, ge=144, le=2160)
    # Free space to keep on top of the expected download size.
    min_free_disk_mb: int = Field(default=2048, ge=0)
    clip_min_seconds: int = Field(default=20, gt=0)
    clip_max_seconds: int = Field(default=30, gt=0)
    max_clips_per_video: int = Field(default=10, gt=0, le=50)

    # --- Rendering ---
    render_width: int = Field(default=1080, ge=144)
    render_height: int = Field(default=1920, ge=256)
    # x264 speed/size trade-off: ultrafast ... veryfast ... medium (slower = smaller files)
    render_preset: str = "veryfast"
    render_crf: int = Field(default=20, ge=0, le=51)  # lower = better quality, bigger files
    # Caption font; empty = automatic by language (Arial, Nirmala UI for Devanagari, ...)
    subtitle_font: str | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("ytdlp_cookies_file", "js_runtime_path", "whisper_language",
                     "subtitle_font", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("storage_dir", "ytdlp_cookies_file", mode="after")
    @classmethod
    def _resolve_path(cls, value: Path | None) -> Path | None:
        if value is None or value.is_absolute():
            return value
        return (BACKEND_DIR / value).resolve()

    @model_validator(mode="after")
    def _check_clip_range(self) -> "Settings":
        if self.clip_min_seconds > self.clip_max_seconds:
            raise ValueError("CLIP_MIN_SECONDS must be <= CLIP_MAX_SECONDS")
        return self

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def jobs_dir(self) -> Path:
        return self.storage_dir / "jobs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
