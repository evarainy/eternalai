# Dependency Mirror and Allowlist Policy

This policy is the Phase 0 dependency governance baseline for repository-local
Python and frontend dependency changes. It defines how agents document mirror
usage and where the deterministic dependency allowlist lives.

P0-INFRA-008 does not add production dependencies, lockfiles, or CI wiring. CI
integration for this checker is deferred to P0-INFRA-007.

## General Rules

- Do not add a Python, npm, or pnpm dependency unless it is represented in the
  deterministic allowlist source below.
- Do not store real mirror URLs, credentials, tokens, cookies, passwords, or
  private registry authentication in this repository.
- Use only internal mirrors or approved offline caches supplied by the internal
  environment/package administrator.
- Spike dependencies must remain in spike-only manifests and must not enter
  production dependency groups without a later task updating this policy.
- Production dependency manifests and lockfiles are out of scope for
  P0-INFRA-008.

## Python uv Mirror Rules

- Use `uv` for Python dependency installation once backend manifests are created
  by later tasks.
- Configure mirror endpoints through environment variables such as
  `UV_INDEX_URL` and `PIP_INDEX_URL`; use `.env.example` only for placeholder
  documentation.
- Do not commit real PyPI mirror addresses or private authentication values.
- Every added Python dependency must have a matching row in the Dependency
  Allowlist before the manifest or lockfile is changed.

## Frontend pnpm Registry Rules

- Use `pnpm` for frontend dependency installation once frontend manifests are
  created by later tasks.
- Configure registry endpoints through environment variables such as
  `NPM_CONFIG_REGISTRY` or through local developer/package-manager config that
  is not committed with secrets.
- Do not commit real npm registry URLs, `_authToken`, username/password pairs,
  or private registry credentials.
- Every added npm dependency must have a matching row in the Dependency
  Allowlist before `package.json` or lockfiles are changed.

## Dependency Allowlist

Deterministic checker source: this section in `docs/dev/dependency_policy.md`.

Dedicated allowlist file: none for P0-INFRA-008.

`scripts/check_dependencies.py` must parse only this section unless this section
explicitly names a future dedicated allowlist file.

| ecosystem | package | allowed_version_range | dependency_group | mirror_status | approval_source |
|---|---|---|---|---|---|
| python | openai | >=1.0.0 | spike | pending_internal_mirror_confirmation | P0-SPIKE-001/P0-SPIKE-002/P0-SPIKE-007 spike requirements |
| python | fastapi | >=0.115,<1.0 | backend-runtime | pending_internal_mirror_confirmation | P0-INFRA-002 backend skeleton (FastAPI backend skeleton / API app foundation) |
| python | pydantic | >=2,<3 | spike; backend-runtime | pending_internal_mirror_confirmation | P0-SPIKE-001/P0-SPIKE-002/P0-SPIKE-007 spike requirements; P0-INFRA-002 backend skeleton (FastAPI backend skeleton / API app foundation) |
| python | pytest | >=8,<9 | backend-test | pending_internal_mirror_confirmation | P0-INFRA-002 backend skeleton (backend test runner for TDD/validation) |
| python | ruff | >=0.8,<1 | backend-tooling | pending_internal_mirror_confirmation | P0-INFRA-002 backend skeleton (backend linting / formatting validation) |
| python | mypy | >=1.13,<2 | backend-tooling | pending_internal_mirror_confirmation | P0-INFRA-002 backend skeleton (backend static type checking) |
| python | httpx | >=0.24.0 | spike; backend-test | pending_internal_mirror_confirmation | P0-SPIKE-001/P0-SPIKE-002/P0-SPIKE-007 spike requirements; P0-INFRA-002 backend skeleton (backend test client / HTTP validation support) |
| python | instructor | >=1.0.0 | spike | pending_internal_mirror_confirmation | P0-SPIKE-002 spike requirements |
| python | pydantic-ai | >=1,<2 | spike | pending_internal_mirror_confirmation | P0-SPIKE-007 spike requirements |
| python | alembic | >=1.13,<2.0 | spike; backend-migration | pending_internal_mirror_confirmation | P0-SPIKE-003 spike requirements; P0-INFRA-002 backend skeleton (Alembic baseline dependency for later migration integration) |
| python | sqlalchemy | >=2.0,<3.0 | spike; backend-runtime | pending_internal_mirror_confirmation | P0-SPIKE-003 spike requirements; P0-INFRA-002 backend skeleton (SQLAlchemy backend baseline) |
| python | psycopg | >=3.1,<4.0 | spike | pending_internal_mirror_confirmation | P0-SPIKE-003 spike requirements |
| python | arq | >=0.26,<1 | spike | pending_internal_mirror_confirmation | P0-SPIKE-004 spike requirements |
| python | redis | >=5,<6 | spike | pending_internal_mirror_confirmation | P0-SPIKE-004 spike requirements |

Future frontend entries must use `ecosystem` value `npm` and must be added here
before any frontend manifest or lockfile is changed.

