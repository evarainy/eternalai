# P0-INFRA-006 — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only as source of truth; do not paste it in full.

## Global hard rules

- Execute only this task_id.
- Start this task only after all depends_on tasks have been reviewed, approved, and merged to the Phase 0 base branch.
- Do not modify frozen blueprint files.
- Do not implement Phase 1 features.
- Do not add unapproved dependencies.
- Do not weaken tests to pass.
- Stop after Unified Task Record and wait for human confirmation.
- No commit, no push, no merge.

## Task YAML

```yaml
task_id: P0-INFRA-006
branch: "phase0/P0-INFRA-006"
title: OpenTelemetry + Langfuse Deployment Baseline
type: infrastructure
depends_on:
  - P0-INFRA-001
  - P0-INFRA-002
priority: prerequisite
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "execution"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "PDR"
  reason_for_owner_choice: >
    This Batch 2 task is deployment-only. Claude Code/MiMo owns Docker Compose,
    collector, Langfuse, env placeholder, and operator documentation baseline
    work. Codex performs independent review. App instrumentation, sanitizer
    behavior, API trace_id behavior, Gateway/Runtime/Tool spans, and end-to-end
    tracing are deferred to later dedicated TDD tasks.

objective: >
  Establish a smoke-ready OpenTelemetry Collector + Langfuse self-hosted
  deployment baseline through Docker Compose configuration, collector config,
  placeholder environment documentation, and operator notes. This task validates
  configuration shape only; it does not require app instrumentation or runtime
  trace emission.

structured_output_baseline_applicability: "not_applicable — this task does not implement LLM structured output."

scope:
  active_scope: "deployment_baseline_only"
  active_method: "PDR"
  deferred_scopes:
    - "app/observability instrumentation code"
    - "Redaction/Sanitizer behavior"
    - "API health trace_id response behavior"
    - "Gateway/Runtime/Tool spans"
    - "end-to-end task trace validation"
  deferred_scope_owner: "later dedicated Codex TDD task"

deliverable:
  - infra/observability/
  - docker-compose.yml (observability profile service wiring for OTel Collector and Langfuse)
  - .env.example (LANGFUSE_ and OTEL_ placeholder documentation, no secrets)
  - README.md (deployment baseline, local config validation, smoke-ready notes, deferred instrumentation scope)

constraints:
  - Deployment baseline only; do not add app instrumentation code
  - Do not create app/observability/ or tests/observability/ in this task
  - Do not claim API trace_id behavior exists
  - Do not claim Gateway, Runtime, Tool, or end-to-end spans exist
  - Do not log or document real credentials, tokens, cookies, or session tokens
  - .env.example already contains LANGFUSE_ and OTEL_ variables from P0-INFRA-001; verify they are complete rather than re-adding them; add only variables specific to OTel Collector deployment not covered by P0-INFRA-001
  - Docker Compose validation is parse/config-only unless the human explicitly asks for container startup
  - Gateway Mock call trace_id association is deferred to P0-DOMAIN-003b2 or Golden Task scope

acceptance_criteria:
  - criterion: "docker compose --profile observability config passes without starting containers"
    result: "pending"
    evidence: ""
  - criterion: "infra/observability/ contains OTel Collector and Langfuse deployment baseline config or documented config placeholders"
    result: "pending"
    evidence: ""
  - criterion: "docker-compose.yml observability profile references OTel Collector and Langfuse services/configuration"
    result: "pending"
    evidence: ""
  - criterion: ".env.example preserves existing LANGFUSE_ and OTEL_ placeholders and only adds OTel Collector-specific variables if P0-INFRA-001 did not already cover them"
    result: "pending"
    evidence: ""
  - criterion: "README.md documents local config validation and smoke-ready deployment notes"
    result: "pending"
    evidence: ""
  - criterion: "README.md explicitly defers app instrumentation, API trace_id behavior, Gateway/Runtime/Tool spans, and end-to-end tracing to later TDD tasks"
    result: "pending"
    evidence: ""
  - criterion: "No app/observability/, tests/observability/, Gateway, Runtime, Tool, or app trace instrumentation files are created"
    result: "pending"
    evidence: ""

failure_examples_tested:
  - example: "Observability Compose profile is missing or invalid"
    result: "triggered"
    evidence: "Negative config validation uses a temporary invalid compose snippet/profile and confirms docker compose config exits non-zero; the temporary file is deleted and not staged."
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "Prompt or docs claim API trace_id, Gateway span, Runtime span, Tool span, or end-to-end trace behavior exists"
    result: "triggered"
    evidence: "Validation searches changed deployment docs/config for active runtime trace claims and fails unless those behaviors are explicitly marked deferred/not implemented."
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"

step_verification_points:
  - step: "Create infra/observability/ with deployment config or documented placeholders"
    result: "pending"
    command: "test -d infra/observability && grep -R -i 'otel\\|collector\\|langfuse' infra/observability"
    evidence: ""
  - step: "Add OTel Collector and Langfuse service wiring to observability profile"
    result: "pending"
    command: "docker compose --profile observability config"
    evidence: ""
  - step: "Negative Compose validation proves config failure is detected"
    result: "pending"
    command: >
      Create a temporary invalid compose snippet/profile for observability, run
      `docker compose -f <temp-file> config`, confirm non-zero exit, then delete
      the temporary file. Do not stage the temporary file.
    evidence: ""
  - step: "Verify existing LANGFUSE_ and OTEL_ env placeholders from P0-INFRA-001 are complete"
    result: "pending"
    command: "grep -cE 'LANGFUSE_|OTEL_' .env.example"
    evidence: "Expected minimum count: >= 4, covering Langfuse endpoint/keys placeholders and OTel endpoint/exporter placeholders or documented equivalents"
  - step: "Verify .env.example changes are incremental and only add OTel Collector-specific gaps"
    result: "pending"
    command: "git diff --cached -- .env.example"
    evidence: ""
  - step: "Verify deployment documentation exists and records deferred instrumentation scope"
    result: "pending"
    command: "grep -i 'observability\\|OpenTelemetry\\|Langfuse\\|deferred\\|not implemented' README.md"
    evidence: ""
  - step: "Verify no app instrumentation or observability test paths are staged"
    result: "pending"
    command: "git diff --cached --name-only | grep -E '^(app/observability|tests/observability|app/runtime|app/gateway)' && exit 1 || echo no_deferred_app_observability_paths"
    evidence: ""
  - step: "Verify no active runtime trace claims are introduced"
    result: "pending"
    command: "grep -R -i 'api health request produces trace_id\\|gateway.*span exists\\|runtime.*span exists\\|tool.*span exists\\|end-to-end trace exists' infra/observability README.md docker-compose.yml .env.example && exit 1 || echo no_active_runtime_trace_claims"
    evidence: ""
  - step: "Stage all files and verify diff"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

touched_paths:
  - infra/observability/
  - docker-compose.yml
  - .env.example
  - README.md

forbidden_paths:
  - app/observability/
  - tests/observability/
  - app/runtime/
  - app/gateway/
  - app/execution_fabric/real_adapters/

stop_conditions:
  - "Working tree is not clean at task start"
  - "Forbidden paths are modified"
  - "docker compose --profile observability config fails"
  - "changed_files cannot be reconciled with staged diff"
  - "Active API trace_id, Gateway span, Runtime span, Tool span, or end-to-end trace claims are made before those components emit spans"
```

## Structured-output baseline applicability

not_applicable — this task does not implement LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
