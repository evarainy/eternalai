$ErrorActionPreference = "Stop"

function Test-RealPython {
  $python = Get-Command python -ErrorAction SilentlyContinue
  if (-not $python) {
    return $false
  }

  return $python.Source -notlike "*WindowsApps*"
}

if (-not (Test-RealPython)) {
  Write-Error "Python 3.11+ is required. Install a real interpreter and ensure it is on PATH."
  exit 1
}

python --version
python -m pip --version

$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($docker) {
  docker --version
} else {
  Write-Host "Docker not found on PATH. Container bootstrap is optional."
}
