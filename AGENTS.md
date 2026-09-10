# AGENTS.md — Phase 2 项目规则 v2.5.0

本文件是项目级规则与约束的唯一权威。`CLAUDE.md` 只以 `@AGENTS.md` 导入本文件并保留 Claude Code 专属补充；不导入长规格文档。Skills、模板和历史记录不得另设权限或门禁。

## 权威、范围与执行

- 权威顺序：当前 Goal 的最新明确指令及 Outcome/Constraints/Verification > 用户红线和适用的 `AGENTS.md` > 已批准的产品/架构/接口/批次/里程碑文档 > 代码、测试、CI、分支保护和运行证据 > 派生计划、skills、历史与建议。一般任务授权不代替红线动作的专项授权。
- Phase 1 已完成。当前状态只见 `docs/phase2/STATUS.md`；范围、DAG 与欠债只见 `docs/phase2/PHASE2_PLAN.md`；架构与治理裁决见 `docs/phase2/DECISIONS.md`。已完成棒用 `git log --grep='phase2('` 追溯；旧提示词保留历史原意，不自动恢复为现役规则。
- 每根 write lane 只有一个 Goal 和 Scope；新 scope 开新 lane。A/B 档独占 worktree/branch；C 档可在主工作树编辑，但集成仍走任务分支 PR。开棒声明 **档位、串行或并行、是否承担 A 类同步**。
- 在授权范围内完成实现、相关验证、修复和交付准备；不因等合并授权而提前停止这些工作。常规可逆实现选择自行处理，重大未决选择、扩域或红线才提问。执行中收到补充要求或问题时，保留原目标与已完成工作。
- 按问题定位入口与相关合同，不默认通读仓库或历史长文。Skills 按实际任务选择；其新增停点或扩大范围要求不能覆盖本文件。检查通过后，仅因新改动、失败或具体未决风险扩大或重跑验证。
- 子智能体用于可独立完成的有界任务，明确责任与只读/写入范围；小改动不固定派发。独立 Monitor 与静态评审桥的职责不得由普通子智能体结论替代。

## 项目不变量与红线

1. 六边形依赖：`app/ports/` 是 Protocol 接口，`app/infra/` 是实现；ports 不得依赖 infra。`app/ports/` 契约仅在设计需要时最小变更，记录理由并同步所有实现与测试，不以 workaround 绕开。
2. Runtime LLM 使用 vLLM raw JSON mode；默认 `http://34.74.11.38:8011/v1` + `glm-4.7`，URL、model 与采样参数可由 env 覆盖；禁止 instructor / PydanticAI。开发助手的模型选择不改变此运行时合同（见 `docs/phase0/PHASE1_TECHNICAL_BASELINE.md` §3.1）。
3. `tests/` 镜像 `app/`；架构守卫在 `tests/architecture/`；`experiments/` 只放 Spike，不进入生产。禁止修改 `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md`。
4. 不得弱化测试换绿（空断言、宽泛 skip、删除断言等）；失败路径保留 error code，不报成功；不得回归 session/tenant/user 隔离。Golden negative/boundary 必须 100% 通过，包括 GT-012 多绑定 scope clarification；下游 descriptor 存在不释放依赖门。
5. 安全分流只依赖可真实校验的协议事实或配置值，不以 `ENV` 等自由文本标签承担安全开关。
6. 明文 password/token/cookie/sessionid/access_token/refresh_token 不得进入代码、Trace、ResponseEnvelope、fixture expected、日志或报告。不得用 `not_applicable` 隐藏失败；未完成义务按欠债登记 reason、blocked_by_task_id、activation_task_id、expiry_condition、evidence。
7. **未脱敏素材任何时候都不得读取，除非取得雨爷当次人工显式授权。** 包括 `_scratch/oa/` 的原始 HAR、登录日志、截图及未经确认清洗、可能含真实凭证、内网地址或人员信息的采集件。只读、提取字段、排查泄漏或补齐素材都不构成授权；核查泄漏只能查已生成的日志与产物，不再读原始件。此限制同样写入相关委派任务，授权不外溢到其他文件或后续动作。
8. 删除文件/目录或改写 Git 历史、修改 secrets/`.env`、DB schema/真实数据或迁移、全局/系统变更、公开发布/生产部署、rebase、reset-hard、force push，均须对应动作专项授权；`FROZEN_GT_IDS` / Golden fixtures 也须人工显式批准。不得绕过 hooks 或 branch protection。
9. 一次性作业禁令不原样固化为长期合同；只维护经现役设计验证的永久约束。

## 分档、Review 与授权

按实际改变的行为或合同定档，不按工作量、文件后缀或提及的主题定档；命中高档即按高档执行，拿不准按高一档。降档须雨爷明确同意并登记。Q0-Q3 / risk_tier 不额外制造人工停点。

| 档 | 实际触碰面 | 提示词 / worktree | 自审 effort | 独立审查 |
|---|---|---|---|---|
| A | 安全边界（认证、CSRF、脱敏、会话/租户/用户隔离）、`app/ports/` 契约、DB schema、凭证语义、Golden fixture 或 `FROZEN_GT_IDS` | 启动 + 监理；独占 | `high` | Monitor PASS → 静态评审桥 PASS，必须串行 |
| B | 其余生产代码与运行配置 | 启动一份；独占 | `medium` | 无固定独立审查 |
| C | 仅说明文档、开发助手规则/skills、仅测试、仅 `_scratch/`；实际改变 A/B 合同时按上档 | 口头交代即可；可用主工作树 | 无自审门禁 | 无例行抽查；有具体疑点再有界取证 |

- 三档均须完成匹配实际改动的确定性验证。C 档取消的是独立自审环节与结论要求，不免除核对 diff、文档冲突、测试有效性与实际结果。
- 人工停点来自红线、扩域、新增或变更架构/框架/公共契约/API/协议/信任边界/核心不变量、重大未决选择或批次/里程碑验收。恢复已批准合同的缺陷修复按当前 Goal 推进，不因定为 A 档而另设人工 Gate；改变权限设计或合同仍须先批。保持既有架构的内部 ports 变更本身不增设停点。
- 不设独立 local-commit Gate。B/C 档合并授权须在开棒时明确；未明确时先完成可做工作，在合并处等待。A 档须满足当前 Goal 的合并授权和下述两道审查。

### A 档独立证据

顺序为实现棒 `high` 自审 → 独立 Monitor PASS → 静态评审桥 PASS → 合并；Monitor FAIL 时不得先跑静态评审桥。分工单位是**具体事实能否静态判定**，不是主题名。

- Monitor 执行五类取证：变异与故障注入、真实授权路径的攻击矩阵、fixture 合法取值充分性、真实依赖保真度、实测数字复核。禁止改写未经批准的 Golden fixture / `FROZEN_GT_IDS`；改用临时用例或书面推演，并标明未经执行验证。
- 静态评审桥无 shell，只判静态事实：合同完整性、类型层可达性、scope 与 diff、显式授权判断完整性、文件落点和直接 import、声明缺失、跨文件一致性。上述静态项不重复列入 Monitor 必做项；动态越权、门禁接线、动态 import / registry 解析不交给静态评审桥。
- 生成监理任务时才读 `docs/phase2/MONITOR_PROMPT_TEMPLATE.md`，其中维护具体操作、负向形态与输出格式。必需证据取不到即停手报告，不以推演或工具失败冒充 PASS。
- 同一 task_id 最多发出三份监理提示词；中止或未产出结论也计轮次，覆盖/改名/修订不减计。第三轮仍非 PASS 即停手交雨爷裁决，不开第四轮。
- 合并前核对 Monitor PASS 文件与静态评审桥合规摘要均绑定最终候选 head；head 改动后旧结论失效，须重评。评审桥现役模型与 effort 配置由主窗口维护，PR 摘要中以 `observed_model` 如实记录。

## Git 与永久任务记录

- 主分支 `phase0/main`；任务分支 `phase2/<task_id>`；commit 为 `phase2(<task_id>): <简述>`，merge 为 `merge phase2(<task_id>): <简述>`。
- A 档棒将纯格式化改动与功能改动分开提交；本条不增加 CI 检查。
- 集成只走任务分支普通 push → PR → required checks 最终全绿 → 获准的 PR 合并；不得本地合完直推主分支。验证、所需 Review、候选 freshness、分支保护与 required checks 均须满足；绿灯本身不是合并授权。每次合并后检查对应 merge SHA 的远端 GitHub Actions 结果。
- 不建 per-task Task Record。PR body 合并前必须完整包含 `## Scope`、`## 验证结果`、`## 本棒新增欠债`。验证段逐条记录实际命令、最小充分原始结果、未执行项理由、候选 commit 与 CI run；欠债每条带 reason、blocked_by_task_id、activation_task_id、expiry_condition、evidence，无新增则写明。
- A 档验证段还须含 `### Opus 评审桥` 或 `### grok 评审桥` JSON 摘要，字段闭集：`requested_model`、`observed_model`、`review_model_verified`、`requested_effort`、`verdict`、`base_sha`、`head_sha`、`provider_error`、`invalid_stream_lines`、`termination_reason`。合规要求 `review_model_verified=true`、PR 摘要如实记录实际 `observed_model`、`verdict=PASS`、`provider_error=false`、`termination_reason=completed`，base/head 绑定最终候选；不得放响应原文或敏感值。PR 三段、欠债字段和摘要均不得合并后补写。
- Owner 已登记待办：为主分支保护开启 **Do not allow bypassing the above settings**；无专项授权不得代改。

## 状态同步

- **A 类机械同步**只含基线数字、task_id、已决后继指针和本棒新欠债；不是 A 档风险分类。状态写 `docs/phase2/STATUS.md`，欠债写 `docs/phase2/PHASE2_PLAN.md`。
- 串行时由实现棒在同一个 payload commit 同步；不另开同步 commit、不 amend、不 force push。真正并行的多 write lane 不改共享状态/欠债文件，由独立 GOV-SYNC 批次同步。
- **B 类治理裁决**（跨棒裁决、推翻蓝图偏差的 ADR、Golden 策略、DAG 重排、跨棒欠债合并）无论串并行都归 GOV-SYNC。实现棒只传播已决且唯一的后继；不唯一则留空并登记待裁决。
- 治理文档不写 commit SHA / CI run id；证据留 PR，历史留 Git。状态保留当前结果、基线来源日期、阻塞与指针，不累积返修流水账，也不把旧数字标成当前实测。

## 验证策略

以最接近改动路径的最小充分检查覆盖成功、失败与受影响边界；全量不能替代定向验证。

**区分证明目的**：新增/修复行为须给接线证据，或保留回归测试、仅回退相关生产改动/注入故障后观察变红；不能把测试一起回退再声称已证明覆盖。既有回归测试前后均绿仍可证明兼容性，但不能单独证明新增行为。格式、链接、解析检查按自身用途验收，不要求变异。发现缺接线可在已授权范围内补齐，超出文件/合同边界先报扩域；Golden 冻结项授权不变。

- **说明文档**：diff 检查、受影响链接/路径/标题/术语与决定冲突检查、未跟踪文件清点；不跑无关 pytest、Golden 或端口测试。
- **机器消费文档与 skills**：增加对应解析器、skill validator 或最窄合同/加载检查；生成器会写文件时，只在已授权输出范围执行。
- **仅测试**：运行改变的测试与直接覆盖的最小生产路径，运行相应弱测试检查；全量仅按下列触发条件。
- **普通生产改动**：对应单元/组件测试、最近一层集成/合同测试，按该语言和包的实际影响选择 lint / typecheck / build。格式化和自动修复限制在授权文件，不全仓重写。
- 依赖/manifest/lockfile/allowlist 触发依赖检查；import/registry/六边形边界触发架构检查；测试变更触发弱测试检查。影响任务理解、Capability 选择、Policy、Workflow、ResponseEnvelope、终态或负向/边界语义时运行 Golden。
- 前端流程见 `.agents/skills/frontend-task/SKILL.md`；后端流程见 `.agents/skills/backend-task/SKILL.md`。入口按需读，不额外规定固定全量套餐。

### 全量测试触发条件

以下任一项成立即运行全量，并保留定向验证：
1. Goal、用户、验收标准、required checks 或里程碑明确要求。
2. A 档或实际改变认证、授权、CSRF、脱敏、会话/租户/用户隔离、凭证、Gateway、Policy、Identity、Secret、Trace、Evidence、DB schema、ports 公共契约、Golden fixture / `FROZEN_GT_IDS` 等核心信任边界。
3. 跨生产层/包的广泛重构、共享基础设施/公共协议/API/依赖解析/测试基础设施变化，无法以可枚举的定向测试充分覆盖。
4. 发布、批次或里程碑收口，或分支保护要求全量回归。

全量入口检查 Docker Desktop、固定测试库 `127.0.0.1:15432` 和 `DATABASE_URL`，以检查结果为准；不改凭证或连接目标以换绿。缺少 DB 环境时失败而非跳过。环境不具备或全量失败即停手报告，不静默降级、重试掩盖或弱化测试；省略测试须获显式批准并列明 `--ignore=` 与原因。

| 命令 | 使用时机 |
|---|---|
| `uv run python scripts/check_dev_environment.py --start-full-tests` | 命中全量触发条件；读取最终后台结果，日志/状态在 `_scratch/` |
| `uv run pytest <changed-or-nearest-test-path>` | 后端定向验证 |
| `uv run pytest tests/ports/test_capability_gateway_port.py` | Capability Gateway port、实现或合同 |
| `uv run python scripts/check_dependencies.py` | 依赖、清单或依赖策略 |
| `uv run pytest tests/architecture/` | import、registry、架构边界 |
| `uv run python scripts/check_weak_tests.py <changed-test-file>` | 新增或修改对应测试 |
| `uv run python scripts/run_golden_tasks.py --gate` | Golden 语义或明确要求 |
| `git diff --check` | 所有写入棒 |
| `git ls-files --others --exclude-standard` | 所有写入棒收口；保留项逐项解释 |

## 文件组织与临时产物

- 项目技能正文与所需资源在 `.agents/skills/<name>/`；`SKILL.md` 写触发、项目特有约束和入口，条件细节按需读。Claude 的 `.claude/skills/<name>/SKILL.md` 只路由到同源正文。只跟踪已审计的精确文件，不纳入个人设置、凭证、缓存或退役作业文件。
- Goal 快照、Candidate Manifest、Recovery Index、Review 证据与摘要放仓库外 `$CODEX_RUNS_ROOT`，未设时用 `$CLAUDE_CODEX_SCRATCH_ROOT/v5-runs`。两者均未设时为本任务选定临时根并声明，不修改系统环境；`_scratch/` 只放手工临时文件，不暂存。
- 常设清理授权仅覆盖当前任务 worktree 的 `__pycache__/`、`*.pyc`、`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`。先查未跟踪文件，为空不删；仅操作解析后的精确目标，不用通配范围递归删除，不碰其他工作树、Git 历史、源码/产物与 `.venv/`。其他删除须专项授权。
- 收口时未跟踪文件应为空；有意保留逐项解释。Scope 未包含时不扫描或清理 `.venv/`。
- Phase 1 模板/索引/任务日志、旧 `docs/dev/task_record_schema.yaml`、跨阶段 `docs/phase0/` 指南及长规格仅在解决具体历史或合同问题时读取，不能增设现役流程。
