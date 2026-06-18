# P0-BATCH2-PROMPTS-001 — Batch 2 Per-task Prompt Package Generation

```yaml
task_id: "P0-BATCH2-PROMPTS-001"
branch: "phase0/P0-BATCH2-PROMPTS-001"
task_type: "documentation"
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"
context_strategy: "docs/phase0/CONTEXT_LOADING_STRATEGY.md"
boundary_checklist: "docs/phase0/BOUNDARY_CHECKLIST.md"

method_profile:
  execution_role: "documentation"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "not_applicable"
  reason_for_owner_choice: "This is a documentation/prompt-generation task requiring reading the Phase 0 spec, extracting Batch 2 task definitions, and writing per-task prompt files. Claude Code/MiMo executes and Codex performs independent staged-diff review."

summary: >
  Generate the Batch 2 (engineering skeleton) per-task prompt files based on the
  Phase 0 spec section 10 task definitions. Each prompt must include method_profile,
  depends_on copied exactly from TASK_INDEX.md, structured-output baseline applicability,
  and evidence gates for spike-dependent tasks. Update README_TASK_PROMPTS.md,
  REPOSITORY_CONTEXT_MAP.md, and MANIFEST.md to register the new prompt files.
  Do not execute any Batch 2 implementation task.

objective: >
  Generate 8 Batch 2 per-task prompt files (P0-INFRA-001 through P0-INFRA-008)
  and the P0-BATCH2-PROMPTS-001 meta-prompt file, with correct depends_on,
  method_profile, structured-output baseline applicability, and spike evidence
  gates. Update README_TASK_PROMPTS.md, REPOSITORY_CONTEXT_MAP.md, and
  MANIFEST.md to register the new prompt inventory. Do not execute any Batch 2
  implementation task.

acceptance_criteria:
  - criterion: "8 Batch 2 task prompt files created: P0-INFRA-001.md through P0-INFRA-008.md"
    result: "pending"
    evidence: ""
  - criterion: "P0-BATCH2-PROMPTS-001.md task prompt file created"
    result: "pending"
    evidence: ""
  - criterion: "Each prompt file contains method_profile with all required fields"
    result: "pending"
    evidence: ""
  - criterion: "Each prompt file contains exact depends_on from TASK_INDEX.md"
    result: "pending"
    evidence: ""
  - criterion: "Every prompt contains 'Structured-output baseline applicability' section"
    result: "pending"
    evidence: ""
  - criterion: "P0-INFRA-004 gates on P0-SPIKE-003 evidence and ADR-P0-SPIKE-003"
    result: "pending"
    evidence: ""
  - criterion: "P0-INFRA-005 gates on P0-SPIKE-004 evidence and ADR-P0-SPIKE-004"
    result: "pending"
    evidence: ""
  - criterion: "P0-INFRA-006 splits scope A (deployment baseline) and scope B (instrumentation code)"
    result: "pending"
    evidence: ""
  - criterion: "README_TASK_PROMPTS.md updated for Batch 2"
    result: "pending"
    evidence: ""
  - criterion: "REPOSITORY_CONTEXT_MAP.md updated if prompt inventory changed"
    result: "pending"
    evidence: ""
  - criterion: "MANIFEST.md updated with new prompt files"
    result: "pending"
    evidence: ""
  - criterion: "No TASK_INDEX.md, blueprint, ADR, historical Task Record, or boot file modified"
    result: "pending"
    evidence: ""
  - criterion: "No dependency or lockfile changes"
    result: "pending"
    evidence: ""
  - criterion: "staged files are only prompt files, README_TASK_PROMPTS.md, REPOSITORY_CONTEXT_MAP.md, and MANIFEST.md"
    result: "pending"
    evidence: ""

failure_examples_tested:
  - example: "Generated prompt missing method_profile field"
    result: "not_applicable"
    evidence: ""
    not_applicable_reason: "This documentation-generation task verifies required prompt fields through step_verification_points; it does not create an independent runtime failure path."
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "Generated prompt has wrong depends_on copied from spec instead of TASK_INDEX.md"
    result: "not_applicable"
    evidence: ""
    not_applicable_reason: "This documentation-generation task verifies depends_on through TASK_INDEX comparison in step_verification_points; it does not create an independent runtime failure path."
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"

step_verification_points:
  - step: "Preflight: confirm Batch 2 IDs and dependency order match TASK_INDEX.md"
    result: "passed"
    command: "manual comparison of TASK_INDEX.md Batch 2 section"
    evidence: "8 task IDs match exactly with correct depends_on"
  - step: "Generate 9 prompt files"
    result: "pending"
    command: "ls docs/phase0/tasks/P0-BATCH2-PROMPTS-001.md docs/phase0/tasks/P0-INFRA-00[1-8].md"
    evidence: ""
  - step: "Verify method_profile presence in all prompts"
    result: "pending"
    command: "grep -l 'method_profile' docs/phase0/tasks/P0-INFRA-00[1-8].md docs/phase0/tasks/P0-BATCH2-PROMPTS-001.md"
    evidence: ""
  - step: "Verify structured-output baseline section in all prompts"
    result: "pending"
    command: "grep -l 'Structured-output baseline applicability' docs/phase0/tasks/P0-INFRA-00[1-8].md docs/phase0/tasks/P0-BATCH2-PROMPTS-001.md"
    evidence: ""
  - step: "Verify depends_on matches TASK_INDEX.md"
    result: "pending"
    command: "manual comparison"
    evidence: ""
  - step: "Verify P0-INFRA-004/005 spike evidence gates"
    result: "pending"
    command: "grep -c 'P0-SPIKE-003' docs/phase0/tasks/P0-INFRA-004.md && grep -c 'P0-SPIKE-004' docs/phase0/tasks/P0-INFRA-005.md"
    evidence: ""
  - step: "Verify no forbidden paths modified"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""
  - step: "Update MANIFEST.md, README_TASK_PROMPTS.md, REPOSITORY_CONTEXT_MAP.md"
    result: "pending"
    command: "git diff --cached MANIFEST.md docs/phase0/tasks/README_TASK_PROMPTS.md docs/phase0/REPOSITORY_CONTEXT_MAP.md"
    evidence: ""

touched_paths:
  - docs/phase0/tasks/P0-BATCH2-PROMPTS-001.md
  - docs/phase0/tasks/P0-INFRA-001.md
  - docs/phase0/tasks/P0-INFRA-002.md
  - docs/phase0/tasks/P0-INFRA-003.md
  - docs/phase0/tasks/P0-INFRA-004.md
  - docs/phase0/tasks/P0-INFRA-005.md
  - docs/phase0/tasks/P0-INFRA-006.md
  - docs/phase0/tasks/P0-INFRA-007.md
  - docs/phase0/tasks/P0-INFRA-008.md
  - docs/phase0/tasks/README_TASK_PROMPTS.md
  - docs/phase0/REPOSITORY_CONTEXT_MAP.md
  - MANIFEST.md

forbidden_paths:
  - docs/blueprint/**
  - docs/adr/**
  - docs/phase0/TASK_INDEX.md
  - docs/phase0/task_logs/*.yaml (historical records)
  - docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
  - docs/dev/task_record_schema.yaml
  - CLAUDE.md
  - AGENTS.md
  - app/**
  - web/**
  - pyproject.toml
  - uv.lock
  - package.json
  - pnpm-lock.yaml
  - .github/**

stop_conditions:
  - "Batch 2 IDs or dependency order differ from TASK_INDEX.md"
  - "Forbidden paths are modified"
  - "Dependency or lockfile changes detected"
  - "Missing method_profile in any generated prompt"
```

## Task Description

Phase 0 spec section 10 defines 8 Batch 2 (engineering skeleton) tasks. The TASK_INDEX.md Batch 2 prompt gate requires generating `docs/phase0/tasks/<task_id>.md` files before Batch 2 execution begins. This task generates those prompt files.

### What to create

1. **9 prompt files** in `docs/phase0/tasks/`:
   - `P0-BATCH2-PROMPTS-001.md` (this file)
   - `P0-INFRA-001.md` through `P0-INFRA-008.md`

### What to update

2. **README_TASK_PROMPTS.md** — Update Batch 2 status
3. **REPOSITORY_CONTEXT_MAP.md** — Update prompt inventory/status if needed
4. **MANIFEST.md** — Register new prompt files

### What NOT to do

- Do not modify TASK_INDEX.md, blueprints, ADRs, historical Task Records, boot files, or dependency files
- Do not execute any Batch 2 implementation task
- Do not create a passed Task Record or INDEX.md row before independent review
- No commit, no push, no merge. Stage for review only.

## Structured-output baseline applicability

not_applicable — this task does not implement LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
