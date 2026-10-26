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
| python | mcp | ==2.2.0 | production | public_registry_development_only | P2-MCP-PLATFORM-001 approved SDK boundary |
| python | httpx2 | >=2.5.0,<3 | production | public_registry_development_only | P2-MCP-PLATFORM-001 bounded MCP HTTP transport |
| python | playwright | ==1.63.0 | production | public_registry_development_only | User 2026-10-02 v4.2 approved Python async Playwright; browser binaries and deployment verification remain separate |
| python | psutil | ==7.2.2 | production (Windows only) | public_registry_development_only | P2-CHAT-BROWSER-READ-001 2026-10-11 scoped dependency wiring for Windows process identity, profile and CDP listener checks; runtime verification remains separate |
| python | pyee | ==13.0.1 | production-transitive | public_registry_development_only | User 2026-10-02 v4.2 Playwright 1.63.0 required dependency, pinned by uv.lock |
| python | openai | >=1.0.0 | spike | pending_internal_mirror_confirmation | P0-SPIKE-001/P0-SPIKE-002/P0-SPIKE-007 spike requirements |
| python | fastapi | >=0.115,<1.0 | backend-runtime | pending_internal_mirror_confirmation | P0-INFRA-002 backend skeleton (FastAPI backend skeleton / API app foundation) |
| python | uvicorn | >=0.34,<1.0 | backend-runtime | pending_internal_mirror_confirmation | P2-PILOT-FOUNDATION-001; 雨爷批准 2026-07-27（生产 ASGI 进程入口） |
| python | pydantic | >=2,<3 | spike; backend-runtime | pending_internal_mirror_confirmation | P0-SPIKE-001/P0-SPIKE-002/P0-SPIKE-007 spike requirements; P0-INFRA-002 backend skeleton (FastAPI backend skeleton / API app foundation) |
| python | jsonschema | >=4.23,<5 | backend-runtime | pending_internal_mirror_confirmation | P2-SMOKE-AUTH-DIAG-001; 雨爷批准 2026-08-06（Gateway 执行 Registry input_schema） |
| python | types-jsonschema | >=4.23,<5 | backend-tooling | pending_internal_mirror_confirmation | P2-SMOKE-AUTH-DIAG-001; jsonschema mypy 类型守卫 |
| python | types-psutil | ==7.2.2.20260906 | backend-tooling | public_registry_development_only | P2-CHAT-BROWSER-READ-001 2026-10-16 required-CI type closure; official typeshed stubs for pinned psutil 7.2.2, development only |
| python | pytest | >=8,<9 | backend-test | pending_internal_mirror_confirmation | P0-INFRA-002 backend skeleton (backend test runner for TDD/validation) |
| python | ruff | >=0.8,<1 | backend-tooling | pending_internal_mirror_confirmation | P0-INFRA-002 backend skeleton (backend linting / formatting validation) |
| python | mypy | >=1.13,<2 | backend-tooling | pending_internal_mirror_confirmation | P0-INFRA-002 backend skeleton (backend static type checking) |
| python | httpx | >=0.24.0 | spike; backend-test | pending_internal_mirror_confirmation | P0-SPIKE-001/P0-SPIKE-002/P0-SPIKE-007 spike requirements; P0-INFRA-002 backend skeleton (backend test client / HTTP validation support) |
| python | cryptography | >=42,<47 | backend-runtime | pending_internal_mirror_confirmation | P2-AUTH-001; 雨爷批准 2026-07-24, 见 docs/phase2/DECISIONS.md |
| python | instructor | >=1.0.0 | spike | pending_internal_mirror_confirmation | P0-SPIKE-002 spike requirements |
| python | pydantic-ai | >=1,<2 | spike | pending_internal_mirror_confirmation | P0-SPIKE-007 spike requirements |
| python | alembic | >=1.13,<2.0 | spike; backend-migration | pending_internal_mirror_confirmation | P0-SPIKE-003 spike requirements; P0-INFRA-002 backend skeleton (Alembic baseline dependency for later migration integration) |
| python | sqlalchemy | >=2.0,<3.0 | spike; backend-runtime | pending_internal_mirror_confirmation | P0-SPIKE-003 spike requirements; P0-INFRA-002 backend skeleton (SQLAlchemy backend baseline) |
| python | psycopg | >=3.1,<4.0 | spike; backend-runtime | pending_internal_mirror_confirmation | P0-SPIKE-003 spike requirements; P0-INFRA-004A / P0-INFRA-004 database baseline runtime driver |
| python | asyncpg | ==0.32.0 | backend-runtime | public_registry_development_only | P2-CHAT-BROWSER-READ-001 2026-10-11 controller-relayed authorization for asyncpg + Proactor compatibility and exact dependency metadata scope |
| python | agentscope | ==2.0.11.dev0 (source archive of commit 6c60853f4237dbc237803cdee2fb96a27a6a0611, sha256 af3fc9d4413acdbbd5744909702230e301461454c15c4b0844625718cf6130eb, pinned by uv.lock) | production | public_source_archive_development_only; no approved offline cache | P2-RUNTIME-AGENTSCOPE-M0-001; 雨爷 2026-10-21 approved Runtime replacement plan v1.3 M0 (in-process SDK, infra-only types); production-transitive inventory below |
| python | arq | >=0.26,<1 | spike | pending_internal_mirror_confirmation | P0-SPIKE-004 spike requirements |
| python | redis | >=5,<6 | spike | pending_internal_mirror_confirmation | P0-SPIKE-004 spike requirements |
| npm | react | >=18,<19 | frontend-runtime | pending_internal_mirror_confirmation | P0-FE-SPIKE-001 frozen baseline (React 18); peerDep >=18.0.0 confirmed |
| npm | react-dom | >=18,<19 | frontend-runtime | pending_internal_mirror_confirmation | P0-FE-SPIKE-001 frozen baseline (React 18); peerDep >=18.0.0 confirmed |
| npm | vite | >=5,<9 | frontend-tooling | pending_internal_mirror_confirmation | P0-INFRA-003C evidence-based range; P0-FE-SPIKE-001 frozen baseline (Vite) |
| npm | typescript | >=5,<7 | frontend-tooling | pending_internal_mirror_confirmation | P0-INFRA-003C evidence-based range; P0-FE-SPIKE-001 frozen baseline (TypeScript strict) |
| npm | antd | >=6,<7 | frontend-runtime | pending_internal_mirror_confirmation | P2-FE-ANTD6-001 verified baseline (Ant Design 6.x) |
| npm | @ant-design/x | >=2,<3 | frontend-runtime | pending_internal_mirror_confirmation | P2-FE-VISUAL-REFACTOR-001; `docs/phase2/DECISIONS.md` 2026-08-20「前端视觉基线」裁定装 `@ant-design/x`；原排除理由「v2.x requires antd ^6.0.0+ (incompatible with frozen antd 5.x baseline)」已随 P2-FE-ANTD6-001 把基线迁到 antd 6 而失效 |
| npm | react-router-dom | >=6,<8 | frontend-runtime | pending_internal_mirror_confirmation | P0-INFRA-003C evidence-based range; P0-FE-SPIKE-001 frozen baseline (React Router) |
| npm | @tanstack/react-query | >=5,<6 | frontend-runtime | pending_internal_mirror_confirmation | P0-INFRA-003C evidence-based range; P0-FE-SPIKE-001 frozen baseline (TanStack Query) |
| npm | zustand | >=5,<6 | frontend-runtime | pending_internal_mirror_confirmation | P0-INFRA-003C evidence-based range; P0-FE-SPIKE-001 frozen baseline (Zustand) |
| npm | @types/react | >=18,<19 | frontend-devtypes | pending_internal_mirror_confirmation | P0-INFRA-003C helper; React 18 type definitions |
| npm | @types/react-dom | >=18,<19 | frontend-devtypes | pending_internal_mirror_confirmation | P0-INFRA-003C helper; React 18 DOM type definitions |
| npm | orval | >=7,<9 | frontend-tooling | pending_internal_mirror_confirmation | P0-INFRA-003C evidence-based range; P0-FE-SPIKE-001 frozen baseline (Orval) |
| npm | @vitejs/plugin-react | >=5,<7 | frontend-tooling | pending_internal_mirror_confirmation | P0-INFRA-003C helper; Vite React plugin for JSX transform |
| npm | @types/node | >=20,<26 | frontend-devtypes | pending_internal_mirror_confirmation | P0-INFRA-003C helper; Node.js type definitions for Vite config |
| npm | vitest | >=2,<5 | frontend-test | pending_internal_mirror_confirmation | P0-INFRA-003C helper; test runner |
| npm | @testing-library/react | >=14,<17 | frontend-test | pending_internal_mirror_confirmation | P0-INFRA-003C helper; React component testing utilities |
| npm | @testing-library/jest-dom | >=6,<7 | frontend-test | pending_internal_mirror_confirmation | P0-INFRA-003C helper; custom jest matchers for DOM |
| npm | jsdom | >=24,<30 | frontend-test | pending_internal_mirror_confirmation | P0-INFRA-003C helper; DOM implementation for Vitest |
| npm | eslint | >=9,<11 | frontend-tooling | pending_internal_mirror_confirmation | P0-INFRA-003C helper; linter |
| npm | @eslint/js | >=9,<11 | frontend-tooling | pending_internal_mirror_confirmation | P0-INFRA-003C helper; ESLint core JS rules |
| npm | typescript-eslint | >=8,<9 | frontend-tooling | pending_internal_mirror_confirmation | P0-INFRA-003C helper; TypeScript ESLint integration |
| npm | eslint-plugin-react-hooks | >=5,<8 | frontend-tooling | pending_internal_mirror_confirmation | P0-INFRA-003C helper; React hooks lint rules |
| npm | eslint-plugin-react-refresh | >=0.4,<0.6 | frontend-tooling | pending_internal_mirror_confirmation | P0-INFRA-003C helper; React refresh lint rules |
| npm | globals | >=15,<18 | frontend-tooling | pending_internal_mirror_confirmation | P0-INFRA-003C helper; global variable definitions for ESLint |

Future frontend entries must use `ecosystem` value `npm` and must be added here
before any frontend manifest or lockfile is changed.

## AgentScope production-transitive inventory

Informational inventory for P2-RUNTIME-AGENTSCOPE-M0-001; it is not parsed by
`scripts/check_dependencies.py`, which checks direct manifest entries only. These
packages are the production closure added by the `agentscope` row above and must
ship with any offline package. Versions are the exact `uv.lock` pins; licenses come
from the PyPI metadata of the pinned version. `agentscope` itself is Apache-2.0.
Every row is `public_registry_development_only`: no enterprise mirror or approved
offline cache exists yet, and local resolution is not target-environment evidence.
Disabled integrations (OTLP export, socket.io, DashScope/Anthropic clients) are
installed because the SDK requires them; installation does not enable them.

| package | locked_version | license | lock_status |
|---|---|---|---|
| aiofiles | 25.1.0 | Apache-2.0 | new |
| aiohappyeyeballs | 2.7.1 | PSF-2.0 | new |
| aiohttp | 3.14.5 | Apache-2.0 AND MIT | new |
| aioitertools | 0.13.0 | MIT | new |
| aiosignal | 1.4.0 | Apache-2.0 | new |
| anthropic | 1.17.0 | MIT | new |
| bidict | 0.24.1 | MPL-2.0 | new |
| cached-property | 2.0.1 | BSD | new |
| certifi | 2026.4.22 | MPL-2.0 | already locked; now production |
| charset-normalizer | 3.5.2 | MIT | new |
| dashscope | 1.27.8 | Apache-2.0 | new |
| docstring-parser | 0.18.0 | MIT | new |
| filetype | 1.2.0 | MIT | new |
| frozenlist | 1.8.0 | Apache-2.0 | new |
| googleapis-common-protos | 1.75.5 | Apache-2.0 | new |
| grpcio | 1.84.0 | Apache-2.0 | new |
| httpcore | 1.0.9 | BSD-3-Clause | already locked; now production |
| httpx | 0.28.1 | BSD-3-Clause | already locked; now production |
| httpx-sse | 0.4.3 | MIT | new |
| jinja2 | 3.1.6 | BSD | new |
| jiter | 0.17.0 | MIT | new |
| json-repair | 0.64.1 | MIT | new |
| json5 | 0.17.3 | Apache-2.0 | new |
| markdown-it-py | 4.2.0 | MIT | new |
| mdurl | 0.1.2 | MIT | new |
| multidict | 7.1.0 | Apache-2.0 | new |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | new |
| openai | 3.35.1 | Apache-2.0 | new |
| opentelemetry-exporter-http-transport | 0.66b0 | Apache-2.0 | new |
| opentelemetry-exporter-otlp | 1.45.0 | Apache-2.0 | new |
| opentelemetry-exporter-otlp-common | 0.66b0 | Apache-2.0 | new |
| opentelemetry-exporter-otlp-proto-common | 1.45.0 | Apache-2.0 | new |
| opentelemetry-exporter-otlp-proto-grpc | 1.45.0 | Apache-2.0 | new |
| opentelemetry-exporter-otlp-proto-http | 1.45.0 | Apache-2.0 | new |
| opentelemetry-proto | 1.45.0 | Apache-2.0 | new |
| opentelemetry-sdk | 1.45.0 | Apache-2.0 | new |
| opentelemetry-semantic-conventions | 0.66b0 | Apache-2.0 | new |
| propcache | 0.5.4 | Apache-2.0 | new |
| protobuf | 7.36.2 | BSD-3-Clause | new |
| pygments | 2.20.0 | BSD-2-Clause | already locked; now production |
| pypdf | 6.21.1 | BSD-3-Clause | new |
| python-datauri | 3.0.2 | Unlicense | new |
| python-engineio | 4.14.0 | MIT | new |
| python-frontmatter | 1.3.0 | MIT | new |
| python-socketio | 5.17.0 | MIT | new |
| pyyaml | 6.0.3 | MIT | new |
| requests | 2.34.2 | Apache-2.0 | new |
| rich | 15.0.0 | MIT | new |
| shellingham | 1.5.4 | ISC | new |
| shortuuid | 1.0.13 | BSD | new |
| simple-websocket | 1.1.0 | MIT | new |
| sniffio | 1.3.1 | Apache-2.0 OR MIT | new |
| tree-sitter | 0.26.0 | MIT | new |
| tree-sitter-bash | 0.25.1 | MIT | new |
| typer | 0.27.3 | MIT | new |
| urllib3 | 2.8.0 | MIT | new |
| websocket-client | 1.9.2 | Apache-2.0 | new |
| wsproto | 1.3.2 | MIT | new |
| yarl | 1.25.1 | Apache-2.0 | new |

## Excluded frontend candidates

The following packages were evaluated but are excluded from the Phase 1 npm
allowlist. This section is informational only and must not be parsed as part of
the Dependency Allowlist table above.

| package | exclusion_reason | evidence_source | carrying_task |
|---|---|---|---|
| @ant-design/pro-components | 移出基线，改为 antd 6 原生 Table + 项目自有薄查询层 | P2-FE-ANTD6-001 | P2-FE-ANTD6-001 |

