# EternalAI Bootstrap Plan

## Repository State Before Bootstrap

The repository started as a planning-pack carrier with these preserved assets:
- `checklists/`
- `prompts/`
- `templates/`
- root `README.md` focused on planning-pack usage

No business-facing source tree, environment template, runtime scripts, Docker files, CI workflow, or service boundaries existed before this bootstrap update.

## Cleanup

- Observed a local root `nul` artifact during workspace preparation and cleaned it locally; it was not part of the tracked repository history
- Kept the planning assets as method assets for later spec-first business planning

## Scaffold Goals

This bootstrap changes the repository into a Python-first monorepo-style scaffold for later business development. The goal is to provide real engineering boundaries without pretending product features are already implemented.

The main service boundaries are:
- `services/api`
- `services/worker`
- `services/asr`

`pyproject.toml` is included as a minimal engineering entrypoint only. It does not represent a mature multi-package publish workflow.

## Scope of This Bootstrap

Included:
- minimal service layout
- minimal health endpoints for API and ASR
- worker config bootstrap
- environment template
- PowerShell run and test scripts
- Dockerfiles and local compose placeholders
- smoke tests and GitHub Actions smoke workflow
- contracts and docs placeholders

Excluded:
- real assistant orchestration
- authentication flows
- knowledge base or retrieval logic
- ASR inference implementation
- Langfuse runtime wiring
- production deployment guarantees

## Langfuse Reservation Strategy

`docker-compose.yml` intentionally does not define Langfuse services yet.

When self-hosted observability is added later, EternalAI should follow the official Langfuse-style shape:
- `langfuse-web`
- `langfuse-worker`
- `clickhouse`
- `postgres`
- `redis`

ClickHouse is a critical dependency for that future Langfuse setup and must not be omitted.

The current bootstrap avoids faking a single-container Langfuse service or an unverified multi-container integration.

## Re-Run Business Planning When

Run spec-first business repo planning again after:
- the scaffold has been reviewed
- a real Python toolchain is installed and verified
- smoke tests pass in CI or on a prepared development machine
- the team is ready to turn placeholders into real business modules
