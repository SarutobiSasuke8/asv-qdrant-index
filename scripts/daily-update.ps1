# Daily diff ingest for the asv-engineering index (Windows Task Scheduler).
#
# Refreshes a dedicated shallow clone of the ASV, never your working copy,
# then re-embeds only changed notes. QDRANT_URL and QDRANT_API_KEY come from
# the user environment. The log holds counts and commits, never the key.
#
# Install:   powershell -File scripts\install-daily-task.ps1
# Run now:   Start-ScheduledTask -TaskName "ASV Qdrant daily ingest"

param(
    [string]$Mirror = "C:\Dev\Data\asv-mirror",
    [string]$Repo = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$logDir = Join-Path $env:LOCALAPPDATA "asv-qdrant"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "ingest.log"

function Write-Log([string]$msg) {
    "$(Get-Date -Format s) $msg" | Out-File -FilePath $log -Append -Encoding utf8
}

try {
    if (-not $env:QDRANT_URL -or -not $env:QDRANT_API_KEY) { throw "QDRANT_URL or QDRANT_API_KEY is not set" }
    if (-not (Test-Path (Join-Path $Mirror ".git"))) {
        git clone -q --depth 1 --single-branch --branch main https://github.com/SarutobiSasuke8/agentic-satellite-vault.git $Mirror
    }
    git -C $Mirror fetch -q --depth 1 origin main
    git -C $Mirror reset -q --hard origin/main
    $head = git -C $Mirror rev-parse --short HEAD
    $python = Join-Path $Repo ".venv\Scripts\python.exe"
    $out = & $python -W ignore -m asv_qdrant.cli ingest $Mirror 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "ingest exited $LASTEXITCODE`n$out" }
    $stats = ($out | ConvertFrom-Json)
    Write-Log "ok asv=$head files=$($stats.files) changed=$($stats.changed) removed=$($stats.removed) chunks=$($stats.chunks)"
} catch {
    Write-Log "FAIL $($_.Exception.Message -replace "`r?`n", ' | ')"
    exit 1
}
