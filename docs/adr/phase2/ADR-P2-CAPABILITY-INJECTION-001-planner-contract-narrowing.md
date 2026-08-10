# ADR-P2-CAPABILITY-INJECTION-001 — Planner 能力注入面收紧为结构字段

- status: accepted
- date: 2026-08-09
- origin_task_id: P1-B5-002（原始决定处）
- recorded_by: P2-GOV-SYNC-009（本 ADR 的补记棒）
- decision_makers: [雨爷]
- related_component: `app/knowledge/basic_knowledge.py`、`app/runtime/intent_router.py`、`app/ports/capability_registry.py`
- code_facts_as_of: `1bf2ba6`
- supersedes: 在 Phase 1/2 范围内收窄冻结蓝图 §6.4 / §6.4.1 / §14.2.8 的 Planner 注入面；不改变 Capability Gateway 执行前按 `capability_id + version` 加载完整定义的约定

`code_facts_as_of` 锚定本 ADR 陈述的全部代码事实；此后代码变动不使本 ADR 的决定失效，但复核代码事实时须以该提交为基线。

## 1. Context

冻结蓝图 §6.4（`:1050-1052`）定义了 **Capability Preselector**：进入 Planner 前的低成本能力
筛选层。§6.4.1（`:1057`）约定 **「Planner 只接收 Top-K 候选 Capability 的短摘要、输入输出
schema 摘要、版本、owner 和风险等级」**；§14.2.8（`:2863-2866`）在安全边界章节重申
**Top-K 摘要注入与执行前按 `capability_id + version` 加载**这一边界（不重复列举字段清单）。
实现路径建议（`:1068`）为规则匹配、标签过滤、Embedding 检索与 Policy 预过滤的轻量组合。

当前实现与此有两处实质偏离：

1. **Preselector 整层未实现。** 实际是按 `capability_id` 字典序取前 8 条 active。
   载体是 `app/knowledge/basic_knowledge.py` 的
   `BasicKnowledge.capability_input_contracts`，上限常量为 `MAX_CAPABILITY_CONTRACTS`。
   生产 `app/` 中不存在 preselector / preselect 相关模块或符号；这是限定目录和检索词的
   absence 结论，复核方法见 §5。
2. **注入内容与蓝图相反。** 实际只注入 7 个结构字段：`capability_id`、`capability_type`、
   `target_system`、`status`、`allowed_argument_keys`、`required_argument_keys`、
   `additionalProperties`（可选 `arguments_must_be`）。载体是
   `app/knowledge/basic_knowledge.py` 的 `_capability_input_contract`。
   短摘要、`name`、`owner`、`intent_tags`、schema 等自由文本一律不进 prompt。

第 2 项中「**自由文本不注入**」是 Phase 1 的**显式决定**
（`docs/phase1/tasks/P1-B5-002.md:22-24`：「不读取或注入 short_description、name、owner、
intent_tags、schema 等自由文本」）。**注意范围**：该任务决定的是「排除自由文本」，
现行的 7 字段具体构成是其后实现中形成的，不宜整体归因于该任务。

支撑物：

- 防注入约束文本：`app/runtime/intent_router.py` 的
  `_CAPABILITY_CONTRACT_SYSTEM_PROMPT` 常量（**是注入给模型的 system prompt，不是源码注释**）。
- 守卫测试：`tests/runtime/test_intent_router.py` 的
  `test_router_has_no_registry_free_text_prompt_entry`，把 `name` / `owner` /
  `short_description` / `intent_tags` 换成唯一 marker 并断言其不出现在 prompt 中。

**问题在治理程序，不在实现选择本身。** 按 `CLAUDE.md`「权威与当前阶段」的权威顺序，
已批准的架构文档高于仓库代码；任务级文档单方面收窄冻结蓝图的约定而无 ADR 记录，
属于程序缺口。本 ADR 补上该记录。

## 2. Decision

### 2.1 承认收紧是有意为之

Planner 注入面在 Phase 1/2 维持为上述 7 个结构字段。**不判定 `P1-B5-002` 越权**，其守卫测试
与 `_CAPABILITY_CONTRACT_SYSTEM_PROMPT` 继续有效，不得为「恢复蓝图约定」而弱化或删除。

### 2.2 记录真实的成立理由，并订正一处不成立的理由

**成立的理由：管理员自由文本缺少 prompt-safe 校验。**

精确表述（避免过宽）：`app/ports/capability_registry.py` 的 `CapabilitySpec` 中，
`type` / `target_system` / `status` 等枚举字段有 `Literal` 约束，schema 字段有 JSON
object 约束；**但 `short_description`、`name`、`intent_tags` 等自由文本字段缺少长度上限、
字符集限制与 prompt 注入转义**。
`tests/infra/persistence/capability_registry/test_capability_spec_validation.py` 的
`test_open_str_arbitrary_values_locked` 主动锁定了「任意标点按原值保留」这一开放语义。
前端 `web/src/pages/admin/RegistryPage.tsx` 的 `RegistryPage` 中，`name`、`owner`、
`short_description` 只有 `required` 规则，`intent_tags` 没有表单校验规则。

在缺 prompt-safe 校验的前提下把管理员自由文本送进 prompt，风险是真的。

**不成立的理由：外部系统污染。**

`short_description` 当前可确认的受治理持久化入口包括两条：

- Admin API 手填：`app/admin/registry.py` 的 `AdminCapabilityCreate.to_draft_spec` 与
  `AdminRegistryService.create_capability`；
- 代码常量灌库：`scripts/smoke/capabilities.py` 的 `expected_oa_capabilities`，经
  `scripts/manage_oa_capabilities.py` 的 `_build_parser` 与 `_manage_registry` 在显式
  `--apply` 时入库。

限定在 `app/infra/adapters/` 目录，对 `CapabilityRegistry`、`capability_registry` 与
`short_description` 的基线检索均为零命中，说明 **Adapter 无写 Registry 的调用**；
`tests/contract_packs/oa/ecology9-pending-workflows-v1/`、
`tests/contract_packs/oa/ecology9-pending-workflows-v2/` 与
`tests/contract_packs/oa/ecology9-system-messages-v1/` 的 profile / sample / fingerprint
中**不含 `short_description`**。这些都是限定目录、文件集和检索项的 absence 结论，
复核方法见 §5。**因此当前 OA Adapter / Contract Pack 链没有把外部响应写成描述词的路径**，
把收紧理由归因于「防外部注入」没有事实支撑。

**真正不可信的输入是能力返回值，而那一侧恰恰没有契约校验。**
对生产 `app/` 的全局检索结论：Registry 的 `output_schema` / `output_schema_digest` 只存在于：

- `app/ports/capability_registry.py` 的 `CapabilitySpec`；
- `app/infra/persistence/capability_registry/schema.py` 的 `capabilities`；
- `app/infra/persistence/capability_registry/repository.py` 的 `CapabilitySpecPatch`；
- `app/admin/registry.py` 的 `AdminCapabilityCreate` 与 `AdminCapabilityView.from_spec`。

执行链路零读取。`app/infra/gateway/capability_gateway.py` 的 `_validate_arguments`
**只校验入参**；`app/evaluator/terminal.py` 的 `TerminalEvaluator.evaluate` 判定分支只依赖
`business_status`，`error_code` 仅透传，不看返回内容。

**结论：信任边界画反了。** 本 ADR 明确记录这一点，因为它决定了恢复路径的先后顺序。

### 2.3 明确恢复路径与解除条件

收紧不是终态。恢复按下表推进，**b2 硬依赖 b1**，返回值校验独立可并：

| 步 | 内容 | 量级 | 依赖 |
|---|---|---|---|
| b1 | 管理员自由文本加 prompt-safe 校验（长度 / 字符集 / 转义） | 小 | — |
| b2 | 有界短摘要 + 输入输出 schema 摘要 + version + owner + risk_level 进 prompt，恢复蓝图 §6.4.1 约定 | 小 | **硬依赖 b1** |
| b3 | 截断从「字典序静默」改为「相关性 Top-K + 超限必须提示」 | 中 | b2 |
| b4 | Embedding 召回接线 | 大 | b3 |
| — | 启用 `output_schema`，Gateway 在返回侧校验 | 中 | 独立，可并行 |

**解除条件：** b1 与 b2 落地后，本 ADR 的 §2.1 自动失效，Planner 注入面恢复为蓝图 §6.4.1
约定；届时须另发 ADR 记录恢复，**不得静默改回**。

### 2.4 本 ADR 不改代码

本 ADR 是治理记录。`app/`、`tests/`、Golden fixtures、`FROZEN_GT_IDS` 与冻结产物均不变。

## 3. Consequences

- **管理员维护的描述词在 LLM 选能力这一环不起作用。** 能力选择实际靠 `capability_id`
  精确匹配或 `intent_tags` 唯一命中，见 `app/runtime/runtime.py` 的
  `RuntimeImpl._select_capability`。Admin 创建表单仍直接接收这些自由文本，见
  `web/src/pages/admin/RegistryPage.tsx` 的 `RegistryPage`。

- **能力数超过 8 后，字典序靠后的能力对 LLM 永久不可见**，且失败时报
  `no_capability_found`；处理入口为 `app/runtime/runtime.py` 的
  `RuntimeImpl._finish_no_capability_found`，对外固定引导见
  `app/knowledge/basic_knowledge.py` 的 `BasicKnowledge.no_capability_guidance`。
  这是**静默的错误归因**：把「我没看见」报成「它不存在」。

  `MAX_CAPABILITY_CONTRACTS = 8` 与字典序截断由完整 commit
  `cb6438abae8790be5f94e0a5dc8e2b4123b5253d` 引入；在 `1bf2ba6` 的
  `docs/phase2/` 目录盘点中未见对应 task 文档，真实来源未见记录；
  `tests/knowledge/test_basic_knowledge.py` 的
  `test_contract_payload_is_count_bounded_without_partial_contracts` 只断言合同数量等于上限，
  不解释理由。`app/runtime/intent_router.py` 中另有 `MAX_KNOWLEDGE_ITEMS = 8`
  （源自 `P1-B5-002`，讲的是**知识条数**）——**数值相同，但不足以证明因果；能力上限 8
  的真实来源未见记录。**

  **蓝图侧对照（锚点经 2026-08-09 四轮复核订正）：**

  - **主锚点 §6.4.1（`:1057`）**「Planner 只接收 **Top-K 候选** Capability 的短摘要……」。
    Top-K 的请求相关候选含义由 §6.4.2（`:1064-1068`）的排序/检索建议与 §6.4.3
    （`:1074`）共同支撑；**字典序前 N 不是请求相关 Top-K**。
  - §6.4.3（`:1074`）预警了同一后果：「新注册 Capability 可能长期进不了 Top-K，
    导致『能力已发布但用户用不上』」。
  - §6.3.1（`:978`）「**超预算时**不得简单从尾部截断」是**超预算触发**的降级约束；
    本实现为**无条件**截断，连 §6.3.4（`:1025-1035`，列表 `:1028-1034`）的降级阶梯
    都未进入。**作旁证可用，不作为直接违反项。**
  - **`:1040`「禁止无提示截断安全策略」不适用于本条**：其宾语是安全策略，
    不能证明 Capability 合同属于该情形。先前草案曾误引此锚点，此处已删除该依据。

- **项目已知该风险但只告警未修**：`scripts/smoke/capabilities.py` 的
  `OA_CAPABILITY_CONTEXT_PROBES` 由 `scripts/smoke/runner.py` 的
  `_classify_capability_registry` 在截断时判定并报 `context_truncated`。

- **新增能力若无对应 Adapter 模型，返回值即无任何校验**（执行链路推论）。
  现有校验只有 `app/infra/adapters/oa/contracts.py` 的
  `OAPendingWorkflowCollection`、`OASystemMessageCollection`、
  `build_structural_fingerprint` 与 `compare_structural_fingerprints`。

上述四条同步登记为 `docs/phase2/PHASE2_PLAN.md` 欠债，本 ADR 不代替欠债登记。

## 4. Verification

- 本 ADR 为纯文档补记，验证限于：变更不触及 `app/`、`tests/`、`scripts/`、`web/`；
  全量 pytest 与 Golden Gate 在合并候选上保持实跑基线且 0 skipped / 0 failed。
- **蓝图锚点**（带行号）须逐条重验命中；蓝图为冻结文档，行号不应漂移，漂移即说明冻结被破坏，
  属停手事项。
- **正向代码事实**使用「完整文件路径 + 符号名」；其在 `code_facts_as_of` 基线上的语义用
  `git show` / `git grep` 复核。当前工作树的 grep 只用于发现后续符号漂移，不能代替基线语义复核。
- absence、Contract Pack、历史 commit 与目录盘点按 §5 的独立体例复核，不伪装成正向符号证据。
- 恢复路径 b1–b4 与返回值校验均未实施，本 ADR 不声称任何已具备能力。

## 5. 锚点体例说明（为什么代码侧不带行号）

ADR 永久留在仓库，而代码行号必然随后续改动漂移。逐条追修代码行号不可持续，且 ADR 是治理
决定记录，不是代码审计报告。因此采用下列可复核体例：

| 事实类型 | 正式锚点 | 基线复核方式 |
|---|---|---|
| 冻结蓝图事实 | 蓝图行号 | 逐条读取冻结文件；漂移即停手 |
| 正向代码事实 | 完整路径 + 符号名 + `code_facts_as_of` | `git show` / `git grep` 基线提交；当前 grep 只检查后续漂移 |
| absence 结论 | 限定目录或文件集 + 精确检索词 + `code_facts_as_of` | 对基线提交执行限定范围的 `git grep`，保留零命中结论 |
| Contract Pack 字段事实 | 完整 artifact 目录/文件集 + JSON 字段路径或检索项 | 对基线 artifact 执行解析或限定检索 |
| 历史事实 | 完整 commit SHA | `git show --stat`、`git show` 与必要的 blame |
| 仓库目录盘点 | 限定目录 + `code_facts_as_of` | `git ls-tree` 基线提交 |

本 ADR 中的 `related_component` 和 Verification 范围只是元数据/范围，不是证据锚点，不受
「完整路径 + 符号名」规则约束。absence 结论的检索范围与检索词已在正文写明；必要时以
`git grep <检索词> 1bf2ba6 -- <限定路径>` 复核。此次 `_scratch/` 审计稿只是本任务的临时工作材料，
不进 Git、不是正式 ADR 的持久证据，正式结论不得依赖其可获取性。
