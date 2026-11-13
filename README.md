# YouTube AI Clip Generator

Paste a YouTube URL. The app transcribes the video, uses Claude to find the most
engaging moments, and renders vertical 9:16 clips (20–30 s) with burned-in captions.

- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Roadmap: [docs/ROADMAP.md](docs/ROADMAP.md)

> Use only with videos you own or have permission to use. Downloading from YouTube
> without permission violates YouTube's Terms of Service.

## Requirements (Windows)

| Tool | Install | Notes |
|------|---------|-------|
| Python 3.12 | python.org or `py` launcher | 3.11–3.13 supported |
| Node.js LTS | `winget install OpenJS.NodeJS.LTS` | |
| FFmpeg | `winget install Gyan.FFmpeg` | set `FFMPEG_PATH` / `FFPROBE_PATH` in `.env` if not on PATH |
| PostgreSQL 16 | `winget install PostgreSQL.PostgreSQL.16` | used for data **and** as the job queue |
| Claude Code | installed and logged in | Claude is called via `claude -p` with your subscription; no API key |

No Redis is needed on Windows: Celery uses PostgreSQL as its queue. On Linux/macOS you
can use Redis instead (`docker compose up -d`, then remove the `CELERY_*` lines from `.env`).

### Smart App Control compatibility

Windows Smart App Control blocks some native DLLs. The project avoids them:
- PostgreSQL driver: **pg8000** (pure Python) instead of psycopg.
- Frontend bundler: Rollup's **WebAssembly build** (`overrides` in `package.json`).
- Claude: the signed `claude.exe` instead of the Anthropic SDK.

## Setup

```powershell
# 1. Database user and database (run once; asks for the postgres superuser password)
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -U postgres -c "CREATE ROLE clipgen LOGIN PASSWORD 'clipgen';" -c "CREATE DATABASE clipgen OWNER clipgen;"

# 2. Backend
cd backend
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env      # then set FFMPEG_PATH / FFPROBE_PATH if needed

# 3. Frontend
cd ..\frontend
npm install
```

## Run

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```

This opens three windows: API (`:8000`), Celery worker, and frontend (`:5173`).
Open http://localhost:5173. The badge in the header shows whether every dependency is ready
(details at http://localhost:8000/api/health/ready).

## Tests and lint

```powershell
cd backend
.venv\Scripts\python -m pytest              # unit tests
.venv\Scripts\python -m ruff check .
$env:RUN_CLAUDE_CLI_TESTS = "1"; .venv\Scripts\python -m pytest -m integration   # real Claude call
cd ..\frontend; npm run build
```

## Project layout

```
backend/   FastAPI API, Celery worker, services, Alembic migrations, tests
frontend/  React + Vite + Tailwind
scripts/   dev.ps1 (start everything)
docs/      Architecture and roadmap
```
