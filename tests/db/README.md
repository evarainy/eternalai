# Database Test Scope

These tests validate only the Phase 0 PostgreSQL/Alembic baseline for `P0-INFRA-004`.

- Test isolation uses the fixed local Docker Compose project `eternalai_test_db` and its
  named volume `eternalai_test_db_data`; it is separate from the root deployment Compose
  database.
- Start it idempotently with `docker compose -f infra/docker/docker-compose.test-db.yml up -d`.
- Set `DATABASE_URL` in your local environment to the fixed test database before validation.
- Reset test data and restore the latest schema with `uv run python scripts/reset_test_db.py`.
  The script refuses every database name other than `eternalai_test` and every host other
  than `127.0.0.1` or `localhost` before it can execute DDL.
- Stop it without deleting its data with
  `docker compose -f infra/docker/docker-compose.test-db.yml stop`.
- No committed file stores a real database URL or production credential.
- The target database is fixed local test infrastructure, never a production database.
- Tests must not create OA, U8, Hikvision, SDUI, Gateway, Runtime, or domain schema data.
- The Alembic migration guard rejects generated migration comments.
- Temporary negative guard migration files are unstaged validation artifacts and must be deleted before staging.
