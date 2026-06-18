# P0-SPIKE-004 — Redis + ARQ Baseline Spike

**spike-only**: This directory contains experiment code for evaluating Redis + ARQ task queue capabilities. It is not part of the production codebase.

## Purpose

Evaluate whether ARQ (backed by Redis) can serve as an async task queue for Phase 1, covering:
- Task enqueue and execution
- Failure recording
- Timeout handling

## Files

| File | Description |
|------|-------------|
| `worker.py` | ARQ worker function definitions (success, failure, timeout) |
| `test_spike.py` | Automated test script covering AC-1, AC-2, AC-3 |
| `requirements.txt` | Spike-only Python dependencies (arq, redis) |

## Usage

```bash
# Start Redis (spike-only compose)
docker compose -f infra/docker/docker-compose.redis-spike.yml up -d

# Create temp venv outside repo
python -m venv $env:TEMP\eternalai-p0-spike-004-venv
$env:TEMP\eternalai-p0-spike-004-venv\Scripts\pip install -r experiments/phase0/redis_arq/requirements.txt

# Run tests
$env:TEMP\eternalai-p0-spike-004-venv\Scripts\python experiments/phase0/redis_arq/test_spike.py

# Cleanup
docker compose -f infra/docker/docker-compose.redis-spike.yml down -v
Remove-Item -Recurse -Force $env:TEMP\eternalai-p0-spike-004-venv -ErrorAction SilentlyContinue
```

## Not Production Code

- This code must not be imported by `app/` modules.
- The Docker Compose file is not a production Redis deployment.
- Dependencies listed in `requirements.txt` are spike-only, not production decisions.
