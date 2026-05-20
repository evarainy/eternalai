# Frontend AI Coding Guidelines

This document constrains AI-generated frontend code for Phase 0 and Phase 1. It is a coding-guideline document, not a dependency authorization document.

Dependency permission remains governed by `docs/dev/dependency_policy.md` and the current task prompt. If this guide conflicts with a task prompt, dependency policy, or explicit Phase 0 rule, stop and ask for review rather than improvising.

## Current Baseline

The intended Phase 1 frontend baseline is:

- React 18
- Vite
- TypeScript strict
- Ant Design 5.x
- ProComponents 2.x
- React Router
- TanStack Query
- Zustand
- Orval

Do not invent exact package versions unless they are already authorized by `docs/dev/dependency_policy.md` or the current task prompt.

## Non-Baseline Technologies

Ant Design X, Ant Design 6, React 19, Next.js, Tailwind, and shadcn/ui are future candidates only, not current Phase 1 baseline technologies.

Do not install, import, scaffold, or use these technologies in examples. Adoption requires a separate approved task and a dependency-policy update.

## Lean Frontend Rules

- Keep the P0-INFRA-003 frontend skeleton minimal.
- Do not implement a full Admin Console in the skeleton task.
- Do not implement chat UI unless a later task explicitly asks for it.
- Do not implement a full SDUI renderer in the skeleton task.
- Treat the SDUI renderer boundary as deferred to P0-FE-ARCH-001.
- Do not invent backend endpoints, workflows, business pages, or domain behavior from frontend assumptions.
- Do not add abstractions before the current task proves they are needed.
- Prefer boring, readable, conventional React code over clever abstractions.

## Ant Design And ProComponents

- Prefer Ant Design 5 and ProComponents 2 composition before custom UI abstractions.
- Use AntD layout, form, table, feedback, typography, theme token, `ConfigProvider`, and `App` conventions where appropriate.
- Use `ProLayout`, `ProTable`, and `ProForm` for admin-console patterns only when the task actually needs those patterns.
- Do not replace standard AntD or ProComponents behavior with custom grids, forms, or tables without task evidence.
- Do not create a custom design system in Phase 0 unless a task explicitly requires it.

## Styling

- Prefer AntD tokens and CSS Modules.
- Avoid repeated arbitrary inline styles.
- Use inline style only for narrow dynamic values or required AntD API escape hatches.
- Do not introduce Tailwind, CSS-in-JS frameworks, or new styling systems unless a later approved task authorizes them.
- Keep global CSS minimal and framework-level.

## API And State

- Use Orval-generated clients for backend business APIs when OpenAPI/client generation is available.
- Do not hand-edit generated clients.
- Do not write ad-hoc business `fetch` or `axios` clients unless a task explicitly authorizes it.
- Let TanStack Query own server/cache state.
- Let Zustand own local UI/session state only.
- Do not duplicate server data in Zustand.
- Do not invent backend endpoints or response schemas from frontend assumptions.

## Routing

- Use React Router.
- Do not introduce Next.js or alternate routing frameworks.
- Keep route definitions small and explicit during Phase 0.
- Do not create a large route or plugin system before task evidence exists.

## Testing

When Vitest and React Testing Library are authorized by the current task prompt and dependency policy, use them for frontend component and route behavior tests.

- Prefer meaningful component and route behavior assertions.
- Avoid broad snapshots as primary evidence.
- Avoid weak tests where "renders without crashing" is the only assertion.
- Use provider wrappers only when the component or route actually needs them.

## Dependency And Deployment Boundary

- Phase 0 internet-connected local development/build may use public npm registry only when explicitly allowed by task prompt and dependency policy.
- Public npm registry does not prove enterprise mirror/offline-cache compliance.
- Intranet runtime uses prebuilt Docker images and must not require npm/pnpm registry access.
- Future intranet source build, intranet CI, or stricter supply-chain governance must revisit enterprise npm mirror/offline cache or another approved dependency strategy.

## AI Agent Rules

- Read the relevant task prompt before changing frontend files.
- Keep changes within the task's allowed paths.
- Do not expand scope based on non-blocking review notes.
- Do not add dependencies casually.
- Do not change the baseline stack casually.
- Do not implement business features not requested by the task.
- Prefer minimal diffs and explicit evidence.
