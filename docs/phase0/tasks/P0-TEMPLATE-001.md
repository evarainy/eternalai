# P0-TEMPLATE-001 — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Out-of-band notice

P0-TEMPLATE-001 is an out-of-band template/documentation upgrade task, not part of Batch DAG. It upgrades the task execution template to support Batch 2 / Phase 1 method_profile, evidence rules, and role boundaries.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md (the file being upgraded)
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- docs/phase0/CONTEXT_LOADING_STRATEGY.md
- docs/phase0/REPOSITORY_CONTEXT_MAP.md
- docs/phase0/CODING_STYLE_BASELINE.md
- docs/phase0/PHASE1_TECHNICAL_BASELINE.md
- docs/dev/task_record_schema.yaml
- docs/phase0/task_logs/INDEX.md

## Global hard rules

- Execute only this task_id.
- Do not modify frozen blueprint files.
- Do not modify docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md.
- Do not modify docs/phase0/CONTEXT_LOADING_STRATEGY.md.
- Do not modify docs/phase0/CODING_STYLE_BASELINE.md.
- Do not modify docs/phase0/TASK_INDEX.md.
- Do not modify docs/phase0/BOUNDARY_CHECKLIST.md.
- Do not modify docs/phase0/PHASE1_TECHNICAL_BASELINE.md.
- Do not modify docs/dev/task_record_schema.yaml.
- Do not modify existing Batch 0/1 task prompts (P0-PREP-*, P0-SPIKE-*, P0-RULES-*, P0-STYLE-*, P0-NAV-*).
- Do not modify historical closed task records.
- Do not modify CLAUDE.md or AGENTS.md.
- Do not add unapproved dependencies.
- No commit, no push, no merge.
- Stage for review only.
- Stop after Unified Task Record and wait for human confirmation and Codex review.

## Non-goals / deferred tasks

- Do not rewrite Batch 0/1 per-task prompts to add method_profile.
- Do not generate Batch 2 per-task prompts (that is a separate task).
- Do not modify the task_record_schema.yaml.
- Do not create new supporting documents beyond what is listed in touched_paths.
- Do not reopen P0-SPIKE-001 / 002 / 007 technical conclusions.
- Do not change Phase 1 structured-output baseline.

## Task YAML

```yaml
task_id: P0-TEMPLATE-001
title: Upgrade task template with method_profile, evidence rules, and role boundaries
type: documentation
method_profile:
  execution_role: "documentation"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "PDR"
  reason_for_owner_choice: "Template/documentation upgrade with repository changes; Claude Code/MiMo handles file edits and validation, Codex performs independent staged-diff review."
objective: >
  Upgrade CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md from v1.0.11 to v2.0.0.
  Add method_profile YAML field definition (execution_role, execution_owner,
  review_owner, review_mode, method, reason_for_owner_choice).
  Add execution_owner selection rules (Claude Code default, Codex override
  for complex/core tasks, review independence requirement).
  Add Engineering Method Selection speed-reference table with 5 methods
  (PDR, BDD, TDD, mixed, not_applicable).
  Add review_mode rules (repository-changing tasks default codex_review).
  Add evidence rules (real commands, changed_files exact match,
  YAML safe_load, UniqueKeyLoader, not_applicable completeness,
  package/archive rules, git_commit_sha deferred convention).
  Add context hygiene rules (progressive loading, selective section loading,
  no full blueprint/ADR/task-log default loading).
  Add execution workflow rules (no commit/push/merge, stage for review,
  changed_files sequencing, stale record cleanup).
  Add role boundary rules (task-specific owners, executor cannot be sole
  approver, review session read-only, Superpowers advisory only).
  Upgrade Batch 2+ prompt gate with method_profile requirement and
  generation rules.
  Update README_TASK_PROMPTS.md with Batch 2+ notes.
  Update MANIFEST.md. Update task_logs/INDEX.md.
  All additions are compact inline rules + references to existing
  supporting documents; no content duplication.
constraints:
  - documentation-only; no code changes
  - do not modify docs/blueprint/ frozen files
  - do not modify supporting documents (ROLE_AND_METHOD_GUARDRAILS.md,
    CONTEXT_LOADING_STRATEGY.md, CODING_STYLE_BASELINE.md,
    task_record_schema.yaml, BOUNDARY_CHECKLIST.md, TASK_INDEX.md,
    PHASE1_TECHNICAL_BASELINE.md)
  - do not modify existing Batch 0/1 task prompts
  - do not modify historical closed task records
  - do not modify CLAUDE.md or AGENTS.md
  - do not generate Batch 2 per-task prompts
  - do not reopen P0-SPIKE-001/002/007 conclusions
  - template must remain under 280 lines
  - all new content must reference existing docs, not duplicate them
  - backwards-compatible: Batch 1 task prompts remain valid
  - no commit / push / merge; stage for review only
acceptance_criteria:
  - AC-1: CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md version header says v2.0.0
  - AC-2: Template contains method_profile YAML field definition with
    execution_role, execution_owner, review_owner, review_mode, method,
    reason_for_owner_choice
  - AC-3: Template contains execution_owner selection rules (default workflow,
    Codex override, review independence)
  - AC-4: Template contains Engineering Method Selection speed-reference
    table with 5 rows (PDR, BDD, TDD, mixed, not_applicable)
  - AC-5: Template contains review_mode rules stating repository-changing
    tasks default to codex_review
  - AC-6: Template contains "证据规则" section with 7 rules
    (commands, changed_files, stat evidence, YAML safe_load, UniqueKeyLoader,
    not_applicable completeness, git_commit_sha deferred)
  - AC-7: Template contains "执行中工作流规则" section with
    no-commit/no-push/no-merge rule, stage for review,
    changed_files sequencing, stale record handling
  - AC-8: Template contains "审查与角色边界" section with task-specific
    owners, executor ≠ sole approver, review session read-only,
    Superpowers advisory only
  - AC-9: Template contains context hygiene paragraph referencing
    CODING_STYLE_BASELINE.md and REPOSITORY_CONTEXT_MAP.md
  - AC-10: Template contains expanded Batch 2+ prompt gate with
    method_profile requirement and generation rules covering all required
    items: method_profile, allowed/forbidden paths, evidence requirements,
    review_mode, execution_owner, review_owner, baseline references,
    task_prompt_incomplete behavior
  - AC-11: Template line count < 280
  - AC-12: Template still contains all v1.0.11 structural content
    (backwards-compatible)
  - AC-13: docs/phase0/tasks/P0-TEMPLATE-001.md exists with
    complete task YAML including method_profile
  - AC-14: README_TASK_PROMPTS.md updated with Batch 2+ notes
    (method_profile, codex_review, execution_owner/review_owner,
    evidence rules, no backfill)
  - AC-15: MANIFEST.md contains P0-TEMPLATE-001.md entry
  - AC-16: task_logs/INDEX.md contains exactly one P0-TEMPLATE-001 row;
    row task_id/date/result match actual Task Record;
    corresponding Task Record YAML exists on disk;
    INDEX row count matches actual task-record YAML count
  - AC-17: Task Record YAML passes safe_load
  - AC-18: Task Record passes UniqueKeyLoader duplicate-key check
  - AC-19: Task Record changed_files exactly matches
    git diff --cached --name-only
  - AC-20: No empty strings in not_applicable, ci_evidence,
    rollback_or_failure_record, package_scope, package_evidence
  - AC-21: package_confirmation_status is not_applicable
  - AC-22: Forbidden paths not modified
  - AC-23: No supporting documents modified
  - AC-24: Historical task records not modified
  - AC-25: Phase 1 structured-output baseline not changed
touched_paths:
  - docs/phase0/tasks/P0-TEMPLATE-001.md
  - docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
  - docs/phase0/tasks/README_TASK_PROMPTS.md
  - docs/phase0/task_logs/INDEX.md
  - MANIFEST.md
  - docs/phase0/REPOSITORY_CONTEXT_MAP.md
  - docs/phase0/task_logs/P0-TEMPLATE-001_*.yaml
forbidden_paths:
  - docs/blueprint/
  - docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
  - docs/phase0/CONTEXT_LOADING_STRATEGY.md
  - docs/phase0/CODING_STYLE_BASELINE.md
  - docs/phase0/TASK_INDEX.md
  - docs/phase0/BOUNDARY_CHECKLIST.md
  - docs/phase0/PHASE1_TECHNICAL_BASELINE.md
  - docs/dev/task_record_schema.yaml
  - docs/adr/
  - app/
  - runtime/
  - gateway/
  - execution_fabric/
  - experiments/
  - pyproject.toml
  - requirements.txt
  - uv.lock
  - package.json
  - pnpm-lock.yaml
  - CLAUDE.md
  - AGENTS.md
  - existing Batch 0/1 task prompts
  - historical closed task records
validation:
  - git status --short
  - git diff --cached --name-only
  - git diff --cached --stat
  - git diff --cached --check
  - Task Record YAML safe_load
  - Task Record UniqueKeyLoader duplicate-key check
  - Task Record empty-string check
  - Task Record changed_files == git diff --cached --name-only
  - Task Record stat evidence == git diff --cached --stat
  - Select-String check for "v2.0.0"
  - Select-String check for "method_profile"
  - Select-String check for "execution_owner"
  - Select-String check for "review_owner"
  - Select-String check for "证据规则"
  - Select-String check for "执行中工作流规则"
  - Select-String check for "审查与角色边界"
  - Select-String check for "No commit" or "no commit"
  - Select-String check for "CODING_STYLE_BASELINE"
  - Select-String check for "REPOSITORY_CONTEXT_MAP"
  - Select-String check for "PDR" and "BDD" and "TDD" and "mixed" and "not_applicable"
  - Select-String check for "Batch 2" and "prompt gate"
  - Line count check: (Get-Content ... | Measure-Object -Line).Lines < 280
  - Select-String check for all v1.0.11 section headers still present
  - Select-String check README_TASK_PROMPTS.md: "method_profile", "codex_review", "execution_owner", "review_owner"
  - Select-String check MANIFEST.md: "P0-TEMPLATE-001"
  - task_logs/INDEX row count == actual YAML count on disk
  - forbidden path check
  - supporting document modification check
  - historical task record modification check
  - Phase 1 baseline modification check
```

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.

Note: This task upgrades the template itself. The executing agent should read the current v1.0.11 template as the base, apply the changes described in this prompt, and produce the v2.0.0 template.

## Quality gate

This task's quality must be verified against:

1. **Reusability**: Can future Batch 2/Phase 1 tasks use the upgraded template without rewriting rules?
2. **Specificity**: Are method selection rules concrete enough for agents to follow without ambiguity?
3. **Evidence strength**: Do rules prevent known Phase 0 mistakes (changed_files mismatch, stale records, incomplete not_applicable, false package claims, fabricated evidence)?
4. **Context hygiene**: Does the template prevent context overload (progressive loading, no full blueprint default)?
5. **Role clarity**: Does it separate execution owner from review owner while allowing Codex to execute complex/core tasks?
6. **Operational usability**: Can a future agent follow the template without asking for obvious information?
7. **Non-regression**: Does it preserve Phase 1 structured-output baseline and avoid reopening spike conclusions?
8. **Brevity**: Is the template concise enough for frequent loading (< 280 lines)?
