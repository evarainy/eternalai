# API Service Bootstrap

`services/api` is the primary API service boundary in the EternalAI bootstrap scaffold.

Current scope:
- load the shared runtime configuration profile
- expose a minimal `GET /health` endpoint
- register a versioned public API router rooted at `/v1`
- define typed request and response schemas in `services/api/app/schemas/`
- provide a stable location for later business API work

Non-goals for this bootstrap:
- authentication flows
- assistant workflows
- retrieval or knowledge-base integration
- production business endpoints
