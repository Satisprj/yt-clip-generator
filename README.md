# YouTube AI Clip Generator

Paste a YouTube link. The app downloads the video, transcribes it locally with Whisper, asks
Claude to pick the most engaging moments, and renders vertical 1080×1920 clips (20–30 s)
that follow the speaker's face and have word-by-word captions, ready for YouTube Shorts,
Instagram Reels and TikTok.

- How it works: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Build history: [docs/ROADMAP.md](docs/ROADMAP.md)

> **Use only with videos you own or have permission to use.** Downloading from YouTube
> without permission violates YouTube's Terms of Service, and reposting other people's
> content is usually copyright infringement.

## Contents

1. [Requirements](#requirements-windows)
2. [Setup](#setup-once)
3. [Run](#run)
4. [Using the app](#using-the-app)
5. [Settings](#settings-backendenv)
6. [How long does it take?](#how-long-does-it-take)
7. [Troubleshooting](#troubleshooting)
8. [Development](#development)

## Requirements (Windows)

| Tool | Install | Notes |
|------|---------|-------|
| Python 3.12 | python.org or the `py` launcher | 3.11–3.13 supported |
| Node.js LTS | `winget install OpenJS.NodeJS.LTS` | |
| FFmpeg | `winget install Gyan.FFmpeg` | set `FFMPEG_PATH` / `FFPROBE_PATH` in `.env` if it isn't on PATH |
| PostgreSQL 16 | `winget install PostgreSQL.PostgreSQL.16` | stores the data **and** acts as the job queue (no Redis needed) |
| Claude Code | installed and logged in (`claude` works in a terminal) | Claude runs through `claude -p` on your subscription: no API key. Subscription use is for personal use; offering the app to others needs an API key |

**Smart App Control:** Windows can block unsigned native libraries. This project avoids the
ones that were blocked: pg8000 (pure Python) instead of psycopg, Rollup's WebAssembly build
for the frontend, and `claude.exe` instead of the Anthropic SDK. Run Alembic and Celery as
`python -m alembic` / `python -m celery` (the generated `.exe` launchers can be blocked).

## Setup (once)

```powershell
# 1. Database user, app database and test database (asks for the postgres password)
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -U postgres `
  -c "CREATE ROLE clipgen LOGIN PASSWORD 'clipgen';" `
  -c "CREATE DATABASE clipgen OWNER clipgen;" `
  -c "CREATE DATABASE clipgen_test OWNER clipgen;"

# 2. Backend
cd backend
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env    # then edit: FFMPEG_PATH, FFPROBE_PATH, JS_RUNTIME_PATH if needed

# 3. Frontend
cd ..\frontend
npm install
```

The first transcription downloads the Whisper model (`small`, about 480 MB) once.

## Run

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```

This applies database migrations, then opens three windows: **clipgen API** (port 8000),
**clipgen worker** (does all the processing), and **clipgen frontend** (port 5173).
Open **http://localhost:5173**. Close the three windows to stop.

The badge in the header turns green when the database, queue, worker, FFmpeg and Claude
CLI are all ready (details: http://localhost:8000/api/health/ready).

## Using the app

1. **New:** paste a YouTube link and click **Generate clips**. If that video is already
   being processed, you're taken to the existing job.
2. **Job page:** a stage tracker (Video info → Download → Audio → Transcribe →
   Highlights → Render), live progress with time-left estimates, and clips that appear one
   by one while they render. If no worker is running, a warning says so.
3. **Clips:** each has a vertical player, **Download MP4**, **Subtitles (.srt)**, **Copy
   caption** (title, description, hashtags), and **Why this clip?** (Claude's reasoning
   and scores).
4. **Actions** on finished jobs:
   - **Retry** (failed jobs): continues where it stopped; downloads and transcripts are reused.
   - **Download all as ZIP**: every clip, its `.srt` file and a `captions.txt`.
   - **New highlights**: Claude picks new moments (about 2 min plus rendering).
   - **Re-render clips**: render the same clips again (e.g. after changing settings).
   - **Free up space**: delete the downloaded video and audio but keep the clips.
   - **Delete job**: removes everything for that job.
5. **History:** all jobs with clip count and disk use, total disk use, and delete.

## Settings (`backend\.env`)

The main ones (all are documented in `backend\.env.example`):

| Setting | Default | What it does |
|---------|---------|--------------|
| `CLAUDE_MODEL` / `CLAUDE_FAST_MODEL` | `claude-opus-5-5` / `claude-haiku-5-5` | picks moments / writes titles |
| `CLIP_MIN_SECONDS`, `CLIP_MAX_SECONDS` | 20, 30 | clip length (whole sentences; up to 2 s shorter or 4 s longer if needed) |
| `MAX_CLIPS_PER_VIDEO` | 10 | |
| `MAX_VIDEO_DURATION_SECONDS` | 10800 (3 h) | longer videos are rejected |
| `MAX_DOWNLOAD_HEIGHT` | 1080 | 720 roughly halves download time |
| `WHISPER_MODEL` | `small` | `base` is 2–3× faster, less accurate |
| `WHISPER_LANGUAGE` | auto | e.g. `en`, `ne`, `hi` to skip language detection |
| `RENDER_PRESET`, `RENDER_CRF` | `veryfast`, 20 | encoding speed and quality |
| `SUBTITLE_FONT` | by language | Arial; Nirmala UI for Devanagari; YaHei/Yu Gothic/Malgun Gothic for CJK |
| `SOURCE_RETENTION_DAYS` | 7 | the worker deletes downloaded videos this long after a job finishes (clips kept; 0 = never) |
| `JOB_RETENTION_DAYS` | 0 (never) | delete whole finished jobs after N days |

Restart the worker window after changing settings.

## How long does it take?

Measured on this setup (i5-1135G7 CPU, about 0.5 MB/s internet):

| Step | Speed | 10-min video | 30-min video |
|------|-------|-------------|--------------|
| Download (1080p) | ~20–25 MB per video minute | ~8 min | ~20–30 min |
| Transcription (`small`, CPU) | ~1× real time | ~10 min | ~25–35 min |
| Claude (highlights + titles) | | < 1 min | 1–2 min |
| Rendering | ~55 s per 30 s clip | ~5–10 min | ~8–10 min |
| **Total** | | **~25 min** | **~55–80 min** |

`MAX_DOWNLOAD_HEIGHT=720` plus `WHISPER_MODEL=base` brings a 30-min video to about 35 min.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Job stays **Queued**, page says "No worker is running" | Start the app with `scripts\dev.ps1`, or check the **clipgen worker** window for errors. Its log is also in `backend\storage\worker.log` |
| Job **failed: interrupted** | The worker was closed during the job. Click **Retry**: it continues where it stopped (partial downloads resume) |
| "No speech was detected" | The video has no speech (or only music). Speech over loud music is retried automatically without the voice filter |
| "Claude CLI returned an error" / usage limit | Your Claude plan's limit was reached, or Claude Code is logged out (`claude` in a terminal). Retry later; the download and transcript are kept |
| YouTube: "bot check" or "age-restricted" | Export your YouTube cookies to a file and set `YTDLP_COOKIES_FILE` |
| YouTube download errors after it worked before | YouTube changed something: `backend\.venv\Scripts\python -m pip install -U "yt-dlp[default]"` |
| "File is in use" when deleting | Close the clip in the browser/player and try again |
| Header badge yellow | Hover it to see which check fails (database, queue, worker, FFmpeg, Claude CLI) |
| A `.exe` is "blocked by an Application Control policy" | Smart App Control; use `python -m <tool>` instead of the `.exe` launcher |

## Development

```powershell
cd backend
.venv\Scripts\python -m pytest            # ~185 tests on the clipgen_test database (~3.5 min; real FFmpeg rendering)
.venv\Scripts\python -m ruff check .
$env:RUN_CLAUDE_CLI_TESTS = "1"; $env:RUN_YOUTUBE_TESTS = "1"; $env:RUN_WHISPER_TESTS = "1"
.venv\Scripts\python -m pytest -m integration   # real Claude CLI, YouTube and Whisper (uses your subscription)
cd ..\frontend; npm run build
```

New database changes: edit `backend/app/models`, then
`python -m alembic revision --autogenerate -m "..."` and `python -m alembic upgrade head`.

```
backend/   FastAPI API, Celery worker and pipeline, services, Alembic migrations, tests
frontend/  React + Vite + Tailwind
scripts/   dev.ps1 (start everything)
docs/      Architecture and roadmap
```

On Linux/macOS, Redis can be used as the queue (`docker compose up -d`, then remove the
`CELERY_*` lines from `.env`), and the worker can run without `--pool=solo`.
