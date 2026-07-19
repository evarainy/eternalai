# P1-WORKFLOW-V5-001 — Codex-Claude V5 governance migration candidate

```yaml
task_id: "P1-WORKFLOW-V5-001"
task_type: "documentation"
goal: "Form the last V4-governed, pre-commit-reviewed Candidate B that aligns EternalAI's active workflow governance with the approved Codex-Claude V5 design."
non_goals:
  - "Do not modify product, architecture, runtime, tests, CI, credentials, global configuration, V4 canonical, Candidate A, or historical task evidence."
  - "Do not commit, push, open a PR, merge, cut over, invoke native /review, or invoke Opus in this task lane."
  - "Do not create a V4/V5 dual track, compatibility layer, state migration, or V5 runtime dependency on this migration descriptor or Task Record."
method_profile:
  execution_role: "documentation"
  execution_owner: "codex"
  review_owner: "claude_code"
  review_mode: "independent_review"
  risk_tier: "high"
  method: "not_applicable"
  model_note: "Sol X-High Candidate Owner; final acceptance requires fresh candidate-bound Codex Review plus Claude Opus 4.8 X-High Review. No additional Opus plan re-review or pre-construction review probe."
  reason_for_owner_choice: "This migration changes the active workflow control plane and therefore requires Sol X-High ownership and Q3 final Review."
controller_risk_tier: "R2"
risk_classification_reason: "The diff is documentation-only but changes the active workflow control plane; no reserved R3 action is authorized."
automation_class: "auto"
authorization_mode: "bounded_goal_preapproval"
required_stops: []
r3_authorization: []
touched_paths:
  - "AGENTS.md"
  - "CLAUDE.md"
  - "docs/phase1/ROLE_POLICY.md"
  - "docs/phase1/TASK_PROMPT_TEMPLATE.md"
  - "docs/dev/task_record_schema.yaml"
  - "docs/phase1/TASK_INDEX.md"
  - "docs/phase1/tasks/P1-WORKFLOW-V5-001.md"
  - "docs/phase1/task_logs/P1-WORKFLOW-V5-001_*.yaml"
forbidden_paths:
  - ".github/"
  - "app/"
  - "tests/"
  - "web/"
  - "infra/"
  - "experiments/"
  - "docs/blueprint/"
  - "docs/phase0/"
  - "docs/phase1/PHASE1_PLAN.md"
  - "docs/phase1/PHASE1_SPEC.md"
acceptance_criteria:
  - id: "V5-B-AC-01"
    criterion: "Scratch descriptor and TASK_INDEX row are byte-identical to the landed artifacts, their SHA-256 values are recorded, the strict V4 parser passes, and the task-specific frozen-authority checker passes."
    evidence: "Bootstrap checker JSON plus SHA-256 values and exact-byte comparisons."
  - id: "V5-B-AC-02"
    criterion: "The repository diff contains exactly the five semantic governance files, one TASK_INDEX edit, this descriptor, and exactly one final Task Record."
    evidence: "Exact staged path list and task-specific checker output."
  - id: "V5-B-AC-03"
    criterion: "AGENTS.md and CLAUDE.md remove active one-turn/one-task and V4 command dependencies, allow multi-task auto-next inside one native Goal, and keep every write lane single-scope."
    evidence: "Search results and staged diff."
  - id: "V5-B-AC-04"
    criterion: "ROLE_POLICY.md replaces universal independent Review and R2 Claude rules with Q0-Q3, maps Q2 to Opus 4.8 High and Q3 to Opus 4.8 X-High, keeps Review independent, and separates risk from human stops."
    evidence: "Policy search results and staged diff."
  - id: "V5-B-AC-05"
    criterion: "TASK_PROMPT_TEMPLATE.md and task_record_schema.yaml are clearly V4 legacy/retired for new V5 Goals while preserving historical records and this final migration record under schema v1.2.0."
    evidence: "YAML duplicate-key-safe parse, legacy text checks, and staged diff."
  - id: "V5-B-AC-06"
    criterion: "TASK_INDEX contains exactly one P1-WORKFLOW-V5-001 row bound to branch phase1/P1-WORKFLOW-V5-001, and exactly one final full Task Record has changed_files identical to the staged path order."
    evidence: "Unique-row search, duplicate-key-safe YAML load, and exact staged-order comparison."
  - id: "V5-B-AC-07"
    criterion: "All task-specific, diff, repository, architecture, dependency, type, lint, and golden-gate validation commands complete with truthful results and no skipped or weakened tests."
    evidence: "Fresh command exit codes and output snippets recorded in the Task Record and external validation evidence."
  - id: "V5-B-AC-08"
    criterion: "The exact eight-path-class candidate is staged against parent f99737cdd3a566be43c6e521cc21f7271c42c6c2 with tree, raw staged diff hash, path order, and freshness evidence, then stops before Review and commit."
    evidence: "Git parent/tree/raw-diff/path/freshness evidence and clean unstaged/untracked checks."
failure_examples:
  - id: "V5-B-FE-01"
    example: "Scratch and landed descriptor or TASK_INDEX bytes/hash differ."
    expected: "Fail closed before semantic governance edits."
  - id: "V5-B-FE-02"
    example: "A changed or staged repository path falls outside the eight allowed path classes, or more than one migration Task Record exists."
    expected: "Reject the candidate and return NEEDS_PARENT rather than widening scope."
  - id: "V5-B-FE-03"
    example: "A duplicate YAML key, hidden failure through not_applicable, or prefilled Review/commit/PR/merge/CI success is detected."
    expected: "Reject the record and do not proceed to Review."
  - id: "V5-B-FE-04"
    example: "The same causal bootstrap or construction failure occurs a second time."
    expected: "Stop construction immediately and report the root-cause key/count without a third patch strategy."
step_verification_points:
  - id: "V5-B-SV-01"
    step: "Freeze and validate scratch bootstrap before semantic edits."
    verification: "Hash-pinned V4 parser plus task-specific checker; five semantic files retain frozen SHA-256 values."
  - id: "V5-B-SV-02"
    step: "Complete only the five semantic edits and the unique TASK_INDEX row."
    verification: "Exact scope and active/legacy semantics search."
  - id: "V5-B-SV-03"
    step: "Run formal repository validation and create one final frozen-v1.2.0 Task Record."
    verification: "Fresh command evidence and duplicate-key-safe schema checks."
  - id: "V5-B-SV-04"
    step: "Stage the exact candidate and freeze pre-commit identity."
    verification: "Parent, tree, raw staged diff SHA-256, ordered paths, freshness, and clean unstaged/untracked evidence."
validation_commands:
  - "uv run --with PyYAML python C:/Users/Administrator/.claude-codex-scratch/v5-build-20260719-093015/candidate-b-bootstrap/validate_candidate_b.py --phase final --worktree E:/code/eternalai/.worktrees/P1-WORKFLOW-V5-001 --scratch C:/Users/Administrator/.claude-codex-scratch/v5-build-20260719-093015/candidate-b-bootstrap"
  - "git diff --check"
  - "uv run pytest"
  - "uv run ruff check app/ tests/"
  - "uv run mypy app/"
  - "uv run python scripts/check_dependencies.py"
  - "uv run pytest tests/architecture/"
  - "uv run python scripts/run_golden_tasks.py --gate"
  - "git diff --cached --check"
evidence_requirements:
  - "Bootstrap evidence records frozen authority hashes, scratch hashes, strict parser result, landed byte identity, and unchanged semantic-file hashes."
  - "Each V5-B-AC identifier has a truthful passed/failed result and concrete file, command, hash, or Git evidence."
  - "Formal validation records exact command, exit code, and a useful output snippet; a baseline failure is never relabeled as candidate success."
  - "Final candidate evidence records reviewed_parent_sha, staged_tree_oid, full raw staged diff SHA-256, ordered staged paths, and freshness."
  - "Review, commit, PR/merge, task-branch CI, post-merge CI, and integration remain explicitly external/deferred/not_triggered before Review."
stop_conditions:
  - "Stop if bootstrap hash/parser/checker validation fails twice for the same causal root; do not edit the five semantic files after a failed bootstrap."
  - "Stop and return NEEDS_PARENT if any required change leaves the eight allowed path classes or alters architecture, public contracts, product behavior, historical evidence, credentials, global configuration, V4 canonical, Candidate A, or repair-task-staging."
  - "Stop before any Review, Opus call, commit, push, PR, merge, CI integration, or cutover; those are outside this Candidate Owner lane."
  - "Do not integrate Candidate B before separate, precise cutover authorization."
  - "Stop on an authority conflict, redline action, missing deterministic evidence, or second occurrence of the same causal construction failure."
local_commit_policy: "after_review_pass"
integration_policy:
  mode: "git"
  remote_strategy: "task_branch_pr_merge"
  task_branch_ci: "if_triggered"
  post_merge_ci: "required"
auto_next_policy: "blocked"
depends_on:
  - "Approved V5 design SHA-256 F464E36DB19A7D05DCB0A6E5970CC0DBBADC8FE010CD476B31F58EC55225D68B"
  - "Approved revised implementation plan SHA-256 118AE10B0659D03540E0945770EF0859908F8264B590D77F4A4203BDDBCE00A0"
  - "User's written 2026-07-19 authorization for isolated Candidate A/B construction and validation; cutover remains unauthorized"
branch: "phase1/P1-WORKFLOW-V5-001"
references:
  - "D:/Backup/Documents/优化codex-claude/docs/specs/2026-07-17-codex-claude-v5-design.md (SHA-256 F464E36DB19A7D05DCB0A6E5970CC0DBBADC8FE010CD476B31F58EC55225D68B)"
  - "D:/Backup/Documents/优化codex-claude/docs/plans/2026-07-19-codex-claude-v5-implementation-plan.md (SHA-256 118AE10B0659D03540E0945770EF0859908F8264B590D77F4A4203BDDBCE00A0)"
  - "C:/Users/Administrator/.claude-codex-scratch/v5-build-20260719-093015/candidate-b-bootstrap/FROZEN_V4_BUNDLE_MANIFEST.md"
  - "origin/phase0/main@f99737cdd3a566be43c6e521cc21f7271c42c6c2"
```

## Result boundary

This is the final V4-governed migration task. It may form and stage Candidate B for final candidate-bound Codex and Opus Review, but this lane must stop before Review and must not commit or integrate. Review PASS is a later acceptance condition, not evidence that may be prefilled into this descriptor's final Task Record.

All repository paths not listed in `touched_paths` are forbidden. The descriptor, TASK_INDEX row, and final Task Record remain historical migration evidence after cutover; new V5 Goals do not read them.

CODEX_CLAUDE_TASK_CONTRACT_BEGIN
{
  "task_id": "P1-WORKFLOW-V5-001",
  "allowed_paths": [
    "AGENTS.md",
    "CLAUDE.md",
    "docs/phase1/ROLE_POLICY.md",
    "docs/phase1/TASK_PROMPT_TEMPLATE.md",
    "docs/dev/task_record_schema.yaml",
    "docs/phase1/TASK_INDEX.md",
    "docs/phase1/tasks/P1-WORKFLOW-V5-001.md",
    "docs/phase1/task_logs/P1-WORKFLOW-V5-001_*.yaml"
  ],
  "forbidden_paths": [
    ".github/",
    "app/",
    "tests/",
    "web/",
    "infra/",
    "experiments/",
    "docs/blueprint/",
    "docs/phase0/",
    "docs/phase1/PHASE1_PLAN.md",
    "docs/phase1/PHASE1_SPEC.md"
  ],
  "required_deliverables": [
    "AGENTS.md",
    "CLAUDE.md",
    "docs/phase1/ROLE_POLICY.md",
    "docs/phase1/TASK_PROMPT_TEMPLATE.md",
    "docs/dev/task_record_schema.yaml",
    "docs/phase1/TASK_INDEX.md",
    "docs/phase1/tasks/P1-WORKFLOW-V5-001.md",
    "docs/phase1/task_logs/P1-WORKFLOW-V5-001_*.yaml"
  ],
  "acceptance_ids": [
    "V5-B-AC-01",
    "V5-B-AC-02",
    "V5-B-AC-03",
    "V5-B-AC-04",
    "V5-B-AC-05",
    "V5-B-AC-06",
    "V5-B-AC-07",
    "V5-B-AC-08"
  ]
}
CODEX_CLAUDE_TASK_CONTRACT_END
