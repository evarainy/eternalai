# Phase 1 启动 Plan — EternalAI (修订版 v4)

**结论先行**：Phase 0 已全部冻结且验收通过，13 个 Port 契约干净自洽，可以启动 Phase 1。本 Plan 只读核对、不动任何文件，等人批准后才进入执行。

**建议的 Phase 1 切分策略 = 沿 Runtime 主链做纵切（vertical slice）。首批先落 4 个准备型任务**：(1) 目录骨架 + Phase 1 派生模板、(2) golden-task 真回归门、(3) 蓝图勘误、(4) 部署参数登记。其中 **golden-task 真回归门（P1-GATE-001）是前置任务**，因为后续实现型纵切 B2-B5 的自动验收依赖它；**部署参数登记（P1-PARAM-001）天然等 infra 回值，不算无条件任务**。理由见 C 节。

> **说明（给非技术读者）**：Phase 0 把"接口长什么样"全部定死了（像先画好所有插座的形状），Phase 1 是"往插座里插真东西"（把 Mock、规则、数据库实现接上，跑通"用户说一句话→查/办/生成"的最小闭环）。这次修订发现一个关键问题：我们一直以为有一道"自动质检门"在卡 golden task 的通过率，**实际核查代码后发现这道门是空的**——它只把结果打印出来就放行，从不因为没达标而拦截。所以首批必须先把这道质检门真正建起来，否则后面的实现任务"通过"是没有保障的。

---

## A. 规则确认（沿用，不重写）

确认并继续遵守 `CLAUDE.md`（Phase 1 v2.0.0）既有治理，本 Plan 不修改治理文档：

| 治理项 | 规则（来源 `CLAUDE.md` §Git workflow / §Phase 1 rules） |
|---|---|
| 主干 | 续用 `phase0/main`（不是 `main`） |
| 任务分支 | `phase1/<task_id>`，例 `phase1/P1-SKEL-001` |
| Commit | `phase1(<task_id>): <简要描述>`；Merge `merge phase1(<task_id>): ...` |
| Task Record | 沿用 Phase 0 YAML schema（`docs/dev/task_record_schema.yaml`）+ INDEX 行 |
| 契约冻结 | `app/ports/` 13 文件 FROZEN，未经显式授权 + 人类批准不得改 |
| 六边形边界 | `app/ports/` 不得 import `app/infra/`；Runtime 不得直接 import Adapter/execution_fabric（`tests/architecture/` 自动校验） |
| LLM 基线 | Qwen + vLLM raw JSON（`json_object` + Pydantic `Literal[...]`，单遍无重试）；**禁引入 instructor / PydanticAI**（`docs/phase0/PHASE1_TECHNICAL_BASELINE.md` §2/§3.1） |
| 执行纪律 | 单 session 单 task_id；先出 Plan 等人批准；每 3 task 跑一次 `BOUNDARY_CHECKLIST.md` |

唯一需要本 Plan 新定的：`docs/phase1/` 目录布局（`CLAUDE.md` §Phase 1 rules 原文 "Phase 1 `docs/phase1/` layout TBD in the Phase 1 Plan"，留给本文档拍板）。

---

## B. `docs/phase1/` 目录布局 + Phase 1 派生模板（新建，需人类拍板）

已确认 `docs/phase1/` 当前不存在（Glob `docs/phase1/**/*` 无结果）。建议布局，镜像 `docs/phase0/` 的成熟约定：

```
docs/phase1/
├── PHASE1_PLAN.md              ← 本 Plan 批准后落盘（单一权威范围/切分文档）
├── PHASE1_SPEC.md              ← Phase 1 详细 spec（B2-B5 实现型任务的硬前置，见 C.1 / F）
├── TASK_INDEX.md               ← Phase 1 任务依赖 DAG（新建，见下方"衔接决策"）
├── TASK_PROMPT_TEMPLATE.md     ← Phase 1 派生任务模板（不复用 phase0 正文，见 B.2）
├── BLUEPRINT_ERRATA.md         ← 蓝图勘误 + 澄清登记（P1-ERRATA-001 产出，集中放此）
├── tasks/                      ← 每任务 per-task prompt：tasks/<task_id>.md
│   └── P1-XXX-NNN.md
└── task_logs/                  ← Task Record YAML + INDEX
    ├── INDEX.md                ← 人类维护的验收行表
    └── P1-XXX-NNN_<YYYYMMDD_HHMMSS>_<passed|failed|blocked>.yaml
```

### B.1 命名 / 放置 / 清理约定

对齐 `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` 与 phase0 TASK_INDEX：

| 约定 | 规则 |
|---|---|
| Task Record 文件名 | `<task_id>_<YYYYMMDD_HHMMSS>_<passed\|failed\|blocked>.yaml`（同 Phase 0） |
| per-task prompt | 每个 Batch 2+ 风格任务执行前必须先有 `docs/phase1/tasks/<task_id>.md`，缺失即 `task_prompt_incomplete` 停手 |
| Schema 复用 | Task Record 沿用 `docs/dev/task_record_schema.yaml`（不新建 schema） |
| 清理 | stale blocked/failed YAML 重跑通过后清除；`_scratch/` 放临时；stage 前清 `__pycache__/`、`*.pyc`、各 cache（`CLAUDE.md` §Scratch） |
| 实验代码 | 任何 spike 代码进 `experiments/phase1/`，**不进 `app/`**（沿用 Phase 0 处置纪律） |

### B.2 模板：建 Phase 1 派生模板（修正 v1 的"仅改两字段"错误；v3 补齐穷尽清单）

**v1 的写法不可执行。** 实际打开 `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` 核对，模板正文里硬编码了大量 phase0 字面量，远不止 `source_spec`/`task_index` 两处。仅改这两个字段会把 Phase 1 任务误导向 Phase 0 的路径与 commit 前缀。

因此 **为 Phase 1 建一个最小派生模板 `docs/phase1/TASK_PROMPT_TEMPLATE.md`**（不复用 phase0 正文，避免每次手改 + 漏改）。

**v3 已对模板全文重新 `grep`（`phase0` / `Phase 0` / `docs/phase0` / `P0-`，带行号），下面是穷尽的 phase0 字面量替换清单**——P1-SKEL-001 的验收要点要求"grep 无 phase0 残留"，此清单必须能支撑该验收，故覆盖每一处命中：

| 模板行号（已核） | phase0 字面量原文（节选） | 处置：替换为 phase1 对应物 / 显式保留理由 |
|---|---|---|
| L1 | `# CODEX_SINGLE_TASK_PROMPT_TEMPLATE — Phase 0 v2.0.0` | **替换**标题为 `# TASK_PROMPT_TEMPLATE — Phase 1 v1.0.0`（派生模板自有版本号，去掉 "Phase 0"） |
| L9 | `source_spec: docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md` | **替换** → `source_spec: docs/phase1/PHASE1_SPEC.md`（Phase 1 spec，见 C.1/F；spec 未落盘前留占位并标注硬前置） |
| L10 | `task_index: docs/phase0/TASK_INDEX.md` | **替换** → `docs/phase1/TASK_INDEX.md` |
| L11 | `boundary_checklist: docs/phase0/BOUNDARY_CHECKLIST.md` | **决策点**（见 F.5）：复用 phase0 checklist（跨阶段通用）或建 phase1 副本；派生模板按拍板结果指向其一 |
| L12 | `context_strategy: docs/phase0/CONTEXT_LOADING_STRATEGY.md` | **保留 phase0（跨阶段通用）** 或建 phase1 指针；该文件是阶段无关的上下文加载策略，倾向保留并在模板注明"跨阶段沿用" |
| L13 | `current_task_prompt: docs/phase0/tasks/<task_id>.md` | **替换** → `docs/phase1/tasks/<task_id>.md` |
| L20 | 正文 `按 docs/phase0/CONTEXT_LOADING_STRATEGY.md ... 不要把整份 Phase 0 spec 粘贴` | **替换** 路径为 `docs/phase1/...`（若 L12 决定保留 phase0 则路径不变）；**"整份 Phase 0 spec"措辞改为"整份 Phase 1 spec"** |
| L24 | `按 docs/phase0/CODING_STYLE_BASELINE.md 仅加载 ... section` | **保留 phase0（跨阶段通用编码样式基线）** 或建 phase1 副本；倾向保留并注明跨阶段沿用（见 F.5） |
| L25 | `按 docs/phase0/REPOSITORY_CONTEXT_MAP.md Section 5 ...` | **保留 phase0（仓库导航图跨阶段通用）**，注明跨阶段沿用 |
| L64 | `repository-changing Phase 0 / Phase 1 tasks default to codex_review` | **改措辞**：本句已含 "Phase 1"，去掉 "Phase 0 /"，统一为 "repository-changing Phase 1 tasks default to `codex_review`"（派生模板只服务 Phase 1） |
| L68 | `详细 guardrails 见 docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md "Engineering Method Selection" 章节` | **保留 phase0（角色/方法 guardrails 跨阶段通用）**，注明跨阶段沿用；CLAUDE.md 亦将其列为 read-on-demand 通用文档 |
| L72 | `P0-PREP-* 是 execution-pack-only preparation tasks ...` | **替换/删除**：Phase 1 若有 PREP 任务改为 `P1-PREP-*` 规则；若 Phase 1 无此类任务则删除该行 |
| L101 | `需要 commit 时，使用 phase0(<task_id>): <one-line summary> 格式` | **替换** → `phase1(<task_id>): <one-line summary>` |
| L123 | `interface_contract 任务：逐条引用 ... 或声明继承 Phase 0 spec 第 8.4.1 节全局样例` | **改指向**：Phase 1 的 interface_contract 任务应引用 Phase 1 spec 的对应章节；若仍继承 Phase 0 spec §8.4.1 全局样例（契约冻结沿用），则**显式注明"继承冻结 Phase 0 spec §8.4.1"** 而非含糊的 "Phase 0 spec" |
| L157 | `生成 Task Failure Record：docs/phase0/task_logs/<task_id>_<...>_failed.yaml` | **替换** → `docs/phase1/task_logs/...` |
| L204 | `docs/phase0/task_logs/<task_id>_<YYYYMMDD_HHMMSS>_<passed\|failed>.yaml` | **替换** → `docs/phase1/task_logs/...` |
| L220 | commit 格式块 `phase0(<task_id>): <one-line summary>` | **替换** → `phase1(<task_id>): ...` |
| L238 | `审查使用 docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md 中 Review Guardrails` | **保留 phase0（同 L68，Review Guardrails 跨阶段通用）**，注明跨阶段沿用 |
| L248 | `优先使用 docs/phase0/tasks/<task_id>.md 中的 per-task prompt ... 避免把整份 spec 反复塞入` | **替换** 路径 → `docs/phase1/tasks/<task_id>.md` |
| L265 | `Batch 0 / Batch 1 的 per-task prompt 已内置。Batch 2+ 启动前必须生成对应 docs/phase0/tasks/<task_id>.md` | **替换** 路径 → `docs/phase1/tasks/<task_id>.md`；**Batch 编号按 Phase 1 实际批次（B1-B5，见 C.2）改写或泛化**（Phase 0 的 "Batch 0/1 已内置" 不适用 Phase 1） |
| L269 | `Batch 2+ 启动前必须生成对应 docs/phase0/tasks/<task_id>.md` | **替换** 路径 → `docs/phase1/tasks/<task_id>.md` |

> **说明**：以上 21 处即 v3 对模板全文 grep 的全部命中（`phase0`/`Phase 0`/`docs/phase0`/`P0-`）。区分两类处置：(a) **必须替换为 phase1**（路径锚点、commit 前缀、task_logs、标题、batch 编号）；(b) **显式保留 phase0 通用文档并注明跨阶段沿用**（CONTEXT_LOADING_STRATEGY / CODING_STYLE_BASELINE / REPOSITORY_CONTEXT_MAP / ROLE_AND_METHOD_GUARDRAILS，及待 F.5 拍板的 BOUNDARY_CHECKLIST）。派生模板里对每个"保留"项都要一行注释说明跨阶段沿用，使 P1-SKEL-001 的 "grep 无 phase0 残留" 验收对"残留"有明确口径——**残留 = 应改未改的 phase1 锚点**，不含被显式注明跨阶段沿用的通用文档引用。
>
> 这道清单是建派生模板时的"差异 checklist"，归并入 **P1-SKEL-001** 产出。

### B.3 衔接决策（需拍板，见 F 节）

- INDEX：建议 `docs/phase1/task_logs/INDEX.md` **新建独立表**（与 phase0 INDEX 同格式：task_id/date/result/branch/git_sha/ci_run/reviewer），不混入 phase0 75 行历史，保持可读。
- TASK_INDEX（DAG）：建议 `docs/phase1/TASK_INDEX.md` 新建，沿用 phase0 的 "Batch + depends_on" 结构。

---

## C. Phase 1 范围切分 + 首批任务清单

### C.1 蓝图给定的 Phase 1 范围（来源，已核行号）

来源 `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md` §13 "Phase 1：MVP 主链"（L2680-2699）+ §4.3 Workflow（§4.3.2 L438-455、§4.3.3 L468）：

**Phase 1 要交付（跑通"查、办、生成"最小闭环，只用 Mock/低风险能力）：**
- Web/CLI 入口 → Intent Router → Workflow/Tool 执行 → Policy Precheck → Trace → Evaluator（§13 L2685-2690）
- Admin Lite：Registry / Policy / Trace / 基础用户角色 / Binding 状态（§13 L2691）
- Session Memory + 基础 Semantic/System Knowledge（§10.1 L2166-2168：Phase 1 只实现最小层）
- IdentityMapping Mock 表；Policy Guard 绑定状态预检；未绑定返回 SDUI `operator_handback_card`；无能力返回 `no_capability_found`（§13 L2693-2696）
- Workflow Engine 轻量版：线性步骤、简单分支、step IO 映射、step 级 Policy、有限重试、human_gate、版本锁定、全链路 Trace（§4.3.2 L438-446）
- **明确不做**：真实业务系统写操作、生产级 Controlled Exploration、动态 Tool Composition、复杂 DAG/长事务（§13 L2697-2698、§4.3.3 L468）

**关键观察**：Phase 0 已交付的不只是 Port，还有 Mock Adapter（OA/U8/Hik）、Gateway 短路/透传/Adapter 执行骨架、Runtime 主链最小骨架、Trace 最小写入、Golden Task runner。所以 Phase 1 大量工作是**把骨架填成可治理实现**，而非从零。

**Phase 1 spec 是 B2-B5 的硬前置（修正 v1 把它当普通决策点）**：现仅有 blueprint（方向）+ MVP spec v1.0.11（锚的是 Phase 0 范围）。实现型纵切的 acceptance criteria 必须有明确来源。因此规定：**`docs/phase1/PHASE1_SPEC.md` 未落盘并批准前，不得进入 B2**。该 spec 承接 blueprint §13 + §4.3 裁剪，由本 Plan 批准后单列任务（或并入 P1-SKEL-001 的后续）产出。

### C.2 切分策略建议：纵切（vertical slice）

理由：Phase 0 已按"横向层"（Port → Mock → Gateway → Runtime）铺完骨架。Phase 1 价值在端到端闭环，**纵切**（每个切片 = 一类用户意图从 Intent 到 ResponseEnvelope 打通）能让每批都产出可被 Golden Task 验收的可用闭环，且天然受 `tests/architecture/` 边界门约束。横切此时会产生大量"半成品层"难以验收。

建议批次（DAG 草案，细化留给首批的 TASK_INDEX 任务）：

| Batch | 主题 | 纵切内容 | 前置 |
|---|---|---|---|
| **B1（首批，本 Plan 列清单）** | 启动准备 | 目录+派生模板 / golden 真回归门 / 蓝图勘误 / 参数登记（下方 4 任务） | 人类批准本 Plan |
| B2 | Intent → Capability 选择闭环 | Intent Router 实现 + Capability Preselector 轻量版（规则/标签/embedding）+ `no_capability_found` 路径 | **P1-GATE-001 完成 + PHASE1_SPEC 批准** |
| B3 | Identity/Policy 预检闭环 | IdentityMapping Mock 表实现 + Policy Guard 绑定预检 + 未绑定 `operator_handback_card` + confirm 路径 | B2 |
| B4 | Workflow 轻量引擎 + 执行 | 线性 Workflow + step Policy + 有限重试 + human_gate，经 Gateway 执行 Mock Adapter | B3 |
| B5 | Session Memory + Evaluator + Admin Lite | 最小记忆层 + Evaluator + Registry/Policy/Trace/Binding 管理页 | B4 |

### C.3 首批任务清单（B1）

#### P1-GATE-001 — golden-task 真回归门（**前置任务，B2 前必须完成**）

| 项 | 内容 |
|---|---|
| 类型 | infrastructure / test（method TDD） |
| 背景（已核现状） | 当前 golden 链路**不是真回归门**：`scripts/run_golden_tasks.py` 仅 `json.dump(summary)` 后 `return 0`（仅 infra 异常返回 2，见 L34-36）；`tests/golden_tasks/test_golden_tasks.py` 的 happy 用例只断言 `positive_passed >= 1` 且 `negative_passed >= 1`（L845-846），无 80%/100% 阈值；`.github/workflows/ci.yml` 的 "Golden tasks" step（L85-86）只跑 `--summary`，标签写 "regression gate" 但实际不按阈值 fail（CI 里另有 `pytest -v` step L81 会跑上述弱断言）。 |
| 背景（口径现状，v4 已核） | `build_summary`（`test_golden_tasks.py` L549-575）输出字段只有 `total / passed / failed / skipped / not_applicable / positive_passed / negative_passed / results`，**既没有 boundary/security 子类计数，也没有 `positive_total`/`negative_total` 分母字段**；每条 result item 仅含 `golden_task_id / category / status / reasons`（L96-101）。fixtures 的 `category` 被 `test_fixture_schema.py` L71 **硬约束为 `{"positive","negative"}` 二选一**（`test_golden_tasks.py` L831 同样断言），现网 fixture（`FROZEN_GT_IDS` L66-78 = GT-001..GT-010 + GT-012，共 **11 个**，跳过 GT-011；`tests/golden_tasks/fixtures/*.json` 同为 11 个）只有 positive/negative 两类。即"边界/安全"目前并入 negative，没有独立维度。 |
| 目标 | 把它变成真正的阈值门：新增 threshold gate——当 `failed > 0` **或** 正向通过率 `< 80%` **或** 负向（含边界/安全，见下口径）`< 100%` 时，runner 返回非 0、CI 失败。给 runner 加 `--gate`（或让 `--summary` 同时判定）并在 CI step 调用 gate 模式。 |
| 阈值来源（v3 已核，**修正 v2 锚错**） | 完整阈值的权威来源 = MVP spec v1.0.11 **§20.1「Golden Task 通过标准」L4501-4503**（§20 是 Execution Consistency Addendum，L4495 明示"若与前文不一致以本节为准"，为最高权威）。原文：L4502「正向任务按总体通过率计算，必须 >= 80%」；L4503「负向路径、边界路径、安全拒绝路径必须 100% 通过」。**§14.5「Golden Task 验收」L3951-3954** 同义重述（L3953「正向任务总体通过率 >= 80%」、L3954「负向路径、边界路径和安全拒绝路径 100% 通过」），可作交叉印证。**v2 误把完整阈值锚到 §12.5 L3496 是错的**：§12.5「负向路径最低要求」L3496 原文仅「核心 Golden Task 必须包含以下负向 / 边界类型，并全部 100% 通过」，**不含正向≥80%**——§12.5/L3494-3501 至多作为「负向/边界清单」来源（列举 no_capability_found/policy deny/未绑定/scope 不明/adapter 超时 5 类），不作为完整阈值来源。 |
| 通过率口径（吸收 N4，v4 重写为可执行） | 现状 summary 与 fixture 只有 positive/negative 两维，无 boundary/security 子类。**处置：本任务现阶段把"边界/安全"并入 negative 统一按 100% 计**。**关键：`build_summary` 当前没有 `positive_total`/`negative_total` 字段，gate 不能假设其存在**——本任务须二选一：(a) 在 GATE 实现里**从 `summary["results"]` 按每条 item 的 `category` 派生** `positive_total`/`negative_total`（统计 `category=="positive"` / `=="negative"` 的条数，分母排除 not_applicable，见下"gate 边界条件"），或 (b) **在 `build_summary` 里新增这两个计数字段**并由 gate 读取。gate 判定 = 正向通过率 `>= 0.8` 且负向通过率 `== 1.0`（负向含边界/安全样本，全 100%）。**不在本任务引用尚不存在的 boundary/security 字段**。spec §20.1/§14.5 区分"负向/边界/安全"三路径是**语义维度**，当前 fixture 用 negative 统一承载；如后续 Phase 1 需要把边界/安全拆成独立可报维度，由专门任务**新增 fixture subtype 或从 fixture 元数据派生子类计数**，届时再扩展 gate——本 GATE 任务先以 negative=100% 落地真门，不让阈值引用不存在的字段。 |
| gate 边界条件（吸收 N5，v4 补全分母口径） | summary 已有 `skipped` / `not_applicable`（L553-554/L562-563）。处置：**`not_applicable != 0` 不算 gate 失败，但 runner 须在输出中显式记录数量**（环境性跳过，属预期）；**通过率分母排除 `not_applicable`**——即正向通过率 = `positive_passed / (positive 总数 − positive 中 not_applicable 数)`，负向同理 = `negative_passed / (negative 总数 − negative 中 not_applicable 数)`，使公式与"not_applicable 不算失败"自洽，避免把环境性跳过样本算进分母拉低通过率。**`skipped != 0` 须在 gate 任务里定策略**——建议默认视为失败（避免静默漏跑核心用例），但允许通过白名单/原因码豁免特定 skip；该策略在 P1-GATE-001 的 spec 段落里写死。`failed > 0` 仍直接失败。 |
| 产出 | 改 `scripts/run_golden_tasks.py`（加阈值判定与非零退出；含从 `results` 派生分母或为 `build_summary` 补 `*_total` 计数）；改 `.github/workflows/ci.yml` 让 golden step 真正 fail；补 `tests/` 中对应阈值断言（含 positive<80%、negative<100%、failed>0、skipped 策略的可证伪用例）；不动 `app/ports/`、不动 fixtures 业务语义。 |
| 依赖 | 人类批准本 Plan |
| 验收要点 | 故意造一条 failed 时 CI 必须红；**完整阈值取自 MVP spec §20.1 L4501-4503（正向≥80% + 负向/边界/安全=100%），§14.5 L3951-3954 交叉印证，§12.5 L3496 仅作负向/边界清单来源**；通过率按上"通过率口径"行（边界/安全并入 negative 计 100%，分母从 `results` 按 `category` 派生或新增 `*_total` 字段，**排除 not_applicable**）；`skipped`/`not_applicable` 按上"gate 边界条件"行处置；不放宽现有任何门；`tests/architecture/` 仍绿 |
| Complexity | **Medium**（涉及 CI 行为变更，需可证伪验证） |

> 为什么前置：B2-B5 的"自动验收"全靠这道门。门是空的时候，实现型任务"通过"无保障。

#### P1-SKEL-001 — `docs/phase1/` 目录 + 派生模板 + INDEX 骨架

| 项 | 内容 |
|---|---|
| 类型 | documentation / preparation（method `not_applicable`） |
| 目标 | 落盘 B 节目录树：建 `docs/phase1/{tasks,task_logs}/`、空 `TASK_INDEX.md` 骨架、`task_logs/INDEX.md` 表头、把本 Plan 存为 `PHASE1_PLAN.md`；**并建 `docs/phase1/TASK_PROMPT_TEMPLATE.md` 派生模板**，按 B.2 清单替换全部 phase0 字面量 |
| 产出 | 上述文件/目录 + 派生模板（含 B.2 字面量替换清单） |
| 依赖 | 人类批准本 Plan（B 节衔接决策） |
| 验收要点 | 目录树与 B 节一致；派生模板内**无残留应改未改的 `docs/phase0/` 锚点、无 `phase0(` commit 前缀**（可 grep 自检；"残留"口径见 B.2 说明——被显式注明跨阶段沿用的通用文档引用不算残留）；INDEX 表头与 phase0 一致；不触碰 `app/`、`docs/phase0/`、`app/ports/`；`git diff --cached --check` 干净 |
| Complexity | **Low**（模板派生需逐条核对替换，略高于纯建目录） |

#### P1-ERRATA-001 — `BLUEPRINT_ERRATA.md` 勘误 + 澄清登记（**3 条正式条目 + 1 条 legacy note**）

| 项 | 内容 |
|---|---|
| 类型 | documentation（method `not_applicable`） |
| 目标 | 在 `docs/phase1/BLUEPRINT_ERRATA.md` 集中登记，**不改冻结蓝图正文**（蓝图 freeze，只旁注）。文档命名/分节为 **"Errata & Clarifications"**（吸收 N1：ARQ 是澄清不是勘误） |
| 产出 | ERRATA 文档：勘误条目每条含 位置（文件+§号+行号）+原文+勘误说明+权威来源；legacy note 单设小节并明确标注来源 |
| 依赖 | 无 |
| 验收要点 | **3 条正式条目每条带可核行号锚点**；legacy note 明确标注"来源=项目记忆，非蓝图"；只新增 ERRATA 文件，不动 blueprint/spec/`app/` |
| Complexity | **Low** |

**正式勘误/澄清条目（3 条，已逐条打开文件核到行号）：**

| # | 类型 | 项 | 锚点（已核） | 说明 |
|---|---|---|---|---|
| 1 | Erratum | instructor 非基线 | blueprint v3.2.4 **§6.11（L1332）** 写 "Phase 1 默认采用 OpenAI SDK + **instructor** + Pydantic v2"；**§12.1.3（L2505）** 同样写 "Phase 1 默认 = OpenAI SDK + instructor + Pydantic v2 Schema" | 已被 ADR-P0-SPIKE-002 否定；`PHASE1_TECHNICAL_BASELINE.md` §2 基线 = **raw OpenAI SDK，无 wrapper**。冻结正文未注记，需勘误。（修正 v1 的 "§11(L1332)" → 实际标题是 §6.11） |
| 2 | Clarification | ARQ 层级澄清 | blueprint **§12.1.4（L2517-2518 L0/L1、L2525 结论）**、升级路线 **L2580-2585** | ARQ 是 L1 候选（部门试点才启用），Phase 1 主线 L0 = BackgroundTasks/in-process；蓝图已明示 "ARQ 不作为长期不可替换底座"（L2518/L2526）。属**澄清**避免实现误读为 Phase 1 必装，非蓝图错误。（吸收 N1：归为 Clarification） |
| 3 | Erratum | `adapter_error_mapped` 错位 | MVP spec v1.0.11（`docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md`）**§8.6.7 `TraceEvent.event_type`（L878-903）缺** `adapter_error_mapped`；但 **§12.4.1 trace 矩阵（L3490）** 引用它；冻结端口 **`app/ports/trace.py:25`** 已含（L27 是 `gateway_post_recorded`） | 实现（端口）正确，spec §8.6.7 漏列。勘误指向 spec §8.6.7，**不动端口**（端口冻结且正确）。（修正 v1 的 `trace.py:27` → 实际 `:25`） |

**Legacy / Deprecation note（非蓝图勘误，单设小节）：**

| 项 | 来源 | 说明 |
|---|---|---|
| jsjy 库已废弃 | **项目记忆（MEMORY），蓝图正文无引用**（已 grep `docs/` 全仓 0 命中） | 登记废弃事实防误引。**因不满足 ERRATA "权威文件+行号" 规则，移出正式勘误，归入 legacy note 并显式标注来源=记忆。**（吸收 B2） |

#### P1-PARAM-001 — Context Budget / vLLM 部署参数登记（**等 infra 回值，非无条件首批任务**）

| 项 | 内容 |
|---|---|
| 类型 | documentation（PDR，research-only） |
| 就绪状态 | **ready only after infra values**——本任务依赖 infra 提供生产/内网 vLLM 实际部署参数；**infra 未给值前天然 blocked**，不与 GATE/SKEL/ERRATA 并列为"无条件可做"。（吸收 N2） |
| 目标 | 消费已完成的内网复测结论（`PHASE1_TECHNICAL_BASELINE.md` §3.1：raw-SDK 结构化 98.1%/90.7% 已达标），**仅补齐部署参数**：`max_model_len`、量化方式、`request_timeout`（复测用 120s）、`max_tokens`（复测用 2048）、`enable_thinking` 是否被 endpoint 尊重（REVAL `remaining_risks` 提示可能未被尊重）。向 infra 确认后登记成 Phase 1 部署基线表 |
| 产出 | `docs/phase1/` 下一份参数登记文档（或并入 baseline 的 Phase 1 addendum） |
| 依赖 | infra 提供实际部署参数（外部依赖，见 E 节） |
| 验收要点 | **明确标注"参数登记，不重测结构化成功率"**；不引入 instructor/PydanticAI；不改 `app/ports/`；每个参数有来源（infra 确认 or REVAL log 引用） |
| Complexity | **Low**（纯登记，卡点在等 infra 回值） |

#### （可选）P1-TOOLCALL-002 — 工具调用 prompt 第二轮复测

| 项 | 内容 |
|---|---|
| 类型 | spike（PDR）；**可选，不阻塞 Phase 1** |
| 目标 | 内网复测工具调用停在 75%（6/8），固定栽在同两条 `query_oa_leave_balance` 样本（TC-001/TC-004），跨端点跨模型可复现 = prompt/tool-description 问题（`PHASE1_TECHNICAL_BASELINE.md` §3.1）。用改进 prompt 复测这两条 |
| 产出 | `experiments/phase1/` 复测脚本 + ADR addendum/Task Record；**不进 `app/`** |
| 依赖 | 内网 vLLM endpoint 可用（同 REVAL 环境） |
| 验收要点 | **明确标注 optional、不阻塞**；只复测工具调用 prompt，不碰结构化基线；不引入 wrapper 库 |
| Complexity | **Medium**（需 GPU/endpoint + 真实 LLM 调用） |

---

## D. 验证策略（修正 golden-task 过度声称）

**重要修正**：v1 在此节声称"CI golden task 回归门把关 / 正向≥80%+负向100%"。**实际核查代码后该表述不成立**——见下表"现状"列。真正的回归门需要 P1-GATE-001 先建立。

| 任务类型 | 验证方式 | 现状 vs 目标 |
|---|---|---|
| 文档型（SKEL/PARAM/ERRATA） | `git diff --cached --check`、forbidden-path 扫描、YAML `safe_load`、INDEX 列对齐、模板 grep 无 phase0 残留（"残留"口径见 B.2）；**review 为主** | 现状即可用，只需 review + 静态检查 |
| golden-task 门（P1-GATE-001 本身） | 故意注入失败验证 CI 转红；阈值断言单测（positive<80%、negative<100%、failed>0、skipped 策略各一可证伪用例） | **现状**：runner `--summary` 输出 JSON 后 `return 0`（仅 infra 异常 return 2）；pytest 仅断言 positive/negative 各 ≥1；summary 仅 positive/negative 两维（无 boundary/security 子类，也无 `*_total` 分母字段，fixture category 硬约束二选一）；CI step 不按阈值 fail。**目标**：P1-GATE-001 建成 `failed>0 / 正向<80% / 负向(含边界·安全)<100%` → 非零退出、CI 红（分母从 `results` 按 `category` 派生或补 `*_total`，排除 not_applicable）；`not_applicable` 记录不失败、`skipped` 默认失败可豁免 |
| 实现型纵切（B2-B5） | `scripts/run_golden_tasks.py`（**P1-GATE-001 完成后**的真阈值门）+ Mock adapters + `tests/architecture/`（import 边界）+ `uv run pytest` + ruff + mypy + `check_dependencies.py` | **依赖 P1-GATE-001 先落地**，否则"通过"无阈值保障 |
| 可选 TOOLCALL | 真实 LLM 调用 + 逐样本记录（防 weak test，REVAL `weak_test_scan_result` 范式） | 半自动 + review |

**阈值来源说明（v3 修正）**：上表"正向≥80% + 负向/边界/安全=100%"的完整阈值锚点 = MVP spec v1.0.11 **§20.1 L4501-4503**（Execution Consistency Addendum，最高权威，L4495 "以本节为准"），**§14.5 L3951-3954** 交叉印证；**§12.5 L3496 仅作"负向/边界清单"来源，不含正向≥80%**（v2 把完整阈值锚到 §12.5 L3496 是错的，已改正）。

CI 门：每 merge 到 `phase0/main` 后查 remote GitHub Actions（`CLAUDE.md` §Git workflow）。**本地全量 `pytest` 在缺 `DATABASE_URL` 时，DB 相关用例的真实行为不一致（v4 已核代码）**：`tests/db/test_db_health.py`（`_database_url_from_environment` L29-32）与 `tests/db/test_migrations.py`（L15-18）会 **抛 `AssertionError`（即 failed）**，而 `tests/infra/persistence/task_store/test_postgresql_task_session_store.py`（`_skip_if_no_db` L22-24）走 **`pytest.skip`（即 skipped）**——**当前没有 `not_applicable/local_only` 这一档**。因此 Phase 1 涉 DB 的纵切必须在**有 DB 的环境 / CI pgvector service 上**验证，本地缺 `DATABASE_URL` 不能视为合格通过（CI 已配 pgvector service，见 `ci.yml` L16-36）。

---

## E. 风险 / 依赖

| 项 | 类型 | 说明 / 缓解 |
|---|---|---|
| golden-task 门当前为空 | **已暴露缺陷（本次 review 发现）** | runner `return 0`、CI 不按阈值 fail；**P1-GATE-001 作为前置任务修复**，B2 前必须完成 |
| golden summary 缺边界/安全维度 + 缺分母字段 | 口径风险（已在 P1-GATE-001 处置） | summary/fixture 只有 positive/negative 两维、无 `*_total` 分母；边界/安全现并入 negative 计 100%，分母从 `results` 派生或补 `*_total`（排除 not_applicable），gate 不引用不存在字段；如需独立维度由后续专门任务扩展 |
| infra 部署参数未确认 | 依赖（阻 P1-PARAM-001） | `max_model_len`/量化/timeout 需 infra 给值；拿不到则 P1-PARAM-001 标 blocked 等回值，不猜 |
| `enable_thinking=false` 可能未被 endpoint 尊重 | 风险 | REVAL `remaining_risks` 明示观察到 reasoning trace + 高延迟；参数登记需注明"以 thinking-active 行为为准" |
| Phase 1 spec 缺失 | **范围风险（升级为硬前置）** | blueprint 是方向、MVP spec v1.0.11 锚 Phase 0 范围；**`docs/phase1/PHASE1_SPEC.md` 未批准不得进 B2**（见 C.1） |
| 工具调用 75% < 80% | 已知债（不阻塞） | prompt 问题，非框架/端点；TOOLCALL-002 为可选补救 |
| pydantic-ai agent-path 未内网复测 | 已知债（不阻塞） | Phase 1 不引入 pydantic-ai，无影响 |
| 契约冻结边界 | 红线 | B2-B5 若发现需改 `app/ports/`，必须停手 + 显式授权 + 人类批准 |

---

## F. 需要人类拍板的决策点

1. **`docs/phase1/` 是否新建独立 INDEX 与 TASK_INDEX**（建议：新建独立，不混 phase0 75 行历史）。
2. **Phase 1 spec 已升级为 B2-B5 硬前置**（不再是普通决策点）——需拍板：spec 单列任务产出，还是并入 P1-SKEL-001 后续？无 spec 不得进 B2。
3. **切分策略：纵切 vs 横切**（建议纵切，理由见 C.2）。
4. **首批取舍：4 准备型（GATE/SKEL/ERRATA/PARAM）+ 1 可选（TOOLCALL）**。其中 GATE 为前置、PARAM 等 infra。
5. **派生模板 vs 复用 phase0 通用文档的边界**：哪些 phase0 文档跨阶段沿用、哪些建 phase1 副本（见 B.2 清单）。具体待拍板项 = `BOUNDARY_CHECKLIST`（L11，复用 or 建 phase1 副本）；建议保留沿用的 = `CONTEXT_LOADING_STRATEGY`(L12/L20)、`CODING_STYLE_BASELINE`(L24)、`REPOSITORY_CONTEXT_MAP`(L25)、`ROLE_AND_METHOD_GUARDRAILS`(L68/L238)。
6. **P1-PARAM-001 的 infra 参数谁提供 / 何时**（决定其从 blocked 转 ready）。

---

## G. Complexity 评估

| 范围 | Complexity | 说明 |
|---|---|---|
| Phase 1 整体 | **High** | 端到端 MVP 闭环 + 轻量 Workflow 引擎 + Memory/Evaluator/Admin Lite，跨 5 个纵切批次 |
| 首批 B1 整体 | **Low-Medium** | GATE 为 Medium（CI 行为变更），SKEL/ERRATA/PARAM 为 Low |
| P1-GATE-001 | Medium | 改 runner + CI，需可证伪验证 |
| P1-SKEL-001 | Low | 建目录 + 派生模板（逐条替换 phase0 字面量） |
| P1-ERRATA-001 | Low | 3 条已定位带行号 + 1 legacy note |
| P1-PARAM-001 | Low（卡 infra） | 纯登记，复杂度在等外部回值 |
| P1-TOOLCALL-002（可选） | Medium | 需 GPU/endpoint + 真实 LLM 调用 + 防 weak test |
| 后续 B4（Workflow 引擎） | High | 整个 Phase 1 最重的纵切，建议拆多个 P1 子任务 |

---

### 调研覆盖说明（供 review 核对）

本轮（v4）**实际重新打开并核对**的文件（针对 B5/B6 三点）：
- `tests/golden_tasks/test_golden_tasks.py`：**`FROZEN_GT_IDS` L66-78**（确认 GT-001..GT-010 + GT-012 = **11 个**，跳过 GT-011）、**`build_summary` L549-575**（确认字段无 `positive_total`/`negative_total`，无 boundary/security 子类）、**result item L96-101**（确认每条仅 `golden_task_id/category/status/reasons`）、**L831**（category 二选一断言）
- `tests/golden_tasks/fixtures/*.json`：`ls` 确认 = **11 个** JSON（GT-001..GT-010 + GT-012），category 仅 positive/negative
- `tests/db/test_db_health.py` **L29-32**（缺 DATABASE_URL → `AssertionError`）
- `tests/db/test_migrations.py` **L15-18**（缺 DATABASE_URL → `AssertionError`）
- `tests/infra/persistence/task_store/test_postgresql_task_session_store.py` **L22-24**（缺 DATABASE_URL → `pytest.skip`）

v3 已核并沿用（未推翻）：
- MVP spec v1.0.11：**§12.5 L3494-3501**（确认 L3496 仅"负向/边界 100%"，无正向≥80%）、**§14.5 L3949-3959**（确认 L3953 正向≥80% + L3954 负向/边界/安全 100%）、**§20.1 L4495-4509**（确认 L4495 "以本节为准" + L4502 正向≥80% + L4503 负向/边界/安全 100%，为最高权威阈值来源）
- `tests/golden_tasks/test_fixture_schema.py` **L71**（确认 `category in {"positive","negative"}` 硬约束）
- `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md`：**全文重新 grep `phase0`/`Phase 0`/`docs/phase0`/`P0-`**，命中 L1/L9-13/L20/L24/L25/L64/L68/L72/L101/L123/L157/L204/L220/L238/L248/L265/L269（共 21 处，全部纳入 B.2 清单）

v2 已核并沿用（未推翻）：
- `scripts/run_golden_tasks.py`（`return 0` / 仅 infra return 2）
- `.github/workflows/ci.yml`（golden step L85-86 仅 `--summary`）
- `app/ports/trace.py`（`adapter_error_mapped` 在 L25，`gateway_post_recorded` 在 L27）
- blueprint v3.2.4：§6.11(L1332)、§10.1(L2166-2168)、§12.1.3(L2505)、§12.1.4(L2517-2528)、§12.2.2/12.2.3(L2569-2585)、§13(L2680-2699)、§4.3.2-4.3.3(L438-468)
- Grep `jsjy` 全 `docs/`：0 命中；Glob `docs/phase1/**/*`：无结果

未逐字打开 ADR 文件本身——内网验证数字（raw 98.1/90.7、工具调用 75%）沿用 baseline §3.1，本轮未推翻。

---

## 对 Codex review 的逐条响应

### 第一轮（B1-B4 / N1-N3）

| ID | review 结论 | 怎么改的 |
|---|---|---|
| **B1** | golden-task 不是真回归门，Plan 过度声称 | 已打开 3 个文件核实：runner `return 0`、pytest 仅断言 positive/negative≥1（L845-846）、CI step 仅 `--summary`（L85-86）。D 节如实改写，删除"CI 回归门把关"失实表述；新增 **P1-GATE-001 前置任务**；C.2 把 B2 前置标为 "P1-GATE-001 完成"。（阈值来源 v3 进一步改正，见 NB1 行） |
| **B2** | ERRATA 第 3 条 jsjy 不合规 | grep 确认 jsjy 在 `docs/` 0 命中。从正式勘误移除→ **legacy note** 并标注"来源=项目记忆"。正式条目降为 **3 条**。 |
| **B3** | 多处行号/章节号错 | 逐条改正：instructor `§11` → **`§6.11(L1332)`**；`trace.py:27` → **`:25`**；Memory `§9 L2166` → **`§10.1（L2166）`**；其余抽查成立。 |
| **B4** | "复用 phase0 模板仅改两字段"不可执行 | 改为**建 Phase 1 派生模板**，B.2 给带行号替换清单，归入 P1-SKEL-001。（v3 补齐为穷尽清单，见 NB2 行） |
| **N1** | ARQ 是 clarification 不是 errata | ERRATA 文档改名 **"Errata & Clarifications"**；ARQ 标 **Clarification**。 |
| **N2** | P1-PARAM-001 不应当无条件任务 | 新增"就绪状态"行：**ready only after infra values**。 |
| **N3** | Phase 1 spec 缺失应升级为硬前置 | 升级为 **C.1 / E 节硬前置**：未批准**不得进 B2**。 |

### 第二轮（NB1 / NB2 / N4 / N5）

| ID | review 结论 | v3 怎么改的 / 新锚点 |
|---|---|---|
| **NB1** | golden 完整阈值来源锚错（v2 写 §12.5 L3496） | 核三处：§12.5 L3496 仅负向/边界 100%、无正向≥80%；§14.5 L3951-3954 与 §20.1 L4501-4503 才完整。**完整阈值新锚 = §20.1 L4501-4503（最高权威，L4495 以本节为准），§14.5 交叉印证；§12.5 降级为"负向/边界清单"**。 |
| **NB2** | B.2 模板替换清单仍漏项 | 全文重新 grep，命中 **21 处**全部进 B.2 清单，分"替换 phase1"/"显式保留通用文档"两类；新增"残留"口径支撑 grep 验收。 |
| **N4** | golden 通过率计算口径未定 | 拍板边界/安全并入 negative 计 100%，不引用不存在的 boundary/security 字段。（v4 进一步明确 `*_total` 分母派生，见 B6 行） |
| **N5** | gate 边界条件（skipped/not_applicable） | 拍板：not_applicable 不失败但记录、skipped 默认失败可豁免、failed>0 直接失败。（v4 补全分母排除 not_applicable，见 B6 行） |

### 第三轮（B5 / B6）

| ID | review 结论 | v4 怎么改的 / 核对来源 |
|---|---|---|
| **B5** | fixture 数量事实错误（v3 写"12 个"，实为 11） | 数 **`FROZEN_GT_IDS` L66-78 = GT-001..GT-010 + GT-012 = 11 个**（跳过 GT-011）；`fixtures/*.json` 同为 11。已把两处"12 个"改为 **11**，标注来源与跳过 GT-011。 |
| **B6.1** | "通过率口径"用 `*_total`，但 `build_summary` 无此字段 | 确认 `build_summary`（L549-575）**只有 `*_passed` + `results`，无 `*_total`**；result item（L96-101）有 `category`。「通过率口径」重写为**可执行**：从 `results` 按 `category` 派生 `*_total` 或新增计数字段，不再假装已有。 |
| **B6.2** | 分母是否排除 not_applicable 未定 | 「gate 边界条件」明确：**分母排除 `not_applicable`**——正向 = `positive_passed/(positive 总数 − positive 中 not_applicable)`，负向同理；保留 skipped 默认失败可豁免、failed>0 直接失败。 |
| **B6.3** | 缺 DATABASE_URL 时 D 节写 `not_applicable/local_only`，与代码不符 | 核 3 个 DB 测试：`test_db_health.py` L29-32 与 `test_migrations.py` L15-18 → **`AssertionError`（failed）**；`test_postgresql_task_session_store.py` L22-24 → **`pytest.skip`（skipped）**；**无 `not_applicable/local_only` 档**。D 节末改为与代码相符，核心意思保留：涉 DB 纵切须在有 DB 环境/CI pgvector service 验。 |
