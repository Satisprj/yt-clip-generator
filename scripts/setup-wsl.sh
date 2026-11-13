#!/usr/bin/env bash
# One-time development setup inside WSL2 Ubuntu 24.04.
# Run from the project root:   bash scripts/setup-wsl.sh
#
# Installs: Python 3.12 venv, FFmpeg, Redis, PostgreSQL, Node.js 22,
# creates the "clipgen" database, backend virtualenv, and frontend packages.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The virtualenv lives in the Linux home folder: it is much faster than /mnt/c and
# does not clash with a Windows virtualenv in backend/.venv.
VENV_DIR="${CLIPGEN_VENV:-$HOME/.venvs/clipgen}"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

step "Installing system packages (sudo password required)"
sudo apt-get update -y
sudo apt-get install -y --no-install-recommends \
  python3 python3-venv python3-dev build-essential \
  ffmpeg redis-server postgresql postgresql-contrib \
  curl ca-certificates git

step "Installing Node.js 22 (if missing or older than 20)"
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

step "Starting PostgreSQL and Redis"
sudo service postgresql start
sudo service redis-server start

step "Creating database role and database 'clipgen'"
sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'clipgen') THEN
    CREATE ROLE clipgen LOGIN PASSWORD 'clipgen';
  END IF;
END $$;
SQL
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='clipgen'" | grep -q 1; then
  sudo -u postgres createdb -O clipgen clipgen
fi

step "Creating Python virtualenv at $VENV_DIR"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/backend/requirements-dev.txt"

if [ ! -f "$PROJECT_DIR/backend/.env" ]; then
  step "Creating backend/.env from .env.example (set CLAUDE_CLI_PATH if needed)"
  cp "$PROJECT_DIR/backend/.env.example" "$PROJECT_DIR/backend/.env"
fi

step "Installing frontend packages"
(cd "$PROJECT_DIR/frontend" && npm install)

step "Running backend tests"
(cd "$PROJECT_DIR/backend" && "$VENV_DIR/bin/python" -m pytest -q)

step "Done"
cat <<EOF
Activate the virtualenv:   source $VENV_DIR/bin/activate
Start services (after each WSL restart):
  sudo service postgresql start && sudo service redis-server start
Run (separate terminals, from the project folder):
  cd backend && uvicorn app.main:app --reload --host 0.0.0.0
  cd backend && celery -A app.worker.celery_app worker --loglevel=INFO
  cd frontend && npm run dev -- --host
EOF
