# P0-DOMAIN-011b - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- app/ports/secret_provider.py (the SecretProviderPort contract this task implements)
- app/ports/trace.py (TracePort — for sanitizer integration context; do not implement)
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted section P0-DOMAIN-011b; do not paste or rewrite the full spec.

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
- CRITICAL: No plaintext secret, password, token, cookie, sessionid, access_token, or refresh_token values may appear in ANY output — including return values, fixtures, logs, reports, or Task Record evidence.

## Task YAML

```yaml
task_id: P0-DOMAIN-011b
branch: "phase0/P0-DOMAIN-011b"
title: Noop SecretProvider Skeleton
type: implementation
depends_on:
  - P0-DOMAIN-011a
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
    Noop SecretProvider is a security-sensitive implementation. It must prove that
    the Gateway can only inject credentials via SecretProviderPort and that no
    plaintext value ever surfaces. Codex owns TDD because the no-plaintext and
    sanitizer-integration constraints must be tested before Gateway integration.

objective: >
  Implement a Phase 0 Noop/Mock SecretProvider that satisfies SecretProviderPort,
  returns only mock_secret_injected=True or redacted placeholder (never plaintext
  values), and proves via tests that the Gateway cannot bypass SecretProviderPort
  for credential handling. Sanitizer integration must be tested to show that
  token-like test values are intercepted before Trace or ResponseEnvelope.

structured_output_baseline_applicability: "not_applicable - this task does not implement LLM structured output."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-011b_<timestamp>_passed.yaml"

constraints_to_carry_forward:
  - "SecretProviderPort contract (app/ports/secret_provider.py) must be satisfied exactly"
  - "Return only mock_secret_injected=True or redacted placeholder — never plaintext secret/password/token/cookie/sessionid/access_token/refresh_token values"
  - "Trace must record only credential_ref or credential_usage_event, never the secret value"
  - "Sanitizer must intercept any token-like value before it reaches Trace or ResponseEnvelope"
  - "No Vault/KMS/OAuth2/vendor-token implementation"
  - "No new Python dependencies; no __init__.py (namespace packages)"
  - "Import boundary: Adapter and Gateway must only access credentials via SecretProviderPort"

deliverable:
  - app/infra/security/noop_secret_provider/
  - tests/infra/security/

constraints:
  - Implement NoopSecretProvider satisfying SecretProviderPort (resolve_secret_ref and inject_execution_secret, both async).
  - resolve_secret_ref must return a dict containing only credential_ref and redacted_placeholder="<redacted>"; never a plaintext secret value.
  - inject_execution_secret must return a dict containing mock_secret_injected=True; never a plaintext secret value.
  - Trace/log output must record only credential_ref or a credential_usage_event marker, never the secret value.
  - Tests must prove sanitizer intercepts any Bearer/sessionid/access_token/refresh_token-like test values before they reach Trace or ResponseEnvelope.
  - Tests must prove import boundary: no Adapter or Gateway code can access credentials bypassing SecretProviderPort.
  - No Vault, KMS, OAuth2, or vendor token handling.
  - No requests/httpx or real credential-store client imports.
  - Do not add new Python dependencies.
  - No __init__.py (namespace packages throughout).
  - Do not modify app/ports/secret_provider.py or app/ports/trace.py.
  - Do not modify pyproject.toml or uv.lock.

acceptance_criteria:
  - criterion: "NoopSecretProvider satisfies SecretProviderPort Protocol (duck-type check)"
    result: "pending"
    evidence: ""
  - criterion: "resolve_secret_ref returns dict with credential_ref and redacted_placeholder='<redacted>'; no plaintext secret value"
    result: "pending"
    evidence: ""
  - criterion: "inject_execution_secret returns dict with mock_secret_injected=True; no plaintext secret value"
    result: "pending"
    evidence: ""
  - criterion: "Trace records only credential_ref or usage event, never secret value"
    result: "pending"
    evidence: ""
  - criterion: "Sanitizer intercepts Bearer/sessionid/access_token/refresh_token-like test values before Trace or ResponseEnvelope"
    result: "pending"
    evidence: ""
  - criterion: "Import boundary test passes: tests/architecture/test_import_boundaries.py still passes with new implementation"
    result: "pending"
    evidence: ""
  - criterion: "No Vault/KMS/OAuth2/vendor-token implementation or import"
    result: "pending"
    evidence: ""

failure_examples:
  - name: secret_value_requested
    trigger: "Caller requests plaintext secret/password/token via resolve_secret_ref or inject_execution_secret"
    expected_result: "Returns only redacted placeholder or mock_secret_injected=True; never a real or dummy plaintext value"
    forbidden_shortcut: "禁止为测试方便返回 dummy_token_123 或任何形似真实 token 的值"
  - name: token_like_payload_generated
    trigger: "Noop provider internally constructs a value containing Bearer/sessionid/access_token/refresh_token pattern"
    expected_result: "Sanitizer intercepts the value; it does not reach Trace or ResponseEnvelope"
    forbidden_shortcut: "禁止只在日志里标记 safe 而不实际拦截"
  - name: bypass_secret_provider
    trigger: "Adapter or Gateway attempts to access mock secret without going through SecretProviderPort"
    expected_result: "Import boundary test fails or test assertion detects bypass"
    forbidden_shortcut: "禁止在 Adapter 中硬编码 credential"

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify P0-DOMAIN-011a passed Task Record exists"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-DOMAIN-011a_*_passed.yaml | Select-Object -First 1"
    evidence: ""
  - step: "Create noop provider tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/infra/security/"
    evidence: "Expected non-zero exit before implementation exists."
  - step: "Implement NoopSecretProvider"
    result: "pending"
    command: "Test-Path app/infra/security/noop_secret_provider/"
    evidence: ""
  - step: "Run all security tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/infra/security/ -v"
    evidence: ""
  - step: "Run import boundary check"
    result: "pending"
    command: "uv run pytest tests/architecture/test_import_boundaries.py -v"
    evidence: ""
  - step: "Run lint and type checks"
    result: "pending"
    command: "uv run ruff check app/infra/security/noop_secret_provider/ tests/infra/security/; uv run mypy app/infra/security/noop_secret_provider/"
    evidence: ""
  - step: "Verify staged diff has no plaintext credential values"
    result: "pending"
    command: "$secretPattern = '(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|authorization|bearer|cookie|session[_-]?id)\\s*[:=]\\s*[\"'']?[^\"''<\\s]{6,}'; $hits = git diff --cached -U0 | Select-String -Pattern $secretPattern; if ($hits) { 'SECRET SCAN FAIL:'; $hits | ForEach-Object { $_.Line } } else { 'SECRET SCAN: no hits' }"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/infra/security/ tests/architecture/test_import_boundaries.py -v"
  - "uv run ruff check app/infra/security/noop_secret_provider/ tests/infra/security/"
  - "uv run mypy app/infra/security/noop_secret_provider/"

touched_paths:
  - app/infra/security/noop_secret_provider/
  - tests/infra/security/

forbidden_paths:
  - app/runtime/
  - app/execution_fabric/real_adapters/
  - app/ports/
  - pyproject.toml
  - uv.lock

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-011b"
  - "Working tree is dirty at task start"
  - "P0-DOMAIN-011a passed Task Record is missing"
  - "Any forbidden path is modified"
  - "Plaintext secret/password/token/cookie/sessionid/access_token/refresh_token value detected in any output"
  - "Vault/KMS/OAuth2/vendor-token implementation or import introduced"
  - "Import boundary test fails"
  - "New Python dependency added"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
```

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm these carried-forward constraints:

1. SecretProviderPort contract (app/ports/secret_provider.py) is satisfied exactly — both methods are async and return dicts with only redacted/mock values.
2. No plaintext secret/password/token/cookie/sessionid/access_token/refresh_token value appears anywhere — return values, fixtures, logs, Task Record evidence.
3. Trace records only credential_ref or usage event markers, never the secret value itself.
4. Sanitizer intercepts any token-like pattern before it reaches Trace or ResponseEnvelope.
5. Import boundary test (tests/architecture/test_import_boundaries.py) passes with this implementation.
6. No Vault/KMS/OAuth2/vendor-token implementation.
7. No new Python dependencies. No __init__.py files (namespace packages).

## Structured-output baseline applicability

not_applicable - this task does not implement LLM structured output. It must not change the Phase 1 baseline and must not reopen instructor or PydanticAI default decisions.
