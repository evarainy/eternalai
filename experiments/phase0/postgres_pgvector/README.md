# P0-SPIKE-003 — PostgreSQL 18 + pgvector

**spike-only**: This directory contains experiment code for evaluating PostgreSQL 18 with pgvector >= 0.8.2. It is not part of the production codebase and must not be imported by `app/` modules.

## Purpose

Validate the minimum Phase 0 acceptance criteria:

- Docker Compose can start PostgreSQL 18 with pgvector available.
- Alembic creates the `vector` extension.
- Alembic creates a test table with a `vector(3)` column.
- Alembic creates a vector index.
- A deterministic nearest-neighbor query returns the expected row.

## Files

| File | Description |
|------|-------------|
| `requirements.txt` | Spike-only Python dependencies; not a production dependency decision |
| `alembic.ini` | Spike-only Alembic configuration |
| `alembic/env.py` | Alembic environment that reads `DATABASE_URL` |
| `alembic/versions/20260511_01_pgvector_spike.py` | Migration that creates extension, table, and vector index |
| `assert_pgvector_version.py` | Verifies installed pgvector extension version is >= 0.8.2 |
| `verify_schema_and_index.py` | Verifies table, vector column, and vector index |
| `run_similarity_query.py` | Runs deterministic insert and similarity query assertion |

## Usage

```powershell
docker compose -f infra/docker/docker-compose.postgres-pgvector-spike.yml up -d

docker compose -f infra/docker/docker-compose.postgres-pgvector-spike.yml exec -T postgres psql -U phase0_spike -d phase0_spike -c "SELECT name, default_version FROM pg_available_extensions WHERE name = 'vector';"

python -m venv $env:TEMP\eternalai-p0-spike-003-venv
$env:PYTHONPYCACHEPREFIX = "$env:TEMP\eternalai-p0-spike-003-pycache"
& "$env:TEMP\eternalai-p0-spike-003-venv\Scripts\python.exe" -m pip install -r experiments/phase0/postgres_pgvector/requirements.txt
$env:DATABASE_URL = "postgresql+psycopg://phase0_spike@127.0.0.1:55432/phase0_spike"
& "$env:TEMP\eternalai-p0-spike-003-venv\Scripts\python.exe" -m alembic -c experiments/phase0/postgres_pgvector/alembic.ini upgrade head
& "$env:TEMP\eternalai-p0-spike-003-venv\Scripts\python.exe" experiments/phase0/postgres_pgvector/assert_pgvector_version.py
& "$env:TEMP\eternalai-p0-spike-003-venv\Scripts\python.exe" experiments/phase0/postgres_pgvector/verify_schema_and_index.py
& "$env:TEMP\eternalai-p0-spike-003-venv\Scripts\python.exe" experiments/phase0/postgres_pgvector/run_similarity_query.py
```

## Cleanup

```powershell
docker compose -f infra/docker/docker-compose.postgres-pgvector-spike.yml down -v
Remove-Item -Recurse -Force "$env:TEMP\eternalai-p0-spike-003-venv" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$env:TEMP\eternalai-p0-spike-003-pycache" -ErrorAction SilentlyContinue
```

## Not Production Code

- This code must not be imported by production modules.
- The Compose file is not a production database deployment.
- The requirements file is spike-only and does not approve production dependencies.
- Phase 1 must design real credentials, migrations, backup, monitoring, retention, and operational controls separately.
