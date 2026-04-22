# Architecture Notes

The repository currently uses a Python-first monorepo-style bootstrap layout:
- `services/api` for API-facing service entrypoints
- `services/worker` for background execution
- `services/asr` for future speech-related integration
- `apps/web` and `apps/miniapp` as client boundaries
- `services/runtime_config.py` as the shared runtime configuration baseline for backend services
- `services/api/app/routes/` as the API route assembly boundary, with system routes and a reserved `/v1` public group
- `services/api/app/schemas/` as the typed boundary for the current public resources
- `services/api/app/routes/sessions.py`, `messages.py`, and `tasks.py` as the Phase 1B/1C public HTTP handlers
- `services/api/app/services/session_service.py` as the lifecycle service over repository-backed session, turn, and task flows
- `services/api/app/repositories/` as the Phase 1C persistence seam with in-memory and Postgres implementations
- `services/api/app/persistence/` and `migrations/` as the minimal Postgres connectivity and schema-evolution foundation

This is a starting point for later business repo planning, not the final production architecture.
