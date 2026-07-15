# P1-SPEC-CONTRACT-ALIGN — P1-SPEC-001 现行契约对齐

```yaml
task_id: "P1-SPEC-CONTRACT-ALIGN"
task_type: "preparation"
goal: "把 P1-SPEC-001 descriptor 对齐 Phase 1 v2.1.0 任务模板与已安装 codex-claude v4，同时保留其 B2 硬门和大纲人工批准语义"
non_goals:
  - "不执行 P1-SPEC-001，不填写 PHASE1_SPEC.md，不启动 B2"
  - "不修改 ROLE_POLICY、TASK_PROMPT_TEMPLATE、Task Record schema、应用代码、测试代码、CI、依赖或 frozen ports"
method_profile:
  execution_role: "documentation"
  execution_owner: "codex"
  review_owner: "claude_code"
  review_mode: "independent_review"
  risk_tier: "high"
  method: "PDR"
  model_note: "Opus read-only Plan and final-diff reviews; Codex owns execution"
  reason_for_owner_choice: "P1-SPEC-001 is the public result contract for all B2-B5 work"
controller_risk_tier: "R2"
risk_classification_reason: "High-impact repository task contract and B2 release gate"
plan_review_required: true
automation_class: "auto"
authorization_mode: "standard"
required_stops:
  - "human_result_acceptance"
r3_authorization: []
touched_paths:
  - "docs/phase1/tasks/P1-SPEC-CONTRACT-ALIGN.md"
  - "docs/phase1/tasks/P1-SPEC-001.md"
  - "docs/phase1/TASK_INDEX.md"
  - "docs/phase1/task_logs/P1-SPEC-CONTRACT-ALIGN_*.yaml"
  - "docs/phase1/task_logs/INDEX.md"
forbidden_paths:
  - "app/**"
  - "tests/**"
  - "scripts/**"
  - "web/**"
  - ".github/**"
  - "docs/phase0/**"
  - "docs/blueprint/**"
  - "docs/phase1/PHASE1_SPEC.md"
  - "docs/phase1/PHASE1_PLAN.md"
  - "docs/phase1/ROLE_POLICY.md"
  - "docs/phase1/TASK_PROMPT_TEMPLATE.md"
  - "docs/dev/task_record_schema.yaml"
acceptance_criteria:
  - "ALIGN-AC-01: P1-SPEC-001 begins with one canonical YAML contract containing every TASK_PROMPT_TEMPLATE required field plus plan_review_required=true"
  - "ALIGN-AC-02: authorization_mode is standard; controller risk, automation, required stops, integration and auto-next are explicit"
  - "ALIGN-AC-03: stale claims that the template owns Plan/Git choreography or that AGENTS.md is non-authoritative are removed"
  - "ALIGN-AC-04: source authority, B2 hard gate, scope, ten ACs, failure examples and outline-first human approval remain semantically intact"
  - "ALIGN-AC-05: P1-SPEC-CONTRACT-ALIGN descriptor and TASK_INDEX entry make this task reproducible without widening its scope"
  - "ALIGN-AC-06: staged candidate 无 forbidden path、无 R3 变更;执行期任何 R3 均为显式授权且在候选之外"
failure_examples:
  - "Descriptor still depends on removed bounded-goal/packet marker mechanisms or stale no-commit choreography"
  - "Alignment silently weakens P1-SPEC-001 outline approval, final human acceptance, B2 hard gate, source authority, ACs or forbidden paths"
  - "The task changes PHASE1_SPEC.md, implementation code, tests, template, role policy or schema"
step_verification_points:
  - "Baseline: clean phase0/main at origin/phase0/main and current template/descriptor parsed"
  - "Plan: Opus Plan Review bound before repository content edits"
  - "Apply: only the five declared documentation/log surfaces may change"
  - "Validate: YAML contract keys, stale wording, preserved AC/gates and exact staged paths"
  - "Review/integrate: final Opus meta, exact commit tree, non-force integration and post-merge CI"
validation_commands:
  - "uv run python scripts/check_dependencies.py"
  - "uv run pytest tests/architecture/ -q -p no:cacheprovider"
  - "uv run pytest"
  - "git diff --cached --name-only"
  - "git diff --cached --stat"
  - "git diff --cached --check"
  - "git ls-files --others --exclude-standard"
evidence_requirements:
  - "Parsed canonical YAML keys and explicit standard/R2/auto/Plan/final Review settings"
  - "Before/after stale-wording and preserved B2/AC/outline-gate scans"
  - "Plan and final claude_*_meta.json bound to actual artifacts"
  - "Exact staged-path, candidate tree/diff, commit parent/tree, push/PR/merge and post-merge CI evidence"
stop_conditions:
  - "Alignment needs any forbidden path, deletion, schema/template/policy change, secret, DB/data change or dangerous Git operation"
  - "P1-SPEC-001 semantics cannot be preserved while adopting the current contract shape"
  - "Plan/final Review, validation, scope, branch protection or CI evidence is missing or stale"
local_commit_policy: "after_review_pass"
integration_policy:
  mode: "git"
  remote_strategy: "task_branch_pr_merge"
  task_branch_ci: "if_triggered"
  post_merge_ci: "required"
auto_next_policy: "blocked"
depends_on:
  - "LOCAL-WF-V4-001 v4 mirror sync complete"
  - "P1-RUNTIME-ENTRY-001 integrated at 869647f with merge-SHA CI passing; its pre-integration Task Record remains stale and is not rewritten by this task"
branch: "phase1/P1-SPEC-CONTRACT-ALIGN"
references:
  - "AGENTS.md"
  - "CLAUDE.md"
  - "docs/phase1/ROLE_POLICY.md"
  - "docs/phase1/TASK_PROMPT_TEMPLATE.md"
  - "docs/dev/task_record_schema.yaml"
  - "docs/phase1/tasks/P1-SPEC-001.md"
```

本任务只对齐结果契约。`P1-RUNTIME-ENTRY-001` 的旧 Task Record/索引远端证据债务另行登记，不在本任务扩域修复；本任务合并并完成人工结果验收前，不启动 `P1-SPEC-001`。
