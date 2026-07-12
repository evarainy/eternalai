# P1-CI-ALIGN-001 — Phase 1 预合并 CI 对齐

```yaml
task_id: "P1-CI-ALIGN-001"
task_type: "infrastructure"
goal: "让 phase1/** task branch 和以 phase0/main 为 base 的 PR 在 merge 前运行完整可信 CI，补齐 frontend test，并只消除可证明的重复执行"
non_goals:
  - "不修改 app/**、tests/**、scripts/** 或 web/** 内容"
  - "不升级依赖、不修改 lockfile、DB schema/migration、secret、repository settings 或生产系统"
  - "不以删除 early-fail、Golden CLI gate 或 weak-test 语义换取表面去重"
  - "不执行 P1-OBS-001、P1-RUNTIME-ENTRY-001、P1-SPEC-001 或 B2"
method_profile:
  execution_role: "execution"
  execution_owner: "codex"
  review_owner: "claude_code"
  review_mode: "independent_review"
  risk_tier: "high"
  method: "PDR"
  model_note: "CI/gate surface; Claude Opus is bounded read-only Plan/final-diff reviewer"
  reason_for_owner_choice: "Phase 1 default split"
controller_risk_tier: "R2"
risk_classification_reason: "CI event matrix, required checks, test coverage and merge evidence are high-impact governance contracts"
automation_class: "auto"
automation_mapping_reason: "The hash-pinned source used the legacy label automation_class=R1 but explicitly required no Gate 1/Gate 2/local-commit stop; canonical v3 maps that intent to controller R2 plus automation_class=auto"
authorization_mode: "standard"
required_stops: []
r3_authorization: []
auto_next_policy: "blocked"
integration_policy:
  mode: "git"
  remote_strategy: "task_branch_pr_merge"
  task_branch_ci: "required"
  post_merge_ci: "required"
touched_paths:
  - ".github/workflows/ci.yml"
  - "docs/phase1/tasks/P1-CI-ALIGN-001.md"
  - "docs/phase1/TASK_INDEX.md"
  - "docs/phase1/task_logs/INDEX.md"
  - "docs/phase1/task_logs/P1-CI-ALIGN-001_*.yaml"
forbidden_paths:
  - "app/**"
  - "tests/**"
  - "scripts/**"
  - "web/**"
  - "pyproject.toml"
  - "uv.lock"
  - "web/package.json"
  - "web/pnpm-lock.yaml"
  - "app/ports/**"
  - "docs/blueprint/**"
  - "docs/phase1/PHASE1_SPEC.md"
  - "alembic/**"
  - "infra/**"
  - ".env"
  - ".env.*"
acceptance_criteria:
  - id: "CI-AC-01"
    text: "push 到 phase1/**、push/merge 到 phase0/main、以及 PR base=phase0/main 的事件矩阵正确；保留其他分支模式时有当前用途证据且不放宽写权限"
  - id: "CI-AC-02"
    text: "frontend frozen-lockfile install 后依次覆盖 lint、typecheck、test、build，Node/pnpm 版本与仓库约定一致且无依赖变更"
  - id: "CI-AC-03"
    text: "backend 继续覆盖 Ruff、mypy、dependency、import boundary、weak-test、Alembic guard、完整 pytest 与 Golden gate；任何去重都有 node-id 集合等价证据"
  - id: "CI-AC-04"
    text: "当前 task commit 的 task-branch/PR CI 全绿后才 merge；post-merge CI 绑定 merge SHA；未触发检查记录 not_triggered 而不是 passed"
  - id: "CI-AC-05"
    text: "workflow 不使用 pull_request_target 执行未信任代码、不引入 secret、不回显凭证、不新增宽泛 write permission；无需写权限时保持 contents: read"
  - id: "CI-AC-06"
    text: "Task Record 合法且 changed_files 等于最终 staged names；Review、validation、task/PR/post-merge CI、commit/merge SHA 按 deferred evidence 规则记录"
failure_examples:
  - "phase1 push 或 phase0/main PR 不触发当前 workflow -> event_matrix_incomplete"
  - "去重后 node-id 覆盖减少或 Golden/weak-test 非 pytest 语义丢失 -> coverage_regression"
  - "frontend test 缺失、lockfile 改动、权限放宽或 pull_request_target 执行未信任代码 -> fail_closed"
  - "pre-merge run 不绑定当前 task commit，或 post-merge run 不绑定 merge SHA -> ci_evidence_stale"
  - "需要修改 forbidden path、repository settings、DB、secret、依赖或 R3 动作 -> scope_expansion_required"
step_verification_points:
  - "preflight: dependency, descriptor, current workflow/event history, auth/protection and base/head"
  - "candidate: before/after event matrix and pytest collection/node-id proof for every proposed deduplication"
  - "pre-review: exact staged diff, workflow YAML, local executable checks, permissions and secret scan"
  - "pre-integration: current commit task-branch/PR CI, Review freshness and mergeability"
  - "post-integration: merge-SHA phase0/main CI and no auto-start of P1-OBS-001"
validation_commands:
  - "python -c \"import yaml; yaml.safe_load(open('.github/workflows/ci.yml', encoding='utf-8')); print('YAML_OK')\""
  - "git diff --check"
  - "uv run ruff check app/ tests/"
  - "uv run mypy app/"
  - "uv run python scripts/check_dependencies.py"
  - "uv run pytest tests/architecture/ -q -p no:cacheprovider"
  - "uv run python scripts/check_weak_tests.py tests"
  - "uv run pytest"
  - "uv run python scripts/run_golden_tasks.py --gate"
  - "pnpm --dir web install --frozen-lockfile"
  - "pnpm --dir web lint"
  - "pnpm --dir web typecheck"
  - "pnpm --dir web test"
  - "pnpm --dir web build"
evidence_requirements:
  - "before/after workflow event matrix and live GitHub run evidence"
  - "before/after pytest collection node IDs when any repeated pytest execution is changed"
  - "frontend four-command results and unchanged dependency/lockfile hashes"
  - "current task commit task-branch/PR CI run id, headSha and job conclusions"
  - "merge SHA plus post-merge phase0/main CI run id, headSha and all triggered jobs"
  - "Codex self-review, Claude meta/prompt/output hashes, exact diff/path/secret checks and Task Record"
stop_conditions:
  - "P1-WORKFLOW-002-REPAIR-001 is not completed with Gate 2 accepted, or this descriptor/DAG is missing or stale"
  - "Plan/Review finds a target, Gate, allowed-path or acceptance conflict"
  - "coverage equivalence cannot be proved, local/remote validation is red, or CI is unbound to the current SHA"
  - "fix requires app/tests/scripts/dependency/lockfile/DB/secret/repository settings or any R3 action"
  - "auth, branch protection, mergeability, network or freshness fails after one bounded safe retry"
  - "any action would start P1-OBS-001 or another downstream task"
local_commit_policy: "after_review_pass"
depends_on:
  - "P1-WORKFLOW-002-REPAIR-001 completed and post-integration Gate 2 accepted"
branch: "phase1/P1-CI-ALIGN-001"
references:
  - "docs/phase1/ROLE_POLICY.md"
  - "docs/phase1/TASK_PROMPT_TEMPLATE.md"
  - "docs/dev/task_record_schema.yaml"
  - ".github/workflows/ci.yml"
```

## Result-contract clarifications

- Only remove duplicate pytest execution when before/after collected node-ID sets prove unchanged total coverage and each intended node runs once. Golden CLI and weak-test commands remain whenever they carry non-pytest gate semantics.
- Local DB/environment limits are recorded honestly and never replace current remote CI. One clearly identified flaky run may be retried once; an unexplained or repeated failure blocks integration.
- A facts-only descriptor correction is allowed by the source scope, but must be applied before candidate work and followed by a fresh controller Plan/state binding; a running state may never silently accept descriptor drift.

CODEX_CLAUDE_TASK_CONTRACT_BEGIN
{
  "task_id": "P1-CI-ALIGN-001",
  "allowed_paths": [
    ".github/workflows/ci.yml",
    "docs/phase1/tasks/P1-CI-ALIGN-001.md",
    "docs/phase1/TASK_INDEX.md",
    "docs/phase1/task_logs/INDEX.md",
    "docs/phase1/task_logs/P1-CI-ALIGN-001_*.yaml"
  ],
  "forbidden_paths": [
    "app/**",
    "tests/**",
    "scripts/**",
    "web/**",
    "pyproject.toml",
    "uv.lock",
    "web/package.json",
    "web/pnpm-lock.yaml",
    "app/ports/**",
    "docs/blueprint/**",
    "docs/phase1/PHASE1_SPEC.md",
    "alembic/**",
    "infra/**",
    ".env",
    ".env.*"
  ],
  "required_deliverables": [
    ".github/workflows/ci.yml",
    "docs/phase1/task_logs/P1-CI-ALIGN-001_*.yaml"
  ],
  "acceptance_ids": [
    "CI-AC-01",
    "CI-AC-02",
    "CI-AC-03",
    "CI-AC-04",
    "CI-AC-05",
    "CI-AC-06"
  ]
}
CODEX_CLAUDE_TASK_CONTRACT_END
