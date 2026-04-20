# API Service Bootstrap

`services/api` is the primary API service boundary in the EternalAI bootstrap scaffold.

Current scope:
- load configuration
- expose a minimal `GET /health` endpoint
- provide a stable location for later business API work

Non-goals for this bootstrap:
- authentication flows
- assistant workflows
- retrieval or knowledge-base integration
- production business endpoints
