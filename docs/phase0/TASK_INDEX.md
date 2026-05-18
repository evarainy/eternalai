# TASK_INDEX — Phase 0 Dependency DAG v1.0.11

本文件是 Phase 0 的任务依赖 DAG。Codex / Claude Code 必须按批次和 `depends_on` 执行，不得跳过前置任务。

**强制单任务执行：每次只能执行一个 `task_id`。完成该 task 并输出统一 Task Record 后，等待人工确认，再进入下一个 task。**

`P0-PREP-*` 是 execution-pack-only preparation tasks，不属于业务实现任务；它们只检查仓库、文档、模板和执行一致性。完成 `P0-PREP-*` 后才允许进入 `P0-SPIKE-*`。

## 0. 批次总览

```text
Batch 0：仓库准备
  ↓
Batch 1：Spike ADR（可跨不同执行环境并行；单个 Codex/Claude 会话内仍每次只执行一个 task_id；都不得产生生产代码）
  ↓
Batch 2：工程骨架
  ↓
Batch 3：接口契约冻结
  ↓
Batch 4：Mock Adapter 与错误注入
  ↓
Batch 5：Gateway / Policy / Identity / Trace 骨架
  ↓
Batch 6：Runtime 主链
  ↓
Batch 7：Golden Task 验收
```


## Batch 2-7 per-task prompt gate

当前执行包只包含 Batch 0 / Batch 1 的 per-task prompt。Batch 1 完成后、Batch 2 启动前，必须根据 Phase 0 spec 生成 Batch 2-7 的 `docs/phase0/tasks/<task_id>.md` 文件。不得在缺少 per-task prompt 的情况下执行 Batch 2-7。

## 1. Batch 0 — 仓库准备

| task_id | title | depends_on | deliverable |
|---|---|---|---|
| P0-PREP-001 | Repository and Environment Readiness Check | none | repo/env audit log |
| P0-PREP-002 | Phase 0 Docs Directory and Template Setup | P0-PREP-001 | docs/phase0/ templates |
| P0-PREP-003 | Freeze Spec v1.0.11 Placement Check | P0-PREP-002 | blueprint docs copied/verified |

## 2. Batch 1 — Spike ADR

| task_id | title | depends_on | can_parallel | requires_gpu | deliverable |
|---|---|---|---:|---:|---|
| P0-SPIKE-001 | Qwen Local Model Structured Output Spike | P0-PREP-003 | yes | yes | ADR-P0-SPIKE-001 |
| P0-SPIKE-002 | instructor + vLLM OpenAI-compatible API Stability Spike | P0-PREP-003 | yes | yes | ADR-P0-SPIKE-002 |
| P0-SPIKE-003 | PostgreSQL 18 + pgvector >= 0.8.2 Deployment Spike | P0-PREP-003 | yes | no | ADR-P0-SPIKE-003 |
| P0-SPIKE-004 | Redis + ARQ Baseline Spike | P0-PREP-003 | yes | no | ADR-P0-SPIKE-004 |
| P0-SPIKE-005 | Target Business Systems API and Authentication Reconnaissance | P0-PREP-003 | yes | no | three ADR-P0-SPIKE-005*-*.md deliverables（ADR 文件名，不是独立 task_id） |
| P0-SPIKE-006 | S3-compatible Object Storage Candidate Spike | P0-PREP-003 | yes | no | ADR-P0-SPIKE-006 |
| P0-SPIKE-007 | PydanticAI + Qwen/vLLM Compatibility Spike | P0-PREP-003 | yes | yes | ADR-P0-SPIKE-007；recommended_after: P0-SPIKE-001, P0-SPIKE-002 |

### P0-SPIKE-005 子 ADR 说明

`P0-SPIKE-005` 是唯一任务 ID。OA / U8 / Hik 三个 ADR 使用 `ADR-P0-SPIKE-005a-*`、`ADR-P0-SPIKE-005b-*`、`ADR-P0-SPIKE-005c-*` 文件名区分，但这些文件名不是独立任务 ID。Task Record 中 `task_id` 必须统一写 `P0-SPIKE-005`。

## 3. Batch 2 — 工程骨架

| task_id | title | depends_on | notes |
|---|---|---|---|
| P0-INFRA-008 | Internal Dependency Mirror and Dependency Allowlist Baseline | P0-PREP-003 | 必须先完成，防止内网依赖卡死 |
| P0-INFRA-001 | Docker Compose Single-node Baseline | P0-INFRA-008 | 不接真实生产系统 |
| P0-INFRA-002 | Python uv + FastAPI Backend Skeleton | P0-INFRA-008 | 只做健康检查和骨架 |
| P0-FE-SPIKE-001 | Ant Design X Compatibility and Dependency Spike | P0-INFRA-008 | 决定 @ant-design/x 是否进入前端依赖 allowlist |
| P0-INFRA-003A | Frontend Dependency Policy and pnpm Availability Gate | P0-FE-SPIKE-001 | 使用 spike 结果更新 npm allowlist；不创建 web/ 或 lockfile |
| P0-FE-GUIDE-001 | Frontend AI Coding Guidelines | P0-INFRA-003A | 创建 docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md |
| P0-INFRA-003 | React 18 + Vite + Ant Design 5.x Frontend Skeleton | P0-INFRA-003A, P0-FE-GUIDE-001 | 必须包含 generate:api；不决定 Ant Design X 或 npm allowlist |
| P0-FE-ARCH-001 | SDUI Renderer Architecture Baseline | P0-INFRA-003 | 仅架构定义；不实现 web/src/sdui/** |
| P0-INFRA-004 | PostgreSQL Schema and Alembic Baseline | P0-SPIKE-003, P0-INFRA-001, P0-INFRA-002 | 仅在 pgvector Spike 通过或条件通过后执行 |
| P0-INFRA-006 | OpenTelemetry + Langfuse Baseline Deployment | P0-INFRA-001, P0-INFRA-002 | Golden Task 前置条件，不可降级；仅验 API health / Spike trace，Gateway trace 后移 |
| P0-INFRA-007 | CI Lint Type Check Staged Import Boundary and Test Baseline | P0-INFRA-002, P0-INFRA-003, P0-INFRA-004, P0-INFRA-006, P0-INFRA-008 | 必须包含 import boundary check；未实现模块检查标记 not_applicable，不得提前造 Runtime/Gateway/Trace |
| P0-INFRA-005 | Redis + ARQ Baseline via JobQueuePort Candidate | P0-SPIKE-004, P0-INFRA-001, P0-INFRA-002 | 仅当 ADR 为 passed / conditionally_passed 时执行 |

## 4. Batch 3 — 接口契约冻结

| task_id | title | depends_on |
|---|---|---|
| P0-DOMAIN-001a | Task and Session Interface Contract | P0-INFRA-002 |
| P0-DOMAIN-002a | Capability Interface Contract | P0-INFRA-002 |
| P0-DOMAIN-003a | Capability Gateway Interface Contract | P0-INFRA-002 |
| P0-DOMAIN-004a | Policy Guard Interface Contract | P0-INFRA-002 |
| P0-DOMAIN-005a | Trace Interface Contract | P0-INFRA-002, P0-INFRA-006 |
| P0-DOMAIN-006a | IdentityMapping Interface Contract | P0-INFRA-002 |
| P0-DOMAIN-007a | Runtime Interface Contract | P0-INFRA-002 |
| P0-DOMAIN-008a | Adapter Interface Contract | P0-INFRA-002 |
| P0-DOMAIN-009a | SDUI Response Envelope Contract | P0-INFRA-002 |
| P0-DOMAIN-010a | LLM Provider and Structured Output Port Contract | P0-SPIKE-001, P0-SPIKE-002, P0-INFRA-002 |
| P0-DOMAIN-011a | SecretProvider Interface Contract | P0-INFRA-002 |

### Batch 1 GPU 阻塞处理

如果 P0-PREP-001 记录 GPU / CUDA / vLLM 不可用，不得阻塞整个 Batch 1。先执行 requires_gpu=no 的 P0-SPIKE-003/004/005/006；GPU 就绪后再执行 P0-SPIKE-001/002/007。

## 5. Batch 4 — Mock Adapter 与错误注入

| task_id | title | depends_on |
|---|---|---|
| P0-DOMAIN-008b | Mock OA Adapter | P0-DOMAIN-008a |
| P0-DOMAIN-008c | Mock U8 Adapter | P0-DOMAIN-008a |
| P0-DOMAIN-008d | Mock Hikvision iVMS Adapter | P0-DOMAIN-008a |
| P0-DOMAIN-008e | Mock Adapter Error Injection Control Endpoint | P0-DOMAIN-008b, P0-DOMAIN-008c, P0-DOMAIN-008d |
| P0-DOMAIN-010b | Mock Structured Output Implementation | P0-DOMAIN-010a |
| P0-DOMAIN-011b | Noop SecretProvider Skeleton | P0-DOMAIN-011a |

## 6. Batch 5 — Gateway / Policy / Identity / Trace 骨架

| task_id | title | depends_on |
|---|---|---|
| P0-DOMAIN-001b | Task and Session Minimal Skeleton | P0-DOMAIN-001a, P0-INFRA-004 |
| P0-DOMAIN-002b | Capability Model and Registry Minimal CRUD | P0-DOMAIN-002a, P0-INFRA-004 |
| P0-DOMAIN-004b | Policy Guard Minimal Deny Skeleton | P0-DOMAIN-004a |
| P0-DOMAIN-005b | Trace Minimal Write Skeleton | P0-DOMAIN-005a, P0-INFRA-006 |
| P0-DOMAIN-006b | IdentityMapping Mock Table and Precheck Skeleton | P0-DOMAIN-006a, P0-INFRA-004 |
| P0-DOMAIN-009b | SDUI Response Envelope Minimal Implementation | P0-DOMAIN-009a |
| P0-DOMAIN-003b0 | Capability Gateway Pass-through Integration Skeleton | P0-DOMAIN-003a, P0-DOMAIN-005a, P0-DOMAIN-008a, P0-DOMAIN-008b |
| P0-DOMAIN-003b1 | Capability Gateway Short-circuit Skeleton | P0-DOMAIN-003a, P0-DOMAIN-003b0, P0-DOMAIN-002b, P0-DOMAIN-004b, P0-DOMAIN-005b, P0-DOMAIN-006b, P0-DOMAIN-011b |
| P0-DOMAIN-003b2 | Capability Gateway Adapter Execution Skeleton | P0-DOMAIN-003b1, P0-DOMAIN-008b, P0-DOMAIN-008c, P0-DOMAIN-008d, P0-DOMAIN-008e |

关键路径说明：`P0-DOMAIN-003b0`、`P0-DOMAIN-002b`、`P0-DOMAIN-004b`、`P0-DOMAIN-005b`、`P0-DOMAIN-006b`、`P0-DOMAIN-011b` 是 Gateway 短路骨架的核心前置；`008b/008c/008d/008e` 可并行推进，进入 `P0-DOMAIN-003b2` 后再验证 Adapter 执行与错误映射。

## 7. Batch 6 — Runtime 主链

| task_id | title | depends_on |
|---|---|---|
| P0-DOMAIN-007b | Runtime Main Chain Minimal Skeleton | P0-DOMAIN-007a, P0-DOMAIN-001b, P0-DOMAIN-003b2, P0-DOMAIN-005b, P0-DOMAIN-009b, P0-DOMAIN-010b |

## 8. Batch 7 — Golden Task 验收

P0-GT-001/002/003 的完整任务 YAML 定义见 Phase 0 spec 第 12.8 节。Golden Task 正向任务通过率必须 >= 80%；负向路径 / 边界路径 / 安全拒绝路径必须 100% 通过。

| task_id | title | depends_on |
|---|---|---|
| P0-GT-001 | Golden Task Fixture Materialization | P0-DOMAIN-007b, P0-DOMAIN-008e |
| P0-GT-002 | Golden Task Test Runner | P0-GT-001, P0-INFRA-007 |
| P0-GT-003 | Phase 0 Acceptance Run | P0-GT-002, P0-SPIKE-001, P0-SPIKE-002, P0-SPIKE-003, P0-SPIKE-004, P0-SPIKE-005, P0-SPIKE-006, P0-SPIKE-007, P0-INFRA-007 |

## 9. 硬顺序摘要

```text
Spike ADR 完成
→ 工程骨架完成
→ 接口契约冻结
→ Mock Adapter + 错误注入
→ Gateway 透传版
→ Policy / Identity / Trace / SDUI 骨架
→ Gateway 短路骨架
→ Gateway Adapter 执行骨架
→ Runtime 主链
→ Golden Task 验收
```

## 10. Unified Task Record 要求

每个任务完成、失败或阻塞时，必须生成机器可读 YAML Task Record：

```text
docs/phase0/task_logs/<task_id>_<YYYYMMDD_HHMMSS>_<passed|failed|blocked>.yaml
```

统一 schema 见：

```text
docs/dev/task_record_schema.yaml
```

v1.0.11 关键要求：

- `not_applicable` 必须包含 reason、scope、blocked_by_task_id、activation_task_id、expiry_condition 和 evidence。
- `review.mode` 可为 `none | self_check | human_optional`；human diff review 非强制。
- `package_confirmation_status` 可为 `created | not_created | not_applicable`，固定确认语不绑定人工 diff review。
