# P1-WORKFLOW-002-REPAIR-001 — W002 完整范围修复与活动控制面收敛

```yaml
task_id: "P1-WORKFLOW-002-REPAIR-001"
task_type: "documentation"
goal: "修复已集成 W002 的完整验收范围遗漏，建立 03—05 正式任务契约与依赖链，收敛活动治理入口，并以可审计方式处理 Git-excluded phase-task companion"
non_goals:
  - "不重写或补签已完成的 P1-WORKFLOW-002 descriptor、Task Record、旧 Plan、旧 Review 或旧 v2 run_state"
  - "不执行 P1-CI-ALIGN-001、P1-OBS-001、P1-RUNTIME-ENTRY-001、P1-SPEC-001 或 B2"
  - "不修改生产代码、测试、CI workflow、依赖、数据库、凭证、冻结 blueprint/Phase 0 文档"
  - "不修改个人 canonical/mirror codex-claude skill 或历史 scratch 证据"
method_profile:
  execution_role: "documentation"
  execution_owner: "codex"
  review_owner: "claude_code"
  review_mode: "independent_review"
  risk_tier: "high"
  method: "not_applicable"
  model_note: "controller risk R2; Claude Opus is bounded read-only Plan/final-diff reviewer only"
  reason_for_owner_choice: "Phase 1 默认分工；本任务修复 workflow control plane"
controller_risk_tier: "R2"
automation_class: "manual_gate1_then_auto_integrate_then_gate2"
authorization_mode: "standard"
touched_paths:
  - "AGENTS.md"
  - "CLAUDE.md"
  - "docs/phase1/ROLE_POLICY.md"
  - "docs/phase1/TASK_PROMPT_TEMPLATE.md"
  - "docs/dev/task_record_schema.yaml"
  - "docs/phase1/tasks/P1-CI-ALIGN-001.md"
  - "docs/phase1/tasks/P1-OBS-001.md"
  - "docs/phase1/tasks/P1-RUNTIME-ENTRY-001.md"
  - "docs/phase1/TASK_INDEX.md"
  - "docs/phase1/task_logs/P1-WORKFLOW-002-REPAIR-001_*.yaml"
  - ".agents/skills/phase-task/SKILL.md"
filesystem_companion_paths:
  - path: ".agents/skills/phase-task/SKILL.md"
    git_tracking: "Git-excluded; never claim it is staged, committed, pushed, merged, or covered by a Git blob id"
    bootstrap_observation: "absent at 006cc04dfd24e788db273aa25b32b164142b2bfb; future repair must reverify"
    action_matrix: "if present, make a reversible RETIRED/redirect-only edit with baseline/candidate hashes and byte backup in scratch; if absent, record already_absent and do not recreate or delete anything"
forbidden_paths:
  - "docs/phase1/tasks/P1-WORKFLOW-002-REPAIR-001.md"
  - "docs/phase1/tasks/P1-WORKFLOW-002.md"
  - "docs/phase1/task_logs/P1-WORKFLOW-002_*.yaml"
  - "docs/phase1/tasks/P1-SPEC-001.md"
  - "docs/phase1/tasks/P1-PARAM-001.md"
  - "docs/phase1/PHASE1_SPEC.md"
  - "app/**"
  - "tests/**"
  - "scripts/**"
  - "web/**"
  - ".github/**"
  - "alembic/**"
  - "infra/**"
  - "docs/blueprint/**"
  - "docs/phase0/**"
  - "_scratch/**"
  - ".worktrees/**"
  - ".env"
  - ".env.*"
  - "uv.lock"
  - "web/package.json"
  - "web/pnpm-lock.yaml"
external_forbidden_paths:
  - "C:/Users/Administrator/.codex/skills/codex-claude/**"
  - "C:/Users/Administrator/.claude/skills/codex-claude/**"
  - "C:/Users/Administrator/.claude-codex-scratch/runs/P1-WORKFLOW-002/**"
  - "C:/tmp/p1-workflow-002-git-run-20260711/**"
  - "D:/repair-task/**"
acceptance_criteria:
  - id: "REPAIR-AC-01"
    text: "明确 006cc04 已完成 W002 主体集成但原任务 02 完整验收存在范围遗漏；旧 v2、旧 bounded authorization、旧 Plan/Review/candidate 仅作历史且不可复用"
  - id: "REPAIR-AC-02"
    text: "依据 hash-pinned 任务 03—05 来源创建三个紧凑正式 task contracts，保留目标、paths、AC、validation、stop，不复制 workflow SOP"
  - id: "REPAIR-AC-03"
    text: "TASK_INDEX 唯一登记并保持 P1-WORKFLOW-002-REPAIR-001 -> P1-CI-ALIGN-001 -> P1-OBS-001 -> P1-RUNTIME-ENTRY-001；依赖未满足时后项不可启动"
  - id: "REPAIR-AC-04"
    text: "repo-local .agents/skills/phase-task/SKILL.md 作为 filesystem companion 单列；存在时可逆退役/redirect，不存在时记录 already_absent 且不重建；Git commit 证据不得包含它"
  - id: "REPAIR-AC-05"
    text: "P1-WORKFLOW-002.md 与既有 W002 Task Record 保持逐字历史；活动入口通过当前 repair descriptor、TASK_INDEX 与必要的活动规则 redirect/errata 收敛"
  - id: "REPAIR-AC-06"
    text: "逐项核对 AGENTS.md、CLAUDE.md、ROLE_POLICY、TASK_PROMPT_TEMPLATE、task_record_schema 与当前 canonical controller；只修复本 descriptor allowed paths 内且有确定证据的活动漂移"
  - id: "REPAIR-AC-07"
    text: "Git candidate/staged changed paths 与 filesystem companion manifest 均严格落在各自边界；无第三类路径、无历史证据回填、无 R3"
  - id: "REPAIR-AC-08"
    text: "本 descriptor 保持 exactly one strict TASK_CONTRACT block；五字段、allowed/forbidden、required deliverables、稳定 acceptance IDs 可由当前 goal_contract.py 解析"
  - id: "REPAIR-AC-09"
    text: "artifact hash、YAML/schema、routing、DAG、architecture、Golden、weak-test、diff/status/untracked/secret 检查均以真实 exit/output/hash 记录"
  - id: "REPAIR-AC-10"
    text: "每次 Claude Plan/final Review 前先有独立只读 packet audit CLEAR；Claude requested/observed Opus model verified，Plan=PLAN_READY，final full diff=PASS"
  - id: "REPAIR-AC-11"
    text: "普通非强制 Git 集成遵守 freshness 与 Review 绑定；至少 post-merge phase0/main CI 绑定 merge SHA 且全部触发 job 绿色；红 CI 不得完成"
  - id: "REPAIR-AC-12"
    text: "Gate 2 展示 Git 与 companion 分账证据、CI、Review、风险和非破坏性回滚；Gate 2 前后均不自动启动 03—05"
failure_examples:
  - "任一 required artifact 缺失/hash 漂移或 descriptor/TASK_INDEX/HEAD 漂移 -> artifact_drift"
  - "需要修改 allowed paths 外任一 repo/个人/历史路径 -> scope_expansion_required"
  - "把 absent phase-task companion 重新创建、把 companion 声称进 Git、或无 baseline backup 覆盖现有 companion -> filesystem_companion_boundary_violation"
  - "复用旧 v2 state、旧 bounded Gate 1、旧 Review、旧 candidate 或 GCR-008 已消费例外 -> legacy_evidence_reuse_forbidden"
  - "Plan/final packet audit 非 CLEAR，Claude verdict/model/meta/hash 不闭合，测试或 CI 红 -> fail_closed"
step_verification_points:
  - "preflight: current HEAD/remote/dirty, descriptor and TASK_INDEX hash, canonical/mirror parity, every required artifact hash"
  - "candidate: exact Git changed paths plus separate filesystem baseline/candidate/operation manifests"
  - "pre-review: deterministic validation, Codex self-review, secret scan, complete untruncated packet"
  - "pre-integration: final Review freshness, commit parent/tree/diff binding, target head/mergeability/auth/policy refresh"
  - "post-integration: merge SHA CI plus companion apply/no-op evidence, then Gate 2 stop"
validation_commands:
  - "python C:/Users/Administrator/.codex/skills/codex-claude/scripts/mirror_check.py"
  - "python -c \"from pathlib import Path; import sys; sys.path.insert(0, r'C:/Users/Administrator/.codex/skills/codex-claude/scripts'); from goal_contract import parse_task_contract; print(parse_task_contract(Path(r'docs/phase1/tasks/P1-WORKFLOW-002-REPAIR-001.md'))['task_id'])\""
  - "python -c \"import yaml; [yaml.safe_load(open(p, encoding='utf-8')) for p in ['docs/dev/task_record_schema.yaml']]; print('YAML_OK')\""
  - "uv run pytest tests/architecture/ -q -p no:cacheprovider"
  - "uv run python scripts/run_golden_tasks.py --gate"
  - "uv run python scripts/check_weak_tests.py tests"
  - "git diff --check"
  - "git diff --cached --name-only"
  - "git diff --name-only"
  - "git ls-files --others --exclude-standard"
evidence_requirements:
  - "每个 required artifact 的 path、expected/actual SHA-256、bytes 与检查时间"
  - "每个 REPAIR-AC ID 的 passed/failed 结果与具体 command/file:line/manifest/CI 证据"
  - "Git candidate parent/tree/raw diff/changed paths/blob ids；filesystem companion baseline/candidate/diff/operation/rollback manifest 分开绑定"
  - "独立 packet audit、Codex self-review、Claude meta/prompt/output/provider/model 的 SHA-256 与 verdict"
  - "task-branch/PR checks（若当前 workflow 实际触发）及 post-merge phase0/main CI run id、headSha、job conclusions"
  - "Task Record changed_files 只能列 Git staged paths；companion 只列入单独 filesystem_companion_evidence"
stop_conditions:
  - "任一 required artifact 缺失/hash 漂移，或 current descriptor/TASK_INDEX/HEAD/remote/parity 不闭合"
  - "需要修改 forbidden path、第三类路径、历史 W002 artifact、个人 controller 或 D:/repair-task"
  - "出现删除、secret/env、DB、全局/系统、发布、rebase、reset-hard、force push等 R3；本 descriptor 未授权任何 R3"
  - "filesystem companion baseline 在 candidate/review/apply 之间漂移，或 absent 状态只能靠创建/删除恢复"
  - "独立 audit 非 CLEAR、Claude 非 model-verified PLAN_READY/PASS、packet 截断、验证失败或 secret 命中"
  - "auth/branch protection/mergeability/freshness/CI 失败或 CI 未绑定当前 commit/merge SHA"
  - "任何步骤将自动启动 P1-CI-ALIGN-001、P1-OBS-001 或 P1-RUNTIME-ENTRY-001"
local_commit_policy: "after_review_pass"
local_commit_policy_reason: "当前 task contract 的 R2 automation 明确取消独立 local-commit Gate；仍受 standard Gate 1、independent Review、repo policy 与 freshness 约束"
depends_on:
  - "P1-WORKFLOW-002 completed at 006cc04dfd24e788db273aa25b32b164142b2bfb"
  - "P1-WORKFLOW-002-REPAIR-BOOTSTRAP-001 landed"
branch: "phase1/P1-WORKFLOW-002-REPAIR-001"
references:
  - "docs/phase1/ROLE_POLICY.md"
  - "docs/phase1/TASK_PROMPT_TEMPLATE.md"
  - "docs/dev/task_record_schema.yaml"
  - "C:/Users/Administrator/.codex/skills/codex-claude/SKILL.md"
```

## Repair authority and history boundary

Commit `006cc04dfd24e788db273aa25b32b164142b2bfb` integrated the substantive `P1-WORKFLOW-002` governance edits and its passed Task Record. The original external task 02 required additional current-governance convergence, formal task 03—05 descriptors/DAG, and a truthful repo-local `phase-task` retirement boundary; those requirements were not all present in the reviewed W002 packet. The old schema-v2 state, `bounded_goal_preapproval`, old Plan/Review/candidate, and consumed GCR-008 bootstrap exception are immutable historical evidence only. This repair starts a fresh task and never upgrades, migrates, edits, or cites them as current authorization.

## Required artifacts (hash-pinned)

```yaml
required_artifacts:
  - artifact_id: "agents"
    path: "E:/code/eternalai/AGENTS.md"
    sha256: "60739f0161799238a224ae4faa36825b8ec2c5880e5b0a48eb36ad1699442a06"
  - artifact_id: "claude"
    path: "E:/code/eternalai/CLAUDE.md"
    sha256: "b0a745682867496c7c878d736310894c6386476bb628e06f9e5e89335b3e4c4c"
  - artifact_id: "role_policy"
    path: "E:/code/eternalai/docs/phase1/ROLE_POLICY.md"
    sha256: "6a8af252c42fe10919ffc830bd4f3444c5a6fdef89ce22b7f96eefb0196324bf"
  - artifact_id: "task_template"
    path: "E:/code/eternalai/docs/phase1/TASK_PROMPT_TEMPLATE.md"
    sha256: "4780467cb3761b6398ee9b6b98d27cffbcb07bcd25631b0d1d7a74ef2036be36"
  - artifact_id: "task_record_schema"
    path: "E:/code/eternalai/docs/dev/task_record_schema.yaml"
    sha256: "bb419bb30a54a71a052195e24ac02dcd8a5031b2839ac601f3977629eb93bc68"
  - artifact_id: "task_index_bootstrap"
    path: "E:/code/eternalai/docs/phase1/TASK_INDEX.md"
    sha256: "1f49a29d68d77c55f202b1faf3ffdb4a28148f0d99aaace1adb591675e1cddb7"
  - artifact_id: "historical_w002_descriptor"
    path: "E:/code/eternalai/docs/phase1/tasks/P1-WORKFLOW-002.md"
    sha256: "f1cbea58dbb9cadaa945231dae024117f09f86b80af508c82f8c3655eac4d8a0"
  - artifact_id: "historical_w002_record"
    path: "E:/code/eternalai/docs/phase1/task_logs/P1-WORKFLOW-002_20260711_230227_passed.yaml"
    sha256: "1f5c1335839a43e7849ae8db5571511049e3098f7bd4c170498a27579ab027b1"
  - artifact_id: "controller_skill"
    path: "C:/Users/Administrator/.codex/skills/codex-claude/SKILL.md"
    sha256: "041edf2bed3f955da3cbbf35193f402680490884e05cacc5eb872e547b2d6b1b"
  - artifact_id: "packet_contract"
    path: "C:/Users/Administrator/.codex/skills/codex-claude/references/packet-contracts.md"
    sha256: "12adef813139889262b5e077bdcae300d7671097b8ffe3bf555e9b4441bb4021"
  - artifact_id: "provider_contract"
    path: "C:/Users/Administrator/.codex/skills/codex-claude/references/provider-contract.md"
    sha256: "27e44aa9ff75f2c418b456bdf08f0d2c7564c5ab0bc0547b950b3c778880eaa1"
  - artifact_id: "goal_contract_parser"
    path: "C:/Users/Administrator/.codex/skills/codex-claude/scripts/goal_contract.py"
    sha256: "455d3309c9af866bf2e40e3ed675473c8cc3c70e8f7cf0b43527bd44e245210f"
  - artifact_id: "gcr008"
    path: "C:/Users/Administrator/.claude-codex-scratch/runs/LOCAL-WORKFLOW-RESET-001-REPAIR-001/evidence/GCR-008_gate2_acceptance.md"
    sha256: "fd5e9b152a3bff421b58b15c2edd66652b36696c1050758cbc4279c051993770"
  - artifact_id: "startup_prompt"
    path: "C:/Users/Administrator/.claude-codex-scratch/runs/LOCAL-WORKFLOW-RESET-001-REPAIR-001/plan/P1-WORKFLOW-002-REPAIR-001_startup_prompt.md"
    sha256: "50e6227485c9631207261036f094a816c4c9e73b3ca7c931e1b48516900f0973"
  - artifact_id: "task02"
    path: "D:/repair-task/02-repo-governance-and-redundancy.md"
    sha256: "4fd45a9bd467d91546dfaea3377f043061c636e73cabae7e02357f89b3b831d6"
  - artifact_id: "task03"
    path: "D:/repair-task/03-ci-alignment.md"
    sha256: "6b220419bd4317ec576353b41e86a3c8ca4a64c7ce23cf66b5e19ba547f196f2"
  - artifact_id: "task04"
    path: "D:/repair-task/04-observability-correctness.md"
    sha256: "b05809aa06e96b9fdae97150f57de797b725f6d029f2552e7b23d4ecb1f47b53"
  - artifact_id: "task05"
    path: "D:/repair-task/05-runtime-entry-and-golden-harness.md"
    sha256: "22b157975400164fa43c133cefa5cd816cdcc6de8578d6f1b22308188842a483"
```

Every artifact is re-hashed before Plan Review, before Gate 1, before final Review, and immediately before integration. `TASK_INDEX.md` uses the post-bootstrap hash above; no pre-bootstrap index hash may authorize this repair.

## Stable requirements and stops

```yaml
requirements:
  - requirement_id: "REPAIR-REQ-01"
    task_ids: ["P1-WORKFLOW-002-REPAIR-001"]
    source_artifact_id: "task02"
    source_anchor: "本任务的既定决策 / 当前事实种子"
    required_paths: []
    required_deliverables: []
    acceptance_ids: ["REPAIR-AC-01"]
  - requirement_id: "REPAIR-REQ-02-CI"
    task_ids: ["P1-WORKFLOW-002-REPAIR-001"]
    source_artifact_id: "task03"
    source_anchor: "任务身份 / 目标 / Acceptance Criteria"
    required_paths: ["docs/phase1/tasks/P1-CI-ALIGN-001.md"]
    required_deliverables: ["docs/phase1/tasks/P1-CI-ALIGN-001.md"]
    acceptance_ids: ["REPAIR-AC-02"]
  - requirement_id: "REPAIR-REQ-02-OBS"
    task_ids: ["P1-WORKFLOW-002-REPAIR-001"]
    source_artifact_id: "task04"
    source_anchor: "任务身份 / 目标契约 / Acceptance Criteria"
    required_paths: ["docs/phase1/tasks/P1-OBS-001.md"]
    required_deliverables: ["docs/phase1/tasks/P1-OBS-001.md"]
    acceptance_ids: ["REPAIR-AC-02"]
  - requirement_id: "REPAIR-REQ-02-RUNTIME"
    task_ids: ["P1-WORKFLOW-002-REPAIR-001"]
    source_artifact_id: "task05"
    source_anchor: "任务身份 / 目标 / Acceptance Criteria"
    required_paths: ["docs/phase1/tasks/P1-RUNTIME-ENTRY-001.md"]
    required_deliverables: ["docs/phase1/tasks/P1-RUNTIME-ENTRY-001.md"]
    acceptance_ids: ["REPAIR-AC-02"]
  - requirement_id: "REPAIR-REQ-03"
    task_ids: ["P1-WORKFLOW-002-REPAIR-001"]
    source_artifact_id: "task02"
    source_anchor: "实施验收 7 / tasks 03—05 DAG"
    required_paths: ["docs/phase1/TASK_INDEX.md"]
    required_deliverables: ["docs/phase1/TASK_INDEX.md"]
    acceptance_ids: ["REPAIR-AC-03"]
  - requirement_id: "REPAIR-REQ-04"
    task_ids: ["P1-WORKFLOW-002-REPAIR-001"]
    source_artifact_id: "task02"
    source_anchor: "repo phase-task retirement / filesystem companion"
    required_paths: [".agents/skills/phase-task/SKILL.md"]
    required_deliverables: []
    acceptance_ids: ["REPAIR-AC-04"]
  - requirement_id: "REPAIR-REQ-05"
    task_ids: ["P1-WORKFLOW-002-REPAIR-001"]
    source_artifact_id: "historical_w002_record"
    source_anchor: "changed_files / remaining_risks / historical completion"
    required_paths: ["docs/phase1/TASK_INDEX.md"]
    required_deliverables: []
    acceptance_ids: ["REPAIR-AC-05"]
  - requirement_id: "REPAIR-REQ-06"
    task_ids: ["P1-WORKFLOW-002-REPAIR-001"]
    source_artifact_id: "agents"
    source_anchor: "Phase 1 rule authority / hard rules"
    required_paths:
      - "AGENTS.md"
      - "CLAUDE.md"
      - "docs/phase1/ROLE_POLICY.md"
      - "docs/phase1/TASK_PROMPT_TEMPLATE.md"
      - "docs/dev/task_record_schema.yaml"
    required_deliverables: []
    acceptance_ids: ["REPAIR-AC-06"]
  - requirement_id: "REPAIR-REQ-07"
    task_ids: ["P1-WORKFLOW-002-REPAIR-001"]
    source_artifact_id: "controller_skill"
    source_anchor: "Candidate isolation / Integration"
    required_paths: []
    required_deliverables: []
    acceptance_ids: ["REPAIR-AC-07"]
  - requirement_id: "REPAIR-REQ-08"
    task_ids: ["P1-WORKFLOW-002-REPAIR-001"]
    source_artifact_id: "goal_contract_parser"
    source_anchor: "parse_task_contract strict keys"
    required_paths: []
    required_deliverables: []
    acceptance_ids: ["REPAIR-AC-08"]
  - requirement_id: "REPAIR-REQ-09"
    task_ids: ["P1-WORKFLOW-002-REPAIR-001"]
    source_artifact_id: "task02"
    source_anchor: "验证"
    required_paths: []
    required_deliverables: []
    acceptance_ids: ["REPAIR-AC-09"]
  - requirement_id: "REPAIR-REQ-10"
    task_ids: ["P1-WORKFLOW-002-REPAIR-001"]
    source_artifact_id: "packet_contract"
    source_anchor: "Plan packet / Review packet pre-dispatch gates"
    required_paths: []
    required_deliverables: []
    acceptance_ids: ["REPAIR-AC-10"]
  - requirement_id: "REPAIR-REQ-11"
    task_ids: ["P1-WORKFLOW-002-REPAIR-001"]
    source_artifact_id: "controller_skill"
    source_anchor: "Git mode / Gate 2 / CI freshness"
    required_paths: []
    required_deliverables: []
    acceptance_ids: ["REPAIR-AC-11"]
  - requirement_id: "REPAIR-REQ-12"
    task_ids: ["P1-WORKFLOW-002-REPAIR-001"]
    source_artifact_id: "startup_prompt"
    source_anchor: "Required reporting / no auto-next"
    required_paths: ["docs/phase1/task_logs/P1-WORKFLOW-002-REPAIR-001_*.yaml"]
    required_deliverables: ["docs/phase1/task_logs/P1-WORKFLOW-002-REPAIR-001_*.yaml"]
    acceptance_ids: ["REPAIR-AC-12"]
required_stops:
  - kind: "human_pre_apply"
    phase: "pre_apply"
  - kind: "human_result_acceptance"
    phase: "post_integration"
  - kind: "no_auto_next"
    phase: "final_result_only"
explicit_deferrals: []
r3_authorization: []
```

This task always uses standard human Gate 1. A future separately supplied bounded Goal may be evaluated only if it independently hash-matches this descriptor and all artifacts; this descriptor itself never asserts `bounded_goal_preapproval`.

## Filesystem companion boundary

`.agents/skills/phase-task/SKILL.md` is not part of the Git candidate or Git commit. Preflight records `Test-Path`, ignore provenance, byte count, SHA-256, and full baseline bytes in repo-external scratch. If present, build a candidate copy that retains valid frontmatter and only routes legacy diagnosis to canonical `codex-claude`; freeze baseline/candidate/diff/operation/rollback hashes and include them in both independent audits and Claude's final packet. Apply only after Git integration and green post-merge CI, with an immediate baseline freshness recheck; rollback restores the exact retained baseline bytes. If absent, record `already_absent`, make no filesystem mutation, do not recreate it, and report rollback as no-op with evidence. Any other filesystem path or any need to create/delete/move the companion stops the task.

## Historical artifact boundary

`docs/phase1/tasks/P1-WORKFLOW-002.md` and `docs/phase1/task_logs/P1-WORKFLOW-002_20260711_230227_passed.yaml` remain byte-identical completed history. Active routing is expressed by this descriptor, the current `TASK_INDEX.md`, and minimal evidence-backed edits to other allowed active rules. Never rewrite history to make old authorization appear valid.

## Review, integration, CI, and rollback

1. Codex writes the Plan. Before Claude Plan Review, an independent read-only packet auditor must return `CLEAR`; then Claude Opus must return model-verified `PLAN_READY`; Codex sanity-checks the exact scope before standard Gate 1.
2. Build Git changes in an independent `git clone --no-hardlinks`; build any present companion candidate separately in scratch. Reclassify risk and validate both surfaces without writing the target.
3. Stage exact Git paths only. The final packet contains the complete untruncated staged diff, Task Record, validation evidence, and the complete companion logical diff/operation/rollback manifest. A fresh independent packet audit `CLEAR` precedes Claude's model-verified final `PASS`.
4. Refresh evidence immediately before integration. Commit and ordinary non-force push/PR/merge target `phase0/main`; never force, rebase, reset-hard, bypass hooks/protection, or merge red CI.
5. Because `P1-CI-ALIGN-001` is downstream, pre-merge CI may not yet trigger for `phase1/**`; record the live event/protection evidence and never call absence green. Post-merge `phase0/main` CI is mandatory, must bind the merge SHA, and every triggered job must be green.
6. After green post-merge CI, apply or no-op the reviewed filesystem companion operation with freshness evidence. Gate 2 reports Git commit/merge/CI and companion manifest separately and blocks auto-next.
7. Before merge, rollback means stop and preserve evidence; do not delete worktrees/candidates. After merge, Git rollback is a new authorized `git revert <merge_sha>` task. Companion rollback restores retained bytes only when the baseline existed; absent/no-op has nothing to restore. No reset/rebase/force/delete is an automatic rollback.

## Task Record

Create one full high-tier YAML record at `docs/phase1/task_logs/P1-WORKFLOW-002-REPAIR-001_<YYYYMMDD_HHMMSS>_<passed|failed|blocked>.yaml`. Its `changed_files` exactly equals `git diff --cached --name-only`; filesystem companion evidence is a separate field and is never inserted into `changed_files`. The record uses the deferred commit-SHA convention and records artifact lifecycle, validation, Review bindings, CI, rollback, and remaining risks.

CODEX_CLAUDE_TASK_CONTRACT_BEGIN
{
  "task_id": "P1-WORKFLOW-002-REPAIR-001",
  "allowed_paths": [
    "AGENTS.md",
    "CLAUDE.md",
    "docs/phase1/ROLE_POLICY.md",
    "docs/phase1/TASK_PROMPT_TEMPLATE.md",
    "docs/dev/task_record_schema.yaml",
    "docs/phase1/tasks/P1-CI-ALIGN-001.md",
    "docs/phase1/tasks/P1-OBS-001.md",
    "docs/phase1/tasks/P1-RUNTIME-ENTRY-001.md",
    "docs/phase1/TASK_INDEX.md",
    "docs/phase1/task_logs/P1-WORKFLOW-002-REPAIR-001_*.yaml",
    ".agents/skills/phase-task/SKILL.md"
  ],
  "forbidden_paths": [
    "docs/phase1/tasks/P1-WORKFLOW-002-REPAIR-001.md",
    "docs/phase1/tasks/P1-WORKFLOW-002.md",
    "docs/phase1/task_logs/P1-WORKFLOW-002_*.yaml",
    "docs/phase1/tasks/P1-SPEC-001.md",
    "docs/phase1/tasks/P1-PARAM-001.md",
    "docs/phase1/PHASE1_SPEC.md",
    "app/**",
    "tests/**",
    "scripts/**",
    "web/**",
    ".github/**",
    "alembic/**",
    "infra/**",
    "docs/blueprint/**",
    "docs/phase0/**",
    "_scratch/**",
    ".worktrees/**",
    ".env",
    ".env.*",
    "uv.lock",
    "web/package.json",
    "web/pnpm-lock.yaml"
  ],
  "required_deliverables": [
    "docs/phase1/tasks/P1-CI-ALIGN-001.md",
    "docs/phase1/tasks/P1-OBS-001.md",
    "docs/phase1/tasks/P1-RUNTIME-ENTRY-001.md",
    "docs/phase1/TASK_INDEX.md",
    "docs/phase1/task_logs/P1-WORKFLOW-002-REPAIR-001_*.yaml"
  ],
  "acceptance_ids": [
    "REPAIR-AC-01",
    "REPAIR-AC-02",
    "REPAIR-AC-03",
    "REPAIR-AC-04",
    "REPAIR-AC-05",
    "REPAIR-AC-06",
    "REPAIR-AC-07",
    "REPAIR-AC-08",
    "REPAIR-AC-09",
    "REPAIR-AC-10",
    "REPAIR-AC-11",
    "REPAIR-AC-12"
  ]
}
CODEX_CLAUDE_TASK_CONTRACT_END
