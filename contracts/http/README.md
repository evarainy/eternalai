# HTTP Contracts

Phase-0 HTTP contract files:
- `api.yaml` for the public API surface owned by `services/api`
- `asr.yaml` for the internal ASR surface owned by `services/asr`

The only currently implemented HTTP operations are the bootstrap health endpoints. Session, task, knowledge-source, and transcription operations remain planned until implemented.

The public API contract may include `schema_ref` pointers into `services/api/app/schemas/` so the reviewed YAML stays aligned with the typed Python boundary models.
