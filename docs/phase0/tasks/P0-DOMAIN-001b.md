# P0-DOMAIN-001b - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- app/ports/task_store.py (primary contract for this task)
- docs/phase0/tasks/P0-DOMAIN-001a.md
- docs/phase0/task_logs/P0-INFRA-004_*_passed.yaml (check existing schema baseline)
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted TaskStore / SessionStore sections and P0-DOMAIN-001b context; do not paste or rewrite the full spec.

## Global hard rules

- Execute only this task_id.
- Start this task only after all depends_on tasks have been reviewed, approved, and merged to the Phase 0 base branch.
- Output a Plan first and wait for human confirmation before modifying files.
- Do not modify frozen blueprint files.
- Do not implement Phase 1 features.
- Do not add unapproved dependencies.
- Do not weaken tests to pass.
- Stop after Unified Task Record and wait for human confirmation.
- No commit, no push, no merge.
- Any execution/pass claim must include exact command, exit code, and evidence output in the Task Record.
- Independent staged review is required before any commit, push, or merge.

## Task YAML

```yaml
task_id: P0-DOMAIN-001b
branch: "phase0/P0-DOMAIN-001b"
title: Task and Session Minimal Skeleton
type: implementation
depends_on:
  - P0-DOMAIN-001a
  - P0-INFRA-004
priority: P0
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "execution"
  execution_owner: "codex"
  review_owner: "separate_session"
  review_mode: "codex_review"
  method: "TDD"
  reason_for_owner_choice: >
    TaskStore and SessionStore are critical production-path PostgreSQL persistence
    skeletons used by the Gateway short-circuit (003b1) and Runtime execution path.
    Codex owns TDD because the Protocol method semantics, TaskStatus Literal boundaries,
    TaskEventRecord append, and no-Phase-1 boundary must be proven before downstream
    integration. Independent review must happen in a separate Codex review session.

objective: >
  Implement the minimal PostgreSQL-backed TaskStore and SessionStore adapters that
  satisfy TaskStorePort (create_task, get_task, update_status, append_event) and
  SessionStorePort (create_session, get_session), always returning TaskRecord /
  SessionRecord objects where the Protocol requires them. This task is limited to
  persistence under the approved infra persistence path; it must not add search,
  pgvector similarity, Gateway behavior, Runtime behavior, or Phase 1 task logic.

structured_output_baseline_applicability: "not_applicable"
structured_output_baseline_not_applicable:
  reason: "This task implements TaskStore/SessionStore persistence only; it does not implement LLM structured output."
  scope: "app/infra/persistence/task_store/ and tests/infra/persistence/task_store/"
  blocked_by_task_id: "none"
  activation_task_id: "P0-DOMAIN-010b"
  expiry_condition: "Structured-output baseline becomes applicable only in LLM provider or structured-output implementation tasks."
  evidence: "TaskStorePort and SessionStorePort have no LLM provider or structured-output methods."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-001b_<timestamp>_passed.yaml"

constraints_to_carry_forward:
  - "TaskStorePort contract (app/ports/task_store.py) must be satisfied exactly; do not modify app/ports/."
  - "SessionStorePort contract (app/ports/task_store.py) must be satisfied exactly."
  - "All methods must be async: create_task, get_task, update_status, append_event, create_session, get_session."
  - "create_task(record: TaskRecord) returns TaskRecord."
  - "get_task(task_id: str) returns TaskRecord | None; missing task returns None."
  - "update_status(task_id: str, status: TaskStatus, error_code: str | None = None) returns TaskRecord; missing task raises a deterministic not-found error. The error_code parameter sets the TaskRecord.error_code field on the persisted record; it is not the error raised when the task is missing."
  - "append_event(task_id: str, event: TaskEventRecord) returns None; missing task raises a deterministic not-found error."
  - "create_session(record: SessionRecord) returns SessionRecord."
  - "get_session(session_id: str) returns SessionRecord | None; missing session returns None."
  - "TaskStatus Literal values must remain unchanged: created/running/waiting_user/completed/failed/no_capability_found."
  - "TaskRecord fields remain unchanged: task_id, session_id, ai_user_id, status, trace_id, capability_id, error_code."
  - "TaskEventRecord fields remain unchanged: event_id, task_id, event_type, timestamp, payload."
  - "SessionRecord fields remain unchanged: session_id."
  - "No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values may appear in Trace, ResponseEnvelope, fixtures, logs, reports, or Task Record evidence."
  - "Phase 1 user-value boundary is preserved: minimal persistence CRUD only, no full Phase 1 task orchestration."

deliverable:
  - app/infra/persistence/task_store/
  - tests/infra/persistence/task_store/

constraints:
  - Implement only under app/infra/persistence/task_store/ and tests/infra/persistence/task_store/.
  - Both TaskStorePort and SessionStorePort implementations may live in the same directory.
  - Before writing any test or implementation code, inspect the existing INFRA-004 Alembic migration files and database schema to determine whether tasks/sessions tables already exist. If they do not exist and PostgreSQL CRUD cannot be implemented using the approved baseline without adding a new migration, stop immediately and report the schema gap; do not invent an in-memory workaround.
  - Use the existing P0-INFRA-004 PostgreSQL/Alembic database baseline and app.db session helpers where applicable.
  - Do not add new Alembic migrations or modify alembic/versions/.
  - If the existing INFRA-004 schema/database baseline cannot support PostgreSQL-backed TaskStore/SessionStore CRUD without a new migration or forbidden-path edit, stop and report the schema gap instead of inventing a workaround.
  - Do not use an in-memory dict, SQLite, Redis, file storage, or mock-only store as the production implementation.
  - No Phase 1 task orchestration, session management, intent routing, capability routing, LLM invocation, or event processing logic.
  - Do not implement Gateway, Runtime, Context Assembly, Policy, IdentityMapping, Adapter execution, or SDUI behavior.
  - Do not modify app/ports/task_store.py or any other port file.
  - Do not add new Python dependencies.
  - Do not modify pyproject.toml or uv.lock.
  - No __init__.py files in new namespace-package directories.
  - Duplicate create_task for an existing task_id must be handled deterministically and must not silently overwrite the existing record.
  - update_status and append_event for a missing task_id must raise a deterministic task-local not-found error; get_task for a missing task_id must return None.
  - TaskStatus Literal values must be validated on update_status; invalid values must not be persisted.

acceptance_criteria:
  - criterion: "create_task persists a TaskRecord in PostgreSQL, rejects duplicate task_id deterministically, and returns TaskRecord rather than a bare dict"
    result: "pending"
    evidence: ""
  - criterion: "get_task returns TaskRecord for an existing task_id and returns None for a missing task_id"
    result: "pending"
    evidence: ""
  - criterion: "update_status updates TaskStatus and optional error_code for an existing task, validates the TaskStatus Literal, raises the correct missing-task error, and returns TaskRecord"
    result: "pending"
    evidence: ""
  - criterion: "append_event persists a TaskEventRecord for an existing task and raises the correct missing-task error for an unknown task_id; returns None"
    result: "pending"
    evidence: ""
  - criterion: "create_session persists a SessionRecord in PostgreSQL and returns SessionRecord rather than a bare dict"
    result: "pending"
    evidence: ""
  - criterion: "get_session returns SessionRecord for an existing session_id and returns None for a missing session_id"
    result: "pending"
    evidence: ""

failure_examples:
  - name: task_not_found_on_update
    trigger: "update_status or append_event is called with an unknown task_id"
    expected_result: "A deterministic task-local not-found error is raised; get_task with the same unknown task_id returns None."
    forbidden_shortcut: "Do not create a placeholder TaskRecord or silently ignore the missing task."
  - name: duplicate_task_registration
    trigger: "create_task is called twice with the same task_id"
    expected_result: "The second create_task is rejected deterministically and the original persisted TaskRecord remains unchanged."
    forbidden_shortcut: "Do not silently overwrite, upsert, or merge records unless the Protocol later adds that behavior."
  - name: store_returns_bare_dict
    trigger: "create_task, get_task, update_status, create_session, or get_session returns raw database rows or dict payloads"
    expected_result: "All Protocol return values are TaskRecord / SessionRecord objects."
    forbidden_shortcut: "Do not rely on callers to wrap dicts into Pydantic models."
  - name: invalid_task_status_persisted
    trigger: "update_status is called with a string value not in TaskStatus Literal ('created','running','waiting_user','completed','failed','no_capability_found')"
    expected_result: "The invalid status is rejected before persistence; an appropriate validation error is raised."
    forbidden_shortcut: "Do not widen TaskStatus or store arbitrary strings in the status column."

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify depends_on passed Task Records exist"
    result: "pending"
    command: "$missing = @(); if (-not (Get-ChildItem docs/phase0/task_logs/P0-DOMAIN-001a_*_passed.yaml -ErrorAction SilentlyContinue | Select-Object -First 1)) { $missing += 'P0-DOMAIN-001a' }; if (-not (Get-ChildItem docs/phase0/task_logs/P0-INFRA-004_*_passed.yaml -ErrorAction SilentlyContinue | Select-Object -First 1)) { $missing += 'P0-INFRA-004' }; if ($missing.Count -gt 0) { throw \"Missing depends_on Task Record(s): $($missing -join ', ')\" } else { 'PASSED' }"
    evidence: ""
  - step: "Inspect existing INFRA-004 Alembic/database baseline before choosing PostgreSQL table approach"
    result: "pending"
    command: "$hits = Get-ChildItem alembic/versions -Filter '*.py' -ErrorAction Stop | Select-String -Pattern 'task','session','create_table' -List; if ($hits) { $hits | ForEach-Object { $_.Path } } else { 'No task/session Alembic table found; stop if PostgreSQL CRUD cannot be implemented using existing approved database baseline without adding a migration.' }"
    evidence: ""
  - step: "Create persistence tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/infra/persistence/task_store/ -v"
    evidence: "Expected non-zero exit before implementation exists."
  - step: "Implement PostgreSQL TaskStore and SessionStore"
    result: "pending"
    command: "Test-Path app/infra/persistence/task_store/"
    evidence: ""
  - step: "Run task/session persistence tests and port contract regression tests (TDD green phase)"
    result: "pending"
    command: "if (Test-Path 'tests/ports/test_task_store_port.py') { uv run pytest tests/infra/persistence/task_store/ tests/ports/test_task_store_port.py -v } else { Write-Warning 'tests/ports/test_task_store_port.py not found — running infra tests only'; uv run pytest tests/infra/persistence/task_store/ -v }"
    evidence: ""
  - step: "Run lint and type checks"
    result: "pending"
    command: "uv run ruff check app/infra/persistence/task_store/ tests/infra/persistence/task_store/; uv run mypy app/infra/persistence/task_store/"
    evidence: ""
  - step: "Verify no __init__.py files were created in new namespace-package directories"
    result: "pending"
    command: "$paths = @('app/infra/persistence/task_store','tests/infra/persistence/task_store'); $hits = foreach ($path in $paths) { if (Test-Path $path) { Get-ChildItem $path -Filter '__init__.py' -Recurse -ErrorAction SilentlyContinue } }; if ($hits) { $hits | ForEach-Object { $_.FullName }; throw '__init__.py detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify no new Alembic migration is staged"
    result: "pending"
    command: "$hits = git diff --cached --name-only | Select-String -Pattern '^alembic/versions/'; if ($hits) { $hits | ForEach-Object { $_.Line }; throw 'Alembic migration change detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify staged diff has no plaintext credential values"
    result: "pending"
    command: "$secretPattern = '(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|authorization|bearer|cookie|session[_-]?id)\\s*[:=]\\s*[\"'']?[^\"''<\\s]{6,}'; $hits = git diff --cached -U0 | Select-String -Pattern $secretPattern; if ($hits) { 'SECRET SCAN FAIL:'; $hits | ForEach-Object { $_.Line } } else { 'SECRET SCAN: no hits' }"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "$forbidden = @('app/ports/','app/runtime/','app/gateway/','app/control_plane/','app/api/','app/execution_fabric/','pyproject.toml','uv.lock','alembic/versions/'); $changed = git diff --cached --name-only; $hits = foreach ($path in $changed) { foreach ($prefix in $forbidden) { if ($path -like \"$prefix*\") { $path } } }; if ($hits) { $hits; throw 'Forbidden path staged' } else { 'PASSED' }"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/infra/persistence/task_store/ tests/ports/test_task_store_port.py -v"
  - "uv run ruff check app/infra/persistence/task_store/ tests/infra/persistence/task_store/"
  - "uv run mypy app/infra/persistence/task_store/"

touched_paths:
  - app/infra/persistence/task_store/
  - tests/infra/persistence/task_store/

forbidden_paths:
  - app/ports/
  - app/runtime/
  - app/gateway/
  - app/control_plane/
  - app/api/
  - app/execution_fabric/
  - pyproject.toml
  - uv.lock
  - alembic/versions/

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-001b"
  - "Working tree is dirty at task start"
  - "P0-DOMAIN-001a passed Task Record is missing"
  - "P0-INFRA-004 passed Task Record is missing"
  - "Existing INFRA-004 schema/database baseline cannot support PostgreSQL-backed TaskStore/SessionStore CRUD without a new Alembic migration or forbidden-path edit"
  - "Any forbidden path is modified"
  - "app/ports/task_store.py or any other port file would need to change"
  - "An in-memory dict, SQLite, Redis, file store, or mock-only store is introduced as the production implementation"
  - "create_task silently overwrites an existing task_id"
  - "Protocol return values are bare dicts or raw database rows instead of TaskRecord / SessionRecord objects"
  - "TaskStatus Literal values are widened or arbitrary strings are accepted"
  - "New Python dependency added"
  - "__init__.py file created in new namespace-package directories"
  - "DATABASE_URL or PostgreSQL validation is unavailable and the executor cannot produce honest persistence evidence"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
```

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm these carried-forward constraints:

1. TaskStorePort (app/ports/task_store.py) is satisfied exactly: async create_task/get_task/update_status/append_event with the existing signatures and return types.
2. SessionStorePort (app/ports/task_store.py) is satisfied exactly: async create_session/get_session with the existing signatures and return types.
3. TaskRecord and SessionRecord remain the shared model boundary; the implementation returns Pydantic model objects, never bare dicts or raw database rows.
4. TaskStatus Literal values remain unchanged: created/running/waiting_user/completed/failed/no_capability_found.
5. TaskEventRecord fields remain unchanged: event_id, task_id, event_type, timestamp, payload.
6. PostgreSQL persistence uses the existing P0-INFRA-004 database baseline; no new Alembic migration is created. If the existing schema cannot support the implementation, stop and report the schema gap.
7. No Phase 1 task orchestration, session management, intent routing, capability routing, LLM invocation, Gateway behavior, Runtime behavior, Context Assembly, Policy, IdentityMapping, Adapter execution, or SDUI behavior is implemented.
8. Structured-output Plan B remains unchanged and this task does not reopen instructor or PydanticAI decisions.
9. No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values appear in fixtures, logs, reports, or Task Record evidence.
10. No new Python dependencies. No pyproject.toml or uv.lock changes. No __init__.py files in new namespace-package directories.

## Structured-output baseline applicability

not_applicable

- reason: This task implements TaskStore/SessionStore PostgreSQL persistence only; it does not implement LLM structured output.
- scope: app/infra/persistence/task_store/ and tests/infra/persistence/task_store/.
- blocked_by_task_id: none.
- activation_task_id: P0-DOMAIN-010b.
- expiry_condition: Structured-output baseline becomes applicable only for LLM provider or structured-output implementation tasks.
- evidence: TaskStorePort and SessionStorePort have no LLM provider or structured-output methods.
