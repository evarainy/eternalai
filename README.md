# EternalAI

Phase 0 placeholder repository. See `docs/blueprint/` for the architecture specification.

## Docker Compose profiles

Phase 0 defines a single-node `docker-compose.yml` with three profiles. No containers are started or runtime-validated in Phase 0; all validation is parse-only (`docker compose config`).

| Profile | Services | Purpose |
|---|---|---|
| `core-infra` | `postgres`, `redis` | Database and cache placeholders |
| `app` | `api`, `frontend` | Application service placeholders |
| `observability` | `langfuse`, `otel-collector` | Tracing and observability placeholders |

### Quick start

```bash
# Validate all profiles (parse-only, no containers started)
docker compose --env-file .env.example config
docker compose --env-file .env.example --profile core-infra config
docker compose --env-file .env.example --profile app config
docker compose --env-file .env.example --profile observability config

# List all services across all profiles
docker compose --env-file .env.example \
  --profile core-infra --profile app --profile observability \
  config --services
```

### Environment variables

Copy `.env.example` to `.env` and adjust values. All secrets are placeholders — never commit real credentials.
