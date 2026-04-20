$ErrorActionPreference = "Stop"
$python = Get-Command python -ErrorAction SilentlyContinue

if (-not $python -or $python.Source -like "*WindowsApps*") {
  Write-Error "Python 3.11+ is required to run pytest for the bootstrap scaffold."
  exit 1
}

python -m pytest
