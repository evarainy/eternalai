# Contracts

This directory now serves as the phase-0 contract registry for EternalAI.

Current contract sets:
- `contracts/http/api.yaml`: public API contract for system, session, message, task, and knowledge-source endpoints
- `contracts/http/asr.yaml`: internal ASR HTTP contract for health and transcription operations
- `contracts/events/worker.yaml`: worker job contract for answer, ingestion, and transcription workloads

Public API versioning:
- `contracts/http/api.yaml` tracks the current public contract as `http.api.public.v1`
- unversioned system health remains in the same contract, while business resources reserve the `/v1` route group
- planned business operations can point at typed Python models through `schema_ref` entries for reviewability

Contract status conventions:
- `implemented`: the route or payload already exists in the repository today
- `planned`: the route or payload is part of the approved phase-1 target, but is not implemented yet
- `draft`: the schema shape is ready for review and can drive implementation sequencing

Rules:
- add or update the contract before adding business endpoints or worker payloads
- keep public API and internal contracts versioned separately
- treat contract YAML as a review artifact, not generated output
