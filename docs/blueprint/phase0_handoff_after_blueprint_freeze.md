# Phase 0 Handoff — 企业智能助手蓝图冻结后交接

## 0. 当前状态

企业智能助手主蓝图已经冻结，冻结版本为：

- `enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md`
- 冻结前双轮审查报告：`v3_2_4_freeze_final_double_audit.md`

后续工作不要继续扩写主蓝图。主蓝图只负责定方向、定边界、定底线、定阶段路线。下一阶段应进入 Phase 0：MVP 范围冻结、接口契约、专项规格、技术 Spike、数据库迁移、验收标准、Codex / Claude Code 执行计划。

---

## 1. 冻结版蓝图的核心结论

### 1.1 系统定位

目标是建设企业智能助手的底层智能大脑，不是普通 Chatbot，也不是自由探索 Agent，更不是统一权限中心。

系统负责接收用户请求、理解意图、选择能力、通过受控 Gateway 执行业务能力、返回可审计结果，并逐步形成可治理、可演进的企业级 Agent Runtime。

### 1.2 架构主线

冻结版蓝图采用：

- 双中枢：Agent Runtime + Capability Gateway
- 三链路：稳定执行主链、受控进化副链、治理审计链
- 分层架构：入口层、接入网关、身份上下文、Runtime、Control Plane、Gateway、Execution Fabric、业务系统资源层

### 1.3 不可突破底线

后续所有 Phase 0 规格必须遵守：

1. Capability Gateway 是唯一真实执行入口。
2. Runtime / Workflow / Skill / Admin Console 不得直接调用 Adapter、SDK、DB Client、RPA Client。
3. 凭证、密码、token、cookie、session token 不得进入 LLM、Prompt、Memory、Trace 摘要或日志。
4. 凭证只能由 SecretProviderPort 在执行瞬间注入 Adapter。
5. user_delegated Capability 不得因为用户未绑定而 fallback 到 system_scope 服务账号。
6. Controlled Exploration 对 OA / U8 / 海康等封闭业务系统默认关闭。
7. 原业务系统的数据权限仍由原系统判断；AI 平台不做统一业务权限中心。
8. Trace / Audit 是事实账本，所有高风险动作必须可追溯。
9. 原始 IoT 高频信号、视频帧流、设备心跳流不得进入 LLM 上下文或主执行链。

---

## 2. 技术选型冻结结论

### 2.1 Phase 1 默认技术栈

Frontend:

- React 18
- TypeScript strict mode
- Vite
- React Router
- TanStack Query
- Zustand
- Ant Design 5.x
- ProComponents 2.x
- pnpm
- Orval
- MSW
- 基础 PWA 可通过 vite-plugin-pwa 早期启用，但不强制 Phase 1 做

Backend:

- Python 3.12
- FastAPI
- Pydantic v2
- SQLAlchemy 2.0
- Alembic
- asyncpg
- httpx
- uv
- Ruff
- mypy
- pytest + pytest-asyncio

LLM / Structured Output:

- OpenAI SDK
- instructor
- Pydantic v2 Schema
- LLMProviderPort / StructuredOutputPort 隔离具体实现
- PydanticAI 作为 Phase 0 验证项，不作为 Phase 1 强依赖

Async / Queue:

- 通过 JobQueuePort 隔离
- L0：FastAPI BackgroundTasks / in-process executor，适用于极小规模、可丢失、低风险任务
- L1：Redis + ARQ，适用于需要更可靠执行的导入、通知、健康检查、Trace 后处理、异步 Adapter 调用
- L2：Celery / Dramatiq / 企业消息队列 / Temporal，后续生产规模再评估
- 业务 Workflow 状态必须落 PostgreSQL，不得把队列作为业务状态源

Data:

- PostgreSQL
- pgvector >= 0.8.2
- Redis / Valkey
- S3-compatible Object Storage 抽象

Observability:

- OpenTelemetry
- Langfuse self-hosted
- 写入 Trace / Log 前必须经过 Redaction / Sanitizer

Auth:

- 优先接企业现有 IAM / AD / LDAP / SSO
- 无现成 IAM 时再评估 Keycloak

### 2.2 不作为 Phase 1 默认主线

- Next.js App Router
- React 19
- Ant Design 6
- PydanticAI
- LangGraph
- Temporal
- Celery
- Milvus / Weaviate / Qdrant
- Elasticsearch / OpenSearch
- 原生 iOS / Android App
- RPA / Local Worker 生产主链
- Controlled Exploration 生产执行
- SenseVoice-Small / faster-whisper / ASR 语音入口
- EMQX / MQTT / IoT 消息接入

这些不是否定，而是后续阶段候选或验证项。

---

## 3. Phase 0 应产出的专项文件

建议下一阶段按以下文件推进，不再往蓝图里塞实现细节：

1. `phase0_architecture_freeze_and_mvp_spec.md`
   - Phase 1 做什么 / 不做什么
   - MVP 裁剪清单
   - 模块优先级
   - 验收标准
   - Codex 开发边界

2. `capability_policy_gateway_spec.md`
   - CapabilitySpec
   - Gateway Port
   - Policy Decision
   - Capability Preselector
   - Capability Summary Cache
   - Gateway 模块隔离
   - static tool chain 与 dynamic composition 边界

3. `security_identity_binding_and_credential_spec.md`
   - IdentityMapping 字段
   - bind_mode / execution_identity
   - OAuth2 / Vault / vendor token / system credential
   - SecretProviderPort
   - Trace 脱敏
   - 审计事件
   - 账号绑定状态机

4. `runtime_state_machine_spec.md`
   - Task 状态机
   - 等待态
   - 失败原因
   - 取消原因
   - 恢复规则
   - 超时策略

5. `sdui_protocol_spec.md`
   - Response Envelope 完整 schema
   - SDUI 组件协议
   - operator_handback_card
   - confirmation_card
   - binding_required_card
   - action 回传协议

6. `workflow_runtime_spec.md`
   - WorkflowSpec YAML
   - Published Skill
   - static tool chain
   - 版本、审批、回滚、审计

7. `phase0_technical_spike_plan.md`
   - OpenAI-compatible 本地模型验证
   - instructor 结构化输出验证
   - PydanticAI 验证
   - ARQ / BackgroundTasks 分级验证
   - pgvector >= 0.8.2 验证
   - vLLM / Xinference / LiteLLM / 自研 Model Gateway 候选验证

8. `codex_phase1_development_plan.md`
   - 给 Codex / Claude Code 的正式开发计划
   - 必须包含开发顺序、测试要求、回归要求、打包要求
   - 最后可保留固定确认语：`confirmation that a fresh package was created from the current repository state`
   - v1.0.11 package note：该确认语表示任务交付产物基于当前仓库状态生成/更新，不绑定强制人工 diff review。

---

## 4. Phase 0 工作优先级

建议顺序：

1. 冻结 Phase 1 MVP 范围
2. 冻结仓库结构与模块边界
3. 冻结 Gateway / Runtime / Control Plane / Execution Fabric 的 import 依赖规则
4. 写 CapabilitySpec 最小字段
5. 写 Task 状态机最小版
6. 写 IdentityMapping / Credential 最小模型
7. 写 Response Envelope / SDUI 最小协议
8. 写 Mock Adapter 和 3–5 个 Golden Tasks
9. 写技术 Spike
10. 写 Codex Phase 1 开发计划

---

## 5. Phase 1 建议只做的内容

Phase 1 建议只做：

1. Web / H5 / PC 浏览器入口
2. Admin Console 最小后台
3. FastAPI 后端
4. Runtime 最小状态机
5. Capability Registry 最小版
6. Capability Gateway 最小版
7. Policy Guard 最小版
8. Trace / Audit 最小版
9. IdentityMapping Mock / 最小账号绑定状态
10. Response Envelope / SDUI 最小卡片
11. 3–5 个 Mock Golden Tasks
12. OpenAI SDK + instructor 结构化输出
13. PostgreSQL + pgvector >= 0.8.2
14. JobQueuePort L0 实现，是否启用 Redis + ARQ 由 Phase 0 验证决定

Phase 1 不做真实 OA / U8 / 海康写入，不做生产 RPA，不做原生 App，不做生产级受控探索，不做完整 Memory Fabric，不做 Skill CI/CD 全流程。

---

## 6. 下一聊天窗口建议首条 Prompt

可以直接复制下面这段到新窗口：

```text
我们已经冻结了企业智能助手主蓝图，冻结文件为 enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md。请不要继续修改主蓝图。接下来进入 Phase 0。

你的任务是基于冻结蓝图，开始编写 phase0_architecture_freeze_and_mvp_spec.md。要求：
1. 严格遵守冻结蓝图的架构边界和安全底线。
2. 不扩写主蓝图，不引入新的大架构。
3. 明确 Phase 1 做什么 / 不做什么。
4. 明确 MVP 模块优先级。
5. 明确 Runtime、Capability Gateway、Policy Guard、Trace、Identity Binding、SDUI 的最小可用范围。
6. 明确技术 Spike 列表。
7. 明确 Codex / Claude Code 后续开发边界。
8. 输出为结构化 Markdown，方便后续继续拆成专项 spec。
```

---

## 7. 冻结声明

截至本 handoff，主蓝图冻结为：

`enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md`

后续如非发现重大架构错误，不再修改主蓝图。所有字段、接口、schema、索引、状态码、默认值、测试用例、验收标准和开发计划，全部进入 Phase 0 或专项规格文件。
