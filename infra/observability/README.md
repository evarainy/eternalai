# Observability Deployment Baseline (P0-INFRA-006)

P0-INFRA-006 is a **deployment/configuration baseline only**. Validation is parse/config-only; no containers are started.

## Scope

The OTel Collector config remains at `infra/docker/otel-collector-config.yaml` (created by P0-INFRA-001). `docker-compose.yml` wires `langfuse` and `otel-collector` under the `observability` profile.

Langfuse is a **placeholder smoke-ready baseline**, not a full production Langfuse v3 stack. `.env.example` values are placeholders only.

## Deferred (not implemented)

The following are **deferred** to later dedicated TDD tasks and are **not implemented** in this task:

- App instrumentation (OpenTelemetry SDK wiring, span creation)
- API `trace_id` behavior in health or business endpoints
- Gateway spans
- Runtime spans
- Tool spans
- End-to-end trace validation

## Validation

```powershell
# Parse-only compose validation (no containers started)
docker compose --env-file .env.example --profile observability config
```

## References

- Collector config: `infra/docker/otel-collector-config.yaml`
- Compose file: `docker-compose.yml` (observability profile)
- Environment placeholders: `.env.example`
