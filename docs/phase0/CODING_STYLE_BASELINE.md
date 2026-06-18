# Phase 0 Coding Style and Quality Baseline v1.0.0

Shared coding style and quality baseline for Claude Code / MiMo / Codex multi-tool collaboration. Reduces style drift, unrelated formatting, context pollution, and review inconsistency across Phase 0 and Phase 1 development.

This document is compact. Each chapter is a scannable list of principles, not a long manual.

## Purpose and scope

- This is the coding style and quality baseline for Phase 0 / early Phase 1, before production formatter/linter tooling is adopted.
- The goal is to reduce style drift, unrelated changes, and inconsistent review across multiple AI coding tools.
- This document is a canonical style baseline and section reference.
- It should be applied through progressive disclosure.
- Agents should load only the sections relevant to the current task unless the task prompt explicitly requires full review.
- Do not paste or load the full document into every task by default.
- This baseline does not immediately introduce formatter, linter, or type-checker tooling; those are evaluated in future tasks.
- This baseline defines style-section applicability only. Repository-wide navigation and progressive context-loading maps belong to a future P0-NAV-001-style task.

## How to use this baseline / Section applicability guide

Apply only the sections relevant to the current task. Do not load the full baseline by default.

- **Always consider:**
  - Purpose and scope
  - General coding principles
  - AI tool collaboration rules

- **Python style baseline:** Use when editing Python files, Python tests, adapters, runners, scripts, or Python-based tooling.

- **Backend / service structure baseline:** Use when editing production backend services, runtime boundaries, service layers, ports, adapters, background jobs, or async execution code.

- **Database / schema style baseline:** Use when editing persistence, schema, migration, ORM, SQL, audit fields, JSON/JSONB, or database-related docs. Do not load for unrelated API-only or documentation-only tasks.

- **API / interface style baseline:** Use when editing gateway routes, API contracts, request/response schemas, external interface docs, or handler boundaries. Do not load for unrelated database-only tasks.

- **Extension point / capability plug-in style baseline:** Use when editing capabilities, tools, skills, registries, adapters, workflow hooks, or execution-fabric extension points. Do not load for unrelated database-only or API-only tasks.

- **Security / privacy baseline:** Use when the task touches auth, authorization, credentials, secrets, tokens, user/business sensitive data, external input, policy, approval, audit, or public-facing behavior.

- **Dependency / supply-chain / license baseline:** Use when the task touches dependencies, package manifests, lock files, external libraries, copied snippets, SDKs, or vendor packages.

- **Configuration / environment baseline:** Use when the task touches environment variables, Docker, deployment, config files, endpoints, credentials, or runtime settings.

- **Error handling / logging / observability baseline:** Use when the task touches exceptions, logs, traces, metrics, audit events, telemetry, or task records.

- **Testing style baseline:** Use when the task adds or changes tests, fixtures, regression checks, workflow behavior, or validation commands.

- **Documentation / ADR / spike style baseline:** Use when the task creates or edits ADRs, research notes, spike docs, feasibility records, or task logs.

- **Future tooling note:** Use when the task discusses formatter/linter/type-checker/tooling adoption.

## General coding principles

- Prefer simple, explicit, readable code.
- Make surgical changes: touch only files required by the current task.
- Follow existing repository style.
- Do not reformat unrelated files.
- Do not rename or restructure unrelated code.
- Do not introduce speculative abstractions or extra configurability.
- Keep task scope narrow; do not add unrelated features or cleanups.

## Python style baseline

- Prefer type hints for public functions, ports, adapters, DTO/schema-like objects, and non-trivial helpers.
- Keep functions small and purpose-specific.
- Avoid broad catch-all exceptions unless explicitly justified.
- Avoid hidden side effects in utility functions.
- Prefer explicit config injection over hard-coded environment assumptions.
- Do not log secrets, tokens, passwords, access keys, or sensitive business data.
- Use clear names for ports, adapters, services, runners, tasks, and fixtures.
- Do not introduce global mutable state unless the task requires it and explains why.

## Backend / service structure baseline

- Keep router / service / adapter / port responsibilities separated when production code begins.
- Runtime / gateway / policy / identity / trace code should preserve clear boundaries.
- External system access should go through ports / adapters, not scattered direct calls.
- Configuration and credentials must stay outside business logic.
- Production code should not reuse spike-only code directly without a promotion task.
- For async / background jobs: define timeout, retry, idempotency, and cancellation behavior explicitly when relevant.
- Do not introduce unbounded loops, unbounded concurrency, or uncontrolled background tasks.
- Performance-sensitive changes require explicit measurement or a documented reason when measurement is not available.

## Database / schema style baseline

### Naming

- Use snake_case for table names, column names, indexes, constraints, and migration identifiers unless a later schema ADR decides otherwise.
- Use clear, domain-specific names; avoid vague names such as `data`, `info`, `payload` unless the field purpose is documented.

### Keys and relationships

- Prefer explicit primary keys, foreign keys, unique constraints, and indexes over implicit assumptions.
- Relationship fields should be named consistently, for example `*_id` for references, unless a later schema ADR defines another convention.

### Audit and lifecycle fields

- Use consistent audit fields where applicable, such as `created_at`, `updated_at`, `created_by`, `updated_by`.
- Status / lifecycle fields should use explicit constrained values where practical, not free-form strings.

### Sensitive data

- Do not store secrets, tokens, passwords, access keys, refresh tokens, or sensitive payloads in plain text.
- Credential / token storage must follow the project's vault / secret-provider design, not ad hoc table fields.

### JSON / flexible fields

- JSON / JSONB fields must have a clear purpose and documented structure.
- Do not use JSON as a dumping ground for unclear schema.
- If JSON is used for extension fields, document expected keys and ownership.

### Migrations

- Migrations must be small, reviewable, and tied to the current task.
- Do not mix unrelated schema changes.
- Do not rewrite migration history after it is shared.
- Rollback / forward-fix expectations should be documented when relevant.

### Scope boundary for P0-STYLE-001

- P0-STYLE-001 defines style principles only.
- Do not create database migrations, SQL schema files, ORM models, or database config in this task.
- Actual database schema and migration conventions should be refined in future persistence / database tasks.

## API / interface style baseline

- API routes should use consistent naming, versioning, and resource-oriented structure when production APIs begin.
- Request / response schemas should be explicit, typed, and stable; avoid ad hoc dictionaries for externally visible contracts.
- API handlers should stay thin; business logic belongs in services, workflows, or domain modules.
- Error responses should be consistent and should not leak secrets, stack traces, credentials, or sensitive business data.
- External system integration should go through ports / adapters, not scattered direct calls.
- Breaking API changes must be explicit and tied to the current task.
- Scope boundary: P0-STYLE-001 does not define final OpenAPI, gateway routes, or production API contracts; those belong to later API / gateway tasks.

## Extension point / capability plug-in style baseline

- Extension points should be explicit, named, and owned; avoid hidden magic hooks.
- New capabilities, tools, skills, adapters, or workflow hooks should have clear input, output, permission, risk, and ownership boundaries.
- Do not add plug-in points speculatively; introduce them only when a task requires a real extension boundary.
- Registry-style designs should separate metadata from execution logic.
- Capability discovery should not bypass policy, identity binding, audit, approval, or observability boundaries.
- Spike-only extension code must not be promoted to production without a follow-up promotion task.
- Scope boundary: P0-STYLE-001 defines style principles only; detailed capability registry / plugin protocol / tool schema conventions belong to later architecture / execution-fabric / capability-registry tasks.

## Testing style baseline

- Use TDD where appropriate for production code, bugfixes, runners, adapters, and regression-sensitive changes.
- Prefer minimal, meaningful assertions over broad smoke tests.
- Name tests by behavior, not implementation detail.
- Use fixtures deliberately; avoid over-large fixtures.
- For workflow / Golden Task / API behavior, prefer BDD-style Given / When / Then or input-action-expected-output.
- Do not require .feature files by default.
- Do not claim verification unless commands were actually run.
- For async / background jobs: assert timeout, retry, idempotency, and cancellation behavior when relevant.

## Documentation / ADR / spike style baseline

- ADR should clearly separate Context, Decision, Alternatives, Consequences, Evidence, Risks, and Open Questions.
- Spike code must be marked spike-only.
- If an environment or vendor document is unavailable, record `needs_test_environment` / `needs_vendor_confirmation` / `needs_internal_mirror_confirmation` instead of guessing.
- Do not turn spike code into production code without a follow-up promotion task.

## Security / privacy baseline

- Treat AI-generated code as untrusted until reviewed and verified.
- Validate untrusted input at trusted boundaries.
- Do not leak stack traces, secrets, credentials, tokens, internal IDs, or sensitive business data.
- Security-sensitive code such as auth, authorization, crypto, credential handling, approval, audit, and policy enforcement requires stricter review.
- Never hard-code secrets, credentials, tokens, API keys, private URLs, or production endpoints.
- Do not weaken auth / ACL / audit / approval checks for convenience.
- Scope boundary: P0-STYLE-001 defines principles only; detailed security controls belong to later security / gateway / policy / identity tasks.

## Dependency / supply-chain / license baseline

- Do not add dependencies unless the current task explicitly allows it.
- Prefer existing dependencies and standard library capabilities.
- Do not accept hallucinated package names or unknown packages without verification.
- New dependencies require source, license, maintenance, security, and internal mirror availability review.
- Do not modify dependency / lock files in P0-STYLE-001.
- Generated code must not copy external code snippets whose license is unclear.

## Configuration / environment baseline

- Keep environment-specific values outside source code.
- Use explicit configuration boundaries; do not scatter environment reads across business logic.
- Do not hard-code localhost / production URLs / credentials unless it is spike-only and clearly marked.
- Defaults must be safe for local development and must not imply production readiness.
- Config examples must use placeholders, not real secrets.
- Production configuration conventions belong to later deployment / environment tasks.

## Error handling / logging / observability baseline

- Errors should be explicit, actionable, and safe.
- Do not swallow exceptions silently.
- Logs must not contain secrets, credentials, tokens, session IDs, or sensitive business payloads.
- Use consistent event names and correlation / trace IDs when available.
- Audit-worthy actions should be distinguishable from debug logs.
- Do not fake observability evidence; record what was actually logged or traced.

## AI tool collaboration rules

- Execution agents should not perform unrelated cleanup.
- Review agents should flag style churn, broad reformatting, unrelated refactors, and scope creep.
- Codex / Claude Code / MiMo should all follow the same project style baseline regardless of model preference.
- If a model wants to change style globally, it must propose a separate style task, not do it inside a feature task.
- Formatter / linter adoption should be a separate future task when the stack stabilizes.
- Treat AI-generated code as draft work by a junior contributor; it requires review and verification.
- The agent that writes code must report assumptions, files changed, commands run, and commands not run.
- Keep task context narrow; do not load unrelated specs, old prompts, or entire histories unless the task requires it.
- Prefer references to canonical docs over pasting long sections.
- Start fresh sessions or compact handoffs between logical tasks when context becomes noisy.
- Reviewers should check intent and logic, not only syntax.
- Use references to relevant sections instead of pasting the full baseline.
- If a task is narrow, do not load unrelated baseline sections.
- Reviewers should flag unnecessary context loading or irrelevant baseline expansion as context pollution risk.
- Missing a relevant section can also be a review risk when the task touches that domain.

## Future tooling note

- Future tasks may evaluate Ruff / pyright or mypy / ESLint / Prettier / EditorConfig when the production stack stabilizes.
- P0-STYLE-001 does not install or configure those tools.
