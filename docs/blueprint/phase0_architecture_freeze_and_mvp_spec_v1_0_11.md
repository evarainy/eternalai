# Phase 0 Architecture Freeze and MVP Spec v1.0.11

> 文件状态：Phase 0 执行基线 v1.0.11（冻结版小补丁）  
> 来源基线：`enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md`、`phase0_handoff_after_blueprint_freeze.md`  
> 适用范围：Phase 0 架构冻结、MVP 裁剪、技术 Spike、工程基线、接口契约、Mock 闭环、Codex / Claude Code 执行边界  
> 明确不做：不修改主蓝图；不扩写主蓝图；不提前展开 Phase 1 完整功能矩阵；不编写 Phase 2-5 spec。  

> v1.0 冻结说明：本版本已纳入执行前硬化项，包括 Golden Task given/when/then/forbidden、Mock Adapter 错误注入接口、IdentityMapping 多身份样例、内网依赖约束、CI import 边界检查、核心 Port 方法签名与 Pydantic Schema。后续不得直接改写本文件；若 Phase 0 执行中发现缺口，须通过 ADR 或 task patch 记录。  
> 本轮修订：补充内网依赖白名单、Golden Task 固定 Mock fixture、CI import 边界自动检查、Mock Adapter 错误注入、多身份 IdentityMapping、SDUI 静态渲染边界、前端 OpenAPI codegen 约束。  
> 二次审核修订：补齐 SecretProviderPort Phase 0 契约、核心 Port 输入输出 Schema、可自动化断言、Golden Task 完整 given/when/then 定义、Mock 错误注入管理端点、Trace 脱敏正则测试、Alembic 迁移规则、Orval API Client 生成流程。
> v1.0.11 小补丁：收敛单模块任务验收边界、补充状态映射表、为 Mock 错误注入端点增加测试环境硬门、强化单任务执行纪律、补充 CI 分阶段 / not_applicable 机制、将 Gateway 观测验收后移、补充敏感词扫描白名单规则。

> v1.0.11 修订说明：本版仅补齐执行前的小范围覆盖缺口，包括新增 GT-012 多账套 scope 澄清负向 Golden Task、短路终态 Trace 事件矩阵、Golden Task CI 实际执行 evidence、Batch 2-7 per-task prompt 门禁、Golden Task capability schema fixture、前端 OpenAPI/SDUI mock fixture 位置和 sanitizer 扩展占位；不新增业务范围，不修改冻结主蓝图。  
---

## 1. Document Purpose

本文件用于在主蓝图冻结后，把 Phase 0 从“方向性讨论”转化为“可执行任务书”。

Phase 0 的目标不是实现完整企业智能助手，而是完成以下工作：

1. 验证关键技术风险，形成 ADR。
2. 搭建最小工程基线，保证后续开发有稳定仓库结构、启动方式、迁移方式、测试方式和观测方式。
3. 冻结 Runtime、Capability Gateway、Policy Guard、Trace、Identity Binding、SDUI 的最小接口契约和骨架边界。
4. 构建 Mock 环境与 Golden Task 测试集，验证主链路可跑通，异常路径可截断。
5. 明确 Codex / Claude Code 后续开发任务边界，防止跨模块生成、绕过 Gateway、实验代码污染正式目录。
6. 形成 Phase 1 spec 的启动依据，而不是在 Phase 0 阶段提前编写 Phase 1 完整功能矩阵。

Phase 0 的核心产物包括：

- Spike ADR 集合。
- Docker Compose 单机部署基线。
- 后端 FastAPI / Python uv 项目骨架。
- 前端 React 18 / Vite / Ant Design 5.x 项目骨架。
- PostgreSQL + Alembic 基线。
- Redis + ARQ 基线验证。
- OpenTelemetry + Langfuse 可观测性基线。
- 核心 Port / Protocol / ABC 接口契约。
- Runtime / Gateway / Policy / Trace / Identity / SDUI 最小骨架。
- Mock OA / Mock U8 / Mock 海康 iVMS Adapter。
- Golden Task Set，核心必选任务包含 GT-001 至 GT-010 以及 GT-012；负向 / 边界 / 安全拒绝路径必须 100% 通过。
- CI：lint + type check + test。

---

## 2. Source of Truth

### 2.1 冻结主蓝图

唯一架构来源为：

```text
enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md
```

主蓝图只负责定方向、定边界、定长期底线。Phase 0 只能从主蓝图裁剪，不得反向修改主蓝图。

### 2.2 Handoff 文件

Phase 0 交接参考文件为：

```text
phase0_handoff_after_blueprint_freeze.md
```

handoff 文件用于补充冻结后进入 Phase 0 的执行节奏、技术选型、专项文件拆分建议和初步范围建议。

### 2.3 本文件与主蓝图的关系

本文件不得替代主蓝图，也不得新增大架构概念。

本文件只做以下裁剪与冻结：

- Phase 0 要验证什么。
- Phase 0 要搭什么工程骨架。
- Phase 0 要定义哪些接口契约。
- Phase 0 如何验收。
- Phase 0 满足什么条件后才允许启动 Phase 1 spec。
- Codex / Claude Code 的后续开发边界。

---

## 3. Hard Boundaries and Spike Rules

### 3.1 不可突破的架构底线

Phase 0 所有任务必须遵守以下底线：

1. Capability Gateway 是唯一真实执行入口。
2. Runtime / Workflow / Skill / Admin Console 不得直接调用 Adapter、SDK、DB Client、RPA Client、IoT Client、Shell 或生产系统接口。
3. LLM 不得直接调用生产工具，不得读取凭证，不得绕过 Capability Gateway。
4. 凭证、密码、token、cookie、session token 不得进入 LLM、Prompt、Memory、Trace 摘要或普通日志。
5. 凭证只能由 SecretProviderPort 在执行瞬间注入 Adapter。
6. user_delegated Capability 不得因为用户未绑定而 fallback 到 system_scope 服务账号。
7. Controlled Exploration 对 OA / U8 / 海康等封闭业务系统默认关闭。
8. 原业务系统的数据权限仍由原系统判断；AI 平台不做统一业务权限中心。
9. Policy Guard 对用户、部门、角色、数据范围、绑定状态、目标系统、Capability 风险等级进行执行前决策。
10. Trace / Evidence / Audit 是事实账本，所有高风险动作、用户确认、拒绝、失败、未绑定、权限拒绝都必须可追溯。
11. 原始 IoT 高频信号、视频帧流、设备心跳流不得进入 LLM 上下文或主执行链。
12. SDUI 前端不得直接执行业务动作；所有 user_action 必须回到 Runtime / Capability Gateway。
13. Phase 1 默认不开放 Dynamic Tool Composition；无现成能力时返回 `no_capability_found`。
14. Spike 不得变成生产功能。

### 3.2 Phase 0 不做事项

Phase 0 明确不做：

- 不接真实生产 OA / U8 / 海康写入能力。
- 不实现生产级凭证托管。
- 不实现完整账号绑定管理产品。
- 不实现完整 Workflow Engine。
- 不实现完整 Skill CI/CD。
- 不实现完整 Memory Fabric。
- 不实现复杂多 Agent 协作。
- 不实现生产级 Controlled Exploration。
- 不实现 RPA / Local Worker 生产主链。
- 不实现原生 iOS / Android App。
- 不实现 ASR / 语音入口。
- 不引入 Temporal / LangGraph / Celery / Milvus / Elasticsearch 作为 Phase 0 主线。
- 不编写 Phase 2-5 spec。
- 不在本文件展开 Phase 1 完整功能矩阵。

### 3.3 Spike 硬约束

Spike 是验证任务，不是生产开发任务。

每个 Spike 必须满足：

- 产出 ADR，不产出生产模块。
- 可以产生临时脚本、demo 或测试样例，但不得进入 `app/` 正式模块目录。
- ADR 必须记录验证环境、版本、配置、测试样例、通过/失败判据、结论、风险、对后续设计影响。
- ADR 必须明确是否建议进入 Phase 1、Phase 2 或后续阶段。
- Spike 代码必须在 Phase 0 结束前完成处置。

### 3.4 Spike ADR 必填字段

每个 Spike ADR 必须包含以下字段：

```yaml
title: ADR 标题
status: proposed | accepted | rejected | superseded
spike_task_id: P0-SPIKE-XXX
date: YYYY-MM-DD
context: 为什么要验证
question: 本 Spike 要回答的问题
environment:
  hardware: GPU / CPU / memory / OS
  software: runtime / framework / service version
  model: 模型名称、量化方式、max_model_len、推理服务
method: 验证方式
pass_criteria: 通过标准
fail_criteria: 失败标准
result: passed | failed | partially_passed
observations: 关键现象
risks: 风险与限制
decision: 架构决策
impact_on_phase1: 对 Phase 1 的影响
impact_on_later_phases: 对后续阶段的影响
spike_code_disposition:
  废弃: 所有仅用于验证的临时脚本，Phase 0 结束前删除
  沉淀为 test_utils: 可复用的测试夹具、Mock 响应、边界值样例，只能进入 tests/utils/
  禁止: Spike 代码以任何形式进入 app/ 下的正式模块目录
```

### 3.5 Spike 代码处置规则

```yaml
spike_code_disposition:
  废弃: 所有仅用于验证的临时脚本，Phase 0 结束前删除
  沉淀为 test_utils: 可复用的测试夹具、Mock 响应、边界值样例，只能进入 tests/utils/
  禁止: Spike 代码以任何形式进入 app/ 下的正式模块目录
```

该规则的目的：防止实验性代码通过复制粘贴惯性流入 Phase 1 生产目录。

### 3.6 内网依赖与依赖白名单硬约束

Phase 0 默认面向企业内网或受限网络环境执行。所有 Codex / Claude Code 任务必须遵守以下依赖规则：

1. 禁止 AI 自行添加未在企业内网 PyPI / npm 镜像缓存的依赖。
2. Python 依赖必须优先从 `pyproject.toml` 已知范围内选择；新增依赖必须在任务说明中列出用途、版本范围、是否已进入内网镜像缓存。
3. 前端依赖必须优先从 `web/package.json` 已知范围内选择；新增依赖必须说明用途、版本范围、是否已进入内网 npm 镜像缓存。
4. `uv.lock`、`pnpm-lock.yaml` 或等价 lockfile 的变更必须在开发日志中说明原因。
5. Spike 依赖不得进入生产依赖组；临时验证依赖只能进入 Spike 环境或 `dev` / `test` 依赖组，Phase 0 结束前必须处置。
6. 禁止为了绕过依赖问题临时切换技术栈，例如把 ARQ 替换为 Celery、把 pgvector 替换为外部向量库、把 Langfuse 替换为普通日志。
7. 如果内网镜像缺失依赖，应暂停该任务并输出依赖缺口清单，不得私自改用外部网络或未知包。

该规则属于 Phase 0 执行硬约束，不改变冻结蓝图架构。

### 3.7 可自动化断言硬约束

Phase 0 spec 中的“验证”“支持”“可走通”必须落为可自动化判定的断言。Codex / Claude Code 不得用“返回 `status=ok`”或“测试只断言 HTTP 200”的方式通过验收。

所有涉及 API、Port、Gateway、Adapter、Runtime、Trace、SDUI、Golden Task 的任务必须满足：

1. **输入 Schema 固定**：请求参数、上下文字段、枚举值必须在任务或公共契约中定义。
2. **输出 Schema 固定**：返回结构必须引用本文件第 8.6 节定义的 Pydantic / Protocol 契约；不得由实现任务自行发明字段。
3. **枚举值固定**：状态、错误码、UI component_type、Trace event_type 等必须使用本文件列出的枚举；新增枚举必须写入契约并增加测试。
4. **Trace 事件序列可断言**：Golden Task 必须断言关键事件序列，不得只断言最终响应文本。
5. **敏感信息禁止断言**：Trace / Response / Log 中不得出现 token、cookie、sessionid、内部接口地址；测试必须包含伪造敏感值并断言 sanitizer 拦截。
6. **Adapter 调用可断言**：负向路径必须声明 `adapter_assertion.must_not_be_called=true` 或具体调用次数，避免 Policy / Identity 被绕过后仍“结果正确”。
7. **Mock 返回字段固定**：Mock OA / U8 / iVMS 的返回字段必须包含固定字段与枚举，禁止只返回 `ok`、`success`、空 dict 刷通过率。

最低示例：

```yaml
bad_acceptance_criteria:
  - Mock OA 可返回待办列表，每条至少包含 workflow_id:str、title:str、status:pending|approved|rejected、applicant:str、created_at:str
  - Mock OA 可返回流程状态，至少包含 workflow_id:str、current_step:draft|pending|approved|rejected、approver:str

required_acceptance_criteria:
  - Mock OA workflow status 返回 JSON 必须包含 workflow_id:str、current_step:str、approver:str
  - current_step 枚举值仅允许 draft / pending / approved / rejected
  - 缺少 current_step 时，Gateway 必须返回 error_code=adapter_missing_required_field 并写入 trace event adapter_result_invalid
```

Trace 脱敏最低断言：

```yaml
trace_redaction_required_patterns:
  - 'Bearer\s+[A-Za-z0-9\-\._~\+\/]+'
  - "sessionid="
  - "(?i)refresh_token"
  - "(?i)access_token"
  - "(?i)api_key"
  - "(?i)x-api-key"
  - "(?i)private_key"
  - "-----BEGIN PRIVATE KEY-----"
  - "(?i)cookie:"
  - "(?i)set-cookie:"
```

Trace 写入函数在 Phase 0 至少必须对上述模式进行扫描；命中时应拒绝写入并抛出安全异常或返回标准错误。测试必须提交含伪造 token / sessionid / api_key / private_key 的输入，并断言写入失败且未落库。

---

## 4. Phase 0 Objective

Phase 0 的总体目标：

> 在不修改冻结主蓝图、不扩写新架构的前提下，验证关键技术风险，搭建可运行工程基线，冻结核心接口契约，跑通 Mock 主链路和异常截断路径，为 Phase 1 spec 提供真实依据。

Phase 0 成功后的状态应是：

1. 关键 Spike 结论明确，不再靠假设决定 Phase 1。
2. 仓库结构、包边界、import 规则、接口契约明确。
3. Docker Compose 可单机启动基础服务。
4. 后端、前端、数据库、队列、观测、测试基线可运行。
5. Capability Gateway 的隔离边界必须通过自动化 import boundary 检查验证；人工 review 仅作为 optional 复核手段。
6. Runtime 可以通过 Gateway 调用 Mock Capability。
7. Policy Guard 可以做最小 deny 截断；Gateway 通过 TracePort 统一记录 deny 事件。
8. IdentityMapping 未绑定路径可以阻止执行，并返回 SDUI 绑定引导卡。
9. SDUI Response Envelope 至少支持 message、fallback_text、confirm card / operator handback card 的最小协议。
10. Golden Task 总数不少于 10 个；正向任务总体通过率 >= 80%；负向路径、边界路径和安全拒绝路径必须 100% 通过。

---

## 5. Phase 0 → Phase 1 Handoff Conditions

本节不展开 Phase 1 功能矩阵，也不写 Phase 1 完整开发计划。Phase 1 spec 必须等 Phase 0 的关键验证结论产出后再单独编写。

### 5.1 Phase 0 必须交出的结论

Phase 0 必须交出以下结论后，才允许启动 Phase 1 spec 编写：

1. 本地模型结构化输出是否可用。
2. instructor + vLLM / OpenAI-compatible API 的工具调用与结构化输出链路是否稳定。
3. PydanticAI 与 Qwen / vLLM 的兼容性是否达到后续引入条件。
4. PostgreSQL 18 + pgvector >= 0.8.2 是否能在目标环境部署并通过最小向量查询验证。
5. Redis + ARQ 是否适合作为 L1 异步任务候选实现。
6. OpenTelemetry + Langfuse 是否能在 Golden Task 前完成链路观测。
7. 泛微 OA、用友 U8、海康 iVMS 的 API 类型与认证方式是否已初步确认。
8. S3-compatible 对象存储候选是否明确。
9. Capability Gateway import 边界是否可以通过工程方式验证。
10. Mock 环境是否能跑通正向与负向 Golden Task。

### 5.2 Phase 1 spec 启动前提

以下条件全部满足后，才允许开始编写 Phase 1 spec：

- 模型结构化输出成功率 >= 80%，基于 P0-SPIKE-001 / P0-SPIKE-002 固定测试集，不是主观判断；测试集不少于 50 条样例，至少覆盖 Intent、CapabilityRef、PlanDraft、ResponseEnvelope 四类 schema；成功定义为可解析、字段完整、枚举合法、业务关键字段不为空。
- 三个目标系统 OA / U8 / 海康的 API 类型和认证方式各完成一份 ADR。
- Golden Task 总数不少于 10 个；正向任务总体通过率 >= 80%；负向路径、边界路径和安全拒绝路径必须 100% 通过。
- PostgreSQL + pgvector >= 0.8.2 部署验证通过；Redis + ARQ Spike ADR 完成，若结论为 passed / partially_passed 则完成轻量基线，若失败则必须通过 JobQueuePort 给出替代候选 ADR 后才能编写 Phase 1 spec。
- OpenTelemetry + Langfuse 基线在 Golden Task 验证前完成并可查看关键链路。
- Capability Gateway import 边界通过自动化 import boundary 检查，Runtime 层无直接 import Adapter / execution_fabric 的引用；人工 review 仅作为 optional 复核手段。
- PydanticAI Spike ADR 完成，并明确给出 Phase 2 是否引入的建议。
- Spike 代码处置完成：临时脚本删除；可复用夹具只进入 `tests/utils/`；无 Spike 代码进入 `app/` 正式模块。

### 5.3 Phase 1 启动前仍禁止的扩展

即使 Phase 0 验收通过，Phase 1 spec 编写前仍禁止：

- 直接把 Spike demo 合并为生产 Runtime。
- 根据单一模型测试结果锁死长期模型架构。
- 将 PydanticAI 作为 Phase 1 强依赖。
- 将 ARQ 作为不可替换长期底座。
- 绕过 Capability Gateway 直接接目标业务系统。
- 提前实现生产级 Controlled Exploration。
- 提前承诺真实 OA / U8 / 海康写入闭环。

---

## 6. Minimum Viable Module Scope

本节只描述 Phase 0 最小交付范围，以及 Phase 1 的目标边界占位。Phase 1 完整功能范围不在本文件展开。

### 6.1 Runtime

**Phase 0 交付范围：**

- 定义 Runtime 相关 Port / Protocol。
- 实现最小 Task / Session 主链骨架。
- 支持接收 Mock 用户请求并生成结构化 Task。
- 支持通过 CapabilityGatewayPort 调用 Mock Capability。
- 支持 `no_capability_found` 终态。
- 支持将执行过程写入 TracePort。
- 支持 ResponseEnvelope 输出。
- 不接真实业务系统。
- 不做复杂 Planner。
- 不做 Dynamic Tool Composition。

**Phase 1 目标范围：**

- 最小可用 Runtime 实现，不在本文件展开。

### 6.2 Capability Gateway

**Phase 0 交付范围：**

- 定义 CapabilityGatewayPort。
- 定义 Capability Registry 最小字段与 CRUD。
- 实现 Gateway 骨架。
- Gateway 调用链必须包含 Registry 读取、Policy Guard 预检、Trace Pre-Record、Mock Adapter 调用、Trace Post-Record、结果返回。
- 建立 import 边界检查，保证 Runtime 不直接依赖 Adapter / execution_fabric。
- 不接真实生产 Adapter。
- 不实现 Known Read Fast Path。
- 不实现动态能力组合。

**Phase 1 目标范围：**

- 最小可用 Capability Gateway 实现，不在本文件展开。

### 6.3 Policy Guard

**Phase 0 交付范围：**

- 定义 PolicyGuardPort。
- 实现最小 allow / deny 决策骨架。
- 支持 binding_required / execution_identity 的最小检查入口。
- 支持返回 deny 决策；Trace 写入由 Gateway 通过 TracePort 统一完成。
- 支持返回标准拒绝原因。
- 不实现完整策略引擎。
- 不实现审批流。
- 不实现复杂部门数据权限模型。

**Phase 1 目标范围：**

- 最小可用 Policy Guard 实现，不在本文件展开。

### 6.4 Trace

**Phase 0 交付范围：**

- 定义 TracePort。
- 实现最小 Trace 写入骨架。
- 记录 task_id、session_id、capability_id、policy_decision、step_status、error_code、trace_summary。
- 支持 Gateway pre / post 记录。
- 支持 no_capability_found、policy_denied、identity_binding_required、adapter_timeout 等关键终态。
- 写入前必须预留 Redaction / Sanitizer 入口。
- 不记录凭证、token、cookie、session token、原始敏感 payload。
- 不实现完整 Evidence / Raw Payload 分层存储。

**Phase 1 目标范围：**

- 最小可用 Trace / Audit 实现，不在本文件展开。

### 6.5 Identity Binding

**Phase 0 交付范围：**

- 定义 IdentityMappingPort。
- 建立 IdentityMapping Mock 表 / 模型。
- 字段至少包含 ai_user_id、target_system、execution_identity、bind_mode、external_principal_id、resource_scope / account_domain、status。
- 支持 active / unbound / expired / revoked / verification_failed 等最小状态。
- 支持 Gateway 执行前读取绑定状态。
- 未绑定时阻止执行，并返回 SDUI 绑定引导卡。
- 不保存明文密码、access_token、refresh_token、cookie、session token。
- 不实现生产级 OAuth2 / Vault / vendor token 管理。

**Phase 1 目标范围：**

- 最小可用 Identity Binding 实现，不在本文件展开。

### 6.6 SDUI

**Phase 0 交付范围：**

- 定义 Response Envelope 最小协议。
- 支持 `schema_version`、`response_id`、`task_id`、`session_id`、`message`、`fallback_text`、`ui`、`trace_summary`。
- 至少支持一种 confirm card。
- 至少支持一种 operator handback / binding required card。
- 所有 user_action 必须回到 Runtime / Capability Gateway。
- UI schema 不得包含凭证、token、cookie、内部接口地址。
- 支持 CLI / 简单文本降级所需 fallback_text。
- 不实现完整 SDUI Renderer。
- 不实现图表卡、表格卡、文件卡、复杂表单卡。

**Phase 1 目标范围：**

- 最小可用 SDUI 实现，不在本文件展开。

---

## 7. Phase 0 Workstreams

Phase 0 分为五条工作流。

### 7.1 Spike Workstream

目标：验证关键技术风险并形成 ADR。

产出：

- `docs/adr/phase0/ADR-P0-SPIKE-*.md`
- Spike 测试结果。
- Spike 代码处置声明。

### 7.2 Infrastructure Workstream

目标：搭建后续开发可复用的工程基线。

产出：

- Docker Compose 单机部署基线。
- Python uv + FastAPI 骨架。
- React 18 + Vite + Ant Design 5.x 骨架。
- PostgreSQL schema + Alembic 基线。
- Redis + ARQ 连接与最小任务验证。
- OpenTelemetry + Langfuse 观测基线。
- CI lint + type check + test。

### 7.3 Domain Foundation Workstream

目标：先冻结接口契约，再实现最小骨架。

产出：

- `app/ports/*.py` 中的 Python ABC 或 Protocol。
- `app/domain/*` 中的核心领域对象。
- `app/control_plane/*` 中的 Registry / Policy / Trace 最小骨架。
- `app/gateway/*` 中的 Gateway 骨架。
- `app/runtime/*` 中的 Runtime 主链骨架。
- `app/execution_fabric/mock_adapters/*` 中的 Mock Adapter。

### 7.4 Golden Task Workstream

目标：以可重复测试用例验证 Mock 主链路与异常截断能力。

产出：

- `tests/golden_tasks/fixtures/GT-XXX.yaml` 固定结构化测试数据。
- `tests/golden/test_golden_tasks.py`。
- 核心 Golden Task 包含 GT-001 至 GT-010 以及 GT-012；负向 / 边界 / 安全拒绝路径必须 100% 通过。

### 7.5 CI / Review Workstream

目标：防止后续 AI 生成代码破坏模块边界。

产出：

- lint / mypy / pytest 基线。
- import boundary 检查。
- touched_paths / forbidden_paths review 清单。
- Phase 0 验收报告模板。

---

## 8. Phase 0 Task Format Standard

所有交给 Codex / Claude Code 的任务必须使用以下结构。

### 8.1 标准任务字段

以下代码块是字段格式示例，不是可执行任务定义；任何自动抽取任务索引的脚本必须忽略本节示例。

```text
task_id: EXAMPLE_TASK_ID
title: 任务标题
type: spike | infrastructure | interface_contract | implementation | test | documentation | review
priority: prerequisite | P0 | P1 | P2
context: 为什么需要这个任务
objective: 任务要完成什么
deliverable: 交付物
depends_on:
  - EXAMPLE_TASK_ID
constraints:
  - 约束 1
  - 约束 2
acceptance_criteria:
  - 验收标准 1
  - 验收标准 2
touched_paths:
  - 允许修改的路径
forbidden_paths:
  - 禁止修改的路径
schema_contract: 本任务必须遵守的 Pydantic / Protocol 契约
automated_assertions:
  - 可由 pytest / CI 自动判定的断言
notes: 其他说明
```

### 8.2 Spike 任务额外字段

```yaml
spike_code_disposition:
  废弃: 临时脚本 Phase 0 结束前删除
  沉淀为 test_utils: 仅允许进入 tests/utils/
  禁止: 不得进入 app/ 正式模块目录
```

### 8.3 Port 相关任务拆分规则

本节中的 `EXAMPLE_DOMAIN_CONTRACT_TASK/XXb` 仅为命名模式示例，不是可执行任务 ID。

所有 Domain Foundation 任务在实现具体类之前，必须先产出 Python ABC 或 Protocol。

涉及 Port 的任务必须拆分为两步。

步骤一：接口契约定义。

```text
task_id: EXAMPLE_DOMAIN_CONTRACT_TASK
title: "[模块名] 接口契约定义"
type: interface_contract
deliverable: "app/ports/[port_name].py"
constraints:
  - 只定义抽象接口，禁止包含任何具体实现逻辑
  - 所有后续实现类必须继承此 ABC 或满足此 Protocol
  - touched_paths 只包含 app/ports/
touched_paths:
  - app/ports/
forbidden_paths:
  - app/runtime/
  - app/gateway/
  - app/control_plane/
  - app/execution_fabric/
  - app/api/
```

步骤二：骨架实现。

```text
task_id: EXAMPLE_DOMAIN_IMPLEMENTATION_TASK
title: "[模块名] 骨架实现"
type: implementation
depends_on:
  - EXAMPLE_DOMAIN_CONTRACT_TASK
constraints:
  - 必须继承或满足上一步定义的 ABC / Protocol
  - 不得绕过 Port 直接依赖具体实现
```

### 8.4 touched_paths / forbidden_paths 规则

每个任务必须明确：

- `touched_paths`：允许修改的目录。
- `forbidden_paths`：禁止修改的目录。

Codex / Claude Code 不得跨越 `forbidden_paths` 修改代码。若确需修改，必须先生成变更请求，由人工确认后拆新任务。



### 8.4.1 Implementation Task Failure Examples and Step Verification Rule

所有 `type: implementation` 的关键任务，尤其是 Domain Foundation、Gateway、Policy、Trace、Identity、Mock Adapter、Runtime、SDUI、SecretProvider 和 Golden Task 相关任务，必须在任务定义中显式包含以下字段；Codex / Claude Code 不得只根据笼统验收标准自行编造失败路径。

```yaml
happy_path_acceptance:
  - 正向路径必须满足的最小断言
failure_examples:
  - name: 失败样例名称
    trigger: 如何触发该失败
    expected_result: 必须返回的错误码、状态或拒绝行为
    forbidden_shortcut: 禁止用什么方式伪通过
step_verification_points:
  - step: 实施步骤名称
    verification: 该步骤完成后必须运行的测试、命令或静态检查
final_test_commands:
  - 最终必须执行的测试命令
forbidden_shortcuts:
  - 禁止硬编码成功响应
  - 禁止为了通过当前任务修改 forbidden_paths
  - 禁止绕过第 8.6 节核心 Schema / Port 契约
```

若某个 `implementation` / `test` 类型任务缺少 `failure_examples` 或 `step_verification_points`，执行前必须先输出 `task_definition_incomplete`，由人工补齐或确认豁免；不得由 Codex / Claude Code 自行发明生产逻辑来填补缺口。

`interface_contract` 类型任务不强制使用 `failure_examples`，但必须提供 `contract_violation_examples` 与 `step_verification_points`，或显式继承本节的全局契约违规样例。接口契约任务的验证重点是：方法签名、返回模型、抽象边界、禁止具体实现逻辑、禁止跨层 import。若接口契约任务同时缺少 `contract_violation_examples` 和可验证的 `acceptance_criteria`，也必须停止并输出 `task_definition_incomplete`。

```yaml
contract_violation_examples:
  - name: missing_required_method
    trigger: Port 未定义 spec 要求的方法签名
    expected_result: type check 或 contract test 失败
  - name: wrong_return_model
    trigger: 方法返回裸 dict 或自定义非契约模型
    expected_result: contract test 失败
  - name: concrete_logic_in_port
    trigger: app/ports/ 下出现数据库访问、HTTP 调用、Adapter 调用或业务执行逻辑
    expected_result: architecture check 失败
step_verification_points:
  - step: contract_signature_check
    verification: uv run mypy app/ports tests/contracts
  - step: no_concrete_logic_check
    verification: uv run pytest tests/architecture/test_port_contracts.py
```

Spike / preparation 类型任务不要求生产级 `failure_examples`。它们必须改用 `blocking_examples` 与 ADR 验收项，例如 GPU 不可用、目标系统文档不可访问、内网镜像缺失、版本不符合、样本数不足、无法复现实验等。此类任务不得因为缺少 implementation 失败样例而直接停止，但必须在 Plan 中列出阻塞样例、验证证据和 ADR 结论标准。

`infrastructure` 类型任务不要求 implementation 级 `failure_examples`。它们必须使用 `blocking_examples`、`infrastructure_verification_points`，或从 `acceptance_criteria` 逐条生成可执行验证步骤。若 infrastructure 任务未显式提供 `step_verification_points`，Codex / Claude Code 不得直接输出 `task_definition_incomplete`；必须先在 Plan 中把每条验收标准转化为命令、文件检查或 evidence 检查。只有当 `acceptance_criteria` 本身不可测试、缺少必要环境说明或会要求提前实现后续模块时，才停止并输出 `task_definition_incomplete` 或 `task_prompt_incomplete`。

```yaml
blocking_examples:
  - name: dependency_mirror_unavailable
    trigger: 内网镜像或离线缓存无法提供任务声明依赖
    expected_result: 停止并记录 blocked，不得切换到公网依赖
  - name: command_not_runnable
    trigger: 任务要求的本地验证命令无法运行
    expected_result: 记录失败命令、原因和所需人工补充
infrastructure_verification_points:
  - step: config_files_exist
    verification: 检查任务 deliverable 中声明的配置文件存在
  - step: local_commands
    verification: 执行 acceptance_criteria 中声明的本地命令
  - step: deferred_checks
    verification: 若检查项尚未激活，Task Record 必须含完整 not_applicable 字段
```

失败样例的目标不是扩展功能，而是把“支持 / 验证 / 可走通”转换为可自动化判定的断言。正向路径、负向路径、中间步骤检查和最终测试命令必须能在 pytest、CI、静态扫描或 ADR 证据中被验证。

### 8.5 示例任务格式

以下示例仅说明任务字段格式，不是可执行任务；不得把示例 task_id 放入 TASK_INDEX 或执行队列。

```text
task_id: NON_EXECUTABLE_EXAMPLE_TASK
title: Non-executable Task Format Example
type: implementation
priority: P0
context: 示例上下文，仅用于说明字段结构。
objective: 示例目标，不对应任何真实 Phase 0 任务。
deliverable:
  - docs/example-only/
depends_on: []
constraints:
  - 这是非执行示例
acceptance_criteria:
  - 必须能说明任务完成的可测断言
happy_path_acceptance:
  - 正向路径必须满足的最小断言
failure_examples:
  - name: example_failure
    trigger: 示例失败触发条件
    expected_result: 示例失败结果
    forbidden_shortcut: 示例禁止伪通过方式
step_verification_points:
  - step: example_step
    verification: echo example
final_test_commands:
  - echo example
forbidden_shortcuts:
  - 禁止把本示例当作真实任务执行
```


### 8.6 Phase 0 核心契约 Schema 与函数签名

本节是 Phase 0 各模块的共同输入输出契约。所有 Port / Mock Adapter / Gateway / Runtime / Golden Task 必须引用这些结构，禁止各任务自行发明返回字段。

#### 8.6.1 基础上下文模型

```python
from typing import Any, Literal, Optional, Protocol
from pydantic import BaseModel, Field

class RequestOrgContext(BaseModel):
    request_id: str
    tenant_id: str = "default"
    org_id: Optional[str] = None
    department_id: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    channel: Literal["web", "cli", "api", "mock"] = "web"
    locale: str = "zh-CN"
    account_set_id: Optional[str] = None
    device_domain_id: Optional[str] = None
    resource_scope: Optional[str] = None
```

#### 8.6.2 Capability 与 Task 最小模型

```python
class CapabilitySpec(BaseModel):
    capability_id: str
    name: str
    type: Literal["query", "action", "workflow", "mock"]
    intent_tags: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    input_schema_digest: str
    output_schema_digest: str
    risk_level: Literal["low", "medium", "high"]
    owner: str
    version: str
    status: Literal["draft", "active", "disabled", "deprecated"]
    short_description: str
    target_system: Optional[Literal["oa", "u8", "hikvision_ivms"]] = None
    execution_identity: Literal["user_delegated", "system_scope", "admin_approved_proxy"]
    binding_required: bool
    policy_digest: Optional[str] = None

class TaskRecord(BaseModel):
    task_id: str
    session_id: str
    ai_user_id: str
    status: Literal[
        "created",
        "running",
        "waiting_user",
        "completed",
        "failed",
        "no_capability_found",
    ]
    trace_id: Optional[str] = None
    capability_id: Optional[str] = None
    error_code: Optional[str] = None
```

#### 8.6.3 执行结果模型

```python
ErrorCode = Literal[
    "identity_unbound",
    "identity_expired",
    "identity_revoked",
    "needs_binding_scope",
    "policy_denied",
    "confirm_required",
    "adapter_timeout",
    "capability_not_found",
    "adapter_error",
    "adapter_payload_invalid",
    "adapter_missing_required_field",
    "adapter_empty_response",
    "adapter_http_500",
    "upstream_permission_denied",
    "internal_error",
]

class ExecutionResult(BaseModel):
    status: Literal[
        "completed",
        "failed",
        "denied",
        "binding_required",
        "timeout",
        "no_capability_found",
        "waiting_user",
    ]
    data: Optional[dict[str, Any]] = None
    error_code: Optional[ErrorCode] = None
    trace_id: str
```

说明：`status` 必须至少包含 `completed / failed / denied / binding_required / timeout`，并为 Phase 0 主链增加 `no_capability_found / waiting_user` 两个终态。任何新增状态必须同时修改契约、测试和 Golden Task fixture。

#### 8.6.4 SDUI ResponseEnvelope 模型

```python
class UIComponent(BaseModel):
    component_type: Literal[
        "none",
        "confirm_card",
        "operator_handback_card",
        "binding_required_card",
    ]
    action: Optional[Literal[
        "confirm",
        "bind_required",
        "clarify_scope",
        "none",
    ]] = None
    target_system: Optional[Literal["oa", "u8", "hikvision_ivms"]] = None
    reason_code: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)

class ResponseEnvelope(BaseModel):
    schema_version: str = "phase0.sdui.v1"
    response_id: str
    task_id: str
    session_id: str
    status: Literal[
        "completed",
        "blocked",
        "waiting_user",
        "failed",
        "no_capability_found",
    ]
    message: str
    fallback_text: str
    ui: UIComponent
    data: Optional[dict[str, Any]] = None
    trace_id: str
    trace_summary: Optional[str] = None
```

Blocked / waiting_user 响应中 `data` 默认必须为 `None`，除非专项任务明确允许返回脱敏业务摘要。

#### 8.6.4.1 状态映射表（ExecutionResult → TaskRecord → ResponseEnvelope）

Phase 0 必须使用下表完成 Gateway / Runtime / SDUI 的状态转换，禁止各模块自行发明状态映射。

| ExecutionResult.status | ExecutionResult.error_code | TaskRecord.status | ResponseEnvelope.status | 说明 |
|---|---|---|---|---|
| `completed` | `None` | `completed` | `completed` | 正常完成 |
| `denied` | `policy_denied` / `upstream_permission_denied` | `failed` | `blocked` | 策略或上游权限拒绝 |
| `binding_required` | `identity_unbound` / `identity_expired` / `identity_revoked` / `needs_binding_scope` | `failed` | `blocked` | 身份绑定缺失、失效、被撤销或范围不明确 |
| `timeout` | `adapter_timeout` | `failed` | `failed` | 上游或 Mock Adapter 超时 |
| `no_capability_found` | `capability_not_found` | `no_capability_found` | `no_capability_found` | 无可用 Capability |
| `waiting_user` | `confirm_required` | `waiting_user` | `waiting_user` | 一次性确认卡等待用户确认 |
| `failed` | `adapter_error` / `adapter_payload_invalid` / `adapter_missing_required_field` / `adapter_empty_response` / `adapter_http_500` / `internal_error` | `failed` | `failed` | 适配器或内部错误 |

约束：

- `PolicyGuard`、`IdentityMapping`、`CapabilityRegistry` 等单模块任务只返回本模块契约对象，不负责跨模块状态转换。
- 跨模块转换只能在 `Capability Gateway` / `Runtime` / `ResponseEnvelope` 组合链路中完成，并由 Golden Task 断言。
- 任意新增状态必须同时修改本表、Pydantic 模型、Gateway / Runtime 测试和 Golden Task fixture。

#### 8.6.5 AdapterResult 模型

```python
class AdapterResult(BaseModel):
    status: Literal["success", "error", "timeout", "permission_denied"]
    data: Optional[dict[str, Any]] = None
    error_code: Optional[ErrorCode] = None
    raw_payload_ref: Optional[str] = None
```

Mock Adapter 可以在内部模拟畸形 JSON / 空响应 / HTTP 500，但传给 Gateway 的结果必须仍符合 `AdapterResult`。

#### 8.6.6 PolicyDecision 与 IdentityCheckResult

```python
class PolicyDecision(BaseModel):
    decision: Literal["allow", "deny", "confirm"]
    reason_code: Optional[str] = None
    required_action: Optional[Literal["confirm", "none"]] = None

class IdentityCheckResult(BaseModel):
    bind_status: Literal[
        "active",
        "unbound",
        "expired",
        "revoked",
        "verification_failed",
        "needs_binding_scope",
    ]
    binding_id: Optional[str] = None
    target_system: Literal["oa", "u8", "hikvision_ivms"]
    execution_identity: Literal["user_delegated", "system_scope", "admin_approved_proxy"]
    binding_scope: Optional[str] = None
    account_set_id: Optional[str] = None
    device_domain_id: Optional[str] = None
    reason_code: Optional[str] = None
```

#### 8.6.7 TraceEvent 最小模型

```python
class TraceEvent(BaseModel):
    trace_id: str
    task_id: str
    session_id: str
    event_type: Literal[
        "task_created",
        "intent_parsed",
        "capability_selected",
        "no_capability_found",
        "identity_check",
        "blocked_by_identity",
        "policy_checked",
        "blocked_by_policy",
        "confirm_required",
        "gateway_pre_recorded",
        "adapter_called",
        "adapter_error",
        "adapter_result_invalid",
        "gateway_post_recorded",
        "response_envelope_created",
        "task_completed",
        "task_failed",
    ]
    status: Literal["ok", "blocked", "failed", "skipped"]
    capability_id: Optional[str] = None
    error_code: Optional[ErrorCode] = None
    attributes: dict[str, Any] = Field(default_factory=dict)
```

Trace 写入前必须调用 sanitizer；若 `attributes`、`trace_summary`、`message` 等文本命中敏感模式，必须拒绝写入。

#### 8.6.8 必须实现的 Port 方法签名

```python
class TaskStorePort(Protocol):
    async def create_task(self, record: TaskRecord) -> TaskRecord: ...
    async def get_task(self, task_id: str) -> TaskRecord | None: ...
    async def update_status(
        self,
        task_id: str,
        status: Literal["created", "running", "waiting_user", "completed", "failed", "no_capability_found"],
        error_code: Optional[str] = None,
    ) -> TaskRecord: ...

class CapabilityRegistryPort(Protocol):
    async def create(self, capability: CapabilitySpec) -> CapabilitySpec: ...
    async def get(self, capability_id: str) -> CapabilitySpec | None: ...
    async def list(
        self,
        target_system: Optional[str] = None,
        type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[CapabilitySpec]: ...
    async def update(self, capability_id: str, patch: dict[str, Any]) -> CapabilitySpec: ...
    async def disable(self, capability_id: str) -> CapabilitySpec: ...

class CapabilityGatewayPort(Protocol):
    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult: ...

class AdapterPort(Protocol):
    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult: ...

class PolicyGuardPort(Protocol):
    async def decide(
        self,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> PolicyDecision: ...

class IdentityMappingPort(Protocol):
    async def resolve_execution_identity(
        self,
        ai_user_id: str,
        target_system: Literal["oa", "u8", "hikvision_ivms"],
        execution_identity: Literal["user_delegated", "system_scope", "admin_approved_proxy"],
        request_context: RequestOrgContext,
    ) -> IdentityCheckResult: ...

class TracePort(Protocol):
    async def record_event(self, event: TraceEvent) -> None: ...

class RuntimePort(Protocol):
    async def handle_user_message(
        self,
        channel: Literal["web", "cli", "api", "mock"],
        ai_user_id: str,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
    ) -> ResponseEnvelope: ...

class SecretProviderPort(Protocol):
    async def resolve_secret_ref(
        self,
        credential_ref: str,
        task_id: str,
        capability_id: str,
    ) -> dict[str, Any]: ...

    async def inject_execution_secret(
        self,
        execution_context: dict[str, Any],
        credential_ref: str,
    ) -> dict[str, Any]: ...
```

`SecretProviderPort` 的 Phase 0 Mock / Noop 实现不得返回明文真实凭证，只能返回脱敏占位、`credential_ref` 或 `mock_secret_injected=true` 这类测试信号。

---

## 9. Spike Task List

### P0-SPIKE-001 — Qwen 本地模型结构化输出验证

```yaml
task_id: P0-SPIKE-001
title: Qwen Local Model Structured Output Spike
type: spike
priority: prerequisite
context: 主蓝图要求 Phase 0 验证候选内网模型在实际 GPU、量化方式、vLLM / SGLang 配置下的可用上下文长度和结构化输出能力，不能直接假设理论指标可用于生产。
objective: 验证 Qwen 系列模型在目标 GPU 环境下的量化方案、可用 max_model_len、结构化输出成功率和推理成本。
deliverable:
  - docs/adr/phase0/ADR-P0-SPIKE-001-qwen-structured-output.md
constraints:
  - 不产生生产 Runtime 代码
  - 不修改主蓝图
  - 不引入新的模型服务架构
  - Spike 代码不得进入 app/ 任何正式模块
acceptance_criteria:
  - 记录模型版本、量化方案、GPU、显存、推理服务、max_model_len
  - 至少覆盖 Intent、CapabilityRef、PlanDraft、ResponseEnvelope 四类结构化输出样例
  - 统计结构化输出成功率，并明确是否达到 >= 80%
  - 给出 Phase 1 是否可用 OpenAI-compatible 本地模型网关的建议
spike_code_disposition: 废弃或沉淀为 tests/utils/
touched_paths:
  - docs/adr/phase0/
  - experiments/phase0/qwen_structured_output/
  - tests/utils/
forbidden_paths:
  - app/
```

### P0-SPIKE-002 — instructor + vLLM 工具调用稳定性验证

```yaml
task_id: P0-SPIKE-002
title: instructor + vLLM OpenAI-compatible API Stability Spike
type: spike
priority: prerequisite
context: Phase 1 默认结构化输出路线为 OpenAI SDK + instructor + Pydantic v2 Schema，需要验证其与 vLLM OpenAI-compatible API 的稳定性。
objective: 验证 instructor 在 vLLM / OpenAI-compatible API 下的结构化输出、重试、异常解析和工具调用响应解析稳定性。
deliverable:
  - docs/adr/phase0/ADR-P0-SPIKE-002-instructor-vllm-stability.md
constraints:
  - 不产生生产 Runtime 代码
  - 不直接定义正式 Tool 调用协议
  - Spike 代码不得进入 app/
acceptance_criteria:
  - 覆盖成功、JSON 缺字段、字段类型错误、模型拒答、超时、非 JSON 输出等场景
  - 记录重试成功率、失败类型和建议参数
  - 明确 Phase 1 是否采用 instructor 作为默认结构化输出实现
spike_code_disposition: 废弃或沉淀为 tests/utils/
touched_paths:
  - docs/adr/phase0/
  - experiments/phase0/instructor_vllm/
  - tests/utils/
forbidden_paths:
  - app/
```

### P0-SPIKE-003 — PostgreSQL 18 + pgvector >= 0.8.2 部署验证

```yaml
task_id: P0-SPIKE-003
title: PostgreSQL 18 + pgvector >= 0.8.2 Deployment Spike
type: spike
priority: prerequisite
context: 主蓝图将 PostgreSQL + pgvector >= 0.8.2 作为 Phase 0 / Phase 1 基线，但具体部署参数和兼容性必须由 Phase 0 验证。
objective: 验证 PostgreSQL 18 与 pgvector >= 0.8.2 在 Docker Compose 单机环境下的部署、迁移、索引和最小向量查询能力。
deliverable:
  - docs/adr/phase0/ADR-P0-SPIKE-003-postgresql-pgvector.md
constraints:
  - 只验证部署与最小查询
  - 不实现正式 Memory / RAG 功能
acceptance_criteria:
  - docker compose 可启动 PostgreSQL 18
  - pgvector 版本 >= 0.8.2
  - Alembic 可创建带 vector 字段的测试表
  - 最小相似度查询通过
  - ADR 明确是否采用该组合进入 Phase 1
spike_code_disposition: 废弃或沉淀为 tests/utils/
touched_paths:
  - docs/adr/phase0/
  - experiments/phase0/postgres_pgvector/
  - infra/docker/
  - tests/utils/
forbidden_paths:
  - app/domain/
  - app/runtime/
  - app/gateway/
```

### P0-SPIKE-004 — Redis + ARQ 基础可用性验证

```yaml
task_id: P0-SPIKE-004
title: Redis + ARQ Baseline Spike
type: spike
priority: P0
context: 主蓝图要求异步任务通过 JobQueuePort 隔离，Redis + ARQ 作为 L1 候选实现，不作为不可替换底座。
objective: 验证 Redis + ARQ 在单机 Docker Compose 环境下的任务入队、执行、失败重试、超时和结果记录能力。
deliverable:
  - docs/adr/phase0/ADR-P0-SPIKE-004-redis-arq.md
constraints:
  - 不把 ARQ 写死为长期底座
  - 不实现正式业务异步任务
  - Spike 代码不得进入 app/ 正式模块
acceptance_criteria:
  - 最小任务可入队并执行
  - 失败任务可记录异常
  - 超时任务有明确错误
  - ADR 明确 Phase 1 是否启用 ARQ 作为 L1 候选
spike_code_disposition: 废弃或沉淀为 tests/utils/
touched_paths:
  - docs/adr/phase0/
  - experiments/phase0/redis_arq/
  - infra/docker/
  - tests/utils/
forbidden_paths:
  - app/runtime/
  - app/gateway/
```

### P0-SPIKE-005 — OA / U8 / 海康 iVMS API 与认证探查

```yaml
task_id: P0-SPIKE-005
title: Target Business Systems API and Authentication Reconnaissance
type: spike
priority: prerequisite
context: Phase 1 设计必须基于三个目标系统真实 API 类型和认证机制，不能凭假设编写接入 spec。
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

### P0-SPIKE-006 — S3-compatible 对象存储候选验证

```yaml
task_id: P0-SPIKE-006
title: S3-compatible Object Storage Candidate Spike
type: spike
priority: P1
context: 主蓝图只要求 S3-compatible Object Storage 抽象，不锁死具体产品；Phase 0 需要确认单机内网候选与替换边界。
objective: 验证 MinIO / NAS S3 Gateway / 企业对象存储候选的最小上传、下载、权限配置和 Docker Compose 部署可行性。
deliverable:
  - docs/adr/phase0/ADR-P0-SPIKE-006-s3-compatible-storage.md
constraints:
  - 不实现正式文件管理模块
  - 不锁死具体供应商
acceptance_criteria:
  - 至少验证一个 S3-compatible 候选
  - 完成最小 put/get/delete 测试
  - 记录许可证、内网部署、备份、权限、运维风险
  - 给出 Phase 1 是否需要启用对象存储的建议
spike_code_disposition: 废弃或沉淀为 tests/utils/
touched_paths:
  - docs/adr/phase0/
  - experiments/phase0/s3_storage/
  - tests/utils/
forbidden_paths:
  - app/
```

### P0-SPIKE-007 — PydanticAI + Qwen/vLLM 兼容性验证

```yaml
task_id: P0-SPIKE-007
title: PydanticAI + Qwen/vLLM Compatibility Spike
type: spike
priority: P1
recommended_after:
  - P0-SPIKE-001
  - P0-SPIKE-002
context: 冻结蓝图明确 PydanticAI 是 Phase 0 验证项，不是 Phase 1 强依赖；验证结果只决定后续阶段是否作为 LLM Orchestration Adapter 候选。
objective: 验证 PydanticAI 与 vLLM OpenAI-compatible API 的兼容性，包括结构化输出格式、工具调用响应解析、异常处理、重试、可观测性和 Qwen 模型适配。
deliverable:
  - docs/adr/phase0/ADR-P0-SPIKE-007-pydanticai-qwen-vllm.md
constraints:
  - 不产生生产 Runtime 代码
  - 不将 PydanticAI 作为 Phase 1 强依赖
  - Spike 代码不得进入 app/ 任何正式模块
acceptance_criteria:
  - 记录兼容性结论：通过 / 失败 / 部分可用
  - 覆盖结构化输出、工具调用响应解析、异常处理、重试、观测埋点
  - 给出是否在 Phase 2 引入 PydanticAI 的明确建议
  - 若条件通过，必须写明限制条件和不适用场景
spike_code_disposition: 废弃或沉淀为 tests/utils/
touched_paths:
  - docs/adr/phase0/
  - experiments/phase0/pydanticai_qwen_vllm/
  - tests/utils/
forbidden_paths:
  - app/
```

---

## 10. Infrastructure Task List

### P0-INFRA-001 — Docker Compose 单机部署基线

```yaml
task_id: P0-INFRA-001
title: Docker Compose Single-node Baseline
type: infrastructure
priority: prerequisite
objective: 建立 Phase 0 单机 Compose 占位基线，仅验证 core-infra 配置可解析；API、前端、Langfuse/OTel、Redis/ARQ 的健康检查分别后移到对应任务。
deliverable:
  - docker-compose.yml
  - infra/docker/
  - .env.example
constraints:
  - 不接真实生产系统
  - 不写入真实凭证
  - 所有密钥使用占位符
acceptance_criteria:
  - docker compose config 通过
  - core-infra profile 可启动 PostgreSQL 等基础占位服务
  - app / observability profile 可以存在占位，但不得要求 API、前端、Langfuse、Gateway 或 Runtime 已健康
  - .env.example 完整列出必需变量
  - README 提供分 profile 启动说明
touched_paths:
  - docker-compose.yml
  - infra/docker/
  - .env.example
  - README.md
forbidden_paths:
  - app/execution_fabric/real_adapters/
```

### P0-INFRA-002 — Python uv + FastAPI 后端骨架

```yaml
task_id: P0-INFRA-002
title: Python uv + FastAPI Backend Skeleton
type: infrastructure
priority: prerequisite
objective: 建立 Python 3.12、uv、FastAPI、Pydantic v2、SQLAlchemy 2.0、Alembic、pytest、Ruff、mypy 后端基线。
deliverable:
  - pyproject.toml
  - uv.lock
  - app/main.py
  - app/api/v1/health.py
  - tests/test_health.py
constraints:
  - 只做骨架与健康检查
  - 不实现业务逻辑
  - 使用企业内网 PyPI 镜像或离线缓存；禁止 AI 自行添加未缓存依赖
  - Python 依赖必须在 pyproject.toml 已知范围内选择；新增依赖必须记录原因、版本范围和内网镜像可用性
  - uv.lock 变更必须在任务日志中说明
acceptance_criteria:
  - uv sync 在内网镜像或离线缓存条件下成功
  - uv run pytest 成功
  - uv run ruff check 成功
  - uv run mypy app 成功
touched_paths:
  - pyproject.toml
  - uv.lock
  - app/
  - tests/
forbidden_paths:
  - app/execution_fabric/real_adapters/
```

### P0-INFRA-003 — React 18 + Vite + Ant Design 5.x 前端骨架

```yaml
task_id: P0-INFRA-003
title: React 18 + Vite + Ant Design Frontend Skeleton
type: infrastructure
priority: P0
objective: 建立 React 18、TypeScript strict mode、Vite、Ant Design 5.x、React Router、TanStack Query、Zustand、pnpm 前端骨架；明确 ProComponents 2.x / MSW / Orval 是否进入内网依赖基线。
deliverable:
  - web/
constraints:
  - 不实现完整 Admin Console
  - 不实现完整 SDUI Renderer
  - 只提供健康页、基础布局和 Mock API 调用样例
  - 使用企业内网 npm 镜像或离线缓存；禁止 AI 自行添加未缓存依赖
  - 前端连接后端 OpenAPI 的 API client 必须通过 Orval 或等价 OpenAPI codegen 生成
  - "`web/package.json` 必须包含 `generate:api` 脚本，用于从 FastAPI OpenAPI JSON 生成 API client"
  - 禁止在业务页面中手写 fetch / axios 调用后端业务 API；健康页静态 mock 与纯 fixture 展示除外
  - Phase 0 前端骨架不强制实现完整 Admin Console，但必须记录 ProComponents 2.x / MSW 是否已在内网 npm 镜像缓存；若未缓存，只记录依赖缺口，不得私自替代技术栈
  - pnpm-lock.yaml 变更必须在任务日志中说明
acceptance_criteria:
  - pnpm install 在内网 npm 镜像或离线缓存条件下成功
  - pnpm lint 成功
  - pnpm build 成功
  - 前端可访问健康页
  - "`pnpm generate:api` 可执行，生成产物与后端 OpenAPI JSON 不漂移；若后端 OpenAPI 尚未稳定，必须提供 mock OpenAPI fixture"
touched_paths:
  - web/
forbidden_paths:
  - app/
```

### P0-INFRA-004 — PostgreSQL schema + Alembic 迁移基线

```yaml
task_id: P0-INFRA-004
title: PostgreSQL Schema and Alembic Baseline
type: infrastructure
priority: prerequisite
objective: 建立数据库连接、SQLAlchemy async session、Alembic 迁移骨架。
deliverable:
  - app/db/
  - alembic/
constraints:
  - 只做迁移基线和连接健康检查
  - 不提前实现完整领域模型
  - Phase 0 不允许使用 Alembic autogenerate；所有迁移必须手写
  - 迁移文件命名格式必须为 `YYYYMMDD_HHMMSS_<short_description>.py`
  - 多个开发任务并发生成迁移时，必须保证时间戳有序，不得手动改已提交迁移
  - Golden Task 与数据库集成测试必须使用隔离策略：优先 pytest-asyncio function scope + transaction rollback；若异步事务不可行，则使用独立 `test_<uuid>` schema 并在测试结束后 drop
acceptance_criteria:
  - alembic upgrade head 成功
  - alembic downgrade -1 可回滚
  - 每个迁移必须通过 `alembic upgrade head` 与 `alembic downgrade -1` 循环测试
  - CI 或测试脚本可检测 `alembic/versions/` 中不得出现 `auto generated by Alembic` 注释
  - API 可执行数据库健康检查
  - 测试数据库隔离策略已在 `tests/db/` 或 `tests/golden_tasks/conftest.py` 中落地说明
touched_paths:
  - app/db/
  - alembic/
  - alembic.ini
  - tests/db/
forbidden_paths:
  - app/runtime/
  - app/gateway/
```

### P0-INFRA-005 — Redis + ARQ 基线

```yaml
task_id: P0-INFRA-005
title: Redis + ARQ Baseline via JobQueuePort Candidate
type: infrastructure
priority: P1
depends_on:
  - P0-SPIKE-004
objective: 在通过 Spike 后建立 Redis 连接和 ARQ 最小 worker 基线，并通过 JobQueuePort 隔离。
deliverable:
  - app/ports/job_queue.py
  - app/infra/job_queue/
constraints:
  - 仅当 P0-SPIKE-004 ADR 结论为 passed 或 partially_passed / conditionally_passed 时执行
  - ARQ 只能作为候选实现
  - Runtime 不得直接依赖 ARQ
  - 业务 Workflow 状态不得存入队列
acceptance_criteria:
  - JobQueuePort 定义完成
  - ARQ 实现满足 JobQueuePort
  - 最小任务入队、执行、失败测试通过
touched_paths:
  - app/ports/job_queue.py
  - app/infra/job_queue/
  - tests/infra/job_queue/
forbidden_paths:
  - app/runtime/
  - app/gateway/
```

### P0-INFRA-006 — OpenTelemetry + Langfuse 基线部署

```yaml
task_id: P0-INFRA-006
title: OpenTelemetry + Langfuse Baseline Deployment
type: infrastructure
priority: prerequisite
context: Golden Task 验证开始前必须完成，否则只能看到结果对错，无法分析 LLM 调用链、Runtime 路由、Gateway、Policy、Adapter 是否按预期执行。
objective: 建立 OpenTelemetry + Langfuse 自托管观测基线，为 Spike 和 Golden Task 提供最小调用链可见性。
deliverable:
  - infra/observability/
  - app/observability/
constraints:
  - 写入 Trace / Log 前必须预留 Redaction / Sanitizer
  - 不得记录凭证、token、cookie、session token
  - Golden Task 开始前必须完成
acceptance_criteria:
  - API health request 产生 trace_id
  - LLM Spike 可记录一次 Langfuse 调用样例
  - trace_id 可在日志或 Langfuse / OpenTelemetry 视图中检索
  - 文档说明如何查看链路
  - Gateway Mock 调用关联 trace_id 的验收后移到 P0-DOMAIN-003b2 或 Golden Task，不得在本任务中提前实现 Gateway
touched_paths:
  - infra/observability/
  - app/observability/
  - docker-compose.yml
  - README.md
  - tests/observability/
forbidden_paths:
  - app/execution_fabric/real_adapters/
```

### P0-INFRA-007 — CI lint + type check + staged test baseline

```yaml
task_id: P0-INFRA-007
title: CI Lint Type Check Staged Import Boundary and Test Baseline
type: infrastructure
priority: prerequisite
objective: 建立后端 Ruff、mypy、pytest、自动化 import boundary 检查，前端 lint、typecheck、build 的分阶段 CI 基线；早期未实现的 Runtime / Gateway / Trace / Golden Task 检查必须标记为 not_applicable，不得为了通过 CI 提前实现后续模块。
deliverable:
  - .github/workflows/ci.yml 或等价 CI 配置
  - tests/architecture/test_import_boundaries.py
  - scripts/check_dependencies.py 或等价依赖白名单检查脚本
constraints:
  - CI 必须能在无真实业务系统凭证下运行
  - Mock 环境必须可测试
  - CI 必须包含 import boundary 自动化检查，不能只依赖自然语言声明或 optional human review
  - CI 必须检查是否新增未授权依赖或 lockfile 异常变更
  - 当前阶段尚未存在的模块检查必须记录为带完整解释字段的 not_applicable，不得为了通过 CI 提前实现 Runtime / Gateway / Trace / Golden Task
  - Golden Task runner 相关 CI 检查在 P0-GT-002 完成前必须记录为带完整解释字段的 not_applicable，并在 P0-GT-002 完成后通过 task patch / ADR patch 激活；不得在 P0-INFRA-007 阶段提前实现 Golden Task runner
acceptance_criteria:
  - 后端 lint / mypy / pytest 空基线可运行
  - 前端 lint / typecheck / build 空基线可运行
  - "`tests/architecture/test_import_boundaries.py` 存在，并支持对尚未创建目录返回 not_applicable"
  - 依赖白名单 / lockfile 检查脚本存在并可运行
  - CI 检查 `alembic/versions/` 中不存在 `auto generated by Alembic` 注释
  - CI 检查测试文件中不存在 `assert True`、空 `pass`、无断言 test function 或把关键断言改成恒真表达式
  - Trace sanitizer 检查在 TracePort 未实现前标记为带完整解释字段的 not_applicable；TracePort 实现后必须强制执行
  - Orval / OpenAPI codegen 检查在前端连接后端业务 API 前标记为带完整解释字段的 not_applicable；连接后必须强制执行
  - Golden Task runner 相关 CI 检查在 P0-GT-002 前标记为带完整解释字段的 not_applicable
  - CI 文档说明本地等价命令与 not_applicable 规则
  - 不得为了通过本任务验收提前实现 Runtime、Gateway、Trace、Golden Task 或业务 Adapter
touched_paths:
  - .github/workflows/
  - README.md
  - pyproject.toml
  - web/package.json
  - tests/architecture/
  - scripts/
forbidden_paths:
  - app/runtime/
  - app/gateway/
  - app/execution_fabric/real_adapters/
```

### P0-INFRA-008 — 内网依赖镜像与依赖白名单基线

```yaml
task_id: P0-INFRA-008
title: Internal Dependency Mirror and Dependency Allowlist Baseline
type: infrastructure
priority: prerequisite
objective: 为 Phase 0 建立可在企业内网执行的 Python / Frontend 依赖安装规则，防止 AI 自行添加外部依赖导致执行卡死或技术栈漂移。
deliverable:
  - docs/dev/dependency_policy.md
  - .env.example 中的 PIP_INDEX_URL / UV_INDEX_URL / npm registry 占位说明
  - scripts/check_dependencies.py 或等价检查脚本
constraints:
  - 不要求本文件记录真实内网镜像地址
  - 禁止写入真实账号、token、私有镜像凭证
  - 所有新增依赖必须记录是否已进入内网镜像缓存
  - Spike 依赖不得进入生产依赖组
acceptance_criteria:
  - 文档说明 Python 使用 uv 与内网 PyPI 镜像的方式
  - 文档说明前端使用 pnpm 与内网 npm 镜像的方式
  - 依赖白名单检查脚本可在 CI 中运行
  - 当检测到未声明依赖或异常 lockfile 变更时，CI 失败
touched_paths:
  - docs/dev/dependency_policy.md
  - .env.example
  - scripts/
  - .github/workflows/
forbidden_paths:
  - app/
  - web/src/
  - app/execution_fabric/real_adapters/
```

## 11. Domain Foundation Task List

### 11.1 领域任务总规则

所有 Domain Foundation 任务必须遵守：

1. 先定义 Port / ABC / Protocol，再做骨架实现。
2. Runtime 只能依赖 CapabilityGatewayPort，不得依赖 Adapter。
3. Gateway 可以依赖 PolicyGuardPort、TracePort、IdentityMappingPort、SecretProviderPort 和 Adapter Registry，但真实凭证注入只能通过 SecretProviderPort。
4. Mock Adapter 只能位于 `app/execution_fabric/mock_adapters/`。
5. 真实 Adapter 目录在 Phase 0 不实现，最多保留空目录和 README。
6. 所有实现必须有测试。
7. 涉及用户可见响应的任务必须返回 ResponseEnvelope。
8. 所有失败路径必须写入 Trace。
9. 所有 Mock Adapter 必须支持可配置错误注入，且错误注入不得破坏 AdapterPort 对 Gateway 的标准返回契约。
10. IdentityMapping Mock 必须覆盖同一用户多账套 / 多设备域场景，不能只验证 1:1 绑定。
11. 所有 Port / Adapter / Gateway / Runtime / ResponseEnvelope 必须引用第 8.6 节公共契约，不得自行发明输入输出 Schema。
12. 所有“验证”“支持”类验收必须写成 pytest 可断言条件，包括字段、枚举、Trace 事件、Adapter 调用次数和禁止出现的敏感内容。

### P0-DOMAIN-001a — Task / Session 接口契约定义

```yaml
task_id: P0-DOMAIN-001a
title: Task and Session Interface Contract
type: interface_contract
priority: P0
deliverable:
  - app/ports/task_store.py
constraints:
  - 只定义抽象接口
  - 不包含具体数据库实现
acceptance_criteria:
  - 定义第 8.6.8 节中的 TaskStorePort 方法签名
  - 定义 SessionStorePort
  - TaskRecord 必须使用第 8.6.2 节模型
  - 覆盖 create_task、get_task、update_status、append_event、create_session、get_session
touched_paths:
  - app/ports/task_store.py
forbidden_paths:
  - app/runtime/
  - app/gateway/
  - app/control_plane/
  - app/execution_fabric/
```

### P0-DOMAIN-001b — Task / Session 骨架实现

```yaml
task_id: P0-DOMAIN-001b
title: Task and Session Minimal Skeleton
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-001a
objective: 实现最小 Task / Session 模型和 PostgreSQL 存储骨架。
acceptance_criteria:
  - Task 支持 created / running / waiting_user / completed / failed / no_capability_found
  - Session 可关联多个 Task
  - 状态更新写入事件表或最小事件记录
  - 测试覆盖创建、状态更新、查询
happy_path_acceptance:
  - create_task 创建 TaskRecord，初始状态必须为 created
  - create_session 可创建 Session，并允许一个 Session 关联多个 Task
  - update_status 支持 created → running → completed / failed / waiting_user / no_capability_found 的合法转换
  - append_event 能为指定 task_id 追加最小事件记录，并能被查询验证
failure_examples:
  - name: invalid_status_transition
    trigger: 将 completed task 更新回 running
    expected_result: 返回 validation_error 或 task_state_invalid，不得静默成功
    forbidden_shortcut: 禁止在测试中跳过状态机校验
  - name: missing_session_id
    trigger: 创建 TaskRecord 时 session_id 缺失或为空
    expected_result: 返回 validation_error
    forbidden_shortcut: 禁止自动生成隐藏 session_id 规避输入校验
  - name: unknown_task_id_update
    trigger: 更新不存在的 task_id
    expected_result: 返回 task_not_found
    forbidden_shortcut: 禁止自动创建缺失 task 以通过更新测试
  - name: event_append_without_task
    trigger: 对不存在 task_id append_event
    expected_result: 返回 task_not_found
    forbidden_shortcut: 禁止写入孤立事件记录
step_verification_points:
  - step: model_validation
    verification: uv run pytest tests/domain/test_task_session_models.py
  - step: persistence_crud
    verification: uv run pytest tests/domain/test_task_session_store.py
  - step: state_transition_negative
    verification: uv run pytest tests/domain/test_task_session_state_transitions.py
  - step: alembic_cycle
    verification: alembic upgrade head && alembic downgrade -1 && alembic upgrade head
final_test_commands:
  - uv run pytest tests/domain/test_task_session_models.py tests/domain/test_task_session_store.py tests/domain/test_task_session_state_transitions.py
  - alembic upgrade head && alembic downgrade -1 && alembic upgrade head
forbidden_shortcuts:
  - 禁止硬编码 status=completed 响应
  - 禁止在 Task / Session 骨架内调用 Gateway、Runtime 或 Adapter
  - 禁止创建不受 TaskStorePort / SessionStorePort 约束的旁路存储
  - 禁止把状态转换测试改成 assert True、skip 或空 pass
touched_paths:
  - app/domain/task/
  - app/domain/session/
  - app/infra/persistence/
  - alembic/versions/
  - tests/domain/
forbidden_paths:
  - app/gateway/
  - app/runtime/
  - app/execution_fabric/
```

### P0-DOMAIN-002a — Capability 接口契约定义

```yaml
task_id: P0-DOMAIN-002a
title: Capability Interface Contract
type: interface_contract
priority: P0
deliverable:
  - app/ports/capability_registry.py
constraints:
  - 只定义 CapabilityRegistryPort
  - 不包含 CRUD 实现
acceptance_criteria:
  - 定义第 8.6.8 节中的 CapabilityRegistryPort 方法签名
  - CapabilitySpec 必须使用第 8.6.2 节模型
  - 定义按 capability_id 查询
  - 定义按 target_system / type / status 查询
touched_paths:
  - app/ports/capability_registry.py
forbidden_paths:
  - app/control_plane/
  - app/gateway/
  - app/runtime/
```

### P0-DOMAIN-002b — Capability 模型与 Registry 最小 CRUD

```yaml
task_id: P0-DOMAIN-002b
title: Capability Model and Registry Minimal CRUD
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-002a
objective: 实现 Capability 模型、数据库迁移和最小 CRUD API。
constraints:
  - 不实现 Preselector
  - 不实现 Capability Summary Cache
  - 不调用 Adapter
acceptance_criteria:
  - Capability 字段包含 capability_id、name、type、intent_tags、input_schema、output_schema、input_schema_digest、output_schema_digest、risk_level、owner、version、status、short_description、target_system、execution_identity、binding_required、policy_digest
  - POST /capabilities 创建成功
  - GET /capabilities/{id} 查询成功
  - PATCH /capabilities/{id} 更新成功
  - capability_id 唯一性约束测试通过
  - name + version 唯一性策略明确并有测试覆盖
  - 重复注册返回明确错误码，不得静默覆盖
  - Registry 必须持久化 input_schema / output_schema 的完整 JSON Schema，不得只保存 digest
  - input_schema_digest / output_schema_digest 必须由 canonical JSON 计算，计算规则写入测试或 helper
  - 创建或更新 Capability 时，若传入 digest 与完整 schema 不一致，必须返回 validation_error
  - Gateway 后续参数校验必须使用完整 input_schema，不得只使用 digest；本任务只负责存储和校验 schema / digest
  - 禁用 Capability 的状态可持久化、查询和更新；Gateway 拒绝 disabled Capability 的链路验收放入 P0-DOMAIN-003b1 / P0-DOMAIN-003b2 / Golden Task，不在 Registry 任务内验收
  - Alembic 可升级和回滚
happy_path_acceptance:
  - 创建、查询、更新 Capability 均返回 CapabilitySpec 兼容结构
failure_examples:
  - name: duplicate_capability_id
    trigger: 使用已存在 capability_id 再次 POST /capabilities
    expected_result: 返回 capability_already_exists，不得静默覆盖
    forbidden_shortcut: 禁止用随机 capability_id 规避唯一性测试
  - name: invalid_risk_level
    trigger: risk_level 不在 low|medium|high 枚举内，例如 critical
    expected_result: 返回 422 或 validation_error
    forbidden_shortcut: 禁止把未知枚举自动改写为 low
  - name: schema_digest_mismatch
    trigger: input_schema_digest 与 input_schema 的 canonical JSON 摘要不一致
    expected_result: 返回 validation_error，不得保存不一致记录
    forbidden_shortcut: 禁止忽略 digest 或在保存时静默重算覆盖用户输入
  - name: missing_execution_identity_when_binding_required
    trigger: binding_required=true 但 execution_identity 缺失
    expected_result: 返回 validation_error
    forbidden_shortcut: 禁止自动补默认 identity
step_verification_points:
  - step: schema_and_migration
    verification: uv run pytest tests/control_plane/test_capability_schema.py
  - step: crud_api
    verification: uv run pytest tests/control_plane/test_capability_registry_crud.py
  - step: uniqueness_and_validation
    verification: uv run pytest tests/control_plane/test_capability_registry_negative.py
  - step: schema_digest_validation
    verification: uv run pytest tests/control_plane/test_capability_schema_digest.py
final_test_commands:
  - uv run pytest tests/control_plane/
  - uv run pytest tests/control_plane/test_capability_schema_digest.py
forbidden_shortcuts:
  - 禁止把 Registry 测试写成只验证 status=ok
  - 禁止在 Registry 内调用 Gateway 或 Adapter
touched_paths:
  - app/domain/capability/
  - app/control_plane/capability_registry/
  - app/api/v1/capabilities.py
  - alembic/versions/
  - tests/control_plane/
forbidden_paths:
  - app/gateway/
  - app/runtime/
  - app/execution_fabric/
```

### P0-DOMAIN-003a — Capability Gateway 接口契约定义

```yaml
task_id: P0-DOMAIN-003a
title: Capability Gateway Interface Contract
type: interface_contract
priority: P0
deliverable:
  - app/ports/capability_gateway.py
constraints:
  - 只定义抽象接口
  - 不包含具体执行逻辑
acceptance_criteria:
  - 定义第 8.6.8 节中的 `execute_capability` 方法签名
  - "`request_context` 必须使用 RequestOrgContext"
  - 输出必须使用 ExecutionResult，不得使用自定义 dict
touched_paths:
  - app/ports/capability_gateway.py
forbidden_paths:
  - app/gateway/
  - app/runtime/
  - app/execution_fabric/
```

### P0-DOMAIN-003b0 — Capability Gateway 透传版集成骨架

```yaml
task_id: P0-DOMAIN-003b0
title: Capability Gateway Pass-through Integration Skeleton
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-003a
  - P0-DOMAIN-005a
  - P0-DOMAIN-008a
  - P0-DOMAIN-008b
objective: 在完整 Gateway 骨架完成前，先实现只通过 CapabilityGatewayPort 暴露的透传版 Gateway，用于 Runtime 早期集成和 import 边界验证。
constraints:
  - 不绕过 CapabilityGatewayPort
  - 只允许调用 Mock Adapter
  - 不实现完整 Policy / Identity 逻辑
  - 不接真实业务系统
  - 后续必须被 P0-DOMAIN-003b1 / P0-DOMAIN-003b2 替换或扩展
acceptance_criteria:
  - Runtime 可通过 CapabilityGatewayPort 调用透传 Gateway
  - 透传 Gateway 可调用 Mock Adapter 并返回标准 AdapterResult
  - 至少写入 trace_id 或 trace 占位事件
  - import boundary 检查通过
happy_path_acceptance:
  - 已注册 mock capability 通过透传 Gateway 返回 AdapterResult(status="ok")，再映射为 ExecutionResult(status="completed")
failure_examples:
  - name: adapter_not_found
    trigger: capability_id 无对应 Mock Adapter
    expected_result: ExecutionResult(status="failed", error_code="adapter_error")
    forbidden_shortcut: 禁止 Runtime 直接调用 Adapter 绕过 Gateway
  - name: adapter_returns_error
    trigger: Mock Adapter 返回 AdapterResult(status="error")
    expected_result: Gateway 返回 ExecutionResult(status="failed", error_code="adapter_error")
    forbidden_shortcut: 禁止吞掉 Adapter 错误并返回 completed
  - name: trace_placeholder_missing
    trigger: Gateway 执行后没有 trace_id 或占位事件
    expected_result: tests/gateway trace placeholder test 失败
    forbidden_shortcut: 禁止在测试中硬编码 trace_id 绕过实现
step_verification_points:
  - step: port_wiring
    verification: uv run pytest tests/gateway/test_pass_through_gateway.py
  - step: adapter_error_mapping
    verification: uv run pytest tests/gateway/test_pass_through_gateway_errors.py
  - step: import_boundary
    verification: uv run pytest tests/architecture/test_import_boundaries.py
final_test_commands:
  - uv run pytest tests/gateway/ tests/architecture/test_import_boundaries.py
touched_paths:
  - app/gateway/
  - tests/gateway/
forbidden_paths:
  - app/runtime/
  - app/execution_fabric/real_adapters/
```

### P0-DOMAIN-003b1 — Capability Gateway 短路决策骨架

```yaml
task_id: P0-DOMAIN-003b1
title: Capability Gateway Short-circuit Skeleton
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-003a
  - P0-DOMAIN-003b0
  - P0-DOMAIN-002b
  - P0-DOMAIN-004b
  - P0-DOMAIN-005b
  - P0-DOMAIN-006b
  - P0-DOMAIN-011b
objective: 实现 Gateway 的短路决策路径，先验证 capability_not_found、policy_denied、identity_unbound、confirm_required 等不应调用 Adapter 的分支。
constraints:
  - Gateway 是唯一执行入口
  - Runtime 不得直接 import Mock Adapter
  - 本任务不实现 Adapter 正常执行路径
  - 任何短路路径均不得调用 Adapter
acceptance_criteria:
  - 未注册 Capability 返回 no_capability_found，Adapter must_not_be_called
  - Policy deny 时返回 denied，Adapter must_not_be_called
  - Identity 未绑定时返回 binding_required，Adapter must_not_be_called
  - 需要确认时返回 waiting_user / confirm_required，Adapter must_not_be_called
  - blocked 事件写入 Trace
  - import boundary 检查通过
happy_path_acceptance:
  - 短路路径均返回标准 ExecutionResult，且 trace_id 存在
failure_examples:
  - name: capability_not_found
    trigger: capability_id 未注册
    expected_result: ExecutionResult(status="no_capability_found", error_code="capability_not_found") 且 Adapter must_not_be_called
    forbidden_shortcut: 禁止 Runtime 直接返回 no_capability_found 绕过 Gateway
  - name: policy_denied
    trigger: PolicyDecision(decision="deny")
    expected_result: ExecutionResult(status="denied", error_code="policy_denied") 且 Adapter must_not_be_called
    forbidden_shortcut: 禁止先调用 Adapter 再返回 deny
  - name: identity_unbound
    trigger: IdentityCheckResult(status="unbound")
    expected_result: ExecutionResult(status="binding_required", error_code="identity_unbound") 且 Adapter must_not_be_called
    forbidden_shortcut: 禁止 fallback 到 system_scope
  - name: confirm_required
    trigger: PolicyDecision(decision="confirm_required")
    expected_result: ExecutionResult(status="waiting_user", error_code="confirm_required") 且 Adapter must_not_be_called
    forbidden_shortcut: 禁止绕过确认直接执行 Adapter
step_verification_points:
  - step: short_circuit_contract
    verification: uv run pytest tests/gateway/test_gateway_short_circuit.py
  - step: adapter_not_called_assertions
    verification: uv run pytest tests/gateway/test_gateway_must_not_call_adapter.py
  - step: trace_blocked_events
    verification: uv run pytest tests/gateway/test_gateway_trace_blocked_events.py
  - step: import_boundary
    verification: uv run pytest tests/architecture/test_import_boundaries.py
final_test_commands:
  - uv run pytest tests/gateway/test_gateway_short_circuit.py tests/gateway/test_gateway_must_not_call_adapter.py tests/architecture/test_import_boundaries.py
touched_paths:
  - app/gateway/
  - tests/gateway/
forbidden_paths:
  - app/runtime/
  - app/execution_fabric/real_adapters/
```

### P0-DOMAIN-003b2 — Capability Gateway Adapter 执行骨架

```yaml
task_id: P0-DOMAIN-003b2
title: Capability Gateway Adapter Execution Skeleton
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-003b1
  - P0-DOMAIN-008b
  - P0-DOMAIN-008c
  - P0-DOMAIN-008d
  - P0-DOMAIN-008e
objective: 实现 Gateway 的 Mock Adapter 正常执行与错误映射路径，验证 AdapterResult 到 ExecutionResult 的标准转换和 Trace pre/post 事件。
constraints:
  - 只调用 Mock Adapter，不接真实生产系统
  - Adapter 错误必须映射为标准 ExecutionResult.error_code
  - 不允许返回裸 dict
acceptance_criteria:
  - 已注册 Mock Capability 可执行并返回 completed
  - adapter_timeout / malformed / empty_response / http_500 / missing_required_field 均映射为标准错误
  - 执行前后写入 Trace pre/post 事件
  - import boundary 检查通过
happy_path_acceptance:
  - 已注册 Mock Capability 经 Registry、Policy、Identity、SecretProvider、Adapter 后返回 ExecutionResult(status="completed")
failure_examples:
  - name: adapter_timeout
    trigger: Mock 注入 error_mode=timeout
    expected_result: ExecutionResult(status="timeout", error_code="adapter_timeout")
    forbidden_shortcut: 禁止吞掉 timeout 返回 completed
  - name: adapter_payload_invalid
    trigger: Mock 注入 error_mode=malformed_json 或 missing_required_field
    expected_result: ExecutionResult(status="failed", error_code="adapter_error")
    forbidden_shortcut: 禁止把畸形 payload 原样透传给 Runtime
  - name: adapter_http_500
    trigger: Mock 注入 error_mode=http_500
    expected_result: ExecutionResult(status="failed", error_code="adapter_error")
    forbidden_shortcut: 禁止把上游 500 映射为 completed
step_verification_points:
  - step: adapter_execution_happy_path
    verification: uv run pytest tests/gateway/test_gateway_adapter_execution.py
  - step: adapter_error_mapping
    verification: uv run pytest tests/gateway/test_gateway_adapter_error_mapping.py
  - step: trace_pre_post
    verification: uv run pytest tests/gateway/test_gateway_trace_pre_post.py
  - step: import_boundary
    verification: uv run pytest tests/architecture/test_import_boundaries.py
final_test_commands:
  - uv run pytest tests/gateway/ tests/architecture/test_import_boundaries.py
touched_paths:
  - app/gateway/
  - tests/gateway/
forbidden_paths:
  - app/runtime/
  - app/execution_fabric/real_adapters/
```

### P0-DOMAIN-004a — Policy Guard 接口契约定义

```yaml
task_id: P0-DOMAIN-004a
title: Policy Guard Interface Contract
type: interface_contract
priority: P0
deliverable:
  - app/ports/policy_guard.py
constraints:
  - 只定义 PolicyGuardPort
  - 不实现策略逻辑
acceptance_criteria:
  - 定义第 8.6.8 节中的 `decide` 方法签名
  - 返回结构必须使用 PolicyDecision
  - decision 仅允许 allow / deny / confirm
  - reason_code 至少覆盖 role_not_allowed、policy_denied、high_risk_action_requires_confirm
touched_paths:
  - app/ports/policy_guard.py
forbidden_paths:
  - app/control_plane/
  - app/gateway/
```

### P0-DOMAIN-004b — Policy Guard 最小 deny 骨架

```yaml
task_id: P0-DOMAIN-004b
title: Policy Guard Minimal Deny Skeleton
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-004a
objective: 实现 Policy Guard 最小骨架，返回 allow / deny / confirm 决策；Trace 写入由 Gateway 通过 TracePort 统一完成。
constraints:
  - 不实现完整策略引擎
  - 不实现审批流
  - 不实现复杂 RBAC/ABAC
acceptance_criteria:
  - 支持配置某 Capability 对某角色 deny
  - 返回 PolicyDecision，decision=deny，reason_code=role_not_allowed 或 policy_denied
  - confirm 场景返回 PolicyDecision，decision=confirm，reason_code=high_risk_action_requires_confirm
  - 本任务不调用 Adapter、不写 Trace、不生成 ResponseEnvelope
  - Gateway 接收到 deny 后不调用 Adapter、写入 Trace、Runtime 返回拒绝 ResponseEnvelope 的链路验收后移到 P0-DOMAIN-003b1 / P0-DOMAIN-003b2 / P0-DOMAIN-007b / GT-009
happy_path_acceptance:
  - 给定允许角色与低风险 Capability 时返回 PolicyDecision(decision="allow")
failure_examples:
  - name: user_role_not_allowed
    trigger: request_context.role 不在 capability.allowed_roles 中
    expected_result: PolicyDecision(decision="deny", reason_code="role_not_allowed")
    forbidden_shortcut: 禁止返回 allow 并依赖 Gateway 二次判断
  - name: high_risk_requires_confirm
    trigger: capability.risk_level="high" 且策略要求确认
    expected_result: PolicyDecision(decision="confirm", reason_code="high_risk_action_requires_confirm")
    forbidden_shortcut: 禁止把 confirm 当 deny 或 allow
  - name: missing_policy_context
    trigger: 缺少 role/org_id/risk_level 等必要上下文
    expected_result: PolicyDecision(decision="deny", reason_code="missing_policy_context" 或 "policy_denied")
    forbidden_shortcut: 禁止默认 allow
step_verification_points:
  - step: policy_schema
    verification: uv run pytest tests/control_plane/policy/test_policy_decision_schema.py
  - step: policy_negative_paths
    verification: uv run pytest tests/control_plane/policy/test_policy_guard_negative.py
final_test_commands:
  - uv run pytest tests/control_plane/policy/
forbidden_shortcuts:
  - 禁止本任务写 Trace、生成 ResponseEnvelope 或调用 Adapter
touched_paths:
  - app/control_plane/policy/
  - tests/control_plane/policy/
forbidden_paths:
  - app/execution_fabric/
  - app/runtime/
```

### P0-DOMAIN-005a — Trace 接口契约定义

```yaml
task_id: P0-DOMAIN-005a
title: Trace Interface Contract
type: interface_contract
priority: P0
deliverable:
  - app/ports/trace.py
constraints:
  - 只定义 TracePort
  - 不实现存储逻辑
acceptance_criteria:
  - 定义第 8.6.8 节中的 `record_event(event: TraceEvent) -> None` 方法签名
  - 定义 start_task_trace、record_step、record_policy_decision、record_gateway_call、finalize_task_trace 或等价封装
  - 定义敏感数据写入前 sanitizer 钩子
  - sanitizer 至少扫描 Bearer token、sessionid、access_token、refresh_token、cookie、set-cookie 模式
touched_paths:
  - app/ports/trace.py
forbidden_paths:
  - app/control_plane/
  - app/gateway/
```

### P0-DOMAIN-005b — Trace 最小写入骨架

```yaml
task_id: P0-DOMAIN-005b
title: Trace Minimal Write Skeleton
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-005a
objective: 实现最小 Trace 写入能力，支持 Gateway、Policy、Runtime 的关键事件记录。
constraints:
  - 不记录凭证、token、cookie、session token
  - 不保存原始敏感 payload
  - 写入前必须经过 sanitizer 占位
acceptance_criteria:
  - Gateway pre/post 调用写入 Trace
  - Policy deny 写入 Trace
  - no_capability_found 写入 Trace
  - identity_binding_required 写入 Trace
  - adapter_timeout 写入 Trace
  - 伪造 Bearer token / sessionid / refresh_token 输入触发 sanitizer，拒绝写入 Trace
  - sanitizer 测试确认敏感值未落库、未进入 response_envelope.trace_summary
happy_path_acceptance:
  - 合法 TraceEvent 可写入并可按 task_id / trace_id 查询
failure_examples:
  - name: bearer_token_detected
    trigger: event_payload 包含 Bearer abc.def.ghi
    expected_result: sanitizer 抛出异常或拒绝写入，数据库无该敏感值
    forbidden_shortcut: 禁止只过滤字段名 token 而不扫描值模式
  - name: sessionid_cookie_detected
    trigger: event_payload 包含 sessionid=mocksession123
    expected_result: sanitizer 拒绝写入，trace_summary 不含 sessionid
    forbidden_shortcut: 禁止把敏感值写入 raw_payload 后再隐藏展示
  - name: raw_secret_in_event_payload
    trigger: event_payload 包含 password / access_token / refresh_token 的伪造值
    expected_result: 拒绝写入或严格脱敏，测试断言持久化输出无明文
    forbidden_shortcut: 禁止仅对 ResponseEnvelope 脱敏而 Trace 落库
step_verification_points:
  - step: trace_schema
    verification: uv run pytest tests/control_plane/trace/test_trace_event_schema.py
  - step: sanitizer
    verification: uv run pytest tests/control_plane/trace/test_trace_sanitizer.py
  - step: persistence
    verification: uv run pytest tests/control_plane/trace/test_trace_persistence.py
final_test_commands:
  - uv run pytest tests/control_plane/trace/
forbidden_shortcuts:
  - 禁止保存原始敏感 payload 到任何持久化字段
touched_paths:
  - app/control_plane/trace/
  - app/observability/
  - alembic/versions/
  - tests/control_plane/trace/
forbidden_paths:
  - app/execution_fabric/real_adapters/
```

### P0-DOMAIN-006a — IdentityMapping 接口契约定义

```yaml
task_id: P0-DOMAIN-006a
title: IdentityMapping Interface Contract
type: interface_contract
priority: P0
deliverable:
  - app/ports/identity_mapping.py
constraints:
  - 只定义 IdentityMappingPort
  - 不实现凭证存储
acceptance_criteria:
  - 定义第 8.6.8 节中的 `resolve_execution_identity` 方法签名
  - 可额外定义 get_mapping、list_mappings、set_mock_mapping、update_status、check_binding_required
  - 支持按 target_system、binding_scope、account_set_id、device_domain_id 查询
  - 返回结构必须使用 IdentityCheckResult，不得包含明文凭证或 token
touched_paths:
  - app/ports/identity_mapping.py
forbidden_paths:
  - app/control_plane/
  - app/gateway/
```

### P0-DOMAIN-006b — IdentityMapping Mock 表与检查骨架

```yaml
task_id: P0-DOMAIN-006b
title: IdentityMapping Mock Table and Precheck Skeleton
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-006a
objective: 实现 IdentityMapping Mock 表和执行前绑定状态检查逻辑，仅返回 IdentityCheckResult，不负责 Gateway / Runtime / SDUI 行为。
constraints:
  - 不保存明文密码
  - 不保存 access_token / refresh_token / cookie / session token
  - user_delegated 未绑定不得 fallback 到 system_scope
  - 必须支持同一 ai_user_id 对同一目标系统存在多个绑定记录
  - 多身份选择必须基于明确 scope，例如 account_set_id、org_id、tenant_id、device_domain_id
acceptance_criteria:
  - 支持 active / unbound / expired / revoked / verification_failed 状态
  - 支持同一用户映射多个 U8 账套
  - 支持同一用户映射多个海康设备域
  - 未指定必要 scope 时返回 IdentityCheckResult(status="needs_scope", reason_code="needs_binding_scope" 或 "needs_disambiguation")，不得默认选择第一个绑定
  - 指定 revoked / expired 绑定时返回 IdentityCheckResult(status="blocked", reason_code="identity_revoked" 或 "identity_expired")
  - binding_required=true 且未绑定时返回 IdentityCheckResult(status="unbound", reason_code="identity_unbound")
  - 本任务不生成 SDUI、不写 Trace、不调用 Adapter；SDUI binding_required / operator_handback card 和 Trace 记录由 Gateway / Runtime / Golden Task 验收
  - 预留 concurrent_binding_update_conflict 与 stale_binding_version_rejected 测试占位
happy_path_acceptance:
  - active 单一绑定在明确 scope 下返回 IdentityCheckResult(status="active")
failure_examples:
  - name: unbound_identity
    trigger: binding_required=true 且 ai_user_id 无目标系统绑定
    expected_result: IdentityCheckResult(status="unbound", reason_code="identity_unbound")
    forbidden_shortcut: 禁止 fallback 到 system_scope
  - name: revoked_binding
    trigger: 指定 binding_id 状态为 revoked
    expected_result: IdentityCheckResult(status="blocked", reason_code="identity_revoked")
    forbidden_shortcut: 禁止忽略 revoked 状态
  - name: expired_binding
    trigger: 指定 binding_id 状态为 expired
    expected_result: IdentityCheckResult(status="blocked", reason_code="identity_expired")
    forbidden_shortcut: 禁止自动刷新或自动改 active
  - name: ambiguous_scope_multi_account
    trigger: 同一用户有多个 U8 account_set_id 但请求未指定 scope
    expected_result: IdentityCheckResult(status="needs_scope", reason_code="needs_binding_scope" 或 "needs_disambiguation")
    forbidden_shortcut: 禁止默认选择第一个绑定
step_verification_points:
  - step: mock_data_seed
    verification: uv run pytest tests/control_plane/identity/test_identity_mock_seed.py
  - step: binding_status
    verification: uv run pytest tests/control_plane/identity/test_identity_status_paths.py
  - step: multi_scope
    verification: uv run pytest tests/control_plane/identity/test_identity_multi_scope.py
final_test_commands:
  - uv run pytest tests/control_plane/identity/
forbidden_shortcuts:
  - 禁止返回明文凭证或 token
  - 禁止本任务生成 SDUI 或写 Trace
touched_paths:
  - app/control_plane/identity/
  - alembic/versions/
  - tests/control_plane/identity/
forbidden_paths:
  - app/execution_fabric/real_adapters/
```

### P0-DOMAIN-007a — Runtime 接口契约定义

```yaml
task_id: P0-DOMAIN-007a
title: Runtime Interface Contract
type: interface_contract
priority: P0
deliverable:
  - app/ports/runtime.py
constraints:
  - 只定义 RuntimePort
  - 不实现业务逻辑
acceptance_criteria:
  - 定义第 8.6.8 节中的 `handle_user_message` 方法签名
  - 输入包含 channel、ai_user_id、session_id、message、client_capabilities
  - 输出必须使用 ResponseEnvelope
touched_paths:
  - app/ports/runtime.py
forbidden_paths:
  - app/runtime/
  - app/gateway/
```

### P0-DOMAIN-007b — Runtime 主链骨架

```yaml
task_id: P0-DOMAIN-007b
title: Runtime Main Chain Minimal Skeleton
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-007a
  - P0-DOMAIN-001b
  - P0-DOMAIN-003b2
  - P0-DOMAIN-005b
  - P0-DOMAIN-009b
  - P0-DOMAIN-010b
objective: 实现 Runtime 最小主链：用户请求 → Task → CapabilityRef Mock 匹配 → Gateway 调用 → ResponseEnvelope。
constraints:
  - Runtime 只能依赖 CapabilityGatewayPort
  - 不得 import Adapter
  - 不实现复杂 Planner
  - 无能力时返回 no_capability_found
acceptance_criteria:
  - 正向 Mock 查询可完成
  - 无 Capability 返回 no_capability_found
  - Policy deny 返回拒绝响应
  - Identity 未绑定返回绑定引导响应
  - 全过程可关联 Trace
happy_path_acceptance:
  - 用户输入可创建 TaskRecord，经 Mock StructuredOutput 得到 CapabilityRef，通过 Gateway 返回 ResponseEnvelope(status="completed")
failure_examples:
  - name: no_capability_found
    trigger: Mock StructuredOutput 返回未知 capability_id 或无法匹配
    expected_result: TaskRecord.status="no_capability_found" 且 ResponseEnvelope.status="no_capability_found"
    forbidden_shortcut: 禁止直接返回 generic failed
  - name: policy_denied
    trigger: Gateway 返回 ExecutionResult(status="denied")
    expected_result: ResponseEnvelope.status="blocked"，message 包含无权限或拒绝原因
    forbidden_shortcut: 禁止继续调用下一步 Capability
  - name: binding_required
    trigger: Gateway 返回 ExecutionResult(status="binding_required", error_code="identity_unbound")
    expected_result: ResponseEnvelope.status="blocked" 且 ui.component_type="operator_handback_card" 或 binding_required_card
    forbidden_shortcut: 禁止 Runtime 自行选择绑定身份
  - name: gateway_timeout
    trigger: Gateway 返回 ExecutionResult(status="timeout")
    expected_result: ResponseEnvelope.status="failed" 且 trace_id 保留
    forbidden_shortcut: 禁止吞掉错误返回 completed
step_verification_points:
  - step: task_creation
    verification: uv run pytest tests/runtime/test_task_creation.py
  - step: gateway_mapping
    verification: uv run pytest tests/runtime/test_runtime_gateway_result_mapping.py
  - step: response_composition
    verification: uv run pytest tests/runtime/test_runtime_response_envelope.py
final_test_commands:
  - uv run pytest tests/runtime/
forbidden_shortcuts:
  - 禁止 Runtime import app/execution_fabric/
  - 禁止 Runtime 直接调用 Mock Adapter
touched_paths:
  - app/runtime/
  - app/api/v1/runtime.py
  - tests/runtime/
forbidden_paths:
  - app/execution_fabric/
```

### P0-DOMAIN-008a — Mock Adapter 接口契约定义

```yaml
task_id: P0-DOMAIN-008a
title: Adapter Interface Contract
type: interface_contract
priority: P0
deliverable:
  - app/ports/adapter.py
constraints:
  - 只定义 AdapterPort
  - 不实现真实业务系统调用
acceptance_criteria:
  - 定义第 8.6.8 节中的 `execute` 方法签名
  - execution_context 支持 mock_error_mode，用于 Phase 0 错误注入
  - mock_error_mode 仅允许 timeout、permission_denied、malformed_json、empty_response、http_500、missing_required_field
  - 输出必须使用 AdapterResult，不得返回裸 dict
  - 定义错误映射：timeout→adapter_timeout，permission_denied→upstream_permission_denied，malformed_json→adapter_payload_invalid，empty_response→adapter_empty_response，http_500→adapter_http_500，missing_required_field→adapter_missing_required_field
touched_paths:
  - app/ports/adapter.py
forbidden_paths:
  - app/execution_fabric/
  - app/gateway/
```

### P0-DOMAIN-008b — Mock OA Adapter

```yaml
task_id: P0-DOMAIN-008b
title: Mock OA Adapter
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-008a
objective: 实现泛微 OA Mock Adapter，用于 Golden Task 验证 OA 查询、草稿创建或流程状态查询。
constraints:
  - 只做 Mock
  - 不接真实 OA
  - 不保存真实凭证
  - 支持可配置 mock_error_mode
  - 错误注入不得破坏 AdapterPort 对 Gateway 的标准返回契约
acceptance_criteria:
  - Mock OA 可返回待办列表，每条至少包含 workflow_id:str、title:str、status:pending|approved|rejected、applicant:str、created_at:str
  - Mock OA 可返回流程状态，至少包含 workflow_id:str、current_step:draft|pending|approved|rejected、approver:str
  - 支持 timeout、permission_denied、malformed_json、empty_response、http_500、missing_required_field 至少 6 种错误模式
  - malformed payload 只模拟上游原始异常，Gateway 收到的仍是标准 AdapterResult
  - 可被 Gateway 调用但不能被 Runtime 直接调用
happy_path_acceptance:
  - Mock OA Adapter 在正向输入下返回 AdapterResult(status="ok")，data 字段满足本任务定义的最小字段结构
failure_examples:
  - name: timeout
    trigger: mock_error_mode=timeout
    expected_result: AdapterResult(status="error", error_code="adapter_timeout")
    forbidden_shortcut: 禁止 sleep 固定长时间导致测试不可控
  - name: permission_denied
    trigger: mock_error_mode=permission_denied
    expected_result: AdapterResult(status="error", error_code="upstream_permission_denied")
    forbidden_shortcut: 禁止返回 HTTP 200 + status ok
  - name: malformed_json
    trigger: mock_error_mode=malformed_json
    expected_result: AdapterResult(status="error", error_code="adapter_payload_invalid")，Gateway 侧仍接收标准 AdapterResult
    forbidden_shortcut: 禁止向 Gateway 抛出未捕获裸异常
  - name: missing_required_field
    trigger: mock_error_mode=missing_required_field
    expected_result: AdapterResult(status="error", error_code="adapter_missing_required_field")
    forbidden_shortcut: 禁止自动补全缺失字段让测试通过
step_verification_points:
  - step: adapter_contract
    verification: uv run pytest tests/execution_fabric/mock_adapters/oa/test_contract.py
  - step: error_modes
    verification: uv run pytest tests/execution_fabric/mock_adapters/oa/test_error_modes.py
final_test_commands:
  - uv run pytest tests/execution_fabric/mock_adapters/oa/
forbidden_shortcuts:
  - 禁止接真实业务系统
  - 禁止返回裸 dict 给 Gateway
touched_paths:
  - app/execution_fabric/mock_adapters/oa/
  - tests/execution_fabric/mock_adapters/oa/
forbidden_paths:
  - app/runtime/
  - app/execution_fabric/real_adapters/
```

### P0-DOMAIN-008c — Mock U8 Adapter

```yaml
task_id: P0-DOMAIN-008c
title: Mock U8 Adapter
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-008a
objective: 实现用友 U8 Mock Adapter，用于 Golden Task 验证财务/ERP 查询类场景。
constraints:
  - 只做 Mock
  - 不接真实 U8
  - 不保存真实凭证
  - 支持可配置 mock_error_mode
  - 错误注入不得破坏 AdapterPort 对 Gateway 的标准返回契约
acceptance_criteria:
  - Mock U8 单据结果至少包含 account_set_id:str、document_no:str、document_status:draft|posted|voided、amount:number、currency:str
  - Mock U8 余额结果至少包含 account_set_id:str、vendor_id:str、vendor_name:str、balance:number、currency:str
  - 支持 timeout、permission_denied、malformed_json、empty_response、http_500、missing_required_field 至少 6 种错误模式
  - 支持多账套 Mock 参数 account_set_id
  - 可被 Gateway 调用但不能被 Runtime 直接调用
happy_path_acceptance:
  - Mock U8 Adapter 在正向输入下返回 AdapterResult(status="ok")，data 字段满足本任务定义的最小字段结构
failure_examples:
  - name: timeout
    trigger: mock_error_mode=timeout
    expected_result: AdapterResult(status="error", error_code="adapter_timeout")
    forbidden_shortcut: 禁止 sleep 固定长时间导致测试不可控
  - name: permission_denied
    trigger: mock_error_mode=permission_denied
    expected_result: AdapterResult(status="error", error_code="upstream_permission_denied")
    forbidden_shortcut: 禁止返回 HTTP 200 + status ok
  - name: malformed_json
    trigger: mock_error_mode=malformed_json
    expected_result: AdapterResult(status="error", error_code="adapter_payload_invalid")，Gateway 侧仍接收标准 AdapterResult
    forbidden_shortcut: 禁止向 Gateway 抛出未捕获裸异常
  - name: missing_required_field
    trigger: mock_error_mode=missing_required_field
    expected_result: AdapterResult(status="error", error_code="adapter_missing_required_field")
    forbidden_shortcut: 禁止自动补全缺失字段让测试通过
step_verification_points:
  - step: adapter_contract
    verification: uv run pytest tests/execution_fabric/mock_adapters/u8/test_contract.py
  - step: error_modes
    verification: uv run pytest tests/execution_fabric/mock_adapters/u8/test_error_modes.py
final_test_commands:
  - uv run pytest tests/execution_fabric/mock_adapters/u8/
forbidden_shortcuts:
  - 禁止接真实业务系统
  - 禁止返回裸 dict 给 Gateway
touched_paths:
  - app/execution_fabric/mock_adapters/u8/
  - tests/execution_fabric/mock_adapters/u8/
forbidden_paths:
  - app/runtime/
  - app/execution_fabric/real_adapters/
```

### P0-DOMAIN-008d — Mock 海康 / iVMS Adapter

```yaml
task_id: P0-DOMAIN-008d
title: Mock Hikvision iVMS Adapter
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-008a
objective: 实现海康 iVMS Mock Adapter，用于 Golden Task 验证设备/视频平台查询类场景。
constraints:
  - 只做 Mock
  - 不接真实海康系统
  - 不处理视频帧流
  - 不把原始设备心跳或视频流引入 LLM 上下文
  - 支持可配置 mock_error_mode
  - 错误注入不得破坏 AdapterPort 对 Gateway 的标准返回契约
acceptance_criteria:
  - Mock iVMS 设备状态至少包含 device_domain_id:str、device_id:str、online:bool、last_seen_at:str、video_frame_included:false
  - Mock iVMS 告警摘要不得包含视频帧流或原始设备心跳流
  - 支持 timeout、permission_denied、malformed_json、empty_response、http_500、missing_required_field 至少 6 种错误模式
  - 支持多设备域 Mock 参数 device_domain_id
  - 可被 Gateway 调用但不能被 Runtime 直接调用
happy_path_acceptance:
  - Mock Hikvision iVMS Adapter 在正向输入下返回 AdapterResult(status="ok")，data 字段满足本任务定义的最小字段结构
failure_examples:
  - name: timeout
    trigger: mock_error_mode=timeout
    expected_result: AdapterResult(status="error", error_code="adapter_timeout")
    forbidden_shortcut: 禁止 sleep 固定长时间导致测试不可控
  - name: permission_denied
    trigger: mock_error_mode=permission_denied
    expected_result: AdapterResult(status="error", error_code="upstream_permission_denied")
    forbidden_shortcut: 禁止返回 HTTP 200 + status ok
  - name: malformed_json
    trigger: mock_error_mode=malformed_json
    expected_result: AdapterResult(status="error", error_code="adapter_payload_invalid")，Gateway 侧仍接收标准 AdapterResult
    forbidden_shortcut: 禁止向 Gateway 抛出未捕获裸异常
  - name: missing_required_field
    trigger: mock_error_mode=missing_required_field
    expected_result: AdapterResult(status="error", error_code="adapter_missing_required_field")
    forbidden_shortcut: 禁止自动补全缺失字段让测试通过
step_verification_points:
  - step: adapter_contract
    verification: uv run pytest tests/execution_fabric/mock_adapters/hikvision_ivms/test_contract.py
  - step: error_modes
    verification: uv run pytest tests/execution_fabric/mock_adapters/hikvision_ivms/test_error_modes.py
final_test_commands:
  - uv run pytest tests/execution_fabric/mock_adapters/hikvision_ivms/
forbidden_shortcuts:
  - 禁止接真实业务系统
  - 禁止返回裸 dict 给 Gateway
touched_paths:
  - app/execution_fabric/mock_adapters/hikvision_ivms/
  - tests/execution_fabric/mock_adapters/hikvision_ivms/
forbidden_paths:
  - app/runtime/
  - app/execution_fabric/real_adapters/
```

### P0-DOMAIN-008e — Mock Adapter 错误注入管理端点

```yaml
task_id: P0-DOMAIN-008e
title: Mock Adapter Error Injection Control Endpoint
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-008b
  - P0-DOMAIN-008c
  - P0-DOMAIN-008d
objective: 为 Golden Task 负向路径提供可重复、非随机的 Mock 错误注入方式。
deliverable:
  - app/api/v1/mock_control.py
  - app/execution_fabric/mock_adapters/error_injection.py
constraints:
  - 仅 Phase 0 Mock 环境可用
  - 仅当 `ENV=testing` 或 `PHASE0_MOCK_MODE=true` 时注册该端点
  - 非测试环境启动时必须返回 404 或完全不注册路由
  - 不暴露生产系统接口
  - 不允许随机错误；所有错误必须由测试显式注入
acceptance_criteria:
  - 提供 `POST /mock/{capability_id}/inject` 临时管理端点
  - 测试环境中端点可用，非测试环境中端点不可用或返回 404
  - 请求体支持 error_mode、duration、error_detail
  - error_mode 枚举：timeout、permission_denied、malformed_json、empty_response、http_500、missing_required_field
  - duration 枚举：next_1_call、next_3_calls、permanent
  - Golden Task 可通过该端点设置异常状态并可重复复现
  - 注入状态可在测试结束后清除
happy_path_acceptance:
  - ENV=testing 或 PHASE0_MOCK_MODE=true 时，POST /mock/{capability_id}/inject 可设置可重复错误注入状态
failure_examples:
  - name: endpoint_disabled_outside_testing
    trigger: ENV!=testing 且 PHASE0_MOCK_MODE!=true
    expected_result: 端点 404 或完全不注册
    forbidden_shortcut: 禁止仅靠前端隐藏端点
  - name: invalid_error_mode
    trigger: error_mode 不在 timeout|permission_denied|malformed_json|empty_response|http_500|missing_required_field
    expected_result: 422 或 validation_error
    forbidden_shortcut: 禁止把未知模式当 timeout
  - name: duration_exhausted
    trigger: duration=next_1_call 且调用两次
    expected_result: 第一次注入生效，第二次恢复正常
    forbidden_shortcut: 禁止永久污染全局状态
step_verification_points:
  - step: endpoint_guard
    verification: uv run pytest tests/execution_fabric/mock_adapters/test_error_injection_guard.py
  - step: injection_lifecycle
    verification: uv run pytest tests/execution_fabric/mock_adapters/test_error_injection_lifecycle.py
final_test_commands:
  - uv run pytest tests/execution_fabric/mock_adapters/
forbidden_shortcuts:
  - 禁止随机错误
  - 禁止非测试环境启用 mock control route
touched_paths:
  - app/api/v1/mock_control.py
  - app/execution_fabric/mock_adapters/
  - tests/execution_fabric/mock_adapters/
forbidden_paths:
  - app/runtime/
  - app/execution_fabric/real_adapters/
```

### P0-DOMAIN-009a — SDUI Response Envelope 接口契约定义

```yaml
task_id: P0-DOMAIN-009a
title: SDUI Response Envelope Contract
type: interface_contract
priority: P0
deliverable:
  - app/ports/response_envelope.py
  - app/contracts/sdui/
constraints:
  - 只定义最小协议和类型
  - 不实现完整渲染器
  - Phase 0 仅支持静态 JSON Schema 渲染
  - 禁止实现多轮确认回路
  - 禁止实现动态表单编排器
acceptance_criteria:
  - 定义第 8.6.4 节 ResponseEnvelope 与 UIComponent 模型
  - 定义 confirm_card
  - 定义 operator_handback_card / binding_required_card
  - 定义 user_action 回传结构
  - confirm_card 的 action 仅允许一次性 confirm，不得实现多轮确认状态机
touched_paths:
  - app/ports/response_envelope.py
  - app/contracts/sdui/
forbidden_paths:
  - web/src/sdui_renderer/
```

### P0-DOMAIN-009b — SDUI Response Envelope 最小实现

```yaml
task_id: P0-DOMAIN-009b
title: SDUI Response Envelope Minimal Implementation
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-009a
objective: 实现 ResponseEnvelope 生成工具，支持 message、fallback_text 和一种 confirm card / binding card。
constraints:
  - 不实现完整 SDUI Renderer
  - Phase 0 仅支持静态 JSON Schema 渲染
  - 禁止实现多轮确认回路
  - 禁止实现动态表单编排器
  - UI schema 不得包含凭证、token、cookie、内部接口地址
  - 所有 user_action 必须回到 Runtime / Gateway
acceptance_criteria:
  - Runtime 可返回普通 message envelope
  - Policy confirm 可返回 confirm card
  - Identity 未绑定可返回 binding_required card 或 operator_handback_card(action=bind_required)
  - scope 不明确返回 operator_handback_card(action=clarify_scope)，不得误用 binding_required_card
  - CLI fallback_text 存在
  - Phase 0 仅允许 confirm card → 一次性 user_action confirm → Runtime/Gateway 执行 Mock Capability，不允许多轮确认回路
happy_path_acceptance:
  - 普通 message、confirm_card、operator_handback_card 均可生成符合 ResponseEnvelope Schema 的输出
failure_examples:
  - name: unsupported_component_type
    trigger: component_type 不在 confirm_card|operator_handback_card|binding_required_card 等 Phase 0 允许范围
    expected_result: validation_error
    forbidden_shortcut: 禁止静默透传未知 UI 组件
  - name: missing_fallback_text
    trigger: ResponseEnvelope 缺少 fallback_text
    expected_result: validation_error 或测试失败
    forbidden_shortcut: 禁止用空字符串规避
  - name: confirm_card_missing_action
    trigger: confirm_card 没有 action=confirm
    expected_result: validation_error
    forbidden_shortcut: 禁止自动补 action 而不暴露错误
  - name: secret_in_ui_schema
    trigger: ui payload 包含 token/cookie/internal_url 伪造值
    expected_result: sanitizer 拒绝或测试失败
    forbidden_shortcut: 禁止仅隐藏展示但仍输出到 JSON
step_verification_points:
  - step: response_schema
    verification: uv run pytest tests/runtime/response_composer/test_response_envelope_schema.py
  - step: sdui_cards
    verification: uv run pytest tests/runtime/response_composer/test_sdui_cards.py
  - step: forbidden_payload
    verification: uv run pytest tests/runtime/response_composer/test_sdui_forbidden_payload.py
final_test_commands:
  - uv run pytest tests/runtime/response_composer/
forbidden_shortcuts:
  - 禁止实现多轮确认状态机
  - 禁止业务页面手写 fetch/axios 调后端业务 API
touched_paths:
  - app/runtime/response_composer/
  - app/contracts/sdui/
  - tests/runtime/response_composer/
forbidden_paths:
  - app/execution_fabric/
```

### P0-DOMAIN-010a — LLM Provider / Structured Output 接口契约定义

```yaml
task_id: P0-DOMAIN-010a
title: LLM Provider and Structured Output Port Contract
type: interface_contract
priority: P0
deliverable:
  - app/ports/llm_provider.py
  - app/ports/structured_output.py
constraints:
  - 只定义接口
  - 不绑定 OpenAI SDK / instructor / PydanticAI 具体实现
acceptance_criteria:
  - LLMProviderPort 支持 complete / chat 或等价抽象
  - StructuredOutputPort 支持 parse_to_schema
  - 返回结构支持错误类型和 trace metadata
touched_paths:
  - app/ports/llm_provider.py
  - app/ports/structured_output.py
forbidden_paths:
  - app/runtime/
  - app/infra/llm/
```

### P0-DOMAIN-010b — Mock Structured Output 实现

```yaml
task_id: P0-DOMAIN-010b
title: Mock Structured Output Implementation
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-010a
objective: 提供可预测的 Mock Structured Output，用于 Runtime 和 Golden Task 不依赖真实 LLM 也能测试。
constraints:
  - 不把真实模型调用写进 Runtime
  - 不使用 PydanticAI 作为生产依赖
acceptance_criteria:
  - 给定测试输入可返回预期 CapabilityRef
  - 可模拟结构化输出失败
  - Runtime 测试可在无模型环境下运行
happy_path_acceptance:
  - 固定输入可稳定返回预期 CapabilityRef，测试不依赖真实 LLM
failure_examples:
  - name: unknown_intent
    trigger: 输入不匹配任何已注册 mock intent
    expected_result: 返回 no_capability_found 或 structured_output_failed 的可测试错误
    forbidden_shortcut: 禁止默认映射到第一个 Capability
  - name: malformed_model_output
    trigger: 模拟非 JSON / 缺字段输出
    expected_result: parse 失败并返回标准错误，不抛未捕获异常
    forbidden_shortcut: 禁止静默补字段
step_verification_points:
  - step: deterministic_mapping
    verification: uv run pytest tests/infra/llm/test_mock_structured_output.py
  - step: negative_output
    verification: uv run pytest tests/infra/llm/test_mock_structured_output_negative.py
final_test_commands:
  - uv run pytest tests/infra/llm/
forbidden_shortcuts:
  - 禁止调用真实模型
touched_paths:
  - app/infra/llm/mock_structured_output/
  - tests/infra/llm/
forbidden_paths:
  - app/runtime/
  - experiments/
```

### P0-DOMAIN-011a — SecretProvider 接口契约定义

```yaml
task_id: P0-DOMAIN-011a
title: SecretProvider Interface Contract
type: interface_contract
priority: P0
deliverable:
  - app/ports/secret_provider.py
constraints:
  - 只定义接口，不实现 Vault / OAuth2 / vendor token
  - 不保存、不读取、不返回真实凭证
  - 不允许 Gateway / Adapter 绕过 SecretProviderPort 处理凭证
acceptance_criteria:
  - 定义第 8.6.8 节中的 `resolve_secret_ref` 与 `inject_execution_secret` 方法签名
  - 返回结构不得包含明文 secret、password、token、cookie、sessionid
  - 伪造 credential_ref 可返回 `mock_secret_injected=true` 或脱敏占位
  - 测试证明 Runtime / Workflow / Skill 无法 import 任何具体 Secret 实现
touched_paths:
  - app/ports/secret_provider.py
  - tests/ports/
forbidden_paths:
  - app/runtime/
  - app/execution_fabric/real_adapters/
  - app/control_plane/identity/
```

### P0-DOMAIN-011b — Noop SecretProvider 骨架

```yaml
task_id: P0-DOMAIN-011b
title: Noop SecretProvider Skeleton
type: implementation
priority: P0
depends_on:
  - P0-DOMAIN-011a
objective: 提供 Phase 0 Mock / Noop SecretProvider，用于证明 Gateway 只能通过 SecretProviderPort 触发凭证注入链路，不处理真实凭证。
constraints:
  - 不接 Vault / KMS / OAuth2 / 真实厂商 token
  - 不返回明文凭证
  - 只允许返回脱敏 mock 注入信号
acceptance_criteria:
  - Gateway 可调用 Noop SecretProvider 并获得 `mock_secret_injected=true`
  - Trace 只记录 credential_ref 或 credential_usage_event，不记录 secret value
  - 含伪造 token 的返回值被 sanitizer 拦截
happy_path_acceptance:
  - Noop SecretProvider 返回脱敏 mock 注入信号，不返回真实凭证值
failure_examples:
  - name: secret_value_requested
    trigger: 调用方请求明文 secret/password/token
    expected_result: 返回拒绝或脱敏占位，不返回敏感值
    forbidden_shortcut: 禁止为测试方便返回 dummy_token_123
  - name: token_like_payload_generated
    trigger: Noop provider 内部构造含 Bearer/sessionid/access_token 的伪造值
    expected_result: sanitizer 拦截，不进入 Trace 或 ResponseEnvelope
    forbidden_shortcut: 禁止只在日志里标记 safe
  - name: bypass_secret_provider
    trigger: Gateway / Adapter 尝试绕过 SecretProviderPort 读取 mock secret
    expected_result: import boundary 或测试失败
    forbidden_shortcut: 禁止在 Adapter 中硬编码 credential
step_verification_points:
  - step: noop_provider_contract
    verification: uv run pytest tests/infra/security/test_noop_secret_provider.py
  - step: sanitizer_integration
    verification: uv run pytest tests/infra/security/test_noop_secret_provider_sanitizer.py
final_test_commands:
  - uv run pytest tests/infra/security/ tests/architecture/test_import_boundaries.py
forbidden_shortcuts:
  - 禁止返回任何真实或形似真实的 secret 值
touched_paths:
  - app/infra/security/noop_secret_provider/
  - tests/infra/security/
forbidden_paths:
  - app/runtime/
  - app/execution_fabric/real_adapters/
```



---

## 12. Golden Task Requirements

### 12.1 总体要求

Phase 0 必须建立不少于 11 个核心 Golden Task。

要求：

- 至少 7 个正向或可控成功场景。
- 至少 4 个负向 / 边界 / 安全拒绝场景，其中必须包含多身份 scope 不明确路径。
- 验收时正向任务总体通过率 >= 80%。
- 验收时负向路径、边界路径和安全拒绝路径必须 100% 通过。
- 每个任务必须可重复执行。
- 每个任务必须关联 trace_id。
- 每个失败任务必须有明确终态和 error_code。
- Golden Task 不依赖真实 OA / U8 / 海康系统。
- Golden Task 不依赖真实凭证。

### 12.2 Golden Task 固定 Fixture 格式

每个 Golden Task 必须在 `tests/golden_tasks/fixtures/GT-XXX.yaml` 下提供固定 fixture，并由 `tests/golden_tasks/test_golden_tasks.py` 或等价 pytest 自动执行。

Fixture 不是可选示例，而是 Codex / Claude Code 必须遵守的测试输入、Mock 状态、Trace 断言和禁止项。每个 fixture 至少包含：

```yaml
golden_task_id: GT-XXX
title: 固定标题
category: positive | negative
given:
  ai_user_id: user_001
  roles: [employee]
  identity_mappings: []
  registered_capabilities: []
  mock_state: {}
when:
  channel: web
  message: 用户输入原文
  arguments: {}
then_response:
  status: completed | blocked | waiting_user | failed | no_capability_found
  envelope:
    message_contains: []
    ui.component_type: none | confirm_card | operator_handback_card | binding_required_card
    ui.action: none | confirm | bind_required | clarify_scope
    ui.target_system: oa | u8 | hikvision_ivms | null
then_trace:
  event_sequence: []
then_forbidden:
  - trace_contains_token
  - response_contains_internal_url
  - mock_adapter_was_called_when_forbidden
mock_failure_injection:
  enabled: true | false
  endpoint: POST /mock/{capability_id}/inject
  payload: null | {}
adapter_assertion:
  must_be_called: true | false
  must_not_be_called: true | false
```

硬性要求：

- 每个 Golden Task 必须固定 Mock 返回字段、枚举值和错误样例。
- 每个会调用 Mock Adapter 的 Golden Task 必须指定至少一种 Adapter 错误注入模式；不调用 Adapter 的负向任务必须显式声明 `mode: none` 与 `adapter_assertion.must_not_be_called=true`。
- 正向任务可以通过 companion failure injection 验证异常路径，但 companion injection 不得改变正向主断言。
- Golden Task 不得通过简单返回 `status=completed` 或 `{"status":"ok"}` 刷通过率。
- Golden Task 的通过必须依赖 Runtime、Gateway、Policy、Identity、Adapter、Trace、ResponseEnvelope 中至少两个以上边界节点。
- Trace assertion 不满足时，即使最终响应正确，也判定任务失败。
- Trace / Response / Log 中不得出现 token、cookie、sessionid、内部接口地址。


### 12.2.1 Golden Task Mock 正向状态注入机制

Golden Task fixture 中的 `given.mock_oa_state`、`given.mock_u8_state`、`given.mock_ivms_state` 不得由 Codex / Claude Code 自行发明加载方式。Phase 0 固定采用测试夹具注入机制，不新增生产 HTTP 状态管理端点。

```yaml
mock_state_injection_strategy:
  strategy: pytest_fixture_loader
  loader_path: tests/golden_tasks/conftest.py
  allowed_targets:
    - MockOAAdapter.set_state(state: dict[str, Any])
    - MockU8Adapter.set_state(state: dict[str, Any])
    - MockIVMSAdapter.set_state(state: dict[str, Any])
  fixture_source: tests/golden_tasks/fixtures/GT-XXX.yaml
  reset_policy: function_scope_reset
  db_isolation: function_scope_transaction_rollback_or_test_schema
forbidden:
  - 禁止把正向 mock state 注入端点暴露到生产 API
  - 禁止让 Mock Adapter 使用随机默认状态刷通过率
  - 禁止跨 Golden Task 复用脏状态
```

测试执行时，`tests/golden_tasks/conftest.py` 必须读取每个 Golden Task YAML 的 `given` 区域，并在每个 test function 开始前调用对应 Mock Adapter 的 `set_state()`；测试结束后必须清空 Mock Adapter 状态、错误注入状态和数据库事务。

错误注入仍使用 `POST /mock/{capability_id}/inject`，但该端点只用于负向路径错误模式，不负责正向业务数据加载。

### 12.3 核心 Golden Task

| ID | 类型 | 场景 | 目标验证 | 必须绑定 fixture |
|---|---|---|---|---|
| GT-001 | 正向 | 查询 OA 待办流程 | Runtime → Gateway → Mock OA → Trace → ResponseEnvelope | `tests/golden_tasks/fixtures/GT-001.yaml` |
| GT-002 | 正向 | 查询 OA 流程状态 | Capability Registry 查询与 Mock OA 调用 | `tests/golden_tasks/fixtures/GT-002.yaml` |
| GT-003 | 正向 | 查询 U8 单据状态 | 多账套 scope 明确时的 Mock U8 查询链路 | `tests/golden_tasks/fixtures/GT-003.yaml` |
| GT-004 | 正向 | 查询 U8 供应商余额摘要 | system_scope 只读查询与 Policy allow | `tests/golden_tasks/fixtures/GT-004.yaml` |
| GT-005 | 正向 | 查询海康设备在线状态 | Mock iVMS 查询，不引入视频流 | `tests/golden_tasks/fixtures/GT-005.yaml` |
| GT-006 | 正向 | 高风险提交前返回确认卡 | Policy confirm → SDUI confirm card，Adapter 不调用 | `tests/golden_tasks/fixtures/GT-006.yaml` |
| GT-007 | 正向 | 用户一次性确认后继续执行 Mock 动作 | user_action confirm 回到 Runtime / Gateway | `tests/golden_tasks/fixtures/GT-007.yaml` |
| GT-008 | 负向 | 用户请求未注册 Capability | no_capability_found 终态与 Trace 记录 | `tests/golden_tasks/fixtures/GT-008.yaml` |
| GT-009 | 负向 | Policy Guard 判定无权限 | deny 决策、Task failed、Trace 写入、Adapter 不调用 | `tests/golden_tasks/fixtures/GT-009.yaml` |
| GT-010 | 负向 | OA 未绑定用户尝试查询待办 | 阻止执行，返回绑定引导卡，Adapter 不调用 | `tests/golden_tasks/fixtures/GT-010.yaml` |
| GT-011 | 负向候补 | Mock Adapter 超时 | adapter_timeout、标准 error_code、Trace 记录、Runtime 标准失败 ResponseEnvelope | `tests/golden_tasks/fixtures/GT-011.yaml` |
| GT-012 | 负向 | U8 多 active 绑定但未指定 account_set_id | needs_binding_scope、clarify_scope、Adapter 不调用、不得 fallback 到第一个绑定或 system_scope | `tests/golden_tasks/fixtures/GT-012.yaml` |

Phase 0 必须实现 GT-001 至 GT-010 以及 GT-012。GT-011 可作为候补或扩展，但若选择替代其他任务，必须仍保证核心必选任务完整、正向任务通过率 >= 80%，且所有负向 / 边界 / 安全拒绝路径 100% 通过。

### 12.4 Golden Task 完整可执行 Fixture 定义

以下是 Phase 0 Golden Task 的最低固定样例。实现可以增加字段，但不得删除这些字段或改写枚举语义。

```yaml
GT-001:
  title: 查询 OA 待办流程
  category: positive
  given:
    ai_user_id: user_employee_001
    roles: [employee]
    identity_mappings:
      - target_system: oa
        binding_id: bind_oa_001
        status: active
        binding_scope: default
    registered_capabilities:
      - capability_id: oa.list_pending_workflows
        target_system: oa
        type: query
        risk_level: low
        execution_identity: user_delegated
        binding_required: true
        input_schema:
          type: object
          properties: {}
          additionalProperties: false
        output_schema:
          type: object
          required: [workflows]
          properties:
            workflows:
              type: array
        input_schema_digest: digest_oa_list_pending_input
        output_schema_digest: digest_oa_list_pending_output
    mock_oa_state:
      pending_workflows:
        - workflow_id: OA-WF-2026-0001
          title: 办公用品采购审批
          status: pending
          applicant: 张三
          current_step: pending
          approver: 李四
          created_at: '2026-05-07T09:30:00+09:00'
        - workflow_id: OA-WF-2026-0002
          title: 差旅报销审批
          status: pending
          applicant: 王五
          current_step: pending
          approver: 赵六
          created_at: '2026-05-06T11:00:00+09:00'
        - workflow_id: OA-WF-2026-0003
          title: 过期合同审批
          status: pending
          applicant: 陈七
          current_step: pending
          approver: 李四
          expired: true
  when:
    channel: web
    message: 查我的OA待办
  then_response:
    status: completed
    envelope:
      message_contains: ["OA", "待办"]
      ui.component_type: none
      data.workflows.length: 3
  then_trace:
    event_sequence: [task_created, intent_parsed, capability_selected, identity_check, policy_checked, gateway_pre_recorded, adapter_called, gateway_post_recorded, response_envelope_created, task_completed]
  then_forbidden: [trace_contains_token, response_contains_internal_url]
  mock_failure_injection:
    enabled: true
    endpoint: POST /mock/oa.list_pending_workflows/inject
    payload:
      error_mode: timeout
      duration: next_1_call
      error_detail: {status_code: 504, body: "upstream timeout"}
    expected_error_code: adapter_timeout
  adapter_assertion:
    must_be_called: true

GT-002:
  title: 查询 OA 流程状态
  category: positive
  given:
    ai_user_id: user_employee_001
    roles: [employee]
    identity_mappings:
      - target_system: oa
        binding_id: bind_oa_001
        status: active
        binding_scope: default
    registered_capabilities:
      - capability_id: oa.get_workflow_status
        target_system: oa
        type: query
        risk_level: low
        execution_identity: user_delegated
        binding_required: true
        input_schema:
          type: object
          required: [workflow_id]
          properties:
            workflow_id: {type: string}
          additionalProperties: false
        output_schema:
          type: object
          required: [workflow_id, current_step, approver]
          properties:
            workflow_id: {type: string}
            current_step: {type: string, enum: [draft, pending, approved, rejected]}
            approver: {type: string}
        input_schema_digest: digest_oa_status_input
        output_schema_digest: digest_oa_status_output
    mock_oa_state:
      workflow_status:
        workflow_id: OA-WF-2026-0001
        current_step: approved
        approver: 李四
        updated_at: '2026-05-07T14:00:00+09:00'
  when:
    message: 查询 OA-WF-2026-0001 的流程状态
    arguments: {workflow_id: OA-WF-2026-0001}
  then_response:
    status: completed
    envelope:
      message_contains: ["OA-WF-2026-0001", "approved"]
      ui.component_type: none
  then_trace:
    event_sequence: [task_created, capability_selected, identity_check, policy_checked, gateway_pre_recorded, adapter_called, gateway_post_recorded, response_envelope_created]
  then_forbidden: [trace_contains_token]
  mock_failure_injection:
    enabled: true
    endpoint: POST /mock/oa.get_workflow_status/inject
    payload:
      error_mode: missing_required_field
      duration: next_1_call
      error_detail: {missing_field: current_step}
    expected_error_code: adapter_missing_required_field
  adapter_assertion:
    must_be_called: true

GT-003:
  title: 查询 U8 单据状态
  category: positive
  given:
    ai_user_id: user_finance_001
    roles: [finance_user]
    identity_mappings:
      - target_system: u8
        binding_id: bind_u8_001
        status: active
        account_set_id: acctset_hunan_01
        binding_scope: acctset_hunan_01
      - target_system: u8
        binding_id: bind_u8_002
        status: active
        account_set_id: acctset_factory_02
        binding_scope: acctset_factory_02
    registered_capabilities:
      - capability_id: u8.get_document_status
        target_system: u8
        type: query
        risk_level: medium
        execution_identity: user_delegated
        binding_required: true
        input_schema:
          type: object
          required: [account_set_id, document_no]
          properties:
            account_set_id: {type: string}
            document_no: {type: string}
          additionalProperties: false
        output_schema:
          type: object
          required: [document_status]
          properties:
            document_status: {type: string}
            amount: {type: number}
            currency: {type: string}
        input_schema_digest: digest_u8_document_input
        output_schema_digest: digest_u8_document_output
    mock_u8_state:
      document:
        account_set_id: acctset_hunan_01
        document_no: U8-AP-2026-0033
        document_status: posted
        amount: 12800.50
        currency: CNY
  when:
    message: 查湖南账套 U8-AP-2026-0033 单据状态
    arguments: {account_set_id: acctset_hunan_01, document_no: U8-AP-2026-0033}
  then_response:
    status: completed
    envelope:
      message_contains: ["U8-AP-2026-0033", "posted"]
      ui.component_type: none
  then_trace:
    event_sequence: [task_created, capability_selected, identity_check, policy_checked, gateway_pre_recorded, adapter_called, gateway_post_recorded, response_envelope_created]
  then_forbidden: [trace_contains_token]
  mock_failure_injection:
    enabled: true
    endpoint: POST /mock/u8.get_document_status/inject
    payload:
      error_mode: malformed_json
      duration: next_1_call
      error_detail: {body: "{bad-json"}
    expected_error_code: adapter_payload_invalid
  adapter_assertion:
    must_be_called: true

GT-004:
  title: 查询 U8 供应商余额摘要
  category: positive
  given:
    ai_user_id: user_finance_001
    roles: [finance_viewer]
    identity_mappings: []
    registered_capabilities:
      - capability_id: u8.get_vendor_balance_summary
        target_system: u8
        type: query
        risk_level: medium
        execution_identity: system_scope
        binding_required: false
        input_schema:
          type: object
          required: [account_set_id, vendor_id]
          properties:
            account_set_id: {type: string}
            vendor_id: {type: string}
          additionalProperties: false
        output_schema:
          type: object
          required: [vendor_id, balance, currency]
          properties:
            vendor_id: {type: string}
            balance: {type: number}
            currency: {type: string}
        input_schema_digest: digest_u8_vendor_balance_input
        output_schema_digest: digest_u8_vendor_balance_output
    mock_u8_state:
      vendor_balance:
        account_set_id: acctset_hunan_01
        vendor_id: VENDOR-0007
        vendor_name: 湖南示例供应商有限公司
        balance: 320000.00
        currency: CNY
  when:
    message: 查湖南账套 VENDOR-0007 的供应商余额
    arguments: {account_set_id: acctset_hunan_01, vendor_id: VENDOR-0007}
  then_response:
    status: completed
    envelope:
      message_contains: ["供应商", "余额"]
      ui.component_type: none
  then_trace:
    event_sequence: [task_created, capability_selected, policy_checked, gateway_pre_recorded, adapter_called, gateway_post_recorded, response_envelope_created]
  then_forbidden: [trace_contains_token]
  mock_failure_injection:
    enabled: true
    endpoint: POST /mock/u8.get_vendor_balance_summary/inject
    payload:
      error_mode: permission_denied
      duration: next_1_call
      error_detail: {status_code: 403, body: "permission denied"}
    expected_error_code: upstream_permission_denied
  adapter_assertion:
    must_be_called: true

GT-005:
  title: 查询海康设备在线状态
  category: positive
  given:
    ai_user_id: user_security_001
    roles: [security_operator]
    identity_mappings:
      - target_system: hikvision_ivms
        binding_id: bind_ivms_001
        status: active
        device_domain_id: prison_area_a
        binding_scope: prison_area_a
      - target_system: hikvision_ivms
        binding_id: bind_ivms_002
        status: revoked
        device_domain_id: prison_area_b
        binding_scope: prison_area_b
    registered_capabilities:
      - capability_id: ivms.get_device_online_status
        target_system: hikvision_ivms
        type: query
        risk_level: medium
        execution_identity: user_delegated
        binding_required: true
        input_schema:
          type: object
          required: [device_domain_id, device_id]
          properties:
            device_domain_id: {type: string}
            device_id: {type: string}
          additionalProperties: false
        output_schema:
          type: object
          required: [device_id, online]
          properties:
            device_id: {type: string}
            online: {type: boolean}
            video_frame_included: {type: boolean}
        input_schema_digest: digest_ivms_online_input
        output_schema_digest: digest_ivms_online_output
    mock_ivms_state:
      device:
        device_domain_id: prison_area_a
        device_id: CAM-A-001
        online: true
        last_seen_at: '2026-05-08T08:30:00+09:00'
        video_frame_included: false
  when:
    message: 查 A 区 CAM-A-001 是否在线
    arguments: {device_domain_id: prison_area_a, device_id: CAM-A-001}
  then_response:
    status: completed
    envelope:
      message_contains: ["CAM-A-001", "在线"]
      ui.component_type: none
  then_trace:
    event_sequence: [task_created, capability_selected, identity_check, policy_checked, gateway_pre_recorded, adapter_called, gateway_post_recorded, response_envelope_created]
  then_forbidden: [video_frame_payload, trace_contains_token]
  mock_failure_injection:
    enabled: true
    endpoint: POST /mock/ivms.get_device_online_status/inject
    payload:
      error_mode: timeout
      duration: next_1_call
      error_detail: {status_code: 504, body: "device platform timeout"}
    expected_error_code: adapter_timeout
  adapter_assertion:
    must_be_called: true

GT-006:
  title: 高风险提交前返回确认卡
  category: positive
  given:
    ai_user_id: user_employee_001
    roles: [employee]
    identity_mappings:
      - target_system: oa
        binding_id: bind_oa_001
        status: active
        binding_scope: default
    registered_capabilities:
      - capability_id: oa.submit_leave_request
        target_system: oa
        type: action
        risk_level: high
        execution_identity: user_delegated
        binding_required: true
    policy_fixture:
      decision: confirm
      reason_code: high_risk_action_requires_confirm
  when:
    message: 帮我提交明天请假的 OA 流程
  then_response:
    status: waiting_user
    envelope:
      message_contains: ["确认", "提交"]
      ui.component_type: confirm_card
      ui.action: confirm
      ui.target_system: oa
  then_trace:
    event_sequence: [task_created, capability_selected, identity_check, policy_checked, confirm_required, response_envelope_created]
  then_forbidden: [mock_oa_was_called, trace_contains_token]
  mock_failure_injection:
    enabled: false
    mode: none
    expected_error_code: confirm_required
  adapter_assertion:
    must_not_be_called: true

GT-007:
  title: 用户一次性确认后继续执行 Mock 动作
  category: positive
  given:
    ai_user_id: user_employee_001
    roles: [employee]
    prior_response_id: resp_gt_006
    user_action:
      action_type: confirm
      response_id: resp_gt_006
      confirmed: true
    identity_mappings:
      - target_system: oa
        binding_id: bind_oa_001
        status: active
        binding_scope: default
    registered_capabilities:
      - capability_id: oa.submit_leave_request.confirmed_mock
        target_system: oa
        type: action
        risk_level: high
        execution_identity: user_delegated
        binding_required: true
    mock_oa_state:
      submit_result:
        draft_id: OA-DRAFT-2026-0009
        submit_status: mock_submitted
        workflow_id: OA-WF-2026-0099
  when:
    message: 用户确认 resp_gt_006
  then_response:
    status: completed
    envelope:
      message_contains: ["已提交", "OA-DRAFT-2026-0009"]
      ui.component_type: none
  then_trace:
    event_sequence: [task_created, policy_checked, gateway_pre_recorded, adapter_called, gateway_post_recorded, response_envelope_created, task_completed]
  then_forbidden: [multi_turn_confirm_loop, trace_contains_token]
  mock_failure_injection:
    enabled: true
    endpoint: POST /mock/oa.submit_leave_request.confirmed_mock/inject
    payload:
      error_mode: timeout
      duration: next_1_call
      error_detail: {status_code: 504, body: "submit timeout"}
    expected_error_code: adapter_timeout
  adapter_assertion:
    must_be_called: true

GT-008:
  title: 用户请求未注册 Capability
  category: negative
  given:
    ai_user_id: user_employee_001
    roles: [employee]
    identity_mappings: []
    registered_capabilities: []
    mock_oa_state: should_not_be_called
  when:
    message: 帮我查询一个还没有接入的系统功能
  then_response:
    status: no_capability_found
    envelope:
      message_contains: ["暂未接入", "能力"]
      ui.component_type: operator_handback_card
      ui.action: none
  then_trace:
    event_sequence: [task_created, intent_parsed, no_capability_found, response_envelope_created]
  then_forbidden: [mock_adapter_was_called, response_data_not_null, trace_contains_token]
  mock_failure_injection:
    enabled: false
    mode: none
    expected_error_code: capability_not_found
  adapter_assertion:
    must_not_be_called: true

GT-009:
  title: Policy Guard 判定无权限
  category: negative
  given:
    ai_user_id: user_employee_001
    roles: [employee]
    identity_mappings:
      - target_system: u8
        binding_id: bind_u8_employee_001
        status: active
        account_set_id: acctset_hunan_01
        binding_scope: acctset_hunan_01
    registered_capabilities:
      - capability_id: u8.get_salary_summary
        target_system: u8
        type: query
        risk_level: high
        execution_identity: user_delegated
        binding_required: true
    policy_fixture:
      decision: deny
      reason_code: role_not_allowed
    mock_u8_state: should_not_be_called
  when:
    message: 查一下湖南账套员工薪资汇总
    arguments: {account_set_id: acctset_hunan_01}
  then_response:
    status: blocked
    envelope:
      message_contains: ["无权限", "拒绝"]
      ui.component_type: operator_handback_card
      ui.action: none
  then_trace:
    event_sequence: [task_created, capability_selected, identity_check, policy_checked, blocked_by_policy, response_envelope_created, task_failed]
  then_forbidden: [mock_u8_was_called, response_data_not_null, trace_contains_token]
  mock_failure_injection:
    enabled: false
    mode: none
    expected_error_code: policy_denied
  adapter_assertion:
    must_not_be_called: true

GT-010:
  title: OA 未绑定用户尝试查询待办
  category: negative
  given:
    ai_user_id: user_unbound
    roles: [employee]
    identity_mappings: []
    registered_capabilities:
      - capability_id: oa.list_pending_workflows
        target_system: oa
        type: query
        risk_level: low
        execution_identity: user_delegated
        binding_required: true
        input_schema:
          type: object
          properties: {}
          additionalProperties: false
        output_schema:
          type: object
          required: [workflows]
          properties:
            workflows:
              type: array
        input_schema_digest: digest_oa_list_pending_input
        output_schema_digest: digest_oa_list_pending_output
    mock_oa_state: should_not_be_called
  when:
    channel: web
    message: 查我的OA待办
    arguments: {}
  then_response:
    status: blocked
    envelope:
      message_contains: ["绑定"]
      ui.component_type: operator_handback_card
      ui.action: bind_required
      ui.target_system: oa
      data: null
  then_trace:
    event_sequence: [task_created, intent_parsed, capability_selected, identity_check, blocked_by_identity, response_envelope_created]
    bind_status: unbound
    reason: unbound
  then_forbidden: [mock_oa_was_called, response_data_not_null, trace_contains_token, response_contains_internal_url]
  mock_failure_injection:
    enabled: false
    mode: none
    expected_error_code: identity_unbound
  adapter_assertion:
    must_not_be_called: true

GT-012:
  title: U8 多账套绑定但未指定 account_set_id
  category: negative
  given:
    ai_user_id: user_finance_001
    roles: [finance_user]
    identity_mappings:
      - target_system: u8
        binding_id: bind_u8_001
        status: active
        account_set_id: acctset_hunan_01
        binding_scope: acctset_hunan_01
      - target_system: u8
        binding_id: bind_u8_002
        status: active
        account_set_id: acctset_factory_02
        binding_scope: acctset_factory_02
    registered_capabilities:
      - capability_id: u8.get_document_status
        target_system: u8
        type: query
        risk_level: medium
        execution_identity: user_delegated
        binding_required: true
        input_schema:
          type: object
          required: [account_set_id, document_no]
          properties:
            account_set_id: {type: string}
            document_no: {type: string}
          additionalProperties: false
        output_schema:
          type: object
          required: [document_status]
          properties:
            document_status: {type: string}
        input_schema_digest: digest_u8_document_input
        output_schema_digest: digest_u8_document_output
    mock_u8_state: should_not_be_called
  when:
    message: 查一下 U8-AP-2026-0033 单据状态
    arguments: {document_no: U8-AP-2026-0033}
  then_response:
    status: blocked
    envelope:
      message_contains: ["账套", "选择"]
      ui.component_type: operator_handback_card
      ui.action: clarify_scope
      ui.target_system: u8
      data: null
  then_trace:
    event_sequence: [task_created, intent_parsed, capability_selected, identity_check, blocked_by_identity, response_envelope_created]
    reason: needs_binding_scope
  then_forbidden: [mock_u8_was_called, fallback_to_first_binding, fallback_to_system_scope, response_data_not_null, trace_contains_token]
  mock_failure_injection:
    enabled: false
    mode: none
    expected_error_code: needs_binding_scope
  adapter_assertion:
    must_not_be_called: true
```

### 12.4.1 短路终态 Trace 事件矩阵

以下矩阵用于统一 Golden Task 与 Gateway / Runtime 的短路路径断言。具体 fixture 可增加事件，但不得删除 must-have 事件；must-not-have 事件一旦出现即判定该 Golden Task 失败。

| 终态 | must-have events | must-not-have events |
|---|---|---|
| `no_capability_found` | `task_created`, `intent_parsed`, `no_capability_found`, `response_envelope_created` | `identity_check`, `policy_checked`, `adapter_called`, `gateway_pre_recorded`, `gateway_post_recorded` |
| `policy_denied` | `task_created`, `capability_selected`, `identity_check`, `policy_checked`, `blocked_by_policy`, `response_envelope_created` | `adapter_called`, `gateway_post_recorded`, `task_completed` |
| `identity_unbound` | `task_created`, `intent_parsed`, `capability_selected`, `identity_check`, `blocked_by_identity`, `response_envelope_created` | `policy_checked` unless policy-before-identity is explicitly documented, `adapter_called`, `fallback_to_system_scope` |
| `needs_binding_scope` | `task_created`, `intent_parsed`, `capability_selected`, `identity_check`, `blocked_by_identity`, `response_envelope_created` | `adapter_called`, `fallback_to_first_binding`, `fallback_to_system_scope` |
| `confirm_required` | `task_created`, `capability_selected`, `identity_check`, `policy_checked`, `confirm_required`, `response_envelope_created` | `adapter_called`, `gateway_post_recorded`, `task_completed` |
| `adapter_timeout` | `task_created`, `capability_selected`, `identity_check`, `policy_checked`, `gateway_pre_recorded`, `adapter_called`, `gateway_post_recorded`, `adapter_error_mapped`, `response_envelope_created` | `task_completed` |

`user_delegated` capability 在未绑定、绑定过期、绑定撤销或绑定 scope 不明确时，不得 fallback 到 `system_scope`。该规则必须同时由 IdentityMapping 单元测试、Gateway 短路测试和 GT-012 验证。

### 12.5 负向路径最低要求

核心 Golden Task 必须包含以下负向 / 边界类型，并全部 100% 通过：

1. 用户请求一个没有注册 Capability 的功能 → 验证 `no_capability_found` 终态、`capability_not_found` error_code、Trace 记录和 Adapter 未调用。
2. Policy Guard 判定当前用户无权限 → 验证 deny 决策、Task 进入 failed / blocked、Trace 写入和 Adapter 未调用。
3. IdentityMapping 未绑定 → 验证执行被阻止并返回 SDUI 绑定引导卡，Adapter 未调用。
4. 多 active 绑定但 scope 不明确 → 验证返回 `clarify_scope`，Adapter 未调用，且不得 fallback 到第一个绑定或 system_scope。
5. Mock Adapter 超时 → 验证失败恢复路径、`adapter_timeout`、Trace 记录和 Runtime 标准失败 ResponseEnvelope。

### 12.6 Mock Adapter 错误注入统一要求

OA / U8 / 海康 iVMS 三个 Mock Adapter 必须统一支持以下错误模式：

```yaml
supported_error_modes:
  - timeout
  - permission_denied
  - malformed_json
  - empty_response
  - http_500
  - missing_required_field
```

Mock 错误注入不得随机触发，必须通过临时管理端点设置：

```http
POST /mock/{capability_id}/inject
```

请求体：

```json
{
  "error_mode": "malformed_json",
  "duration": "next_3_calls",
  "error_detail": {
    "status_code": 502,
    "body": "upstream error"
  }
}
```

要求：

- `error_mode` 枚举：`timeout`、`permission_denied`、`malformed_json`、`empty_response`、`http_500`、`missing_required_field`。
- `duration` 枚举：`next_1_call`、`next_3_calls`、`permanent`。
- Mock Adapter 可以模拟畸形上游 payload，但对 Gateway 输出必须仍符合 AdapterResult 标准结果。
- Gateway 必须把错误映射为稳定 error_code，并写入 Trace。
- Runtime 不得根据 Mock Adapter 的内部实现细节分支处理。
- 每个负向 Golden Task 必须明确 `adapter_assertion.must_not_be_called` 或通过注入端点明确触发异常。

### 12.7 SDUI 确认流边界

Phase 0 允许验证一次性 confirm user_action 回传：

```text
confirm card → 用户确认 → Runtime 接收 confirm action → Gateway 执行 Mock Capability
```

Phase 0 禁止实现多轮确认回路、动态追问、嵌套确认、确认状态机编排和动态表单编排器。

---


### 12.8 Golden Task 执行任务定义

以下三个任务必须在 Batch 7 前存在完整定义，避免 Codex / Claude Code 到验收阶段因 `task_definition_incomplete` 卡死。

#### P0-GT-001 — Golden Task Fixture Materialization

```yaml
task_id: P0-GT-001
title: Golden Task Fixture Materialization
type: test
priority: P0
depends_on:
  - P0-DOMAIN-007b
  - P0-DOMAIN-008e
objective: 将第 12.4 节的 10 个 Golden Task 固化为可执行 YAML fixture，并实现测试级 mock state loader，使正向状态和负向错误注入都可重复。
deliverable:
  - tests/golden_tasks/fixtures/GT-001.yaml
  - tests/golden_tasks/fixtures/GT-002.yaml
  - tests/golden_tasks/fixtures/GT-003.yaml
  - tests/golden_tasks/fixtures/GT-004.yaml
  - tests/golden_tasks/fixtures/GT-005.yaml
  - tests/golden_tasks/fixtures/GT-006.yaml
  - tests/golden_tasks/fixtures/GT-007.yaml
  - tests/golden_tasks/fixtures/GT-008.yaml
  - tests/golden_tasks/fixtures/GT-009.yaml
  - tests/golden_tasks/fixtures/GT-010.yaml
  - tests/golden_tasks/fixtures/GT-012.yaml
  - tests/golden_tasks/conftest.py
constraints:
  - Fixture 字段必须使用第 12.2 节给定格式
  - 正向 mock state 只能通过 pytest fixture loader 注入 MockAdapter.set_state()
  - 错误注入只能通过测试环境下的 POST /mock/{capability_id}/inject 或 MockErrorInjector 测试夹具触发
  - 每个 test function 必须清空数据库状态、mock state 和 error injection state
acceptance_criteria:
  - 10 个 GT fixture 均可被 YAML parser 加载
  - 每个 fixture 都包含 given、input、expected_response、expected_trace、forbidden、adapter_assertion
  - 至少 3 个 fixture 包含负向路径，且其中至少 1 个验证 Adapter must_not_be_called
  - tests/golden_tasks/conftest.py 支持 function-scope transaction rollback 或独立 test schema
  - mock_oa_state / mock_u8_state / mock_ivms_state 可注入并在测试结束后重置
happy_path_acceptance:
  - GT-001/002/003/004/005 的正向 mock state 可稳定加载，重复运行结果一致
  - GT-012 固化多 active 绑定但 scope 未指定的负向 fixture，且断言 Adapter 不调用
failure_examples:
  - name: fixture_missing_then_forbidden
    trigger: fixture 缺少 forbidden 区域
    expected_result: fixture validation failed
    forbidden_shortcut: 禁止测试 runner 自动补默认 forbidden
  - name: mock_state_leakage
    trigger: 连续运行两个使用同一 ai_user_id 但不同 identity 状态的 GT
    expected_result: 第二个 GT 不受第一个 GT 的 mock state 污染
    forbidden_shortcut: 禁止全局单例状态不清理
  - name: adapter_called_when_forbidden
    trigger: fixture 声明 adapter_assertion.must_not_be_called=true
    expected_result: 测试断言 Adapter 调用计数为 0
    forbidden_shortcut: 禁止忽略 adapter_assertion
step_verification_points:
  - step: materialize_fixtures
    verification: uv run pytest tests/golden_tasks/test_fixture_schema.py
  - step: mock_state_loader
    verification: uv run pytest tests/golden_tasks/test_mock_state_loader.py
final_test_commands:
  - uv run pytest tests/golden_tasks/test_fixture_schema.py tests/golden_tasks/test_mock_state_loader.py
touched_paths:
  - tests/golden_tasks/
forbidden_paths:
  - app/runtime/
  - app/gateway/
  - app/execution_fabric/real_adapters/
```

#### P0-GT-002 — Golden Task Test Runner

```yaml
task_id: P0-GT-002
title: Golden Task Test Runner
type: test
priority: P0
depends_on:
  - P0-GT-001
  - P0-INFRA-007
objective: 实现 Golden Task runner，把 fixture 的 given/when/then/forbidden 转换为可重复 pytest 断言，并接入 staged CI。
deliverable:
  - tests/golden_tasks/test_golden_tasks.py
  - tests/golden_tasks/assertions.py
  - scripts/run_golden_tasks.py
constraints:
  - 不得修改 Golden Task fixture 以适配实现；必须修实现或提交 task patch
  - 不得将未实现模块的检查伪造为 passed
  - P0-INFRA-007 中的 Golden Task CI 检查在本任务完成后才可由 not_applicable 激活
acceptance_criteria:
  - runner 可逐个执行 GT-001 到 GT-010 以及 GT-012
  - runner 可断言 expected_response.status、message_contains、ui.component_type、ui.action、error_code
  - runner 可断言 Trace 事件序列与关键字段
  - runner 可断言短路终态 Trace 事件矩阵中的 must-have / must-not-have events
  - runner 可断言 forbidden 中的 token、内部 URL、mock adapter called 等禁止项
  - runner 输出机器可读 summary：passed、failed、skipped、not_applicable
happy_path_acceptance:
  - 在完整 Mock 环境中，runner 至少能执行一个正向 GT 和一个负向 GT
failure_examples:
  - name: test_rewrite_detected
    trigger: 测试文件出现 assert True、空 pass 或无断言 test function
    expected_result: CI / checklist 失败
    forbidden_shortcut: 禁止把失败用例改成恒真断言
  - name: forbidden_trace_token
    trigger: Trace 输出含 Bearer token 或 sessionid
    expected_result: runner 标记该 GT failed
    forbidden_shortcut: 禁止仅检查字段名不检查值模式
  - name: expected_adapter_not_called_but_called
    trigger: adapter_assertion.must_not_be_called=true 但调用计数 > 0
    expected_result: runner 标记该 GT failed
    forbidden_shortcut: 禁止忽略调用计数
step_verification_points:
  - step: runner_schema_assertions
    verification: uv run pytest tests/golden_tasks/test_runner_assertions.py
  - step: runner_negative_assertions
    verification: uv run pytest tests/golden_tasks/test_runner_negative_assertions.py
  - step: ci_activation_patch
    verification: |
      Golden Task runner 建立后，CI 中 Golden Task 相关检查不得继续保持 not_applicable。
      必须提供本地等价命令和证据，例如：
      grep -En "(golden_task|Golden Task|run_golden_tasks).*not_applicable" .github/workflows/ci.yml && exit 1 || true
      uv run python scripts/run_golden_tasks.py --summary
      若 CI 使用显式状态变量，必须断言 GOLDEN_TASK_CI_STATUS=active。
final_test_commands:
  - uv run pytest tests/golden_tasks/
  - uv run python scripts/run_golden_tasks.py --summary
touched_paths:
  - tests/golden_tasks/
  - scripts/run_golden_tasks.py
  - .github/workflows/
forbidden_paths:
  - app/runtime/
  - app/gateway/
  - app/execution_fabric/real_adapters/
```

#### P0-GT-003 — Phase 0 Acceptance Run

```yaml
task_id: P0-GT-003
title: Phase 0 Acceptance Run
type: review
priority: P0
depends_on:
  - P0-GT-002
  - P0-SPIKE-001
  - P0-SPIKE-002
  - P0-SPIKE-003
  - P0-SPIKE-004
  - P0-SPIKE-005
  - P0-SPIKE-006
  - P0-SPIKE-007
  - P0-INFRA-007
objective: 执行 Phase 0 最终验收，汇总 Spike ADR、工程基线、边界检查、Golden Task 通过率和未关闭风险。
deliverable:
  - docs/phase0/phase0_acceptance_report.md
constraints:
  - 不修改生产代码
  - 不降低验收标准
  - 不把 failed / not_applicable 写成 passed
  - P0-INFRA-005 若因 P0-SPIKE-004 failed 未执行，报告中必须说明替代方案或阻塞影响
acceptance_criteria:
  - 所有 Spike ADR 均存在并给出 passed / failed / conditionally_passed
  - Golden Task 核心必选任务包含 GT-001 至 GT-010 以及 GT-012；正向任务总体通过率 >= 80%；负向路径、边界路径和安全拒绝路径 100% 通过
  - import boundary check 通过或在未到激活阶段时有完整 not_applicable 说明
  - Trace sanitizer 测试通过或在 TracePort 未激活阶段有完整 not_applicable 说明
  - 依赖白名单和 Alembic autogenerate 检查通过
  - 输出是否允许开始编写 Phase 1 spec 的明确结论
happy_path_acceptance:
  - phase0_acceptance_report.md 可以作为 Phase 1 spec 启动输入
failure_examples:
  - name: positive_threshold_not_met
    trigger: positive Golden Task pass rate < 80% 或 GT-012 未通过
    expected_result: 结论为 Phase 1 spec 不得启动
    forbidden_shortcut: 禁止把 skipped 计为 passed
  - name: negative_boundary_failed
    trigger: 任一负向路径 / 边界路径 / 安全拒绝路径失败
    expected_result: 结论为 Phase 1 spec 不得启动
    forbidden_shortcut: 禁止只因总通过率 >=80% 就放行
  - name: missing_adr
    trigger: 任一必需 Spike ADR 缺失
    expected_result: 验收失败并列出缺失 ADR
    forbidden_shortcut: 禁止用口头结论替代 ADR
  - name: boundary_check_failed
    trigger: Runtime 直接 import Adapter 或 Trace 泄露 token
    expected_result: 验收失败
    forbidden_shortcut: 禁止仅在报告中标注风险后继续通过
step_verification_points:
  - step: run_all_checks
    verification: uv run pytest && pnpm --dir web lint && pnpm --dir web build
  - step: run_golden_tasks
    verification: uv run python scripts/run_golden_tasks.py --summary
  - step: write_acceptance_report
    verification: phase0_acceptance_report.md 必须引用 Task Record、ADR、CI/self-check、Golden Task summary 证据；human review optional
final_test_commands:
  - uv run pytest
  - uv run python scripts/run_golden_tasks.py --summary
touched_paths:
  - docs/phase0/phase0_acceptance_report.md
forbidden_paths:
  - app/
  - web/
  - docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md
```

## 13. Codex / Claude Code Development Boundary

### 13.1 总原则

Codex / Claude Code 只能按任务书执行，不得自行扩展架构。

每次任务必须包含：

- task_id
- title
- context
- objective
- constraints
- acceptance_criteria
- touched_paths
- forbidden_paths
- test commands
- expected deliverables

### 13.2 Codex 适合承担的任务

Codex 适合：

- 按明确 spec 生成代码。
- 建立项目骨架。
- 实现 CRUD。
- 编写 Alembic 迁移。
- 编写单元测试。
- 补齐类型检查。
- 修复 lint / mypy / pytest。
- 按 touched_paths 修改局部模块。
- 打包当前仓库状态。

### 13.3 Claude Code 适合承担的任务

Claude Code 适合：

- 多文件重构。
- 边界 review。
- 复杂测试补齐。
- 任务执行过程审计。
- 根据失败日志定位问题。
- 协助生成 ADR、开发日志和回归报告。

### 13.4 禁止行为

Codex / Claude Code 禁止：

1. 修改冻结主蓝图。
2. 在 Phase 0 spec 中扩写 Phase 1 完整功能矩阵。
3. 绕过 Capability Gateway 调用 Adapter。
4. 在 Runtime 中 import `app/execution_fabric/*`。
5. 在 Workflow / Skill / Admin Console 中直接调用生产系统。
6. 将 Spike 代码复制进 `app/` 正式模块。
7. 将 PydanticAI、LangGraph、Temporal、Celery、Milvus 等候选技术变成 Phase 0 / Phase 1 强依赖。
8. 将真实凭证写入代码、日志、Trace、测试 fixture、README 或 `.env.example`。
9. 在无测试情况下宣称任务完成。
10. 修改 forbidden_paths。
11. 自行创建新的顶层架构目录。
12. 将 Mock Adapter 当作真实 Adapter 设计。
13. 用 system_scope 替代未绑定 user_delegated 执行。

### 13.5 每次任务完成必须输出

每个 Codex / Claude Code 任务完成后必须输出：

```text
1. Completed task_id
2. Files changed
3. Tests added or updated
4. Commands run
5. Test results
6. Boundary checks
7. Known limitations
8. Follow-up tasks, if any
9. confirmation that a fresh package was created from the current repository state
```

### 13.6 每 3 个 Task 的边界自检要求

Codex / Claude Code 每完成 3 个 Phase 0 task，必须执行一次 boundary self-check。

自检内容：

1. 重读本文件第 3 节架构底线与 Spike 规则。
2. 执行 import boundary check。
3. 检查是否新增未授权依赖或异常 lockfile 变更。
4. 检查是否修改 forbidden_paths。
5. 检查 Runtime / Workflow / Skill / Admin Console 是否绕过 Capability Gateway。
6. 检查 Spike 代码是否进入 `app/` 正式目录。
7. 输出 self-check log，记录命令、结果和发现的问题。

若 self-check 失败，必须先修复边界问题，再继续后续任务。

### 13.7 依赖与前端 API Client 约束

- 后端新增依赖必须符合第 3.6 节内网依赖与依赖白名单规则。
- 前端连接后端 OpenAPI 的 API client 必须通过 Orval 或等价 OpenAPI codegen 生成。
- 禁止在业务页面中手写 fetch / axios 调用后端业务 API。
- 健康页静态 mock、纯 fixture 展示、未连接后端的 SDUI 样例可以不走 Orval。
- 如果 Orval 本身不在内网 npm 镜像中，应记录为依赖缺口，不得私自引入其他未批准 codegen 包。

### 13.8 Import Boundary Review 要求

Phase 0 必须建立最小 import 边界检查。

最低要求：

- `app/runtime/` 不得 import `app/execution_fabric/`。
- `app/workflow/` 不得 import `app/execution_fabric/`。
- `app/skill/` 不得 import `app/execution_fabric/`。
- `app/admin_console/` 不得 import `app/execution_fabric/real_adapters/`。
- LLM / structured output 模块不得 import Adapter。
- Adapter 不得反向依赖 Runtime。

可选实现方式：

- 自定义 pytest import 检查。
- import-linter。
- grimp。
- Ruff 规则配合简单脚本。

具体工具不在本文件强制锁死，但 Phase 0 验收必须能证明边界未被破坏。

---

## 14. Phase 0 Acceptance Criteria

Phase 0 全部验收标准如下。

### 14.1 Spike 验收

- P0-SPIKE-001 Qwen 结构化输出 ADR 完成。
- P0-SPIKE-002 instructor + vLLM ADR 完成。
- P0-SPIKE-003 PostgreSQL 18 + pgvector >= 0.8.2 ADR 完成。
- P0-SPIKE-004 Redis + ARQ ADR 完成。
- P0-SPIKE-005 OA / U8 / 海康 API 与认证 ADR 各完成一份。
- P0-SPIKE-006 S3-compatible 对象存储 ADR 完成。
- P0-SPIKE-007 PydanticAI Spike ADR 完成，明确给出 Phase 2 是否引入的建议：通过 / 不通过 / 条件通过。
- 每个 Spike ADR 包含 `spike_code_disposition` 字段。
- Spike 临时代码完成处置，无 Spike 代码进入 `app/` 正式模块。

### 14.2 工程基线验收

- Docker Compose 一键启动成功。
- Python uv + FastAPI 健康检查通过。
- React 18 + Vite + Ant Design 5.x 前端骨架可构建。
- PostgreSQL schema + Alembic 迁移可升级和回滚。
- PostgreSQL + pgvector >= 0.8.2 验证通过。
- Redis + ARQ Spike ADR 完成；若采用 ARQ，则轻量基线验证通过，不要求所有本地开发场景常驻运行。
- OpenTelemetry + Langfuse 基线部署完成，并在 Golden Task 前可查看链路。
- CI 跑通 lint + type check + test。
- CI 自动执行 import boundary check。
- CI 自动执行依赖白名单 / lockfile 检查。

### 14.3 Domain Foundation 验收

- Task / Session Port 与骨架完成。
- Capability Registry Port 与最小 CRUD 完成。
- Capability Gateway Port 与骨架完成。
- Policy Guard Port 与最小 deny 骨架完成。
- Trace Port 与最小写入骨架完成。
- IdentityMapping Port 与 Mock 表完成，且覆盖同一用户多 U8 账套 / 多海康设备域样例。
- Runtime Port 与主链骨架完成。
- AdapterPort 与 Mock OA / Mock U8 / Mock 海康 iVMS Adapter 完成，且三个 Mock Adapter 均支持统一错误注入模式。
- SDUI Response Envelope 最小实现完成。
- SecretProviderPort 契约与 Noop SecretProvider 骨架完成。
- Mock Adapter 错误注入管理端点完成。
- 所有核心 Port 方法签名与第 8.6 节一致。
- Mock Structured Output 完成，Runtime 测试可不依赖真实 LLM。

### 14.4 安全与边界验收

- Capability Gateway 是唯一执行入口。
- Runtime 层无直接 import Adapter / execution_fabric 的引用。
- Gateway 调用前执行 Policy Guard。
- Policy deny 时 Adapter 不被调用。
- binding_required 且 IdentityMapping 未绑定时 Adapter 不被调用。
- user_delegated 未绑定不得 fallback 到 system_scope。
- Trace 不记录凭证、token、cookie、session token；伪造 Bearer token / sessionid / access_token / refresh_token / cookie 写入测试必须触发 sanitizer 并失败。
- SDUI schema 不包含凭证、token、cookie、内部接口地址。
- Spike 代码未进入 `app/` 正式模块。
- 依赖白名单检查通过，无未授权外部依赖。
- 前端业务 API client 通过 Orval 或等价 OpenAPI codegen 生成，业务页面无手写 fetch / axios。

### 14.5 Golden Task 验收

- Golden Task 总数 >= 10。
- 负向 Golden Task 数量 >= 2。
- Mock 环境中正向任务总体通过率 >= 80%。
- 负向路径、边界路径和安全拒绝路径 100% 通过。
- 每个 Golden Task 均有固定 Mock fixture，包含 given / when / then_response / then_trace / then_forbidden / adapter_assertion；会调用 Adapter 的任务必须有错误注入模式，不调用 Adapter 的负向任务必须声明 mode=none 与 must_not_be_called。
- `no_capability_found` 路径可验证。
- Policy Guard deny 路径可验证。
- IdentityMapping 未绑定路径可验证，并返回 SDUI 绑定引导卡。
- 至少一个任务可在 Trace 中看到 Runtime → Gateway → Policy → Adapter → Response 的关键链路。
- 多身份 IdentityMapping 用例通过：同一用户多个 U8 账套或多个海康设备域不发生默认误选。
- Mock Adapter 错误注入路径至少覆盖权限拒绝、超时、畸形 payload、空响应、HTTP 500、缺失字段中的 3 类，并通过 `POST /mock/{capability_id}/inject` 明确触发。

### 14.6 Phase 1 spec 启动验收

Phase 1 spec 启动前提，以下全部满足：

- 模型结构化输出成功率 >= 80%，基于不少于 50 条固定测试样例，且覆盖 Intent、CapabilityRef、PlanDraft、ResponseEnvelope 四类 schema。
- OA / U8 / 海康 API 类型和认证方式各完成一份 ADR。
- Golden Task 总数不少于 10 个；正向任务总体通过率 >= 80%；负向路径、边界路径和安全拒绝路径 100% 通过；通过判定必须包含 ResponseEnvelope 字段断言、Trace 事件序列断言和 forbidden 内容断言。
- PostgreSQL + pgvector >= 0.8.2 部署验证通过；Redis + ARQ Spike ADR 完成，若结论为 passed / partially_passed 则完成轻量基线，若失败则必须通过 JobQueuePort 给出替代候选 ADR 后才能编写 Phase 1 spec。
- OpenTelemetry + Langfuse 可观测链路可用。
- Capability Gateway import 边界通过自动化 import boundary check；人工 review 仅作为 optional 复核手段。
- PydanticAI Spike ADR 完成。
- Phase 0 验收报告完成。

---

## 15. Follow-up Specs to Split Later

Phase 0 完成或关键 Spike 结论出来后，再滚动拆分以下专项 spec。

### 15.1 Phase 1 spec

文件建议：

```text
phase1_mvp_execution_spec.md
```

触发条件：满足第 14.6 节全部条件。

内容范围：

- Phase 1 功能矩阵。
- Phase 1 开发顺序。
- Phase 1 真实 / Mock 系统边界。
- Phase 1 验收标准。
- Phase 1 Codex / Claude Code 任务拆解。

### 15.2 Capability / Policy / Gateway 专项 spec

文件建议：

```text
capability_policy_gateway_spec.md
```

内容范围：

- CapabilitySpec 完整字段。
- GatewayPort。
- PolicyDecision。
- Preselector。
- Capability Summary Cache。
- Gateway import 边界。
- static tool chain 与 dynamic composition 边界。

### 15.3 Security / Identity Binding / Credential 专项 spec

文件建议：

```text
security_identity_binding_and_credential_spec.md
```

内容范围：

- IdentityMapping 字段。
- bind_mode / execution_identity。
- OAuth2 / Vault / vendor token / system credential。
- SecretProviderPort。
- Trace 脱敏。
- 审计事件。
- 账号绑定状态机。

### 15.4 Runtime State Machine 专项 spec

文件建议：

```text
runtime_state_machine_spec.md
```

内容范围：

- Task 状态机。
- 等待态。
- 失败原因。
- 取消原因。
- 恢复规则。
- 超时策略。

### 15.5 SDUI Protocol 专项 spec

文件建议：

```text
sdui_protocol_spec.md
```

内容范围：

- Response Envelope 完整 schema。
- SDUI 组件协议。
- operator_handback_card。
- confirmation_card。
- binding_required_card。
- action 回传协议。

### 15.6 Workflow Runtime 专项 spec

文件建议：

```text
workflow_runtime_spec.md
```

内容范围：

- WorkflowSpec YAML / JSON Schema。
- Published Skill。
- static tool chain。
- 版本、审批、回滚、审计。

### 15.7 Phase 0 Technical Spike Plan

文件建议：

```text
phase0_technical_spike_plan.md
```

内容范围：

- Spike 执行顺序。
- 测试样例。
- ADR 模板。
- 实验目录约束。
- Spike 代码处置流程。

### 15.8 Codex / Claude Code Development Plan

文件建议：

```text
codex_phase1_development_plan.md
```

内容范围：

- 正式开发任务拆解。
- touched_paths / forbidden_paths。
- 测试要求。
- 回归要求。
- 开发日志要求。
- 打包要求。
- `confirmation that a fresh package was created from the current repository state`。

---


## 16. Codex Single Task Prompt Template Requirement

Phase 0 执行包必须包含 `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md`。每次调用 Codex / Claude Code 执行任务时，必须使用该模板包装具体 task，而不是只说“按执行包执行”。

模板的强制要求：

1. 每次只执行一个 `task_id`。
2. 行动前必须先输出 Plan，并等待人工确认。
3. Plan 必须逐条引用任务定义中的 `acceptance_criteria`、`touched_paths`、`forbidden_paths`，并按任务类型引用对应验证字段。
4. `implementation` / `test` 任务缺少 `failure_examples` 或 `step_verification_points` 时，必须停止并报告 `task_definition_incomplete`。
5. `interface_contract` 任务使用 `contract_violation_examples` 与接口验收点，不要求 implementation 级 `failure_examples`；若缺少契约违规样例或接口验收点，必须停止并报告 `task_definition_incomplete`。
6. Spike / preparation 任务使用 `blocking_examples` 与 ADR 验收点替代，不因此停止。
7. 每一步完成后必须输出可测试结果，包括已运行命令、通过/失败结果和下一步。
8. 模板必须包含敏感词白名单说明：字段名、接口契约和 sanitizer 测试输入允许出现 token/password/cookie/sessionid 等字样；Trace 持久化输出、ResponseEnvelope、task log 主体和 fixture expected output 禁止出现敏感值。
9. 完成后必须输出 Unified Task Record，并包含 `confirmation that a fresh package was created from the current repository state`。

该模板是流程包装器，不能替代本 spec 中的任务内容。任务定义才是验收标准、失败样例和测试命令的来源。


## 16.1 Codex / Claude Code 使用技巧补充

### 16.1.1 Codex 使用注意事项

- 在 Codex / IDE / CLI 中优先使用 `AGENTS.md` 固化项目约束；一次 prompt 只给一个 `task_id` 和该任务的精简上下文。
- 不要把 3000+ 行 spec 每次整份塞给 Codex；应使用 `docs/phase0/tasks/` 中的 per-task prompt，或人工摘录：全局硬约束 + 第 8.6 节核心 Schema + 当前任务 YAML + depends_on 接口摘要。
- 网络访问默认按最小权限处理。内网依赖未确认前，不要让 Codex 自行联网搜索和安装依赖。
- 让 Codex 先输出 Plan，再人工确认；Plan 未列出失败样例、步骤验证点、测试命令和 forbidden_paths 时不要允许执行。
- Codex 完成后先看 diff，不要只看总结；重点检查测试是否被弱化、forbidden_paths 是否被修改、是否新增未授权依赖。
- 每个任务尽量单独分支或单独提交；失败任务直接回滚，不要在同一上下文里连续补救过多轮。

### 16.1.2 Claude Code 使用注意事项

- 项目根必须放置 `CLAUDE.md`，用于跨 session 保持 Phase 0 边界。
- 可选启用 `.claude/agents/boundary-checker.md` 作为专用边界审查 subagent；它只做检查，不写生产代码。
- 可选使用 `.claude/settings.example.json` 中的 hooks 示例，但 hooks 会自动执行本机命令，启用前必须人工审查命令内容和权限。
- Claude Code 长会话容易产生上下文漂移；每完成 1 个 task 建议 `/clear` 或新 session，并重新贴入单任务 prompt。

### 16.1.3 AGENTS.md / CLAUDE.md 最小约束

- `AGENTS.md` 面向 Codex 和多数 coding agents。
- `CLAUDE.md` 面向 Claude Code project memory。
- 两者内容应短小、硬约束优先，不要复制整份 spec。
- 真实验收仍以 `docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md` 和 `docs/phase0/TASK_INDEX.md` 为准。

## 17. Final Self-check Checklist

编写和执行 Phase 0 时，每次变更都应检查以下问题：

- 是否修改了冻结主蓝图？如果是，停止。
- 是否引入了新大架构？如果是，停止。
- 是否提前展开 Phase 1 功能矩阵？如果是，移出本文件。
- 是否存在 Spike 代码进入 `app/` 正式模块？如果是，删除或移入 `tests/utils/`。
- 是否存在 Runtime 直接 import Adapter？如果是，修复。
- 是否存在 Policy Guard 可以被绕过？如果是，修复。
- 是否存在 Identity 未绑定但 fallback 到 system_scope？如果是，修复。
- 是否存在凭证进入 LLM / Prompt / Memory / Trace / Log？是否有 sanitizer 正则测试覆盖 Bearer token / sessionid / access_token / refresh_token / cookie？如果没有，修复。
- 是否存在任务有验收标准但没有实现任务？如果是，补任务。
- 是否存在实现任务没有接口契约？如果是，先补 ABC / Protocol。
- 是否存在 Golden Task 全是正向路径？如果是，补负向路径。
- 是否每个 Golden Task 都有固定 Mock fixture、字段值、枚举值、错误注入、Trace 事件序列和 forbidden 断言？如果没有，补齐。
- 是否存在 OpenTelemetry + Langfuse 未完成就开始 Golden Task？如果是，暂停 Golden Task。
- 是否存在新增依赖未说明内网镜像可用性？如果有，停止合并。
- 是否存在 Runtime 直接 import Adapter 的代码？如果有，停止合并。
- 是否存在前端业务页面手写 fetch / axios 调用后端 API？如果有，改为 OpenAPI codegen。
- 是否存在 Codex / Claude Code 修改 forbidden_paths？如果是，回滚并拆新任务。
- 是否存在测试文件被改成 `assert True`、空 `pass`、无断言 test function 或恒真断言？如果是，回滚并修实现。
- 是否存在 Alembic autogenerate 注释 `auto generated by Alembic`？如果是，重写为手写迁移。
- Golden Task 正向 mock state 是否通过 `tests/golden_tasks/conftest.py` function-scope fixture 注入并清理？如果不是，修复。
- 是否存在 Batch 1 因 GPU 不可用而阻塞所有非 GPU Spike？如果是，先执行 requires_gpu=no 的 Spike。

---

## 18. Freeze Statement for Phase 0 Execution

主蓝图已冻结。Phase 0 的任务是从冻结蓝图中裁剪可执行范围，而不是继续设计大架构。

本文件冻结以下 Phase 0 执行原则：

```text
先 Spike，后 spec；
先接口契约，后骨架实现；
先 Mock 闭环，后真实接入；
先边界检查，后功能扩展；
先 Trace 可见，后 Golden Task 验证；
先 Phase 0 验收，后 Phase 1 spec。
```



---

## 19. Phase 0 Execution Package Reference

Phase 0 v1.0.11 冻结后，Codex / Claude Code 不应直接凭本文件自由发挥，而应通过精简执行包逐批执行。执行包核心入口文件包括以下 6 个；其他 `docs/dev/`、per-task prompts 和可选 Claude Code 增强文件按需加载：

1. `docs/phase0/README_FOR_CODEX.md`
2. `docs/phase0/TASK_INDEX.md`
3. `docs/phase0/FIRST_BATCH_TASKS.md`
4. `docs/phase0/ADR_TEMPLATE.md`
5. `docs/phase0/BOUNDARY_CHECKLIST.md`
6. `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md`

执行规则：

- `TASK_INDEX.md` 是任务依赖 DAG 与批次顺序的唯一入口。
- `FIRST_BATCH_TASKS.md` 只包含 Batch 0 与 Batch 1，不允许一次性执行后续批次；每次只能执行一个 `task_id`，完成并输出 Unified Task Record 后等待人工确认。
- `CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` 是单任务执行包装器；每次执行前必须先输出 Plan，引用验收标准、失败样例和步骤验证点，等待人工确认后再改文件。
- 每完成 3 个 task，必须按 `BOUNDARY_CHECKLIST.md` 执行一次边界自检。
- 单个任务执行时只摘录当前 task 相关章节和全局硬边界；不得每次把整份 spec 全量塞给 Codex / Claude Code，避免上下文漂移。
- Spike 任务只产出 ADR 与可废弃实验代码，不得把 Spike 代码复制到 `app/` 正式目录。
- 每次任务完成必须提供开发日志、测试结果、变更清单、边界检查结果、以及固定确认语：`confirmation that a fresh package was created from the current repository state`。


---

## 20. v1.0.11 Standardization, Observability, and Review Patch

本节是 v1.0.11 对 v1.0.11 的执行治理补丁，只约束 Phase 0 的开发过程，不修改冻结主蓝图，也不引入新的运行时架构。

### 20.1 已在 v1.0.11 解决的反馈项

以下 v1.0.2 反馈在 v1.0.11 中已经解决，本版只保留和强化：

- 项目根 `AGENTS.md` 已存在，用于 Codex / 通用 coding agent 的项目级约束。
- 项目根 `CLAUDE.md` 已存在，用于 Claude Code project memory。
- `P0-GT-001 / P0-GT-002 / P0-GT-003` 已补齐任务定义。
- Golden Task 正向 mock state 采用 `tests/golden_tasks/conftest.py` function-scope fixture loader 注入。
- Batch 1 已区分 `requires_gpu`，GPU 不可用时可先执行非 GPU Spike。

### 20.2 Codex / Claude Code 双轨配置

Phase 0 必须同时支持 Codex 与 Claude Code，但两者配置入口不同：

- Codex / 通用 coding agents：项目根 `AGENTS.md`。
- Claude Code：项目根 `CLAUDE.md`，并可选使用 `.claude/agents/` 与 `.claude/settings.example.json`。
- 两者都必须使用 `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` 作为单任务执行包装器。

Codex 执行前必须确认：

1. 当前目录为仓库根目录。
2. `AGENTS.md` 存在并指向 v1.0.11 spec。
3. 工作区 `git status` 干净。
4. 当前分支为 `phase0/<task_id>` 或人工批准的任务分支。
5. 不使用危险 bypass 模式。
6. 网络默认关闭；如需联网查文档或依赖镜像，必须人工批准。

### 20.3 Codex / Claude Code 开发过程可观测性

Phase 0 区分两类可观测：

| 层级 | 覆盖对象 | 强制方式 |
|---|---|---|
| L1 应用运行时 | 用户请求、Gateway、Adapter、Trace | OpenTelemetry + Langfuse |
| L2 AI 开发过程 | Codex / Claude Code 改了什么、测了什么、为什么通过 | Git + CI + Task Record + Self-check YAML |

L2 不得只依赖 AI 自报。所有 `passed` 必须引用外部证据：

- Git commit SHA；
- CI run id / job name；
- 命令输出片段；
- 日志文件路径；
- Task Record 路径；
- Self-check YAML 路径。

`P0-PREP-001` 必须检查当前 Codex 安装是否支持 OTel / JSONL run log / 等价 telemetry。如果支持，可接入本地 OTel Collector；如果不支持，不阻塞 Phase 0，但必须记录替代审计链：Git + CI + Task Record。

### 20.4 Package 定义

Phase 0 中的 `package` 定义为：

> 当前 task 完成后，从当前仓库状态创建的、基于当前仓库状态生成或更新的任务交付产物。

不是 Docker image，不是随手 zip，不是无审查的工作区快照。

Commit message 格式：

```text
phase0(<task_id>): <one-line summary>
```

任务完成确认语仍固定为：

```text
confirmation that a fresh package was created from the current repository state
```

该确认语表示当前任务已经基于当前仓库状态重新生成、整理或更新了交付产物；它不再绑定强制人工 diff review。

### 20.5 统一 Task Record

废弃多套 Task Log / Unified Task Record 格式，统一使用：

```text
docs/dev/task_record_schema.yaml
```

任务记录保存路径：

```text
docs/phase0/task_logs/<task_id>_<YYYYMMDD_HHMMSS>_<passed|failed>.yaml
```

人工维护索引：

```text
docs/phase0/task_logs/INDEX.md
```

Task Record 必须包含：

- `task_id`
- `task_type`
- `result`
- `plan_approved_by`
- `plan_approved_at`
- `acceptance_criteria_result`
- `failure_examples_tested`
- `step_verification_points`
- `changed_files`
- `tests_run`
- `import_boundary_check.evidence`
- `security_scan_result.evidence`
- `weak_test_scan_result.evidence`
- `ci_evidence`
- `review`
- `git_commit_sha`
- `package_confirmation`

### 20.6 Human Review Checklist

Plan approval 只能证明“意图被批准”，不能证明“结果正确”。每个 task 完成后建议进行人工复核；human diff review 为 optional，不作为 v1.0.11 阻断条件。

人工审查清单：

```text
docs/dev/human_review_checklist.md
```

至少确认：

- `git diff` 已逐文件审阅；
- 没有修改 forbidden_paths；
- 没有削弱测试；
- 没有新增未声明依赖；
- 没有敏感值进入 Trace / ResponseEnvelope / task log；
- commit message 符合 `phase0(<task_id>): <summary>`。

### 20.7 Git workflow

Git 是 Phase 0 开发过程的主要可审查记录。

规则：

- 每个 task 使用独立分支：`phase0/<task_id>`。
- 任务开始前 `git status` 必须干净。
- 任务失败不得 commit 失败状态。
- 任务通过后创建一个 task commit。
- merge 前必须有人审查 diff，并确认 Task Record 与 CI evidence。

完整规则见：

```text
docs/dev/git_workflow.md
```

### 20.8 Self-check YAML

每 3 个 task 的 boundary self-check 不再使用散文式 Markdown，必须使用 YAML：

```text
docs/phase0/self_checks/check_after_<task_id>_<YYYYMMDD_HHMMSS>.yaml
```

YAML 必须能被脚本聚合，至少包含：

- `tasks_covered`
- `checks.import_boundary`
- `checks.secret_scan`
- `checks.forbidden_paths`
- `checks.dependency_allowlist`
- `checks.weak_test_scan`
- `checks.alembic_autogenerate`
- `checks.golden_task_regression`
- `violations`
- `git_sha_at_check`

### 20.9 AI 改弱测试防护

CI / self-check 必须检查测试是否被削弱。至少扫描：

- `assert True`
- 空 `pass`
- 无断言 test function
- 无理由 `skip`
- 恒真断言

任何命中必须人工审查。若属于合法测试样例，必须在 Task Record 中说明。

### 20.10 失败处理与回滚协议

任务失败时必须按顺序处理：

1. 停止修改文件。
2. 不 commit 失败状态。
3. 如需保留现场，先问人工；需要时执行 `git stash push -u -m "FAILED <task_id> <timestamp>"`。
4. 写入 `docs/phase0/task_logs/<task_id>_<YYYYMMDD_HHMMSS>_failed.yaml`。
5. 记录失败的 acceptance criteria、命令输出、日志路径和已修改文件。
6. 等待人工决定：patch 补修、重开 task branch、还是写 ADR / task patch。
7. 不得跳过失败任务继续后续 task。
8. 不得用 `not_applicable` 掩盖真实失败。

### 20.11 v1.0.4 执行包新增开发治理文件

v1.0.4 执行包曾在 v1.0.3 基础上新增以下开发治理文件，v1.0.11 继续保留：

```text
docs/dev/codex_setup.md
docs/dev/git_workflow.md
docs/dev/package_definition.md
docs/dev/task_record_schema.yaml
docs/dev/human_review_checklist.md
```

这些文件约束开发过程，不改变应用运行时架构。


---

## v1.0.11 Patch — Progressive and On-demand Context Loading

### Purpose

This patch changes the Phase 0 execution pack from an “eager-load everything” style to a progressive, on-demand context strategy for Codex and Claude Code. The goal is to keep root-level agent instruction files short, stable, and always loaded, while moving detailed rules into task-specific prompts and stage-specific reference files that are read only when needed.

### Non-goals

- This patch does not modify the frozen enterprise blueprint.
- This patch does not change Phase 0 task scope.
- This patch does not add Phase 1 functionality.
- This patch does not require agents to load the full Phase 0 spec for every task.

### Context tiers

| Tier | Files | Loading behavior | Purpose |
|---|---|---|---|
| Tier 0 | `AGENTS.md`, `CLAUDE.md` | Auto-loaded / session-start files | Only hard rules, phase, source-of-truth pointers, and single-task discipline |
| Tier 1 | `docs/phase0/tasks/<task_id>.md` | Loaded for exactly one task | Task-specific scope, acceptance criteria, blocking/failure examples, verification points, touched_paths, forbidden_paths |
| Tier 2 | `docs/phase0/CONTEXT_LOADING_STRATEGY.md`, `docs/phase0/TASK_INDEX.md`, `docs/phase0/BOUNDARY_CHECKLIST.md`, `docs/dev/*.md` | Loaded on demand | Task DAG, boundary checks, Git/package/task-record/review rules |
| Tier 3 | `docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md`, frozen blueprint, handoff | Consulted only for disputes, patching, or missing task context | Canonical long-form reference, not pasted into every task prompt |

### Rules

1. Root-level `AGENTS.md` and `CLAUDE.md` must stay concise. They must not import or inline the full Phase 0 spec.
2. Each Codex / Claude Code session should execute exactly one `task_id`. Use the matching `docs/phase0/tasks/<task_id>.md` file as the primary task context.
3. If a task prompt lacks enough detail, stop and request a task-prompt patch instead of reading unrelated sections or guessing.
4. Use the long Phase 0 spec only as the canonical reference when resolving contradictions or generating the next batch of per-task prompts.
5. Claude Code `@import` should not be used in root `CLAUDE.md` for long documents. Imports may be used later only for short task/phase files and only when a human explicitly asks.
6. Do not create additional nested `AGENTS.md` or `CLAUDE.md` files unless a specific task patch requests directory-scoped rules.
7. Prefer explicit subagents and hooks for checks only as optional enhancements; they do not replace Task Records, CI/self-check evidence, ADR, tests, or optional human review.

### Required execution-pack changes

- Add `docs/phase0/CONTEXT_LOADING_STRATEGY.md`.
- Rewrite root `AGENTS.md` to be a compact Tier 0 file.
- Rewrite root `CLAUDE.md` to be a compact Claude-specific Tier 0 file and avoid `@import` of long documents.
- Update `README_FOR_CODEX.md` and `CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to instruct agents to read only the current per-task prompt plus the minimum required reference files.
- Update `MANIFEST.md` to list the context strategy file and identify root files as compact boot files, not full context carriers.


---

## 20. v1.0.11 Execution Consistency Addendum

本节覆盖 v1.0.11 中与执行一致性冲突的旧表述；若本节与前文存在不一致，以本节为准。本期不新增“AI 编码安全基线”，不把人工 diff review 作为强制项。

### 20.1 Golden Task 通过标准

Phase 0 v1.0.11 的 Golden Task 验收标准如下：

- Golden Task 总数不少于 10 个。
- 正向任务按总体通过率计算，必须 >= 80%。
- 负向路径、边界路径、安全拒绝路径必须 100% 通过。
- 下列场景任一失败，均阻断 Phase 0 验收：
  1. `no_capability_found`
  2. `policy_deny`
  3. `identity_unbound` / `binding_required`
  4. disabled capability
  5. timeout
  6. mock adapter error injection
  7. 禁止绕过 Capability Gateway 的路径
  8. `user_delegated` 未绑定时不得 fallback 到 `system_scope`

### 20.2 `not_applicable` 使用规则

`not_applicable` 只能用于当前阶段确实尚未具备执行条件的检查，不得用于隐藏失败测试、失败边界检查或失败验收项。

每个 `not_applicable` 必须记录：

```yaml
not_applicable_reason: ""
not_applicable_scope: "current_phase_only | waiting_dependency | intentionally_deferred"
blocked_by_task_id: ""
activation_task_id: ""
expiry_condition: ""
evidence: ""
```

缺少 `reason`、`blocked_by_task_id` 或 `expiry_condition` 的 Task Record 无效。若某检查项在后续任务完成后应被激活，必须写明 `activation_task_id`。

### 20.3 Package confirmation 语义

固定确认语保留：

```text
confirmation that a fresh package was created from the current repository state
```

在 v1.0.11 中，它表示“已经基于当前仓库状态重新生成、整理或更新了本 task 的交付包 / 任务产物”，不再绑定强制人工 diff review。

Task Record 必须使用：

```yaml
package_confirmation_status: "created | not_created | not_applicable"
package_confirmation: "confirmation that a fresh package was created from the current repository state"
package_scope: ""
package_evidence: ""
```

纯文档审查、只读检查或 Spike 调研无法生成代码包时，可标记 `not_applicable`，但必须写明原因。

### 20.4 Review 字段

`human_diff_review` 不再是强制字段。Task Record 使用：

```yaml
review:
  mode: "none | self_check | human_optional"
  reviewed_by: ""
  reviewed_at: ""
  notes: ""
```

Phase 0 v1.0.11 默认允许 `mode: self_check`。`human_optional` 表示进行了人工检查，但不是阻断条件。不得因为未填写人工 diff review 而判定任务失败。

### 20.5 P0-PREP 任务定位

`P0-PREP-*` 是 execution-pack-only preparation tasks，不属于业务实现任务。它们不产生架构能力，不实现 Runtime / Gateway / Adapter / Golden Task，只用于仓库检查、文档放置、模板完整性检查和执行前一致性检查。`P0-PREP-*` 的完整可执行定义不内嵌在主 spec 的 Phase 0 研发任务表中，而位于执行包 `docs/phase0/tasks/P0-PREP-001.md`、`docs/phase0/tasks/P0-PREP-002.md`、`docs/phase0/tasks/P0-PREP-003.md`。主 spec 只声明其门禁地位；实际执行以 per-task prompt 为准。`P0-PREP-*` 完成后，才允许进入 `P0-SPIKE-*`；不得跳过 `P0-PREP` 直接进入实现任务。

### 20.6 P0-SPIKE-005 子 ADR 编号

`P0-SPIKE-005` 是唯一任务 ID。`ADR-P0-SPIKE-005a-oa-api-auth.md`、`ADR-P0-SPIKE-005b-u8-auth-session.md`、`ADR-P0-SPIKE-005c-hik-api-device-boundary.md` 是该任务下的 ADR 交付物文件名，不是独立任务 ID。Task Record 中 `task_id` 必须统一写 `P0-SPIKE-005`。

### 20.7 Qwen / vLLM / Structured Output Spike 证据

`P0-SPIKE-001`、`P0-SPIKE-002`、`P0-SPIKE-007` 的 ADR 必须记录：

- vLLM version
- model name
- model digest / model path
- structured output mode
- tool choice mode
- schema used
- success sample count
- failure sample count
- malformed output sample
- retry strategy
- fallback strategy
- whether result is suitable for Phase 1
- whether result only supports mock usage
- known limitations

`named` / `required` tool choice 的结果可以作为 Phase 1 参考依据。`auto` tool calling 只能作为探索项，不能单独作为 Phase 1 放行依据。若结构化输出在关键字段、枚举、嵌套对象上不稳定，ADR 必须标记为 `blocking` 或 `caution`。

### 20.8 OA / U8 / Hik 调研 ADR 字段

`P0-SPIKE-005` 的三个 ADR 必须包含：

1. system_name
2. version_or_assumed_version
3. auth_mode
4. token/session lifecycle
5. user identity mapping method
6. permission source of truth
7. read API availability
8. write API availability
9. callback/webhook availability
10. rate limit / concurrency limit
11. audit log availability
12. sandbox/test environment availability
13. irreversible operation list
14. Phase 0 allowed operations
15. Phase 0 forbidden operations
16. open questions
17. blocking risks
18. recommendation: `mock_only | can_build_adapter_later | needs_vendor_confirmation | not_suitable`

如果没有厂商文档或测试环境，必须明确写 `needs_vendor_confirmation`。不得因为网上零散资料假设可直接接入。Phase 0 不得做真实生产写入，Controlled Exploration 不得作用于真实 OA / U8 / Hik 系统。

### 20.9 自动化边界验证优先

Capability Gateway 的隔离边界必须通过自动化 import boundary 检查验证。任务自检日志必须记录相关检查结果。人工 review 可作为 optional 复核手段，但不是 v1.0.11 阻断条件。若 import boundary 检查尚未激活，必须标记 `not_applicable` 并写明 `activation_task_id`。

### 20.10 Hooks / Subagent 定位

Claude Code hooks / subagent 是 optional enhancement。Phase 0 不依赖 hooks 才能完成。所有关键验收以 Task Record、CI/self-check 命令、ADR、测试日志为准。若启用 hooks，只能作为辅助提醒或本地自动检查，不替代任务验收。本期不要求新增 AI 编码安全基线，也不强制把 hooks 改成 blocking。

### 20.11 FIRST_BATCH 执行边界

`FIRST_BATCH_TASKS.md` 只允许执行 `P0-PREP-*` 和 `P0-SPIKE-*`。不得提前创建工程骨架，不得提前实现 Gateway / Runtime / Adapter / Golden Task runner。Spike 代码必须位于 `experiments/phase0/`、`docs/adr/`、`docs/research/` 或临时实验目录，不得进入 `app/` 正式路径。

Spike 完成后必须说明 `spike_code_disposition`：

```yaml
spike_code_disposition: discard | keep_as_reference | promote_after_rewrite | blocked
```

### 20.12 Phase 0 v1.0.11 通过条件

1. `P0-PREP-*` 全部完成。
2. `P0-SPIKE-*` 全部产出 ADR 或明确 `blocking` 结论。
3. 所有 Task Record 符合 schema。
4. 所有 `not_applicable` 都有原因、阻塞任务和激活条件。
5. Golden Task 正向任务通过率 >= 80%。
6. Golden Task 负向路径 / 边界路径 100% 通过。
7. Capability Gateway import boundary 相关检查在对应任务阶段被激活；未到阶段时必须有明确 `not_applicable` 说明。
8. Spike 代码不得进入 `app/` 正式路径，除非经过后续正式任务重写。
9. OA / U8 / Hik 不得进行真实生产写入。
10. Controlled Exploration 不得作用于真实 OA / U8 / Hik 系统。
11. 固定 package confirmation 可以保留，但不与人工 diff review 绑定。
12. 人工 review / human diff review 是 optional，不是本期阻断条件。
13. 本期不新增 AI 编码安全基线，不扩展 MCP / 依赖治理 / 外部工具安全规则。


## 22. v1.0.11 Task Granularity and Verification Consistency Patch

本节只修复任务拆分颗粒度、约束一致性和验收可执行性，不新增业务范围。

### 22.1 Interface Contract Task Rule

`interface_contract` 任务不强制使用 `failure_examples`，但必须具备 `contract_violation_examples` 或继承第 8.4.1 节的全局契约违规样例。Codex / Claude Code 执行接口契约任务时，应验证方法签名、返回模型、抽象边界和禁止具体实现逻辑；不得因为缺少 implementation 级失败样例而停止。

### 22.2 Gateway Task Split

Gateway 完整骨架被拆为两个可单独验收的任务：

- `P0-DOMAIN-003b1`：短路决策骨架，验证 no_capability_found、policy_denied、identity_unbound、confirm_required 等 Adapter must_not_be_called 路径。
- `P0-DOMAIN-003b2`：Adapter 执行骨架，验证 Mock Adapter 正常调用、错误注入映射和 Trace pre/post。

Runtime 主链依赖 `P0-DOMAIN-003b2`，不得依赖旧的单体 `P0-DOMAIN-003b`。

### 22.3 Golden Task Final Acceptance

`P0-GT-003` 必须依赖所有 `P0-SPIKE-*`、`P0-INFRA-007` 和 `P0-GT-002`。正向 Golden Task 通过率必须 >= 80%，负向路径 / 边界路径 / 安全拒绝路径必须 100% 通过。

### 22.4 Optional Human Review Consistency

Human Review Checklist 是 optional / recommended，不是 v1.0.11 阻断条件。任务完成前必须有 self-check、Task Record 和可引用证据；人工 review 可后补。

### 22.5 YAML Task Block Validity

所有标记为 `yaml` 的任务块必须尽量保持机器可解析。含反引号、冒号、管道、正则表达式或 shell 片段的验收项应加引号，或改成 `text` 代码块。示例任务必须使用 `P0-EXAMPLE-*`，不得使用真实 task_id。


---

## 22. v1.0.11 Consistency Cleanup Patch

本节仅清理上一版遗留的文字一致性和机器抽取歧义，不改变任务范围、不新增架构、不修改 Phase 1 启动策略。

### 22.1 Automated Boundary Evidence Is Primary

涉及 Capability Gateway / Runtime / Adapter 隔离边界的验收，统一以自动化 import boundary 检查、自检日志和 Task Record 证据为准。人工 review 是 optional / recommended 复核手段，不是 v1.0.11 阻断条件。

### 22.2 Unified Task Record Naming

Phase 0 不再使用多套任务完成报告 / 任务日志命名。所有任务完成、失败和自检记录统一引用 `docs/dev/task_record_schema.yaml` 定义的 Unified Task Record。

### 22.3 Non-executable Examples

第 8 节中的 `EXAMPLE_TASK_ID`、`EXAMPLE_DOMAIN_CONTRACT_TASK`、`EXAMPLE_DOMAIN_IMPLEMENTATION_TASK` 和 `NON_EXECUTABLE_EXAMPLE_TASK` 均为非执行示例占位符。任何任务索引工具不得将其纳入可执行 DAG。

### 22.4 Golden Task Rule

Golden Task 只采用当前规则：正向任务通过率必须 >= 80%；所有负向路径、边界路径、安全拒绝路径必须 100% 通过。


## 23. v1.0.11 Context Reduction and Execution Pack Slimming Patch

本节是 v1.0.11 对执行包的减法补丁。它不改变 Phase 0 架构边界、任务范围、验收标准或冻结蓝图，只减少重复上下文，降低 VibeCoding 执行时的上下文污染风险。

### 23.1 Reduction principles

- `FIRST_BATCH_TASKS.md` 只作为 Batch 0 / Batch 1 的任务选择索引，不再内嵌完整任务 YAML。
- 单个 task 的执行上下文以 `docs/phase0/tasks/<task_id>.md` 为准。
- `README_FOR_CODEX.md` 只作为启动入口，不重复展开详细规则。
- `MANIFEST.md` 只保留当前版本文件清单和当前版本 patch note，不堆叠历史 patch notes。
- 不删除冻结蓝图、Phase 0 长 spec、per-task prompts、Task Record schema 或 Boundary Checklist。

### 23.2 Loading rule

执行单个任务时，不应同时粘贴长 spec、完整 `FIRST_BATCH_TASKS.md` 和当前 task prompt。推荐最小上下文为：

1. 根目录 `AGENTS.md` / `CLAUDE.md` 自动加载的短规则；
2. 当前 `docs/phase0/tasks/<task_id>.md`；
3. `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md`；
4. 如需要，再按需读取 `BOUNDARY_CHECKLIST.md`、`TASK_INDEX.md` 或 `docs/dev/*`。

### 23.3 Canonical task source

- 当前 task 的可执行定义以 per-task prompt 为直接执行源。
- 主 spec 是规范源和争议解决源，不应在日常单任务执行中全量进入上下文。
- `FIRST_BATCH_TASKS.md` 不是任务定义副本，只用于选择任务和确认 Batch 0 / Batch 1 的执行顺序。

## 24. v1.0.11 Execution-Readiness Patch

本节只修复执行前会导致 Codex / Claude Code 卡住或产生歧义的任务定义问题，不做全文 flatten，不新增架构范围，不改变冻结主蓝图。

### 24.1 P0-DOMAIN-001b 强制字段补齐

`P0-DOMAIN-001b` 是 `implementation` 类型任务，必须包含 `happy_path_acceptance`、`failure_examples`、`step_verification_points`、`final_test_commands` 和 `forbidden_shortcuts`。本版已补齐 Task / Session 状态转换、未知 task、缺失 session、孤立事件写入等失败样例，避免执行到 Domain 第一项时触发 `task_definition_incomplete`。

### 24.2 P0-PREP 任务归属说明

`P0-PREP-*` 是 execution-pack-only preparation tasks。完整任务定义位于执行包 `docs/phase0/tasks/`，不内嵌在主 spec 的研发任务表中。主 spec 中的 Phase 0 通过条件引用它们时，只表达执行门禁，不表示它们是业务实现任务。

### 24.3 Infrastructure 任务格式规则

`infrastructure` 任务不强制要求 implementation 级 `failure_examples`。它们必须通过 `blocking_examples`、`infrastructure_verification_points` 或从 `acceptance_criteria` 逐条转化出的命令 / 文件 / evidence 检查来证明。不得因为缺少 implementation 失败样例而直接拒绝执行 Infra 任务；但如果验收标准不可测试，必须报告 `task_definition_incomplete` 或 `task_prompt_incomplete`。

### 24.4 not_applicable 激活闭环

凡是某任务负责把早期 `not_applicable` 检查切换为 active，必须在 `step_verification_points` 中包含机器可执行或可审计的激活断言。`P0-GT-002` 必须证明 Golden Task CI 检查已从 not_applicable 激活，不能只实现 runner 脚本。

### 24.5 Capability Schema 存储和 digest 校验

`CapabilitySpec` 在 Phase 0 中必须保存完整 `input_schema` / `output_schema`，同时保存对应 digest。digest 只用于一致性校验和变更识别，不得替代完整 schema。Registry 必须校验 schema 与 digest 一致；Gateway 后续参数校验必须使用完整 schema。

### 24.6 risk_level 枚举澄清

Phase 0 `risk_level` 只允许 `low | medium | high`。`critical` 仅作为非法枚举的负向测试输入示例，不得加入 Phase 0 合法枚举。



## v1.0.11 执行一致性补丁

### A. Batch 2-7 per-task prompt 门禁

当前执行包只包含 Batch 0 / Batch 1 的 per-task prompt。Batch 1 完成后、Batch 2 启动前，必须根据本 spec 的任务定义生成 Batch 2-7 的 `docs/phase0/tasks/<task_id>.md` 文件。不得在缺少 per-task prompt 的情况下直接执行 Batch 2-7 任务。

生成后续 per-task prompt 时，必须把每个任务末尾统一加入：

```text
完成后请生成 Unified Task Record，并提示人工根据 Task Record 更新 docs/phase0/task_logs/INDEX.md。不得仅通过修改 TASK_INDEX.md 声明任务完成。
```

### B. Frontend OpenAPI / SDUI mock fixture 位置

Phase 0 前端 mock fixture 仅用于 OpenAPI client 生成验证和 SDUI 静态渲染验证，不得替代后端 Golden Task。推荐路径：

```text
web/tests/fixtures/openapi/
web/tests/fixtures/sdui/
```

若使用 MSW 或等价 mock 方案，必须只作为前端本地开发辅助，不得作为 Runtime / Gateway / Adapter 的验收依据。
