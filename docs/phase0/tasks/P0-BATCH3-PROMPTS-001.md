# P0-BATCH3-PROMPTS-001 — Batch 3 Interface-Contract Prompt Generation Gate

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/tasks/README_TASK_PROMPTS.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md (source of truth for Batch 3 task definitions; do not paste in full)
- docs/phase0/PHASE1_TECHNICAL_BASELINE.md (structured-output baseline constraints)

## Global hard rules

- Execute only this task_id.
- Start this task only after all depends_on tasks have been reviewed, approved, and merged to the Phase 0 base branch.
- Do not modify frozen blueprint files.
- Do not implement Phase 1 features.
- Do not add unapproved dependencies.
- Do not weaken tests to pass.
- Stop after Unified Task Record and wait for human confirmation.
- No commit, no push, no merge.
- Any execution/pass claim must include the exact command, exit code, and evidence output in the Task Record.
- Independent staged review is required before any commit, push, or merge.

## Task YAML

```yaml
task_id: P0-BATCH3-PROMPTS-001
branch: "phase0/P0-BATCH3-PROMPTS-001"
title: Batch 3 Interface-Contract Prompt Generation Gate
type: documentation
depends_on:
  - P0-INFRA-002
  - P0-INFRA-003B
priority: P0
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "documentation"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "not_applicable"
  reason_for_owner_choice: >
    Documentation task: generating Batch 3 per-task prompts for interface-contract
    freezing. Claude Code/MiMo generates prompt files based on Phase 0 spec;
    Codex reviews for completeness and constraint preservation. Method is
    not_applicable because this is prompt generation, not code implementation.

objective: >
  Generate per-task prompt files under docs/phase0/tasks/ for Batch 3
  interface-contract tasks (P0-DOMAIN-001a through P0-DOMAIN-011a). Carry
  forward Phase 0 constraints as a checklist — not as a full specification dump.
  Prompt generation may proceed before downstream implementation prerequisites
  are complete, because generating a prompt is a documentation activity.
  However, each generated prompt must declare its own dependencies and must not
  authorize execution before those dependencies are satisfied. This task must not
  claim that downstream implementation prerequisites have passed. Do not broadly
  rewrite existing Batch 3 rows in TASK_INDEX.md unless dependency consistency
  requires it. This is a gate task: it produces prompt files and registers them,
  not implementation code.

constraints_to_carry_forward:
  - "Context Assembly minimum input boundary"
  - "Capability Summary injection rules"
  - "Intent to Capability minimum validation path"
  - "structured-output failure Plan B"
  - "no_capability_found terminal state"
  - "clarification_needed terminal state"
  - "validation_failed outcome"
  - "manual_review_needed outcome"
  - "Phase 1 user-value boundary"

structured_output_baseline_applicability: "not_applicable — this task generates documentation prompts, not LLM structured output."

deliverable:
  - docs/phase0/tasks/P0-DOMAIN-001a.md through P0-DOMAIN-011a.md (Batch 3 per-task prompts)
  - TASK_INDEX.md registration for Batch 3 prompt rows (if not already present)
  - README_TASK_PROMPTS.md Batch 3 prompt inventory update
  - MANIFEST.md registration for generated prompt files (scoped update only)

constraints:
  - Must not generate Batch 3 implementation code
  - Must not rewrite existing completed Task Records
  - Must not modify frozen blueprint files
  - Must carry forward structured-output baseline: raw OpenAI-compatible SDK, response_format={"type":"json_object"}, Pydantic model_validate, Literal[...] enum validation
  - Must not change the structured-output baseline
  - Must not reopen instructor or PydanticAI default decisions
  - Must not broadly rewrite Batch 3 rows in TASK_INDEX.md beyond what dependency consistency requires
  - Each generated prompt must include method_profile per docs/phase0/tasks/README_TASK_PROMPTS.md rules
  - Each generated prompt must include no-commit/no-push rules
  - MANIFEST.md may be updated only to register generated Batch 3 prompt files or task-index documentation; unrelated MANIFEST.md rewrites are forbidden

acceptance_criteria:
  - criterion: "Batch 3 per-task prompt files (P0-DOMAIN-001a through P0-DOMAIN-011a) generated under docs/phase0/tasks/"
    result: "pending"
    evidence: ""
  - criterion: "Each prompt carries forward the required constraints checklist"
    result: "pending"
    evidence: ""
  - criterion: "Each prompt includes method_profile"
    result: "pending"
    evidence: ""
  - criterion: "README_TASK_PROMPTS.md updated with Batch 3 prompt inventory"
    result: "pending"
    evidence: ""
  - criterion: "TASK_INDEX.md Batch 3 rows are consistent with generated prompts"
    result: "pending"
    evidence: ""
  - criterion: "No implementation code generated"
    result: "pending"
    evidence: ""
  - criterion: "Final staged-diff evidence is recorded and Task Record changed_files exactly matches git diff --cached --name-only"
    result: "pending"
    evidence: ""

failure_examples_tested:
  - example: "Batch 3 task implementation prerequisite not yet complete"
    result: "not_applicable"
    evidence: >
      Prompt generation is a documentation task. Generating prompts before
      downstream implementation prerequisites are complete is permitted, provided
      each generated prompt declares its own dependencies and does not authorize
      execution before those dependencies are satisfied. This task must not claim
      prerequisite implementation evidence exists when it does not.
    not_applicable_reason: "Prompt generation requires spec text, not prerequisite implementation."
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "Generated prompt missing required constraint"
    result: "triggered"
    evidence: "Validation checks each generated prompt for the required constraints checklist items and fails if any are missing."
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "Generated prompt removes dependency gates or authorizes execution before dependencies are satisfied"
    result: "triggered"
    evidence: "Each generated prompt must declare depends_on and must not include wording that permits execution before dependencies pass."
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"

step_verification_points:
  - step: "Preflight: confirm branch and clean working tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify TASK_INDEX.md Batch 3 rows exist"
    result: "pending"
    command: "Select-String -Path docs/phase0/TASK_INDEX.md -Pattern 'P0-DOMAIN-001a'"
    evidence: ""
  - step: "Generate Batch 3 per-task prompt files"
    result: "pending"
    command: "Get-ChildItem docs/phase0/tasks/P0-DOMAIN-*a.md | Measure-Object | Select-Object -ExpandProperty Count"
    evidence: ""
  - step: "Verify each prompt includes method_profile"
    result: "pending"
    command: "Get-ChildItem docs/phase0/tasks/P0-DOMAIN-*a.md | ForEach-Object { if ((Get-Content $_.FullName) -notmatch 'method_profile:') { throw 'Missing method_profile in ' + $_.Name } }; Write-Output 'PASSED'"
    evidence: ""
  - step: "Verify README_TASK_PROMPTS.md Batch 3 inventory updated"
    result: "pending"
    command: "Select-String -Path docs/phase0/tasks/README_TASK_PROMPTS.md -Pattern 'Batch 3'"
    evidence: ""
  - step: "Final branch check"
    result: "pending"
    command: "git branch --show-current"
    evidence: ""
  - step: "Final staged/working-tree status"
    result: "pending"
    command: "git status --short"
    evidence: ""
  - step: "Final staged file list"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""
  - step: "Final staged diff stat"
    result: "pending"
    command: "git diff --cached --stat"
    evidence: ""
  - step: "Final staged diff whitespace/path check"
    result: "pending"
    command: "git diff --cached --check"
    evidence: ""
  - step: "Final untracked-file check"
    result: "pending"
    command: "git ls-files --others --exclude-standard"
    evidence: ""
  - step: "Final forbidden-path scan"
    result: "pending"
    command: "if (git diff --cached --name-only | Select-String -Pattern '^docs/blueprint/','^app/','^web/','(^|/)package.json$','(^|/)pnpm-lock.yaml$','^pyproject.toml$','^uv.lock$','^docker-compose.yml$','^CLAUDE.md$','^AGENTS.md$','^\\.github/') { throw 'Forbidden staged path detected' }"
    evidence: ""
  - step: "Verify Task Record changed_files matches staged diff exactly"
    result: "pending"
    command: "$recordFile = Get-ChildItem docs/phase0/task_logs/P0-BATCH3-PROMPTS-001_*.yaml | Where-Object { $_.Name -match '_passed\\.yaml$|_blocked\\.yaml$|_failed\\.yaml$' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if (-not $recordFile) { throw 'P0-BATCH3-PROMPTS-001 Task Record not found' }; $staged = @(git diff --cached --name-only); $lines = Get-Content $recordFile.FullName; $start = [Array]::IndexOf($lines, 'changed_files:'); $end = [Array]::IndexOf($lines, 'touched_paths_confirmed: true'); if ($start -lt 0 -or $end -le $start) { throw 'changed_files section not found' }; $record = @($lines[($start + 1)..($end - 1)] | Where-Object { $_ -match '^  - ' } | ForEach-Object { $_ -replace '^  - ', '' }); if ($staged.Count -ne $record.Count) { throw 'changed_files count mismatch' }; for ($i = 0; $i -lt $staged.Count; $i++) { if ($staged[$i] -ne $record[$i]) { throw 'changed_files order mismatch' } }"
    evidence: ""

touched_paths:
  - docs/phase0/tasks/P0-DOMAIN-001a.md through P0-DOMAIN-011a.md
  - docs/phase0/tasks/README_TASK_PROMPTS.md
  - docs/phase0/task_logs/P0-BATCH3-PROMPTS-001_<timestamp>_passed.yaml
  - docs/phase0/task_logs/INDEX.md

forbidden_paths:
  - docs/blueprint/**
  - app/**
  - web/**
  - package.json
  - pnpm-lock.yaml
  - pyproject.toml
  - uv.lock
  - docker-compose.yml
  - .github/**
  - CLAUDE.md
  - AGENTS.md
  - docs/phase0/task_logs/** except docs/phase0/task_logs/P0-BATCH3-PROMPTS-001_<timestamp>_passed.yaml

allowed_paths_with_scope:
  - path: "MANIFEST.md"
    scope: "Update only to register generated Batch 3 prompt files or task-index documentation. Unrelated rewrites forbidden."

stop_conditions:
  - "Branch is not phase0/P0-BATCH3-PROMPTS-001"
  - "Working tree is dirty at implementation start"
  - "Forbidden paths are modified"
  - "Implementation code is generated instead of prompt documentation"
  - "changed_files cannot match staged diff exactly"
  - "Commit, push, or merge is attempted before independent staged review"
```

## Batch 3 prompt constraints to carry forward

Each generated P0-DOMAIN-*a prompt must include a constraints checklist section that references:
1. Context Assembly minimum input boundary
2. Capability Summary injection rules
3. Intent to Capability minimum validation path
4. structured-output failure Plan B (raw OpenAI-compatible SDK, response_format, Pydantic model_validate, Literal enum)
5. no_capability_found terminal state
6. clarification_needed terminal state
7. validation_failed outcome
8. manual_review_needed outcome
9. Phase 1 user-value boundary

## Structured-output baseline applicability

not_applicable — this task generates documentation prompts, not LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
