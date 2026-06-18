# P0-EXPERIENCE-001 — AI Agent Engineering Lessons Reference

```yaml
task_id: "P0-EXPERIENCE-001"
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
  reason_for_owner_choice: "This is a documentation/lessons reference task requiring local file edits, index/map updates, and evidence validation; Claude Code/MiMo executes and Codex performs independent staged-diff review."

summary: >
  Create an independent engineering lessons-learned reference file that captures
  real Phase 0 pitfalls and anti-patterns. Update navigation and manifest files
  to register it as an on-demand resource. This file must NOT become a default
  full-context load.

acceptance_criteria:
  - criterion: "docs/dev/AI_AGENT_ENGINEERING_LESSONS.md created with prominent loading policy header"
    result: "pending"
    evidence: ""
  - criterion: "Lessons file contains at least 10 of 11 planned sections with actionable rules"
    result: "pending"
    evidence: ""
  - criterion: "Loading policy states: do not load full file by default, load only relevant sections"
    result: "pending"
    evidence: ""
  - criterion: "docs/phase0/REPOSITORY_CONTEXT_MAP.md updated with lessons file as on-demand entry"
    result: "pending"
    evidence: ""
  - criterion: "MANIFEST.md registers new files (lessons file + task prompt)"
    result: "pending"
    evidence: ""
  - criterion: "docs/phase0/task_logs/INDEX.md has exactly one P0-EXPERIENCE-001 row"
    result: "pending"
    evidence: ""
  - criterion: "INDEX data row count equals actual YAML file count in task_logs/"
    result: "pending"
    evidence: ""
  - criterion: "Task Record YAML passes safe_load and UniqueKeyLoader checks"
    result: "pending"
    evidence: ""
  - criterion: "changed_files matches git diff --cached --name-only exactly"
    result: "pending"
    evidence: ""
  - criterion: "No forbidden paths or historical records modified"
    result: "pending"
    evidence: ""
  - criterion: "Lessons file does not exceed 280 lines"
    result: "pending"
    evidence: ""

failure_examples_tested:
  - example: "Lessons file becomes vague or too broad to use as a searchable reference"
    result: "not_applicable"
    evidence: "Documentation quality is verified by content review during plan and execution phases; each section contains specific actionable rules with 'Why' explanations"
    not_applicable_reason: "Documentation quality is verified by content review, not by automated test"
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A — content quality is assessed by human/Codex review, not by automated test"
  - example: "Context map implies lessons file is mandatory full-load"
    result: "not_applicable"
    evidence: "Verified by grep for 'on demand' and 'Do not load' in REPOSITORY_CONTEXT_MAP.md during execution"
    not_applicable_reason: "Verified by grep check during execution"
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A — loading policy verification is a one-time documentation check"

step_verification_points:
  - step: "Create docs/dev/AI_AGENT_ENGINEERING_LESSONS.md"
    result: "pending"
    command: "test -f docs/dev/AI_AGENT_ENGINEERING_LESSONS.md"
    evidence: ""
  - step: "Create docs/phase0/tasks/P0-EXPERIENCE-001.md"
    result: "pending"
    command: "test -f docs/phase0/tasks/P0-EXPERIENCE-001.md"
    evidence: ""
  - step: "Update REPOSITORY_CONTEXT_MAP.md"
    result: "pending"
    command: "grep 'AI_AGENT_ENGINEERING_LESSONS' docs/phase0/REPOSITORY_CONTEXT_MAP.md"
    evidence: ""
  - step: "Update MANIFEST.md"
    result: "pending"
    command: "grep 'AI_AGENT_ENGINEERING_LESSONS' MANIFEST.md && grep 'P0-EXPERIENCE-001' MANIFEST.md"
    evidence: ""
  - step: "Update task_logs/INDEX.md"
    result: "pending"
    command: "grep 'P0-EXPERIENCE-001' docs/phase0/task_logs/INDEX.md"
    evidence: ""
  - step: "Stage all files and verify diff"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""
  - step: "YAML validation"
    result: "pending"
    command: "python -c 'import yaml; yaml.safe_load(open(\"docs/phase0/task_logs/P0-EXPERIENCE-001_<ts>_passed.yaml\"))'"
    evidence: ""

touched_paths:
  - docs/dev/AI_AGENT_ENGINEERING_LESSONS.md
  - docs/phase0/REPOSITORY_CONTEXT_MAP.md
  - docs/phase0/tasks/P0-EXPERIENCE-001.md
  - docs/phase0/task_logs/P0-EXPERIENCE-001_<timestamp>_passed.yaml
  - docs/phase0/task_logs/INDEX.md
  - MANIFEST.md

forbidden_paths:
  - docs/blueprint/**
  - app/**
  - experiments/**
  - docs/adr/**
  - docs/phase0/task_logs/*.yaml (historical records — except the new P0-EXPERIENCE-001 record)
  - docs/phase0/PHASE1_TECHNICAL_BASELINE.md
  - docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
  - docs/phase0/TASK_INDEX.md
  - docs/dev/task_record_schema.yaml
  - CLAUDE.md
  - AGENTS.md
  - pyproject.toml
  - uv.lock
  - package.json
  - pnpm-lock.yaml

stop_conditions:
  - "Working tree is not clean at task start"
  - "Required source docs (CONTEXT_LOADING_STRATEGY.md, REPOSITORY_CONTEXT_MAP.md, task_record_schema.yaml) are missing"
  - "Forbidden paths are modified"
  - "changed_files cannot be reconciled with staged diff"
```

## Task Description

Phase 0 has accumulated real engineering pitfalls across spike tasks, evidence handling, environment management, and documentation sync. These lessons are currently scattered across individual Task Records and ADRs. This task consolidates them into a single, searchable reference file.

### What to create

1. **docs/dev/AI_AGENT_ENGINEERING_LESSONS.md** — The lessons-learned reference file with:
   - Prominent loading policy header (do not load full file by default)
   - 11 sections covering: Task Record Evidence, Git/Review Workflow, Environment/Runtime, Security/Secret Hygiene, Provider API Boundary, Spike/Test Design, Documentation/Index Sync, Scope/Context Hygiene, Tool Assignment, When to Update
   - Each lesson: one-sentence rule + one-sentence why
   - Target: 200-250 lines, max 280

2. **docs/phase0/tasks/P0-EXPERIENCE-001.md** — This task prompt file

3. **Task Record** — docs/phase0/task_logs/P0-EXPERIENCE-001_<timestamp>_passed.yaml

### What to update

4. **docs/phase0/REPOSITORY_CONTEXT_MAP.md** — Add lessons file to Section 3 docs/dev/ table as on-demand entry. If Section 5 progressive loading guide has an appropriate column, add a concise reference there.

5. **MANIFEST.md** — Register both new files

6. **docs/phase0/task_logs/INDEX.md** — Add one P0-EXPERIENCE-001 row

### What NOT to do

- Do not modify any forbidden paths listed above
- Do not modify historical Task Records or ADRs
- Do not update TASK_INDEX.md (this is an out-of-band task)
- Do not generate Batch 2 task prompts
- Do not start Batch 2 implementation
- No commit, no push, no merge. After execution, stage for review only. Commit / push / merge are allowed only after independent review approval and explicit user instruction.
