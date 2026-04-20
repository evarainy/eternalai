$ErrorActionPreference = "Stop"
$python = Get-Command python -ErrorAction SilentlyContinue

if (-not $python -or $python.Source -like "*WindowsApps*") {
  Write-Error "Python 3.11+ is required to run the ASR bootstrap service."
  exit 1
}

$hostName = if ($env:ASR_HOST) { $env:ASR_HOST } else { "0.0.0.0" }
$port = if ($env:ASR_PORT) { $env:ASR_PORT } else { "8010" }

python -m uvicorn services.asr.app.main:app --host $hostName --port $port
