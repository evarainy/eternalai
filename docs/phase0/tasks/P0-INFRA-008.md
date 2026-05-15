# P0-INFRA-008 — Single-task Prompt

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
task_id: P0-INFRA-008
branch: "phase0/P0-INFRA-008"
title: Internal Dependency Mirror and Dependency Allowlist Baseline
type: infrastructure
depends_on:
  - P0-PREP-003
priority: prerequisite
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "mixed"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "mixed"
  reason_for_owner_choice: >
    This task has two scopes. PDR scope: dependency mirror/allowlist policy and
    decision baseline (docs/dev/dependency_policy.md). TDD scope: locally runnable
    dependency checker behavior and negative checker validation. Claude Code/MiMo
    executes both scopes; Codex performs independent review. CI workflow integration
    belongs to P0-INFRA-007, not this task.

scope_split:
  scope_a_policy:
    description: "Dependency mirror/allowlist policy baseline: docs/dev/dependency_policy.md and .env.example placeholder documentation"
    method: "PDR"
  scope_b_checker:
    description: "Locally runnable dependency checker script with negative validation. CI wiring is deferred to P0-INFRA-007."
    method: "TDD"

objective: >
  Establish dependency installation rules for Python (uv) and frontend (pnpm)
  under enterprise intranet mirrors. Define a repository-controlled dependency
  allowlist and checker script to prevent AI agents from adding unapproved external
  dependencies that would cause execution stalls or technology stack drift.

structured_output_baseline_applicability: "not_applicable — this task does not implement LLM structured output."

deliverable:
  - docs/dev/dependency_policy.md (including a structured "Dependency Allowlist" section)
  - .env.example (PIP_INDEX_URL / UV_INDEX_URL / npm registry placeholder documentation)
  - scripts/check_dependencies.py or equivalent checker

deterministic_policy_input:
  file: "docs/dev/dependency_policy.md"
  required_section: "## Dependency Allowlist"
  required_fields: "ecosystem, package, allowed_version_range, dependency_group, mirror_status, approval_source"
  checker_rule: >
    scripts/check_dependencies.py must parse this one repository-controlled
    allowlist source. If a future dedicated allowlist file is introduced, this
    policy file must name it explicitly and the checker must fail when both
    sources conflict or neither source exists.

constraints:
  - Do not require real intranet mirror addresses
  - Do not write real credentials, tokens, or private mirror auth
  - All new dependencies must be represented in the deterministic allowlist source with mirror_status recorded
  - Spike dependencies must not enter production dependency groups
  - This task defines policy and checker infrastructure only; no dependency or lockfile edits in P0-INFRA-008
  - Future dependency mirror/allowlist policy refinement happens in later tasks
  - CI workflow integration for the checker belongs to P0-INFRA-007; this task only creates the locally runnable checker
  - The checker must not infer policy from prose outside the named allowlist section unless that section explicitly points to a dedicated allowlist file

acceptance_criteria:
  - criterion: "docs/dev/dependency_policy.md documents Python uv + intranet PyPI mirror usage rules"
    result: "pending"
    evidence: ""
  - criterion: "docs/dev/dependency_policy.md documents frontend pnpm + intranet npm mirror usage rules"
    result: "pending"
    evidence: ""
  - criterion: "docs/dev/dependency_policy.md contains a structured Dependency Allowlist section with required fields"
    result: "pending"
    evidence: ""
  - criterion: "Dependency allowlist checker script reads the deterministic policy input and runs locally (exits zero on clean state, non-zero on undeclared dependency)"
    result: "pending"
    evidence: ""
  - criterion: "docs/dev/dependency_policy.md documents future CI integration handoff to P0-INFRA-007; actual CI workflow failure behavior is deferred to P0-INFRA-007"
    result: "pending"
    evidence: ""
  - criterion: "Repository-controlled allowlist/checker input is deterministic; future executors do not need to guess where policy lives"
    result: "pending"
    evidence: ""

failure_examples_tested:
  - example: "AI agent adds a dependency not in the allowlist"
    result: "triggered"
    evidence: "Negative checker validation step: run the checker against a temporary manifest or input listing an undeclared dependency; confirm the checker exits non-zero and reports the undeclared dependency; then delete the temp input. This proves the checker reads the deterministic allowlist source, not just that it exists."
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "Lockfile changes without corresponding pyproject.toml or package.json change"
    result: "not_applicable"
    evidence: "P0-INFRA-008 defines the deterministic allowlist input and local checker baseline; full lockfile drift enforcement is deferred to P0-INFRA-007 CI/checker wiring."
    not_applicable_reason: "Full lockfile drift testing requires dependency/lockfile mutation and CI wiring, which belong to P0-INFRA-007 or later dependency-changing execution tasks"
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"

step_verification_points:
  - step: "Create docs/dev/dependency_policy.md"
    result: "pending"
    command: "test -f docs/dev/dependency_policy.md"
    evidence: ""
  - step: "Verify deterministic allowlist source and required fields"
    result: "pending"
    command: "grep -E '^## Dependency Allowlist|ecosystem|package|allowed_version_range|dependency_group|mirror_status|approval_source' docs/dev/dependency_policy.md"
    evidence: ""
  - step: "Update .env.example with mirror placeholder documentation"
    result: "pending"
    command: "grep -i 'PIP_INDEX_URL\|UV_INDEX_URL\|npm.*registry' .env.example"
    evidence: ""
  - step: "Create scripts/check_dependencies.py"
    result: "pending"
    command: "test -f scripts/check_dependencies.py"
    evidence: ""
  - step: "Run checker script against clean state (positive case)"
    result: "pending"
    command: "python scripts/check_dependencies.py"
    evidence: ""
  - step: "Negative checker validation: run checker against known-bad input (undeclared dependency) and confirm non-zero exit"
    result: "pending"
    command: >
      Create a temporary manifest or checker input listing an undeclared dependency,
      run the checker against it, confirm non-zero exit and error message identifying
      the undeclared dependency and deterministic allowlist source, then delete the
      temp input. Do not stage the temp input.
    evidence: ""
  - step: "Stage all files and verify diff"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

touched_paths:
  - docs/dev/dependency_policy.md
  - .env.example
  - scripts/

forbidden_paths:
  - app/
  - web/src/
  - app/execution_fabric/real_adapters/

stop_conditions:
  - "Working tree is not clean at task start"
  - "Forbidden paths are modified"
  - "changed_files cannot be reconciled with staged diff"
```

## Structured-output baseline applicability

not_applicable — this task does not implement LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
