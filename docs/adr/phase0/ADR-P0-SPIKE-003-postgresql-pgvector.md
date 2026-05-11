# ADR-P0-SPIKE-003 — PostgreSQL 18 + pgvector Deployment Spike

- status: accepted
- date: 2026-05-11
- task_id: P0-SPIKE-003
- owner: Codex
- related_spec: docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md

## 1. Context

P0-SPIKE-003 validates whether PostgreSQL 18 with pgvector >= 0.8.2 can be used in a Docker Compose single-node development setup for Phase 1 memory/vector-storage planning. This is a deployment and minimum query spike only. It does not implement formal Memory, RAG, Runtime, Gateway, Adapter, or production persistence behavior.

## 2. Question to Answer

- Can Docker Compose start PostgreSQL 18 with pgvector available?
- Can Alembic create the vector extension, a test table, a `vector(3)` column, and a vector index?
- Can a deterministic similarity query pass against the migrated schema?
- Should PostgreSQL 18 + pgvector >= 0.8.2 be adopted for Phase 1 planning?

## 3. Validation Setup

- environment: local Docker Compose on Windows host
- hardware: local development machine
- software_versions:
  - Docker: `Docker version 29.4.0, build 9d7ad9f`
  - Docker Compose: `Docker Compose version v5.1.2`
  - PostgreSQL image: `pgvector/pgvector:0.8.2-pg18`
  - PostgreSQL server version: `PostgreSQL 18.3 (Debian 18.3-1.pgdg12+1)`
  - pgvector extension version: `0.8.2`
- dependency_source: package download/cache available in local execution; internal mirror confirmation still required for repeatable offline use
- test_dataset: deterministic three-row vector dataset: `alpha=[1,0,0]`, `beta=[0,1,0]`, `gamma=[0,0,1]`
- commands_run:
  - `docker manifest inspect pgvector/pgvector:0.8.2-pg18`
  - `docker compose -f infra/docker/docker-compose.postgres-pgvector-spike.yml config`
  - `docker compose -f infra/docker/docker-compose.postgres-pgvector-spike.yml up -d`
  - `SELECT name, default_version FROM pg_available_extensions WHERE name = 'vector';`
  - `python -m alembic -c experiments/phase0/postgres_pgvector/alembic.ini upgrade head`
  - `SELECT extversion FROM pg_extension WHERE extname = 'vector';`
  - `python experiments/phase0/postgres_pgvector/assert_pgvector_version.py`
  - `python experiments/phase0/postgres_pgvector/verify_schema_and_index.py`
  - `python experiments/phase0/postgres_pgvector/run_similarity_query.py`
  - `docker compose -f infra/docker/docker-compose.postgres-pgvector-spike.yml down -v`

## 4. Acceptance Criteria Result

| criterion | result | evidence |
|---|---|---|
| docker compose can start PostgreSQL 18 | passed | Compose service reached `Up ... (healthy)` with `PostgreSQL 18.3` |
| pgvector version >= 0.8.2 | passed | `pg_available_extensions` default version `0.8.2`; post-Alembic assertion `pgvector_extversion_ok: 0.8.2 >= 0.8.2` |
| Alembic can create a test table with a vector field | passed | Alembic migration created `pgvector_spike_documents` and `embedding vector(3)` |
| minimal similarity query passes | passed | `similarity_query_ok: nearest=alpha distance=0.0` |
| ADR states whether this combination should enter Phase 1 | passed | Decision below is `passed` with Phase 1 conditions |

## 5. Decision

decision: passed

PostgreSQL 18 with pgvector 0.8.2 is suitable to carry forward into Phase 1 as the baseline candidate for local development and future persistence/vector-storage design, subject to separate Phase 1 work on production credentials, migration discipline, backup/restore, observability, index tuning, rollback, upgrade, and internal mirror confirmation.

## 6. Impact on Later Design

- impact_on_phase1: Phase 1 can use PostgreSQL + pgvector as the baseline candidate for vector storage planning.
- impact_on_runtime: No Runtime code is changed by this spike.
- impact_on_gateway: No Gateway code is changed by this spike.
- impact_on_identity: No identity model is changed by this spike.
- impact_on_trace: No Trace persistence is changed by this spike.
- impact_on_sdui: No SDUI contract is changed by this spike.

## 7. Risks and Open Questions

- PostgreSQL 18 operational maturity, backup, extension upgrade handling, and rollback strategy need a separate Phase 1 database design task.
- The spike uses local trust authentication for disposable local validation only; production credentials and network boundaries are out of scope.
- The vector index method and operator class may need workload-specific tuning after realistic data size and query patterns are known.
- Internal mirror availability for spike-only Python dependencies must be confirmed before repeatable offline execution.
- PostgreSQL 18 Docker images require the named volume to be mounted at `/var/lib/postgresql`, not directly at `/var/lib/postgresql/data`.

## 8. spike_code_disposition

```yaml
spike_code_disposition:
  disposable:
    - experiments/phase0/postgres_pgvector/
    - infra/docker/docker-compose.postgres-pgvector-spike.yml
  promoted_to_test_utils: []
  prohibited:
    - app/
```

## 9. Evidence Links

- logs:
  - PostgreSQL first start failed until the volume mount was corrected from `/var/lib/postgresql/data` to `/var/lib/postgresql`; this is recorded as a deployment finding.
  - Cleanup removed the Compose container, network, and named volume.
- test output:
  - `pgvector_extversion_ok: 0.8.2 >= 0.8.2`
  - `table_exists_ok: public.pgvector_spike_documents`
  - `vector_column_ok: embedding vector(3)`
  - `vector_index_ok: ix_pgvector_spike_documents_embedding_hnsw`
  - `vector_index_definition: CREATE INDEX ix_pgvector_spike_documents_embedding_hnsw ON public.pgvector_spike_documents USING hnsw (embedding vector_l2_ops)`
  - `similarity_query_ok: nearest=alpha distance=0.0`
- commit / package: no commit or package in this execution turn

## 10. Validation Evidence

### Docker / PostgreSQL

```text
Docker version 29.4.0, build 9d7ad9f
Docker Compose version v5.1.2
PostgreSQL 18.3 (Debian 18.3-1.pgdg12+1)
```

### Pre-Alembic pgvector Availability

Alembic was not preceded by `CREATE EXTENSION`. The pre-Alembic check only verified availability:

```text
name   | default_version
-------+----------------
vector | 0.8.2
```

### Alembic Migration Responsibility

The migration `experiments/phase0/postgres_pgvector/alembic/versions/20260511_01_pgvector_spike.py` executes:

- `CREATE EXTENSION IF NOT EXISTS vector`
- create table `pgvector_spike_documents`
- create column `embedding vector(3)`
- create index `ix_pgvector_spike_documents_embedding_hnsw`

### Post-Alembic pgvector Version

```text
extversion
----------
0.8.2

pgvector_extversion_ok: 0.8.2 >= 0.8.2
```

### Schema and Index

```text
to_regclass
-----------
pgvector_spike_documents

column_name | udt_name
------------+---------
embedding   | vector

indexname                                   | indexdef
--------------------------------------------+---------------------------------------------------------------------------------------------------------------------------------
ix_pgvector_spike_documents_embedding_hnsw | CREATE INDEX ix_pgvector_spike_documents_embedding_hnsw ON public.pgvector_spike_documents USING hnsw (embedding vector_l2_ops)

table_exists_ok: public.pgvector_spike_documents
vector_column_ok: embedding vector(3)
vector_index_ok: ix_pgvector_spike_documents_embedding_hnsw
vector_index_definition: CREATE INDEX ix_pgvector_spike_documents_embedding_hnsw ON public.pgvector_spike_documents USING hnsw (embedding vector_l2_ops)
```

### Deterministic Similarity Query

```text
similarity_query_ok: nearest=alpha distance=0.0
```

### Cleanup

```text
docker compose -f infra/docker/docker-compose.postgres-pgvector-spike.yml down -v
Container eternalai-p0-spike-003-postgres-1 Removed
Volume eternalai-p0-spike-003_postgres-pgvector-spike-data Removed
Network eternalai-p0-spike-003_default Removed

TEMP_VENV_REMOVED
TEMP_PYCACHE_REMOVED
NO_REPO_DOT_VENV
NO_REPO_PYCACHE
NO_REPO_PYC
```

## 11. Phase 1 Recommendation

Adopt PostgreSQL 18 + pgvector 0.8.2 as the Phase 1 baseline candidate for vector-storage planning, but only as an implementation candidate. Phase 1 must still define production-safe database credentials, extension lifecycle, migration review rules, backup/restore, index tuning, observability, data retention, and internal dependency mirror availability before production use.
