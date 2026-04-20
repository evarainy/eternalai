# Architecture Notes

The repository currently uses a Python-first monorepo-style bootstrap layout:
- `services/api` for API-facing service entrypoints
- `services/worker` for background execution
- `services/asr` for future speech-related integration
- `apps/web` and `apps/miniapp` as client boundaries
- `services/runtime_config.py` as the shared runtime configuration baseline for backend services
- `services/api/app/routes/` as the API route assembly boundary, with system routes and a reserved `/v1` public group
- `services/api/app/schemas/` as the typed boundary for planned public resources before persistence lands

This is a starting point for later business repo planning, not the final production architecture.
