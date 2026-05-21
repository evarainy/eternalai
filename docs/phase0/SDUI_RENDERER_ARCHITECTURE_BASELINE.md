# SDUI Renderer Architecture Baseline — PDR

## Purpose

Define the minimal architecture boundary for the future Server-Driven UI (SDUI) Renderer. This document establishes interface contracts, trust boundaries, and design decisions so that subsequent implementation tasks have a frozen reference point. It does not implement the renderer.

## Non-goals

- Implement the SDUI renderer, card library, or any `web/src/sdui/**` code.
- Define a full card schema language or exhaustive card type catalog.
- Define backend endpoints, runtime logic, gateway policies, or adapter integrations.
- Change the frontend dependency baseline or add new dependencies.
- Create frontend fixtures, routes, tests, or UI pages.
- Establish a broad workflow engine or multi-step orchestration system.

## Current frontend baseline and exclusions

The SDUI renderer must integrate with the frozen frontend baseline established by P0-INFRA-003 and governed by `docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md`:

- React 18
- Vite
- TypeScript strict
- Ant Design 5.x
- ProComponents 2.x
- React Router
- TanStack Query
- Zustand
- Orval

The following are excluded and must not be adopted without a separate approved task and dependency-policy update:

- Ant Design X
- Ant Design 6
- React 19
- Next.js
- Tailwind
- shadcn/ui
- msw

The existing `web/**` skeleton is read-only context for this task. No `web/**` files are modified.

## PDR summary

| Aspect | Decision |
|---|---|
| Method | PDR (Plan, Alternatives, Risks, Recommendation) |
| Scope | Architecture boundary definition only |
| Implementation | Deferred to future tasks |
| Trust model | Renderer trusts nothing from payload; validates at boundary |
| State model | Zustand for local UI/session; TanStack Query for server/cache |
| Action model | Structured intent dispatch only; no direct business execution |

## ComponentRegistry boundary

### Definition

ComponentRegistry is a static registration metadata map that resolves a card type key to a React component. It is a compile-time artifact, not a runtime plugin system.

### Interface contract

```
ComponentRegistry:
  register(type: string, component: React.ComponentType, metadata: ComponentMeta): void
  resolve(type: string): RegisteredComponent | undefined
  has(type: string): boolean
```

### Key positions

1. **Static registration only.** Components are registered at application bootstrap or module initialization time. There is no runtime plugin loading, dynamic `import()` on demand from untrusted sources, or hot-swapping of components.
2. **Type key is a string constant.** Card type keys are defined by the backend schema contract (e.g., `"message"`, `"confirm_card"`, `"operator_handback_card"`, `"binding_required_card"`). The registry does not invent keys.
3. **Lookup returns `undefined` for unknown types.** The renderer must handle `undefined` resolution by delegating to FallbackRenderer. The registry must not throw on unknown types.
4. **Registration is idempotent.** Re-registering the same type key with the same component and metadata is a no-op. Re-registering with a different component for the same key is a development-time error (logged, not thrown in production).
5. **No dependency on backend adapters.** ComponentRegistry must not import or reference OA, U8, Hik, or any adapter module. It is a pure frontend mapping.

### Alternatives considered

| Alternative | Why rejected |
|---|---|
| Runtime plugin loading with dynamic import | Increases attack surface; untrusted payloads could trigger arbitrary module loading. Static registration is safer and simpler. |
| Convention-based auto-discovery (file naming) | Implicit registration hides the mapping; makes debugging harder. Explicit registration is auditable. |
| Decorator-based registration | Requires experimental TypeScript features or Babel plugins; adds build complexity for no Phase 0 benefit. |

## ActionDispatcher boundary

### Definition

ActionDispatcher receives a structured user intent from an SDUI card and forwards it as a single structured action. It does not execute business logic directly.

### Interface contract

```
UserAction:
  type: string          // e.g., "confirm", "cancel", "navigate"
  payload: JsonObject   // action-specific data, validated at boundary
  card_id: string       // originating card identifier
  schema_version: string

ActionDispatcher:
  dispatch(action: UserAction): DispatchResult
```

### Key positions

1. **Structured intent only.** Cards emit `UserAction` objects with a `type`, `payload`, `card_id`, and `schema_version`. The dispatcher validates the shape and forwards it.
2. **No direct business execution.** The dispatcher must not call OA/U8/Hik adapters, invoke backend APIs, modify databases, or trigger side effects beyond UI state updates and forwarding the action to the Runtime/Capability Gateway layer.
3. **Real business execution returns to Runtime / Capability Gateway.** The dispatcher's role ends at forwarding. The Runtime or Capability Gateway is responsible for executing business actions, calling adapters, and returning results.
4. **Confirm flows are single structured actions.** A confirm card emits one `UserAction` with `type: "confirm"`. It is not a workflow engine. Multi-step flows are orchestrated by the backend/Runtime, not by the frontend dispatcher.
5. **Boundary with Zustand.** The dispatcher may update local UI state via Zustand (e.g., setting a loading indicator, recording the last dispatched action for optimistic UI). It must not store server data in Zustand — that is TanStack Query's responsibility.
6. **Boundary with TanStack Query.** The dispatcher may trigger TanStack Query invalidation or refetch after a successful action dispatch (e.g., invalidating a query after a confirm action). It must not directly mutate TanStack Query cache.
7. **Payload validation.** The dispatcher must validate `UserAction.payload` against the expected schema for the action type before forwarding. Malformed payloads are rejected with a user-safe error.

### Alternatives considered

| Alternative | Why rejected |
|---|---|
| Direct API calls from cards | Cards would need knowledge of backend endpoints and auth; violates trust boundary and makes cards non-portable. |
| Event bus / pub-sub | Over-engineered for Phase 0; adds indirection without clear benefit. Single dispatcher is simpler and auditable. |
| Redux middleware chain | Redux is not in the frontend baseline; adding it contradicts the Zustand + TanStack Query state model. |

## FallbackRenderer boundary

### Definition

FallbackRenderer is the component that renders when ComponentRegistry cannot resolve a card type. It provides a safe, user-visible fallback instead of crashing or showing nothing.

### Interface contract

```
FallbackRenderer:
  props:
    card_type: string          // the unknown/unsupported type key
    raw_payload: JsonObject    // the original payload, for diagnostic display
    schema_version?: string    // version if available
```

### Key positions

1. **Unknown card types render safely.** FallbackRenderer must always render something visible to the user — never a blank screen or silent failure.
2. **User-safe display.** The fallback must not render raw JSON or internal identifiers directly to the user. It shows a generic message like "This content type is not yet supported" and optionally a card type identifier for debugging (controlled by a development-mode flag).
3. **No credential or sensitive data leakage.** FallbackRenderer must not render values from `raw_payload` that could contain credentials, tokens, cookies, sessions, or secrets. It must filter or redact sensitive keys before any diagnostic display.
4. **Telemetry/logging limits.** FallbackRenderer may log the unknown card type and schema_version for monitoring purposes. It must not log the full raw_payload (which may contain sensitive data). Logging is capped at type + version + timestamp.
5. **Graceful degradation.** The fallback is a non-interactive display. It does not offer retry buttons or action dispatch for unknown types. The user can dismiss or navigate away using standard app navigation.

### Alternatives considered

| Alternative | Why rejected |
|---|---|
| Silent failure (render nothing) | User sees blank space; no indication that content was expected. Violates user-safe display principle. |
| Error boundary with stack trace | Stack traces are developer tooling, not user-facing. Also risks leaking internal structure. |
| Retry mechanism in fallback | Unknown types cannot be retried meaningfully; the card type definition is missing, not transiently unavailable. |

## schema_version strategy

### Definition

Every SDUI payload must include a `schema_version` field. The renderer uses this field to determine compatibility with the payload before rendering.

### Key positions

1. **schema_version is required conceptually.** Every payload from the backend must carry a `schema_version`. The renderer checks for its presence.
2. **Compatibility check.** The renderer maintains a set of supported schema versions. When a payload arrives, the renderer compares the payload's `schema_version` against the supported set.
3. **Supported versions.** The renderer supports a defined range of schema versions (e.g., `"1.0"` to `"1.3"`). This range is a compile-time constant, not dynamically fetched.
4. **Unsupported version handling.** If the schema_version is not in the supported set, the renderer must not attempt to render the payload. It delegates to FallbackRenderer with a version-mismatch indicator.
5. **Malformed version handling.** If `schema_version` is missing, `null`, or not a parseable string, the renderer treats the payload as malformed and delegates to FallbackRenderer. It must not guess or default the version.
6. **Unsafe payload rejection.** If the payload fails schema_version validation, the entire payload is considered unsafe for rendering. The renderer must not partially render cards from unversioned or unsupported payloads.
7. **No auto-migration.** The renderer does not attempt to migrate payloads from older schema versions to newer ones. Migration is a backend responsibility.

### Alternatives considered

| Alternative | Why rejected |
|---|---|
| No versioning (assume latest) | Backend changes would silently break the renderer. Versioning makes incompatibility explicit and safe. |
| Semantic versioning with auto-migration | Auto-migration in the renderer couples frontend to backend schema evolution; increases complexity and risk. Backend owns migration. |
| Version negotiation protocol | Over-engineered for Phase 0. Static supported-version set is sufficient. |

## user_action boundary

### Definition

`user_action` defines what SDUI cards can request from the system and what they cannot trigger. It establishes the trust boundary between the renderer and the backend.

### What SDUI cards CAN request

- **Confirm actions.** A card can request a single confirm action (e.g., user clicks "Approve" on a confirm card). This emits a `UserAction` with `type: "confirm"`.
- **Cancel/dismiss actions.** A card can request cancellation or dismissal.
- **Navigation actions.** A card can request navigation to a known route within the application (route must be a registered React Router route).
- **Form submission.** A card can request submission of form data it collects, as a single structured payload.

### What SDUI cards CANNOT trigger

- **Direct adapter calls.** Cards must not call OA, U8, Hik, or any adapter directly or indirectly through the renderer.
- **Arbitrary API calls.** Cards must not trigger arbitrary HTTP requests. All business actions go through ActionDispatcher → Runtime/Capability Gateway.
- **Database mutations.** Cards must not directly modify any database or persistent store.
- **Credential operations.** Cards must not request, read, store, or transmit credentials, tokens, cookies, or session identifiers.
- **Multi-step workflows.** Cards emit single actions. Multi-step orchestration is a backend/Runtime responsibility.
- **File system access.** Cards must not access the browser file system, local storage (beyond what Zustand manages for UI state), or IndexedDB.
- **Dynamic code execution.** Cards must not trigger `eval()`, `new Function()`, dynamic `import()` from untrusted sources, or any form of code injection.

### Backend/gateway actions remain outside renderer trust

The renderer does not trust the payload to define what happens after an action is dispatched. The backend/Runtime/Capability Gateway determines the actual business effect. The renderer's responsibility ends at dispatching the structured `UserAction`.

## Security and sensitive data boundary

### Key positions

1. **Cards must not expose credentials, tokens, cookies, sessions, or secrets.** The renderer must not render these values even if they appear in the payload. Sensitive keys (e.g., `password`, `token`, `api_key`, `secret`, `cookie`, `session_id`, `access_token`, `refresh_token`) must be filtered from display.
2. **Payload validation at boundary.** The renderer validates payload structure at the entry point. It does not trust payload shape beyond what `schema_version` and the card type contract define.
3. **No inline script or HTML injection.** The renderer must not use `dangerouslySetInnerHTML` or equivalent mechanisms to render card content. All content is rendered through React components.
4. **Action payload sanitization.** Before dispatching a `UserAction`, the dispatcher sanitizes the payload by removing any sensitive keys that should not be forwarded to the backend (defense in depth — the backend also validates).
5. **Development-mode diagnostics are opt-in.** Any diagnostic display of raw payloads, card types, or schema versions is gated behind a development-mode flag and must not be enabled in production.

## Frontend state and API boundary

### Zustand — local UI/session state only

- Zustand stores: UI interaction state (e.g., selected card, loading indicators, expanded/collapsed state), session preferences (e.g., theme, locale), and the last dispatched action for optimistic UI patterns.
- Zustand must not store: server data, API responses, or cached business objects. That is TanStack Query's responsibility.
- Zustand stores must not import or reference backend adapter modules, API client functions, or Runtime/Gateway code.

### TanStack Query — server/cache state only

- TanStack Query owns: API response caching, background refetching, stale data management, and mutation lifecycle.
- TanStack Query must not be used for: local UI state that does not come from a server endpoint.
- The SDUI renderer may trigger TanStack Query invalidation after action dispatch (e.g., `queryClient.invalidateQueries()` after a confirm action), but must not directly mutate the cache.

### Orval — generated API clients

- Backend API calls use Orval-generated clients exclusively.
- The renderer must not hand-write `fetch` or `axios` calls for business APIs.
- The renderer does not call API clients directly for business actions — it dispatches `UserAction` objects through ActionDispatcher.

### No invented backend endpoints

- The renderer must not assume or call backend endpoints that are not defined by an OpenAPI spec and generated through Orval.
- The renderer must not invent response schemas from frontend assumptions.

## Alternatives considered (cross-cutting)

| Alternative | Why rejected |
|---|---|
| JSON Forms / form-schema rendering | Too opinionated for the card model; assumes all cards are forms. SDUI cards include non-form types (message, operator_handback). |
| Headless CMS-driven rendering | CMS is not in the architecture; adds a new dependency and operational component. |
| Web Components / custom elements | Not in the React 18 baseline; adds interop complexity without benefit. |
| Server-side rendering (Next.js) | Next.js is excluded from the frontend baseline. Vite + React 18 SPA is the current model. |

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Schema version drift between frontend and backend | Cards fail to render or render incorrectly | FallbackRenderer handles version mismatch; schema_version compatibility set is a compile-time constant that is updated when backend schema changes are deployed |
| ComponentRegistry grows unbounded | Bundle size increases; registration becomes hard to audit | Static registration is auditable at build time; code-splitting per card type can be added in future tasks |
| ActionDispatcher becomes a catch-all for business logic | Trust boundary erodes; cards gain indirect backend access | Strict interface contract; code review enforces that dispatcher only forwards structured intent |
| FallbackRenderer leaks sensitive data in diagnostic mode | Security incident | Diagnostic mode is opt-in and development-only; sensitive key filtering is always active |
| Payload contains unexpected fields not covered by schema | Rendering errors or security issues | Renderer validates at boundary; ignores unknown fields; does not pass them through to components |
| Zustand and TanStack Query responsibilities blur | State inconsistency, duplicate data, stale cache | Clear ownership: Zustand for local UI, TanStack Query for server data; no duplication |

## Recommendation

Adopt the architecture defined in this document as the frozen baseline for SDUI Renderer implementation. Key recommendations:

1. **Implement ComponentRegistry as a static map** with explicit registration at bootstrap. No runtime plugin loading.
2. **Implement ActionDispatcher as a thin forwarding layer** that validates action shape and dispatches to Runtime/Capability Gateway. No business logic in the dispatcher.
3. **Implement FallbackRenderer as a safe, non-interactive fallback** with sensitive data filtering and development-mode diagnostics.
4. **Enforce schema_version as a required field** with static compatibility checking and safe rejection of unsupported/malformed payloads.
5. **Enforce user_action as a single structured action** per card interaction. No workflow engine, no direct adapter calls, no credential operations.
6. **Maintain strict state ownership**: Zustand for local UI, TanStack Query for server data, Orval for API clients.
7. **Defer all implementation** to future tasks that explicitly authorize `web/src/sdui/**` code creation.

## Minimal architecture acceptance checks

The following checks must pass before the SDUI Renderer implementation is considered architecturally sound:

1. **ComponentRegistry acceptance**: Registry is a static map; no dynamic imports; unknown types return `undefined` and delegate to FallbackRenderer.
2. **ActionDispatcher acceptance**: Dispatcher forwards structured `UserAction` only; no direct API calls, adapter calls, or business logic execution in the dispatcher.
3. **FallbackRenderer acceptance**: Unknown/unsupported cards render a safe, non-interactive fallback; no sensitive data leakage; logging limited to type + version.
4. **schema_version acceptance**: Every payload carries `schema_version`; unsupported or malformed versions are rejected safely; no auto-migration.
5. **user_action acceptance**: Cards emit single structured actions; no direct adapter calls, credential operations, arbitrary API calls, or multi-step workflows.
6. **Security acceptance**: No `dangerouslySetInnerHTML`; sensitive keys filtered from display; diagnostic mode is development-only.
7. **State boundary acceptance**: Zustand for local UI only; TanStack Query for server data only; no duplication; no invented backend endpoints.
8. **Baseline compliance**: Renderer integrates with React 18 / Vite / TypeScript strict / Ant Design 5.x / ProComponents 2.x / React Router / TanStack Query / Zustand / Orval. No excluded technologies adopted.
9. **No implementation code**: `web/src/sdui/**` does not exist until a future task explicitly authorizes it.

## Deferred work

The following are explicitly deferred to future tasks:

- SDUI renderer implementation (`web/src/sdui/**`)
- Full card type catalog and card schema definitions
- Component registration for specific card types
- ActionDispatcher integration with Runtime/Capability Gateway
- FallbackRenderer implementation and testing
- schema_version compatibility set definition and update process
- Frontend fixtures for SDUI card types (per `docs/phase0/FRONTEND_MOCK_FIXTURES.md`)
- Orval-generated API client integration for SDUI endpoints
- TanStack Query hooks for SDUI data fetching
- Zustand stores for SDUI UI state
- React Router route definitions for SDUI pages
- Vitest and React Testing Library tests for SDUI components
- Accessibility (a11y) audit for SDUI cards
- Internationalization (i18n) for SDUI card content
- Performance optimization (code splitting, lazy loading) for card components
