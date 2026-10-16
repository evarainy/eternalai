# AGENTS.md — Phase 2 项目规则 v3.1.0

本文件是项目级规则与约束的唯一权威。`CLAUDE.md` 只以 `@AGENTS.md` 导入本文件并保留 Claude Code 专属补充；不导入长规格文档。Skills、模板和历史记录不得另设权限或门禁。

## 权威、范围与执行

- 权威顺序：当前 Goal 的最新明确指令及 Outcome/Constraints/Verification > 用户红线和适用的 `AGENTS.md` > 已批准的产品/架构/接口/批次/里程碑文档 > 代码、测试、CI、分支保护和运行证据 > 派生计划、skills、历史与建议。一般任务授权不代替红线动作的专项授权。
- Phase 1 已完成。当前状态只见 `docs/phase2/STATUS.md`；范围、DAG 与欠债只见 `docs/phase2/PHASE2_PLAN.md`；架构与治理裁决见 `docs/phase2/DECISIONS.md`。已完成棒用 `git log --grep='phase2('` 追溯；旧提示词保留历史原意，不自动恢复为现役规则。
- 每根 write lane 只有一个 Goal 和 Scope；新 scope 开新 lane。worktree 要求见「分档、风险标记与审查」。开棒声明 **档位、风险标记、串行或并行、是否承担 A 类同步**。
- 在授权范围内完成实现、相关验证、修复和交付准备；不因等合并授权而提前停止这些工作。常规可逆实现选择自行处理，重大未决选择、扩域或红线才提问。执行中收到补充要求或问题时，保留原目标与已完成工作。
- 交付 Goal 所定的完整 scope，不自行收窄、扩大或替换。某部分受阻时完成其余部分，并写明缺什么、为什么。与本棒无关的既有问题登记为后续；完成已授权行为所必需、且在范围内的修复继续完成，超出范围另报扩域。报告进度前逐项对照本会话的工具结果；未验证项明确标注，不以启动成功代替完成。
- 按问题定位入口与相关合同，不默认通读仓库或历史长文。Skills 按实际任务选择；其新增停点或扩大范围要求不能覆盖本文件。检查通过后，仅因新改动、失败或具体未决风险扩大或重跑验证。
- 子智能体用于可独立完成的有界任务，明确责任与只读/写入范围；简单或中型任务可单窗口完成，不机械增设多窗口审批。只有 Goal 明确要求独立 Monitor 时，其动态职责不得由静态复核替代。

## 开发与真实验收约定

以下八条是现役工作约定；本文件后文及 `DECISIONS.md`、上层通用建议、skills、模板中的旧路由或验证套餐不得恢复与之冲突的默认要求。

1. 每个新模块或功能动手前，有界核查成熟组件及官方、开源实现。能满足需求则优先复用，其次配置或薄适配；自研只补已核实缺口，不无限调研。
2. 先完成业务代码和最少关键节点脱敏日志，再做静态审查，随后以真实代码、真实流程、实际目标环境验收。实际操作结果与运行记录共同为准；日志重要但不是唯一依据，Mock 或自写测试通过不能代替真实接通。
3. 默认不新增独立测试工程、测试脚本或新测试用例，不跑本地全量；保留必要静态检查。现有 required CI 照常运行，不绕过保护；必要的既有测试接口兼容修复须在任务范围内，不借修测试扩大业务范围，也不弱化现有断言。
4. 正常使用真实正式环境；条件不具备先说明具体缺项，由雨爷决定是否使用 Mock，不写死全面禁止 Mock。真实环境操作仅限已授权业务动作；填写、保存、提交分别判定授权，不擅自提交，也不由验收约定自动取得取密、登录、发布或其他专项授权。
5. 根因不明先补最少关键日志、运行取证，再定点修复，避免猜测修改和同类无效循环。日志脱敏，保留必要运行和错误记录；临时高噪音诊断日志验收后关闭，删除文件仍须符合专项授权，不能派生任意清理权限。
6. 复杂任务由 Astra 出可执行计划并做业务代码 Review，SOL 执行；测试脚本 Review 用 SOL，这不构成新增测试授权。主控与执行分开适用于复杂任务，简单或中型任务可单窗口完成，不机械新增自审、Monitor、交叉厂商或评审桥门禁。
7. 主控每次派发或换窗必须完整携带这八条、实际仓库 `AGENTS.md` 路径和最新用户边界，不能仅写“遵守主控聊天”；新窗口回报已读及本任务真实验收路径、具体缺项。
8. 最小改动，复用已有事实与配置，不反复索取已提供信息；隔离无关工作，避免污染主控上下文。共享文件单 writer，保留其他窗口未提交修改，不擅自覆盖、重置或清理。

当前开发优先级：以当前用户功能的真实闭环为第一目标。非阻塞的验证、加固和重构只记录待办、闭环后处理；不新增测试工程、重复基线核验、无关全量检查或额外审批层。每项新增工作必须说明直接阻断哪个当前用户功能，说不清则不做；复用已有代码、配置和成熟组件，Review 只聚焦新增业务差异。仍须保护凭据不输出或外传、用户身份不串用、真实业务不误保存或提交，不绕平台访问拒绝或浏览器安全告警；费用、供应商、真实业务数据出网和业务写入仍按现有授权边界。

## 项目不变量与红线

1. 六边形依赖：`app/ports/` 是 Protocol 接口，`app/infra/` 是实现；ports 不得依赖 infra。`app/ports/` 契约仅在设计需要时最小变更，记录理由并同步所有实现与既有测试消费接口，不以 workaround 绕开。
2. Runtime LLM 使用 vLLM raw JSON mode；默认 `http://34.74.11.38:8011/v1` + `glm-4.7`，URL、model 与采样参数可由 env 覆盖；禁止 instructor / PydanticAI。开发助手的模型选择不改变此运行时合同（见 `docs/phase0/PHASE1_TECHNICAL_BASELINE.md` §3.1）。
3. `tests/` 镜像 `app/`；架构守卫在 `tests/architecture/`；`experiments/` 只放 Spike，不进入生产。禁止修改 `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md`。
4. 不得弱化测试换绿（空断言、宽泛 skip、删除断言等）；失败路径保留 error code，不报成功；不得回归 session/tenant/user 隔离。Golden negative/boundary 必须 100% 通过，包括 GT-012 多绑定 scope clarification；下游 descriptor 存在不释放依赖门。
5. 安全分流只依赖可真实校验的协议事实或配置值，不以 `ENV` 等自由文本标签承担安全开关。
6. 明文 password/token/cookie/sessionid/access_token/refresh_token 不得进入代码、Trace、ResponseEnvelope、fixture expected、日志或报告。不得用 `not_applicable` 隐藏失败；未完成义务按欠债登记 reason、blocked_by_task_id、activation_task_id、expiry_condition、evidence。
7. **未脱敏素材任何时候都不得读取，除非取得雨爷当次人工显式授权。** 包括 `_scratch/oa/` 的原始 HAR、登录日志、截图及未经确认清洗、可能含真实凭证、内网地址或人员信息的采集件。只读、提取字段、排查泄漏或补齐素材都不构成授权；核查泄漏只能查已生成的日志与产物，不再读原始件。此限制同样写入相关委派任务，授权不外溢到其他文件或后续动作。
8. 删除文件/目录或改写 Git 历史、修改 secrets/`.env`、DB schema/真实数据或迁移、全局/系统变更、公开发布/生产部署、rebase、reset-hard、force push，均须对应动作专项授权；`FROZEN_GT_IDS` / Golden fixtures 也须人工显式批准。不得绕过 hooks 或 branch protection。
9. 一次性作业禁令不原样固化为长期合同；只维护经现役设计验证的永久约束。

## 分档、风险标记与审查

施工档按难度定，决定是否写 plan 与派给哪类执行方；风险标记按后果定，决定审查强度。两者分别判定：按实际改变的行为或合同判断，不按工作量、文件后缀或提及的主题。一次改动混有多档按最高档；拿不准时难度往低放、风险往高放，因为模型不够强会表现为看得见的返修，风险漏审的缺陷则会悄悄流出。降档或摘除高风险标记须雨爷明确同意并登记。开发助手按上文 Astra / SOL 分工；`docs/phase2/DECISIONS.md` 的模型与 effort 细节仅在不冲突时适用，不恢复旧路由或默认多窗口门禁。风险标记与 risk_tier 不额外制造人工停点。

| 档 | 判据 | plan | worktree |
|---|---|---|---|
| A | 架构层面或十分复杂：新子系统、跨层状态机、跨进程/跨标签页一致性、首次组合多个执行接缝 | Astra 出可执行 plan，SOL 施工 | 独占 worktree/branch |
| B | 边界清楚但需要强推理：既定合同下的安全相关实现、跨组件异步状态、非平凡算法 | 复杂时同 A；简单或中型任务列实现与验证步骤，可单窗口 | 独占 worktree/branch |
| C | 常规功能与修复 | 任务中列出实现与验证步骤 | 独占 worktree/branch |
| D | 机械活、说明文档、开发助手规则/skills、补测试、状态同步 | 不写 | 可在主工作树编辑，集成仍走任务分支 PR |

**高风险**：改变认证、CSRF、授权、脱敏、会话/租户/用户隔离、凭证语义或受保护数据的可见范围；不可逆的外部写入；DB schema 或迁移；Golden fixture 或 `FROZEN_GT_IDS`。高风险任务至少按 B 档施工。

| 档 | 普通 | 高风险 |
|---|---|---|
| A | Astra plan → SOL 施工 → Astra 业务静审 → 获准的真实流程验收 | 同左，针对风险路径保留实际结果及脱敏运行证据 |
| B | 复杂时同 A；简单或中型任务自行静态核对及获准的真实流程验收 | 按实际复杂度选分工，针对风险路径取证；不自动触发本地全量 |
| C | 自行核对 diff、必要静态检查及获准的真实流程结果，可单窗口 | 不适用，遇到即升 B |
| D | 核对 diff、解析与真实结果；无独立审核 | 不适用，遇到即升 B |

- 各档均核对 diff、必要静态检查、文档冲突及实际结果；D 档无独立审核。复杂任务执行上表 Astra / SOL 分工；简单或中型任务不因档位机械增设独立审核、自审或 Monitor。Goal 明确要求的额外证据另按其范围执行，静态 PASS 不代替真实验收。
- 人工停点来自红线、扩域、新增或变更架构/框架/公共契约/API/协议/信任边界/核心不变量、重大未决选择或批次/里程碑验收。恢复已批准合同的缺陷修复按当前 Goal 推进，不因档位或风险标记而另设人工 Gate；改变权限设计或合同仍须先批。保持既有架构的内部 ports 变更本身不增设停点。
- 不设独立 local-commit Gate。各档合并授权须在开棒时明确；未明确时先完成可做工作，在合并处等待。复杂任务须满足下述 Astra 业务静审与真实验收要求，其他任务不因档位叠加默认 Review 门禁。

### 业务 Review 与明确要求的独立证据

复杂任务按 Astra 可执行计划 → SOL 施工 → Astra 业务静审 → 获准的真实环境验收推进；测试脚本 Review 用 SOL。Review 绑定准确候选及 scope，只证明相应静态事实。若 Goal 明确要求额外自审或独立 Monitor，再执行对应职责及顺序；不得默认叠加窗口或用推演冒充动态 PASS。

- Goal 明确要求独立 Monitor 时，按授权的风险路径选择适用取证：变异与故障注入、真实授权路径的攻击矩阵、fixture 合法取值充分性、真实依赖保真度、实测数字复核。高风险 B 档的有界 Monitor 只对风险路径执行其中适用的类别，并写明取证范围。禁止改写未经批准的 Golden fixture / `FROZEN_GT_IDS`，也不自动新增临时测试；不能执行的证据如实记缺项，书面推演不冒充实测。
- 静态审核只读（评审桥脚本或 OpenCode 原生会话同样适用）：可用只读命令查阅代码、历史与合同，不运行测试、构建或任何写操作，越界执行即判 BLOCKED；只判静态事实：合同完整性、类型层可达性、scope 与 diff、显式授权判断完整性、文件落点和直接 import、声明缺失、跨文件一致性。上述静态项不重复列入 Monitor 必做项；动态越权、门禁接线、动态 import / registry 解析不交给静态审核方。
- 默认业务静审由 Astra 承担、SOL 施工，不要求不同厂商交叉评审；Goal 明确另选评审方时才采用其指定组合。独立 Monitor 若明确要求，仍须保持职责与会话独立。
- 生成监理任务时才读 `docs/phase2/MONITOR_PROMPT_TEMPLATE.md`，其中维护具体操作、负向形态与输出格式。必需证据取不到即停手报告，不以推演或工具失败冒充 PASS。
- 同一 task_id 最多发出三份监理提示词；中止或未产出结论也计轮次，覆盖/改名/修订不减计。第三轮仍非 PASS 即停手交雨爷裁决，不开第四轮。
- 合并前核对实际要求的 Review 与运行证据均绑定最终 base/head 或明确候选摘要；受审文件或相关合同变化则旧结论失效。复杂任务记录 Astra 业务静审的实际模型证据（能取得才填）、verdict、候选及会话产物；明确要求 Monitor 时还有对应 PASS 证据。实际使用评审桥时，配置由主控维护，并如实记录 `observed_model`，不虚构模型证明。

## Git 与永久任务记录

- 主分支 `phase0/main`；任务分支 `phase2/<task_id>`；commit 为 `phase2(<task_id>): <简述>`，merge 为 `merge phase2(<task_id>): <简述>`。
- A 档棒将纯格式化改动与功能改动分开提交；本条不增加 CI 检查。
- 集成只走任务分支普通 push → PR → required checks 最终全绿 → 获准的 PR 合并；不得本地合完直推主分支。验证、所需 Review、候选 freshness、分支保护与 required checks 均须满足；绿灯本身不是合并授权。每次合并后检查对应 merge SHA 的远端 GitHub Actions 结果。
- 不建 per-task Task Record。PR body 合并前必须完整包含 `## Scope`、`## 验证结果`、`## 本棒新增欠债`。验证段逐条记录实际命令、最小充分原始结果、未执行项理由、候选 commit 与 CI run；欠债每条带 reason、blocked_by_task_id、activation_task_id、expiry_condition、evidence，无新增则写明。
- 实际运行静态评审桥时，验证段须含其 JSON 摘要（标题按获准工具可用 `### Opus 评审桥`、`### Codex 评审桥` 或 `### MiMo 评审桥`，不把工具选项变成默认评审路由），由桥脚本生成，字段闭集：`requested_model`、`observed_model`、`model_evidence`、`review_model_verified`、`requested_effort`、`verdict`、`base_sha`、`head_sha`、`provider_error`、`invalid_stream_lines`、`termination_reason`。`model_evidence` 为 `server`（服务端响应中的模型）或 `cli_session`（与本次运行对应的 codex 会话记录，非服务端证据）；`review_model_verified` 指按该证据类型核验一致。合规要求 `review_model_verified=true`、PR 摘要如实记录实际 `observed_model`、`verdict=PASS`、`provider_error=false`、`termination_reason=completed`，base/head 绑定最终候选；不得放响应原文或敏感值。PR 三段、欠债字段和摘要均不得合并后补写。
- 经明确授权用 OpenCode 原生会话做静态审核、未运行桥脚本时，PR 验证段改用简短的 `### OpenCode 静态评审` 记录，写清：请求模型与 effort、实际取得的模型证据（能取得才填）、verdict（PASS / FAIL / BLOCKED）、绑定的最终 `base_sha` 与 `head_sha`、会话产物位置。**不伪造上一条的桥脚本闭集字段，不把模型自述当服务端证明**；不能据历史路线自动替代本文件的 Astra / SOL 分工。桥脚本实际运行时仍按上一条的闭集字段与真证据约束执行。D 档不要求该记录。
- 评审桥因额度耗尽或服务故障不可用时，经雨爷当次明确授权，可由**替代静态评审方**（如子智能体）承担 A/B 档静态评审。此时 PR 验证段改用 `### 静态评审（替代评审桥）` 段落，如实记录：替代授权的来源与日期、实际使用的模型、`verdict`、绑定的 `base_sha` 与 `head_sha`、以及未运行评审桥的原因。**不得伪造 `review_model_verified`、`termination_reason`、`provider_error`、`invalid_stream_lines` 等由桥脚本产出的闭集字段**——没有跑桥就不写这些字段。替代评审方的判据、只读边界与「不重复 Monitor 已取证的动态事实」要求与评审桥一致。
- Owner 已登记待办：为主分支保护开启 **Do not allow bypassing the above settings**；无专项授权不得代改。

## 状态同步

- **A 类机械同步**只含基线数字、task_id、已决后继指针和本棒新欠债；不是 A 档风险分类。状态写 `docs/phase2/STATUS.md`，欠债写 `docs/phase2/PHASE2_PLAN.md`。
- 串行时由实现棒在同一个 payload commit 同步；不另开同步 commit、不 amend、不 force push。真正并行的多 write lane 不改共享状态/欠债文件，由独立 GOV-SYNC 批次同步。
- **B 类治理裁决**（跨棒裁决、推翻蓝图偏差的 ADR、Golden 策略、DAG 重排、跨棒欠债合并）无论串并行都归 GOV-SYNC。实现棒只传播已决且唯一的后继；不唯一则留空并登记待裁决。
- 治理文档不写 commit SHA / CI run id；证据留 PR，历史留 Git。状态保留当前结果、基线来源日期、阻塞与指针，不累积返修流水账，也不把旧数字标成当前实测。

## 验证策略

以最接近改动路径的必要静态检查、实际流程结果和关键脱敏运行记录验收；不默认新增测试或跑本地全量，required CI 与分支保护仍须满足。

**区分证明目的**：新增或修复行为须核对真实产品接线，并在获准目标环境中以实际操作结果和运行记录验收；静态、Mock 或测试绿不能单独证明真实接通。既有测试可证明相应兼容性，不为满足形式另建用例或脚本，也不弱化断言。根因未明先做最少关键日志取证，不能猜测修改。格式、链接、解析检查按自身用途验收，不要求变异。缺接线在已授权范围内补齐，超出文件或合同边界先报扩域；Golden 冻结授权不变。保留必要脱敏运行和错误证据，临时高噪音诊断验收后关闭；文件删除仍遵守专项授权。

- **说明文档**：diff 检查、受影响链接/路径/标题/术语与决定冲突检查、未跟踪文件清点；不跑无关 pytest、Golden 或端口测试。
- **机器消费文档与 skills**：使用既有解析器、skill validator 或最窄合同/加载检查，不另建测试或检查工程；生成器会写文件时，只在已授权输出范围执行。
- **仅测试**：只在雨爷明确指派时施工；必要既有接口兼容修复不新增用例、不弱化断言。按当次授权做对应检查，required CI 中的测试和弱测试检查照常执行，不自动启动本地全量。
- **普通生产改动**：先核对业务代码和关键日志，选择匹配影响的 lint / typecheck / build 等必要静态检查，经适用 Review 后执行获准的真实流程验收。格式化和自动修复限制在授权文件，不全仓重写，不自动补测试套餐。
- 依赖、manifest、lockfile、allowlist 及 import/registry/六边形边界变化，做匹配的依赖与边界静态核对；测试、Golden、架构回归按 required CI 或当次明确验证要求执行，不据主题自动新增本地测试。Golden 负向与边界质量要求、冻结保护不变。
- 前端流程见 `.agents/skills/frontend-task/SKILL.md`；后端流程见 `.agents/skills/backend-task/SKILL.md`。入口按需读，不额外规定固定全量套餐。

### 本地测试与 required CI

默认不跑本地全量。高风险、核心信任边界、广泛重构、发布或里程碑本身不自动触发本地全量；只有雨爷明确要求本地全量时，才在该次授权范围执行。现有 required CI、分支保护与明确验收标准仍须满足，不能跳过或用局部结果替代其要求。

确获授权执行本地全量时，沿用 Docker Desktop、固定测试库 `127.0.0.1:15432` 与 `DATABASE_URL` 的入口检查，不改凭证或连接目标换绿。真实验收或检查环境缺项先说明具体事实，由雨爷决定补环境或是否使用 Mock；Mock 结果不能冒称正式环境通过，也不解除 required CI。运行失败保留错误事实并按关键日志定位根因，不用切换 Mock、静默跳过、弱化断言、吞错或重试掩盖失败。

以下既有命令保留为入口；测试命令仅用于 required CI 或当次明确授权的验证，不构成默认本地运行要求。

| 命令 | 使用时机 |
|---|---|
| `uv run python scripts/check_dev_environment.py --start-full-tests` | 雨爷明确要求本地全量；读取最终后台结果，日志/状态在 `_scratch/` |
| `uv run pytest <changed-or-nearest-test-path>` | required CI 或明确授权的后端定向测试 |
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
