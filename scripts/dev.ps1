# Starts the API (port 8000) and the web interface (port 8501), then opens the browser.
# Run from anywhere:   .\scripts\dev.ps1
# An old copy still running from an earlier session?   .\scripts\dev.ps1 -Restart
param([switch]$Restart)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

# A server left running from an earlier session keeps its port, so a new one would fail to start
# (in a window that is easy to miss) and the OLD code would keep answering. Check first.
function Get-PortOwner([int]$Port) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $listener) { return $null }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    [pscustomobject]@{ Port = $Port; Id = $listener.OwningProcess; Started = $process.CreationDate }
}

$busy = @(8000, 8501 | ForEach-Object { Get-PortOwner $_ } | Where-Object { $_ })
if ($busy) {
    if (-not $Restart) {
        foreach ($owner in $busy) {
            Write-Warning "Port $($owner.Port) is already in use by process $($owner.Id), started $($owner.Started)."
        }
        throw "Something is already running, probably an OLD copy of the app (it would keep answering with old code). Close it, or run:  .\scripts\dev.ps1 -Restart"
    }
    foreach ($owner in $busy) {
        Write-Host "Stopping process $($owner.Id) on port $($owner.Port)..."
        Stop-Process -Id $owner.Id -Force
    }
    Start-Sleep -Seconds 2
}

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
