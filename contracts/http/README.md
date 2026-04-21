# HTTP Contracts

Phase-0 HTTP contract files:
- `api.yaml` for the public API surface owned by `services/api`
- `asr.yaml` for the internal ASR surface owned by `services/asr`

Currently implemented public HTTP operations are:
- `GET /health`
- `POST /v1/sessions`
- `GET /v1/sessions/{session_id}`
- `POST /v1/sessions/{session_id}/messages`
- `GET /v1/tasks/{task_id}`

These Phase 1B session and task endpoints use process-local in-memory state only. Knowledge-source and transcription operations remain planned.

The public API contract may include `schema_ref` pointers into `services/api/app/schemas/` so the reviewed YAML stays aligned with the typed Python boundary models.
