# Starts the API (port 8000) and the web interface (port 8501), then opens the browser.
# Run from anywhere:   .\scripts\dev.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "No .venv found. Create it first: py -3.12 -m venv .venv ; .venv\Scripts\pip install -e '.[dev,frontend]'"
}
if (-not (Test-Path ".env")) {
    Write-Warning "No .env file. Copy .env.example to .env and set OPENAI_API_KEY there (answers need it)."
}

# Create or update the database tables.
& .\.venv\Scripts\alembic.exe -c backend\alembic.ini upgrade head

# Each server gets its own window, so you can see its log and close it with Ctrl+C.
Start-Process powershell -ArgumentList "-NoExit", "-Command",
    "Set-Location '$root'; .\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --port 8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command",
    "Set-Location '$root'; .\.venv\Scripts\streamlit.exe run frontend\streamlit_app.py --server.port 8501"

Start-Sleep -Seconds 8   # the API takes a few seconds to load its libraries
Start-Process "http://localhost:8501"
Write-Host "Web interface: http://localhost:8501   API docs: http://localhost:8000/docs"
