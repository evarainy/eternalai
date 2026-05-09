# 企业智能助手 / 受控进化型 Agent Runtime 最终蓝图方案 v3.2.4（Freeze Final Blueprint for Phase 0）

> **定位**：这是“最终蓝图方案”，用于定方向、定框架、定长期边界。它不是一期 MVP 任务书，也不是详细开发排期。后续 Phase 0、Phase 1、Phase 2 的开发计划应从本蓝图中裁剪，而不是全量实现。  
> **当前实现约束**：纯内网优先；开发团队 1 人；开发辅助工具为 Codex 与 Claude Code；企业系统老旧、权限不统一；员工信息化水平低；系统长期目标是受控进化。具体模型、版本、部署形态、测试用例和验收标准由 Phase 0 规格文件冻结；蓝图只保留长期边界、架构原则和不可突破底线。
---

## 0. 执行摘要

本项目不应定义为“聊天机器人”，也不应定义为“自由探索型 AI Agent”，而应定义为：

> **企业级受控进化型 Agent Runtime**：在企业现有 OA、ERP、财务、HR、MES、IoT、文件系统和本地电脑之上，构建一层可自然语言交互、可执行、可治理、可审计、可长期进化的智能操作层。

它的核心目标不是让 AI 直接接管企业系统，而是让 AI 成为企业确定性系统之上的：

1. **自然语言与结构化交互层**：让低信息化用户通过自然语言、语音、文件、图片、动态卡片、微表单和快捷按钮等方式发起任务。
2. **任务理解与调度层**：把模糊请求转成结构化 Task，并选择 Workflow、Skill、Tool；在满足沙箱、白名单和治理条件后，才可进入受控探索路径。
3. **能力治理控制层**：把 API、DB、RPA、Local Worker、IoT、MCP、外部 Agent 等全部能力资产化、版本化、可审计。
4. **稳定执行编排层**：对确定流程走 Workflow，对成熟经验走 Skill，对原子操作走 Tool；对无现成能力的请求，Phase 1 返回 no_capability_found，后续阶段在受控条件下生成探索提案；Planner 前必须先经过 Capability Preselector 压缩候选能力。
5. **受控进化闭环层**：从 Trace 中沉淀经验，但所有新 Skill 必须经过测试、风险评级、自动评测、人工最终审批、灰度和回滚。

最终设计原则：

```text
AI 处理不确定性；
工程系统处理确定性；
治理系统约束 AI 的行动边界；
Trace 与 Skill CI/CD 支撑系统受控进化。
```

---

## 1. 系统边界：三不原则

### 1.1 不是 Chatbot

Chatbot 的核心是回答问题；本系统的核心是：

```text
理解任务 → 匹配能力 → 执行动作 → 校验结果 → 留痕审计 → 沉淀经验
```

它可以聊天，但聊天只是入口体验，不是系统本质。

### 1.2 不是自由探索 Agent

自由探索 Agent 适合低风险、开放式、研究型任务，但不适合直接控制企业 OA、财务、ERP、MES、IoT、Shell、本地文件等生产资源。

本系统允许探索，但探索必须被约束：

```text
只读探索：有限开放；
写入探索：必须预览和确认；
高风险探索：默认禁止；
探索成功：只能生成 Skill 草稿，不得直接上线。
```

### 1.3 不是统一权限中心

企业业务系统权限复杂，短期内不应幻想由本系统统一接管所有权限。

本系统采用：

```text
业务数据权限：交给原业务系统判断；
Agent 动作权限：由 Runtime / Policy Guard 控制；
过程审计与证据：由 Trace / Audit 系统负责。
```

更准确地说：

```text
业务鉴权薄；
动作策略厚；
审计留痕厚。
```

---

## 2. 设计原则：五化原则

### 2.1 入口智能化

通过 Web、H5、PWA、PC 浏览器、CLI、企业微信、钉钉、语音、电话、Webhook 等入口承接用户请求。入口可以多样，但最终都应转成统一的任务输入协议。移动端优先采用 H5 / PWA / 企业 IM 内嵌页面，不将原生 iOS / Android App 作为本蓝图阶段主线交付端。

### 2.2 Runtime 受控化

Agent Runtime 负责理解、路由、规划、调度和校验，但不能绕过 Capability Gateway 直接调用生产工具。

### 2.3 执行确定化

确定性业务优先走 Workflow；成熟经验走 Skill；简单原子能力走 Tool；无现成能力时优先进入 no_capability_found 和管理员配置流程，只有满足沙箱、白名单、Policy、Trace 与审批条件后，才允许进入受控探索。

正式优先级：

```text
Workflow > Published Skill > Single Tool / Registered Tool Chain > no_capability_found / Controlled Exploration Proposal
```

说明：

```text
Registered Tool Chain 指由 Workflow / Published Skill 明确声明的静态工具链，Phase 1 可以支持轻量版本；
Dynamic Tool Composition 指由 LLM 在运行时临时组合多个 Tool，Phase 1 默认不开放，只能在后续沙箱、白名单、Policy、Trace 与审批条件满足后评估。
```

### 2.4 治理平台化

Workflow、Skill、Tool、MCP、Worker、外部 Agent 都必须进入统一注册、评测、审批、发布、审计和回滚流程。

### 2.5 进化闭环化

系统可以越用越聪明，但进化必须通过受控副链：

```text
Trace → 复盘 → Skill 草稿 → 自动评测 → 风险评级 → 审批 → 灰度 → 发布 → 监控 → 回滚
```

进化副链不得自动扩大主执行链权限。

---

## 3. 总体架构：双中枢、三链路、八层

### 3.1 双中枢

#### 中枢一：Agent Runtime

负责：

```text
理解用户请求；
生成结构化任务；
检索上下文和记忆；
匹配能力；
选择执行路径；
调度执行；
校验结果；
组织响应。
```

它回答的是：

```text
用户想做什么？
应该走哪条路径？
缺什么信息？
风险多高？
执行结果是否可信？
```

#### 中枢二：Capability Control Plane

负责：

```text
能力注册；
能力版本；
策略治理；
访问边界；
发布审批；
审计追踪；
评测质量；
生命周期管理。
```

它回答的是：

```text
系统到底有哪些能力？
谁可以调用？
在什么条件下可以调用？
如何测试和发布？
出了问题如何停用和回滚？
```

### 3.2 三链路

```text
稳定执行主链：
用户请求 → Task → 能力匹配 → Policy → 执行 → Evaluator → 响应

受控进化副链：
Trace → 复盘 → 记忆候选 → Skill 草稿 → 评测 → 审批 → 发布

治理审计链：
能力注册 → 风险评级 → 访问控制 → Trace → Evidence → Audit → Report
```

### 3.3 八层架构图

```text
┌───────────────────────────────────────────────────────────────┐
│  1. 多入口交互层                                               │
│  Web / H5 / PWA / PC 浏览器 / CLI / 企业微信 / 钉钉 / 语音 / 电话 / Webhook │
└───────────────────────────────┬───────────────────────────────┘
                                ↓
┌───────────────────────────────────────────────────────────────┐
│  2. 接入网关层                                                 │
│  统一消息协议 / 会话标准化 / 附件解析 / ASR-TTS / 通道适配      │
└───────────────────────────────┬───────────────────────────────┘
                                ↓
┌───────────────────────────────────────────────────────────────┐
│  3. 身份与上下文承接层                                         │
│  用户身份 / 账号绑定状态 / 业务账号映射 / 凭证承接 / Session  │
└───────────────────────────────┬───────────────────────────────┘
                                ↓
┌───────────────────────────────────────────────────────────────┐
│  4. Agent Runtime 层                                           │
│  Intent Router / Task Builder / Context Assembler / Planner    │
│  Capability Preselector / Execution Coordinator / Runtime Eval │
│  只生成结构化 ExecutionRequest，不直接调用生产工具             │
└───────────────────────────────┬───────────────────────────────┘
                                ↓
┌───────────────────────────────────────────────────────────────┐
│  5. Capability Control Plane 层                                │
│  运行时治理：Registry / Policy / Trace / Runtime Evaluation    │
│  管理控制面：Admin / Approval / Version / Lifecycle / Rollback │
└───────────────────────────────┬───────────────────────────────┘
                                ↓
┌───────────────────────────────────────────────────────────────┐
│  6. Capability Gateway 层                                      │
│  唯一真实执行入口：Precheck / Dispatch / Evidence / Postcheck  │
│  所有 Workflow / Skill / Tool / MCP / RPA / IoT 调用必须经过此层│
└───────────────────────────────┬───────────────────────────────┘
                                ↓
┌───────────────────────────────────────────────────────────────┐
│  7. 执行能力编排与 Execution Fabric 层                         │
│  Workflow Engine / Skill Engine / Tool Runner / AgentInterop   │
│  API Adapter / DB Gateway / RPA / Local Worker / Office / IoT  │
└───────────────────────────────┬───────────────────────────────┘
                                ↓
┌───────────────────────────────────────────────────────────────┐
│  8. 企业系统与设备层                                           │
│  OA / ERP / 财务 / HR / MES / 文件系统 / 海康 / UWB / IoT       │
└───────────────────────────────────────────────────────────────┘
```

> 注：第 6 层 Capability Gateway 是边界补丁版中显式强化的“唯一真实执行入口”。Agent Runtime、LLM 框架、Workflow、Skill、Admin Console 都不得绕过它直接调用生产工具或生产系统。

横向贯穿能力：

```text
Memory Fabric
Trace / Audit / Observability
Policy / Guardrails
Evaluation
Skill CI/CD
UI Rendering Protocol / SDUI
Ports and Adapters
```


### 3.4 UI Rendering Protocol / SDUI：低信息化用户的结构化交互层

多入口不等于只有聊天框。对低信息化用户而言，空白聊天框会带来“输入恐惧”：用户不知道系统能做什么，也不知道如何描述需求。因此，本系统的交互输出不应只是一段 Markdown 文本，而应支持由服务端返回结构化 UI 描述，由各端按自身能力渲染为卡片、微表单、按钮组、表格、图表、确认框或文件卡。

#### 3.4.1 基本定位

```text
UI Rendering Protocol：Runtime / Response Composer 输出的统一响应协议。
Server-Driven UI：服务端决定当前任务需要什么交互组件，前端负责安全渲染。
Dynamic Card Engine：前端/客户端根据协议渲染动态卡片、微表单、仪表盘和确认卡。
```

它解决的问题：

```text
用户不知道怎么问；
用户输入过于模糊；
需要收集结构化参数；
需要展示业务结果、图表、文件、流程状态；
需要高风险动作的预览和确认；
不同入口需要降级显示。
```

#### 3.4.2 Response Envelope

Runtime 的最终响应应统一封装为 Response Envelope，而不是只返回文本。Response Envelope 是蓝图级协议决策，用于支撑多端渲染、追问补参、确认审批、失败解释、operator handback 和 Trace 摘要展示。

顶层结构应至少包含以下语义区域：

```text
response_id：响应唯一标识。
task_id / session_id：与任务和会话关联。
message：面向用户的文本说明、追问或结果摘要。
ui：可选的结构化 UI 描述，用于卡片、表单、按钮、表格、图表、确认框等。
trace_summary：面向用户或审计的低敏 Trace 摘要，不包含凭证和敏感原文。
```

Response Envelope 必须支持 `schema_version`、多端降级和 `fallback_text`。Web、H5 / PWA、CLI、企业微信、钉钉、语音端的升级节奏不同，因此协议必须从一开始定义兼容策略：客户端应上报支持的 schema 版本和通道能力；服务端优先返回客户端支持的最高兼容版本；客户端不支持的组件和 action 必须降级展示或隐藏。

完整字段定义、JSON Schema、组件协议、action 枚举、客户端能力声明、版本兼容测试和前端渲染实现见 `sdui_protocol_spec.md`，由 Phase 0 冻结。

#### 3.4.3 支持的 UI 组件类型

蓝图阶段只定义组件类型方向，不冻结具体 schema。协议应预留文本、快捷按钮、微表单、确认卡、表格卡、图表卡、文件卡、流程状态卡、错误卡和 `operator_handback_card` 等类型。

`operator_handback_card` 既用于转人工、要求管理员配置能力，也用于用户未完成目标业务系统账号绑定时引导绑定。

完整组件字段、校验规则、action 种类和不同通道渲染映射见 `sdui_protocol_spec.md`。

#### 3.4.4 通道降级规则

不同入口展示能力不同，Response Envelope 必须支持降级：

```text
Web / H5 / PWA / PC 浏览器：渲染完整卡片、表单、图表、文件卡。
CLI：渲染为文本菜单和编号选项。
企业微信 / 钉钉 / 飞书：渲染为平台支持的卡片或按钮。
语音 / 电话：只播报摘要和关键选项，由用户语音确认。
Webhook / API：返回完整 JSON，由调用方自行渲染。
```

#### 3.4.5 SDUI 安全边界

```text
前端不得直接执行业务动作；
按钮点击只产生结构化 user_action；
所有 user_action 必须回到 Runtime / Capability Gateway；
UI schema 不得包含凭证、Token、Cookie、内部接口地址；
确认卡必须展示影响范围、目标系统、关键字段、是否可撤回；
所有用户点击、确认、拒绝、取消必须进入 Trace。
```

SDUI 的核心价值不是“炫 UI”，而是让低信息化用户通过“可点、可选、可确认”的方式完成企业任务，降低自然语言表达门槛，同时不破坏 Capability Gateway 的治理边界。

---

## 4. 核心对象模型

系统地基是以下对象，而不是某个模型或某个框架。

| 对象 | 含义 | 关键字段 |
|---|---|---|
| Session | 一次用户交互上下文容器 | session_id、user_id、channel、task_ids、active_task_id、context_snapshot、status、expired_at |
| Task | 用户请求标准化后的任务 | session_id、user、channel、intent、context、status、risk、终态原因摘要 |
| Capability | 可被系统调用的一项能力 | type、target_system、schema、risk、owner、version、policy、execution_identity、binding_required |
| Tool | 原子动作 | input_schema、output_schema、side_effect、adapter |
| Workflow | 管理员发布的确定性/半确定性业务流程 | input_schema、output_schema、steps、decision_nodes、human_gate、failure_policy、rollback_policy、version |
| Skill | 经验型能力包 | trigger、procedure、scope、health_score、source_trace |
| Memory | 可治理记忆 | scope、source、confidence、sensitivity、ttl |
| Trace | 执行轨迹 | task、steps、tool_calls、policy_checks、evidence |
| Policy | 动作策略 | condition、decision、reason、gate、audit_level |
| Evaluation | 评测结果 | case、expected、actual、score、grade、risk |
| Evidence | 可审计证据 | type、storage、sensitivity、retention、access_policy |
| IdentityMapping | AI 用户与业务系统真实账号、系统级凭证或厂商令牌之间的绑定/映射关系 | ai_user_id、target_system、bind_mode、execution_identity、external_principal_id、credential_ref、token_ref、status |


### 4.1 IdentityMapping 对象与账号绑定架构原则

IdentityMapping 是“AI 助手代表用户或系统调用业务系统”时的身份落点，不是普通用户资料字段。它回答的是：当前 AI 平台用户、部门或系统任务，在目标业务系统中应以什么身份执行；绑定是否有效；执行时应从哪里取得最小必要凭证引用。

IdentityMapping 只保存绑定元数据和凭证引用，不保存明文密码、access_token、refresh_token、Cookie 或 session token。

蓝图级绑定模式保留如下紧凑枚举：

```text
bind_mode:
- oauth2_delegate
- vault_user_credential
- system_credential_with_policy
- sso_ticket
- vendor_token
- manual_mapping_only

execution_identity:
- user_delegated
- system_scope
- admin_approved_proxy
```

核心约束：

```text
credential_ref / token_ref 只能引用 SecretProviderPort 可解析的密文或密钥管理系统对象；
external_principal_id 可以进入审计摘要，但不得被当作权限放行依据；
status=active 只表示绑定存在且最近验证有效，不代表本次业务动作一定有权限；
业务系统最终权限仍由目标系统、厂商平台或系统级 Policy 共同判断；
管理员导入账号映射不等于替用户录入密码；Vault 模式仍应由用户自助录入密码或由受控迁移流程导入密文引用；
不是所有业务系统都有用户级账号，海康、设备平台、开放平台类系统可能采用 system_scope + 业务侧 Policy 控制设备、区域、通道访问；
同一 ai_user_id 在同一 target_system 下可能存在多个账套、组织、资源域、园区、设备域或数据域映射，因此 IdentityMapping 不能假设“用户 + 系统”天然唯一。
```

Capability 必须声明目标系统、执行身份、是否需要绑定、允许的绑定模式、资源域要求和 fallback 策略。对 `user_delegated` Capability，`fallback_allowed` 默认必须为 false；不得因为用户未绑定、授权过期或 Vault 凭证失败，fallback 到 system_scope、管理员账号、共享账号或部门账号执行。

具体字段定义、唯一性约束、索引策略、绑定状态机、凭证引用规则、健康检查和审计事件见 `security_identity_binding_and_credential_spec.md`；Capability 身份字段细则见 `capability_policy_gateway_spec.md`。这些内容由 Phase 0 根据首批目标系统验证结果冻结。

### 4.2 Session 对象：会话级上下文容器

Session 是多轮交互和多 Task 协调的基础对象，不是前端实现细节。一个用户在同一个 Web / CLI / IM / 语音会话中可能连续发起多个 Task，也可能在等待追问、确认或审批时切换话题后再回来。

Session 与 Task 的边界：

```text
Session 负责承载“这次对话/交互窗口”的连续上下文；
Task 负责承载“一件可执行事情”的状态机；
一个 Session 可以包含多个 Task；
一个 Task 必须属于一个 Session，除非是纯 API 后台任务；
Clarifier、confirmation_card、micro_form、task.resume 必须绑定 session_id + task_id；
用户切换话题时，应创建新 Task，但原 waiting_* Task 仍可在同一 Session 中恢复；
Session 过期不等于 Task 自动取消，Task 是否取消取决于其 timeout_policy。
```

Context Assembler 的“最近会话摘要”应优先来自 Session，而不是把所有历史 Task 全量注入 LLM。Trace 归档时应记录 session_id，便于回放用户在一个会话中的完整交互路径。

Session 字段、上下文快照结构、过期策略和跨通道续接规则见 `runtime_state_machine_spec.md` / `phase0_architecture_freeze_and_mvp_spec.md`。

其中最关键的是 Capability：API、Workflow、Skill、MCP Tool、RPA 脚本、Local Worker 动作、IoT 查询和外部 Agent 都应作为 Capability 进入统一治理。

### 4.3 WorkflowSpec 架构级定义

Workflow 是正式业务流程，不是 Prompt，也不是自由脚本。Phase 1 的 Workflow Engine 可以很轻，但 Workflow 的定义格式必须从一开始规范，否则 Codex / Claude Code 在实现时会各自发明格式，后续很难治理。

#### 4.3.1 Workflow 的基本定位

```text
Workflow：
- 面向正式业务流程；
- 由管理员或业务 owner 发布；
- 有明确输入、输出、步骤、失败策略和版本；
- 可以包含确定性步骤、简单条件分支和人工确认；
- 所有步骤都只能引用已注册 Capability；
- 执行时仍必须逐步经过 Capability Gateway 和 Policy Guard。
```

Workflow 不是：

```text
不是 LLM 自由规划结果；
不是任意脚本；
不是绕过 Policy 的批处理器；
不是把多个 Tool 调用拼在一起就自动上线的东西。
```

#### 4.3.2 MVP 阶段 Workflow 能力边界

```text
Phase 1 / MVP 支持：
- 线性步骤；
- 简单条件分支；
- Step 输入/输出映射；
- Step 级 Policy；
- Step 失败停止或有限重试；
- 用户确认 human_gate；
- Workflow 版本锁定；
- Trace 全链路记录。

Phase 1 / MVP 不支持：
- 复杂并行 DAG；
- 长事务；
- 跨天长任务恢复；
- 外部 Workflow 引擎；
- 自动从 Skill 升级为 Workflow；
- LLM 动态改写 Workflow 结构。
```

#### 4.3.3 WorkflowSpec 架构原则

Workflow 是正式业务流程，不是 Prompt，也不是自由脚本。WorkflowSpec 必须具备稳定语义：标识、版本、状态、owner、scope、risk、输入输出 schema、步骤、条件分支、human gate、失败策略、回滚/补偿策略和审计信息。

蓝图只定义 WorkflowSpec 的治理边界：

```text
所有步骤只能引用已注册 Capability；
执行时必须逐步经过 Capability Gateway 和 Policy Guard；
Workflow 必须版本化、可测试、可审批、可回滚；
Phase 1 优先支持线性步骤、简单规则分支、用户确认和有限失败处理；
复杂 DAG、跨天长事务、外部 Workflow 引擎和 LLM 动态改写 Workflow 结构不进入 Phase 1 主线。
```

WorkflowSpec 的完整 YAML/JSON Schema、Step 类型、输入输出映射、失败策略、版本锁定和回滚策略见 `workflow_runtime_spec.md`，由 Phase 0 冻结。

#### 4.3.4 Workflow 与 Skill 的边界

```text
Workflow：
正式业务流程；
稳定、高频、跨用户复用；
有明确 owner；
发布前必须经过测试和审批；
适合 OA、HR、财务、MES 等确定性业务。

Skill：
经验型能力包；
可以由 Trace 沉淀；
默认先是草稿；
可以服务个人/部门/企业不同 scope；
适合“做某类事的经验模板”。
```

Skill 升级为 Workflow 的条件：

```text
高频使用；
跨用户复用；
步骤稳定；
风险可控；
输入输出 schema 清晰；
通过 Golden Task 测试；
明确业务 owner；
需要被正式治理、审计和版本管理。
```

---


### 4.4 Scope 与部门级隔离原则

本系统必须在对象模型层面对 `personal / department / enterprise / system` 等作用域做显式建模，不能只在文档文字中表达“部门共享”。部门隔离会影响 Capability 可见性、Memory 检索、Policy 决策、Trace 访问和 Admin Console 看板。

#### 4.4.1 请求上下文必须携带组织信息

每个 Runtime 请求至少应携带：

```yaml
RequestOrgContext:
  user_id: string
  department_id: string | null
  org_unit_path: string[]
  roles: string[]
  position: string | null
  data_scope: own | department | enterprise | custom
```

#### 4.4.2 Capability 可见性字段

```yaml
Capability:
  scope: personal | department | enterprise | system
  owner_user_id: string | null
  owner_department_id: string | null
  visible_to_departments: string[]
  visible_to_roles: string[]
  allowed_departments: string[]
  data_scope: own | department | enterprise | custom
```

#### 4.4.3 Memory 检索隔离

```text
personal Memory 只能由本人检索；
department Memory 只能由同部门或被授权角色检索；
enterprise Memory 必须经过治理发布；
system Memory 只能由系统和管理员维护；
Capability Preselector 必须先做 scope / department 过滤，再做 embedding 检索；
Memory 检索不得把 A 部门经验注入 B 部门上下文，除非该记忆已被治理为 enterprise scope。
```

#### 4.4.4 Policy 决策上下文

Policy Guard 必须把 `department_id / roles / data_scope / target_system / capability_scope` 作为决策输入。跨部门查询、跨部门导出、跨部门共享 Skill、跨部门 RPA 代理执行应默认升级为 `approve` 或 `deny`，不得默认 allow。

---

## 5. 业务请求流程与 Task 状态机

本章描述系统真正运行时的业务流程。它不是代码实现细节，而是所有后续开发都必须遵守的运行时逻辑。

核心原则：

> **业务请求不是一条直线，而是一个可暂停、可确认、可审批、可失败、可恢复、可归档的状态机。**

---

### 5.1 完整业务请求流程

标准业务请求流程如下：

```text
1. Ingress：用户通过任意入口提出请求。
2. Normalize：接入网关将文本、语音、文件、图片、Webhook 等标准化为统一消息。
3. Identify：身份与上下文层装配用户、会话、业务账号映射和通道信息。
4. Build Task：Runtime 创建结构化 Task，并生成 trace_id / task_id。
5. Classify：Intent Router 判断意图、复杂度、风险初判和任务类型。
6. Retrieve Context：Context Assembler 装配用户画像、会话上下文、相关记忆、相关能力摘要。
7. Match Capability：Capability Preselector 从 Registry 检索 Workflow / Skill / Tool / Worker / Agent 候选。
8. Clarify：缺少必填参数时进入追问，不得由模型猜测关键参数。
9. Plan：Planner 生成受控执行计划，并锁定将要使用的能力版本。
10. Policy Prefilter：Capability Gateway 调用 Policy Guard 对候选能力和计划做预过滤。
11. Preview：涉及写入、发送、导出、RPA、设备控制等动作时生成执行预览。
12. Confirm / Approve：按风险等级进入用户确认或管理员/业务负责人审批。
13. Execute：通过 Capability Gateway 执行，进入 Workflow / Skill / Tool / Execution Fabric。
14. Trace Step：每一步执行前后都记录 Trace，不是任务结束后才记录。
15. Evaluate：Runtime Evaluator 校验结果是否完整、可信、合规。
16. Recover：失败时按失败类型有限重试、补偿、转人工或终止。
17. Respond：Response Composer 返回用户可理解的结果、原因、证据或下一步建议。
18. Finalize Trace：归档执行记录、确认记录、审批记录、证据和最终状态。
19. Async Evolution：脱敏后异步进入 Memory 更新、失败聚类、Skill 草稿候选等进化副链。
```

在这条链路中：

```text
Trace 贯穿全过程；
Policy 在执行前和高风险步骤前生效；
Evaluator 在执行后生效；
Memory / Skill 进化只能异步发生，不能阻塞主链，也不能自动扩大权限。
```

---

### 5.2 Task 状态机

Task 必须有明确生命周期，不能只用 `pending / success / failed` 三个粗状态。

建议状态集合与典型路径：

```text
正常成功路径：
created → normalized → identified → capability_matched → planned → policy_checked → executing → verifying → succeeded → archived

能力缺失路径：
created → normalized → identified → no_capability_found → archived

等待 / 确认 / 审批路径：
waiting_clarification / waiting_user_confirm / waiting_admin_approval → resumed 或 cancelled / escalated → archived

失败 / 取消 / 补偿路径：
executing → failed → archived
executing → cancelling → cancelled → archived
executing → compensation_required → compensating → compensated → cancelled_with_side_effects → archived
```

说明：上述是状态集合与典型路径，不是单一线性链路。`no_capability_found` 是能力缺失分支，不应被实现为正常成功路径中的中间状态。

状态说明：

| 状态 | 含义 | 典型触发 |
|---|---|---|
| created | 已创建任务 | 用户请求进入 |
| normalized | 输入已标准化 | 语音转文本、附件解析完成 |
| identified | 身份和会话已承接 | 用户、通道、业务身份映射完成 |
| capability_matched | 已找到候选能力 | Registry 检索完成 |
| no_capability_found | 未找到可用能力 | 没有匹配 Workflow / Skill / Tool / Query Capability，或候选全部被 Policy 预过滤剔除 |
| waiting_clarification | 等待补充信息 | 缺必填参数或语义不清 |
| planned | 已形成执行计划 | Planner 生成结构化计划 |
| policy_checked | 已完成策略预检 | allow / confirm / approve / deny |
| waiting_user_confirm | 等待用户确认 | 中风险写操作、发送、导出等 |
| waiting_admin_approval | 等待管理员或业务负责人审批 | 高风险、跨部门、敏感数据等 |
| executing | 正在执行 | Workflow / Skill / Tool 执行中 |
| cancelling | 用户在执行中请求取消 | 任务已进入 executing，但收到取消请求 |
| compensation_required | 需要补偿处理 | 已产生外部副作用，不能直接取消 |
| compensating | 正在执行补偿动作 | 撤回 OA、删除草稿、补发说明、人工处理等 |
| compensated | 补偿完成 | 副作用已被撤回或被业务接受 |
| verifying | 正在校验结果 | Evaluator 校验阶段 |
| succeeded | 成功完成 | 结果通过校验 |
| failed | 失败结束 | 权限、策略、工具、校验等失败 |
| cancelled | 用户取消 | 尚未产生不可逆副作用时用户取消 |
| cancelled_with_side_effects | 带副作用取消 | 任务被取消但外部副作用无法完全撤回，需记录补偿结果 |
| escalated | 转人工 | 超出自动化边界或不可信 |
| archived | 已归档 | Trace / Evidence / Final State 完成归档 |

状态机约束：

```text
高风险任务不得从 planned 直接进入 executing；
缺参数任务不得从 capability_matched 直接进入 planned；
Policy deny 的任务不得进入 executing；
用户在未进入 executing 前取消的任务不得继续调用工具；
任务已进入 executing 后收到取消请求，只能停止未执行步骤，已发出的外部调用不得假定可撤回；
执行失败或取消但已产生副作用的任务必须进入补偿或转人工流程；
所有终态都必须进入 archived。
```

#### 5.2.1 状态、原因码与等待态原则

Task 状态机只表达生命周期阶段，不应把每一种失败原因、取消原因或超时原因都做成独立状态。失败、取消、超时、拒绝、凭证失效、Evaluator 不通过等应通过原因码表达，避免状态爆炸。

所有 `waiting_*` 状态必须具备可配置超时和明确出口，避免生产中产生永久挂起任务。超时不得自动执行；超时前可提醒用户或审批人；超时后的 Task 必须归档 Trace，并可进入取消、升级或转人工。

Task 状态枚举、原因码、等待态默认超时、恢复规则、状态转换表和数据库字段由 `runtime_state_machine_spec.md` / `phase0_architecture_freeze_and_mvp_spec.md` 承接。

#### 5.2.2 no_capability_found：能力缺失终态

`no_capability_found` 不是普通失败，而是系统能力缺口。它应作为可统计的终态进入 Trace 和 Admin Console，用于后续能力建设。

触发条件：

```text
没有任何候选 Capability；
候选能力均低于置信度阈值；
候选能力存在但当前用户/部门/模式不可见；
用户需求需要新增业务系统 Adapter、Workflow 或 Query Capability。
```

处理规则：

```text
返回用户“当前没有已配置能力，需要管理员配置或新增 Capability”；
记录原始意图摘要、用户部门、请求频率、候选失败原因；
进入 Capability Demand Board 聚合；
不得让 LLM 自由探索生产系统作为替代路径。
```


---

### 5.3 缺参数与追问分支

系统发现缺少必填参数时，必须追问，而不是让模型猜测。

流程：

```text
Intent Router / Capability Preselector
→ 识别缺少必填参数
→ Task 状态进入 waiting_clarification
→ Clarifier 生成明确问题
→ 用户补充
→ Runtime 合并上下文
→ 继续 Match / Plan
```

示例：

```text
用户：帮我申请一台电脑。
系统：请确认申请人、设备类型、申请原因和期望到货时间。
```

禁止：

```text
不得自动猜申请人；
不得自动猜设备型号；
不得自动提交缺字段流程；
不得把“用户平时常用笔记本”当成正式申请参数。
```

---

### 5.4 用户确认与管理员审批分支

Policy Guard 的结果必须驱动任务进入不同状态。

```text
allow：进入 executing。
confirm：进入 waiting_user_confirm。
approve：进入 waiting_admin_approval。
deny：进入 failed，并记录策略拒绝原因。
escalate：进入 escalated。
```

用户确认适用于：

```text
创建草稿；
提交普通流程；
发送通知；
生成并分享文件；
有限范围的数据导出；
低风险 RPA 写入。
```

管理员或业务负责人审批适用于：

```text
跨部门敏感数据；
财务写入；
批量导出；
代理账号 RPA；
高风险设备操作；
Shell 或本地系统配置变更；
新增企业级 Skill / Workflow 发布。
```

确认与审批必须记录：

```text
确认人 / 审批人；
确认时间；
确认内容快照；
能力版本；
风险等级；
预览内容；
最终决策。
```

用户拒绝确认后，Task 必须进入 `cancelled`，不得继续执行。

---

### 5.5 权限失败分支

业务系统返回 `401 / 403 / permission denied` 时，系统不得尝试绕权。

流程：

```text
业务系统返回权限失败
→ 不重试越权路径
→ 不切换高权限账号
→ 不通过 DB Gateway 或 RPA 绕开原系统权限
→ 返回“无权限或需开通权限”
→ Trace 记录 target_system、capability、错误类型
→ Task 结束为 failed，并记录权限拒绝原因
```

禁止：

```text
不得因为 API 403 就改用数据库直查；
不得因为用户账号无权限就改用管理员代理账号；
不得把权限失败交给 Agent 自由探索替代路径。
```

---

### 5.6 执行失败与恢复分支

失败必须分类处理，不能统一写成 unknown error。权限、策略、输入校验、外部依赖、凭证失效、绑定失效、RPA 漂移、Evaluator 不通过、用户取消和未知错误等，应通过标准原因码表达，而不是扩展成大量生命周期状态。

恢复策略：

```text
可重试错误：有限次数重试，指数退避；
权限错误：不重试，不绕权，提示用户；
校验错误：尝试修正参数或追问；
外部依赖错误：降级、稍后重试或转人工；
已产生副作用的失败：进入补偿流程或转人工；
未知错误：停止执行并保留证据。
```

重试边界：不得无限重试；不得重试高风险写操作，除非具备幂等键；不得在用户取消后重试；不得在 Policy deny 后重试。

执行中取消边界：用户在 executing 前取消时不得继续调用工具；任务进入 executing 后，只能取消未开始的后续步骤；已发出的外部调用不得假定可撤回；若已产生副作用，必须进入补偿流程或转人工，并保留 Evidence。

具体原因码枚举、重试策略、补偿状态转换和典型业务示例见 `runtime_state_machine_spec.md`。

### 5.7 Evaluator 不通过分支

Evaluator 不是装饰性模块，它必须决定后续动作。

```text
Evaluator passed
→ 返回结果并归档。

Evaluator uncertain
→ 请求用户确认、转人工复核或降级返回“不确定”。

Evaluator failed
→ 尝试安全修正、重新执行无副作用步骤、标记失败或转人工。
```

示例：

```text
生成文件后，Evaluator 发现文件不存在：不得返回“已完成”。
提交流程后，Evaluator 未拿到流程号：不得宣称“提交成功”。
查询结果字段缺失：不得把不完整数据包装成完整答案。
```

---

### 5.8 Trace 全链路记录原则

Trace 不是任务结束后的日志，而是运行时事实账本。

必须记录：

```text
Task 创建：task_id、trace_id、user、channel、timestamp。
能力匹配：候选能力、置信度、未命中原因。
计划生成：计划摘要、能力版本、风险初判。
Policy 检查：输入摘要、决策、原因、命中规则。
用户确认/审批：确认内容快照、确认人、时间。
工具调用前：能力、参数摘要、风险等级。
工具调用后：结果摘要、状态码、耗时、证据引用。
Evaluator：校验项、结果、失败原因。
任务终态：成功、失败、取消、转人工、补偿结果。
进化副链：是否进入 Memory 候选、Skill 草稿候选、失败聚类。
```

Trace 记录必须遵守第 9 章的 Trace / Evidence / Raw Payload 分层，不得把敏感原文直接写入普通日志。

---

### 5.9 写操作的幂等、预览与补偿

企业写操作不能只靠“确认”两个字兜底，必须具备幂等、预览和补偿设计。

#### 5.9.1 幂等

```text
所有 L3/L4 写操作必须有 idempotency_key；
重复请求必须能识别；
提交类动作必须记录业务系统返回的 workflow_id / request_id / document_id；
用户连续点击确认不得产生重复提交。
```

#### 5.9.2 预览

写入前必须展示：

```text
将要做什么；
影响哪个系统；
写入哪些字段；
是否发送通知；
是否生成流程；
是否导出数据；
是否可以撤回；
风险等级和确认人。
```

#### 5.9.3 回滚与补偿

不同写操作的回滚能力不同，必须在 Capability 声明中写清：

```text
创建草稿：通常可删除草稿。
提交 OA：通常只能撤回或作废。
发送通知：通常不可撤回，只能补发说明。
修改业务数据：依赖业务系统提供撤销流程。
设备控制：一般不可回滚，只能执行反向操作或人工处理。
```

Capability 必须声明幂等要求、预览要求、回滚策略、补偿策略和副作用等级。具体字段名、枚举值、校验规则和发布审批要求见 `capability_policy_gateway_spec.md`。

## 6. Agent Runtime 设计

### 6.1 Runtime 的职责

Agent Runtime 不是模型本身，而是一套受控运行机制。

它负责：

```text
任务理解；
任务建模；
上下文装配；
上下文预算控制；
能力选择；
混合规划；
执行协调；
结果校验；
响应组织；
异常恢复；
降级运行；
进化事件触发。
```

Runtime 的核心边界：

```text
Runtime 只决定“应该请求什么能力”；
Capability Control Plane 决定“该能力是否存在、是否可用、是否允许、是否需要审批”；
Capability Gateway 决定“本次调用如何受控执行”；
Execution Fabric 负责“真正落到外部系统或设备”。
```

### 6.2 Runtime 的组件

```text
Intent Router：意图识别、复杂度判断、路径初选。
Task Builder：将输入转成结构化 Task。
Clarifier：缺信息时追问，而不是猜。
Context Assembler：装配会话、记忆、用户画像、候选能力上下文。
Context Budget Manager：按模型窗口、任务类型和优先级控制上下文注入。
Capability Preselector：在 Planner 前用规则、标签、Embedding、小模型等低成本方式筛出 Top-K 候选能力。
Capability Summary Cache：缓存能力摘要与向量索引，不把全量能力定义塞进 LLM。
Planner：只基于候选能力摘要为复杂任务生成受控计划，支持静态主计划和有限动态分支。
Execution Coordinator：协调执行状态、提交结构化 ExecutionRequest、接收执行结果；不直接碰生产工具。
Runtime Mode Manager：根据 LLM、Gateway、DB、Trace 等依赖健康状况切换运行模式。
Evaluator：校验执行结果是否符合预期。
Response Composer：生成用户可理解的文本响应和 UI Rendering Protocol / SDUI Response Envelope。
```

### 6.3 Context Budget Manager：LLM 上下文预算原则

Context Assembler 不只是“拼上下文”，而是一个预算受控的上下文装配器。随着会话历史、Memory、Capability 数量增长，直接把所有内容塞进 LLM 会导致质量下降、关键内容被截断，甚至超过模型窗口。

#### 6.3.1 基本原则

```text
上下文预算必须模型感知；
上下文注入必须有优先级；
超预算时不得简单从尾部截断；
Capability Registry 不得把全部能力全文注入 LLM；
Memory 不得以全文历史形式无限增长；
每次模型调用都必须记录 context_budget_summary，便于调试和评测。
```

Context Budget Manager 必须感知：

```text
model_id；
max_model_len；
reserved_output_tokens；
structured_output_tokens；
tool_call_schema_tokens；
safety_margin；
task_type；
risk_level；
current_runtime_mode。
```

#### 6.3.2 预算计算

```text
available_context_budget =
  model.max_model_len
  - system_prompt_budget
  - safety_policy_budget
  - current_user_input_budget
  - reserved_output_tokens
  - structured_output_schema_budget
  - tool_call_schema_budget
  - safety_margin
```

Phase 0 必须通过模型部署 Spike 确认候选内网模型（如 Qwen 系列、DeepSeek、GLM 或其他 OpenAI-compatible 本地模型）在实际硬件、实际量化方式、实际 vLLM/SGLang 配置下的可用 `max_model_len`，不能直接假设理论最大上下文可用于生产。具体模型、上下文长度、结构化输出能力和推理成本由 Phase 0 验证后冻结。

#### 6.3.3 注入优先级

```text
P0 必须注入：系统指令、安全边界、当前用户请求、Task 状态、当前模式。
P1 高优先级：当前执行计划、已选 Capability 摘要、Policy 相关信息、缺参字段。
P2 中高优先级：最近会话摘要、当前任务相关文件/表单摘要。
P3 中优先级：相关长期记忆 Top-K、用户偏好、历史相似任务摘要。
P4 低优先级：相关历史 Trace 摘要、失败案例摘要、候选能力 Top-N 摘要。
P5 可丢弃：低相关记忆、长历史全文、完整工具说明、过期能力描述。
```

#### 6.3.4 超预算处理顺序

```text
1. 压缩最近会话为摘要；
2. 降低 Memory Top-K；
3. 用 Capability 摘要替代完整定义；
4. 用 Trace 摘要替代原始执行轨迹；
5. 拆分任务为多步执行；
6. 追问用户缩小范围；
7. 如果仍超预算，拒绝执行并说明原因。
```

禁止：

```text
禁止无提示截断安全策略；
禁止截断当前用户关键参数；
禁止截断 Policy 决策依据；
禁止把凭证、Token、Cookie 注入上下文；
禁止为了塞入更多记忆而删除执行边界说明。
```


### 6.4 Capability Preselector：Planner 前的能力动态压缩层

Capability Preselector 是进入 Planner 之前的低成本能力筛选层，目标是在不牺牲治理边界的前提下，减少注入 LLM 的工具/能力数量，提升 27B/32B/72B 内网模型的规划稳定性。

#### 6.4.1 基本原则

```text
Planner 不得看到全量 Capability Registry；
Planner 不得看到完整 API 文档、完整 Tool Schema、完整 MCP 工具列表；
Planner 只接收 Top-K 候选 Capability 的短摘要、输入输出 schema 摘要、版本、owner 和风险等级；
完整 Capability 定义只在 Capability Gateway 执行前按 ID 加载；
候选能力置信度不足时，优先追问、转人工或返回“需新增能力”，不得强行规划。
```

#### 6.4.2 输入、输出与预算原则

Capability Preselector 的输入包括用户请求、Task 类型、用户身份与组织范围、会话摘要、用户可用能力范围、Capability Summary Cache、Policy 预过滤结果和当前 Runtime Mode。

输出不是可执行计划，而是候选能力集合及其摘要信息，例如 capability_id、匹配理由、置信度、风险等级、版本和输入输出摘要。Planner 只能基于预算内候选能力做计划；完整 Capability 定义只允许在 Gateway 执行前按 ID 加载。

MVP / Phase 1 可采用规则匹配、标签过滤、Embedding 检索和 Policy 预过滤的轻量组合。V1+ 可增强小模型分类器、部门级热度排序、用户历史偏好、失败率和 health_score 加权、能力图谱邻接扩展。

Top-K 策略、候选排序算法、输出 JSON Schema、冷启动参数和 Context Budget 细节见 `capability_policy_gateway_spec.md` / Phase 0 调优文档。

#### 6.4.3 Capability 冷启动策略

Capability Preselector 不能只依赖历史命中率和 embedding 相似度，否则新注册 Capability 可能长期进不了 Top-K，导致“能力已发布但用户用不上”。

新能力发布时必须具备：

```text
intent_tags：明确意图标签；
trigger_examples：3–10 条典型用户说法；
aliases：业务别名、系统别名、口语表达；
owner：维护人；
scope / allowed_departments：可见范围；
risk_level：风险等级；
input_schema_digest / output_schema_digest：摘要。
```

冷启动机制建议：

```text
新能力在首次 N 天或前 N 次相关请求内获得 cold_start_boost；
精确标签命中优先于纯 embedding 排名；
管理员可以将能力 pin 到指定 intent / department / role；
冷启动期结束后，根据真实命中率、成功率、用户反馈、失败率调整权重；
冷启动加权不得绕过 Policy 预过滤和部门隔离。
```

冷启动的目标是让新能力“可被发现”，不是让它绕过治理。

#### 6.4.4 与 Capability Gateway 的关系

Capability Preselector 只负责筛选候选能力，不负责执行，也不负责最终授权。所有执行仍必须经过：

```text
Planner
→ ExecutionRequest
→ Capability Gateway
→ Policy Guard
→ Execution Fabric
```

它不能绕过 Capability Gateway，也不能把未注册能力临时暴露给 Planner。

---

### 6.5 执行路径优先级

正式优先级只定义一次，后续所有模块引用此原则。注意：Controlled Exploration 是长期能力，在 Phase 1 和首批封闭业务系统中默认关闭。

```text
Workflow > Published Skill > Single Tool / Registered Tool Chain > no_capability_found / Controlled Exploration Proposal
```

含义：

```text
已有固定流程：走 Workflow。
已有成熟经验：走 Published Skill。
只有单个原子能力：走 Single Tool。
已有明确步骤组合：走 Registered Tool Chain，即由 Workflow / Published Skill 显式定义的静态工具链。
无现成路径：Phase 1 返回 no_capability_found；后续在满足沙箱、白名单、Policy、Trace 与审批条件后，才允许进入受控探索提案。
```

阶段边界：

```text
静态 Tool Chain：由 Workflow / Published Skill 明确声明多个 Capability 步骤，Phase 1 可以支持轻量版本。
动态 Tool Composition：由 LLM 在运行时临时组合多个 Tool，Phase 1 默认不开放；Phase 3+ 才可在沙箱、白名单和严格 Policy 下评估。
```

### 6.6 Hybrid Planning：静态主计划 + 受控动态分支

企业任务并不总能在执行前一次性规划完毕。某些后续步骤依赖前一步结果，例如“先查考勤，根据结果决定是否生成提醒”。因此 Planner 不能被定义成纯静态计划器。

本系统采用：

```text
静态主计划：先锁定已知步骤、能力版本、风险等级、输入输出预期。
受控动态分支：在执行结果出来后，通过 Decision Node 选择下一步。
```

#### 6.6.1 Decision Node 类型

```text
Rule Decision：
确定性规则判断，例如关键指标超过阈值、状态枚举命中异常集合。
优先使用，适合数值、枚举、状态类判断。

LLM-Assisted Decision：
LLM 输出结构化判断建议，例如异常原因归类、文本意图判断。
必须经过 output_schema 校验和 Evaluator 校验，不能直接放行高风险动作。

Human Decision：
风险较高、结果不确定或涉及业务责任时，由用户、管理员或业务负责人确认。
```

#### 6.6.2 动态分支铁律

```text
动态分支的每一步都必须重新经过 Capability Gateway；
动态分支的每个高风险步骤都必须重新经过 Policy Guard；
LLM 不得在执行中临时调用未注册 Capability；
LLM 不得在执行中修改 Workflow / Skill 的已发布定义；
执行结果只能影响“下一步选择”，不能扩大权限；
动态分支必须写入 Trace，便于回放。
```

#### 6.6.3 示例边界

Hybrid Planning 示例只用于说明“静态主计划 + 受控动态分支”的模式，不在蓝图中冻结具体业务规则、阈值或字段。考勤、OA、ERP、视频查询等具体示例应进入 `examples.md` 或对应业务 Workflow 规格文件。

### 6.7 Known Read Fast Path：低风险高频查询快速路径

Known Read Fast Path 是 Capability Gateway 内部的低风险调度模式，不是新的业务入口，也不是绕过治理的捷径。它用于已注册、只读、低风险、高频、参数完整的查询，解决车间、销售、管理人员高频“查一下”场景的响应延迟问题。

核心原则：

```text
Fast Path 属于 Capability Gateway 的内部优化；
Runtime 只负责识别“可能是已知低风险查询”并提交候选请求；
复杂 Planner 可以跳过，但 Gateway、Policy、Trace、权限、脱敏和 Deterministic Evaluator 不能跳过；
不得用于批量导出、跨部门敏感数据、写操作、RPA、Shell 或 IoT 控制；
不得绕过 Capability Registry，也不得临时暴露未注册能力。
```

快速路径的触发条件、最小 Trace 字段、Deterministic Evaluator 规则和异步补全策略见 `phase0_architecture_freeze_and_mvp_spec.md` / `capability_policy_gateway_spec.md`。

### 6.8 Runtime Degraded Mode：模型不可用时的降级策略

LLM 是重要能力，但不能成为所有企业流程的单点故障。当 vLLM / 模型服务出现 OOM、超时、崩溃、排队过长时，系统必须有降级模式。

#### 6.8.1 运行模式

```text
Normal Mode：
LLM 正常，支持自然语言、Skill、已注册静态工具链、总结生成和受控探索草案。动态 Tool Composition 仅在后续阶段满足沙箱、白名单和 Policy 条件后评估。

Limited LLM Mode：
LLM 慢或不稳定，限制模型调用，优先走规则、Embedding、已发布 Workflow 和 Fast Path。

Workflow-only Mode：
LLM 不可用，只允许用户通过按钮、菜单、CLI 命令、固定 API 触发已注册 Workflow。
不支持自然语言意图理解、LLM Planner、动态 Tool Composition、Controlled Exploration、长文本总结。

Maintenance Mode：
核心依赖不可用，只允许查看系统状态、历史任务、审计记录和管理员维护操作。
```

#### 6.8.2 Workflow-only 模式允许与禁止

```text
允许：
已发布 Workflow；
固定表单入口；
固定参数校验；
确定性 Policy；
Trace / Audit；
用户确认 / 管理员审批。

禁止：
自然语言解析；
LLM Planner；
LLM 总结；
动态 Skill 生成；
动态 Tool Composition；
Controlled Exploration；
自动 Memory 抽取。
```

#### 6.8.3 模式切换原则

```text
Runtime Mode Manager 监测 ModelProviderPort、Capability Gateway、DB、Trace Store、SecretProvider 的健康状态；
模式切换必须写入审计事件；
用户界面必须明确提示当前处于降级模式；
降级模式不得扩大权限；
恢复 Normal Mode 后，不自动重放失败任务，除非用户或管理员确认。
```

### 6.9 Capability Summary Cache：能力摘要缓存

Capability 数量增长后，Runtime 不能每次读取全部能力定义，也不能把所有能力全文塞进 LLM。必须建立能力摘要缓存。

Capability Summary Cache 应包含能力标识、类型、意图标签、输入输出摘要、风险等级、owner、版本、状态、短描述、向量索引和策略摘要等核心元信息。它的目标是服务 Router / Capability Preselector 的候选检索和 Context Assembler 的预算控制，而不是替代 Capability Registry 的完整定义。

使用原则：

```text
Router / Capability Preselector 使用摘要和 embedding 做候选检索；
Context Assembler 只注入预算内候选能力摘要；
完整 Capability 定义只在 Gateway 执行前按版本加载；
Capability 更新、停用、回滚时必须使缓存失效；
摘要不得包含凭证、内部接口密钥、敏感字段样例。
```

完整字段定义、缓存更新策略、digest 计算规则、索引策略和冷启动参数见 `capability_policy_gateway_spec.md`。

### 6.10 Controlled Exploration 受控探索机制

Controlled Exploration 是长期演进能力，不是当前 OA / ERP / 海康等封闭业务系统接入的默认路径。它适合在目标系统具备开放 API、沙箱、文档、只读工具白名单和完整 Trace/Evidence 的前提下，用于生成 Execution Proposal / Capability Proposal / SkillDraft，而不是直接完成生产任务。

阶段边界：

```text
Phase 1：默认关闭生产级受控探索。无现成能力时，返回 no_capability_found，并进入管理员配置或 Capability Demand Board。
Phase 2：仅允许在测试用户、测试环境、只读白名单内生成探索草案，例如读取已审批文档、schema、Mock/Sandbox Tool、错误日志。
Phase 3+：可在沙箱中尝试低风险工具组合，但结果只能进入 Proposal / SkillDraft，不得直接执行生产写操作。
```

对首批封闭系统的默认规则：

```text
泛微 OA、用友 U8 / U8 Cloud、海康 iVMS / 综合安防平台默认不开放未知路径探索；
无现成 Capability 时，系统不得尝试绕过接口文档、登录页面、SDK 或数据库自行摸索；
用户看到的是“当前无可用能力，需要管理员配置或新增能力”，而不是系统错误；
相关请求进入 no_capability_found 统计，用于后续建设 Workflow / Adapter / Query Capability。
```

允许评估开启的前置条件：

```text
目标系统有开放 API / 沙箱 / 文档；
已配置只读/沙箱工具白名单；
已配置 Exploration Policy；
已配置最大步数、最大耗时、最大工具调用次数；
已配置 Trace / Evidence 记录；
已配置 Governance Evaluation 测试集；
已配置管理员审批、灰度、回滚和禁用机制。
```

责任划分：

```text
Runtime：只负责提出 ExplorationRequest；
Capability Gateway：负责执行前策略检查和工具白名单限制；
Admin Console：负责开关、范围、审批和审计；
Policy Guard：负责最终允许/拒绝/确认/审批判定；
Governance Evaluation：负责探索结果能否进入 Capability Proposal / SkillDraft。
```

允许使用的能力范围：

```text
允许：只读知识检索、已脱敏 Trace 检索、已审批 DB schema/视图说明、公开接口文档、Mock/Sandbox Tool、低风险文件分析。
限制：生产 API 只读接口必须通过 Capability Gateway，且需要 Policy allow。
禁止：生产写接口、Shell、RPA 点击、IoT 控制、批量导出、代理账号、未注册 MCP Tool。
```

硬边界：

```text
探索结果不是执行结果；
探索成功不等于能力上线；
探索不得自动扩大权限；
探索不得绕过 Capability Gateway；
探索不得把真实业务数据写入 Memory 或 Skill；
Planner 不得把探索计划当成生产执行计划直接运行。
```

### 6.11 LLM 结构化输出与 Agent 编排框架定位

LLM 框架不是企业 Runtime 本身，也不是生产执行边界。Phase 1 默认采用 `OpenAI SDK + instructor + Pydantic v2 Schema` 做结构化输出，优先保证与 OpenAI-compatible 本地模型网关、vLLM / Xinference / LiteLLM / 自研模型网关的兼容性。

Phase 1 中 LLM 只允许产出：

```text
IntentDecision；
ParameterExtractionResult；
CapabilityRef / CandidateCapability；
PlanDraft / ExecutionProposal；
ClarificationQuestion；
ResponseEnvelope 草稿；
PolicyReasoningDraft（仅作解释草稿，不作最终放行决策）；
EvalResult 草稿；
MemoryCandidate / SkillDraft 候选。
```

正确调用关系：

```text
OpenAI SDK / instructor / PydanticAI / 其他 LLM 框架
→ 输出结构化决策 / 计划 / 草稿
→ Runtime 校验
→ Capability Gateway
→ Policy Guard
→ Execution Fabric
```

禁止关系：

```text
LLM 框架 → 直接调用生产 API / DB / RPA / Shell / IoT / MCP；
LLM 框架 → 直接读取 SecretProviderPort / Credential Vault；
LLM 框架 → 直接产生最终 Policy 放行决策；
LLM 框架 → 直接持有 Adapter 或业务系统 SDK 引用。
```

PydanticAI 的定位：

```text
PydanticAI 是 Phase 0 验证项，不是 Phase 1 强依赖；
验证重点包括 Qwen / vLLM / OpenAI-compatible 接口兼容性、流式输出、结构化输出、异常处理、重试和可观测性；
验证通过后，可在 Phase 2 作为 LLM Orchestration Adapter 引入；
无论是否引入 PydanticAI，都必须通过 LLMProviderPort / StructuredOutputPort / AgentOrchestrationPort 隔离。
```

LLM 可以辅助生成策略解释、风险说明、补参建议，但最终 `allow / confirm / approve / deny / escalate` 必须由确定性 Policy Engine 产生。

## 7. Capability Control Plane 设计

Capability Control Plane 是企业化的关键，不是可选项。

### 7.0 控制面边界

Capability Control Plane 分为两类能力：

```text
运行时治理服务：
Registry / Policy / Trace / Runtime Evaluation / Secret / Human Gate。
这些服务参与每次执行链路，必须低延迟、可审计、可回放。

管理型控制面：
Admin Console / Approval / Version / Lifecycle / Rollback / Analytics。
这些能力负责能力发布、审批、灰度、停用、回滚和治理运营。
```

边界原则：

```text
运行时治理服务可以被 Runtime / Gateway 调用；
管理型控制面不得绕过 Registry / Lifecycle / Approval / Audit 直接修改能力状态或生产配置；
能力发布、停用、回滚必须走受控生命周期流程并形成审计事件；
Capability Gateway 负责运行时执行，不承担能力生命周期管理职责。
```


### 7.1 管理对象

```text
Workflow
Skill
Tool
MCP Server / MCP Tool
API Adapter
DB View / Query Capability
RPA Script
Local Worker Action
IoT Capability
Document Capability
External Agent
```

### 7.2 核心能力

```text
Registry：能力注册与检索。
Policy：风险等级、调用条件、确认机制。
Evaluation：发布前和运行中的质量评测。
Lifecycle：草稿、测试、审批、发布、灰度、停用、回滚。
Audit：执行过程、调用人、参数摘要、结果证据。
Ownership：owner、维护人、业务归属、审批人。
Versioning：版本、兼容性、变更记录。
Admin Console：管理、观察、审计、回滚入口。
```

### 7.3 Capability Gateway 硬边界

Capability Gateway 是唯一真实执行入口。这个约束必须通过工程模块边界固化，而不能只依赖开发纪律。

所有调用必须经过：

```text
Capability Registry
→ Policy Guard
→ Trace Pre-Record
→ Adapter / Worker
→ Trace Post-Record
→ Runtime Evaluator
```

Workflow / Skill / Tool 的 Policy 粒度必须分层：

```text
Workflow 级 Policy：是否允许启动该流程。
Step 级 Policy：流程内部每个高风险步骤是否允许执行。
Tool 级 Policy：具体工具调用是否合规、是否需确认、是否需审批。
```

禁止：

```text
UI 直接调用 Tool。
Runtime 直接调用 Adapter。
LLM 框架直接调用 MCP / API / DB / Shell / RPA / IoT。
Workflow 绕过 Policy 调用工具。
Skill 直接访问生产系统。
Admin Console 直接操作业务系统生产数据库，或绕过 Registry / Lifecycle / Policy / Audit 修改 AI 平台生产配置。
```

Skill 边界：

```text
Skill 不是代码执行器；
Skill 是声明式经验包；
Skill 只能引用已注册 Capability；
Skill Engine 执行 Skill 时，所有步骤仍必须经过 Capability Gateway。
```

#### 7.3.1 Gateway 模块隔离原则

推荐工程依赖边界：

```text
apps/api：HTTP API 与入口层；
runtime：任务理解、路由、计划、响应；
control_plane：Registry、Policy、Trace、Approval、Admin Console 后端能力；
gateway：唯一执行入口、幂等、Policy Precheck、Trace Pre/Post、凭证注入；
execution_fabric：Adapter、Worker、DB Gateway、RPA、IoT、MCP、厂商 SDK；
shared_contracts：只放 DTO、Schema、Port，不放生产调用实现。
```

模块可见性原则：

```text
Runtime 只能依赖 CapabilityGatewayPort，不得依赖任何 Adapter、SDK、DB Client、RPA Client；
Workflow Engine 只能编排 CapabilityRef，不得持有工具函数引用；
Skill Engine 只能解释声明式 Skill，并将步骤提交给 Gateway；
LLM 框架只能输出结构化 Intent / Plan / Draft，不得注册或调用生产 Tool；
Admin Console 只能管理 Registry / Policy / Lifecycle / Binding，不得直接调用生产系统；
Adapter / Worker / SDK Client 只能位于 Gateway 内部或 Execution Fabric 内部包中；
生产凭证解析只允许 Gateway 通过 SecretProviderPort 发起。
```

Phase 0 / Phase 1 的仓库结构、lint、import rule 和测试应围绕上述边界设计。蓝图不规定具体工具，但必须保证 `runtime / workflow / skill / admin_console` 在代码依赖上拿不到 `execution_fabric` 的直接引用。

### 7.4 Credential & Secret Architecture：凭证与密钥架构

凭证管理是安全架构的一部分，不是 Adapter 的实现细节。OA、ERP、HR、MES、海康、UWB、DB、RPA、Local Worker、MCP Server 等都可能需要凭证，必须通过统一机制托管、注入、审计和轮换。

#### 7.4.1 凭证类型

```text
System Credential：
系统级服务账号、API Key、集成 Token。适合后台服务对接、只读查询、系统间集成。

User Delegated Credential：
用户授权 Token、SSO Token、OAuth/OIDC 委托凭证。适合以用户身份调用 OA、ERP、HR 等系统。

Adapter Credential：
某个 Adapter 连接业务系统所需的技术凭证，例如数据库连接账号、SDK 密钥。

Worker Credential：
Local Worker、RPA Worker、IoT Gateway 与 Capability Gateway 通信所需的设备证书、签名密钥或 mTLS 凭证。

Temporary Credential：
短期临时凭证，用于一次任务、一次上传、一次下载或一次沙箱执行。
```

#### 7.4.2 凭证作用域

```text
system：系统级，极少数服务账号使用。
user：用户级，绑定具体用户。
department：部门级，需部门 owner 审批。
capability：能力级，绑定某个 Adapter / Tool / Workflow。
worker：终端或 Worker 级，绑定设备身份。
task：任务级临时凭证，任务结束后失效。
```

#### 7.4.3 注入原则

```text
凭证不得进入 LLM 上下文；
凭证不得进入 Prompt、Memory、Skill、Trace 摘要、日志；
Runtime / LLM 框架不持有凭证；
Adapter 只能在执行瞬间通过 SecretProviderPort 获取最小必要凭证；
Capability Gateway 负责凭证选择、注入和审计；
凭证注入必须和 Capability、用户、Task、Policy 绑定；
凭证不得被 Adapter 返回给 Runtime 或用户界面。
```

#### 7.4.4 轮换、过期与执行中任务

```text
凭证必须支持过期、轮换、吊销和审计；
凭证过期时，正在执行的任务应进入 paused / failed_credential_expired / waiting_reauth，而不是自动切换高权限账号；
需要用户重新授权时，进入 human_gate；
需要管理员轮换系统凭证时，进入 admin maintenance flow；
所有凭证使用必须记录 credential_usage_event，但不得记录凭证明文。
```

#### 7.4.5 建议端口

```text
SecretProviderPort：读取、注入、轮换、吊销、验证凭证。
CredentialBindingPort：管理用户/能力/任务与凭证引用的绑定关系。
IdentityMappingPort：管理 AI 用户与目标业务系统账号的映射关系。
CredentialScope：描述凭证作用域。
CredentialRotationPolicy：轮换周期、过期策略、吊销策略。
CredentialAudit：记录凭证访问事件。
```


#### 7.4.6 身份执行模式与账号绑定方式

账号绑定管理必须把“AI 平台统一身份”和“目标业务系统真实执行身份”显式关联。不同业务系统的认证方式差异很大，不能只抽象成 OAuth2 委托模式和 Vault 凭证金库模式两类；蓝图应支持 `oauth2_delegate`、`vault_user_credential`、`system_credential_with_policy`、`sso_ticket`、`vendor_token`、`manual_mapping_only` 等多种绑定方式。

推荐 `bind_mode`：

```text
oauth2_delegate：标准 OAuth2 / OIDC / SSO 委托授权。
vault_user_credential：用户级账号密码或登录凭证加密托管。
system_credential_with_policy：系统级凭证 + AI 平台 Policy 控制访问范围。
sso_ticket：企业 SSO / 单点登录票据或短期登录态。
vendor_token：厂商开放平台 token、app_key/app_secret、SDK session。
manual_mapping_only：只维护账号映射，不托管凭证，执行时依赖目标系统已有会话或人工确认。
```

推荐 `execution_identity`：

```text
user_delegated：以用户在目标系统中的真实身份执行。
system_scope：以系统级身份执行，仅适用于系统维护、公开字典、健康检查、平台级设备查询等声明为 system_scope 的 Capability。
admin_approved_proxy：经过管理员审批的代理执行，必须有明确业务授权、审计和有效期，不得默认开放。
```

##### 模式一：OAuth2 / OIDC / SSO 委托模式

适用系统：支持标准 OAuth2 / OIDC / SSO 委托授权的业务系统，或企业统一身份平台。泛微 OA e-cology 是否支持标准授权码流必须以现场版本和厂商文档验证为准，不能在蓝图中默认假设。

```text
用户通过授权页完成授权；
AI 平台只持有 access_token + refresh_token 的加密引用；
不托管用户密码；
业务权限由目标系统基于用户授权身份自行判断；
refresh_token 必须加密存储、设置有效期、支持吊销；
授权过期、吊销或刷新失败时，任务进入 waiting_reauth / failed，不能切换成管理员服务账号。
```

##### 模式二：Vault 用户凭证金库模式

适用系统：不支持标准 OAuth2，但允许用户账号登录或接口登录的系统，例如部分老旧 ERP、MES、财务系统或厂商 SDK。Vault 模式是 legacy system exception，不是默认推荐模式。

```text
用户录入自己在该业务系统中的账号密码；
系统明确告知“密码加密存储，仅用于系统代理调用”；
密码加密存入 Credential Vault，只保存 credential_ref；
AI 调用时按需换取短期 session token / Cookie / SDK session；
session token 用完即弃，不持久存储；
业务权限仍由目标系统基于该用户账号判断。
```

安全要求：

```text
Vault 密码必须加密存储，密钥管理必须独立于数据库；
优先 KMS / Vault / HSM / 操作系统密钥服务，并采用 envelope encryption；
Credential Vault 访问必须有审计日志；
验证凭证有效性只能产生有效/失效状态，不得把错误响应中的敏感内容写入日志；
不得为了提升成功率把多个用户共享为一个部门账号或管理员账号；
不得因用户未绑定或凭证失效，自动降级为服务账号代理执行。
```

##### 模式三：系统级凭证 + Policy 控制

适用系统：没有用户级账号透传能力、以 app_key/app_secret、平台 token、SDK session 或设备级认证为主的系统，例如部分视频平台、IoT 平台、厂商开放平台、系统健康检查和公开字典同步。

```text
Capability 必须声明 execution_identity=system_scope；
必须声明可访问的设备、区域、通道、数据类型和风险等级；
Policy Guard 根据用户、部门、角色、目标资源、操作类型做二次控制；
系统级凭证不得作为 user_delegated Capability 的 fallback；
所有 system_scope 调用必须进入审计，并明确是系统级身份执行。
```

统一抽象：

```text
IdentityMapping：记录 ai_user_id、target_system、execution_identity、bind_mode、external_principal_id、resource_scope / account_domain / tenant / org / device_domain、status 等身份映射元数据；必须支持同一用户在同一目标系统下的多账套、多组织、多租户、多资源域和多设备域映射；具体字段定义、唯一性约束和索引策略见 `security_identity_binding_and_credential_spec.md`；
CredentialBinding：描述 Capability / 用户 / 任务与凭证引用的绑定；
SecretProviderPort：只暴露按引用解析、瞬时注入、轮换、吊销、验证接口；
Capability Gateway：负责在执行前进行绑定检查和凭证注入；
Policy Guard：负责禁止未绑定、绑定失效、越权降级和服务账号绕权。
```

#### 7.4.7 账号绑定与凭证健康状态

账号绑定状态不是一次性配置，必须被持续治理。

```text
active：绑定存在，最近一次验证有效；
expired：OAuth token 过期、refresh 失败、Vault 凭证登录失败或目标系统提示密码失效；
unbound：用户尚未完成绑定；
revoked：用户主动解绑或管理员手动解绑；
verification_failed：系统验证失败，原因需脱敏记录；
admin_review_required：系统级凭证、厂商 token、代理执行或异常绑定需要管理员复核。
```

健康检查边界：

```text
Phase 1：只做执行前绑定状态检查和 Mock 凭证有效性判断；
Phase 2：接入真实 Adapter 时提供基础凭证验证和管理员状态总览；
Phase 3：再做 OAuth2 自动续签、定期健康检查、批量失效通知和凭证轮换策略。
```

Phase 1 可以先采用简单 SecretProvider 适配 `.env` / 本地加密配置 / Docker Secret；Phase 2 接真实系统前必须替换为正式 Secret 管理方案，并补齐轮换、验证和审计能力。

### 7.5 Admin Console：治理后台边界与账号绑定管理

Admin Console 是治理入口，不是生产执行入口。它负责管理、观察、审批、审计和回滚；真实业务动作仍必须回到 Runtime / Capability Gateway。

#### 7.5.1 Admin Console 能力分层

Admin Console 功能必须按“主链上线前提 / 部门试点必需 / 后期增强”分层，避免运营辅助功能拖慢 Runtime 主链。

| 层级 | 能力 | 定位 |
|---|---|---|
| A. Runtime 主链上线前提 | Capability Registry 基础管理、Policy 基础配置、Trace 查询、Identity Binding 状态查看与手动解绑/重置、基础用户/角色管理、系统健康状态 | 没有这些，主链无法运维 |
| B. 部门试点运营必需 | 绑定状态总览、凭证健康看板、Capability 发布审批、Workflow / Skill 版本管理、失败任务复盘、审计导出 | 试点扩展到真实部门前必须补齐 |
| C. 后期增强 | Capability Demand Board、Memory 治理工作台、自动评测看板、Skill CI/CD 全流程管理、运营分析与使用率统计 | 平台化运营能力，不阻塞 Phase 1 主链 |

#### 7.5.2 账号绑定管理

账号绑定管理（Identity Binding Management）是 Admin Console 的正式功能板块，不应散落在各个 Adapter 配置页中。它负责管理员和用户如何建立、查看、验证、解绑 AI 平台用户与目标业务系统账号、系统级凭证或厂商 token 之间的绑定关系。

管理员侧能力：

```text
绑定状态总览：按目标系统、部门、用户、状态查看已绑定 / 未绑定 / 绑定失效 / 已解绑 / 待复核；
目标系统配置：查看每个系统支持的 bind_mode、execution_identity、Adapter 状态和待验证约束；
批量导入账号映射：支持从 HR 系统同步或 Excel 导入 external_principal_id 映射；
绑定引导通知：对未绑定用户批量发送 SDUI 绑定入口或企业微信/钉钉/站内通知；
凭证健康状态：查看 OAuth token 是否过期、refresh 是否失败、Vault 凭证是否验证失败、厂商 token 是否失效；
手动管理：对指定用户执行解绑、重置绑定、重新发送授权链接、标记需重新验证；
审计查看：查看绑定创建、解绑、验证、失败、管理员操作记录，但不得查看密码或 token 明文。
```

管理员操作边界：

```text
管理员可以导入 ai_user_id + target_system + external_principal_id 的账号映射；
管理员不得在普通后台页面查看、复制、导出用户密码、refresh_token、access_token、Cookie；
管理员不得因某用户未绑定，直接替该用户配置管理员服务账号执行；
管理员重置绑定只改变绑定状态和凭证引用，不得绕过用户重新授权或重新录入要求；
批量导入只解决账号对应关系，不自动证明该用户拥有目标系统权限。
```

#### 7.5.3 用户自助能力

用户侧解决“首次使用某个业务系统能力时如何顺畅完成绑定”的体验问题。

```text
首次触发目标系统 Capability 时，如未绑定，系统返回 SDUI operator_handback_card；
卡片说明为什么需要绑定、绑定哪个系统、绑定后将以哪个业务系统账号或系统授权范围执行；
OAuth2 / SSO 系统：用户点击授权页完成授权，无需输入业务系统密码；
Vault 系统：用户录入业务系统账号密码，并明确告知“密码加密存储，仅用于系统代理调用”；
用户可查看自己已绑定系统列表、绑定方式、状态、最近验证时间；
用户可主动解绑，解绑后相关业务系统能力不可继续以该用户身份执行。
```

用户交互边界：

```text
绑定卡片不得展示 token、Cookie、Vault 密钥引用等内部信息；
Vault 密码输入框不得回显，不得进入前端普通日志、埋点、Trace 或 LLM 上下文；
绑定成功后只返回状态和下一步操作，不返回凭证内容；
绑定失败应返回可理解原因和重试入口，不应暴露目标系统原始错误堆栈。
```

#### 7.5.4 与 Capability Gateway 的关系

Admin Console 负责“管理绑定关系”，Capability Gateway 负责“执行前强制检查”。两者不能互相替代。

```text
Admin Console 可以创建、导入、解绑、重置 IdentityMapping；
Capability Gateway 每次执行前仍必须读取最新绑定状态；
Policy Guard 对未绑定、绑定失效、模式不匹配、越权降级做最终拒绝或引导；
任何 Workflow / Skill / Tool / Adapter 都不得绕过 Gateway 自行读取 Vault 或 token。
```

### 7.6 Capability Demand Board：能力需求看板

系统不仅要调度已有能力，还要持续发现“应该新增哪些能力”。`no_capability_found`、重复追问、失败聚类、用户取消原因和管理员标记都应进入能力需求看板，帮助管理员决定下一批应该建设的 Workflow / Tool / Adapter / Query Capability。

数据来源：

```text
no_capability_found Task；
Capability Preselector 低置信度请求；
用户重复提出但系统无法完成的请求；
执行失败聚类；
用户反馈“没有我要的功能”；
管理员手动标记。
```

看板输出：

```text
Top unmet intents：最高频未满足意图；
Top missing systems：最常被问但未接入的系统；
Top departments：需求最强的部门；
Top requested workflows：最常被请求的流程；
Top failed capabilities：失败率最高的能力；
Suggested capability backlog：建议建设的能力待办。
```

Phase 建议：

```text
Phase 1：只记录 no_capability_found 和 capability_miss_reason。
Phase 2：Admin Console 提供基础统计看板。
Phase 3+：结合失败聚类和业务 owner 审批，形成能力建设 backlog。
```

Capability Demand Board 不需要一开始依赖复杂 AI。它首先是 Trace 与 Task 终态的统计视图，但会极大提升能力扩展的方向感。

---

## 8. Execution Fabric 设计

Execution Fabric 是“脏活累活层”，负责把受控能力落到真实系统。

### 8.1 API Adapter

优先级最高。适用于有正式 API 的系统，如 OA、ERP、HR、MES、财务系统。

要求：

```text
Schema 明确；
错误码映射；
身份透传；
请求审计；
响应脱敏；
超时重试；
版本管理。
```

### 8.2 DB Gateway

只在没有 API 或报表场景下使用。

硬边界：

```text
优先使用 API 和用户身份透传，由原系统鉴权；
只有没有 API 或报表需求明确时，才启用 DB Gateway；
只能访问业务负责人和 DBA 审批过的只读视图；
默认禁止 LLM 面向生产核心表自由生成 SQL；
禁止 DDL / DML；
必须按用户身份做行级、字段级、范围级限制；
若目标 DB / 报表视图无法承接用户级身份，则必须由 AI 平台 Policy Guard 按用户、部门、角色、数据范围做等价访问控制，并在审计中明确该能力不是由原系统用户级鉴权直接完成；
必须限制返回行数和查询时间；
必须做字段脱敏和审计；
不能绕过原系统权限扩大访问。
```

### 8.3 RPA / Browser Worker

作为无 API、无稳定数据库接口时的兜底，不是首选。

要求：

```text
脚本版本化；
页面变化检测；
截图证据；
失败自动隔离；
写操作确认；
高风险动作禁止自动执行；
优先使用用户本人账号；
代理账号必须登记、限权、绑定真实发起人并单独审计；
不得使用高权限代理账号替普通用户绕过原系统权限。
```

### 8.4 Local Worker

用于本地文件、Office、打印机、网络诊断、桌面应用、有限 Shell 等场景。Local Worker 与服务端 API Adapter 的安全模型不同：它运行在用户 PC 上，是不完全受控端点，必须单独定义信任模型。

原则：

```text
本地 Worker 必须由用户授权；
默认最小权限；
读取文件要弹窗确认；
Shell 默认关闭；
危险命令硬禁止；
执行证据回传 Trace。
```

信任模型：

```text
Local Worker 必须以主动注册方式接入 Capability Gateway；
Local Worker 与 Gateway 之间应采用持久连接或轮询拉取，不暴露可被任意访问的本地执行端口；
Local Worker 只接受来自合法 Gateway 的签名任务；
Local Worker 本地必须维护预注册 Capability 白名单；
Local Worker 不接受任意命令文本，不作为通用命令执行端点；
每个本地 Capability 必须声明权限、风险等级、用户确认方式和证据回传要求；
Worker 失联、版本过旧、签名校验失败、能力清单不一致时，Capability Gateway 必须拒绝下发任务。
```

Local Worker 注册、设备绑定、连接方式、签名校验、能力白名单、用户侧确认和证据回传细节见 `local_worker_spec.md`。

### 8.5 IoT / Video Gateway

用于海康监控、UWB、传感器、PLC、门禁等设备和视频系统。IoT / Video Gateway 的职责是把高频、噪声大、协议私有的物理信号转换为低频、结构化、可审计的业务事件或受控 Capability。

原则：

```text
先查询，后控制；
设备控制默认高风险；
关键设备控制必须人工确认或管理员审批；
LLM 不得直接操作设备；
LLM 不得直接订阅 MQTT topic、设备原始流、视频帧流或私有协议报文；
所有设备操作必须经 IoT / Video Gateway 标准化。
```

高频物理信号硬边界：

```text
UWB 定位腕带、门禁、摄像头事件、传感器等依赖高频广播、私有协议或物理信号的硬件，必须在 IoT Gateway / Edge Gateway 侧完成限流、状态去重、死区过滤、异常抖动消除和事件语义化；
Agent Runtime 只允许接收低频结构化业务事件，例如“进入电子围栏”“离开电子围栏”“长时间静止”“设备离线”“告警触发”；
严禁将原始高频坐标流、设备心跳流、视频帧流或私有协议报文直接引入 LLM 上下文、Memory、Trace 摘要或主执行链；
所有 IoT / 视频输入必须先经 IoT Gateway / Video Gateway 转换为受控 Capability 或结构化事件。
```

IoT Capability 字段、设备分级、消息接入、限流去重、死区过滤、事件语义化和控制策略见 `iot_gateway_spec.md`。

### 8.6 MCP Gateway

MCP 是连接协议，不是信任协议。

要求：

```text
MCP Server 必须注册；
每个 MCP Tool 必须声明 schema、权限、风险、owner；
MCP 工具变更不得自动上线；
MCP 调用必须经 Policy Guard；
MCP 输出必须经过安全检查和脱敏。
```

MCP 与 A2A 的边界：

```text
MCP / OpenAPI / SDK Adapter：工具与数据接入协议。
A2A：Agent 与 Agent 互操作协议。
二者不是互相替代关系，必须分别通过 CapabilityGatewayPort 与 AgentInteropPort 接入。
```

---

### 8.7 DB Gateway 业务流程约束

DB Gateway 不能成为“LLM 自由查库入口”。它只能执行已治理、已注册、已审批的查询能力。

标准流程：

```text
用户请求
→ Capability Preselector 优先匹配 API / 报表 / 已注册查询 Capability
→ 若命中 DB Query Capability，则通过 Capability Gateway 执行
→ Policy Guard 检查用户、视图、字段、行级范围、返回规模
→ DB Gateway 执行参数化查询或受控模板查询
→ 结果脱敏、限行、审计
→ Evaluator 校验结果结构
```

未注册查询能力时：

```text
不得临时让 LLM 自由生成 SQL 查询生产库；
应转为“申请新增查询能力”或“管理员配置报表视图”；
必要时由 DBA / 业务负责人创建只读视图和查询模板。
```

---

### 8.8 RPA 能力生命周期

RPA 不是普通 Tool，而是易受界面变化影响的脆弱执行能力，必须有独立生命周期。RPA 只能作为无 API、无稳定数据库接口时的兜底能力，不得成为绕过业务系统权限的捷径。

蓝图级原则：

```text
RPA 能力必须版本化；
发布前必须经过沙箱验证和风险评级；
执行时必须保留截图或等价证据；
页面漂移、选择器失效、登录失败或疑似副作用不明时必须隔离能力；
写操作必须预览和确认；
代理账号必须登记、限权、绑定真实发起人并单独审计；
不得使用高权限代理账号替普通用户绕过原系统权限。
```

RPA 生命周期、字段、页面漂移检测、截图基线、隔离策略、失败状态和重录流程见 `rpa_worker_spec.md`。

### 8.9 IoT / 设备操作业务分级

IoT 能力必须按物理风险分级，不能把“查询摄像头截图”和“控制设备动作”混为一类。

```text
设备数据查询：低风险，可自动执行。例如状态、温湿度、位置、摄像头截图。
设备记录调取：中风险，需要审计。例如录像回放、门禁记录、轨迹查询。
设备状态变更：中高风险，需要用户确认或管理员审批。例如调整参数、打开/关闭非关键设备。
关键设备控制：高风险，默认禁止或必须双重审批。例如 PLC 控制、停机、开门、危险区域设备动作。
```

硬边界：

```text
LLM 不得直接控制设备；
设备控制必须通过 IoT Gateway；
关键设备控制必须保留人工责任人；
设备执行结果必须有可审计证据；
原始高频物理信号必须在边缘侧完成限流、去重、过滤和事件语义化后，才能进入 Runtime 主链。
```

IoT Capability 字段、设备分级、消息接入、控制等级、双重确认、紧急停止和物理风险策略见 `iot_gateway_spec.md`。

## 9. Policy、Audit、Evaluation

### 9.1 Policy Guard

Policy Guard 在动作执行前进行决策。

典型结果：

```text
allow：允许执行。
confirm：需要用户确认。
approve：需要管理员或业务负责人审批。
deny：拒绝执行。
escalate：转人工。
```

风险等级建议：

```text
L0：闲聊、解释、格式化。
L1：个人可见只读查询。
L2：部门级只读汇总。
L3：创建草稿、生成文件、准备提交。
L4：提交审批、发送通知、批量导出、修改业务数据。
L5：删除关键数据、绕过权限、泄露凭据、控制危险设备，默认禁止。
```


#### 9.1.1 账号绑定预检底线

Policy Guard 必须把账号绑定状态作为执行前硬约束。凡是 Capability 声明 `execution_identity=user_delegated` 或 `binding_required=true`，执行前必须验证当前 `ai_user_id + target_system` 的 IdentityMapping。凡是 Capability 声明 `execution_identity=system_scope`，不得要求用户绑定，但必须按用户、部门、角色、资源范围和操作类型执行 Policy 检查，并明确禁止作为 user_delegated 失败时的 fallback。

强制规则：

```text
已绑定且 status=active：允许继续进入常规 Policy 风险判断；
未绑定 status=unbound 或无记录：阻止执行，返回 SDUI operator_handback_card，引导用户完成绑定；
绑定失效 status=expired / verification_failed：阻止执行，引导重新授权或重新录入凭证；
已解绑 status=revoked：阻止执行，提示用户重新绑定；
bind_mode / execution_identity 与 Capability 要求不匹配：阻止执行，提示管理员修正系统配置；
system_scope 能力缺少资源范围或 Policy 约束：阻止执行，提示管理员补齐配置；
目标系统返回 401 / 403：不得绕权、不得自动切换高权限账号，按 permission_denied 或 identity_binding_expired 处理。
```

绝对禁止：

```text
不得因为用户未绑定账号而自动切换为管理员服务账号执行；
不得因为 OAuth 授权过期而改用系统级 Token 执行用户动作；
不得因为 Vault 凭证验证失败而改用共享账号、部门账号或 RPA 管理员账号；
不得让 LLM 决定是否跳过绑定检查；
不得把绑定失败伪装成普通系统错误。
```

用户体验要求：

```text
未绑定时返回“需要绑定账号”的可操作卡片，而不是 unknown error；
绑定失效时返回“需要重新授权 / 重新验证凭证”的可操作卡片；
卡片应包含 target_system、绑定方式说明、风险提示和继续绑定按钮；
CLI / 语音等不支持完整 SDUI 的入口必须提供 fallback_text 和可访问的绑定链接或转人工路径。
```

审计要求：

```text
每次绑定预检都应记录 target_system、capability_id、ai_user_id、bind_status、policy_decision；
审计日志不得记录密码、token、Cookie、session token 或 Vault 解密内容；
因未绑定或绑定失效而阻止执行，应进入 Trace 和 Admin Console 统计，用于绑定运营和能力上线准备。
```

### 9.2 Trace / Evidence / Raw Payload 分层

Trace 不能变成新的敏感数据黑洞。

分三层：

```text
Trace：操作摘要、步骤、风险判断、结果摘要，默认用于审计和调试。
Evidence：截图、文件、原始凭证类证据，加密存储，按权限访问。
Raw Payload：原始请求/响应，默认不保存；确需保存时必须脱敏、加密、设置保留期。
```

红线：

```text
日志不得记录明文密码、Token、Cookie；
不得记录完整身份证号、敏感财务明细、未经脱敏的员工隐私；
不得把 SECRET 级内容注入 LLM 上下文。
```

### 9.3 Evaluation

Evaluation 分为运行时校验和治理评测，也分为确定性校验和语义评测。不能把所有请求都交给 LLM 语义评测，否则会增加延迟、成本和不稳定性，也会抵消 Fast Path 的价值。

#### 9.3.1 Runtime Evaluator

Runtime Evaluator 面向单次任务执行结果，回答：这次任务是否完成、结果是否可信、是否需要重试、追问、转人工。

Runtime Evaluator 分两类：

```text
Deterministic Evaluator：
Schema 校验、必填字段、枚举、状态码、流程号、文件是否生成、业务规则、权限与脱敏检查。
特点：代码/规则执行，低延迟，可复现，优先使用。

Semantic Evaluator：
结果是否完整、是否符合用户意图、摘要是否可信、RAG groundedness、文档质量、解释是否清晰。
特点：可使用 LLM-as-judge，有成本和延迟，不用于低风险 Fast Path 默认链路。
```

Capability / Workflow Step 应声明评测模式：

```yaml
evaluation_mode: deterministic | semantic | both | none
```

建议默认规则：

```text
低风险只读查询：deterministic。
Known Read Fast Path：deterministic，除非显式声明需要 semantic。
写操作：both。
文档生成 / 总结 / 语义匹配：semantic 或 both。
高风险能力：both + 人工确认/审批。
```

#### 9.3.2 Governance Evaluation

Governance Evaluation 面向能力发布、版本变更和治理运营，回答：这个 Skill / Workflow / Prompt / Tool 是否可以上线、灰度或回滚。

```text
单元测试：普通代码和工具适配器。
Golden Task 测试：核心业务场景闭环。
Agent Trace 评测：路由、规划、执行链路质量。
Skill 发布评测：Skill 上线前测试集。
安全攻击评测：Prompt Injection、越权、敏感信息、工具滥用。
回归测试：能力版本升级后是否破坏既有任务。
```

Phase 4 以后，Governance Evaluation 应支持 LLM-as-judge 自动化，但必须保持边界：

```text
确定性优先：Schema、类型、权限、性能阈值、回归断言优先使用代码和规则 Evaluator。
LLM-as-judge 适用：语义完整性、事实一致性、RAG groundedness、用户体验、摘要质量、说明是否清晰。
不得替代人工最终审批：Skill 发布、Workflow 变更、高风险能力上线必须保留人工审批节点。
可审计：judge_prompt、rubric、judge_model、评分结果、失败样例必须进入 Evaluation Report 和 Trace。
可复现：同一发布批次应固定 judge prompt、judge model 和评分阈值。
```


---

## 10. Memory Fabric 与 Skill CI/CD

### 10.1 记忆分层与最小可用定义

Memory Fabric 是长期架构，不要求 Phase 1 全量实现。蓝图保留六层记忆模型，但必须明确最小可用层，避免把 Memory Governance 做成一期阻塞项。

长期六层：

```text
Session Memory：当前会话。
User Profile Memory：用户偏好、常用任务、表达风格。
Episodic Memory：历史任务摘要和关键事件。
Semantic Memory：企业术语、知识、制度、模板。
Procedural Memory：Skill、流程经验、操作模式。
Knowledge Vault：API 端点、字段枚举、敏感配置，只能受控使用。
```

阶段化启用：

```text
Phase 1 必须实现：
1. Session Memory：当前任务/当前会话上下文；
2. 基础 Semantic/System Knowledge：企业术语、能力说明、Mock 系统说明、少量制度模板。

Phase 1 只保留结构位，不做完整治理：
1. User Profile Memory；
2. Episodic Memory；
3. Procedural Memory；
4. Knowledge Vault。

Phase 2 引入：
- User Profile Memory：常用部门、常用查询、用户偏好；
- Semantic Memory 增强：制度、字段、报表口径、业务术语。

Phase 3/4 引入：
- Episodic Memory：历史任务摘要；
- Procedural Memory：从 Trace 到 SkillDraft 的经验沉淀；
- Knowledge Vault：API 文档、字段枚举、接口约束等受控知识。
```

Memory Governance 的完整审批、脱敏、升级、回滚机制与 Skill CI/CD 深度绑定，不作为 Phase 1 主链前提。

### 10.2 Memory Scope

每条记忆必须有作用域：

```text
personal：仅本人使用。
department：部门共享，需审批。
enterprise：企业级共享，需治理流程发布。
system：系统级知识，需管理员维护。
```

防污染原则：

```text
个人记忆不得自动升级为部门或企业记忆；
一次成功经验不得自动变成企业级 Skill；
错误记忆必须可查看、可修改、可删除、可过期。
```

### 10.3 Skill CI/CD 铁律

正式铁律：

```text
Trace 可以生成 Skill 草稿；
Skill 草稿不能自动上线；
上线必须经过测试、风险评级、审批、灰度、发布、监控和回滚机制。
```

Skill 生命周期：

```text
draft
→ candidate
→ tested
→ reviewed
→ published
→ monitored
→ deprecated / quarantined / rollback
```

Skill 必须包含：

```text
trigger
input_schema
output_schema
procedure
risk_level
scope
owner
source_trace_id
test_cases
health_score
version
rollback_strategy
```

Skill 边界必须写死：

```text
Skill 是声明式经验包，不是自由脚本；
Skill 只能引用已注册 Capability；
Skill 内部步骤不得绕过 Gateway / Policy / Trace；
个人 Skill 不得自动晋升为部门或企业 Skill；
企业级 Skill 必须通过 Governance Evaluation 和人工审批。
```

---

### 10.4 Workflow / Skill 版本锁定

Task 启动后必须锁定所使用的 Workflow / Skill / Tool / Prompt / Policy 的具体版本，避免执行中途能力变更造成业务风险。

规则：

```text
Task 启动时绑定能力版本；
执行中不得自动切换版本；
新版本只对新 Task 生效；
紧急停用时，正在执行的高风险 Task 应暂停或转人工；
用户确认时展示的预览内容必须与实际执行版本一致。
```

禁止：

```text
用户按 v1 流程确认后，系统执行 v2 流程；
Workflow 发布新版本后自动影响已在执行中的任务；
Skill 更新后改变已确认任务的执行字段。
```

---

### 10.5 Memory / Skill 进化前脱敏流程

Trace 不得直接进入长期 Memory 或 Skill 草稿。所有进化副链都必须先经过敏感检测、脱敏和作用域判断。

标准流程：

```text
Trace / Evidence
→ 敏感检测
→ 脱敏 / 摘要
→ 记忆候选 / Skill 草稿候选
→ scope 判断
→ Policy / Governance Evaluation
→ 写入 Memory 或进入 Skill CI/CD
```

Memory 候选只能保留：

```text
用户偏好；
任务摘要；
通用业务经验；
失败原因分类；
可复用但不含敏感原文的上下文。
```

Skill 草稿只能保留：

```text
通用步骤；
参数占位符；
已注册 Capability 引用；
风险等级；
测试用例；
成功标准；
来源 Trace 引用。
```

禁止进入长期记忆或 Skill 草稿的内容：

```text
明文凭证；
完整身份证号；
完整财务明细；
未经脱敏的员工隐私；
生产系统接口密钥；
真实业务附件原文；
敏感截图原图；
可复现越权路径的细节。
```

---


### 10.6 Async Evolution Queue：进化副链异步基础设施

进化副链不能阻塞主执行链，也不能依赖“后台脚本随便跑”。Memory 更新、Skill 草稿生成、失败聚类、Trace 补全、能力需求聚合、凭证健康检查和通知发送等，都应通过明确的异步任务端口承载。

蓝图级原则：

```text
JobQueuePort：提交、消费、重试、死信、查询异步任务；
EventBusPort：发布领域事件，如 task.finished、capability.missed、skill.drafted；
Phase 1 异步能力必须通过 JobQueuePort 隔离，不把任何具体队列实现写死为不可替换底座；
L0 单机小规模试点可采用 FastAPI BackgroundTasks / in-process executor，仅用于可丢失、可重试、低风险任务；
L1 部门试点或需要更可靠执行时，可启用 Redis + ARQ 作为候选实现；
L2 生产规模或高可靠要求下，评估 Celery、Dramatiq、企业消息队列或 Temporal；
任何队列都不得替代业务 Workflow 状态机，业务状态仍以 PostgreSQL 持久化为准；
异步失败不得影响已经成功返回用户的主链结果，但必须进入运维和治理看板。
```

异步任务类型、字段、重试策略、幂等键、死信处理、L0/L1/L2 选择标准和队列实现验证见 `phase0_architecture_freeze_and_mvp_spec.md`。

## 11. 解耦与可插拔设计

### 11.1 Ports and Adapters 原则

系统核心不依赖具体技术实现，只依赖端口。

```text
Core Domain：Task、Capability、Policy、Trace、Skill、Memory 等核心对象与规则。
Ports：核心层需要的抽象接口。
Adapters：具体实现，如 Qwen / vLLM / OpenAI-compatible 网关、instructor、PydanticAI、pgvector、Playwright、MCP、ERP API。
```

### 11.2 必须保留的端口

```text
ModelProviderPort
LLMProviderPort
StructuredOutputPort
AgentOrchestrationPort
CapabilityRegistryPort
CapabilityGatewayPort
PolicyEnginePort
WorkflowEnginePort
ToolExecutionPort
MemoryPort
TracePort
EvaluationPort
SecretProviderPort
CredentialBindingPort
IdentityMappingPort
HumanGatePort
DocumentWorkerPort
RPAWorkerPort
LocalWorkerPort
IoTConnectorPort
VectorStorePort
EventBusPort
JobQueuePort
ObjectStoragePort / ArtifactStoragePort
AgentInteropPort
```

### 11.3 可替换矩阵

| 当前建议 | 未来可替换 | 端口 |
|---|---|---|
| 候选内网模型（Qwen / DeepSeek / GLM / 其他 OpenAI-compatible 本地模型） | 其他内网模型 / 云端模型 / 厂商模型网关 | ModelProviderPort |
| OpenAI SDK + instructor | PydanticAI / OpenAI Agents SDK / Microsoft Agent Framework / 自研 Runner | LLMProviderPort / StructuredOutputPort / AgentOrchestrationPort |
| 自研轻量状态机 | LangGraph / Temporal / DBOS | WorkflowEnginePort |
| PostgreSQL + pgvector >= 0.8.2 | Qdrant / Milvus / Weaviate | VectorStorePort |
| L0 BackgroundTasks / in-process executor；L1 Redis + ARQ | Celery + RabbitMQ / Dramatiq / SAQ / RQ / NATS / Temporal | JobQueuePort / EventBusPort |
| Playwright | 厂商 RPA / GUI Agent | RPAWorkerPort |
| MCP / OpenAPI / SDK Adapter | 内部工具协议 / 新工具协议 | CapabilityGatewayPort |
| A2A | 内部 Agent 协议 / 远程 Agent 适配器 | AgentInteropPort |
| S3-compatible Object Storage 抽象 | MinIO / Ceph / NAS S3 Gateway / 企业对象存储 / 文件服务器 | ObjectStoragePort |

### 11.4 禁止依赖规则

```text
UI 不得直接调用 Tool / Workflow / Adapter。
Runtime 不得直接调用 API / DB / RPA / Shell / IoT。
LLM 框架不得直接调用生产 MCP / API / DB / Shell / RPA / IoT。
Workflow 不得绕过 Policy Guard 调用工具。
Workflow / Skill 内部高风险步骤不得因整体流程已放行而自动放行。
Skill 不得直接保存生产数据到 Memory。
Adapter 不得直接发布 Skill。
Admin Console 不得直接操作业务系统生产数据库；对 AI 平台配置库的修改必须通过后端管理 API、Registry / Lifecycle / Policy 服务完成，并形成审计事件。
```

---

## 12. 技术选型与自研边界（v3.2.4 冻结基线）

本章只定义“Phase 0 / Phase 1 当前采用基线”和“后续可替换路线”。除蓝图明确声明的安全底线（例如 pgvector >= 0.8.2）外，具体小版本、部署参数、兼容性验证、许可证结论和验收测试，由 Phase 0 规格 / ADR 冻结。

核心原则：

```text
现阶段优先成熟、稳定、AI 代码生成准确率高、1 人团队可维护；
后续阶段再根据规模、验证结果和真实接入约束评估升级或替换；
任何第三方框架都不能替代 Agent Runtime、Capability Gateway、Policy Guard、Trace、Identity Binding 和 Capability Control Plane。
```

### 12.1 当前默认技术栈（Phase 0 / Phase 1）

#### 12.1.1 前端

| 模块 | 当前选择 | 说明 |
|---|---|---|
| 前端框架 | React 18 | 与 Ant Design 5.x / ProComponents 2.x 组合成熟，AI 生成代码命中率高 |
| 构建工具 | Vite | 简单、快、适合内网 SPA 管理后台 |
| 包管理器 | pnpm | 前端依赖管理与 workspace 基线 |
| 语言 | TypeScript strict mode | 减少 AI 生成代码的隐性错误 |
| UI 组件 | Ant Design 5.x | 企业后台成熟路线 |
| 后台增强组件 | ProComponents 2.x | ProTable、ProForm、ProLayout 等直接服务 Admin Console |
| 路由 | React Router | 简单成熟，AI 生成稳定 |
| 服务端状态 | TanStack Query | 适合 API 查询、缓存、刷新、失败重试 |
| 本地状态 | Zustand | 简洁，适合 UI 状态、会话状态 |
| API 客户端生成 | Orval | 从 FastAPI OpenAPI 生成类型安全客户端 |
| Mock | MSW | 配合 Orval / 前端联调 |
| 基础 PWA | vite-plugin-pwa 候选 | 可用于 manifest、可安装入口、基础 Service Worker 缓存；不等同于采用 Next.js，也不强制 Phase 1 启用 |
| AI 对话组件 | Ant Design X 候选 | 可用于对话界面，但不绑定系统 SDUI 协议 |

最终结论：

```text
Phase 1 主前端 = React 18 + Vite + Ant Design 5.x + ProComponents 2.x。
```

Next.js App Router 不作为 Phase 1 主线。该决策不是基于 SEO 判断，而是因为当前系统核心服务端能力必须收敛在 Python Runtime / Capability Gateway / Policy Guard 中；Next.js 会引入额外服务端边界、Server/Client Component 心智和工程复杂度。Next.js 保留为后续独立用户门户、复杂 PWA、公网门户或多租户门户候选方案。基础 PWA（manifest、可安装入口、基础 Service Worker 缓存）可在 Vite 体系内通过 vite-plugin-pwa 早期启用，不需要等待 Next.js。

本蓝图不将原生 iOS / Android App 作为阶段主线交付端。移动端优先采用 H5 / PWA / 企业微信或钉钉内嵌页面实现。若未来确需原生 App，应作为独立产品线重新立项，单独评估打包、签名、分发、设备兼容和运维流水线。

React 19 与 Ant Design 6 暂不作为 Phase 1 默认基线。原因不是新版本不好，而是 Ant Design 5.x + ProComponents 2.x 更成熟，Claude Code / Codex 生成准确率更高，对 1 人团队更稳。

#### 12.1.2 后端

| 模块 | 当前选择 | 说明 |
|---|---|---|
| 语言 | Python 3.12 | 稳定，AI/LLM 生态成熟 |
| 包管理器 | uv | Python 依赖、虚拟环境、lockfile 与工具运行基线 |
| Web 框架 | FastAPI | OpenAPI 自动生成，适合前后端类型联动 |
| Schema | Pydantic v2 | 数据校验、结构化输出、DTO 基础 |
| ORM | SQLAlchemy 2.0 | 成熟、类型支持好 |
| PostgreSQL async 驱动 | asyncpg | SQLAlchemy 2.0 async PostgreSQL 运行依赖 |
| 迁移 | Alembic | 数据库迁移标准选择 |
| HTTP Client | httpx | async 友好，适合 Adapter / LLM 网关调用 |
| 配置 | pydantic-settings | 类型化配置 |
| 测试 | pytest + pytest-asyncio | 后端测试基础 |
| 代码质量 | Ruff + mypy | 适合 AI 生成代码后的自动检查 |

最终结论：

```text
Phase 1 后端 = Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2.0 + Alembic。
```

#### 12.1.3 LLM / 结构化输出

| 模块 | 当前选择 | 说明 |
|---|---|---|
| LLM API | OpenAI SDK | 对 OpenAI-compatible 本地网关兼容最好 |
| 结构化输出 | instructor | 成熟、轻量、Pydantic 友好 |
| Schema | Pydantic v2 | 与 FastAPI 后端一致 |
| 抽象层 | LLMProviderPort / StructuredOutputPort | 保证未来可替换 |
| 本地模型接入 | OpenAI-compatible API | 适合 vLLM / Xinference / LiteLLM / 自研网关 |

最终结论：

```text
Phase 1 默认 = OpenAI SDK + instructor + Pydantic v2 Schema。
```

LLM 只负责 Intent、CapabilityRef、PlanDraft、ResponseEnvelope、参数抽取和解释说明；不得直接调用生产工具、读取凭证或绕过 Capability Gateway。

PydanticAI 作为 Phase 0 验证项，不作为 Phase 1 强依赖。验证通过后可在 Phase 2 作为 LLM Orchestration Adapter 引入；引入前后都必须通过 LLMProviderPort / StructuredOutputPort / AgentOrchestrationPort 隔离。

#### 12.1.4 异步任务

| 模块 | 当前选择 | 说明 |
|---|---|---|
| 抽象层 | JobQueuePort | 防止未来替换困难，不把具体队列写死为底座 |
| L0 实现 | FastAPI BackgroundTasks / in-process executor | 单机小规模试点可用，仅限可丢失、可重试、低风险任务 |
| L1 候选 | Redis + ARQ | 部门试点或需要更可靠异步执行时启用；ARQ 不作为长期不可替换底座 |
| L2 候选 | Celery / Dramatiq / 企业消息队列 / Temporal | 生产规模、高可靠、长流程或复杂补偿需求明确后评估 |
| 不适用 | 核心 Workflow 状态机、长事务编排、审批流状态 | 这些状态仍落 PostgreSQL |

最终结论：

```text
Phase 1 异步能力通过 JobQueuePort 隔离。单机小规模试点可采用 FastAPI BackgroundTasks / in-process executor 作为 L0 实现；当出现导入、通知、健康检查、Trace 后处理、异步 Adapter 调用等需要更可靠执行的任务时，再启用 Redis + ARQ 作为 L1 实现。
ARQ 不作为长期不可替换底座，未来可替换为 Celery、Dramatiq、企业消息队列或 Temporal。
业务 Workflow 状态仍然落 PostgreSQL，由 Runtime / Gateway / Policy 主链驱动。
```

#### 12.1.5 数据、存储、认证与观测

| 模块 | 当前选择 | 说明 |
|---|---|---|
| 主数据库 | PostgreSQL | 统一业务数据、Trace、Registry、Policy、任务状态 |
| 向量能力 | pgvector >= 0.8.2 | Phase 1 数据量不大，没必要单独上 Milvus；不得使用低于 0.8.2 的版本 |
| 缓存 | Redis / Valkey | 会话、队列、短期缓存 |
| 对象存储 | S3-compatible Object Storage 抽象 | 不在蓝图锁死具体产品 |
| AI 平台登录 | 优先接企业现有 IAM / AD / LDAP / SSO | 不重复造身份系统 |
| 无现有 IAM 时 | Keycloak | 作为统一登录候选 |
| LLM Trace | Langfuse 自托管 | Prompt、LLM 调用、结构化输出、评测 |
| 通用 Trace | OpenTelemetry | Trace / Metrics / Logs 标准化 |
| 脱敏 | Redaction / Sanitizer 层 | 写入日志和 Trace 前统一处理 |

关键区分：

```text
IAM / Keycloak 解决“你是谁”；
Identity Binding 解决“你在目标业务系统里是谁 / 以什么系统级身份访问”；
Policy Guard 解决“这次 AI 能不能帮你做这件事”。
```

### 12.2 后续可替换 / 可升级路线

#### 12.2.1 前端升级路线

| 当前技术 | 后续候选 | 触发条件 |
|---|---|---|
| React 18 | React 19 | Ant Design / ProComponents 生态完全稳定后 |
| Ant Design 5.x | Ant Design 6.x | ProComponents 3.x 成熟、AI 生成质量稳定后 |
| Vite SPA | Vite + vite-plugin-pwa 基础 PWA | 需要移动端可安装入口、manifest、基础 Service Worker 缓存时，可早期启用 |
| Vite SPA + 基础 PWA | Next.js App Router | 出现独立门户、复杂 PWA、公网门户、多租户门户需求 |
| React Router | TanStack Router | 前端路由和类型约束复杂到 React Router 难以维护时 |
| Ant Design X 候选 | 自研 SDUI Renderer | SDUI 组件复杂度超过 Ant Design X 能力时 |

#### 12.2.2 LLM 框架升级路线

| 当前技术 | 后续候选 | 触发条件 |
|---|---|---|
| OpenAI SDK + instructor | PydanticAI | Phase 0 验证 Qwen / vLLM 兼容性通过 |
| instructor | 原生 JSON Schema / guided decoding | 本地模型结构化输出能力稳定后 |
| 自研轻量 Runtime | LangGraph 局部引入 | 复杂多步骤、可恢复、人机协同任务明显增多 |
| LLMProviderPort | LiteLLM / 自研 Model Gateway | 多模型、多供应商、路由、限流、成本统计需求增强 |

注意：即使后续引入 PydanticAI / LangGraph，也不能让它们直接调用生产工具。生产执行仍必须经过 Capability Gateway。

#### 12.2.3 异步与工作流升级路线

| 当前技术 | 后续候选 | 触发条件 |
|---|---|---|
| L0 BackgroundTasks / in-process executor | Redis + ARQ | 单机试点进入部门试点，出现导入、通知、健康检查、Trace 后处理、异步 Adapter 调用等更可靠异步需求 |
| Redis + ARQ | Celery / Dramatiq / SAQ / RQ / 企业消息队列 | 根据可靠性、维护状态、团队熟悉度和运维条件评估 |
| 自研状态机 + PostgreSQL | Temporal | 长流程、跨天任务、强恢复、复杂补偿需求明显出现 |
| PostgreSQL 状态表 | 专用 Workflow Engine | 审批流和业务流程复杂到自研状态机不可控时 |

注意：BackgroundTasks / in-process executor / ARQ / Celery / Temporal 都不能替代 Capability Gateway。它们只是任务执行和调度设施，不是权限边界。

#### 12.2.4 数据与检索升级路线

| 当前技术 | 后续候选 | 触发条件 |
|---|---|---|
| PostgreSQL + pgvector >= 0.8.2 | Milvus / Weaviate / Qdrant | 向量数据规模和检索并发明显上升 |
| PostgreSQL 全文检索 | OpenSearch / Elasticsearch | 日志、文档、审计检索规模变大 |
| Redis | Valkey | 企业开源策略或许可证策略需要 |
| S3-compatible 抽象 | MinIO / Ceph / NAS S3 Gateway / 企业对象存储 | Phase 0 完成许可证、部署和运维验证后 |
| 单 PostgreSQL | 读写分离 / 分库 | 数据规模或并发压力明显上升 |

#### 12.2.5 通道、语音与 IoT 升级路线

| 当前技术 | 后续候选 | 触发条件 |
|---|---|---|
| 文本 / 按钮 / SDUI 入口 | SenseVoice-Small / faster-whisper / ASR 语音入口 | 移动端语音助手、电话入口、低门槛操作或语音唤醒需求明确 |
| 无 IoT 消息基础设施 | EMQX / MQTT / 厂商 IoT Gateway | 设备状态订阅、视频平台事件推送、告警联动、实时设备消息接入需求出现 |

### 12.3 不在 Phase 1 默认主线的技术

| 技术 | 当前态度 |
|---|---|
| Next.js App Router | 不做 Phase 1 主前端 |
| 原生 iOS / Android App | 不作为本蓝图阶段主线交付端；移动端优先 H5 / PWA / 企业 IM 内嵌，未来确需原生 App 时独立立项 |
| React 19 | 不作为 Phase 1 默认 |
| Ant Design 6 | 不作为 Phase 1 默认 |
| PydanticAI | Phase 0 验证项，不作为 Phase 1 强依赖 |
| LangGraph | 可 PoC，不做生产 Runtime 底座 |
| Temporal | 后期评估，Phase 1 不引入 |
| Celery | 后期可替换 L0/L1 队列实现，Phase 1 不默认 |
| Milvus / Weaviate / Qdrant | 后期评估，Phase 1 用 pgvector >= 0.8.2 |
| Elasticsearch / OpenSearch | 后期评估，Phase 1 不默认 |
| MinIO | 候选对象存储，不在蓝图锁死 |
| RPA / Local Worker | Phase 1 只保留协议位，不做主链能力 |
| SenseVoice-Small / faster-whisper / ASR / 语音入口 | Phase 1 不实现；Phase 3/4 或 Phase 5 根据移动端、电话入口、语音唤醒需求评估 |
| EMQX / MQTT / IoT 消息接入 | Phase 1 不引入；只保留 IoT Gateway / Capability 协议位；Phase 3 根据设备消息、告警、事件订阅需求评估 |
| Controlled Exploration 生产执行 | Phase 1 默认关闭 |

### 12.4 哪些必须自研？

```text
Agent Runtime Core；
Capability Gateway；
Capability Control Plane；
Policy Guard；
Trace / Evidence / Audit 分层；
Identity Binding Management；
Skill CI/CD；
DB Gateway；
Memory Governance；
SDUI Response Envelope 协议。
```

### 12.5 技术选型底线

```text
1. 不让 LLM 框架直接调用生产工具。
2. 不让 Next.js / 前端服务端能力绕过 Python Gateway。
3. 不把 PydanticAI、LangGraph、BackgroundTasks、ARQ、Celery、MinIO、Milvus 等写死为不可替换底座。
4. 不把对象存储锁死到单一产品，统一走 S3-compatible 抽象。
5. pgvector 不得使用低于 0.8.2 的版本；如后续出现新的安全公告，以安全修复版本作为最低基线。
6. 不在 Phase 1 追新版本，优先 React 18 + Ant Design 5.x + ProComponents 2.x。
7. 不让任务队列承载核心 Workflow 状态，业务状态必须落 PostgreSQL。
8. 不把 Keycloak 等同于目标业务系统账号绑定。
9. 所有技术组件都必须服从 Capability Gateway / Policy Guard / SecretProviderPort / Trace Redaction 的安全边界。
```

## 13. 分阶段蓝图

本章只定义阶段方向和蓝图级里程碑。每个 Phase 的详细任务、验收标准、接口契约和测试用例由 Phase 0 / Phase 1 规格文件承接。

### Phase 0：地基与协议（Pre-MVP）

目标：冻结 MVP 范围、技术基线、接口契约和关键验证结论。

```text
Task / Capability / Policy / Trace / Memory 基础对象；
Capability Registry 雏形；
Capability Gateway 雏形；
Gateway 模块隔离与 import 边界；
基础 Runtime 主链；
Mock HR / Mock MES / Mock OA；
Golden Tasks 测试集；
React 18 + Vite + pnpm + Ant Design 5.x + ProComponents 2.x 前端基线锁定；
uv + Python 3.12 + FastAPI + asyncpg + OpenAI SDK + instructor + Pydantic v2 结构化输出基线；
PydanticAI 兼容性 Spike，但不作为 Phase 1 强依赖；
本地模型 / vLLM / OpenAI-compatible 网关硬件、量化、上下文、结构化输出验证；
PostgreSQL + pgvector >= 0.8.2 版本组合验证；
JobQueuePort L0/L1 实现验证：BackgroundTasks / in-process executor 与 Redis + ARQ 候选验证；
S3-compatible Object Storage 候选验证（MinIO / Ceph / 企业对象存储等）；
首批目标系统接入约束验证：泛微 OA、用友 U8 / U8 Cloud、海康 iVMS / 综合安防平台；
Docker Compose 单机内网部署基线。
```

### Phase 1：MVP 主链（产品版本：MVP）

目标：跑通“查、办、生成”的最小闭环，但只使用 Mock / 低风险能力。

```text
Web / CLI；
Intent Router；
Workflow / Tool 执行；
Policy Precheck；
Trace；
Evaluator；
Admin Lite：Registry / Policy / Trace / 基础用户角色 / Binding 状态；
Session Memory + 基础 Semantic/System Knowledge；
IdentityMapping Mock 表；
Policy Guard 绑定状态预检；
未绑定时返回 SDUI operator_handback_card 引导绑定；
无现成能力时返回 no_capability_found + 管理员配置入口；
生产级 Controlled Exploration 默认关闭；
不接入真实业务系统写操作。
```

### Phase 2：部门试点（产品版本：V0.5）

目标：接入少量真实系统，先只读、再低风险写入。

```text
1–2 个真实 API Adapter；
基础 DB Gateway；
更多 Golden Tasks；
用户反馈闭环；
基础 Skill 候选池；
审计看板；
账号绑定管理基础版：管理员查看绑定状态、按部门筛选、手动解绑/重置、发送绑定引导通知；
基于真实验证结果支持 OAuth2 / SSO / Vault / vendor_token / system_scope 等接入模式；
支持 Excel / HR 同步导入 external_principal_id 映射，但不得导入明文密码；
User Profile Memory 与 Semantic Memory 增强；
必要时局部引入 PydanticAI 或其他编排框架，但不得越过 Gateway。
```

### Phase 3：执行织物增强（产品版本：V1.0）

目标：扩展真实场景能力。

```text
Office Worker；
基础 Local Worker；
受控 RPA；
更多业务系统 Adapter；
Keycloak / LDAP / 企业现有 SSO 接入；
基础 IoT / 视频查询；
EMQX / MQTT / IoT 消息接入评估；
更完整 Policy；
凭证健康检查、定期验证、批量失效通知；
根据任务规模和可靠性要求评估 L0/L1 异步实现是否升级为 Celery / RabbitMQ / 企业消息队列 / Temporal。
```

### Phase 4：受控进化（产品版本：V1.5 / V2.0）

目标：Skill CI/CD 与 Memory Governance 成熟。

```text
Skill 草稿生成；
Skill 测试集；
自动测试生成；
LLM-as-judge 语义质量评测；
Evaluation Report 归档；
人工最终审批；
灰度发布；
health_score；
失败聚类；
Episodic Memory / Procedural Memory / Knowledge Vault；
在满足沙箱、白名单、Policy 和 Trace 条件后，评估开启低风险 Controlled Exploration。
```

### Phase 5：平台化与互操作（产品版本：V2+）

目标：从工具变成平台。

```text
A2A / 外部 Agent 兼容；
多入口扩展（Web / H5 / PWA / 企业 IM / 语音 / 电话 / Webhook）；
完整 Local Worker；
IoT 控制审批；
内外网个人记忆胶囊；
企业级治理中心；
根据门户、移动端、复杂 PWA 需求重新评估 Next.js App Router；
根据移动端语音助手、电话入口和语音唤醒需求评估 SenseVoice-Small / faster-whisper / ASR；
根据数据规模重新评估 Milvus / OpenSearch / Temporal 等重型基础设施。
```

## 14. 风险与底线

### 14.1 最大风险

```text
范围膨胀；
自由 Agent 探索过早开放；
RPA 稳定性被高估；
Trace 变成敏感数据仓库；
Memory 跨用户污染；
Skill 自动上线导致错误固化；
DB Gateway 绕过原系统权限；
为了开发快而绕过 Capability Gateway。
```

### 14.2 不可突破的底线（按主题分组）

#### 14.2.1 执行边界

```text
1. Runtime 不直接调用生产工具，只能提交结构化 ExecutionRequest。
2. Capability Gateway 是唯一真实执行入口。
3. LLM 框架不得直接调用生产 API、DB、RPA、Shell、IoT、MCP。
4. Workflow / Skill 内部高风险步骤仍必须逐步经过 Policy Guard。
5. Skill 是声明式经验包，不是绕过治理的脚本执行器。
6. 低风险快速路径不得绕过 Gateway、Policy、Trace、Evaluator，只能跳过复杂 Planner 和同步详细 Trace。
```

#### 14.2.2 数据安全与部门隔离

```text
1. DB Gateway 只能访问审批过的只读视图，不得自由查询生产核心表。
2. Memory 只能存治理后的摘要、偏好和经验，不得保存敏感业务原文。
3. Trace / Evidence / Raw Payload 必须分层、脱敏、加密、设置保留期。
4. Preselector / Memory 检索必须先按用户、部门、角色、scope 过滤，再做语义检索。
5. department scope 记忆和能力不得被其他部门自动检索或注入上下文。
```

#### 14.2.3 凭证管理

```text
1. 凭证不得进入 LLM、Prompt、Memory、Skill、Trace 摘要或日志。
2. Runtime / LLM 框架不持有凭证，凭证只能由 Capability Gateway 通过 SecretProviderPort 在执行瞬间注入 Adapter。
3. RPA 不得使用高权限代理账号绕过用户原有业务权限。
4. 凭证过期或轮换时，正在执行的任务只能暂停、重新授权、失败或转人工，不得自动切换高权限凭证绕过。
5. Capability Gateway 执行前必须验证用户对目标系统的账号绑定状态；未绑定或绑定失效时必须阻止执行并引导绑定。
6. 严禁因用户未绑定、授权过期或 Vault 凭证失败，自动降级为服务账号、管理员账号、共享账号或部门账号代理执行。
```

#### 14.2.4 UI 交互

```text
1. Runtime 不应只返回 Markdown 文本，必须支持 Response Envelope，为低信息化用户提供动态卡片、微表单、确认卡、文件卡等结构化交互。
2. 所有 UI action 只能回到 Runtime / Capability Gateway，不得由前端直接执行业务动作。
3. Response Envelope 不得包含凭证、Token、Cookie、内部接口地址或敏感原始数据。
4. Response Envelope 必须支持 schema_version、fallback_text 和通道降级。
```

#### 14.2.5 Memory / Skill 进化约束

```text
1. Trace 可以生成 Skill 草稿，但 Skill 草稿不能自动上线。
2. 进化副链不能自动扩大主链权限。
3. 探索成功不等于能力上线；探索结果只能进入 Proposal / SkillDraft。
4. Skill 发布、Workflow 变更、高风险能力上线必须保留人工最终审批。
5. Async Evolution 必须通过异步队列/事件端口承载，失败必须可重试、可死信、可观测。
```

#### 14.2.6 Local Worker / RPA / IoT

```text
1. Local Worker 必须主动注册并通过持久连接接入 Gateway，不得暴露通用远程命令端点。
2. Local Worker 只能执行本地预注册 Capability，并验证来自合法 Gateway 的签名任务。
3. RPA 页面漂移必须进入 quarantine 或修复流程，不得继续盲点。
4. LLM 不得直接控制设备，不得直接订阅 MQTT topic、设备原始流、视频帧流或私有协议报文；设备控制必须通过 IoT Gateway 和 Policy Guard。
5. UWB、门禁、摄像头事件、传感器等高频物理信号必须在边缘侧完成限流、去重、死区过滤和事件语义化，Runtime 只接收低频结构化业务事件。
6. 关键设备控制默认禁止或需多重确认/管理员审批。
```

#### 14.2.7 模型与评估

```text
1. Context Assembler 必须受 Context Budget Manager 约束，不得无限注入历史、记忆和能力全文。
2. Capability Registry 只能向 LLM 暴露候选能力摘要，完整定义只能在 Gateway 执行前按版本加载。
3. Planner 支持动态分支，但所有分支步骤必须重新经过 Capability Gateway 与 Policy Guard。
4. LLM 不可用时系统必须进入降级模式，Workflow-only 模式下仍可通过固定入口执行已发布 Workflow。
5. LLM-as-judge 只能用于语义质量、完整性、groundedness、用户体验等评测，不得替代确定性校验和人工最终审批。
```

#### 14.2.8 协议边界

```text
1. MCP 是工具协议，不是信任协议；A2A 是 Agent 互操作协议，不是 MCP 替代品。
2. Planner 不得看到全量 Capability Registry，只能看到 Capability Preselector 输出的 Top-K 候选能力摘要。
3. Capability Preselector 不做最终授权，不做真实执行，只负责候选能力压缩。
4. 完整 Capability 定义只能在 Capability Gateway 执行前按 capability_id 和 version 加载。
```

---

## 15. 首批目标系统接入约束假设

本章不是详细调研报告，而是蓝图级风险提示：首批系统的认证机制、API 形态和身份模型必须在 Phase 0 验证，不能把所有系统都理想化为“用户级 OAuth2 / Vault 绑定”。

### 15.1 泛微 OA e-cology

```text
候选接入方式：OAuth2 / OIDC / SSO / 私有 REST / SOAP / 表单流程 API。
身份假设：优先用户级委托；是否支持标准授权码流必须以现场版本和厂商文档验证为准。
待验证：当前部署版本、授权机制、流程发起接口、待办/已办接口、用户权限判断方式。
对 IdentityMapping 的影响：如不支持标准 OAuth2，应采用 sso_ticket、vendor_token、manual_mapping_only 或 vault_user_credential，而不是强行标记为 oauth2_delegate。
```

### 15.2 用友 U8 / U8 Cloud

```text
候选接入方式：开放平台 API / 本地部署接口 / DB 只读视图 / 厂商二开接口。
身份假设：可能是平台 token、app_key、账套、操作员、集成账号或用户级账号，不能默认都是用户级 OAuth2。
待验证：是否支持用户级身份透传、是否允许写操作、审批/制单权限如何判断、API 开放程度与版本差异。
对 IdentityMapping 的影响：可能需要 vault_user_credential、vendor_token、manual_mapping_only 或 system_credential_with_policy。
```

### 15.3 海康 iVMS-8700 / 综合安防平台

```text
候选接入方式：OpenAPI / SDK / Artemis 网关 / 视频平台接口。
身份假设：可能不是用户级账号绑定，而是平台级 appKey/appSecret、设备权限、区域权限、通道权限。
待验证：是否存在用户级账号 API、是否支持员工身份透传、摄像头/区域权限模型、预览/回放 URL 的安全边界。
对 IdentityMapping 的影响：很可能采用 system_credential_with_policy + Policy Guard 控制用户可访问的设备/区域/通道，而不是 ai_user_id → external_user_id 的简单用户映射。
```

### 15.4 统一要求

```text
真实系统接入前必须形成系统级接入 ADR；
每个目标系统必须明确 target_system、bind_mode、execution_identity、credential_ref/token_ref、Policy 资源范围；
如目标系统不支持用户级身份透传，不得伪装成 user_delegated；
system_scope 能力必须声明资源边界，并通过 Policy Guard 控制用户可访问范围。
```


## 16. 最终定稿结论

本方案最终确定的主线是：

> **自研企业 Agent Runtime 与 Capability Control Plane；以 Workflow / Skill 优先保证稳定执行；以 Capability Gateway 统一治理所有真实执行；以 OpenAI SDK + instructor 作为 Phase 1 结构化输出基线，并通过端口保留 PydanticAI / LangGraph 等后续替换空间；以 Ports and Adapters 保证未来模型、框架、工具、存储、工作流引擎可替换；以 UI Rendering Protocol / SDUI 降低低信息化用户使用门槛；以 Trace、Evaluation、Skill CI/CD 和 Memory Governance 支撑系统受控进化。**

一句话：

```text
入口智能化，Runtime 受控化，执行确定化，治理平台化，进化闭环化，技术可插拔。
```

v3.2.4 可以作为进入 Phase 0 的冻结版最终蓝图。进入开发前，不应继续在蓝图里扩写实施细节，而应转入 Phase 0 规格文件完成范围裁剪、接口契约、验收标准、技术 Spike、数据库迁移和 Codex / Claude Code 执行计划。
