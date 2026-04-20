# Business Repo Phase 0 Output Contract

Codex must produce a planning package that is:
- grounded in actual repository evidence
- machine-readable
- detailed enough for multi-step execution
- honest about blockers and unknowns

## Required files

### spec/
- product_spec.md
- product_spec.yaml
- module_spec.yaml
- resource_spec.yaml
- invariant_spec.yaml

### contracts/
- README.md
- contract files when applicable

### planning/
- dev_plan.yaml
- acceptance_spec.yaml
- run_policy.yaml
- implement.md
- documentation.md
- plan_review_checklist.md
- phase0_result.json

## Required properties

### dev_plan.yaml
Every task must include:
- id
- title
- layer
- goal
- deps
- touched_paths
- forbidden_paths
- acceptance_ids
- parallel_safe
- needs_db
- needs_ports
- shared_resources_touched
- verification_commands
- evidence_paths
- human_gate
- notes

### acceptance_spec.yaml
Every acceptance item must include:
- id
- title
- type
- trigger
- expect
- evidence

For API/UI/integration items, prefer explicit request/assertion structures.

### implement.md
Must define a thick per-task result contract with:
- task_id
- attempt
- layer
- status
- acceptance_ids_planned
- acceptance_ids_passed
- acceptance_ids_failed
- changed_files
- tests_run
- verification_commands
- evidence_paths
- invariants_preserved
- blocked_by
- needs_human
- error_code
- next_action

### phase0_result.json
Must include:
- phase
- status
- generated_files
- missing_files
- blockers
- risks
- repo_summary
- readiness
- next_step

`readiness` must include:
- planning_ready
- execution_ready
- reason

## Honesty rules
- Do not mark execution-ready if core business evidence is missing.
- Do not claim contracts exist unless files or source evidence support them.
- Do not claim tests are runnable unless commands were actually identified from the repo.
