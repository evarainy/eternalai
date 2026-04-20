# Observability Placeholder

EternalAI does not include Langfuse services in `docker-compose.yml` yet.

When self-hosted observability is added later, the expected Langfuse shape should follow the official multi-service architecture:
- `langfuse-web`
- `langfuse-worker`
- `clickhouse`
- `postgres`
- `redis`

ClickHouse is a critical dependency in that future design and must not be skipped.

The current bootstrap scaffold intentionally avoids pretending that Langfuse is already wired, verified, or production-ready.
