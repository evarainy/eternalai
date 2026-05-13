# P0-SCHEMA-001 — Task Record schema review_mode alignment

## method_profile

```yaml
method_profile:
  execution_role: "documentation"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "not_applicable"
  reason_for_owner_choice: "This is a small schema/documentation alignment task requiring local file edits and validation; Claude Code/MiMo executes, Codex performs independent staged-diff review."
```

## Workflow Rule

No commit, no push, no merge. After execution, stage for review only. Commit / push / merge are allowed only after independent review approval and explicit user instruction.

## Task Scope

- **task_id:** P0-SCHEMA-001
- **task_type:** documentation
- **Goal:** Extend `docs/dev/task_record_schema.yaml` `review.mode` allowed values from `none | self_check | human_optional` to `none | self_check | human_optional | codex_review`, aligning with the v2.0.0 template workflow.
- **This task will NOT:**
  - Generate Batch 2 implementation task prompts
  - Modify business code (`app/**`, `experiments/**`)
  - Modify `docs/blueprint/**`
  - Modify historical ADRs
  - Modify historical Task Record YAML files
  - Modify Phase 1 structured-output baseline
  - Rewrite P0-TEMPLATE-001 completed deliverables
  - Modify `CLAUDE.md` / `AGENTS.md`
  - Modify production dependencies or lock files

## Acceptance Criteria

| # | Criterion | Evidence |
|---|---|---|
| AC-1 | `docs/dev/task_record_schema.yaml` `review.mode` line includes `codex_review` in allowed values | grep output showing `codex_review` on the mode line |
| AC-2 | All previous allowed values (`none`, `self_check`, `human_optional`) remain present | grep output confirming backward compatibility |
| AC-3 | Historical Task Record YAML files are not modified | `git diff --cached --name-only` filtered for `task_logs/*.yaml` shows only the new P0-SCHEMA-001 record |
| AC-4 | `task_logs/INDEX.md` contains exactly one P0-SCHEMA-001 row | `grep P0-SCHEMA-001 task_logs/INDEX.md \| wc -l` = 1 |
| AC-5 | INDEX data row count matches YAML file count in `task_logs/` | row count = `ls *.yaml \| wc -l` |
| AC-6 | Task Record YAML passes `yaml.safe_load` | Python safe_load validation |
| AC-7 | Task Record passes UniqueKeyLoader duplicate-key check | No duplicate keys at any indent level |
| AC-8 | `changed_files` in Task Record exactly matches `git diff --cached --name-only` | Exact string comparison |
| AC-9 | `PHASE1_TECHNICAL_BASELINE.md` not in staged diff | `git diff --cached --name-only` filtered |
| AC-10 | No forbidden paths modified | `git diff --cached --name-only` filtered against forbidden list |

## Failure Examples / Blocking Examples

Not applicable — documentation/schema alignment task; no runtime failure examples.

- **Blocking example 1:** Wrong branch → stop and report.
- **Blocking example 2:** Dirty working tree → stop and report.
- **Blocking example 3:** Schema file missing → stop and report.

## Touched Paths

- `docs/dev/task_record_schema.yaml`
- `docs/phase0/tasks/P0-SCHEMA-001.md`
- `docs/phase0/task_logs/P0-SCHEMA-001_<timestamp>_passed.yaml`
- `docs/phase0/task_logs/INDEX.md`
- `MANIFEST.md`

## Forbidden Paths

- `app/**`
- `experiments/**`
- `docs/blueprint/**`
- `docs/adr/**` (historical ADRs)
- `docs/phase0/PHASE1_TECHNICAL_BASELINE.md`
- `CLAUDE.md`
- `AGENTS.md`
- `*.lock`, `pyproject.toml`, `package.json`
- Historical Task Record YAML files (all `task_logs/*.yaml` except the new P0-SCHEMA-001 record)
