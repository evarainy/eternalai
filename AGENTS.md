# AGENTS.md — Phase 2 项目规则 v2.4.1

本文件是项目级规则与约束的唯一权威。`CLAUDE.md` 仅用 `@AGENTS.md` 导入本文件并保留 Claude Code 专属补充；项目级治理文件只允许这一项导入，禁止导入长规格文档。

## 权威与当前阶段

权威顺序：当前 Goal（最新指令及 Outcome/Constraints/Verification）> 用户红线和适用的 `AGENTS.md` > 已批准的产品/架构/接口/批次/里程碑文档 > 仓库代码、测试、CI、分支保护和运行证据 > 派生计划、skills、历史与建议。

Phase 1 已完成。当前 Phase 2 状态只见 `docs/phase2/STATUS.md`；已完成棒见 `git log --grep='phase2('`，正式登记与欠债见 `docs/phase2/PHASE2_PLAN.md`，架构决定见 `docs/phase2/DECISIONS.md`。

每个 write lane 只承载一个明确 Goal、一个 Scope 和一个隔离 worktree/branch；新 scope 开新 lane。历史 V4 prompt/Task Record 保持原意，新工作以当前 Goal 和本文件为准。

## 项目不变量

- 六边形依赖方向：`app/ports/` 是 Protocol 接口，`app/infra/` 是实现；`app/ports/` 不得依赖 `app/infra/`。
- LLM 使用 vLLM raw JSON mode；默认 `http://34.74.11.38:8011/v1` + `glm-4.7`，URL、model 与采样参数均可由 env 覆盖；禁止引入 instructor / PydanticAI（见 `docs/phase0/PHASE1_TECHNICAL_BASELINE.md` §3.1）。
- `tests/` 镜像 `app/` 结构，并以 `tests/architecture/` 承载架构守卫；`experiments/` 仅放 Spike 实验代码，不进入生产。

## Git、Review 与授权

- **主分支**：`phase0/main`（不是 `main`）。
- **任务分支**：`phase2/<task_id>`。
- **Commit**：`phase2(<task_id>): <简要描述>`。
- **Merge**：`merge phase2(<task_id>): <简要描述>`。
- Q0-Q3 只控制 Review 强度，不制造人工停点。人工停点仅来自专项红线动作、扩域、新增或变更架构/框架/公共契约/API/协议/信任边界/核心不变量、重大未决选择、更严格的仓库规则或批次/里程碑验收；保持既有架构的内部 `app/ports/` 变更本身不是停点。
- 不设独立 local-commit Gate。普通非强制任务分支 push/PR/merge、CI/CD 配置修改和 CI 运行，仅在 Goal 与仓库规则允许，且确定性验证、所需 Review、freshness、branch protection、required checks 均通过时执行。集成到 `phase0/main` 一律走 PR：push 任务分支 → 开 PR → 等 required checks 最终全绿 → 通过 PR 合并。即使没有显式 bypass 参数，也永不本地合完直推 `phase0/main`。
- Phase 2 不建独立 per-task Task Record。每个 PR body 必须固定包含 `## Scope`、`## 验证结果（实际执行的最小充分验证原始结果 + CI run；pytest / Golden 仅在命中触发条件时列入）`、`## 本棒新增欠债` 三段；PR body 是绑定 commit 与 CI run 的永久任务记录。
- PR body 三段必须在合并前全部完成；`## 本棒新增欠债` 中每条欠债须在合并前具备 reason、blocked_by_task_id、activation_task_id、expiry_condition、evidence 五个字段。合并后补写不计为合规任务记录。required checks 全绿只是必要条件，不构成自行合并授权：配有监理窗口的棒必须先获 Monitor PASS；未配监理窗口的棒，只有启动提示词显式授权时才可自行合并。
- A 档棒的 PR 还必须在合并前于 `## 验证结果` 下放置 `### Opus 评审桥` JSON 摘要；B / C 档不适用。摘要字段闭集仅为 `requested_model`、`observed_model`、`review_model_verified`、`requested_effort`、`verdict`、`base_sha`、`head_sha`、`provider_error`、`invalid_stream_lines`、`termination_reason`，不得放模型响应原文或任何敏感值。合规摘要须同时满足 `review_model_verified=true`、`observed_model` 等于现役锁定模型、`verdict=PASS`、`provider_error=false`、`termination_reason=completed`，且 `base_sha` / `head_sha` 绑定最终候选；head 改动后旧摘要立即失效，必须重新评审并替换。合并后补写不计为合规任务记录。
- A 档的两道 Review **必须串行，监理在前、Opus 在后**：实现棒自审 → 独立监理 PASS → 才跑 Opus 桥 → 合并。监理判 FAIL 时不得先跑 Opus。
- **监理是独立执行取证方**（沿用「监理」这一既有称谓，Monitor PASS 同义），不是第二个读代码的人。分工判据只有一条：**这件事靠读代码能不能判定？** 能读出来的一律归 Opus，监理不得重复复核。监理只做四类必须执行才能判定的取证：①**变异与故障注入**——把本棒改动回滚，或按 schema 允许的合法形态收窄生产逻辑后，相关门禁是否真的变红；②**测试数据充分性**——fixture 与被测输入的取值是否恰好让守卫看不见某类合法形态，须通过改动 fixture 取值重跑来证伪；③**依赖保真度**——断言是否走真实依赖路径（真实 DB、真实授权路径、真实 migration 往返）而非替身；④**数字复核**——自己重跑并与登记基线逐项对照。
- Opus 评审环境无 shell，只能静态阅读；把只有执行才能判定的问题交给它，会得到不可靠的 PASS，且 head 一变绑定即失效、整轮作废。归 Opus 的是：合同完整性、类型层可达性、授权边界与 scope 越界、目录与分层合规、声明缺失、跨文件一致性。这些项不得同时出现在监理合同里。
- **监理返修上限三轮。** 第三轮监理仍未 PASS 即停手上报，由雨爷裁决是交付确有缺陷还是监理判据失焦，不得自行开第四轮。轮次按同一 task_id 下的监理结论份数计。
- 本棒走多重的流程由下方「任务分档」决定：它规定要写几份提示词、要不要独占 worktree、要不要配监理窗口、要不要过 Opus 桥。
- 仓库 owner 待办：为 `phase0/main` 的 GitHub 分支保护打开 **Do not allow bypassing the above settings**。本项是已登记欠债；没有专项授权时 agent 不得修改该设置。
- 删除文件/目录或改写历史、secrets/`.env`、DB schema/真实数据、全局/系统变更、公开发布/生产部署、rebase、reset-hard、force push，均需对应动作的专项授权；风险标签不构成授权。不得绕过 hooks 或 branch protection。
- 每次 merge 到 `phase0/main` 后检查对应的 remote GitHub Actions CI 结果。

### 任务分档

按**本棒实际触碰的面**定档，不按工作量或耗时估计定档；命中更高档的任一条件即整棒按更高档执行。

| 档 | 触碰面 | 提示词 | worktree | 监理窗口 | Opus 桥 |
|---|---|---|---|---|---|
| **A** | 安全边界（认证 / CSRF / 脱敏 / 会话·租户·用户隔离）、`app/ports/` 契约、DB schema、凭证语义、Golden fixture 或 `FROZEN_GT_IDS` | 启动 + 监理两份 | 独占 | **必配** | **必过** |
| **B** | 其余生产代码与运行配置 | 启动一份 | 独占 | 免 | 免 |
| **C** | 仅文档、仅测试、仅 `_scratch/` | 免，口头交代即可 | 免，可在主工作树改 | 免 | 免 |

- **三档都不豁免**：与本棒实际触碰面相匹配的确定性验证必须通过；同时遵守 PR 合并路径、红线停点、PR body 三段和欠债五字段。验证按下方「验证策略」选择，不按档位机械执行固定套餐。
- **三档都必须** Codex 自审 effort `xhigh`；B、C 档免掉的只是独立监理窗口与 Opus 桥。
- **拿不准档位按高一档走。** 降档需雨爷明确同意，并在启动提示词或开棒交代里写明降到哪一档。
- B、C 档没有 Monitor PASS 作为合并前置，因此**必须在开棒时显式写明合并授权**；未写明则合并仍是停点。
- C 档合并后由主窗口派子智能体抽查。

### A 类机械同步

- A 类仅包括测试基线数字、`task_id`、下一棒指针和本棒新发现的欠债。当前状态只写 `docs/phase2/STATUS.md`，欠债只写 `docs/phase2/PHASE2_PLAN.md`。
- 同一时刻仅一个 write lane 时，由该实现棒在本棒 payload commit 内一次完成 A 类同步，不另开 commit、不 amend、不 force push；同时有两个及以上 write lane 时，实现棒不得修改共享状态与欠债文件，统一由独立 GOV-SYNC 批次棒处理。
- 治理文档不记录 commit SHA 与 CI run id；以 `git log --grep=<task_id>` 追溯任务，CI 结果以 GitHub 为唯一权威，运行证据留在 PR body 的验证段。

### B 类归 GOV-SYNC

- 跨棒裁决、推翻蓝图偏差的 ADR、Golden 策略、DAG 重排和跨棒欠债合并永远归 GOV-SYNC；实现棒只能机械传播已决 DAG 的下一个 `task_id`，不得自行挑选后继，不唯一时留空并登记待裁决。

### 开棒前声明

- 开棒时必须声明三项：**档位**（A/B/C）、**串行或并行**、以及本棒是否承担 A 类同步。
- **串行**（同一时刻只有一根 write lane）时跳过并行专用的开销：不做 A/B 类同步分流，`docs/phase2/STATUS.md` 由该棒在自己的 payload commit 内直接更新。并行相关规则仅在真正并行时生效。

## 验证策略

验证目标是 **最小而充分**：用最小范围、最接近真实改动路径的检查证明本棒没有破坏相关合同。最小不等于只跑最容易通过的测试；必须覆盖改动的成功路径、失败路径和受影响边界，并在汇报与 PR body 中逐条列出实际命令、结果和未执行项理由。

**门禁覆盖性**：要求运行某项门禁时，须同时给出该门禁实际走到本次改动路径的证据——接线证明，或「故意破坏改动后该门禁应变红」的反证。判据是：**把本次改动全部回滚，该门禁还会绿吗？会绿即说明它没有覆盖改动，其结果不构成本棒的验证证据。** 绿色结果本身不是证据，覆盖了改动路径的绿色结果才是。发现门禁绕开改动路径时，补齐接线属加强、计入本棒范围；但 fixture 与 `FROZEN_GT_IDS` 仍须人工显式批准，不得以改题换绿。

### C 档：文档、测试与 `_scratch/`

- **纯说明性文档**：默认只做 `git diff --check`、受影响链接/路径/标题/术语/前后决定冲突检查，以及收口时的 `git ls-files --others --exclude-standard`；**不运行**全量 pytest、Golden、端口测试或与文档无关的程序门禁。
- **可执行或机器消费文档**（OpenAPI、JSON Schema、fixture、allowlist、CI 配置、命令示例、生成源等）：除文档检查外，只运行对应解析器、生成器或最窄合同测试；是否升级到更高档按其实际触碰面判断。
- **仅测试改动**：运行新增/修改测试及其直接覆盖的最小生产路径，并对改动的测试文件运行弱测试检查；默认不跑全量，除非命中下方全量触发条件。

### B 档：普通生产代码与运行配置

- 默认运行：改动文件对应的单元/组件测试、最近一层集成或合同测试，以及该语言和包实际需要的 lint / typecheck / build；只覆盖受影响模块和调用路径。
- 改动依赖清单、lockfile 或 allowlist 时运行依赖检查；触及 import、registry 或六边形边界时运行架构测试；新增或修改测试时运行对应弱测试检查。
- 只有改动可能影响 Golden 所覆盖的任务理解、Capability 选择、Policy、Workflow、ResponseEnvelope、终态判定或负向/边界行为时，才运行 Golden gate。
- B 档默认**不跑全量测试**；定向验证不足以覆盖风险或命中下方触发条件时再升级。

### 全量测试触发条件

命中以下任一项时必须运行全量测试，并同时保留针对改动路径的定向验证；全量不能替代定向测试：

1. 当前 Goal、用户、验收标准、required checks 或里程碑明确要求；
2. A 档改动，或触及认证、授权、CSRF、脱敏、会话/租户/用户隔离、凭证、Gateway、Policy、Identity、Secret、Trace、Evidence、DB schema、`app/ports/` 公共契约、Golden fixture / `FROZEN_GT_IDS` 等核心信任边界；
3. 跨多个生产层/包的大范围重构，修改共享基础设施、公共协议/API、依赖解析或测试基础设施，无法用一组可枚举的定向测试充分覆盖；
4. 发布、批次或里程碑收口，仓库/分支保护要求全量回归。

全量测试需要 Docker Desktop 启动、测试库 healthy 于 `127.0.0.1:15432`，且当前进程能看到 `DATABASE_URL`（用户级环境变量；进程若早于设置时启动则继承不到，重开终端/应用即可）。缺少 `DATABASE_URL` 时 DB 测试失败而不是跳过；确需省略必须显式使用 `--ignore=` 并披露原因。环境不具备或全量失败时停手报告，不得静默降级、跳过、重试掩盖或修改测试换绿。

### 命令菜单（按触发条件选用，不是固定套餐）

| 命令 | 触发条件 |
|---|---|
| `uv run python scripts/check_dev_environment.py --start-full-tests` | 仅命中「全量测试触发条件」；后台日志/状态写入 `_scratch/` |
| `uv run pytest <changed-or-nearest-test-path>` | 编码棒默认；选择直接覆盖改动路径的最窄测试 |
| `uv run pytest tests/ports/test_capability_gateway_port.py` | 触及 Capability Gateway port 或其实现/合同 |
| `uv run python scripts/check_dependencies.py` | 触及依赖、manifest、lockfile、allowlist 或依赖策略 |
| `uv run pytest tests/architecture/` | 触及 import、registry、六边形边界或架构守卫 |
| `uv run python scripts/check_weak_tests.py <changed-test-file>` | 新增或修改对应测试文件 |
| `uv run python scripts/run_golden_tasks.py --gate` | 触及 Golden 覆盖的运行时语义、负向/边界行为或命中明确要求 |
| `git diff --check` | 所有写入棒收口前 |
| `git ls-files --others --exclude-standard` | 所有写入棒收口前；有意保留时逐项解释 |

## 不可协商规则

1. 禁止修改 `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md`。
2. `app/ports/` 契约只在设计确实需要时变更：采用最小契约，记录理由，并在同一改动中更新所有实现与测试；不得用 workaround 绕开本应修正的契约。
3. 不得为换绿弱化测试（`assert True`、空 `pass`、宽泛 skip、删断言）；修代码，否则停手报告。失败路径不得报成功或丢 error code；不得回归 session/tenant/user 隔离。`FROZEN_GT_IDS` / golden fixtures 必须经人工显式批准。
4. 明文 password/token/cookie/sessionid/access_token/refresh_token 不得进入 Trace、ResponseEnvelope、fixture expected、日志、Task Record 或报告。
5. 不得用 `not_applicable` 隐藏失败；每项必须带 reason、blocked_by_task_id、activation_task_id、expiry_condition 和 evidence。
6. 下游 descriptor 存在不释放依赖门。Golden negative/boundary paths 必须 100% 通过，包括 GT-012 多绑定 scope clarification。
7. 安全开关只能依赖可真实校验的协议事实或配置值；不得让 `ENV` 等自由文本环境标签承担安全分流，避免拼写错误导致 fail-open。
8. 面向单棒执行 agent 的临时作业约束不得原样写入长期交付文档；只保留经当前设计验证的永久合同，防止把一次性禁令误固化为现役规则。
9. **未脱敏素材任何时候都不得读取，除非取得雨爷的人工显式授权。** 未脱敏素材指 `_scratch/oa/` 下的原始 HAR、登录日志与截图，以及任何未经确认清洗、可能含真实 cookie / password / token / 内网地址 / 真实人员信息的采集件。禁止以「只读不写」「只输出字段名与类型」「排查是否泄漏」「素材缺失需补齐」等任何理由自我授权；**核查泄漏时同样不得再读原始件**，只能查已生成的日志与产物。本条同等约束派给 codex 或子智能体的任务：不得在提示词中引导其读取未脱敏素材。授权只覆盖当次动作，不外溢到同目录其他文件或后续任务。

## 按需读取（默认不读）

- Phase 2 监理提示词模板：`docs/phase2/MONITOR_PROMPT_TEMPLATE.md`（生成 A 档监理提示词时读）。
- Phase 1：`docs/phase1/TASK_PROMPT_TEMPLATE.md`、`docs/phase1/TASK_INDEX.md`、`docs/phase1/tasks/<task_id>.md`、`docs/phase1/task_logs/`、`docs/phase1/ROLE_POLICY.md`。
- 旧记录 schema：`docs/dev/task_record_schema.yaml`。
- 跨阶段：`docs/phase0/CONTEXT_LOADING_STRATEGY.md`、`docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md`、`docs/phase0/REPOSITORY_CONTEXT_MAP.md`、`docs/phase0/CODING_STYLE_BASELINE.md`、`docs/phase0/BOUNDARY_CHECKLIST.md`。
- 规范长文（仅在需要时）：`docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md`。

## Scratch/temp 与产物审查

- Goal 快照、Candidate Manifest、Recovery Index、Review 证据和摘要放在仓库外的 `$CODEX_RUNS_ROOT`，未设置时回退到 `$CLAUDE_CODEX_SCRATCH_ROOT/v5-runs`；仓库内 `_scratch/` 仅放手工临时文件。
- 常设清理授权只适用于当前任务 worktree 内的 `__pycache__/`、`*.pyc`、`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`。动手前先跑 `git ls-files --others --exclude-standard`；为空就不删除。只对已解析的精确目标操作，不用通配范围递归删除；不碰 `.venv/`、源码/产物、其他 worktree 或 Git 历史。除此之外的文件/目录删除仍是红线，必须取得对应专项授权；不得暂存 `_scratch/` 内容。
- 收口前 `git ls-files --others --exclude-standard` 必须为空；若有意保留，须逐项解释。
- Scope 未明确包含时，不扫描或清理 `.venv/`。
