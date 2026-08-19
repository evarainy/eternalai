# EternalAI 总体架构（Phase 2 视图）

## 0. 本文件的地位

**本文件是决定的空间投影，不是新的权威源。**

`docs/phase2/DECISIONS.md` 是时间序流水账，回答「什么时候决定了什么」；本文件按层重排同一批事实，回答「这个系统现在长什么样、哪里是空的」。两者冲突时，以 `DECISIONS.md` 和仓库代码为准。

三条自我约束：

1. **零新增决定。** 本文件不产生任何架构决定。发现的冲突、缺口和接缝全部登记到 §8 待裁决清单，由雨爷或 GOV-SYNC 棒裁决后回写 `DECISIONS.md`，再投影回本文件。
2. **每条陈述带出处。** 出处只有四类：`DECISIONS.md` 的条目日期与标题、冻结蓝图的 `§章节` 与行号、仓库代码符号、`AGENTS.md` 的规则编号。没有出处的陈述不写进本文件。
3. **代码引用带符号不带行号。** 行号必漂；蓝图是冻结文件，行号稳定，故蓝图引用保留行号。

**失效条件**：本文件描述的是 Phase 2 某一时刻的形态。任何一条与当前代码或 `DECISIONS.md` 不符时，以后两者为准，并把本文件对应段落标记为待更新——**不要在本文件里就地修正事实，那正是第二套真相的生成方式**。前身 `_scratch/任务驱动业务工作台与可替换AgentHarness架构说明书_v0.2.md` 两天内三次重修后被判停止修订，死因即此（`DECISIONS.md` 2026-08-18「架构说明书 v0.2 的地位：停止修订」）。

**与冻结蓝图的关系**：`docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md` 是 Phase 0 冻结产物，禁止修改（`AGENTS.md` 不可协商规则 1）。它在若干处已被后继决定覆盖；本文件 §7 逐条列出对照结论，但**不改蓝图本体**，覆盖一律以 `DECISIONS.md` 的后继决定形式生效。

---

## 1. 产品定位与三种工作表面

EternalAI 是**部署在现有业务系统之上的 AI 工作操作系统**，不是聊天机器人、智能工作台、业务中台或企业 IM。

系统持续接收 IM、OA、文件、业务系统事件和人工输入，将其转化为可治理的工作对象，经 AI Runtime、Capability、Workflow 与 Skill 受控执行，再把同一工作状态投射到三种互补的工作表面：

| 表面 | 关系 | 承载什么 |
|---|---|---|
| **通信空间 / IM** | 人与人 | 沟通、文件、通知；也是最重要的工作事件来源之一 |
| **AI 共事界面** | 人与 AI | 通用问题、开放任务、自然语言指令、连续协作与结果修订 |
| **工作台** | 人与工作 | 责任、状态、时限、流程、阻塞、证据、确认和交接 |

三者不是三个数据孤岛：IM 消息可形成 `WorkCandidate`（待办候选），AI 对话可创建或继续处理工作，工作台结果可返回原会话，工作对象可追溯来源消息；身份、上下文引用、工作状态和审计记录必须贯通。

**权威归属划分**（这条决定了下面每一层能存什么）：

- 原始消息以 **IM** 为权威来源；
- 正式业务状态以 **OA 等源系统**为权威；
- EternalAI 的 **Work Object** 只作为跨系统工作编排状态的权威对象，**不复制成第二套业务真相**。

> 出处：`DECISIONS.md` 2026-08-18「产品定位：AI 工作操作系统与三种工作表面」。该条覆盖了 `_scratch/ADR_前端体验架构_草案.md` 中「工作队列为主体」的产品级定位。

---

## 2. 分层总览

```text
┌─────────────────────────────────────────────────────────────┐
│ Surface 层                                                   │
│   IM 表面（紧凑卡片） │ AI 共事界面（对话） │ 工作台（摘要+详情）│
│   └── Surface Adapter：把表面动作归一为 UserAction           │
├─────────────────────────────────────────────────────────────┤
│ Work Object 层  ← 三表面的共同枢纽                            │
│   WorkCandidate →（人工确认）→ Work Object                   │
│   责任人 / 时限 / 流程状态 / 阻塞 / 证据 / 来源引用            │
├─────────────────────────────────────────────────────────────┤
│ 企业控制内核（必须自有，不可替换）                             │
│   Capability Gateway │ Policy Guard │ Identity │ Secret      │
│   Trace │ Evidence │ Capability Registry │ Evaluator         │
├─────────────────────────────────────────────────────────────┤
│ 通用执行内核（优先复用成熟组件，保持可替换）                    │
│   agent loop │ 上下文工程 │ 持久 checkpoint │ 沙箱 │ 子 Agent │
│   经 AgentOrchestrationPort / WorkflowEnginePort 隔离        │
├─────────────────────────────────────────────────────────────┤
│ Execution Fabric / Adapter                                   │
│   OA Adapter │ DB Gateway │ 本地 capability provider │ ...    │
└─────────────────────────────────────────────────────────────┘
```

**中间两层的分界线是本架构最重要的一条线**：企业控制内核（Gateway / Policy / Identity / Secret / Trace / Evidence）必须自有；通用执行内核（agent loop、上下文工程、持久 checkpoint、沙箱、子 Agent）优先复用成熟组件并保持可替换。

这条线的依据不是偏好，是实盘核对的结论：已建成的那些层第三方框架都不提供，而框架真正强的那些层项目完全空白，两边几乎不重叠——所以问题从来不是「要不要换掉自研」，而是「要不要补一层从没建过的」。

> 出处：`DECISIONS.md` 2026-08-17「蓝图原则修订：企业控制内核自有，通用执行内核复用」。该条以后继决定形式覆盖冻结蓝图 L2435 / L2613，蓝图本体不改。

---

## 3. 跨层不变量

以下八条在任何一层、任何表面、任何阶段都成立。新增模块、新增表面、引入第三方组件时，先对照本节。

| # | 不变量 | 出处 |
|---|---|---|
| 1 | 任何执行必须经 `Gateway → Policy → Adapter → Evaluator → Trace`，且不可绕过 | 蓝图 §7.3 L1436-L1502、§14.2.1-§14.2.2 L2787-L2806 |
| 2 | 执行路径优先级：`Workflow > Published Skill > Single Tool / Registered Tool Chain > no_capability_found` | 蓝图 §6.5 |
| 3 | 卡片动作的授权依据只能是服务端签发、绑定 `(tenant, principal, work_object, card_version)` 的不透明引用；转发、引用、复制、截图**不传递操作权**；明文业务 ID 不得作为授权依据 | `DECISIONS.md` 2026-08-18「卡片动作的授权边界：引用不传递权限」 |
| 4 | AI 的自动权限止于 `WorkCandidate`；升级为正式 Work Object 必须绑定一次明确的人机交互确认，责任归作出确认的人 | `DECISIONS.md` 2026-08-18「聊天事件候选闸门」 |
| 5 | 明文 password / token / cookie / sessionid / access_token / refresh_token 不得进入 Trace、ResponseEnvelope、fixture、日志、Task Record 或报告；该禁令不受内外网区分影响 | `AGENTS.md` 不可协商规则 4；蓝图 §7.4.3 L1540-L1549；`DECISIONS.md` 2026-08-18「Skill 候选来源语义」 |
| 6 | 六边形依赖方向：`app/ports/` 是 Protocol 接口，`app/infra/` 是实现，`app/ports/` 不得依赖 `app/infra/` | `AGENTS.md` 项目不变量；`tests/architecture/` 守卫 |
| 7 | 安全开关只能依赖可真实校验的协议事实或配置值；不得让 `ENV` 等自由文本环境标签承担安全分流 | `AGENTS.md` 不可协商规则 7 |
| 8 | 最终 `allow / confirm / approve / deny / escalate` 必须由确定性 Policy Engine 产生；LLM 只能产出解释、风险说明和补参建议 | 蓝图 §6.11 |

**插槽式模块边界**：产品模块采用插槽式组合，插槽由稳定契约、静态注册表和 allowlist 控制，在构建或部署时装配。任何插槽模块都不得绕过 Identity、Policy、Gateway、Evaluator、Trace 和 Evidence，不得自行扩大消息读取范围、发明新动作或直接执行目标系统操作。**不采纳运行时插件容器**（动态加载第三方代码、HMR 热插拔、同进程无隔离、「没有 privileged core」）——它与不变量 1 正面冲突。

> 出处：`DECISIONS.md` 2026-08-18「插槽式模块边界」、2026-08-17「DeepSeek Harness：不引入运行时插件容器」、2026-08-18「DeepSeek Harness 评估结项」。

---

## 4. 后端：已建成的面与空白的面

### 4.1 已建成（有代码、有测试、有现场证据）

`app/ports/` 下 15 个 Protocol 契约：

```text
adapter │ auth │ capability_gateway │ capability_registry │ identity_mapping
job_queue │ llm_provider │ policy_guard │ request_context │ response_envelope
runtime │ secret_provider │ structured_output │ task_store │ trace
```

对应实现分布在 `app/infra/`（adapters、auth、gateway、health、identity、job_queue、llm、observability、persistence、policy、sdui、security）与 `app/`（evaluator、execution_fabric、knowledge、memory、runtime、workflow、contracts、db、admin、api）。

已跑通的纵切：

- **OA 只读全链**——两个 capability（`oa.list_pending_workflows`、`oa.list_system_messages`）从请求、真实身份、Gateway、Adapter、Evaluator、Trace 到响应；2026-08-13 完成真实内网现场验收（最小请求头、全新 Cookie 冷启动、两个 capability Live 指纹、真实浏览器 `/chat`）。
- **认证与会话**——OA 登录、EternalAI Session Cookie、认证 Principal、CSRF（`Origin` + 固定自定义头）、fail-closed 401 重认证。
- **SDUI 与 ResponseEnvelope**——`confirm_card` 的 `ui.payload` 固定四键，参数值零流入。
- **Workflow 执行**——`WorkflowEngine`（`app/workflow/engine.py`）含 checkpoint、resume、条件分支、confirmed capability 校验。
- **Trace / Evidence / Golden**——Golden Gate 27 题（negative 16、positive 11），凭证扫描 pattern 七条。

> 具体基线数字见 `docs/phase2/STATUS.md`；已完成棒见 `git log --grep='phase2('`。

### 4.2 蓝图 §6 Agent Runtime 的落地实况

蓝图 §6.2 列出 Agent Runtime 的 12 个组件。本轮逐个核对代码符号，结果如下：

| 蓝图 §6.2 组件 | 代码中是否存在 | 说明 |
|---|---|---|
| Intent Router | **是** | `app/runtime/intent_router.py::IntentRouter` |
| Task Builder | 否 | 无对应符号 |
| Clarifier | 部分 | 无独立组件；clarification 语义散在 `app/contracts/sdui/models.py`、`app/infra/gateway/capability_gateway.py`、`app/infra/sdui/response_envelope_builder.py` |
| Context Assembler | 否 | 无对应符号 |
| Context Budget Manager | 否 | 无对应符号；蓝图 §6.3 要求每次模型调用记录 `context_budget_summary` |
| Capability Preselector | 否 | 无对应符号；蓝图 §6.4「Planner 不得看到全量 Capability Registry」目前无执行者 |
| Capability Summary Cache | 否 | 无对应符号 |
| Planner | 否 | 无对应符号；蓝图 §6.6 Hybrid Planning 未落地 |
| Execution Coordinator | 部分 | 职责揉在 `app/runtime/runtime.py::RuntimeImpl` 内，非独立组件 |
| Runtime Mode Manager | 否 | 无对应符号；蓝图 §6.8 四种运行模式（Normal / Limited LLM / Workflow-only / Maintenance）均未实现 |
| Evaluator | **是** | `app/evaluator/terminal.py::TerminalEvaluator` |
| Response Composer | 部分 | 职责揉在 `RuntimeImpl._build_envelope` 与 `app/infra/sdui/response_envelope_builder.py` |

**核对方法**：对 12 个组件名及其变体（类名、`*Port` 后缀、函数名）在 `app/` 下做符号级检索，再对宽松关键词（`preselect`、`budget_manager`、`runtime_mode`、`degraded`、`fast_path`、`composer`、`clarif`）做全文检索。「否」表示两轮检索均无命中。

**结论**：`RuntimeImpl`（`app/runtime/runtime.py`）是一个把意图路由后的能力选择、执行协调、响应组织揉在一起的单体。它足以支撑「单轮请求 → 单个 capability → 响应」这条已验收的路径，但蓝图 §6 中与**多轮、规划、上下文预算、降级**相关的组件一个都没有。这不是缺陷——那些组件属于「通用执行内核」，按 2026-08-17 决定应优先复用成熟组件，而不是自研补齐（见 §4.3）。

蓝图 §6.7 Known Read Fast Path 亦未实现。

### 4.3 两个有名字、没实现的接缝

| 接缝 | 蓝图出处 | 代码现状 | 作用 |
|---|---|---|---|
| `AgentOrchestrationPort` | 蓝图 §6.11、L2373 / L2402 / L2403 | `app/ports/` 下不存在 | 隔离第三方 agent 编排框架；蓝图 §6.11 原文：「无论是否引入 PydanticAI，都必须通过 LLMProviderPort / StructuredOutputPort / AgentOrchestrationPort 隔离」 |
| `WorkflowEnginePort` | 蓝图 L2373 / L2402 / L2403 | `app/ports/` 下不存在；`WorkflowEngine` 是具体类，无 Protocol 抽象 | 隔离工作流引擎实现 |

**为什么这两个接缝是当前最紧的前置**：按 2026-08-17「蓝图优化的落地顺序（四步走）」，路线是 `修文档漂移 → 内网真实 smoke → 修工具描述 → 留孔 + 装现成组件`。前两步已完成，当前落在第三步；「留孔」就是补这两个接缝。装第三方执行内核之前必须先有接缝，否则框架类型会长进业务代码——框架下次改 API 时改的是业务逻辑，想换第二家的成本比第一次更高。**第三步是第四步的前置，不能对调。**

补接缝是把蓝图欠的账补上，不是新增架构，因此不构成新的架构停点。

---

## 5. Work Object 层：一层全新的、代码中零存在的枢纽

### 5.1 实况

对 `app/` 与 `web/src/` 全量检索 `work_object` / `workobject` / `work_candidate` / `workcandidate` / `skillcandidate`（大小写不敏感），**零命中**。

### 5.2 现有 `TaskRecord` 为什么扮演不了这一层

`app/ports/task_store.py::TaskRecord` 的全部字段：

```text
task_id │ session_id │ ai_user_id │ status │ trace_id │ capability_id │ error_code
```

`TaskStatus` 六态：`created` / `running` / `waiting_user` / `completed` / `failed` / `no_capability_found`。

这是**一次 AI 请求的执行记录**——它回答「这次调用走到哪一步、出了什么错、Trace 在哪」。而 §1 定义的工作台要承载**责任、状态、时限、流程、阻塞、证据、确认和交接**，其中：

- **责任人**（不是发起请求的 `ai_user_id`，是这项工作归谁办）——无字段；
- **时限 / 逾期**——无字段，而工作台的优先级、超时提醒、反向推送全依赖它；
- **来源引用**（哪条 IM 消息、哪个 OA 事项、哪个文件产生了这项工作）——无字段；
- **流程状态与阻塞**（等谁确认、卡在哪个权限或数据上）——`waiting_user` 一个枚举值承载不了；
- **证据与确认记录**（确认人、确认时间、`WorkCandidate` 版本、补充字段、确认结果）——无字段；
- **交接**——无概念。

且 `TaskRecord` 的生命周期是单次请求，而 Work Object 的生命周期跨多次请求、多个表面、多个源系统。**两者不是同一个对象，`TaskRecord` 不应被改造成 Work Object**——它作为执行记录仍然需要，改造会同时毁掉两个语义。

### 5.3 这一层要承载什么

按 §1 的权威归属划分，Work Object 是**跨系统工作编排状态的权威对象**，且**不复制成第二套业务真相**——正式业务状态仍以 OA 等源系统为权威。它存的是「这项工作在 EternalAI 里编排到哪一步」，不是「这项业务在 OA 里是什么状态」。

配套的 `WorkCandidate` 状态机（`DECISIONS.md` 2026-08-18「聊天事件候选闸门」）：

```text
聊天消息 / 引用消息 / OA 事件 / 文件 / 业务系统事件
  → AI 提取事实、责任人、时限、来源和歧义
  → 生成 WorkCandidate 卡片并推入工作台
  → 人工审阅、补充或驳回
  → 人工明确确认后转为正式 Work Object 并进入流程
  → 任何外部执行仍经 Gateway → Policy → Adapter → Evaluator → Trace
```

一条消息可以产生多个 `WorkCandidate`，多条消息也可以汇聚为一个；**不得强制建立 `message = task` 的一一映射**。

**命名硬约束**：代码标识符（类名、字段名、表名、API 路径、枚举值、Trace 事件名、错误码、配置键、测试名）中禁止单独使用「候选」/`Candidate`，必须写 `WorkCandidate` 或 `SkillCandidate` 全称，无例外。理由是这两者状态机形状几乎一样但风险面完全不同——`WorkCandidate` 被误升级导致未授权业务执行，`SkillCandidate` 被误发布导致未审查能力进运行时；出现 `CandidateStore` / `approve_candidate` 这类名字时读代码的人无法判断动的是哪一种。

> 出处：`DECISIONS.md` 2026-08-18「『候选』正名」及同日「适用面收窄」修订。

---

## 6. 前端：现状与目标

### 6.1 现状（精确清单）

`web/src/` 下非生成、非测试的源文件共 11 个：

```text
App.tsx │ main.tsx
pages/          LoginPage │ ChatPage │ HealthPage │ loginNavigation.ts
pages/admin/    BindingsPage │ RegistryPage │ TasksPage │ registryValidation.ts
components/     RoleSelector
stores/         authStore │ roleStore
api/            mutator.ts │ mockHealth.ts
```

Orval 生成的客户端四组：`auth` / `runtime` / `admin` / `admin-trace`（`web/src/generated/`）。

已装依赖（`web/package.json`）：

| 类别 | 已装 |
|---|---|
| 运行时 | `react` 18 · `react-dom` 18 · `antd` 6.6.1 · `@tanstack/react-query` 5 · `zustand` 5 · `react-router-dom` 6 |
| 开发 | `vite` 6 · `vitest` 3 · `orval` 7 · `typescript` 5.6 · `eslint` 9 · `jsdom` 25 |

**已定但未装**（在 `web/package.json`、`package-lock.json` 和 `web/src/` 中均零出现）：

| 未装项 | 已由哪条决定要求 |
|---|---|
| `@ant-design/x` 2 | `DECISIONS.md` 2026-08-18「前端技术栈与多 Surface 渲染的衔接」——承载「AI 共事界面」的对话壳 |
| `@rjsf/antd` / `@rjsf/core` / `@rjsf/utils` / `@rjsf/validator-ajv8` 6.7.x | 同上——承载结构化表单（能力入参、事项工作室字段编辑） |
| `@ant-design/icons` 6 | 同上——列入 Web 前端基线 |
| `msw` | 蓝图 §12.1.1 列入前端基线 |

**已排除**：`@ant-design/pro-components` 已移出 manifest、lockfile 与依赖允许表，改列排除表（`P2-FE-ANTD6-001`）。

### 6.2 三种表面 × 三档密度

同一份工作卡片语义与状态在三个表面复用，**按 Surface 采用不同密度**：

| 表面 | 密度 | 渲染实现 |
|---|---|---|
| IM | 紧凑卡片 | 待定；RJSF 在紧凑密度下**是否适用尚未验证** |
| 工作台 | 摘要卡片 | antd 6 原生 `Table` + 项目自有薄查询层（**该薄查询层目前不存在**） |
| 事项工作室 | 完整详情 | antd 6 + RJSF 结构化表单 |

复用的是**语义协议、状态、动作合同和 Ant Design 6 组件实现**，不是强迫所有界面显示完全相同的布局。**渲染实现按密度分别提供**，不得默认三档都用同一渲染路径。

### 6.3 协议包与渲染器物理隔离

这是前端最硬的一条结构约束：

- 权威源必须是 **JSON Schema / OpenAPI 类语言中立契约**；
- CI 同时生成或校验 TypeScript 与 Python 产物；
- **协议包不得依赖 React、antd 或任何渲染器**；
- antd 渲染器只消费协议的 TypeScript 产物；
- 协议包须能在**无 DOM 环境**独立校验；
- 由架构测试守住依赖方向。

推论：JuggleIM 或任何通信产品的消息载荷只携带卡片协议版本、对象引用和允许的动作，由 EternalAI Web Renderer 渲染；**不得让通信产品的数据结构反向成为工作对象或卡片协议的权威定义**。

### 6.4 其余就地固化的前端硬约束

1. React 固定 18.x，按 React 19 兼容约束编写代码，本阶段不升级。
2. 引入 RJSF 时四个包必须保持同一 6.7.1 版本线。
3. 禁止用 npm `overrides` / `resolutions` 或强制 dedupe 改写 RJSF 的 `react-is` 解析树。
4. 引入 RJSF 的那一棒必须落两条永久回归测试：`defaultProps.options` 合并路径、forwardRef widget 分支。
5. RJSF 的 PoC 结论是「有条件通」，条件为 2026-08-14 的锁定矩阵；React 升主版本 / antd 落回 `≤6.3.5` / 任一 `@rjsf/*` 离开 6.7.x / `react-is` 解析树被改写，任一命中即须重跑 PoC，结论不得外推。

> 出处：`DECISIONS.md` 2026-08-18「前端 ADR 草案不进仓库，硬约束就地固化」。不再新增独立前端 ADR 文件，前端决定一律进 `DECISIONS.md`。

### 6.5 平台约束

内网终端操作系统为 **ARM 架构银河麒麟 V10**。这一事实封死桌面壳的三条路：Electron ≥23 要求 Windows 10（末班 22.3.27 / Chromium 108 已于 2023-10-10 EOL）；WebView2 对 Windows 7 的支持已于 2023-01-10 终止；Tauri 要求 WebKitGTK ≥2.40，而麒麟 V10 SP1 ARM 提供 2.22.2。

因此**「只做浏览器 + PWA，另配无浏览器内核的本地 capability provider」不是权宜之计，是当前平台下的唯一可行解**。

本地 capability provider **不是原生客户端**：不带浏览器内核、不带 UI，只是装在终端上、经 `localhost` 被 Web 应用调用的瘦服务，承接浏览器确实做不到的能力（Office COM、扫描仪、Win32 老软件、后台长驻任务），麒麟侧按 Python 3 实现。Excel 报表主流程仍以纯 Web（File System Access）为默认路径。

**未实测项**：Excel 与本地文件能力依赖 secure context，其两条取得路径（HTTPS + 内部 CA、Chrome 企业策略白名单）**均未内网实测**。

> 出处：`DECISIONS.md` 2026-08-18「部署目标平台确认」「本地 capability provider 的边界澄清」「前端技术栈与多 Surface 渲染的衔接」。

### 6.6 通信内核的当前定性

JuggleIM 目前**只是架构完善所做的初步筛查对象，不构成已定选型**。当前阶段先把 Web 做出来、把流程跑通；IM 相关工作排在 Web 与工作流基本成型之后，**届时无条件重新评估**。

现在不为 JuggleIM 编写验证清单、不做 IM 选型调研、不将其写入任何实现棒的前置。

但三条 IM 相关的协议与责任边界（`WorkCandidate` 闸门、卡片只做路由不承载自由文本执行、工作台反向推送）**继续有效**——它们约束的是「AI 与聊天如何交互」，与具体通信产品无关。

> 出处：`DECISIONS.md` 2026-08-18「通信内核的当前定性：初步筛查，未定选型」。该条修订了同日「当前前端与通信技术路线」中关于重开选型触发条件的表述。

---

## 7. 与冻结蓝图的对照结论

### 7.1 本轮范围与方法

此前的对照只覆盖蓝图 §12（技术选型）与 §15（目标系统接入），且其中五条「边界待明确」以 DeepSeek Harness 为主语，该前提已于 2026-08-18 失效。本轮补齐七个高风险章节，并按 2026-08-18 决定的要求**同时以冻结蓝图与 `DECISIONS.md` 为输入**（上一轮只输入了蓝图，结构上不可能发现前提失效）。

| 章节 | 本轮是否对照 | 承担方式 |
|---|---|---|
| §3.4 UI Rendering Protocol / SDUI | 是 | 逐字摘录 + 分类 |
| §6 Agent Runtime | 是 | **全文精读**（见 §4.2、§4.3） |
| §8.4 Local Worker | 是 | 逐字摘录 + 分类 |
| §9 Policy、Audit、Evaluation | 是 | 逐字摘录 + 分类 |
| §10 Memory Fabric 与 Skill CI/CD | 是 | 逐字摘录 + 分类 |
| §11 解耦与可插拔设计 | 是 | 逐字摘录 + 分类 |
| §13 分阶段蓝图 | 是 | 逐字摘录 + 分类 |

逐字原料与随机回查记录留在 `_scratch/P2-ARCH-OVERVIEW-001_蓝图对照原料.md`（35 条记录，5 条随机回查行号逐字一致）。

### 7.2 结论：本轮七章零冲突

35 条记录的分类结果：**28 条佐证、7 条缺口、0 条冲突**。

即：本轮对照的七个章节中，**没有任何一条蓝图陈述与 `DECISIONS.md` 决定组不能同时成立**。此前已知的两条真冲突（ProComponents 处置路线、Phase 1 技术禁令的继承性）都在 §12，且均已裁决（2026-08-18「Ant Design 6 采用移除 ProComponents 后独立升级」、「Phase 1 技术禁令不自动继承到 Phase 2」）。

**这个结论的含义**：Phase 2 的决定组是在蓝图之上做**增量与阶段化**，不是在推翻蓝图。已发生的两次覆盖（L2435 / L2613 的自研边界、§12.2.1 的升级触发条件）都以后继决定形式生效，蓝图本体未改，也不需要新发 ADR 覆盖本轮七章的任何一条。

其中最值得记的一条佐证在 §11.4「禁止依赖规则」：`UI 不得直接调用 Tool / Workflow / Adapter`——这条 Phase 0 写下的禁令，与 2026-08-18「聊天只做卡片路由，不承载自由文本执行」「插槽式模块边界」是同一条线，隔了一整个阶段后仍然对齐。

### 7.3 端口对照：蓝图 25 个「必须保留的端口」vs 现有 15 个

蓝图 §11.2 L2367-L2395 列出必须保留的端口。逐个对照 `app/ports/`：

| 蓝图端口 | 现状 | 说明 |
|---|---|---|
| `ModelProviderPort` / `LLMProviderPort` | **已有** | 合并为 `app/ports/llm_provider.py` |
| `StructuredOutputPort` | **已有** | `app/ports/structured_output.py` |
| `CapabilityRegistryPort` | **已有** | `app/ports/capability_registry.py` |
| `CapabilityGatewayPort` | **已有** | `app/ports/capability_gateway.py` |
| `PolicyEnginePort` | **已有** | `app/ports/policy_guard.py`（名字不同，职能对应） |
| `ToolExecutionPort` | **已有** | `app/ports/adapter.py::AdapterPort`（名字不同，职能对应） |
| `TracePort` | **已有** | `app/ports/trace.py` |
| `SecretProviderPort` | **已有** | `app/ports/secret_provider.py` |
| `IdentityMappingPort` | **已有** | `app/ports/identity_mapping.py` |
| `JobQueuePort` | **已有** | `app/ports/job_queue.py` |
| `AgentOrchestrationPort` | **缺** | 见 §4.3；四步走第四步的前置 |
| `WorkflowEnginePort` | **缺** | 见 §4.3；`WorkflowEngine` 是具体类，无 Protocol |
| `MemoryPort` | **缺** | `app/memory/session_memory.py::SessionMemory` 是具体类，无 Protocol |
| `EvaluationPort` | **缺** | `app/evaluator/` 下无 Protocol；`TerminalEvaluator` 是具体类 |
| `CredentialBindingPort` | 无独立端口 | binding 语义在 `app/ports/auth.py` 内（`SessionBindingError` 等）；**是否需要独立端口待判**，不默认列为缺口 |
| `HumanGatePort` | 无独立端口 | confirm 语义分布在 `policy_guard` 的 `confirm` 决策值与 `response_envelope` 的 `ConfirmCard`；**是否需要独立端口待判** |
| `LocalWorkerPort` | **缺** | 本地 capability provider 已定方向（见 §6.5），端口未建 |
| `VectorStorePort` / `EventBusPort` / `ObjectStoragePort` | 缺 | 后期阶段项，非当前缺口 |
| `DocumentWorkerPort` / `RPAWorkerPort` / `IoTConnectorPort` / `AgentInteropPort` | 缺 | Phase 3+ 项，非当前缺口 |

现有但蓝图未列的五个：`auth` / `request_context` / `response_envelope` / `runtime` / `task_store`——这些是实现过程中形成的本地契约，不与蓝图冲突。

**读这张表的正确方式**：`25 − 15 = 10` 不是待办数量。真正与当前阶段相关的只有前四个缺口（`AgentOrchestrationPort`、`WorkflowEnginePort`、`MemoryPort`、`EvaluationPort`）加 `LocalWorkerPort`；其余属 Phase 3+ 或后期阶段项，列在这里是为了说明「为什么现在不做它们」，不是为了排进 DAG。

### 7.4 七条缺口

「缺口」= 蓝图提出了要求，而 `DECISIONS.md` 与 `app/` 现有代码**都没有对应承载**。

| # | 缺口 | 蓝图出处 | 现状 |
|---|---|---|---|
| G-1 | **Local Worker 信任模型**：主动注册接入 Gateway、只接受合法 Gateway 的签名任务、本地预注册 Capability 白名单、不作为通用命令执行端点、失联/版本过旧/签名失败/清单不一致时 Gateway 必须拒绝下发 | §8.4 L1851-L1874 | 决定组只定了本地 provider 的**形态与用途**（不带内核、不带 UI、经 `localhost` 调用），这套**信任合同一条都没有**；`app/` 无 Local Worker 承载 |
| G-2 | **Trace / Evidence / Raw Payload 三层分层**：Trace 存操作摘要，Evidence 存截图与原始凭证类证据（加密、按权限访问），Raw Payload 默认不保存、确需保存必须脱敏加密设保留期 | §9.2 | 决定组只把双层 Trace 分层列为「待独立 ADR 承接」；现有实现未形成三层存储与保留合同 |
| G-3 | **逐 Capability / Workflow Step 的 `evaluation_mode` 声明**（`deterministic` / `semantic` / `both` / `none`，写操作默认 `both`，高风险 `both` + 人工确认） | §9 L2104-L2118 | 只有终态确定性 Evaluator（`TerminalEvaluator`），无声明合同、无语义评测路径 |
| G-4 | **Memory 六层**：User Profile、Episodic、Procedural、Knowledge Vault、增强 Semantic | §10 L2163-L2186 | 只有 Session Memory |
| G-5 | **Skill CI/CD 生命周期**：`draft → candidate → tested → reviewed → published → monitored → deprecated / quarantined / rollback`，含测试、风险评级、审批、灰度、发布、监控、回滚 | §10.3 | 决定组只定了 P2 的 `SkillCandidate` **人工登记与不得自动发布**边界，完整生命周期无承载 |
| G-6 | **Workflow / Skill / Tool / Prompt / Policy 版本锁定**：Task 启动时绑定版本，执行中不得自动切换，新版本只对新 Task 生效，用户确认时展示的预览必须与实际执行版本一致 | §10.4 | 决定组要求确认绑定卡片版本，但未覆盖 Task 启动后的**整体版本锁定**；`app/` 无统一锁定合同 |
| G-7 | **User Profile Memory 与 Semantic Memory 增强**（蓝图明确列为 Phase 2 交付项） | §13 L2715 | 同 G-4；`P2-MEMORY-001` 已在 DAG 中且标 BLOCKED（数据边界/语料） |

**G-6 值得单独一句**：它与不变量 3「引用不传递权限」是同一个安全面的另一半。引用不传递权限防的是「卡片被转发后被别人点」，版本锁定防的是「用户按 v1 确认、系统执行 v2」。前者已裁决，后者目前无任何承载——而低风险写入（`P2-LOW-RISK-WRITE-001`）正是最需要它的那一棒。

---

## 8. 缺口与待裁决清单

本节是第③步「重排任务」的输入。按性质分三类：**需要裁决的**、**不需裁决只等排期的**、**需要先核实的**。

本文件不裁决任何一条（见 §0 自我约束 1）。

### 8.1 七项待裁决 —— 2026-08-19 已全部裁决

**裁决结果**（权威正文见 `DECISIONS.md` 2026-08-19 的六条，本表只是索引）：

| # | 裁决 | 落到哪 |
|---|---|---|
| D-1 | Work Object 建最小真实纵切，但**首个来源 OA 待办直通 Work Object，不经 `WorkCandidate`**；后者留位不实现 | `P2-WORK-OBJECT-001`（BLOCKED 于同步策略，见 §8.3 V-5） |
| D-2 | 两个接缝**现在就建，第一个实现包住项目自己已有的代码**（`WorkflowEngine` 与 `RuntimeImpl` 的编排行为），不等第三方框架 | `P2-PORT-SEAM-001` |
| D-3 | 不新建 `CredentialBindingPort`（记为相对蓝图 §11.2 的既定偏差）；新增 `HumanGatePort` | `P2-CONFIRM-BINDING-001` |
| D-4 | 版本绑定**推广既有 Workflow 锁定模式**（不是从零建），与 `HumanGatePort` 合同一起设计，列为低风险写入硬前置 | `P2-CONFIRM-BINDING-001` |
| D-5 | `P2-PILOT-OPS-001` 拆解，不再作任何后续任务前置；下游改依赖具体交付面 | `PHASE2_PLAN.md` DAG 已重排 |
| D-6 | 薄查询层**不独立成棒**；Work Object 首版列表不做服务端分页排序，数据量上限进验收条件 | 并入 `P2-WORK-OBJECT-001` |
| D-7 | 下一棒 `P2-PORT-SEAM-001`；`STATUS.md` 与 `PHASE2_PLAN.md` 两处指针已对齐 | 本次 GOV-SYNC |

**另有一项在裁决过程中新发现、尚未裁决**：Work Object 与 OA 的状态同步策略（见 §8.3 V-5）。它决定 Work Object 有哪些字段，是 `P2-WORK-OBJECT-001` 的开工前置。

以下为裁决前的原始描述，保留以便追溯当时的问题陈述：

| # | 待裁决项 | 为什么需要裁决 |
|---|---|---|
| D-1 | **Work Object 层怎么建、什么时候建** | 它是三表面的共同枢纽（§5），代码中零存在，且不能由 `TaskRecord` 改造而来。它一旦开建就会影响 DB schema、`app/ports/` 契约和几乎所有后续前端棒——属 A 档触碰面。**这是当前最大的单点未决**。 |
| D-2 | **`AgentOrchestrationPort` / `WorkflowEnginePort` 接缝棒的排期与形状** | 四步走第三步收口后即是「留孔」，蓝图 §11.3 已给出可替换矩阵（编排器候选 PydanticAI / OpenAI Agents SDK / Microsoft Agent Framework / 自研 Runner；状态机候选 LangGraph / Temporal / DBOS）。**接缝形状要不要按某个具体候选反推，是设计决定**。 |
| D-3 | **`CredentialBindingPort` 与 `HumanGatePort` 是否需要独立端口** | 两者的语义目前分别寄生在 `auth.py` 和 `policy_guard` / `response_envelope` 中。蓝图列为必须保留的端口，但现有形态也能工作。**独立成端口是契约变更（A 档），不独立则需明确记录偏差理由**。 |
| D-4 | **G-6 版本锁定的排期** | 低风险写入棒最需要它，而 `P2-LOW-RISK-WRITE-001` 当前只依赖 `P2-GOLDEN-001`。**要不要把版本锁定加为它的前置，是 DAG 调整（归 GOV-SYNC）**。 |
| D-5 | **`P2-PILOT-OPS-001` 并进工作台后的 DAG 重排** | 2026-08-18 决定已明写该重排「归 GOV-SYNC，实现棒不得自行改写」，且依赖它的 `P2-SKILL-CANDIDATE-001`、`P2-GOLDEN-001` 需一并重排。**至今未执行**。 |
| D-6 | **工作台薄查询层的归属** | ProComponents 已移出基线，工作台表格需先建「antd 6 原生 `Table` + 项目自有薄查询层」，该层目前不存在，2026-08-18 决定称其为「并进后的第一项前置」。**是独立一棒还是并进工作台首棒，未定**。 |
| D-7 | **`docs/phase2/PHASE2_PLAN.md` 的「下一棒」指针** | 当前留空待裁决。上一棒未自行挑选后继是合规的（实现棒不得自选）。**必须由 GOV-SYNC 裁定**。 |

### 8.2 不需裁决，只等排期或解除 BLOCKED

| # | 项 | 状态 |
|---|---|---|
| E-1 | G-4 / G-7 Memory 六层与 User Profile | `P2-MEMORY-001` 已在 DAG，BLOCKED 于数据边界/语料 |
| E-2 | G-5 Skill CI/CD 完整生命周期 | P2 只做到 `SkillCandidate` 人工登记（已拍板），完整生命周期属 Phase 3/4 |
| E-3 | 前端四项已定未装依赖（`@ant-design/x`、RJSF 四包、`@ant-design/icons`、`msw`） | 装哪个由承接该表面的棒决定，硬约束已就地固化（§6.4） |
| E-4 | G-1 Local Worker 信任模型 | 本地 provider 方向已定，但 Excel / 本地文件场景尚未排期 |
| E-5 | G-2 Trace / Evidence / Raw Payload 三层 | 已列「待独立 ADR 承接」，属后期治理合同 |
| E-6 | G-3 `evaluation_mode` 逐能力声明 | 与 `P2-GOLDEN-001` 同一评测面，可合并考虑 |
| E-7 | `P2-DB-GATEWAY-001` | BLOCKED 于 DBA/业务批准视图 |
| E-8 | 第二个真实 Adapter | 待选，首个已现场结项 |

### 8.3 需要先核实的未验事项

| # | 事项 | 为什么不能当事实用 |
|---|---|---|
| V-1 | secure context 的两条取得路径（HTTPS + 内部 CA、Chrome 企业策略白名单） | **均未内网实测**；Excel 与本地文件能力全部依赖它 |
| V-2 | RJSF 在紧凑密度（IM 卡片）下的适用性 | PoC 只覆盖了 2026-08-14 锁定矩阵，结论「有条件通」**不得外推** |
| V-3 | 现有 Trace 是否已记录完整 LLM 输入 | 2026-08-17 决定要求「先核实再排期」，至今未核 |
| V-4 | 旧对照清单中 Y-01 / Y-03 / Y-04 / Y-08 换主语后是否仍成立 | Y-05（`AgentHarnessPort` 与 `AgentOrchestrationPort` 是否重叠）**本轮已消解**——蓝图 §6.11 原文点名 `AgentOrchestrationPort` 就是隔离第三方编排框架的那个接缝，不需要第二个端口。其余四条仍需换主语后重判 |
| ~~V-5~~ | ~~Work Object 与 OA 的状态同步策略~~ **已于 2026-08-19 裁决** | 五项细则见 `DECISIONS.md` 2026-08-19「Work Object 与 OA 的状态同步策略」：存「上次看到的样子 + 我的处理痕迹」而非业务状态本身、后台轮询 + 打开即拉 + 手动刷新三者并存、界面显示上次拉到的状态并始终显示数据时间戳、用户标记已办记为 `待同步完成情况` 中间态（完成只认 OA）、同步失败显示旧数据加醒目提示。同日另有「凭证模型变更」决定推翻既有的「不存密码」「不静默重登」两句约束以支持后台轮询。`P2-WORK-OBJECT-001` 的 BLOCKED 已解除 |
| V-6 | OA 登录失败锁定阈值与程序登录的审计可见性 | OA 只返回累计密码错误次数，锁定阈值、时长与解锁条件**未找到**；程序登录是否在 OA 侧留下可见登录记录、是否更新在线状态**未找到**。前者是后台轮询容错边界的依据，后者决定 OA 管理员会看到什么。当前以「一错即持久失效」与「按会产生审计记录处理」的保守设计规避，但两项本身仍未验证 |

### 8.4 本文件本身的边界

- 本文件**没有**覆盖蓝图 §1、§2、§4、§5、§7、§8（除 §8.4）、§12、§14、§15。§12 / §15 此前已对照，其余章节本轮未纳入。
- 本文件**没有**对 §8.2 中任何一项排序或估算工作量——那是第③步的事。
- 本文件**没有**新增任何决定。§8.1 的七项全部是待裁决项，不是结论。

---
