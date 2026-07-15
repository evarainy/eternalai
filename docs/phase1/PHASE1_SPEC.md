# Phase 1 详细规格 — B2-B5

status: draft

本文件是 B2-B5 范围与验收的权威结果契约；当本文件与 `docs/phase1/PHASE1_PLAN.md` C.1/C.2 冲突时，以本文件为准。下游 task prompt、Task Record 与 Review 必须引用稳定小节 ID，行号只作定位辅助。文字修订不得复用已删除 ID；删除 ID 时必须在修订记录中保留 tombstone。

来源权威序为 `BLUEPRINT_ERRATA.md > PHASE1_TECHNICAL_BASELINE.md > MVP spec v1.0.11（阈值与验收语言） > blueprint（方向）`。Phase 1 structured-output 基线为 raw OpenAI SDK、vLLM/OpenAI-compatible endpoint、`response_format={"type":"json_object"}` 与 Pydantic v2 `model_validate`/`Literal` 校验；模型、provider、wrapper、retry、tool-calling 或 structured-output mode 的改变都必须另行验证，本文件不作新依赖决策。

四批按 B2 → B3 → B4 → B5 顺序交付。每批只约束可观察结果与验收证据，不规定函数签名、数据结构实现或代码级方案。`app/ports/` 的 13 个 Protocol 保持冻结；本文件引用 Port 只表示该批次的直接验收面，不授权新增或修改 Port。若下游 Plan 证实冻结契约不足，必须停手上报。

Golden 总量不得少于 10，正向总体通过率必须 `>= 80%`，负向、边界和安全拒绝必须 `100%` 通过（MVP spec §20.1 L4501-L4503）。当前 fixture 只有 `positive`/`negative` 分类，边界和安全拒绝并入 negative 分母；`not_applicable` 排除分母但必须显式报告，`skipped` 不得静默。现有 frozen Golden 只作为回归基线；任何增量均受各章第四节的授权规则约束。

**PHASE1_PLAN C.1 交付与排除项 → 稳定 ID 完整映射**

| C.1 项 | 类型 | 稳定 ID | 责任边界 |
|---|---|---|---|
| Web/CLI → Intent Router → Workflow/Tool → Policy Precheck → Trace → Evaluator | 交付 | `S-B2.1`, `S-B3.1`, `S-B4.1`, `S-B5.1` | B2/B3/B4/B5 分别负责入口选择、身份策略、执行、评测收口，不设置会掩盖纵切责任的单一主 ID。 |
| Admin Lite：Registry / Policy / Trace / 基础用户角色 / Binding 状态 | 交付 | `S-B5.1`, `S-B5.2`, `S-B5.3`, `S-B5.4`, `S-B5.5` | B5 独占。 |
| Session Memory + 基础 Semantic/System Knowledge | 交付 | `S-B5.1`, `S-B5.3`, `S-B5.4`, `S-B5.5` | B5 独占最小层。 |
| IdentityMapping / Binding precheck / handback / no capability | 交付 | `S-B2.1`, `S-B2.4`, `S-B3.1`, `S-B3.4`, `S-B5.1` | B2 收口无能力，B3 收口身份与策略；blueprint §13 L2696 的管理员配置入口归 B5。 |
| Workflow Engine 轻量版 | 交付 | `S-B4.1`, `S-B4.2`, `S-B4.3`, `S-B4.4`, `S-B4.5` | B4 独占。 |
| 真实业务系统写操作 | 排除 | `S-B2.1`, `S-B2.5`, `S-B3.1`, `S-B3.5`, `S-B4.1`, `S-B4.5`, `S-B5.1`, `S-B5.5` | 四批只使用 Mock/低风险能力；来源为 blueprint §13 L2698。 |
| 生产级 Controlled Exploration | 排除 | `S-B2.1`, `S-B2.5`, `S-B5.1`, `S-B5.5` | 无能力时停在标准终态；正确承重锚为 blueprint §13 L2697。 |
| Dynamic Tool Composition | 排除 | `S-B2.1`, `S-B2.5`, `S-B4.1`, `S-B4.5` | 只允许 Workflow/Published Skill 明示的静态链；正确承重锚为 blueprint §2.3 L103、§6.5 L1138。 |
| 复杂 DAG / 长事务 | 排除 | `S-B4.1`, `S-B4.5` | B4 仅实现轻量 Workflow；来源为 blueprint §4.3.3 L468。 |

在 spec status、P1-SPEC-001 Task Record、`task_logs/INDEX.md`、集成与 CI 证据、human result acceptance 全部一致前，B2 不得解锁；`draft` 状态本身不构成批准。

## B2 — Intent → Capability 选择闭环

### S-B2.1 范围与非目标

B2 承担主链的 Web/CLI 入口、Intent Router、Capability 选择与选择段 Trace（blueprint §13 L2685-L2686）。输入经当前 Runtime 进入后，必须形成可校验的 intent 结果，并且只从 Registry 中符合状态与请求约束的既有 Capability 产生选择；选择行为必须可重复、可追溯，但本章不锁定规则、标签或向量方案。

当没有已注册且可用的 Capability 时，Task 必须进入 `no_capability_found`，返回 `capability_not_found` 语义的标准 ResponseEnvelope，Trace 至少形成 `task_created`、`intent_parsed`、`no_capability_found`、`response_envelope_created`，且不得进入 Identity、Policy、Gateway 或 Adapter。blueprint §13 L2696 的管理员配置入口留给 B5。

本章不接入真实业务系统写操作（blueprint §13 L2698），不开放生产级 Controlled Exploration（blueprint §13 L2697），不开放 Dynamic Tool Composition（blueprint §2.3 L103、§6.5 L1138），不把 embedding 或其他选择实现写成依赖决策，也不重新规格化 Phase 0 已交付的 Runtime/Gateway/Trace 骨架。

### S-B2.2 涉及的 frozen port 清单

| Frozen Protocol | B2 直接验收责任 |
|---|---|
| `RuntimePort` | 接受 Web/CLI 请求并返回标准 ResponseEnvelope。 |
| `LLMProviderPort` | 在既有 LLM 边界内提供 intent 解析所需的模型调用。 |
| `StructuredOutputPort` | 承载 structured-output 的校验结果；实现必须服从本文头部技术基线。 |
| `CapabilityRegistryPort` | 提供候选 Capability 的既有注册信息与状态。 |
| `TaskStorePort` | 持久化选择成功或 `no_capability_found` 的 Task 终态。 |
| `TracePort` | 记录 intent、选择或无能力终态的可审计事件。 |

`ResponseEnvelope` 是 `RuntimePort` 的冻结返回契约，不计为第 14 个 Port。本章对上述 Protocol 零修改提议。

### S-B2.3 验收来源

| 证据层 | 必须证明的结果 |
|---|---|
| Golden | 现有 GT-008 的未注册 Capability 短路继续成立；端到端结果符合本文总阈值，Adapter 未调用，Trace 与 Task 终态一致。 |
| pytest | 覆盖 Web/CLI 输入归一、structured-output 校验、候选过滤、唯一选择、未注册/disabled 边界、标准 ResponseEnvelope 与失败不越过选择段。 |
| `tests/architecture` | 证明应用层只依赖 frozen Port，Port 不反向依赖 infra，B2 未绕过 Registry/Runtime/Trace 边界且未引入新的 Port。 |

验收还必须核对 raw SDK + JSON object + Pydantic v2 基线未被替换；任何算法或依赖取舍留给获授权的下游 Plan。

### S-B2.4 golden 覆盖增量

现有冻结来源仅确认 GT-008 的未注册能力场景。本批要求建立以下增量候选：disabled Capability 不能被选择；多个候选无法按已批准规则唯一收敛时不得任意调用 Adapter；Intent/选择实现完成后 GT-008 仍保持短路与完整 Trace。候选的最终 ID、输入与预期结果由后续授权流程确定，不在本文件中预占冻结身份。

**强制授权规则：golden 扩充必须由专门任务执行，该任务必须显式授权修改 `FROZEN_GT_IDS` / fixtures，并经人批；本 spec 不直接写 fixture JSON，也不授权隐式扩充。**

新增后，disabled、歧义选择与所有无能力负向/边界场景必须按 negative 口径 `100%` 通过。

### S-B2.5 裁剪决策记录

| PDR 决策 | 裁剪理由 | 重开条件 |
|---|---|---|
| 无能力即终止，不进入生产级 Controlled Exploration | Phase 1 默认关闭，承重锚为 blueprint §13 L2697。 | 后续阶段具备沙箱、白名单、Policy、Trace 与审批，并有独立任务和人批。 |
| 不允许 Dynamic Tool Composition | Phase 1 只允许明示静态链；承重锚为 blueprint §2.3 L103、§6.5 L1138。 | 独立验证、治理契约与人批齐备。 |
| 不锁定 embedding 或其他选择依赖 | C.2 是切分建议，不是依赖授权。 | ADR、独立验证和人批。 |
| 不执行真实业务系统写操作 | Phase 1 只用 Mock/低风险能力；blueprint §13 L2698。 | 不属于本 Phase 1 spec。 |

## B3 — Identity / Policy 预检闭环

### S-B3.1 范围与非目标

B3 承担 IdentityMapping Mock 表、Binding 状态预检、Policy Precheck、confirm 路径及该段 Trace（blueprint §13 L2688、L2693-L2695）。选定 Capability 后必须先解析执行身份与 scope，再作 Policy 决策；身份或策略未放行时不得调用 Gateway 下游 Adapter。

`unbound`、`expired`、`revoked` 必须返回标准绑定引导，其中未绑定呈现 SDUI `operator_handback_card`；多 active binding 且 scope 不明确时，错误原因为 `needs_binding_scope`，UI 行为为 `ui.action=clarify_scope`。`clarify_scope` 不是新的 Gateway error code，且不得 fallback 到第一条 binding 或 `system_scope`。Policy `deny` 形成阻断终态，`confirm` 形成 `waiting_user`/确认响应，二者不得混同为允许。

本章只完成 Mock/低风险能力的预检闭环，不把预检解释为真实业务系统写授权（blueprint §13 L2698），不实现完整 IAM、凭证生命周期或生产级 Binding 平台，也不重新定义 Phase 0 Gateway 短路行为。

### S-B3.2 涉及的 frozen port 清单

| Frozen Protocol | B3 直接验收责任 |
|---|---|
| `RuntimePort` | 串联已选择 Capability 与身份/策略响应。 |
| `CapabilityGatewayPort` | 保持 Identity/Policy 未放行时的执行短路。 |
| `IdentityMappingPort` | 返回既有 binding 状态、目标系统与 scope 结果。 |
| `PolicyGuardPort` | 返回 `allow`、`deny` 或 `confirm` 决策。 |
| `TaskStorePort` | 记录 blocked/failed/waiting 等可观察状态。 |
| `TracePort` | 记录身份检查、策略检查、阻断或确认事件。 |

本章不提出新增 IAM、Role 或 Binding Port；基础角色与管理状态的可见性归 B5，并继续使用现有契约。

### S-B3.3 验收来源

| 证据层 | 必须证明的结果 |
|---|---|
| Golden | GT-009 policy deny、GT-010 identity unbound、GT-012 多 active binding scope 不明继续满足 Adapter 零调用、终态、SDUI 与 Trace 约束。 |
| pytest | 分别覆盖 active/unbound/expired/revoked/needs-binding-scope、allow/deny/confirm、scope 选择、无 fallback、Task 状态与 ResponseEnvelope 映射。 |
| `tests/architecture` | 证明 Runtime/Gateway 仅经 `IdentityMappingPort` 与 `PolicyGuardPort` 协作，Port/infra 依赖方向不反转，阻断路径不存在旁路。 |

对 GT-012 的判定必须同时核对 `error_code=needs_binding_scope` 与 `ui.action=clarify_scope` 两层语义。

### S-B3.4 golden 覆盖增量

在 GT-009/010/012 现有基线上，本批要求建立以下增量候选：expired 与 revoked binding 均阻断执行；account-set/device-domain/resource-scope 的多绑定边界不得 fallback；Policy `confirm` 在确认前不得调用 Adapter；新的 precheck 与 B4 的 step 前预检不能绕过 deny。候选未完成专门任务与审批前，不是当前 frozen Golden 来源。

**强制授权规则：golden 扩充必须由专门任务执行，该任务必须显式授权修改 `FROZEN_GT_IDS` / fixtures，并经人批；本 spec 不直接写 fixture JSON，也不授权隐式扩充。**

新增的身份、scope、deny 与 confirm 负向/边界场景必须 `100%` 通过。

### S-B3.5 裁剪决策记录

| PDR 决策 | 裁剪理由 | 重开条件 |
|---|---|---|
| 只实现 Mock IdentityMapping 与基础 binding 状态 | blueprint §13 L2693-L2695 只要求 Phase 1 最小预检闭环。 | 生产身份源、凭证治理与审计另立任务并人批。 |
| confirm 是停止态，不是隐式 allow | MVP spec §12.4.1 L3489 要求确认前 Adapter 零调用。 | 只能由显式用户确认恢复，具体实现由下游任务设计。 |
| 不接入真实业务系统写操作 | Policy 预检不等于写授权；blueprint §13 L2698。 | 不属于本 Phase 1 spec。 |
| 不开放 Controlled Exploration 或动态工具组合 | 两者为全局裁剪，分别以 blueprint §13 L2697 与 §2.3 L103/§6.5 L1138 为准。 | 满足各自治理、验证与人批条件。 |

## B4 — Workflow 轻量引擎与执行闭环

### S-B4.1 范围与非目标

B4 承担 Workflow/Tool 执行与 step Trace（blueprint §13 L2687；§4.3.2 L438-L446）。轻量引擎必须支持线性步骤、简单条件分支、step 输入/输出映射、step 级 Policy、失败停止或有限重试、`human_gate`、Workflow 版本锁定和全链路 Trace。每一步只能引用已注册 Capability，并经 Capability Gateway 与 Policy Guard；本批只执行 Mock/低风险能力。

有限重试不得变成长事务：只有已获下游任务明确批准的可重试失败可重试，耗尽后停止，后续 step 不执行。`human_gate` 未确认时停在 waiting/confirm，确认前后 Workflow 版本保持锁定。静态多步链必须由 Workflow/Published Skill 显式声明；不允许 LLM 在运行时临时组合 Tool（blueprint §2.3 L103、§6.5 L1138）。

本章不支持复杂并行 DAG、长事务、跨天恢复、外部 Workflow 引擎、自动 Skill→Workflow 或 LLM 动态改写结构（blueprint §4.3.2 L448-L454、§4.3.3 L468），不接入真实业务系统写操作（§13 L2698），也不自行发明 blueprint 所引用但仓库缺失的 `workflow_runtime_spec.md` schema。

非代码级子任务建议如下；每项在启动前仍须生成正式 task prompt：

| 子任务 | 结果边界 | 前置/交接 |
|---|---|---|
| 引擎骨架 | 线性步骤、简单分支、IO 映射、版本锁定与 step Trace 的可观察闭环。 | B3 完成；交给失败策略与端到端验收。 |
| step Policy | 每一步调用前的 Policy 决策、deny/confirm 短路与证据。 | 引擎骨架可承载 step 状态。 |
| human_gate | waiting/confirm、恢复条件与版本不漂移的结果契约。 | step Policy 的 confirm 语义稳定。 |
| 失败停止与有限重试 | timeout/error 的有限重试、耗尽终态与后续 step 零调用。 | 引擎骨架与 Gateway 错误映射稳定。 |
| 纵切回归 | 将上述结果绑定到 Golden、pytest 与架构边界证据。 | 前四项完成；不新增实现范围。 |

### S-B4.2 涉及的 frozen port 清单

| Frozen Protocol | B4 直接验收责任 |
|---|---|
| `RuntimePort` | 启动轻量 Workflow 并返回最终或 waiting ResponseEnvelope。 |
| `CapabilityGatewayPort` | 作为每个 Capability step 的唯一执行入口。 |
| `PolicyGuardPort` | 提供 step 前 allow/deny/confirm 决策。 |
| `AdapterPort` | 在 Gateway 之后执行既有 Mock/低风险能力。 |
| `SecretProviderPort` | 保持 Adapter 所需敏感信息的既有受控边界，敏感值不得进入 Trace。 |
| `TaskStorePort` | 持久化 Workflow/step 可恢复所需的当前任务状态与事件证据，但不承载跨天长事务。 |
| `TracePort` | 记录 step Policy、Gateway、Adapter、错误映射与终态。 |

`JobQueuePort` 保持冻结，但不属于本章的直接验收面：本章不以队列承载 Workflow 状态，不将任何队列实现写成必装依赖，也不引入新的 Port 类型。

### S-B4.3 验收来源

| 证据层 | 必须证明的结果 |
|---|---|
| Golden | 现有 GT-001/005/007 timeout injection companion 保持标准失败与 `adapter_error_mapped`；新增 Workflow 场景获批后证明 step 顺序、停止、重试耗尽和 human gate。 |
| pytest | 覆盖线性执行、简单分支、IO 映射、版本锁定、step deny/confirm、有限重试边界、后续 step 零调用、Gateway 唯一入口及 Trace 次序/终态。 |
| `tests/architecture` | 证明引擎不直接依赖 Adapter 实现、不绕过 Gateway/Policy、不让 Port 依赖 infra，且未引入复杂 Workflow 或队列旁路。 |

Adapter timeout/error 的合法 Trace 必须含 `adapter_error_mapped`，失败路径不得出现 `task_completed`；该事件由 BLUEPRINT_ERRATA E-003 与冻结 `TracePort` 共同确认，不是 Port 缺陷。

### S-B4.4 golden 覆盖增量

在现有 timeout injection companion 基线上，本批要求建立以下增量候选：单 step timeout 经有限重试耗尽后停止且后续 step 零调用；step Policy deny/confirm 阻断后续执行；`human_gate` 未确认时保持 waiting/confirm，确认前 Adapter 零调用，恢复后版本锁定不漂移；简单分支与 IO 映射的正向闭环。候选获批前不占用 frozen ID。

**强制授权规则：golden 扩充必须由专门任务执行，该任务必须显式授权修改 `FROZEN_GT_IDS` / fixtures，并经人批；本 spec 不直接写 fixture JSON，也不授权隐式扩充。**

timeout、deny、confirm、human gate 与错误映射等负向/边界场景必须 `100%` 通过。

### S-B4.5 裁剪决策记录

| PDR 决策 | 裁剪理由 | 重开条件 |
|---|---|---|
| 仅线性步骤与简单分支 | 复杂 DAG、长事务、跨天恢复、外部引擎不进入 Phase 1；blueprint §4.3.2 L448-L454、§4.3.3 L468。 | 后续阶段另立架构与可靠性任务。 |
| 只允许显式静态工具链 | Dynamic Tool Composition 在 Phase 1 不开放；blueprint §2.3 L103、§6.5 L1138。 | 沙箱、白名单、Policy、Trace、独立验证与人批齐备。 |
| 不补写缺失的 Workflow schema | 本任务不得发明 `workflow_runtime_spec.md` 的内容或代码级契约。 | 下游若确需该源，先补授权来源契约。 |
| 不把队列用于 Workflow 状态或长事务 | 本批验收不需要该依赖决策，且会扩大可靠性边界。 | 独立 ADR、升级条件与人批。 |
| 不接入真实业务系统写操作 | blueprint §13 L2698。 | 不属于本 Phase 1 spec。 |

## B5 — Session Memory、Evaluator 与 Admin Lite 收口

### S-B5.1 范围与非目标

B5 承担主链 Evaluator 与终局 Trace（blueprint §13 L2689-L2690），实现最小 Session Memory、基础 Semantic/System Knowledge（§10.1 L2166-L2168），并提供 Admin Lite 的 Registry、Policy、Trace、基础用户角色与 Binding 状态能力（§13 L2691）。blueprint §13 L2696 的管理员配置入口在本章落地：无能力终态可引导到 Admin Lite，但不得自动创建或执行未注册能力。

Session Memory 只保持当前任务/当前会话上下文；基础知识只覆盖企业术语、能力说明、Mock 系统说明和少量制度模板。Evaluator 必须为终局结果形成可追溯评测结论；评测失败或缺失不得把业务失败伪装为成功。Admin Lite 只提供最小管理可见性与现有冻结契约可承载的受控动作，基础角色必须限制 Registry/Policy/Trace/Binding 管理面。

本章不实现完整 User Profile、Episodic、Procedural Memory 或 Knowledge Vault（blueprint §10.1 L2170-L2174），不建设完整 IAM、通用评测平台或生产运维后台，不开放生产级 Controlled Exploration（正确承重锚为 blueprint §13 L2697），不通过 Admin Lite 解锁真实业务系统写操作（§13 L2698）。若下游实现必须新增或修改 Port，必须停手而不是把本章解释为授权。

### S-B5.2 涉及的 frozen port 清单

| Frozen Protocol | B5 直接验收责任 |
|---|---|
| `RuntimePort` | 汇总 Memory、Evaluator 与最终 ResponseEnvelope。 |
| `SessionStorePort` | 提供当前 session 的既有存取边界。 |
| `CapabilityRegistryPort` | 支撑 Registry 状态、受控管理结果与无能力配置入口。 |
| `IdentityMappingPort` | 提供 Binding 状态与 scope 的现有查询结果。 |
| `PolicyGuardPort` | 对管理面与业务请求形成既有 Policy 决策。 |
| `TaskStorePort` | 提供 Task 终态与当前任务证据。 |
| `TracePort` | 记录终局、Evaluator 和管理动作的可审计事件。 |

角色、评测、知识和管理结果必须在上述冻结契约与既有应用边界后实现；契约不足即触发下游 stop condition，不在本章引入新的 Port 类型。

### S-B5.3 验收来源

| 证据层 | 必须证明的结果 |
|---|---|
| Golden | 先回归 B2-B4 全主链与本文总阈值；B5 专属负向场景只有经 `S-B5.4` 的专门任务和人批建立后，才计入 frozen Golden 验收。 |
| pytest | 覆盖同 session 上下文连续性、跨 session/tenant 隔离、基础知识命中边界、Evaluator 成功/失败映射、角色拒绝、Registry/Policy/Trace/Binding 最小管理行为与无能力配置入口。 |
| `tests/architecture` | 证明 Memory/Evaluator/Admin Lite 不绕过 Runtime/Store/Registry/Policy/Trace，不让 web/infra 反向污染 Port，且未新增或修改 frozen Port。 |

验收必须区分“业务执行结果”与“Evaluator 结果”，并证明 Admin Lite 的展示或操作不能改变 B2-B4 的 Gateway、Identity、Policy 安全顺序。

### S-B5.4 golden 覆盖增量

MVP spec §12.5/§20.1 没有冻结 B5 专属 Session Memory、Evaluator 或 Admin Lite 场景，因此下列内容只是必须由专门任务建立并获人批的新候选，不得冒充现有冻结来源：跨 session/tenant 记忆泄漏必须阻断；Evaluator 失败不得伪装业务成功；无管理角色的 Registry/Policy/Binding 管理动作必须拒绝并留 Trace；无能力配置入口不得自动注册或执行 Capability。

**强制授权规则：golden 扩充必须由专门任务执行，该任务必须显式授权修改 `FROZEN_GT_IDS` / fixtures，并经人批；本 spec 不直接写 fixture JSON，也不授权隐式扩充。**

专门任务必须补齐直接 source anchor 或经 PDR 固化新验收语义；获批后的隔离、权限和错误处理场景按 negative 口径 `100%` 通过。

### S-B5.5 裁剪决策记录

| PDR 决策 | 裁剪理由 | 重开条件 |
|---|---|---|
| Memory 仅实现 Session + 基础 Semantic/System Knowledge | blueprint §10.1 L2166-L2174 明确其余记忆层仅保留结构位。 | 后续专门治理 spec、数据边界与人批。 |
| Evaluator 只做主链终局可追溯评测 | Phase 1 目标是最小闭环，不是通用评测平台。 | 独立评测平台任务与验收来源。 |
| Admin Lite 只做最小管理面 | blueprint §13 L2691 要求 Lite；完整 IAM/生产运维会扩大范围。 | 独立权限、运维与安全设计。 |
| 生产级 Controlled Exploration 保持关闭 | 正确承重锚为 blueprint §13 L2697。 | 沙箱、白名单、Policy、Trace、审批和独立验证全部具备。 |
| 不通过管理面解锁真实写操作或未注册能力 | blueprint §13 L2696-L2698 要求配置入口与执行边界分离。 | 不属于本 Phase 1 spec。 |
| B5 新 Golden 不冒充冻结来源 | §12.5/§20.1 未提供 B5 专属场景语义。 | 专门任务补锚或 PDR、显式冻结授权与人批。 |
