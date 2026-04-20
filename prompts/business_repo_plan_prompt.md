You are executing **Phase 0 / 0.5: spec-first planning for a real business repository**.

Before doing any work, inspect and follow, when present:
1. `AGENTS.md`
2. `planning/PLANS.md`
3. `prompts/`
4. `templates/`
5. `schemas/`
6. repository source code, configs, manifests, scripts, CI files, tests, docs, examples, migrations, infra files

## Mission

Your goal is **not** to maintain the planning pack itself.
Your goal is to read the **actual business repository** and produce a detailed, machine-readable, spec-first planning package for later controlled execution.

Do **not** start broad implementation.
Do **not** pretend unknowns are solved.
Do **not** mark execution-ready unless the repository evidence truly supports it.

---

## Required output directories and files

Create or update these files:

### spec/
- `spec/product_spec.md`
- `spec/product_spec.yaml`
- `spec/module_spec.yaml`
- `spec/resource_spec.yaml`
- `spec/invariant_spec.yaml`

### contracts/
- `contracts/README.md`
- `contracts/*.yaml` when APIs, events, queues, RPC, or data interchange exist or are implied

### planning/
- `planning/dev_plan.yaml`
- `planning/acceptance_spec.yaml`
- `planning/run_policy.yaml`
- `planning/implement.md`
- `planning/documentation.md`
- `planning/plan_review_checklist.md`
- `planning/phase0_result.json`

---

## Required working order

1. Inspect repository evidence deeply
2. Identify product goals and current system boundaries from actual code/config/docs
3. Write `spec/product_spec.md`
4. Write `spec/product_spec.yaml`
5. Write `spec/module_spec.yaml`
6. Write `spec/resource_spec.yaml`
7. Write `spec/invariant_spec.yaml`
8. Write `contracts/*.yaml` if applicable
9. Write `planning/dev_plan.yaml`
10. Write `planning/acceptance_spec.yaml`
11. Write `planning/run_policy.yaml`
12. Write `planning/implement.md`
13. Write `planning/documentation.md`
14. Write `planning/plan_review_checklist.md`
15. Validate internal consistency
16. Write `planning/phase0_result.json` last

---

## Hard requirements

### 1) Work from real repo evidence
Base every important planning decision on actual repository evidence:
- code layout
- framework/toolchain files
- package manifests
- build/test scripts
- CI config
- env examples
- DB migrations
- API routes
- docs and examples

In `planning/documentation.md`, explicitly record the evidence source for each major conclusion.

If the repo lacks a business subsystem, say so directly.

### 2) Tasks must be very fine-grained
Do not produce vague tasks like:
- implement auth
- build frontend
- finish API

Instead split by layer and bounded responsibility.
Examples:
- define auth contract response schema
- implement POST /login backend success path
- implement POST /login invalid password path
- add frontend form validation for login page
- persist session token
- add auth middleware current-user endpoint check
- add E2E happy-path login verification
- add E2E invalid-password verification

Each task in `planning/dev_plan.yaml` must include:
- `id`
- `title`
- `layer`
- `goal`
- `deps`
- `touched_paths`
- `forbidden_paths`
- `acceptance_ids`
- `parallel_safe`
- `needs_db`
- `needs_ports`
- `shared_resources_touched`
- `verification_commands`
- `evidence_paths`
- `human_gate`
- `notes`

Allowed `layer` values:
- `spec`
- `contract`
- `backend`
- `frontend`
- `integration`
- `e2e`
- `docs`
- `ops`

### 3) Acceptance must be automation-grade
Never write acceptance like:
- login works
- UI looks good
- API is correct

Write structured, testable assertions.
For example:
- request: `POST /api/login`
- input: valid username/password
- expected status: `200`
- expected JSON path `$.code == 0`
- expected JSON path `$.data.accessToken exists`
- expected JSON path `$.data.user.id exists`
- invalid password status `401`
- `$.data.accessToken` absent
- evidence path `artifacts/evidence/login_success_response.json`

For each acceptance item, include at least:
- `id`
- `title`
- `type`
- `trigger`
- `expect`
- `evidence`

And when applicable also:
- `endpoint`
- `given`
- `precondition`
- `postcondition`
- `invariants_preserved`

### 4) Spec-first means layered specifications
You must not stop at one prose spec.
Produce these layers:

#### `spec/product_spec.md`
Human-readable business/product intent.
Must include:
- Goals
- In scope
- Non-goals
- Constraints
- Assumptions
- Dependencies
- Risks
- Open questions
- Current repository evidence

#### `spec/product_spec.yaml`
Machine-readable high-level scope and execution framing.
Must include:
- `system_name`
- `goals`
- `in_scope`
- `non_goals`
- `constraints`
- `assumptions`
- `dependencies`
- `risks`
- `open_questions`
- `human_gates`

#### `spec/module_spec.yaml`
Describe modules/subsystems with boundaries and responsibilities.
Each module should include:
- `id`
- `name`
- `purpose`
- `owned_paths`
- `public_interfaces`
- `dependencies`
- `risks`

#### `spec/resource_spec.yaml`
Capture shared resources and contention points:
- ports
- databases
- migrations
- caches
- queues
- background jobs
- external APIs
- secrets/config domains

#### `spec/invariant_spec.yaml`
Capture rules that later implementation must not break.
Examples:
- auth-required routes must reject unauthenticated requests
- DB migrations may not auto-run in parallel workers
- root config files may not be mutated by feature tasks without explicit gate
- response envelope format remains stable

### 5) Use readiness levels honestly
In `planning/phase0_result.json`, do not collapse all readiness into a single optimistic flag.
Include both:
- `planning_ready`
- `execution_ready`

Set `execution_ready: false` when key blockers remain, such as:
- target repo unclear
- no business source found
- major contracts unknown
- env/test commands unverified
- unresolved open questions block safe implementation

### 6) Implement contract must be thick, not thin
`planning/implement.md` must define a structured per-task result with at least:
- `task_id`
- `attempt`
- `layer`
- `status`
- `acceptance_ids_planned`
- `acceptance_ids_passed`
- `acceptance_ids_failed`
- `changed_files`
- `tests_run`
- `verification_commands`
- `evidence_paths`
- `invariants_preserved`
- `blocked_by`
- `needs_human`
- `error_code`
- `next_action`

### 7) Planning validation must anticipate scheduler failure
In `planning/plan_review_checklist.md`, include checks for:
- duplicate task IDs
- duplicate acceptance IDs
- unresolved dependency IDs
- DAG cycle risk
- tasks too broad
- touched-path overlap risk
- shared resource collision risk
- acceptance items lacking evidence
- contracts missing for cross-boundary interfaces
- invariants missing for critical flows

### 8) Keep unknowns explicit
If something cannot be determined from the repository, record it in:
- `planning/documentation.md`
- `spec/product_spec.yaml -> open_questions`
- `planning/phase0_result.json -> blockers` or `risks`

Never silently invent commands, endpoints, modules, or flows.

---

## Output quality bar

Your task list and review checklist must be **extremely detailed**.
They should be specific enough that:
- a scheduler can use the task graph directly
- a human reviewer can audit the plan without guessing
- a later Codex execution run can consume the files with minimal reinterpretation

---

## Final constraint

Do not start Phase 1 implementation.
Your final action must be writing `planning/phase0_result.json` truthfully.
