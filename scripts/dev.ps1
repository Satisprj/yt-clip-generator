# Starts the API, the Celery worker and the frontend, each in its own window.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File scripts\dev.ps1

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'

$python = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw "Backend virtualenv not found at $python. Follow the setup steps in README.md."
}

$npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $npm) { $npm = 'C:\Program Files\nodejs\npm.cmd' }
if (-not (Test-Path $npm)) { throw 'npm not found. Install Node.js (winget install OpenJS.NodeJS.LTS).' }

if (-not (Get-Service -Name 'postgresql*' -ErrorAction SilentlyContinue | Where-Object Status -eq 'Running')) {
    Write-Warning 'The PostgreSQL service is not running. Start it from services.msc.'
}

Write-Host 'Applying database migrations...' -ForegroundColor Cyan
Push-Location $backend
try {
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed (is PostgreSQL running?).' }
} finally {
    Pop-Location
}

function Start-DevWindow([string]$title, [string]$dir, [string]$command) {
    $full = "`$Host.UI.RawUI.WindowTitle = '$title'; $command"
    Start-Process powershell -WorkingDirectory $dir -ArgumentList '-NoExit', '-Command', $full
}

Start-DevWindow 'clipgen API' $backend "& '$python' -m uvicorn app.main:app --reload --port 8000"
Start-DevWindow 'clipgen worker' $backend "& '$python' -m celery -A app.worker.celery_app worker --pool=solo --loglevel=INFO"
Start-DevWindow 'clipgen frontend' $frontend "& '$npm' run dev"

Write-Host ''
Write-Host 'Started 3 windows:' -ForegroundColor Cyan
Write-Host '  App:      http://localhost:5173'
Write-Host '  API docs: http://localhost:8000/api/docs'
Write-Host '  Health:   http://localhost:8000/api/health/ready'
Write-Host 'Close the windows (or press Ctrl+C in each) to stop.'
