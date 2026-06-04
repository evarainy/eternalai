# P0-DOMAIN-006b - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- app/ports/identity_mapping.py (primary IdentityMappingPort contract; read before writing implementation code)
- docs/phase0/tasks/P0-DOMAIN-006a.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for the targeted IdentityMapping / P0-DOMAIN-006b section; do not paste or rewrite the full spec.

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
- No real external identity system calls, including LDAP, SSO, Active Directory, OAuth, IdP, browser session, or network credential lookup calls.

## Task YAML

```yaml
task_id: P0-DOMAIN-006b
branch: "phase0/P0-DOMAIN-006b"
title: IdentityMapping Mock Table and Precheck Skeleton
type: implementation
depends_on:
  - P0-DOMAIN-006a
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
    IdentityMapping controls execution identity resolution before capability
    execution. Codex owns TDD because the implementation must satisfy the
    IdentityMappingPort Protocol exactly, reject unresolved identities
    predictably, and prove that Phase 0 uses only mock identity data without
    real LDAP, SSO, Active Directory, OAuth, or credential-provider calls.

objective: >
  Implement a minimal Phase 0 mock IdentityMapping using an in-memory mapping
  table. PostgreSQL-backed IdentityMapping is deferred to Phase 1.
  The implementation must satisfy app/ports/identity_mapping.py,
  provide a precheck convenience method on the concrete class that validates
  whether an identity can be resolved before capability execution, and avoid all
  real LDAP/SSO/OAuth/Active Directory integration.

structured_output_baseline_applicability: "not_applicable"

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-006b_<timestamp>_passed.yaml"

constraints_to_carry_forward:
  - "IdentityMappingPort contract (app/ports/identity_mapping.py) must be satisfied exactly; do not modify app/ports/."
  - "Implement async resolve_execution_identity, get_mapping, and list_mappings according to the Protocol."
  - "Return IdentityCheckResult from resolve/lookups where required by the Protocol; do not return bare dict and do not invent a separate IdentityResult model."
  - "get_mapping may return IdentityCheckResult or None according to the Protocol; unknown exact mapping must not raise an exception."
  - "Precheck must use the same mock table and must return bool or IdentityCheckResult consistently; it must reject unresolvable identities."
  - "Queries must preserve target_system, binding_scope, account_set_id, and device_domain_id semantics."
  - "Supported target_system values are oa, u8, and hikvision_ivms."
  - "Supported execution_identity values are user_delegated, system_scope, and admin_approved_proxy."
  - "Supported bind_status values for Phase 0 mock: active, unbound, expired, revoked, needs_binding_scope. Do NOT produce verification_failed — this status is deferred to Phase 1 identity provider integration; P0-DOMAIN-003b1 Gateway maps it to identity_unbound anyway, so the mock must not produce it."
  - "No real external identity system calls, imports, client setup, credentials, tokens, browser sessions, or credential storage."
  - "No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values in return values, fixtures, logs, reports, or Task Record evidence."
  - "No new Python dependencies; no __init__.py (namespace packages)."
  - "Phase 1 user-value boundary: mock identity table and precheck skeleton only, no full identity provider workflow."

deliverable:
  - app/infra/identity/
  - tests/infra/identity/

constraints:
  - Implement a concrete Phase 0 mock IdentityMapping implementation under app/infra/identity/.
  - Use an in-memory mock mapping table (hardcoded or constructor-injected); PostgreSQL-backed IdentityMapping is deferred to Phase 1 and must NOT be implemented here.
  - Implement async resolve_execution_identity matching IdentityMappingPort.
  - Implement async get_mapping and list_mappings matching IdentityMappingPort.
  - Implement a precheck convenience method on the concrete implementation class (NOT as part of IdentityMappingPort Protocol); it may return bool or IdentityCheckResult but must be covered by tests. The Protocol methods (resolve_execution_identity, get_mapping, list_mappings) must be satisfied separately.
  - Return IdentityCheckResult for resolve_execution_identity and for successful lookup results; never return a bare dict.
  - Unknown identity or unresolved binding must return a deterministic failure result with bind_status='unbound' and must not raise a naked exception; do not return bind_status='verification_failed' from the Phase 0 mock.
  - Preserve target_system, binding_scope, account_set_id, and device_domain_id filters in lookups and list operations.
  - Do not import LDAP, ldap3, SSO, Active Directory, MSAL, OAuth, OAuthlib, Authlib, requests, httpx, aiohttp, subprocess, browser automation, or vendor IdP clients.
  - Do not add real credentials, token-like values, credential storage, login/session handling, browser cookies, or production identity-provider configuration.
  - Do not add new Python dependencies.
  - No __init__.py (namespace packages throughout).
  - Do not modify app/ports/identity_mapping.py or any other port file.
  - Do not modify app/runtime/ or implement Gateway/Runtime integration in this task.

acceptance_criteria:
  - criterion: "Concrete mock implementation satisfies IdentityMappingPort Protocol (async resolve_execution_identity, get_mapping, list_mappings with compatible signatures)"
    result: "pending"
    evidence: ""
  - criterion: "resolve_execution_identity returns IdentityCheckResult, not a bare dict or invented IdentityResult alias"
    result: "pending"
    evidence: ""
  - criterion: "get_mapping returns IdentityCheckResult for known mappings and None or a documented failure path for unknown exact mappings, without raising naked exceptions"
    result: "pending"
    evidence: ""
  - criterion: "precheck method on the concrete class (not on IdentityMappingPort) returns bool or IdentityCheckResult and rejects unresolvable identities; it is an implementation convenience not a Protocol requirement"
    result: "pending"
    evidence: ""
  - criterion: "Unknown or unbound identity returns bind_status='unbound' instead of raising an exception; do not use verification_failed in Phase 0 mock"
    result: "pending"
    evidence: ""
  - criterion: "target_system, binding_scope, account_set_id, and device_domain_id filters are honored"
    result: "pending"
    evidence: ""
  - criterion: "No real LDAP, SSO, Active Directory, OAuth, IdP, HTTP client, browser session, or credential-store integration is imported or called"
    result: "pending"
    evidence: ""
  - criterion: "No plaintext credential/password/token/cookie/sessionid/access_token/refresh_token values appear in implementation, fixtures, logs, reports, or Task Record evidence"
    result: "pending"
    evidence: ""

failure_examples:
  - name: unknown_identity_raises
    trigger: "resolve_execution_identity is called for an ai_user_id with no active mapping"
    expected_result: "Returns IdentityCheckResult with bind_status='unbound' and a reason_code; no naked exception; do not use verification_failed in Phase 0 mock"
    forbidden_shortcut: "Do not raise KeyError/ValueError merely because the mock table has no matching row."
  - name: bare_dict_lookup
    trigger: "known identity mapping is resolved or returned from a lookup"
    expected_result: "Returns IdentityCheckResult, not a bare dict and not an invented IdentityResult alias"
    forbidden_shortcut: "Do not satisfy tests by comparing dict keys while bypassing the Pydantic contract model."
  - name: precheck_allows_unresolvable
    trigger: "precheck is called for an unknown ai_user_id or mismatched target_system/binding_scope"
    expected_result: "Returns False or a failure IdentityCheckResult; capability execution must not be considered allowed"
    forbidden_shortcut: "Do not make precheck return True by default."
  - name: external_identity_import
    trigger: "implementation imports LDAP, SSO, Active Directory, OAuth, IdP, HTTP client, or browser-session library"
    expected_result: "Review and import scan fail"
    forbidden_shortcut: "Do not hide real external identity setup behind a mock flag."

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify P0-DOMAIN-006a passed Task Record exists"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-DOMAIN-006a_*_passed.yaml | Select-Object -First 1"
    evidence: ""
  - step: "Verify P0-INFRA-004 passed Task Record exists"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-INFRA-004_*_passed.yaml | Select-Object -First 1"
    evidence: ""
  - step: "Create identity implementation tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/infra/identity/ -v"
    evidence: "Expected non-zero exit before implementation exists."
  - step: "Implement mock IdentityMapping under app/infra/identity/"
    result: "pending"
    command: "Test-Path app/infra/identity/"
    evidence: ""
  - step: "Run identity implementation tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/infra/identity/ -v"
    evidence: ""
  - step: "Run existing IdentityMappingPort contract tests as non-regression"
    result: "pending"
    command: "uv run pytest tests/ports/test_identity_mapping_port.py -v"
    evidence: ""
  - step: "Run lint and type checks"
    result: "pending"
    command: "uv run ruff check app/infra/identity/ tests/infra/identity/; uv run mypy app/infra/identity/"
    evidence: ""
  - step: "Verify no real external identity system imports"
    result: "pending"
    command: "$patterns = 'ldap','ldap3','sspi','active_directory','msal','oauth','oauthlib','authlib','requests','httpx','aiohttp','subprocess','playwright','selenium'; $files = Get-ChildItem app/infra/identity/ -Filter '*.py' -Recurse; $hits = $files | Select-String -Pattern $patterns; if ($hits) { $hits | ForEach-Object { $_.Line }; throw 'Real external identity import detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify no plaintext credential-like values are staged"
    result: "pending"
    command: "if (git diff --cached -U0 | Select-String -Pattern '(?i)(password|passwd|secret|token|cookie|sessionid|access_token|refresh_token|api_key|private_key)\\s*[:=]\\s*[\"''][^\"''<][^\"''\\s]{6,}' -Quiet) { throw 'Possible plaintext secret value detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify no __init__.py was added"
    result: "pending"
    command: "$hits = git diff --cached --name-only | Select-String -Pattern '(^|/)__init__\\.py$'; if ($hits) { $hits | ForEach-Object { $_.Line }; throw '__init__.py added' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/infra/identity/ tests/ports/test_identity_mapping_port.py -v"
  - "uv run ruff check app/infra/identity/ tests/infra/identity/"
  - "uv run mypy app/infra/identity/"

touched_paths:
  - app/infra/identity/
  - tests/infra/identity/

forbidden_paths:
  - app/ports/
  - app/runtime/
  - pyproject.toml
  - uv.lock

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-006b"
  - "Working tree is dirty at task start"
  - "P0-DOMAIN-006a passed Task Record is missing"
  - "P0-INFRA-004 passed Task Record is missing"
  - "Any forbidden path is modified"
  - "Real external identity system is imported or called"
  - "LDAP, SSO, Active Directory, OAuth, IdP, HTTP client, browser session, or credential-store dependency is introduced"
  - "Plaintext credential-like values are staged"
  - "__init__.py is added"
  - "New Python dependency added"
  - "Bare dict returned instead of IdentityCheckResult where the Protocol requires IdentityCheckResult"
  - "Precheck allows an unresolvable identity"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
```

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm these carried-forward constraints:

1. IdentityMappingPort contract (app/ports/identity_mapping.py) is satisfied exactly and app/ports/ is not modified.
2. resolve_execution_identity, get_mapping, and list_mappings are async and signature-compatible with the Protocol.
3. Return values use IdentityCheckResult where required by the Protocol; no bare dict and no invented IdentityResult alias.
4. The mock table supports target_system plus binding_scope, account_set_id, and device_domain_id filtering.
5. Precheck rejects unresolvable identity mappings and does not default to allow.
6. Unknown identity behavior is deterministic and returns a failure result or documented None path, never a naked exception.
7. Phase 0 mock boundary is preserved: no LDAP, SSO, Active Directory, OAuth, IdP, browser session, HTTP client, credential-store call, or real credential handling.
8. No plaintext credential/password/token/cookie/sessionid/access_token/refresh_token values appear in implementation, fixtures, logs, reports, or Task Record evidence.
9. No new Python dependencies. No __init__.py files (namespace packages).
10. Gateway/Runtime integration remains deferred; this task implements only app/infra/identity/ and tests/infra/identity/.

## Structured-output baseline applicability

not_applicable

- reason: P0-DOMAIN-006b implements mock identity mapping and precheck behavior only; it does not implement LLM structured output.
- scope: app/infra/identity/ and tests/infra/identity/ only.
- blocked_by_task_id: none.
- activation_task_id: P0-DOMAIN-010b.
- expiry_condition: Structured-output baseline becomes applicable only if this task is expanded to include LLM structured output or response validation behavior.
- evidence: The task objective, deliverable, touched_paths, and forbidden_paths are identity-mapping specific and exclude structured-output implementation.
