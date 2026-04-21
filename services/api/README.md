# API Service Bootstrap

`services/api` is the primary API service boundary in the EternalAI bootstrap scaffold.

Current scope:
- load the shared runtime configuration profile
- expose a minimal `GET /health` endpoint
- expose a minimal in-memory HTTP loop for sessions, message submission, and task polling under `/v1`
- define typed request and response schemas in `services/api/app/schemas/`
- keep public route definitions aligned with `contracts/http/api.yaml`

Non-goals for this Phase 1B slice:
- database persistence and migrations
- Redis-backed coordination
- worker-driven task execution
- Langfuse integration
- real ASR execution
- frontend client implementation
