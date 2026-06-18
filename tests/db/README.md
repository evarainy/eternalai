# Database Test Scope

These tests validate only the Phase 0 PostgreSQL/Alembic baseline for `P0-INFRA-004`.

- Test isolation uses the task-scoped Docker Compose project `eternalai_p0_infra_004`.
- `DATABASE_URL` is supplied by the caller's environment for validation runs.
- No committed file stores a real database URL or production credential.
- The target database is disposable task infrastructure, not a production database.
- Tests must not create OA, U8, Hikvision, SDUI, Gateway, Runtime, or domain schema data.
- The Alembic migration guard rejects generated migration comments.
- Temporary negative guard migration files are unstaged validation artifacts and must be deleted before staging.
