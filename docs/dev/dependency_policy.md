# Dependency Mirror and Allowlist Policy

This policy is the Phase 0 dependency governance baseline for repository-local
Python and frontend dependency changes. It defines how agents document mirror
usage and where the deterministic dependency allowlist lives.

P0-INFRA-008 does not add production dependencies, lockfiles, or CI wiring. CI
integration for this checker is deferred to P0-INFRA-007.

## Environment model

This policy recognizes three distinct environments with different dependency-source
requirements:

### Phase 0 internet-connected local development/build

- Development/build machines have internet access.
- May use public registries (PyPI, npmjs.org) only when explicitly allowed by
  the task prompt and this policy.
- `uv sync`, `pnpm install`, `npm install` run in this environment.
- All added dependencies must still have a matching row in the Dependency
  Allowlist before the manifest or lockfile is changed.

### Intranet runtime deployment

- Uses prebuilt Docker images produced from the local development/build phase.
- Must not run `pnpm install`, `npm install`, `uv sync`, or `pip install` at
  runtime.
- Must not require npm, pnpm, or PyPI registry access at runtime.
- Docker images are self-contained with all dependencies pre-installed.

### Future intranet source build / intranet CI / stricter supply-chain governance

- Enterprise npm mirror or approved offline cache is required.
- This requirement is deferred to later tasks (e.g., P0-INFRA-007 when executed
  in intranet CI, or a dedicated supply-chain governance task).
- The mirror_status field in the Dependency Allowlist records the current state
  for each dependency.

## General Rules

- Do not add a Python, npm, or pnpm dependency unless it is represented in the
  deterministic allowlist source below.
- Do not store real mirror URLs, credentials, tokens, cookies, passwords, or
  private registry authentication in this repository.
- Phase 0 internet-connected local development/build may use public registries
  (PyPI, npmjs.org) only when explicitly allowed by the task prompt and this
  policy. Public registries are not enterprise mirror evidence or approved
  offline-cache evidence, and do not prove intranet source-build compliance.
- Internal mirrors or approved offline caches supplied by the internal
  environment/package administrator are required for intranet source build,
  intranet CI, or stricter supply-chain governance. This requirement is deferred
  to relevant future tasks.
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
- Phase 0 internet-connected local development/build may use public npm registry
  (https://registry.npmjs.org/) when explicitly allowed by the task prompt and
  this policy.
- Intranet runtime uses prebuilt Docker images and must not run `pnpm install`
  or require npm registry access.
- Enterprise npm mirror or approved offline cache is required only for future
  intranet source build, intranet CI, or stricter supply-chain governance tasks.
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
| npm | react | >=18,<19 | frontend-runtime | pending_internal_mirror_confirmation | P0-FE-SPIKE-001 frozen baseline (React 18); peerDep >=18.0.0 confirmed |
| npm | react-dom | >=18,<19 | frontend-runtime | pending_internal_mirror_confirmation | P0-FE-SPIKE-001 frozen baseline (React 18); peerDep >=18.0.0 confirmed |
| npm | vite | pending_version_evidence_no_manifest_use | frontend-tooling | pending_internal_mirror_confirmation | P0-FE-SPIKE-001 frozen baseline (Vite); informational only — do not authorize manifest or lockfile use until a later task records an evidence-based version range |
| npm | typescript | pending_version_evidence_no_manifest_use | frontend-tooling | pending_internal_mirror_confirmation | P0-FE-SPIKE-001 frozen baseline (TypeScript strict); informational only — do not authorize manifest or lockfile use until a later task records an evidence-based version range |
| npm | antd | >=5,<6 | frontend-runtime | pending_internal_mirror_confirmation | P0-FE-SPIKE-001 frozen baseline (Ant Design 5.x); peerDep >=5.0.0/^5.20.3 confirmed |
| npm | @ant-design/pro-components | >=2,<3 | frontend-runtime | pending_internal_mirror_confirmation | P0-FE-SPIKE-001 frozen baseline (ProComponents 2.x) |
| npm | react-router-dom | pending_version_evidence_no_manifest_use | frontend-runtime | pending_internal_mirror_confirmation | P0-FE-SPIKE-001 frozen baseline (React Router); informational only — do not authorize manifest or lockfile use until a later task records an evidence-based version range |
| npm | @tanstack/react-query | pending_version_evidence_no_manifest_use | frontend-runtime | pending_internal_mirror_confirmation | P0-FE-SPIKE-001 frozen baseline (TanStack Query); informational only — do not authorize manifest or lockfile use until a later task records an evidence-based version range |
| npm | zustand | pending_version_evidence_no_manifest_use | frontend-runtime | pending_internal_mirror_confirmation | P0-FE-SPIKE-001 frozen baseline (Zustand); informational only — do not authorize manifest or lockfile use until a later task records an evidence-based version range |
| npm | orval | pending_version_evidence_no_manifest_use | frontend-tooling | pending_internal_mirror_confirmation | P0-FE-SPIKE-001 frozen baseline (Orval); informational only — do not authorize manifest or lockfile use until a later task records an evidence-based version range |

Future frontend entries must use `ecosystem` value `npm` and must be added here
before any frontend manifest or lockfile is changed.

## Excluded frontend candidates

The following packages were evaluated but are excluded from the Phase 1 npm
allowlist. This section is informational only and must not be parsed as part of
the Dependency Allowlist table above.

| package | exclusion_reason | evidence_source | carrying_task |
|---|---|---|---|
| @ant-design/x | Blocked: no approved enterprise npm mirror or offline cache configured. v2.x requires antd ^6.0.0+ (incompatible with frozen antd 5.x baseline). Only legacy v1.x (v1.6.1) is antd 5.x-compatible. | P0-FE-SPIKE-001_20260518_211335_blocked.yaml; public npmjs.org (non-authoritative reference) | P0-FE-SPIKE-001 |

