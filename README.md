# EternalAI

EternalAI is a Python-first monorepo-style bootstrap scaffold for an enterprise intranet assistant. This repository is now a development repo scaffold, not a planning-pack-only carrier.

The current scaffold is intentionally small:
- it creates real service boundaries for `services/api`, `services/worker`, and `services/asr`
- it keeps a shared runtime configuration profile across those services
- it adds base project files, smoke tests, Docker placeholders, and CI
- it does not claim that product features, Langfuse integration, or end-to-end business workflows are complete

## Current Status

This repo now represents a bootstrap engineering baseline:
- `services/api` exposes `GET /health` plus a minimal session/message/task HTTP loop
- `services/api` can run the current loop on either in-memory state or Postgres-backed repositories
- `services/worker` is a minimal worker bootstrap with config loading
- `services/asr` is an ASR service placeholder with config loading and `GET /health`
- `apps/web` and `apps/miniapp` are placeholders for future client work
- `pyproject.toml` is only a minimal engineering entrypoint and does not represent a mature multi-package publishing structure

## Repository Layout

```text
apps/
  web/                  Placeholder web client boundary
  miniapp/              Placeholder miniapp boundary
services/
  api/                  Minimal API service plus Phase 1C persistence foundation
  worker/               Minimal worker bootstrap service
  asr/                  Minimal ASR bootstrap service
contracts/              Interface placeholders for HTTP and events
docs/                   Architecture, runbook, and observability notes
deploy/docker/          Base Dockerfiles for local bootstrap services
scripts/                PowerShell scripts for doctor, run, and test flows
tests/smoke/            Repository structure smoke tests
planning/               Bootstrap planning and result artifacts
checklists/             Preserved planning method asset
prompts/                Preserved planning method asset
templates/              Preserved planning method asset
```

## Prerequisites

Local development is host-first. Install these tools before trying to run the scaffold:
- Python 3.11 or newer with a real interpreter on `PATH`
- `pip`
- Docker Desktop and Docker Compose if you want containerized bootstrap services

The current workstation used for bootstrap planning did not have a verified Python, Git, Node.js, or Docker toolchain on `PATH`, so local runtime validation must still be completed on a prepared machine or in CI.

## Local Bootstrap

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
python -m alembic upgrade head
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\doctor.ps1
.\scripts\dev-api.ps1
.\scripts\dev-worker.ps1
.\scripts\dev-asr.ps1
.\scripts\test.ps1
```

If PowerShell execution policy blocks direct script execution on Windows, apply a process-scoped bypass before running any of the bootstrap scripts:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## Docker Bootstrap

`docker-compose.yml` only reserves base infra and bootstrap service slots:
- `postgres`
- `redis`
- `api`
- `worker`
- `asr`

This file does not claim that business flows are wired together. It only provides a starting point for local infrastructure and service containers.

Compose reads service variables from `.env`, so copy `.env.example` before using `docker compose up`.

## Phase 1C Persistence Foundation

The current API persistence scope is intentionally narrow:
- `API_PERSISTENCE_BACKEND=memory` keeps the fast Phase 1B-style local test path
- `API_PERSISTENCE_BACKEND=postgres` routes sessions, turns, and tasks through Postgres
- `python -m alembic upgrade head` applies the minimal Phase 1C schema for `sessions`, `turns`, and `tasks`
- knowledge sources, Redis coordination, Langfuse, ASR execution, and worker-driven task transitions remain out of scope for this phase

## Observability Note

Langfuse is intentionally not wired into `docker-compose.yml` yet. When EternalAI is ready for self-hosted observability, it should follow the official Langfuse shape:
- `langfuse-web`
- `langfuse-worker`
- `clickhouse`
- `postgres`
- `redis`

ClickHouse is a critical dependency for a future self-hosted Langfuse deployment and must not be omitted from the eventual design.

## Preserved Planning Assets

The repository still keeps the original planning assets:
- `checklists/`
- `prompts/`
- `templates/`

They remain available as method assets for later spec-first business planning after the bootstrap scaffold is reviewed and validated against a real toolchain.

## When To Re-Run Business Planning

Re-run spec-first business repo planning after:
- this scaffold has been reviewed and accepted
- a real development environment or CI has validated dependency installation
- smoke tests and health checks pass from actual code, not just from the planned structure
