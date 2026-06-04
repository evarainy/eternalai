# P0-DOMAIN-009b - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- app/ports/response_envelope.py (primary port-facing contract import surface)
- app/contracts/sdui/models.py (schema implementation re-exported by the port; read through the port boundary)
- docs/phase0/tasks/P0-DOMAIN-009a.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted sections 6.6, 8.6.4, 8.6.4.1, and P0-DOMAIN-009b; do not paste or rewrite the full spec.

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
- CRITICAL: ResponseEnvelope JSON must never contain plaintext credential, password, secret, token, cookie, sessionid, access_token, or refresh_token values. Sanitizer logic must intercept any such value before serialization.

## Task YAML

```yaml
task_id: P0-DOMAIN-009b
branch: "phase0/P0-DOMAIN-009b"
title: SDUI Response Envelope Minimal Implementation
type: implementation
depends_on:
  - P0-DOMAIN-009a
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
    SDUI ResponseEnvelope serialization is a user-facing and security-sensitive
    boundary. Codex owns TDD because builder behavior, Pydantic v2 JSON
    serialization, exact contract fields, and no-secret output guarantees must
    be asserted before Runtime and Golden Task work consume the envelope.

objective: >
  Implement a minimal SDUI ResponseEnvelope builder/serializer that satisfies
  the P0-DOMAIN-009a contract exposed through app/ports/response_envelope.py.
  It must produce valid JSON for ordinary message, confirm card, binding
  required, operator handback, and failed/error envelope cases while ensuring
  no plaintext secret/token/credential value can enter serialized output.

structured_output_baseline_applicability: "not_applicable"
structured_output_baseline_not_applicable:
  reason: "This task implements deterministic SDUI ResponseEnvelope builder/serializer; it does not implement LLM structured output."
  scope: "app/infra/sdui/ and tests/infra/sdui/"
  blocked_by_task_id: "P0-SPIKE-001, P0-SPIKE-002, P0-SPIKE-007, P0-DOMAIN-010a, P0-DOMAIN-010b"
  activation_task_id: "P0-DOMAIN-010b"
  expiry_condition: "Structured-output baseline becomes applicable only when an approved task links ResponseEnvelope generation to LLM parser output."
  evidence: "This task consumes Pydantic ResponseEnvelope models from P0-DOMAIN-009a and performs deterministic serialization/sanitization only."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-009b_<timestamp>_passed.yaml"

constraints_to_carry_forward:
  - "Use the ResponseEnvelope and UIComponent models re-exported by app/ports/response_envelope.py; do not redefine or modify the port."
  - "Serialized ResponseEnvelope JSON must contain the exact P0-DOMAIN-009a fields: schema_version, response_id, task_id, session_id, status, message, fallback_text, ui, data, trace_id, trace_summary."
  - "Do not invent generic error or metadata fields unless a future approved contract task changes app/contracts/sdui/models.py."
  - "Allowed ResponseEnvelope.status values are completed, blocked, waiting_user, failed, and no_capability_found."
  - "Supported Phase 0 UI components remain none, confirm_card, operator_handback_card, and binding_required_card."
  - "confirm_card is one-shot confirm only; no multi-turn confirmation state machine."
  - "binding_required_card is only for bind_required; unclear scope uses operator_handback_card(action=clarify_scope)."
  - "Blocked or waiting_user envelopes should keep data as null unless this task explicitly supplies a sanitized business summary."
  - "CLI fallback_text must be present and non-empty for every successful builder output and failed envelope output."
  - "No plaintext credential, password, secret, token, cookie, sessionid, access_token, or refresh_token values in ResponseEnvelope JSON, fixtures expected output, logs, reports, or Task Record evidence."

deliverable:
  - app/infra/sdui/
  - tests/infra/sdui/

constraints:
  - Implement an infra-side SDUI builder/serializer only; do not add API routes.
  - Use existing Pydantic v2 models and serialization methods such as model_dump and model_dump_json; Pydantic is already a project dependency.
  - Provide a minimal public builder/serializer surface that can produce ordinary message, confirm card, binding_required card, operator_handback card, and failed/error ResponseEnvelope objects.
  - Serializer must return valid JSON and must not expose raw ValidationError or other uncaught exceptions for expected invalid/malformed builder input; return a sanitized failed ResponseEnvelope instead.
  - Sanitizer MUST run before ResponseEnvelope construction (Pydantic model level); sanitizing only at JSON serialization is insufficient because Pydantic model construction may record or log the value before serialization.
  - Sanitizer tests may use fake token-like input strings only as input; expected serialized output must contain redacted placeholders or safe failure text, never the plaintext input value.
  - Do not modify app/ports/response_envelope.py.
  - Do not modify app/contracts/sdui/models.py.
  - Do not create app/runtime/response_composer/ for this task; app/runtime/ is forbidden by this prompt.
  - Do not create app/api/v1/sdui/; this task is not an HTTP endpoint task.
  - Do not implement a full SDUI renderer, dynamic form orchestrator, graph/table/file card, frontend renderer, or multi-turn confirmation loop.
  - Do not add database, Redis, MinIO, external service, HTTP client, or real business-system dependency.
  - Do not add new Python dependencies.
  - No __init__.py files (namespace packages throughout).

acceptance_criteria:
  - criterion: "Builder imports and returns the ResponseEnvelope/UIComponent classes exposed by app/ports/response_envelope.py without redefining the contract"
    result: "pending"
    evidence: ""
  - criterion: "Serialized JSON for ordinary message envelope is valid JSON and contains all exact contract fields: schema_version, response_id, task_id, session_id, status, message, fallback_text, ui, data, trace_id, trace_summary"
    result: "pending"
    evidence: ""
  - criterion: "Serialized JSON for confirm card uses ui.component_type='confirm_card' and ui.action='confirm' only"
    result: "pending"
    evidence: ""
  - criterion: "Serialized JSON for identity unbound MUST use binding_required_card; operator_handback_card is NOT used for identity unbound in Phase 0"
    result: "pending"
    evidence: ""
  - criterion: "Serialized JSON for unclear scope uses operator_handback_card(action='clarify_scope') and does not misuse binding_required_card"
    result: "pending"
    evidence: ""
  - criterion: "Builder can return a sanitized failed ResponseEnvelope for expected invalid/malformed input instead of leaking raw exceptions to Runtime/Gateway callers"
    result: "pending"
    evidence: ""
  - criterion: "Sanitizer intercepts nested plaintext credential/password/secret/token/cookie/sessionid/access_token/refresh_token-like values before serialization"
    result: "pending"
    evidence: ""
  - criterion: "No plaintext secret/token/credential input value appears in serialized JSON, fixtures expected output, logs, or Task Record evidence"
    result: "pending"
    evidence: ""
  - criterion: "For ResponseEnvelope with status blocked, waiting_user, or no_capability_found, the data field is null in serialized JSON unless the builder caller explicitly provides a sanitized business summary"
    result: "pending"
    evidence: ""
  - criterion: "No app/ports/, app/contracts/sdui/, app/runtime/, app/api/v1/sdui/, or web renderer path is modified"
    result: "pending"
    evidence: ""

failure_examples:
  - name: missing_required_field
    trigger: "Builder receives data that would omit required ResponseEnvelope fields such as response_id, task_id, session_id, status, message, fallback_text, ui, or trace_id"
    expected_result: "Test fails or builder returns a sanitized ResponseEnvelope(status='failed') with all required fields present; it must not serialize an incomplete envelope"
    forbidden_shortcut: "Do not add optional defaults to the frozen contract or bypass Pydantic validation."
  - name: secret_value_serialized
    trigger: "message, fallback_text, data, ui.payload, trace_summary, or error text contains a fake plaintext credential/password/secret/token/cookie/sessionid/access_token/refresh_token-like value"
    expected_result: "Sanitizer intercepts before serialization; serialized JSON contains only redacted/safe text and never the plaintext input value"
    forbidden_shortcut: "Do not merely hide the value in UI display while still emitting it in JSON."
  - name: raw_exception_leaks
    trigger: "Expected invalid/malformed builder input causes a Pydantic ValidationError, KeyError, TypeError, or sanitizer failure"
    expected_result: "Public builder/serializer returns a sanitized failed ResponseEnvelope or explicit safe error result for caller handling"
    forbidden_shortcut: "Do not let Runtime/Gateway callers receive raw exceptions for expected builder input errors."
  - name: unsupported_component_type
    trigger: "Builder input asks for a component_type outside none, confirm_card, operator_handback_card, or binding_required_card"
    expected_result: "Validation fails or sanitized failed ResponseEnvelope is returned; unknown UI components are not silently passed through"
    forbidden_shortcut: "Do not downgrade unknown components to component_type='none' without evidence."
  - name: missing_fallback_text
    trigger: "Builder input omits fallback_text or passes an empty fallback_text"
    expected_result: "Validation fails or sanitized failed ResponseEnvelope is returned; all serialized envelopes keep non-empty CLI fallback_text"
    forbidden_shortcut: "Do not use an empty string to pass tests."

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify P0-DOMAIN-009a passed Task Record exists"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-DOMAIN-009a_*_passed.yaml | Select-Object -First 1"
    evidence: ""
  - step: "Create SDUI infra tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/infra/sdui/ -v"
    evidence: "Expected non-zero exit before app/infra/sdui/ implementation exists."
  - step: "Implement minimal infra-side ResponseEnvelope builder/serializer"
    result: "pending"
    command: "Test-Path app/infra/sdui/"
    evidence: ""
  - step: "Run SDUI infra tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/infra/sdui/ -v"
    evidence: ""
  - step: "Run frozen contract regression tests"
    result: "pending"
    command: "uv run pytest tests/contracts/sdui/test_response_envelope_contract.py -v"
    evidence: ""
  - step: "Run lint and type checks"
    result: "pending"
    command: "uv run ruff check app/infra/sdui/ tests/infra/sdui/; uv run mypy app/infra/sdui/"
    evidence: ""
  - step: "Verify no __init__.py files were created"
    result: "pending"
    command: "$initFiles = Get-ChildItem app/infra/sdui/, tests/infra/sdui/ -Filter '__init__.py' -Recurse -ErrorAction SilentlyContinue; if ($initFiles) { $initFiles | ForEach-Object { $_.FullName }; throw '__init__.py created in namespace package path' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify staged diff has no plaintext secret/token output values"
    result: "pending"
    command: "$secretPattern = '(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|authorization|bearer|cookie|session[_-]?id|access_token|refresh_token)\\s*[:=]\\s*[\"'']?[^\"''<\\s]{6,}'; $hits = git diff --cached -U0 | Select-String -Pattern $secretPattern; if ($hits) { 'SECRET SCAN FAIL:'; $hits | ForEach-Object { $_.Line } } else { 'SECRET SCAN: no hits' }"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "$forbidden = '^(app/ports/|app/contracts/sdui/|app/runtime/|app/api/v1/sdui/|web/src/sdui_renderer/|pyproject\\.toml$|uv\\.lock$)'; $hits = git diff --cached --name-only | Select-String -Pattern $forbidden; if ($hits) { 'FORBIDDEN PATH FAIL:'; $hits | ForEach-Object { $_.Line }; throw 'Forbidden path staged' } else { 'PASSED' }"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/infra/sdui/ tests/contracts/sdui/test_response_envelope_contract.py -v"
  - "uv run ruff check app/infra/sdui/ tests/infra/sdui/"
  - "uv run mypy app/infra/sdui/"

touched_paths:
  - app/infra/sdui/
  - tests/infra/sdui/

forbidden_paths:
  - app/ports/
  - app/contracts/sdui/
  - app/runtime/
  - app/api/v1/sdui/
  - web/src/sdui_renderer/
  - pyproject.toml
  - uv.lock

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-009b"
  - "Working tree is dirty at task start"
  - "P0-DOMAIN-009a passed Task Record is missing"
  - "Any forbidden path is modified"
  - "ResponseEnvelope JSON contains plaintext credential/password/secret/token/cookie/sessionid/access_token/refresh_token-like value"
  - "Builder/serializer requires a DB, Redis, MinIO, HTTP client, external service, or real business-system dependency"
  - "Builder changes the P0-DOMAIN-009a contract fields or invents error/metadata fields"
  - "__init__.py file is created"
  - "New Python dependency added"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
```

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm these carried-forward constraints:

1. ResponseEnvelope builder/serializer uses the contract re-exported by app/ports/response_envelope.py and does not modify app/ports/ or app/contracts/sdui/.
2. Serialized JSON contains the exact P0-DOMAIN-009a fields: schema_version, response_id, task_id, session_id, status, message, fallback_text, ui, data, trace_id, trace_summary.
3. No generic error or metadata fields are invented for this task; failed/error output is represented with the existing status/message/fallback_text/ui/data/trace fields.
4. Ordinary message, confirm_card, binding_required, operator_handback, and failed/error envelope paths are covered by tests.
5. CRITICAL: Sanitizer intercepts nested credential/password/secret/token/cookie/sessionid/access_token/refresh_token-like values before serialization, and serialized ResponseEnvelope JSON never contains the plaintext input value.
6. fallback_text is present and non-empty for CLI/text fallback.
7. confirm_card remains one-shot confirm only; no multi-turn confirmation state machine, renderer, or dynamic form orchestrator is introduced.
8. app/runtime/, app/api/v1/sdui/, web renderer paths, app/ports/, pyproject.toml, and uv.lock remain untouched.
9. No new Python dependencies. No __init__.py files (namespace packages).
10. No DB dependency, no external service dependency, no real OA/U8/Hikvision call, and no HTTP client import.

## Structured-output baseline applicability

not_applicable

- reason: P0-DOMAIN-009b implements deterministic SDUI ResponseEnvelope builder/serializer behavior, not LLM schema extraction.
- scope: LLM provider, structured output parser, prompt format, response_format, instructor, and PydanticAI decisions.
- blocked_by_task_id: P0-SPIKE-001, P0-SPIKE-002, P0-SPIKE-007, P0-DOMAIN-010a, P0-DOMAIN-010b.
- activation_task_id: P0-DOMAIN-010b or later Runtime/Golden Task integration that explicitly consumes LLM structured output.
- expiry_condition: Expires only when an approved task changes the structured-output baseline or links ResponseEnvelope generation to LLM parser output.
- evidence: This task consumes Pydantic ResponseEnvelope models already produced by P0-DOMAIN-009a and performs deterministic serialization/sanitization only.
