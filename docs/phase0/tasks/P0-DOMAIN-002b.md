# P0-DOMAIN-002b - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- app/ports/capability_registry.py (primary contract for this task)
- docs/phase0/tasks/P0-DOMAIN-002a.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted CapabilitySpec / CapabilityRegistryPort sections and P0-DOMAIN-002b context; do not paste or rewrite the full spec.

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
task_id: P0-DOMAIN-002b
branch: "phase0/P0-DOMAIN-002b"
title: Capability Model and Registry Minimal CRUD
type: implementation
depends_on:
  - P0-DOMAIN-002a
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
    CapabilityRegistry is a production-path PostgreSQL persistence skeleton used
    by later Gateway short-circuit and Runtime tasks. Codex owns TDD because the
    Protocol method semantics, CapabilitySpec validation, duplicate handling,
    and no-Phase-1 boundary must be proven before downstream integration.
    Independent review must happen in a separate Codex review session.

objective: >
  Implement the minimal PostgreSQL-backed CapabilityRegistry CRUD adapter that
  satisfies CapabilityRegistryPort.create/get/list/update/disable and always
  returns CapabilitySpec objects where the Protocol requires them. This task is
  limited to registry persistence under the approved infra persistence path; it
  must not add search, pgvector similarity, Gateway behavior, Runtime behavior,
  or Phase 1 capability logic.

structured_output_baseline_applicability: "not_applicable"
structured_output_baseline_not_applicable:
  reason: "This task implements CapabilityRegistry persistence only; it does not implement LLM structured output."
  scope: "app/infra/persistence/capability_registry/ and tests/infra/persistence/capability_registry/"
  blocked_by_task_id: "none"
  activation_task_id: "P0-DOMAIN-010b"
  expiry_condition: "Structured-output baseline becomes applicable only in LLM provider or structured-output implementation tasks."
  evidence: "CapabilityRegistryPort has no LLM provider or structured-output method."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-002b_<timestamp>_passed.yaml"

constraints_to_carry_forward:
  - "CapabilityRegistryPort contract (app/ports/capability_registry.py) must be satisfied exactly; do not modify app/ports/."
  - "All registry methods must be async: create, get, list, update, disable."
  - "create(capability: CapabilitySpec) returns CapabilitySpec."
  - "get(capability_id: str) returns CapabilitySpec | None; missing capability returns None."
  - "list(target_system: str | None = None, type: str | None = None, status: str | None = None) returns list[CapabilitySpec]."
  - "update(capability_id: str, patch: dict[str, Any]) returns CapabilitySpec and must validate the patched model through CapabilitySpec."
  - "disable(capability_id: str) returns CapabilitySpec and represents the delete/deactivate operation by setting status='disabled'."
  - "CapabilitySpec fields and Literal boundaries remain unchanged: type=query/action/workflow/mock; risk_level=low/medium/high; status=draft/active/disabled/deprecated; target_system=oa/u8/hikvision_ivms or None; execution_identity=user_delegated/system_scope/admin_approved_proxy."
  - "Context Assembly minimum input boundary is preserved; do not implement context assembly."
  - "Capability Summary injection rules are preserved; do not implement summary injection."
  - "Intent to Capability minimum validation path is preserved; do not implement intent routing or gateway selection."
  - "Structured-output failure Plan B remains unchanged: raw OpenAI-compatible SDK, response_format={\"type\":\"json_object\"}, Pydantic model_validate, and Literal enum validation."
  - "no_capability_found, clarification_needed, validation_failed, and manual_review_needed downstream states/outcomes must not be redefined."
  - "No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values may appear in Trace, ResponseEnvelope, fixtures, logs, reports, or Task Record evidence."
  - "Phase 1 user-value boundary is preserved: minimal registry CRUD only, no full Phase 1 workflow."

deliverable:
  - alembic/versions/<rev>_capability_schema.py
  - app/infra/persistence/capability_registry/
  - tests/infra/persistence/capability_registry/

constraints:
  - Implement only under app/infra/persistence/capability_registry/ and tests/infra/persistence/capability_registry/.
  - Before writing any test or implementation code, inspect the existing INFRA-004 Alembic migration files and confirm that a capabilities table does not yet exist. The INFRA-004 baseline contains only a pgvector extension migration; this is the expected state. Create exactly one hand-written Alembic migration (no autogenerate comment) under alembic/versions/ that adds the capabilities table; this migration is the only permitted alembic/versions/ change for this task.
  - Use the existing P0-INFRA-004 PostgreSQL/Alembic database baseline and app.db session helpers where applicable.
  - Do not use an in-memory dict, SQLite, Redis, file storage, or mock-only store as the production registry implementation.
  - No Phase 1 search, no pgvector similarity, no embeddings, no semantic ranking, no capability recommendation, and no full capability discovery.
  - Do not implement Gateway, Runtime, Context Assembly, Intent routing, Policy, IdentityMapping, Adapter execution, or SDUI behavior.
  - Do not modify app/ports/capability_registry.py or any other port file.
  - Do not add new Python dependencies.
  - Do not modify pyproject.toml or uv.lock.
  - No __init__.py files in new namespace-package directories.
  - Duplicate create for an existing capability_id must be handled deterministically and must not silently overwrite the existing record.
  - update and disable for a missing capability_id must raise a deterministic task-local not-found error or equivalent existing persistence error; get for a missing capability_id must return None.
  - update must reject patch keys that do not exist in CapabilitySpec (unknown fields rejected via Pydantic validation) AND reject Literal values that violate the frozen boundaries for type, risk_level, status, target_system, and execution_identity; these are two distinct rejection cases, not a single check.
  - list filters are exact filters for target_system, type, and status only.
  - Tests for async methods must use asyncio.run() in synchronous test functions; pytest-asyncio is not installed and cannot be added as a new dependency.

acceptance_criteria:
  - criterion: "create persists a CapabilitySpec in PostgreSQL, rejects duplicate capability_id deterministically, and returns CapabilitySpec rather than a bare dict"
    result: "pending"
    evidence: ""
  - criterion: "get returns CapabilitySpec for an existing capability_id and returns None for a missing capability_id"
    result: "pending"
    evidence: ""
  - criterion: "list returns list[CapabilitySpec] and supports exact filters by target_system, type, and status without Phase 1 search or vector similarity"
    result: "pending"
    evidence: ""
  - criterion: "update applies a validated patch to an existing capability, rejects invalid or unknown fields, raises the correct missing-capability error, and returns CapabilitySpec"
    result: "pending"
    evidence: ""
  - criterion: "disable sets status='disabled' for an existing capability, raises the correct missing-capability error, and returns CapabilitySpec"
    result: "pending"
    evidence: ""

failure_examples:
  - name: capability_not_found
    trigger: "update or disable is called with an unknown capability_id"
    expected_result: "A deterministic task-local not-found error or equivalent existing persistence error is raised; get with the same unknown capability_id returns None."
    forbidden_shortcut: "Do not create a placeholder CapabilitySpec or return an empty dict to hide the missing row."
  - name: duplicate_capability_registration
    trigger: "create is called twice with the same capability_id"
    expected_result: "The second create is rejected deterministically and the original persisted CapabilitySpec remains unchanged."
    forbidden_shortcut: "Do not silently overwrite, upsert, or merge records unless the Protocol later adds that behavior."
  - name: registry_returns_bare_dict
    trigger: "create, get, list, update, or disable returns raw database rows or dict payloads"
    expected_result: "All Protocol return values are CapabilitySpec objects, and list returns list[CapabilitySpec]."
    forbidden_shortcut: "Do not rely on callers to wrap dicts into CapabilitySpec."
  - name: phase1_similarity_search_added
    trigger: "list or get adds vector, embedding, nearest-neighbor, fuzzy search, ranking, or recommendation behavior"
    expected_result: "Review fails; P0-DOMAIN-002b only implements exact CRUD/filter behavior."
    forbidden_shortcut: "Do not use pgvector or semantic search to make registry lookup appear smarter."

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify depends_on passed Task Records exist"
    result: "pending"
    command: "$missing = @(); if (-not (Get-ChildItem docs/phase0/task_logs/P0-DOMAIN-002a_*_passed.yaml -ErrorAction SilentlyContinue | Select-Object -First 1)) { $missing += 'P0-DOMAIN-002a' }; if (-not (Get-ChildItem docs/phase0/task_logs/P0-INFRA-004_*_passed.yaml -ErrorAction SilentlyContinue | Select-Object -First 1)) { $missing += 'P0-INFRA-004' }; if ($missing.Count -gt 0) { throw \"Missing depends_on Task Record(s): $($missing -join ', ')\" } else { 'PASSED' }"
    evidence: ""
  - step: "Inspect existing INFRA-004 Alembic/database baseline — confirm no capabilities table exists"
    result: "pending"
    command: "$hits = Get-ChildItem alembic/versions -Filter '*.py' -ErrorAction Stop | Select-String -Pattern 'capabil','create_table' -List; if ($hits) { $hits | ForEach-Object { $_.Path }; 'WARNING: unexpected capabilities table already present — verify before proceeding' } else { 'CONFIRMED: no capabilities table in INFRA-004 baseline; one hand-written migration will be created as part of this task.' }"
    evidence: ""
  - step: "Create persistence tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/infra/persistence/capability_registry/ -v"
    evidence: "Expected non-zero exit before implementation exists."
  - step: "Create hand-written Alembic migration for capabilities table and run upgrade head"
    result: "pending"
    command: "Test-Path alembic/versions/*capabilit*"
    evidence: "Migration file must exist and contain no 'auto generated by Alembic' comment; uv run alembic upgrade head must exit 0."
  - step: "Implement PostgreSQL CapabilityRegistry"
    result: "pending"
    command: "Test-Path app/infra/persistence/capability_registry/"
    evidence: ""
  - step: "Run registry persistence tests and port contract regression tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/infra/persistence/capability_registry/ tests/ports/test_capability_registry_port.py -v"
    evidence: ""
  - step: "Run lint and type checks"
    result: "pending"
    command: "uv run ruff check app/infra/persistence/capability_registry/ tests/infra/persistence/capability_registry/; uv run mypy app/infra/persistence/capability_registry/"
    evidence: ""
  - step: "Verify no __init__.py files were created in new namespace-package directories"
    result: "pending"
    command: "$paths = @('app/infra/persistence/capability_registry','tests/infra/persistence/capability_registry'); $hits = foreach ($path in $paths) { if (Test-Path $path) { Get-ChildItem $path -Filter '__init__.py' -Recurse -ErrorAction SilentlyContinue } }; if ($hits) { $hits | ForEach-Object { $_.FullName }; throw '__init__.py detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify no pgvector or similarity search logic was added"
    result: "pending"
    command: "$paths = @('app/infra/persistence/capability_registry','tests/infra/persistence/capability_registry'); $files = foreach ($path in $paths) { if (Test-Path $path) { Get-ChildItem $path -Filter '*.py' -Recurse -ErrorAction SilentlyContinue } }; $hits = $files | Select-String -Pattern 'pgvector','embedding','similarity','cosine','nearest','vector' -ErrorAction SilentlyContinue; if ($hits) { $hits | ForEach-Object { $_.Path + ':' + $_.LineNumber + ': ' + $_.Line }; throw 'Phase 1 vector/similarity logic detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify exactly one hand-written capabilities Alembic migration is staged"
    result: "pending"
    command: "$staged = git diff --cached --name-only | Select-String -Pattern '^alembic/versions/'; $count = ($staged | Measure-Object).Count; if ($count -ne 1) { \"FAIL: expected 1 alembic/versions/ file staged, found $count\"; throw 'Migration count mismatch' } else { $file = $staged.Line; $content = Get-Content $file -Raw; if ($content -match 'auto generated by Alembic') { throw 'Autogenerate comment detected in migration file' } else { \"PASSED: $file staged and is hand-written\" } }"
    evidence: ""
  - step: "Verify staged diff has no plaintext credential values"
    result: "pending"
    command: "$secretPattern = '(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|authorization|bearer|cookie|session[_-]?id)\\s*[:=]\\s*[\"'']?[^\"''<\\s]{6,}'; $hits = git diff --cached -U0 | Select-String -Pattern $secretPattern; if ($hits) { 'SECRET SCAN FAIL:'; $hits | ForEach-Object { $_.Line } } else { 'SECRET SCAN: no hits' }"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "$forbidden = @('app/ports/','app/runtime/','app/gateway/','app/control_plane/','app/api/','app/execution_fabric/','pyproject.toml','uv.lock'); $changed = git diff --cached --name-only; $hits = foreach ($path in $changed) { foreach ($prefix in $forbidden) { if ($path -like \"$prefix*\") { $path } } }; if ($hits) { $hits; throw 'Forbidden path staged' } else { 'PASSED' }"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/infra/persistence/capability_registry/ tests/ports/test_capability_registry_port.py -v"
  - "uv run ruff check app/infra/persistence/capability_registry/ tests/infra/persistence/capability_registry/"
  - "uv run mypy app/infra/persistence/capability_registry/"

touched_paths:
  - alembic/versions/
  - app/infra/persistence/capability_registry/
  - tests/infra/persistence/capability_registry/

forbidden_paths:
  - app/ports/
  - app/runtime/
  - app/gateway/
  - app/control_plane/
  - app/api/
  - app/execution_fabric/
  - pyproject.toml
  - uv.lock

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-002b"
  - "Working tree is dirty at task start"
  - "P0-DOMAIN-002a passed Task Record is missing"
  - "P0-INFRA-004 passed Task Record is missing"
  - "More than one alembic/versions/ file is staged, or any alembic/versions/ file other than the capabilities schema migration is modified"
  - "Any forbidden path is modified"
  - "app/ports/capability_registry.py or any other port file would need to change"
  - "An in-memory dict, SQLite, Redis, file store, or mock-only registry is introduced as the production implementation"
  - "create silently overwrites an existing capability_id"
  - "Protocol return values are bare dicts or raw database rows instead of CapabilitySpec objects"
  - "Phase 1 vector/similarity/search/recommendation logic is introduced"
  - "New Python dependency added"
  - "__init__.py file created in new namespace-package directories"
  - "DATABASE_URL or PostgreSQL validation is unavailable and the executor cannot produce honest persistence evidence"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
```

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm these carried-forward constraints:

1. CapabilityRegistryPort (app/ports/capability_registry.py) is satisfied exactly: async create/get/list/update/disable with the existing signatures and return types.
2. CapabilitySpec remains the shared model boundary; the implementation returns CapabilitySpec objects, never bare dicts or raw database rows.
3. CapabilitySpec Literal boundaries remain unchanged for type, risk_level, status, target_system, and execution_identity.
4. Minimal CRUD means create, read by capability_id, exact list filters, validated patch update, and disable-as-deactivate with status='disabled'; no hard delete is added because the Protocol has no delete method.
5. PostgreSQL persistence uses the existing P0-INFRA-004 database baseline; exactly one hand-written Alembic migration (no autogenerate comment) is created for the capabilities table. No other alembic/versions/ files are added or modified.
6. No Phase 1 search, pgvector similarity, embeddings, semantic ranking, recommendation, Gateway behavior, Runtime behavior, Context Assembly, or Intent routing is implemented.
7. Context Assembly minimum input boundary, Capability Summary injection rules, Intent to Capability validation path, and downstream no_capability_found / clarification_needed / validation_failed / manual_review_needed states remain unchanged.
8. Structured-output Plan B remains unchanged and this task does not reopen instructor or PydanticAI decisions.
9. No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values appear in fixtures, logs, reports, or Task Record evidence.
10. No new Python dependencies. No pyproject.toml or uv.lock changes. No __init__.py files in new namespace-package directories.

## Structured-output baseline applicability

not_applicable

- reason: This task implements CapabilityRegistry PostgreSQL CRUD only; it does not implement LLM structured output.
- scope: app/infra/persistence/capability_registry/ and tests/infra/persistence/capability_registry/.
- blocked_by_task_id: none.
- activation_task_id: P0-DOMAIN-010b.
- expiry_condition: Structured-output baseline becomes applicable only for LLM provider or structured-output implementation tasks.
- evidence: CapabilityRegistryPort has no LLM provider or structured-output method.
