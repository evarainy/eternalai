# AGENTS.md — Phase 2 项目规则 v3.1.1

本文件是项目级规则与约束的唯一权威；Skills、模板和历史记录不得另设权限或门禁。

## 权威、范围与执行

- 权威顺序：当前 Goal 的最新明确指令及 Outcome/Constraints/Verification > 用户红线和适用的 `AGENTS.md` > 已批准的产品/架构/接口/批次/里程碑文档 > 代码、测试、CI、分支保护和运行证据 > 派生计划、skills、历史与建议。一般任务授权不代替红线动作的专项授权。
- 当前状态见 `docs/phase2/STATUS.md`，范围、DAG 与欠债见 `docs/phase2/PHASE2_PLAN.md`，架构与治理裁决见 `docs/phase2/DECISIONS.md`；已完成棒用 `git log --grep='phase2('` 追溯。旧任务指令不自动沿用。
- 写入任务开棒声明 **档位、风险、串行或并行、是否承担 A 类同步**；每根 write lane 一个 Goal 和 Scope，新 scope 开新 lane。咨询与只读调查不启动施工流程。
- 完成授权内的完整 scope、验证、修复与交付准备；部分受阻时继续独立可做部分并说明阻塞。常规可逆选择自行处理，重大未决选择、扩域或红线才提问；等合并授权不阻断准备工作。补充消息不丢失原目标和已完成工作。
- 只读与任务相关的入口和合同，按需扩展；无关问题登记后续。报告区分实测、推断和未验证，不以启动成功代替完成。
- 子智能体只承担输入、文件责任和输出明确的独立任务；小改动直接处理。Monitor 与静态审核职责见下文，不能互相替代。

## 实现取舍

- 模块动手前先查相关官网文档，确认当前版本的推荐用法；官方没有可用方案时，找同类成熟实现借鉴。有适配的成熟代码就复用，优先项目已有实现和依赖，只做必要的薄适配；都不满足才自研缺口。新增依赖遵守现有许可、依赖策略和授权要求。
- 以最小必要改动完成当前业务路径。抽象、配置项、兼容层与扩展点须有当前需求或实际重复支撑，不为假想未来搭框架，不顺手重构无关代码。
- 校验放在外部输入和实际信任边界；内部使用已成立的类型、框架与调用合同，不层层重复判空、转换或兜底。错误按合同明确暴露，不用宽泛捕获、静默默认值、回退或重试掩盖缺陷。必要的鉴权、隔离、业务校验、事务和已约定的故障恢复照常实现。
- 功能开发先完成本次交付的业务路径与必要错误路径，再集中 Review 和实际验证；默认不新增测试代码，不每改几行就跑测试。排障可先做最小复现，纯测试任务按其目标执行。任务明确要求静态开发阶段不写、不跑测试时遵守该边界；所需审查顺序和 required checks 仍执行。
- 沿用日志设施，在关键状态变化、外部调用失败和必要耗时处记录阶段、错误码与合同允许的关联标识，便于实际排障；不逐层重复记录，不输出凭证、个人信息或完整业务请求/响应，不新建监控框架。

## 项目不变量与红线

1. 六边形依赖：`app/ports/` 是 Protocol 接口，`app/infra/` 是实现；ports 不得依赖 infra。`app/ports/` 契约仅在设计需要时最小变更，记录理由并同步所有实现与测试，不以 workaround 绕开。
2. Runtime LLM 使用 vLLM raw JSON mode；默认 `http://34.74.11.38:8011/v1` + `glm-4.7`，URL、model 与采样参数可由 env 覆盖；禁止 instructor / PydanticAI。开发助手的模型选择不改变此运行时合同（见 `docs/phase0/PHASE1_TECHNICAL_BASELINE.md` §3.1）。
3. `tests/` 镜像 `app/`；架构守卫在 `tests/architecture/`；`experiments/` 只放 Spike，不进入生产。禁止修改 `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md`。
4. 不得弱化测试换绿（空断言、宽泛 skip、删除断言等）；失败路径保留 error code，不报成功；不得回归 session/tenant/user 隔离。Golden negative/boundary 必须 100% 通过，包括 GT-012 多绑定 scope clarification；下游 descriptor 存在不释放依赖门。
5. 安全分流只依赖可真实校验的协议事实或配置值，不以 `ENV` 等自由文本标签承担安全开关。
6. 明文 password/token/cookie/sessionid/access_token/refresh_token 不得进入代码、Trace、ResponseEnvelope、fixture expected、日志或报告；不得用 `not_applicable` 隐藏失败，未完成义务按「Git 与永久任务记录」登记欠债。
7. **读取未脱敏素材须雨爷当次人工显式授权**，包括 `_scratch/oa/` 原始 HAR、登录日志、截图和未确认清洗的采集件。只读、提取字段、排查泄漏不构成授权；核查泄漏限已生成且符合读取授权的日志与产物，不读原始件。相关委派必须带此限制，授权不外溢到其他文件或后续动作。
8. 删除文件/目录或改写 Git 历史、修改 secrets/`.env`、DB schema/真实数据或迁移、全局/系统变更、公开发布/生产部署、rebase、reset-hard、force push、修改分支保护设置，均须对应动作专项授权；`FROZEN_GT_IDS` / Golden fixtures 也须人工显式批准。不得绕过 hooks 或 branch protection。
9. 一次性作业禁令不原样固化为长期合同；只维护经现役设计验证的永久约束。

## 分档、风险标记与审查

施工档按难度、风险按后果，均看实际行为或合同变化，不按文件后缀或提及的主题。混合任务取最高档；不确定时难度往低、风险往高。降档或摘除高风险须雨爷同意并登记。模型与 effort 以当前明确指定或 `docs/phase2/DECISIONS.md` 现役路由为准，本文件不维护型号表。风险标记不额外产生人工 Gate。

| 档 | 判据 | plan | worktree |
|---|---|---|---|
| A | 架构层面或十分复杂：新子系统、跨层状态机、跨进程/跨标签页一致性、首次组合多个执行接缝 | 写 plan，经独立评审后施工 | 独占 worktree/branch |
| B | 边界清楚但需要强推理：既定合同下的安全相关实现、跨组件异步状态、非平凡算法 | 写 plan（可短），经独立评审后施工 | 独占 worktree/branch |
| C | 常规功能与修复 | 任务中列出实现与验证步骤 | 独占 worktree/branch |
| D | 机械活、说明文档、开发助手规则/skills、补测试、状态同步 | 不写 | 可在主工作树编辑，集成仍走任务分支 PR |

**高风险**：改变认证、CSRF、授权、脱敏、会话/租户/用户隔离、凭证语义或受保护数据的可见范围；不可逆的外部写入；DB schema 或迁移；Golden fixture 或 `FROZEN_GT_IDS`。高风险任务至少按 B 档施工。

| 档 | 普通 | 高风险 |
|---|---|---|
| A | 新上下文自审 PASS → 独立 Monitor PASS → 静态审核 PASS，串行 | 同左，验证范围按实际影响 |
| B | 新上下文自审 PASS → 静态审核 PASS | 新上下文自审 PASS → 有界 Monitor（只对风险路径取证）PASS → 静态审核 PASS，串行 |
| C | 自行核对 + 独立跨模型静态审核，实际验证按影响 | 不适用，遇到即升 B |
| D | 核对 diff、解析与真实结果；无独立审核 | 不适用，遇到即升 B |

- 自审用同系列**新上下文**，不再加同一施工上下文自审。C/D 不加 plan 或 Monitor。各档均核对 diff、文档冲突、测试有效性和实际结果，并完成匹配改动的实际验证。
- 人工停点：红线、扩域、新增或变更架构/框架/公共契约/API/协议/信任边界/核心不变量、重大未决选择、批次/里程碑验收。恢复已批准合同的修复按 Goal 推进；保持架构的内部 ports 变更不另设 Gate。
- 无 local-commit Gate；开棒说明合并授权，未明确则先完成准备，在合并处等待。

### A/B 档独立证据

按上表顺序执行，Monitor 未 PASS 不进入静态审核。plan 仅由现役路由指定方独立评审，不叠加复审。取证分工依据**具体事实能否静态判定**。

- Monitor 取证类别、具体操作、负向形态与输出格式见 `docs/phase2/MONITOR_PROMPT_TEMPLATE.md`，生成监理任务时才读：A 档执行全部五类，高风险 B 档只对风险路径执行适用类别并写明范围。不改写未经批准的 Golden fixture / `FROZEN_GT_IDS`，改用临时用例或书面推演并标明未经执行。必需证据取不到即停手报告，不以推演或工具失败冒充 PASS。
- 静态审核（含桥与 OpenCode）只读代码、历史与合同，不运行测试、构建或写操作，越界即 BLOCKED。负责合同完整性、类型可达性、scope/diff、显式授权判断、文件落点、直接 import、声明与跨文件一致性；Monitor 不重复这些静态项，静态方不承担动态越权、门禁接线或动态 import/registry 取证。
- 生产改动由不同厂商模型交叉静态评审，组合见 DECISIONS 现役路由；Monitor 与静态方可同厂商，须独立会话。Review 聚焦可触发的缺陷、合同与接线，不因假想扩展或缺少非必需测试要求返工；静态 PASS 不声称运行通过。
- 同一 task_id 至多三轮监理（中止、无结论或改写重发均计轮次），第三轮仍非 PASS 即停手交雨爷裁决，不开第四轮。
- 合并前核对静态审核及适用的 Monitor PASS 均绑定最终 base/head；任一变化须重评。评审桥模型/effort 由主窗口维护，PR 如实记录 `observed_model`。

## Git 与永久任务记录

- 主分支 `phase0/main`；任务分支 `phase2/<task_id>`；commit 为 `phase2(<task_id>): <简述>`，merge 为 `merge phase2(<task_id>): <简述>`。
- A 档棒将纯格式化改动与功能改动分开提交；本条不增加 CI 检查。
- 集成：任务分支普通 push → PR → required checks 全绿 → 获准合并；不得本地合完直推主分支。验证、Review、候选 freshness 和分支保护均须满足，绿灯不是授权。合并后检查对应 merge SHA 的 GitHub Actions。
- 不建 per-task Task Record。合并前 PR body 写齐 `## Scope`、`## 验证结果`、`## 本棒新增欠债`；验证列实际命令、最小充分原始结果、未执行原因、候选 commit 与 CI run。欠债含 reason、blocked_by_task_id、activation_task_id、expiry_condition、evidence，无新增则写明。
- A/B 档 PR 合并前附静态评审记录，按实际评审方式（评审桥、OpenCode 原生会话、经雨爷当次授权的替代评审方）使用 `docs/phase2/REVIEW_EVIDENCE.md` 的格式。**不伪造桥字段，不把模型自述当服务端证明，不合并后补证。**

## 状态同步

- **A 类机械同步**只含基线数字、task_id、已决后继指针和本棒新欠债；不是 A 档风险分类。状态写 `docs/phase2/STATUS.md`，欠债写 `docs/phase2/PHASE2_PLAN.md`。
- 串行时由实现棒在同一个 payload commit 同步；不另开同步 commit、不 amend、不 force push。真正并行的多 write lane 不改共享状态/欠债文件，由独立 GOV-SYNC 批次同步。
- **B 类治理裁决**（跨棒裁决、推翻蓝图偏差的 ADR、Golden 策略、DAG 重排、跨棒欠债合并）无论串并行都归 GOV-SYNC。实现棒只传播已决且唯一的后继；不唯一则留空并登记待裁决。
- 治理文档不写 commit SHA / CI run id；证据留 PR，历史留 Git。状态保留当前结果、基线来源日期、阻塞与指针，不累积返修流水账，也不把旧数字标成当前实测。

## 验证策略

默认不新增自动化测试，以跨模型静态 Review 和获授权环境中的实际流程、结果、日志核对为主；已有测试只选受影响且有必要的部分。不按数量或覆盖率扩写测试，纯文档不启动应用。静态 Review 与日志不能证明运行正确或替代必要权限验证；环境不足则标记未实测，不拿 mock 或单测绿冒充真实验收。

新增/修复行为核对真实接线；运行验证记录操作、预期与实际结果。仅对重复缺陷、重要业务/权限边界或明确验收要求补最少的自动化回归，不要求每次修改都写测试。若用变异证明覆盖，保留测试只改生产路径；既有测试前后均绿只证明兼容。格式/链接/解析检查不要求变异，一次性检查不默认提交。

完成适用检查后即交付；仅因新改动、失败或具体未决风险重跑或扩大验证，不为安心反复检查。

- **说明文档**：diff 检查、受影响链接/路径/标题/术语与决定冲突检查、未跟踪文件清点；不跑无关 pytest、Golden 或端口测试。
- **机器消费文档与 skills**：增加对应解析器、skill validator 或最窄合同/加载检查；生成器会写文件时，只在已授权输出范围执行。
- **仅测试**：运行改变的测试与直接覆盖的最小生产路径，运行相应弱测试检查；全量仅按下列触发条件。
- **普通生产改动**：覆盖改变的行为与必要失败路径，按问题选择实际操作或已有定向测试；涉及接缝检查真实接入。lint / typecheck / build 按实际影响选择，格式化和自动修复只处理授权文件。
- 依赖/manifest/lockfile/allowlist 变化查依赖；import/registry/六边形边界变化查对应架构用例；测试变更查对应弱测试。实际改变任务理解、Capability 选择、Policy、Workflow、ResponseEnvelope、终态或负向/边界语义时运行 Golden；仅提及或改注释不触发。
- 前端流程见 `.agents/skills/frontend-task/SKILL.md`；后端流程见 `.agents/skills/backend-task/SKILL.md`。入口按需读，不额外规定固定全量套餐。

### 全量测试触发条件

仅在以下情况运行全量，并说明覆盖范围：
1. 用户、验收标准、required checks 或分支保护明确要求；CI 已承担的全量不默认在本地重复。
2. 共享基础设施、公共合同或广泛重构的影响无法用定向验证充分覆盖，能指出具体遗漏风险；先扩大到受影响包，确实跨全仓才跑全仓。

高风险标签、触及某个核心文件或到了收口阶段，本身不触发全量。认证、授权、隔离等改动必须覆盖实际受影响的拒绝与越权路径；所需 Review、Monitor 和 Golden 冻结规则不变。全量不能替代定向检查与真实业务验证。

全量缺少 DB 环境或失败即停手报告，不跳过、静默降级、重试掩盖或弱化测试，不改凭证或连接目标换绿；省略测试须获显式批准并列明 `--ignore=` 与原因。环境前置见 `.agents/skills/backend-task/SKILL.md`。

| 命令 | 使用时机 |
|---|---|
| `uv run python scripts/check_dev_environment.py --start-full-tests` | 命中全量触发条件；读取最终后台结果，日志/状态在 `_scratch/` |
| `uv run pytest <changed-or-nearest-test-path>` | 后端定向验证 |
| `uv run pytest tests/ports/test_capability_gateway_port.py` | Capability Gateway port、实现或合同 |
| `uv run python scripts/check_dependencies.py` | 依赖、清单或依赖策略 |
| `uv run pytest tests/architecture/<affected-test-file>` | 受影响的 import、registry、架构边界；广泛影响才跑整个目录 |
| `uv run python scripts/check_weak_tests.py <changed-test-file>` | 新增或修改对应测试 |
| `uv run python scripts/run_golden_tasks.py --gate` | Golden 语义或明确要求 |
| `git diff --check` | 所有写入棒 |
| `git ls-files --others --exclude-standard` | 所有写入棒收口；保留项逐项解释 |

## 文件组织与临时产物

- 项目技能正文与所需资源在 `.agents/skills/<name>/`；`SKILL.md` 写触发、项目特有约束和入口，条件细节按需读。Claude 的 `.claude/skills/<name>/SKILL.md` 只路由到同源正文。只跟踪已审计的精确文件，不纳入个人设置、凭证、缓存或退役作业文件。
- Goal 快照、Candidate Manifest、Recovery Index、Review 证据与摘要放仓库外 `$CODEX_RUNS_ROOT`，未设时用 `$CLAUDE_CODEX_SCRATCH_ROOT/v5-runs`。两者均未设时为本任务选定临时根并声明，不修改系统环境；`_scratch/` 只放手工临时文件，不暂存。
- 常设清理授权仅覆盖当前任务 worktree 的 `__pycache__/`、`*.pyc`、`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`。先查未跟踪文件，为空不删；仅操作解析后的精确目标，不用通配范围递归删除，不碰其他工作树、Git 历史、源码/产物与 `.venv/`。其他删除须专项授权。
- 开工记录既有改动，收口核对本任务新增文件并解释保留项；不处理他人或既有未跟踪文件。Scope 未包含时不扫描或清理 `.venv/`。
- Phase 1 模板/索引/任务日志、旧 `docs/dev/task_record_schema.yaml`、跨阶段 `docs/phase0/` 指南及长规格仅在解决具体历史或合同问题时读取，不能增设现役流程。
