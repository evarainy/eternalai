# Phase 0 Execution Pack v1.0.11 Manifest

Files:

- .claude/agents/boundary-checker.md
- .claude/settings.example.json
- AGENTS.md
- CLAUDE.md
- MANIFEST.md
- docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md
- docs/blueprint/phase0_handoff_after_blueprint_freeze.md
- docs/dev/codex_setup.md
- docs/dev/git_workflow.md
- docs/dev/human_review_checklist.md
- docs/dev/package_definition.md
- docs/dev/task_record_schema.yaml
- docs/phase0/ADR_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/CONTEXT_LOADING_STRATEGY.md
- docs/phase0/FIRST_BATCH_TASKS.md
docs/phase0/FRONTEND_MOCK_FIXTURES.md
- docs/phase0/README_FOR_CODEX.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/self_checks/README.md
- docs/phase0/task_logs/INDEX.md
- docs/phase0/tasks/P0-PREP-001.md
- docs/phase0/tasks/P0-PREP-002.md
- docs/phase0/tasks/P0-PREP-003.md
- docs/phase0/tasks/P0-SPIKE-001.md
- docs/phase0/tasks/P0-SPIKE-002.md
- docs/phase0/tasks/P0-SPIKE-003.md
- docs/phase0/tasks/P0-SPIKE-004.md
- docs/phase0/tasks/P0-SPIKE-005.md
- docs/phase0/tasks/P0-SPIKE-006.md
- docs/phase0/tasks/P0-RULES-001.md
- docs/phase0/tasks/P0-SPIKE-007.md
- docs/phase0/tasks/README_TASK_PROMPTS.md

Start from `docs/phase0/README_FOR_CODEX.md`.

## v1.0.11 Patch Notes

- Scope: execution-readiness fixes only; no architecture, task-scope, or frozen-blueprint change.
- Completed required implementation fields for `P0-DOMAIN-001b`.
- Clarified `P0-PREP-*` as execution-pack-only tasks whose full definitions live in `docs/phase0/tasks/`.
- Added infrastructure task handling rules so Infra tasks do not trigger `task_definition_incomplete` merely for lacking implementation failure examples.
- Strengthened `P0-GT-002` CI activation evidence for Golden Task `not_applicable` removal.
- Added full Capability schema storage and digest consistency requirements.
- Clarified `risk_level` legal enum: `low | medium | high`; `critical` is an invalid-input test example only.
