$ErrorActionPreference = "Stop"
$python = Get-Command python -ErrorAction SilentlyContinue

if (-not $python -or $python.Source -like "*WindowsApps*") {
  Write-Error "Python 3.11+ is required to run the API bootstrap service."
  exit 1
}

$hostName = if ($env:API_HOST) { $env:API_HOST } else { "0.0.0.0" }
$port = if ($env:API_PORT) { $env:API_PORT } else { "8000" }

python -m uvicorn services.api.app.main:app --host $hostName --port $port
