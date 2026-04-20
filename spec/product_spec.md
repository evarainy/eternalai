# EternalAI Product Spec

## Intent

EternalAI is planned as an enterprise intranet assistant that lets internal users ask questions in text or audio, retrieve grounded answers from curated enterprise knowledge, and track long-running work through explicit task states.

This document describes the first business implementation target. It is not a claim that the repository already implements these features.

## Current Repository Baseline

The repository was inspected on 2026-04-20 and currently provides only a verified bootstrap baseline:
- `services/api` exposes `GET /health`
- `services/asr` exposes `GET /health`
- `services/worker` loads configuration and emits a bootstrap state
- `apps/web` and `apps/miniapp` are placeholders
- `.venv` is usable, `pytest` passes, and the API `/health` endpoint was verified over HTTP
- Docker and compose flows remain unverified

## Product Outcome

The first business slice should deliver a thin but real assistant workflow:
1. A client creates or resumes a conversation session.
2. A user submits a text question or an audio question.
3. Audio is transcribed through the ASR boundary before entering the answer flow.
4. The API records the request and returns a task identifier.
5. The worker executes ingestion or answer-generation work asynchronously.
6. The client polls task state and retrieves the final assistant answer with provenance metadata.

## Primary Users

- Employee user: asks questions and reviews answers in web or miniapp clients
- Knowledge operator: registers and refreshes approved knowledge sources
- Platform maintainer: configures runtime settings, verifies health, and monitors async processing

## Phase 1 Scope

Phase 1 should stay intentionally small and aligned to the existing service layout.

Included:
- session-based text Q&A
- optional audio-question transcription through the ASR service
- knowledge-source registration plus ingestion jobs
- asynchronous task lifecycle with explicit statuses
- shared HTTP and event contracts for backend and clients
- host-first verification flow that keeps current bootstrap checks green

Excluded:
- enterprise SSO or full IAM integration
- streaming audio or realtime chat transport
- multi-tenant isolation beyond a single internal workspace assumption
- full Langfuse rollout or mandatory observability stack
- Docker-first development as a required path

## Service Responsibilities

### API (`services/api`)

- own public HTTP ingress
- manage session, message, task, and knowledge-source resources
- publish async work requests to the worker boundary
- call the ASR boundary only through explicit internal contracts

### Worker (`services/worker`)

- consume answer, ingestion, and transcription-related work items
- update task state transitions
- isolate long-running or retryable processing from request/response code

### ASR (`services/asr`)

- expose internal transcription capability
- keep provider selection behind a stable adapter interface
- prefer SenseVoice as the primary backend and reserve `faster-whisper` as fallback

### Clients (`apps/web`, `apps/miniapp`)

- consume the same public API contract
- avoid inventing private backend fields
- focus on session creation, question submission, task polling, and answer rendering

## Key Resources

- Session: the container for a user conversation
- Conversation turn: one user request and one assistant response within a session
- Knowledge source: a registered source that can be queued for ingestion
- Assistant task: a tracked asynchronous unit of work
- Transcription request: an audio-to-text task handled through ASR

Detailed lifecycle and ownership rules are defined in `spec/resource_spec.yaml`.

## Delivery Principles

- Keep the bootstrap health contracts stable while business work is added.
- Add contracts before or alongside implementation, never after.
- Treat Postgres as the source of truth for durable assistant state.
- Treat Redis as coordination or queue infrastructure, not the canonical record.
- Keep host-first scripts and local `.venv` workflows working throughout development.

## Open Review Gates

The following decisions should be reviewed before implementation starts:
- whether web or miniapp is the primary first client
- which authentication mode replaces the current bootstrap `AUTH_MODE=disabled`
- which knowledge-source formats are in-scope for the first ingestion slice
- whether phase 1 persists uploaded audio bytes or only derived transcript text

## Done Definition For Phase 1

Phase 1 is complete when:
- contract-defined session, message, task, and transcription endpoints exist
- worker-driven asynchronous processing updates durable task states
- knowledge-source ingestion is runnable and testable
- at least one client path can complete a question flow against the public API
- acceptance checks in `planning/acceptance_spec.yaml` pass on the host-first path
