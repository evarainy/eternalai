# P0-DOMAIN-005b - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- app/ports/trace.py (primary TracePort contract; read before writing any implementation code)
- docs/phase0/tasks/P0-DOMAIN-005a.md
- docs/phase0/task_logs/P0-DOMAIN-005a_*_passed.yaml (dependency evidence and residual 005b sanitizer enforcement risk)
- docs/phase0/task_logs/P0-INFRA-006_*_passed.yaml (OTel/Langfuse config evidence; check what was actually deployed)
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted Trace sections 8.6.7, 8.6.8, and P0-DOMAIN-005b; do not paste or rewrite the full spec.

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
- CRITICAL: Trace must never record plaintext secret, password, token, cookie, sessionid, access_token, or refresh_token values. Sanitizer checks must run before any Trace output, OTel span attribute, Langfuse/collector export path, fixture expected output, log, report, or Task Record evidence can contain data.

## Task YAML

```yaml
task_id: P0-DOMAIN-005b
branch: "phase0/P0-DOMAIN-005b"
title: Trace Minimal Write Skeleton
type: implementation
depends_on:
  - P0-DOMAIN-005a
  - P0-INFRA-006
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
    Trace Minimal Write Skeleton is a production-code, security-sensitive
    observability implementation. Codex owns TDD because TracePort satisfaction,
    OTel span creation, soft failure behavior, and reject-before-write sanitizer
    enforcement must be proven before Gateway integration can depend on trace
    writes. Independent review must happen in a separate session.

objective: >
  Implement a minimal OTel-backed TraceWriter satisfying the TracePort Protocol
  frozen in P0-DOMAIN-005a. The implementation writes sanitized TraceEvent data
  as OpenTelemetry spans and can route to the existing OTel/Langfuse deployment
  path when configured. It does not implement a full observability pipeline,
  database persistence, Runtime integration, Gateway integration, or control-plane
  trace query API. The sanitizer must prevent secret/token leakage before any
  Trace output is produced.

structured_output_baseline_applicability: "not_applicable"
structured_output_baseline_not_applicable:
  reason: "This task implements Trace minimal write only; it does not implement LLM structured output."
  scope: "app/infra/observability/ and tests/infra/observability/"
  blocked_by_task_id: "none"
  activation_task_id: "P0-DOMAIN-010b"
  expiry_condition: "Structured-output baseline becomes applicable only in LLM provider or structured-output implementation tasks."
  evidence: "TracePort has no LLM provider or structured-output method."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-005b_<timestamp>_passed.yaml"

constraints_to_carry_forward:
  - "TracePort contract (app/ports/trace.py) must be satisfied exactly"
  - "Trace write path is async record_event(event: TraceEvent) -> None; do not rename the port method or require Gateway to call a different public method"
  - "Wrapper methods from trace.py must be implemented: start_task_trace, record_step, record_policy_decision, record_gateway_call, finalize_task_trace"
  - "TraceEvent fields must match trace.py: trace_id, task_id, session_id, event_type, status, capability_id, error_code, attributes; extra fields are forbidden by the model"
  - "set_sanitizer(hook: SanitizerHookFn) is synchronous and must run before trace writes"
  - "Sanitizer must cover Bearer token, sessionid, access_token, refresh_token, cookie, and set-cookie patterns"
  - "Plaintext secret/password/token/cookie/sessionid/access_token/refresh_token values must never appear in Trace output, OTel span attributes, Langfuse/collector output, fixtures expected output, logs, reports, or Task Record evidence"
  - "P0-INFRA-006 established an OTel Collector + Langfuse deployment baseline and explicitly deferred app instrumentation to later TDD tasks"

deliverable:
  - app/infra/observability/
  - tests/infra/observability/

constraints:
  - Implement a minimal TraceWriter satisfying TracePort.
  - Verify opentelemetry-sdk is already declared in pyproject.toml before implementation; do not add it. If opentelemetry-sdk is absent from pyproject.toml, implement a no-op TraceWriter that logs at DEBUG level instead of creating OTel spans; the sanitizer must still run and enforce the no-plaintext-secret constraint in no-op mode.
  - Connect to the existing OTel/Langfuse path only if existing configuration and existing dependencies support it; otherwise keep external export as a documented soft no-op and prove local span creation in tests.
  - Do not add a Langfuse SDK dependency or any other unapproved dependency.
  - Tests for async methods must use asyncio.run() in synchronous test functions; pytest-asyncio is not installed and cannot be added as a new dependency.
  - Do not implement database persistence, trace query APIs, Runtime integration, Gateway integration, control-plane routes, or full observability pipeline behavior.
  - record_event must create an OTel span for valid TraceEvent input after sanitizer approval.
  - Wrapper methods must build TraceEvent instances using the frozen TraceEvent fields and delegate to record_event.
  - Sanitizer MUST replace matched credential-like values with the literal string [REDACTED] in TraceEvent attribute values before OTel span creation; it must NOT silently drop the attribute or reject the entire trace write — the attribute must survive with [REDACTED] as its value.
  - OTel/exporter/config failures must be soft failures for Gateway-facing TracePort calls; do not let exporter availability crash normal callers.
  - Sanitizer rejection may stop the write, but any raised error must be explicit, deterministic, and must not include the sensitive value.
  - No plaintext secret/password/token/cookie/sessionid/access_token/refresh_token values in fixtures, logs, reports, Task Record evidence, or expected trace output.
  - No __init__.py (namespace packages throughout).
  - Do not modify app/ports/trace.py or any other port file.
  - Do not modify app/runtime/.
  - Do not modify pyproject.toml or uv.lock in this task. If dependency evidence contradicts the expected OTel SDK dependency, stop and report.

acceptance_criteria:
  - criterion: "TraceWriter satisfies TracePort Protocol with async record_event and all wrapper methods from app/ports/trace.py"
    result: "pending"
    evidence: ""
  - criterion: "record_event creates an OpenTelemetry span for a valid TraceEvent and records sanitized TraceEvent fields as span attributes/events"
    result: "pending"
    evidence: ""
  - criterion: "Wrapper methods create TraceEvent instances whose fields match the Protocol definition and delegate to record_event"
    result: "pending"
    evidence: ""
  - criterion: "Sanitizer intercepts Bearer/sessionid/access_token/refresh_token/cookie/set-cookie-like values before any Trace output or OTel span attribute is written"
    result: "pending"
    evidence: ""
  - criterion: "No plaintext secret/password/token/cookie/sessionid/access_token/refresh_token value appears in span output, fixtures expected output, logs, reports, or Task Record evidence"
    result: "pending"
    evidence: ""
  - criterion: "OTel exporter or Langfuse/collector configuration absence causes a soft failure/no-op path, not an unhandled exception to callers"
    result: "pending"
    evidence: ""
  - criterion: "opentelemetry-sdk dependency is verified as already present; no pyproject.toml or uv.lock change is introduced"
    result: "pending"
    evidence: ""

failure_examples:
  - name: raw_secret_value_recorded
    trigger: "TraceEvent attributes contain a token-like or credential-like value"
    expected_result: "Sanitizer rejects or strictly prevents the write before OTel span creation; no Trace output contains the value"
    forbidden_shortcut: "Do not write raw_payload first and hide it only in a display summary"
  - name: exporter_unavailable_crashes_gateway
    trigger: "OTel exporter, collector, or Langfuse endpoint is missing, unavailable, or misconfigured"
    expected_result: "TracePort call returns/soft-fails without crashing the Gateway-facing caller; failure evidence contains no sensitive value"
    forbidden_shortcut: "Do not let exporter setup raise an unhandled exception from record_event"
  - name: trace_event_missing_required_fields
    trigger: "Wrapper builds a TraceEvent without trace_id, task_id, session_id, event_type, or status"
    expected_result: "Pydantic validation or tests fail before a malformed event is written"
    forbidden_shortcut: "Do not serialize partial dicts to bypass TraceEvent validation"
  - name: sanitizer_runs_after_span_write
    trigger: "Sensitive pattern is present and sanitizer is invoked only after span attributes are set"
    expected_result: "Test fails; sanitizer must run before span creation or attribute/event write"
    forbidden_shortcut: "Do not rely on exporter-side redaction as the primary control"

step_verification_points:
  - step: "Preflight branch and dirty tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify dependency Task Records exist and passed"
    result: "pending"
    command: >
      $missing = @();
      if (-not (Get-ChildItem -Path docs/phase0/task_logs -Filter 'P0-DOMAIN-005a_*_passed.yaml' -ErrorAction SilentlyContinue)) { $missing += 'P0-DOMAIN-005a' }
      if (-not (Get-ChildItem -Path docs/phase0/task_logs -Filter 'P0-INFRA-006_*_passed.yaml' -ErrorAction SilentlyContinue)) { $missing += 'P0-INFRA-006' }
      if ($missing) { throw ('Missing passed Task Record: ' + ($missing -join ', ')) } else { 'PASSED' }
    evidence: ""
  - step: "Read P0-INFRA-006 deployment evidence before choosing exporter behavior"
    result: "pending"
    command: "Get-Content docs/phase0/task_logs/P0-INFRA-006_*_passed.yaml | Select-String -Pattern 'OpenTelemetry','Langfuse','otel-collector','deferred','not implemented'"
    evidence: ""
  - step: "Detect OTel SDK presence and choose OTel or no-op TraceWriter implementation path"
    result: "pending"
    command: >
      $pyproject = Get-Content -LiteralPath pyproject.toml -Raw;
      if ($pyproject -notmatch 'opentelemetry-sdk') { 'opentelemetry-sdk NOT in pyproject.toml — implement no-op TraceWriter that logs at DEBUG level; do NOT add opentelemetry-sdk to pyproject.toml or uv.lock; sanitizer must still run in no-op mode.' } else { 'opentelemetry-sdk FOUND in pyproject.toml — implement OTel-backed TraceWriter.' }
    evidence: ""
  - step: "Create TraceWriter tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/infra/observability/"
    evidence: "Expected non-zero exit before implementation exists."
  - step: "Implement minimal TraceWriter"
    result: "pending"
    command: "Test-Path app/infra/observability/"
    evidence: ""
  - step: "Run observability tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/infra/observability/ -v"
    evidence: ""
  - step: "Run TracePort contract non-regression tests"
    result: "pending"
    command: "uv run pytest tests/ports/test_trace_port.py tests/infra/observability/ -v"
    evidence: ""
  - step: "Run lint and type checks"
    result: "pending"
    command: "uv run ruff check app/infra/observability/ tests/infra/observability/; uv run mypy app/infra/observability/"
    evidence: ""
  - step: "Verify no __init__.py files were added"
    result: "pending"
    command: >
      $hits = Get-ChildItem -Path app/infra/observability,tests/infra/observability -Filter '__init__.py' -Recurse -ErrorAction SilentlyContinue;
      if ($hits) { $hits | ForEach-Object { $_.FullName }; throw '__init__.py detected' } else { 'PASSED' }
    evidence: ""
  - step: "Verify staged diff has no plaintext credential values in output-bearing code or expected outputs"
    result: "pending"
    command: >
      $secretPattern = '(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|authorization|bearer|cookie|sessionid|session[_-]?id|access_token|refresh_token)\s*[:=]\s*["'']?[^"''<\s]{6,}';
      $hits = git diff --cached -U0 | Select-String -Pattern $secretPattern;
      if ($hits) { 'SECRET SCAN REVIEW REQUIRED:'; $hits | ForEach-Object { $_.Line }; throw 'Possible plaintext secret value detected' } else { 'SECRET SCAN: no hits' }
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/ports/test_trace_port.py tests/infra/observability/ -v"
  - "uv run ruff check app/infra/observability/ tests/infra/observability/"
  - "uv run mypy app/infra/observability/"

touched_paths:
  - app/infra/observability/
  - tests/infra/observability/

forbidden_paths:
  - app/ports/
  - app/runtime/
  - app/gateway/
  - app/control_plane/
  - pyproject.toml
  - uv.lock

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-005b"
  - "Working tree is dirty at task start"
  - "P0-DOMAIN-005a passed Task Record is missing"
  - "P0-INFRA-006 passed Task Record is missing"
  - "P0-INFRA-006 evidence contradicts the expected OTel/Langfuse deployment baseline"
  - "pyproject.toml or uv.lock is modified to add opentelemetry-sdk or any other dependency; use the no-op TraceWriter path if opentelemetry-sdk is absent"
  - "Any forbidden path is modified"
  - "Plaintext secret/password/token/cookie/sessionid/access_token/refresh_token value is detected in Trace output, span attributes, expected output, logs, reports, or Task Record evidence"
  - "Sanitizer is missing, runs after write, or fails to cover Bearer/sessionid/access_token/refresh_token/cookie/set-cookie patterns"
  - "record_event or wrapper methods do not satisfy TracePort"
  - "OTel/exporter failure raises an unhandled exception to Gateway-facing callers"
  - "New Python dependency is added"
  - "__init__.py is added"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
```

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm these carried-forward constraints:

1. TraceWriter satisfies the frozen TracePort contract in app/ports/trace.py, including async record_event and the five wrapper methods.
2. TraceEvent fields and validation match the Protocol definition; implementation does not write partial dicts or extra fields.
3. Trace must never record plaintext secret/password/token/cookie/sessionid/access_token/refresh_token values.
4. Sanitizer runs before any Trace output, OTel span attribute/event write, Langfuse/collector export path, fixture expected output, log, report, or Task Record evidence.
5. Sanitizer covers Bearer token, sessionid, access_token, refresh_token, cookie, and set-cookie patterns and refuses or strictly prevents unsafe writes.
6. Minimal OTel span creation is implemented; full observability pipeline, persistence/query API, Runtime integration, Gateway integration, and control-plane routes remain out of scope.
7. P0-INFRA-006 deployment evidence is checked before exporter behavior is chosen.
8. No new dependencies are added; opentelemetry-sdk is verified as already declared or the task stops.
9. No __init__.py files are added.
10. Forbidden paths, especially app/ports/, remain untouched.

## Structured-output baseline applicability

not_applicable - this task does not implement LLM structured output. It must not change the Phase 1 baseline and must not reopen instructor or PydanticAI default decisions.
