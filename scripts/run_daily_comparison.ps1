# Daily social comparison refresh for Windows Task Scheduler.
# Uses the project venv, absolute DATABASE_PATH (same file as the webapp),
# and the existing CLI. Does not start Meta calls when -Preflight is set.
param(
    [switch]$Preflight
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Script = Join-Path $ProjectRoot "scripts\refresh_comparison.py"
$DbPath = Join-Path $ProjectRoot "data\social_search.db"
$LogDir = Join-Path $ProjectRoot "logs"
$Stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$LogFile = Join-Path $LogDir "daily_comparison_$Stamp.log"

if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python"
}
if (-not (Test-Path $Script)) {
    throw "CLI script not found: $Script"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Set-Location $ProjectRoot
$env:DATABASE_PATH = $DbPath

$argList = @($Script)
if ($Preflight) {
    $argList += "--preflight"
} else {
    $argList += @("--source", "daily")
}

$transcriptStarted = $false
try {
    Start-Transcript -Path $LogFile -Force | Out-Null
    $transcriptStarted = $true
    Write-Output "project_root=$ProjectRoot"
    Write-Output "python=$Python"
    Write-Output "database_path=$env:DATABASE_PATH"
    Write-Output "preflight=$Preflight"
    Write-Output "started_at=$(Get-Date -Format o)"
    & $Python @argList
    $code = $LASTEXITCODE
    Write-Output "exit_code=$code"
    Write-Output "finished_at=$(Get-Date -Format o)"
    exit $code
}
finally {
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
}
