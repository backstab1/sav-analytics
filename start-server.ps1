$ErrorActionPreference = "Stop"

$projectPath = $PSScriptRoot
$pythonPath = Join-Path $projectPath ".venv\Scripts\python.exe"
$appBaseUrl = "http://127.0.0.1:8000"
$appUrl = "$appBaseUrl/?v=20260812-report-settings-1"
$healthUrl = "$appBaseUrl/api/health"

Set-Location $projectPath
$Host.UI.RawUI.WindowTitle = "SAV Analytics Server"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host "Python environment .venv was not found." -ForegroundColor Red
    Write-Host "Install the project dependencies first."
    Read-Host "Press Enter to close"
    exit 1
}

try {
    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
    if ($health.status -eq "ok") {
        Write-Host "SAV Analytics is already running. Opening the browser."
        Start-Process $appUrl
        Start-Sleep -Seconds 2
        exit 0
    }
}
catch {
    # The server is not running yet, so continue with startup.
}

$env:PYTHONUTF8 = "1"

Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
    "-NoProfile",
    "-Command",
    "Start-Sleep -Seconds 2; Start-Process '$appUrl'"
)

Write-Host "Starting SAV Analytics..." -ForegroundColor Green
Write-Host "Address: $appUrl"
Write-Host ""
Write-Host "The server works while this window is open."
Write-Host "Press Ctrl+C or close this window to stop it."
Write-Host ""

& $pythonPath -m uvicorn sav_analytics.api:app --app-dir src --host 127.0.0.1 --port 8000 --reload

Write-Host ""
Write-Host "Server stopped."
Read-Host "Press Enter to close"
