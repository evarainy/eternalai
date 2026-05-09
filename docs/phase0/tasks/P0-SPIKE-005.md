# P0-SPIKE-005 — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- AGENTS.md
- CLAUDE.md when using Claude Code
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only as source of truth; do not paste it in full.

## Global hard rules

- Execute only this task_id.
- Do not modify frozen blueprint files.
- Do not implement Phase 1 features.
- Do not add unapproved dependencies.
- Do not weaken tests to pass.
- Stop after Unified Task Record and wait for human confirmation.

## Task YAML

```yaml
task_id: P0-SPIKE-005
title: Target Business Systems API and Authentication Reconnaissance
type: spike
depends_on:
  - P0-PREP-003
objective: 初步探查泛微 OA、用友 U8、海康 iVMS 的 API 类型、认证机制、权限边界、账号绑定可能性和 Mock Adapter 形态。
deliverable:
  - docs/adr/phase0/ADR-P0-SPIKE-005a-oa-api-auth.md
  - docs/adr/phase0/ADR-P0-SPIKE-005b-u8-api-auth.md
  - docs/adr/phase0/ADR-P0-SPIKE-005c-hikvision-ivms-api-auth.md
constraints:
  - 只做文档、测试环境或访谈探查
  - 不接生产系统
  - 不保存真实凭证
  - 不实现正式 Adapter
acceptance_criteria:
  - 每个目标系统各完成一份 ADR
  - 每份 ADR 记录 API 类型、认证方式、是否支持 OAuth2、是否需要 Vault、是否存在厂商 token、是否支持用户级权限
  - 每份 ADR 记录 Phase 1 是否只做 Mock、是否允许只读 PoC、是否禁止写入
  - 明确 Identity Binding 对该系统的最小需求
spike_code_disposition: 废弃或沉淀为 tests/utils/
touched_paths:
  - docs/adr/phase0/
  - docs/research/phase0/target_systems/
  - tests/utils/
forbidden_paths:
  - app/execution_fabric/
  - app/gateway/
  - app/runtime/
```

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.


## v1.0.11 Sub-ADR note

P0-SPIKE-005 is the only task_id. ADR filenames with suffixes 005a/005b/005c are deliverable identifiers only, not independent task IDs. Task Record must use `task_id: P0-SPIKE-005`.

Each target-system ADR must include: system_name, version_or_assumed_version, auth_mode, token/session lifecycle, user identity mapping method, permission source of truth, read/write API availability, callback/webhook availability, rate/concurrency limit, audit log availability, sandbox/test environment availability, irreversible operation list, Phase 0 allowed/forbidden operations, open questions, blocking risks, and recommendation: mock_only | can_build_adapter_later | needs_vendor_confirmation | not_suitable. If vendor docs or test environment are missing, mark needs_vendor_confirmation. Do not connect to or write to production OA/U8/Hik systems.
