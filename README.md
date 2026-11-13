# YouTube AI Clip Generator

Paste a YouTube URL. The app transcribes the video, uses Claude to find the most
engaging moments, and renders vertical 9:16 clips (20–30 s) with burned-in captions.

- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Roadmap: [docs/ROADMAP.md](docs/ROADMAP.md)

> Use only with videos you own or have permission to use. Downloading from YouTube
> without permission violates YouTube's Terms of Service.

## Recommended setup on Windows: WSL2

Windows Smart App Control blocks native DLLs used by faster-whisper, the Anthropic SDK
and psycopg, so the backend runs inside WSL2 (Ubuntu), where everything works natively.

1. In an **administrator** PowerShell: `wsl --install -d Ubuntu-24.04`, then restart.
2. Open **Ubuntu** from the Start menu and create a Linux username and password.
3. In Ubuntu:
   ```bash
   cd /mnt/c/Users/<you>/yt-clip-generator
   bash scripts/setup-wsl.sh
   ```
   This installs Python, FFmpeg, Redis, PostgreSQL and Node.js, creates the database and
   virtualenv, installs all packages and runs the tests.

The manual steps below are for Linux/macOS or a Windows machine without Smart App Control.

## Prerequisites

| Tool | Version | Install (Windows) |
|------|---------|-------------------|
| Python | 3.12 | already installed (`py -3.12`) |
| Node.js | 20 LTS or newer | `winget install OpenJS.NodeJS.LTS` |
| FFmpeg | 6 or newer | `winget install Gyan.FFmpeg` |
| PostgreSQL + Redis | 16 / 7 | `docker compose up -d` (needs Docker Desktop), or run them in WSL2 |

## Backend

```bash
cd backend
py -3.12 -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env            # then set ANTHROPIC_API_KEY

uvicorn app.main:app --reload     # http://localhost:8000/api/docs
celery -A app.worker.celery_app worker --loglevel=INFO --pool=solo   # second terminal
pytest                            # tests
ruff check .                      # lint
```

Check dependencies: `GET http://localhost:8000/api/health/ready`.

## Frontend

```bash
cd frontend
npm install
npm run dev                       # http://localhost:5173 (proxies /api to :8000)
```

## Project layout

```
backend/   FastAPI API, Celery worker, services, Alembic migrations, tests
frontend/  React + Vite + Tailwind
docs/      Architecture and roadmap
```
