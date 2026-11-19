# 开发助手与治理事故历史归档（截至 2026-11-19）

> 原文逐字底稿见同目录的 [源文本](DECISIONS_dev_assistant_history_to_20261119.md.source)。本页调整了归档后的链接。

> 历史原文按需查阅，现役口径见其来源文档。

## 2026-08-20 — 裁决：Opus 终审默认档位改为 `high`

**决定**：Opus 桥终审的默认 effort 由 `xhigh` 改为 **`high`**。Codex 自审档位**保持 `xhigh` 不变**。merge 硬前置的其余三项不变：`review_model_verified=true`、`observed_model=claude-opus-5`、`verdict=PASS`，且 base/head SHA 绑定当前候选。

**两条兜底，不得省略**：

1. **`high` 输出明显敷衍时升回 `xhigh` 重跑**。可判定的判据：没有具体 findings、只有笼统结论、或未逐条对照启动提示词的 criteria。
2. **`high` 也超时的，停手上报，不得再降级**。不存在「降到 medium」这条路；此时正确的处置是**拆小该棒**，而不是继续放松把关。

**为什么**：Opus 终审的价值在于**独立视角**而非档位深度——Codex 自审是同一模型按同一套理解写、又按同一套理解审，盲区重合；Opus 的作用是换一个模型看，这一价值与 effort 档位关系不大。实证支持：`P2-WORK-OBJECT-001` 的 `high` 终审逐条核对了 criteria，挑出 API 层跨用户隔离缺一条断言、认证 fixture 弱于同类测试，并指出外键会在被引用表安装 RI 触发器这一红线边界情况，均附文件与位置，非敷衍。

反向证据同样成立：`xhigh` 并非保险——历史上 Opus 曾 PASS 掉一个整类检测的豁免洞。破坏性改动最终由守卫测试兜底，不由评审档位兜底。因此提高档位的边际收益有限，而 `xhigh` 超时的代价是确定的：`P2-WORK-OBJECT-001` 的首次 `xhigh` 即以 `claude review timed out` 失败，19 文件 / 3076 行 diff 无法完成。

**一个需要分清的根因**：该次超时的直接原因很可能是 **diff 规模**而非档位。因此兜底 2 指向拆棒——若 `high` 在更大的 diff 上同样超时，应对是缩小单棒范围，不是继续降档。

**影响面**：`_scratch/P2_TASK_PROMPT_TEMPLATE.md` 的评审段与后续所有任务提示词的 `--effort` 参数；`P2-PORT-SEAM-001` 与 `P2-CONFIRM-BINDING-001` 的已备提示词需按本条更新；`P2-WORK-OBJECT-001` 的降档由本条追认为常规做法，其 PR body 中「仅限本棒」的表述以本条为准更新为默认档位。


## 2026-08-31 — 事实确认：共享测试库未被污染，两个偶发失败是本机 Windows 环境缺陷

**决定**：**不重置测试库**。雨爷已授权重置，但重置前的取证证明重置无效且会销毁证据，故不执行。

**背景**：多根并行棒各自在共享测试库（`127.0.0.1:15432` 的 `eternalai_test`）跑过 alembic 往返后，`phase0/main` 上出现偶发失败，初判为状态污染。取证推翻该判断。

### 一、确实发生过一次真污染，已修复

`alembic_version` 曾停在 `20260831_120000`——该版本只存在于 `P2-OA-ORGANIZATION-DIRECTORY-001` 分支，`phase0/main` 无法解析，导致 `tests/db/` 五个用例报 `CommandError: Can't locate revision`。协调窗口 `downgrade` 到 `20260827_180000` 后恢复。

**这是并行棒共用一个测试库跑 migration 的结构性风险**，与下面两条无关。

### 二、`test_existing_human_gate_decision_maps_to_already_claimed`：测试替身保真度缺陷

`app/infra/human_gate/in_memory.py::InMemoryHumanGate.record_decision` 用**对象相等**判幂等：`existing == decision` 时静默返回旧对象，只有不相等才抛 `HumanGateConflictError`。

而 `app/runtime/runtime.py::RuntimeImpl._resume_pending_workflow` **只有收到 `HumanGateConflictError` 才**转成 already-claimed；幂等返回时会继续 resume，最终返回 `accepted`。

两条 decision 除 `decided_at` 外字段本就全同。本机实测**相邻 `datetime.now(UTC)` 采样 99,894/100,000 对相等**，故两次调用极大概率落在同一时间刻度、对象完全相等，冲突分支不触发。

**生产实现没有此缺陷**：`app/infra/human_gate/postgresql.py::PostgreSQLHumanGate.record_decision` 的 UPDATE 带 `AND decision IS NULL` 谓词，第二次认领匹配 0 行，必然走冲突分支。**认领一次性在生产端由 SQL 谓词强制。**

**因此这是 fake 比生产宽松**——该测试永远抓不到认领一次性的回归。属门禁覆盖性缺陷，不是生产安全漏洞。

### 三、`test_existing_oa_session_survives_upgrade_and_remains_loadable`：Proactor 事件循环不兼容

失败发生在**建立 psycopg async 连接时**，早于任何 SQL。本机默认事件循环实测为 `ProactorEventLoop`，psycopg async 不支持它，须用 Selector loop。

`app/infra/auth/postgresql.py::PostgreSQLCredentialStore.load` 的宽泛 `except Exception` 把驱动层 `InterfaceError` 统一包装成 `CredentialStoreError("OA session credential cannot be loaded")`，**掩盖了真实根因**——这是排查困难的直接原因。

排除了另外两种可能：不是行缺失（`load` 对无行返回 `None` 而非抛错），也不是解密失败（失败在连接建立前，decoder 未执行）。

`P2-OA-ORGANIZATION-DIRECTORY-001` 已在自己的测试里按仓库既有体例切到 Selector loop 绕过；**该绕法尚未推广到既有测试**。

### 四、为什么记这一条

雨爷在授权重置的同时追问「是否需要先检查」。**该追问直接避免了一次错误操作**：两个失败都与数据库状态无关，重置不会修好它们，却会永久销毁判定所需的证据——重置后两者都可能因偶发而变绿，从而被误记为「污染已清除」。

**结论：可重置 ≠ 该重置。授权是必要条件，不是充分理由。**

**影响面**：三项登记为欠债，均不阻塞现役棒——(1) `InMemoryHumanGate` 幂等语义与生产 SQL 谓词不一致；(2) `PostgreSQLCredentialStore.load` 的宽泛 `except` 掩盖根因；(3) 并行棒共用测试库跑 migration 会互相破坏 `alembic_version`。今夜已合并各棒的全量验证在污染发生**之前**执行，结论不受影响；协调窗口将在库恢复后于 `phase0/main` 重跑一次全量实证。

---


## 2026-08-31 — 流程违规记录：`P2-OA-ORGANIZATION-DIRECTORY-001` 在缺 Review 前置时被合并

**本条不是对该合并的合规化。** PR #144 是一次已经发生、且无法通过编辑 PR body 消除的流程违规，**永久记为不合规**。以下是事故处置，不是追认。

### 一、发生了什么

`P2-OA-ORGANIZATION-DIRECTORY-001`（A 档）经 PR #144 合入 `phase0/main`（merge commit `a257322`）。合并时两道 Review 前置在**最终 head `30f2250`** 上均不成立：

- **独立监理**：现存结论为 FAIL（针对返修前的 `c000bd4`）；返修后的复审进程被中止，从未写出结论。
- **Opus 桥**：PASS 但绑定 `c000bd4`，非最终 head。按本文件同日新增的规则，head 改动后旧摘要立即失效。

**直接原因**：协调窗口看到 `gh pr list` 显示 `CLEAN` 即认定可合。`CLEAN` 只表示 required checks 绿 + 无冲突。本文件「Git、Review 与授权」段已明写「required checks 全绿只是必要条件，不构成自行合并授权：配有监理窗口的棒必须先获 Monitor PASS」。该条被转述过多次后仍被违反。

### 二、为什么处置为保留而非撤回

独立核查（读写分离、协调窗口逐条复核）确认：

1. **真正未经任何 Review 的面只有返修增量** `c000bd4..1fd0cab`：13 文件 `+687/-109`。
   `1fd0cab..30f2250` 的 16 文件 `+2409/-6` 是前向合并 `phase0/main` 的内容，经逐文件树内容比对，与当时 `f27c014` **差异为 0**，已各自通过所属棒的门禁，不构成新审查面。
2. 该 687 行返修增量经 A 档标准复核，**未发现必须撤回的阻断问题**：用途边界、成环 fail-closed、分页完整性、凭证纪律、`*span` 排除、`managerid` 未被猜测使用、schema 授权边界、测试强度，均为 fail-closed 或收紧。
3. 撤回的代价与风险：`git revert -m 1` 会删除 13 个文件（属须专项授权的红线动作）；alembic migration 已进 main；原 commit 仍在历史中，重新引入必须构造新候选（revert 该 revert 或重打补丁）并重走完整 Review。

**判据是「不成比例」**：为一个不完整的门禁撤回一批本身无越界、已实质审查的代码，代价与收益不匹配。

### 三、事后监理的 FAIL 说明的是什么

在最终 head `30f2250` 上补跑的独立监理判 **FAIL**，阻断问题为：

> 在 `list_user_memberships` 的现有 SQL 中加入按组织关系收窄的谓词（不加 Port 参数、不加 adapter 状态、不加公开方法、不用敏感词名），
> `tests/architecture/test_organization_directory_boundary.py` 仍 `7 passed`。
> 根因：`_StructuralReadVisitor` 检查 Python 的条件分支、条件推导式、`filter()`、循环与未批准 callable，**但不检查 `text(...)` 内的 SQL 谓词**。

**须严格区分**：这是**门禁覆盖性缺陷**，不是活的边界违规。监理必须**自行注入**一个 SQL 谓词才能证明守卫无效；全仓生产使用面复核确认该 port/reader **当前没有任何授权消费者**，只有模块导出与测试引用。即：已合并代码本身没有越界，但保护该边界的守卫拦不住 SQL 形式的越界。

**这是同一道门禁第二次被换花样绕过**：第一轮词名守卫被方法内私有 predicate 绕过，改为结构守卫；第二轮结构守卫被 SQL 谓词绕过。据既有经验，同一门禁被绕第二次即应换路线，不再补分支。

**正确路线是行为断言而非静态检测**：合成一个同时具备多个组织值与空组织值的用户，`list_user_memberships` 必须返回完整集合——无论过滤用哪种语言实现都会变红。

### 四、处置（五条，缺一不可）

1. PR #144 **永久记为不合规**；不得使用「补齐」「追认」「恢复合规」等措辞，事后 Review 结果不得回填冒充合并前记录。
2. 立即立 **`P2-ORGDIR-BOUNDARY-GUARD-001`**（A 档）修复门禁覆盖缺陷，按上述行为断言路线实现。
3. 在该棒合入前，**冻结一切依赖组织目录的新增集成**：`P2-AUDIT-TRACE-SCOPE-001`、`P2-INTERNAL-WO-SCOPE-001` 不得开棒或续开。
4. 事后 Review 若发现新的阻断问题，处置升级为撤回，本条作废。
5. 防复发规则见下条。

### 五、防复发：合并前必须核对的两项事实

**A 档合并前，不得以 PR 状态色（`CLEAN` / `MERGEABLE`）作为 Review 前置的判据。** 必须逐项核对**两个可验证的事实**：

1. **监理结论文件存在，且判定为 PASS**，且其绑定的 head 等于待合并 head；
2. **Opus `review-meta.json` 的 `head_sha` 等于待合并 head**，且 `verdict=PASS`、`review_model_verified=true`。

任一不成立即不得合并。`gh pr list` 的状态只反映 required checks 与冲突，**与 Review 前置无关**。


## 2026-09-06 — 裁决：`P2-USER-PROFILE-READ-001` 的 Opus 桥豁免是单次动作授权，不构成先例

**决定**：`P2-USER-PROFILE-READ-001` 为 A 档，`AGENTS.md` 要求合并前在 PR 放置 `### Opus 评审桥` JSON 摘要。
雨爷 2026-09-06 指示「OPUS 桥先不跑，我用 workbuddy 里的 kimi k3 手动跑一次」，随后对该前置明确答复
「豁免」。该棒实际执行的是：独立监理窗口（动态取证，判 FAIL 后两轮返修）+ kimi k3 静态评审（20 项，19 PASS
/ 1 阻断），两者独立撞上同一个阻断项（头像响应缓存头），该项已在同棒修复。

**这是单次动作授权，不构成先例。** 它只覆盖 `P2-USER-PROFILE-READ-001` 这一次合并，不外溢到同类触碰面、
不外溢到后续任何棒。

**兜底**：**后续 A 档棒仍按 `AGENTS.md` 现行条款执行 Opus 评审桥**——顺序为实现棒自审 → 独立 Monitor PASS
→ Opus 桥 → 合并，摘要字段闭集与合规条件不变，head 改动后旧摘要立即失效。本条不建立「等效独立静态评审可
替代 Opus 桥」这一规则；若要建立，须由 GOV-SYNC 另行裁定并给出等效判据（模型可验证性、摘要字段闭集、
base/head 绑定如何成立），在该裁定落盘前不得援引本条豁免。

**影响面**：`PHASE2_PLAN.md` 中同名活欠债的 expiry_condition 由本条满足其第 ① 支（「Opus 桥是 A 档不可替代
前置，本次豁免不外溢」）。


## 2026-09-07 — 开发助手治理与 skills 收敛（P2-GOV-SYNC-ASTRA-001）

**决定**：雨爷批准按实际触碰面分配审查：A 档 Codex 自审 `high`，随后独立 Monitor PASS → Opus；B 档自审 `medium`；C 档无自审门禁，保留匹配改动的确定性验证。取消 C 档例行合并后抽查，有具体疑点才有界取证。Opus 现役模型、effort、最终候选绑定与监理三轮上限均不变。

**替代关系**：本条替代 2026-08-20「Opus 终审默认档位改为 high」决定中 Codex 自审保持 `xhigh` 的部分，以及旧 AGENTS.md 的全档 `xhigh` 与 C 档例行抽查；其他历史理由与事实保持原意。全局个人规则不得重新给本仓库 B/C 档加独立 Review。A 档独立取证的价值仍来自独立输入与真实执行，而非重复读代码。

**覆盖性修正**：替代 2026-08-21「把本次改动全部回滚，该门禁仍绿即无覆盖」的笼统判据。新增行为的反证须保留回归测试，只回退对应生产改动或注入故障，并观察相关断言变红；接线证明仍可采用。既有回归检查前后均绿可以证明兼容性，不能单独证明新增行为；格式、链接和解析检查按自身用途验收。缺接线只能在授权范围内补齐，越出文件/合同边界仍须报扩域，冻结 Golden 项仍须显式人批。

**执行边界**：合并授权仅阻断合并，不提前阻断授权内实现、验证、修复和 PR 准备。A/B 档独占工作树，C 档可用主工作树但集成仍走任务分支 PR；A 类机械同步与 B 类治理裁决分开，串行不豁免后者归 GOV-SYNC。规则、skills 与文档改变不表示已迁移 Runtime LLM、变更系统配置或修改生产代码。

**知识组织**：AGENTS.md 保留权限、风险、验证与审查不变量；监理模板维护具体操作和输出。项目前后端 skills 在 `.agents/skills/` 同源维护并纳入版本控制，Claude 入口只路由；个人规则和全局 skills 不进入仓库。STATUS.md 只保留结果、日期、阻塞与指针，历史返修留 Git，旧测试数字不冒充本棒实测。

**依据与局限**：[GPT-6 Astra 官方指南](https://developers.openai.com/api/docs/guides/latest-model) 提醒审计冲突指令、非必要停点和过度验证；[Skills 官方说明](https://learn.chatgpt.com/docs/build-skills) 支持按需加载及显式调用策略。指定 X 文章未能直接读取，转载只作线索；本条分档是雨爷的治理选择，不是厂商承诺的质量或性能结论。


## 2026-09-07 — 裁决：开发助手的模型路由

**决定**：**astra high** 用于 A 档实现与监理；**astra medium** 用于 B / C 档与流程性操作；
**luna（`gpt-5.6-luna`，effort `xhigh`）承担文档类、检索取材与重复性工作**，其结论仍须复核后才能当作
事实。雨爷 2026-09-07 补充：能并行的任务在保证安全与质量的前提下并行推进，不为省事串起来做。

**三条边界**：

1. 本条只定**派工时选用的模型与思考强度**，不改变 `AGENTS.md` 分档表里的自审门禁与独立审查要求；两者在
   A=high、B=medium 上一致，C 档仍是「无自审门禁」。
2. 本条**不改变 Runtime LLM 运行时合同**（`AGENTS.md` 不变量 2：vLLM raw JSON mode，默认
   `http://34.74.11.38:8011/v1` + `glm-4.7`）。开发助手换模型与 Runtime 用什么模型是两回事。
3. Opus 评审桥的现役锁定模型与 effort **不受本条影响**。

**已核实的执行事实（不改变路由决定本身，只记录当前可达性）**：2026-09-07 实测 `gpt-5.6-luna` +
effort `xhigh` 在本机 codex CLI 上**可用**。同日曾以 `gpt-6-luna` / `gpt-6-luna-mini` / `luna`
三个名字探测并得到 `400 ... not supported when using Codex with a ChatGPT account`，**那是模型名写错，
不是账号限制或额度耗尽**；据错名的探测结果推断「luna 不可达」是一次实际发生过的误判，记此以免重犯。

**影响面**：开发助手派工时的模型选择；不影响 `AGENTS.md` 分档表、`app/` Runtime 配置或 Opus 评审桥配置。


## 2026-09-17 — 决定：结束三模型同题对比实验

**决定**：雨爷已结束三模型同题对比实验，模型分工按实验结论固化，存档保留在仓库外。将来仅在更换模型版本或任务类型时重启，且须改由非参赛方评分。


## 2026-09-20 — 记录：现役 Opus 桥及本批非阻断发现处置


本批 `P2-AUDIT-EVAL-POSTCOND-001` 与 `P2-AUDIT-WO-LIFECYCLE-001` 在桥 PASS 后未改候选代码，避免使已绑定候选的 Monitor 与桥结论失效；4 + 6 条非阻断发现由本棒按五字段登记。生命周期 `completion` 的未知值回显与重复值 schema 不符作为同一合同缺口登记；时间格式重复为维护依赖方向的已知取舍，不派生抽取公共函数义务。

这是本批处置记录，不是“桥 PASS 后一律禁改代码”的通用规则。后续仍可按 AGENTS 修改代码并重新验证、重评与绑定最终候选；历史 2026-08-30“Opus PASS 即终局”处置不得解释为覆盖现役规则。


## 2026-09-23 — P2-GOV-SYNC-068：开发助手模型路由与四档施工


| 环节 | 模型与 effort |
|---|---|
| 主控；A/B 档 plan | Fable 5.1，high；不可用时依次回退 Opus 5.5 xhigh、astra max（见第 1 条） |
| plan 评审 | astra（`gpt-6-astra`），max（雨爷 2026-09-23 指定） |
| Monitor | astra（`gpt-6-astra`），high |
| A 档施工 | Opus 5.5（`claude-opus-5-5`），high；施工出现设计级或长程失稳问题时换 Fable 5.1 |
| B 档施工 | Opus 5.5 high（简单任务 medium），或 GPT-6 Sol max |
| D 档施工 | GPT-6 Luna，medium |

1. **Fable 不可用时的回退**（雨爷 2026-09-23）：Fable 5.1 因额度、服务故障或安全拒绝不可用时，主控、plan 编写与 A 档改派 Fable 的施工依次回退到 Opus 5.5 xhigh、astra max。Fable 与 Opus 共用 Claude 账户额度，额度耗尽时通常一起不可用，因此设第二级。任务本身失败不算不可用，不触发回退。回退按实际模型如实记录；plan 由 astra 写成时，plan 评审另开独立上下文的 astra max 会话，并在评审结论中注明作者与评审同源。
2. **effort 必须显式传**：Opus 5.5 在 API 上的默认 effort 是 medium，派发时不写即静默降档。Opus 施工默认不用 xhigh（雨爷 2026-09-23：测评中与 high 差距很小），需要时以实测收益为依据；第 1 条的回退用 xhigh 是雨爷另行指定。
4. **ports 与档位**：仅触碰 `app/ports/` 不再自动升档；实际改变安全语义的按高风险处理，公共契约变更的人工停点、全量测试触发与历史补审义务分别按现役规则执行。
5. **独立性**：PR 按实际模型如实记录。独立性来自独立上下文、完整候选材料与职责分离；厂商不同不作为通过条件。
6. **校准记录**：每个 PR 记录档位、风险标记、实际执行模型与 effort（取不到实际值时记请求值）、返修轮次；A 档另记是否换过 Fable 及原因。每 20 个任务回看一次，按返修集中的档位调整模型或分界。

**替代关系**（历史正文不改写，事故记录与当时结果保持原意）：
- 2026-09-07「裁决：开发助手的模型路由」全文。
- 2026-09-07「开发助手治理与 skills 收敛（P2-GOV-SYNC-ASTRA-001）」中的分档、工作树、自审 effort 与路由部分；该条的覆盖性修正、执行边界（其中工作树一句除外）与知识组织继续有效。
- 2026-08-31「流程违规记录：`P2-OA-ORGANIZATION-DIRECTORY-001` 在缺 Review 前置时被合并」第五节：第 2 项写死 Opus `review-meta.json` 的部分改为现役静态评审桥的合规摘要；A/B 档均核对静态评审，Monitor PASS 仅在现役审查矩阵要求时核对。「不得以 PR 状态色作为 Review 前置判据」与逐项核对最终候选绑定继续有效。
- 2026-08-20「裁决：Opus 终审默认档位改为 `high`」：effort 由本条路由表承接；`observed_model=claude-opus-5` 的型号锁定改为现役桥各自的模型核验合同（桥脚本按实际请求型号核验，PR 以 `observed_model` 如实记录）。
- 2026-09-01「裁决：监理职责收窄为独立执行取证，返修上限三轮」：Monitor 模型改按本条路由，高风险 B 档按有界范围取证；静态事实归静态评审桥（不再限定 Opus）；职责划分与三轮上限保留。
- 2026-09-06「裁决：`P2-USER-PROFILE-READ-001` 的 Opus 桥豁免是单次动作授权，不构成先例」中「后续 A 档仍须执行 Opus 桥」改为执行现役静态评审桥；该次豁免不外溢的结论保留。
- 2026-09-17「决定：结束三模型同题对比实验」中「分工按实验结论固化」由本条路由替代；实验已结束、将来重启须由非参赛方评分的结论保留。

**保留不变**：覆盖性证明要求、串行同步、最终候选绑定、合并前逐项核对与留证、监理三轮上限；Runtime LLM 合同（`AGENTS.md` 不变量 2）不受开发助手换模型影响。

**依据与局限**：Anthropic 迁移指南（Opus 5.5 默认 effort、Fable 5.1 行为调优）与 OpenAI 最新模型指南（冲突指令、按影响验证、astra 行为倾向）为本次规则措辞的依据；路由本身是雨爷的治理选择，不是厂商给出的最优配置。定稿与两轮 astra 评审在仓库外 `v5-runs/GOV-TIER-RULES-20260922/`。


## 2026-09-24 — P2-GOV-SYNC-069：静态评审按作者交叉评审


| 档 | 施工方 | 静态评审方 |
|---|---|---|
| A | Anthropic 系（Opus 5.5、Fable 5.1） | astra（`gpt-6-astra`），medium |
| A | OpenAI 系（如回退情形下 astra 施工） | Opus 5.5，high |
| B | Opus 5.5 | GPT-6 Sol，max |
| B | GPT-6 Sol | Opus 5.5，medium |

1. **交叉**：静态评审方须与施工方不同厂商；所需评审方不可用时，按 2026-09-17「裁决：不可用评审桥的替代静态评审方」经当次授权替代。Monitor 与静态评审允许同厂商，但使用独立会话，保持适用的串行顺序与相同候选绑定。真实候选判 FAIL 必须处理发现，不得换桥寻求 PASS。
2. **模型证据**：Opus 桥以服务端响应中的模型为证（`model_evidence=server`）。Codex 桥优先取服务端模型；取不到时以与本次运行 `thread_id` 对应的 codex 会话记录为证（`model_evidence=cli_session`，非服务端证据，雨爷 2026-09-24 接受此取舍）。不得以请求参数或模型自述代替会话记录；服务端模型与请求不符时判不通过，不得降级改用会话证据；两类证据都取不到、关联失败或冲突时判 BLOCKED。
3. **只读边界**：静态评审方可用只读命令查阅代码、历史与合同，不运行测试、构建或任何写操作。桥依据实际工具事件检测越界执行，检测到即判 BLOCKED、非零退出，不产生可用于合并的 PASS。



## 2026-09-29 — P2-GOV-SYNC-070：开发助手路由更新与本批状态同步

**决定**：雨爷 2026-09-29 明确指定下列路由与审查安排，覆盖 2026-09-23「P2-GOV-SYNC-068：开发助手模型路由与四档施工」与 2026-09-24「P2-GOV-SYNC-069：静态评审按作者交叉评审」的对应条目；历史条目正文不改写。分档、风险、验证与红线的规则正文仍唯一见 `AGENTS.md`；本条只维护派工模型、effort、审查顺序与记录形式，且是现役路由型号的唯一载体（`AGENTS.md` 不维护型号表）。

| 环节 | 模型与 effort |
|---|---|
| 主控（Codex） | astra（`gpt-6-astra`），ultra |
| A/B 档 plan | GPT Pro，经 codex-chatgpt-web 浏览器通道（slug `chatgpt-web/gpt-6-pro`，effort=max，Browser-only） |
| plan 独立评审 | astra，ultra（新上下文） |
| A 档施工 | astra，high |
| B 档施工 | sol，max |
| C 完成后 | Sol high 独立静态审核（本条新增；C 不加自审、plan 或 Monitor） |
| D 完成后 | 无独立审核；保留 diff、解析与真实结果核对（D 不加 plan 或 Monitor） |
| Monitor（A 档与高风险 B 档） | astra，high |

5. **GPT Pro 通道**：Browser-only 只写 plan，不需要 MCP/API key；全量 Codex 上下文会经本地桥送到 ChatGPT，plan 应新建会话、只提供必要已脱敏上下文。浏览器登录未完成前如实报告不可用，不以 Astra 旧稿冒充 Pro 产物。codex-chatgpt-web 的安装与登录进度属外部证据，不写入本条等稳定路由文档。
7. **本棒 A 类机械同步**：PR #211 `P2-WO-ERROR-CONTRACT-001` 已于 2026-09-22 合并；PR #212 `P2-TENANT-IDENTITY-001` S1 已于 2026-09-23 合并。`STATUS.md` / `PHASE2_PLAN.md` 中把两者写成候选、待审、未 push 或未合并的陈旧表述据此更正。S2/S3 未施工、开棒标识待 GOV-SYNC 分配；全链验收前维持单租户运行，第二租户禁止启用，相关义务不因 S1 合并核销。已决后继分配为 `P2-ADMIN-EVENTS-UNIFORM-001`：2026-09-08 方向 A 已批准，待 GPT Pro 计划与 Astra 评审，尚未生产实现；该登记不构成施工或合并授权。`P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002` 必达链的 OA 审批提交协议结构阻塞保持不变。
8. **测试库事实（不外推）**：固定测试库已获当次专项授权迁移到 `20260922_190000`，三表行数与 tenant 列、复合主键、索引核验及环境诊断通过；该授权仅限本次动作，不外推到其他 DB 或其他 schema 操作。



## 2026-09-29 — P2-GOV-SYNC-071：计划审核强度、执行者提示粒度与 Pro 额度提醒

**决定**：一、计划审核改为由 Astra high 执行，主控仍为 Astra ultra，其余施工与后审分工保持不变；二、计划粒度按执行者匹配，A/B 出计划与审计划，C/D 不产生 Pro plan，由主控直接下达任务书；三、Pro 额度耗尽时立即在当前会话提醒用户，且不静默回退更换模型。此前已执行的 ultra 审查记录原样保留，不改写为 high。本记录不虚构任何执行结果或审批，也不增加额外审核步骤。

**替代关系**：仅修正 P2-GOV-SYNC-070 路由表的 plan 独立评审（原为 astra ultra 新上下文；该表中 MiMo 相关行已于 2026-11-19 删除）effort 为 `high`；主控仍为 `ultra`；其余安排以 070 原表为准（该表中 MiMo 相关安排已于 2026-11-19 删除）。操作模板与额度信号处理见 [Codex 主控使用说明](codex_orchestration_to_20261119.md)。本条不变更产品合同、验证要求、权限红线或合并授权。


## 2026-09-30 — P2-GOV-SYNC-071 补充：主控 max 与新窗口接续

**决定**：用户将 Codex 主控由 Astra ultra 改为 Astra max；计划独立审核仍为全新 Astra high，A、B、C、D 执行分工不变，其他审核不变。旧运行记录不改写，新窗口须核对实际模型与 effort。 项目 `.codex/config.toml` 的主控 `model_reasoning_effort` 设为 `max`；仅替代此前路由中的主控 ultra，plan 审核 high、执行者提示粒度与 Pro 额度提醒沿用 2026-09-29 修正。新窗口接续方法见 [Codex 主控使用说明](codex_orchestration_to_20261119.md)，不新增审查或人工 Gate。


## 2026-10-21 — 主干整合澄清原文


## 2026-10-27 — AgentScope Runtime 选型的历史记录

1. **选型与可替换边界**：采用 AgentScope SDK（锁定提交 `6c60853f4237dbc237803cdee2fb96a27a6a0611`）作为新 Runtime 的通用编排，沿用“企业控制内核自有、通用执行内核复用”。SDK 同进程集成，框架类型限于 `app/infra/`，ports 与业务合同不暴露框架类型，可替换为其他基础设施实现；Gateway、Policy、企业控制内核与确认执行权不变，工具仍经过既有业务授权和执行入口。不启用 `agentscope.app`、Hub 或外部追踪导出，不另建 Service 或前端。

4. **分棒与切换**：`P2-GOV-SYNC-M1-OPEN`（本批，D 档）→ `P2-RUNTIME-AGENTSCOPE-M1A-001`（A 档、高风险：读路径、工具组、会话状态、infra 架构守卫、删除 M0 计数路由）→ `P2-RUNTIME-AGENTSCOPE-M1B-001`（A 档、高风险：确认/写入、C1–C6、Golden 注入接缝），串行推进。M2 继续承担 D 收集、普通聊天浏览器、三份迁移清单承接与新路径 Golden 34 题正式回放；M6 继续承担默认入口整体切换。新路径以配置 `RUNTIME_ENGINE`（`legacy|agentscope`，默认 `legacy`）切换，M1 不切默认入口。
5. **M0 删除专项授权**：2026-10-27 已专项授权在 M1A 删除 `app/api/v1/agentscope_m0.py`、`app/infra/orchestration/agentscope_m0_turn.py`，以及 `app/application.py`、`app/composition.py`、`app/config.py` 中对应接线和 `AGENTSCOPE_M0_TURN_ENABLED` 开关、`tests/scripts/test_browser_registered_server.py` 中对应参数。授权记录已核本机启动器未开启该开关；执行时仍核对生产配置未开启，仓库外 `m0-runs` 证据保留。本条替代 M1 方案旧文中留待 M1B 申请删除的安排，本治理批不执行删除。
