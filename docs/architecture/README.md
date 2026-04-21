# Architecture Notes

The repository currently uses a Python-first monorepo-style bootstrap layout:
- `services/api` for API-facing service entrypoints
- `services/worker` for background execution
- `services/asr` for future speech-related integration
- `apps/web` and `apps/miniapp` as client boundaries
- `services/runtime_config.py` as the shared runtime configuration baseline for backend services
- `services/api/app/routes/` as the API route assembly boundary, with system routes and a reserved `/v1` public group
- `services/api/app/schemas/` as the typed boundary for planned public resources before persistence lands
- `services/api/app/routes/sessions.py`, `messages.py`, and `tasks.py` as the Phase 1B public HTTP handlers
- `services/api/app/services/session_service.py` as the in-memory lifecycle boundary for sessions, turns, and tasks
- `services/api/app/services/in_memory_state.py` as the process-local placeholder until the persistence phase lands

This is a starting point for later business repo planning, not the final production architecture.
