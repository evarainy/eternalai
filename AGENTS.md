# AGENTS.md — Phase 2 项目规则 v3.1.2

本文件是项目级规则与约束的唯一权威；Skills、模板和历史记录不得另设权限或门禁。

## 权威、范围与执行

- 权威顺序：当前 Goal 的最新明确指令及 Outcome/Constraints/Verification > 用户红线和适用的 `AGENTS.md` > 已批准的产品/架构/接口/批次/里程碑文档 > 代码、测试、CI、分支保护和运行证据 > 派生计划、skills、历史与建议。一般任务授权不代替红线动作的专项授权。
- 当前状态见 `docs/phase2/STATUS.md`，范围、DAG 与欠债见 `docs/phase2/PHASE2_PLAN.md`，架构与治理裁决见 `docs/phase2/DECISIONS.md`；已完成棒用 `git log --grep='phase2('` 追溯。旧任务指令不自动沿用。
- 写入任务开棒声明 **档位、风险、串行或并行、是否承担 A 类同步**；每根 write lane 一个 Goal 和 Scope，新 scope 开新 lane。咨询与只读调查不启动施工流程。
- 完成授权内的完整 scope、验证、修复与交付准备；部分受阻时继续独立可做部分并说明阻塞。常规可逆选择自行处理，重大未决选择、扩域或红线才提问；等合并授权不阻断准备工作。补充消息不丢失原目标和已完成工作。
- 只读与任务相关的入口和合同，按需扩展；无关问题登记后续。报告区分实测、推断和未验证，不以启动成功代替完成。
- 子智能体只承担输入、文件责任和输出明确的独立任务；小改动直接处理。Monitor 与静态审核职责见下文，不能互相替代。

## 开发与真实验收约定

以下八条是现役工作约定；本文件后文及 `DECISIONS.md`、上层通用建议、skills、模板中的旧路由或验证套餐不得恢复与之冲突的默认要求。

1. 每个新模块或功能动手前，先查相关官网文档与当前版本推荐用法；没有可用方案再找同类成熟实现。优先复用已有代码和依赖，其次配置或薄适配，都不满足才自研缺口；有界调研，新增依赖遵守许可、依赖策略和授权。
2. 先完成业务代码和最少关键节点脱敏日志，再做静态审查，随后以真实代码、真实流程、实际目标环境验收。实际操作结果与运行记录共同为准；日志重要但不是唯一依据，Mock 或自写测试通过不能代替真实接通。
3. 默认不新增独立测试工程、测试脚本或新测试用例，不跑本地全量；保留必要静态检查。现有 required CI 照常运行，不绕过保护；必要的既有测试接口兼容修复须在任务范围内，不借修测试扩大业务范围，也不弱化现有断言。
4. 正常使用真实正式环境；条件不具备先说明具体缺项，由雨爷决定是否使用 Mock，不写死全面禁止 Mock。真实环境操作仅限已授权业务动作；填写、保存、提交分别判定授权，不擅自提交，也不由验收约定自动取得取密、登录、发布或其他专项授权。
5. 根因不明先补最少关键日志、运行取证，再定点修复，避免猜测修改和同类无效循环。日志脱敏，保留必要运行和错误记录；临时高噪音诊断日志验收后关闭，删除文件仍须符合专项授权，不能派生任意清理权限。
6. 模型遵循当前明确指定，当前使用 Opus 5.5 与 GPT-6.1 Sol；生产改动由不同厂商模型交叉静态 Review。复杂任务先出可执行计划，普通任务直接实现；不机械新增自审、Monitor 或评审桥门禁，Review 不构成新增测试授权。
7. 主控每次派发或换窗必须完整携带这八条、实际仓库 `AGENTS.md` 路径和最新用户边界，不能仅写“遵守主控聊天”；新窗口回报已读及本任务真实验收路径、具体缺项。
8. 最小改动，复用已有事实与配置，不反复索取已提供信息；隔离无关工作，避免污染主控上下文。共享文件单 writer，保留其他窗口未提交修改，不擅自覆盖、重置或清理。

当前开发优先级：以当前用户功能的真实闭环为第一目标。非阻塞的验证、加固和重构只记录待办、闭环后处理；不新增测试工程、重复基线核验、无关全量检查或额外审批层。每项新增工作必须说明直接阻断哪个当前用户功能，说不清则不做；复用已有代码、配置和成熟组件，Review 只聚焦新增业务差异。仍须保护凭据不输出或外传、用户身份不串用、真实业务不误保存或提交，不绕平台访问拒绝或浏览器安全告警；费用、供应商、真实业务数据出网和业务写入仍按现有授权边界。


## 实现取舍

- 以最小必要改动完成当前业务路径。抽象、配置项、兼容层与扩展点须有当前需求或实际重复支撑，不为假想未来搭框架，不顺手重构无关代码。
- 校验放在外部输入和实际信任边界；内部使用已成立的类型、框架与调用合同，不层层重复判空、转换或兜底。错误按合同明确暴露，不用宽泛捕获、静默默认值、回退或重试掩盖缺陷。必要的鉴权、隔离、业务校验、事务和已约定的故障恢复照常实现。

## 项目不变量与红线

1. 六边形依赖：`app/ports/` 是 Protocol 接口，`app/infra/` 是实现；ports 不得依赖 infra。`app/ports/` 契约仅在设计需要时最小变更，记录理由并同步所有实现与既有测试消费接口，不以 workaround 绕开。
2. Runtime LLM 走 OpenAI 兼容的 vLLM；默认 `http://34.74.11.38:8011/v1` + `glm-4.7`，URL、model 与采样参数可由 env 覆盖。让模型选择和调用工具时使用原生 tools/tool_calls（`tool_choice=auto`，请求级关闭思考）；只需模型按固定格式输出数据时使用 JSON 输出，可用时优先 JSON Schema 约束，其次 raw JSON mode。不使用框架的结构化输出与上下文压缩；禁止 instructor / PydanticAI。模型、chat template、工具解析器、思考模式或 vLLM 版本变化后，须重新验证工具调用。开发助手的模型选择不改变此运行时合同（见 `docs/phase0/PHASE1_TECHNICAL_BASELINE.md` §3.1）。
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
| A | 架构层面或十分复杂：新子系统、跨层状态机、跨进程/跨标签页一致性、首次组合多个执行接缝 | 写可执行 plan | 独占 worktree/branch |
| B | 边界清楚但需要强推理：既定合同下的安全相关实现、跨组件异步状态、非平凡算法 | 复杂时写 plan，其余列实现与验证步骤 | 独占 worktree/branch |
| C | 常规功能与修复 | 任务中列出实现与验证步骤 | 独占 worktree/branch |
| D | 机械活、说明文档、开发助手规则/skills、补测试、状态同步 | 不写 | 可在主工作树编辑，集成仍走任务分支 PR |

**高风险**：改变认证、CSRF、授权、脱敏、会话/租户/用户隔离、凭证语义或受保护数据的可见范围；不可逆的外部写入；DB schema 或迁移；Golden fixture 或 `FROZEN_GT_IDS`。高风险任务至少按 B 档施工。

| 档 | 普通 | 高风险 |
|---|---|---|
| A | 可执行 plan → 施工 → 跨模型静态 Review → 获准的真实流程验收 | 同左，针对风险路径取证 |
| B | 按复杂度选择 plan，施工后跨模型静态 Review 与实际验证 | 同左，针对风险路径取证；不自动加 Monitor |
| C | 自行核对 + 独立跨模型静态审核，实际验证按影响 | 不适用，遇到即升 B |
| D | 核对 diff、解析与真实结果；无独立审核 | 不适用，遇到即升 B |

- 各档核对 diff、文档冲突、必要静态检查及适用的实际结果；D 无独立审核。自审或 Monitor 仅按 Goal 明确要求执行，不因档位叠加窗口；静态 PASS 不代替真实验收。
- 人工停点：红线、扩域、新增或变更架构/框架/公共契约/API/协议/信任边界/核心不变量、重大未决选择、批次/里程碑验收。恢复已批准合同的修复按 Goal 推进；保持架构的内部 ports 变更不另设 Gate。
- 无 local-commit Gate；开棒说明合并授权，未明确则先完成准备，在合并处等待。

### 业务 Review 与明确要求的独立证据

Review 绑定准确候选与 scope，取证分工依据**具体事实能否静态判定**。Goal 明确要求额外自审、plan 评审或 Monitor 时，按该 Goal 的职责和顺序执行。

- 仅在 Goal 明确要求 Monitor 时，按授权风险路径选择取证；操作与输出参考 `docs/phase2/MONITOR_PROMPT_TEMPLATE.md`，模板不恢复默认五类套餐。不私改 Golden fixture / `FROZEN_GT_IDS`，不自动新增临时测试；必需证据取不到即报告缺项，书面推演不冒充实测。
- 静态审核（含桥与 OpenCode）只读代码、历史与合同，不运行测试、构建或写操作，越界即 BLOCKED。负责合同完整性、类型可达性、scope/diff、显式授权判断、文件落点、直接 import、声明与跨文件一致性；Monitor 不重复这些静态项，静态方不承担动态越权、门禁接线或动态 import/registry 取证。
- 生产改动由不同厂商模型交叉静态评审，组合见 DECISIONS 现役路由；Monitor 与静态方可同厂商，须独立会话。Review 聚焦可触发的缺陷、合同与接线，不因假想扩展或缺少非必需测试要求返工；静态 PASS 不声称运行通过。
- 同一 task_id 至多三轮监理（中止、无结论或改写重发均计轮次），第三轮仍非 PASS 即停手交雨爷裁决，不开第四轮。
- 合并前核对静态审核及适用的 Monitor PASS 均绑定最终 base/head；任一变化须重评。评审桥模型/effort 由主窗口维护，PR 如实记录 `observed_model`。

## Git 与永久任务记录

- 主分支 `phase0/main`；任务分支 `phase2/<task_id>`；commit 为 `phase2(<task_id>): <简述>`，merge 为 `merge phase2(<task_id>): <简述>`。
- A 档棒将纯格式化改动与功能改动分开提交；本条不增加 CI 检查。
- 集成：任务分支普通 push → PR → required checks 全绿 → 获准合并；不得本地合完直推主分支。验证、Review、候选 freshness 和分支保护均须满足，绿灯不是授权。合并后检查对应 merge SHA 的 GitHub Actions。
- 不建 per-task Task Record。合并前 PR body 写齐 `## Scope`、`## 验证结果`、`## 本棒新增欠债`；验证列实际命令、最小充分原始结果、未执行原因、候选 commit 与 CI run。欠债含 reason、blocked_by_task_id、activation_task_id、expiry_condition、evidence，无新增则写明。
- PR 合并前记录实际要求的静态 Review 结论、候选和产物位置；实际跑桥、获准使用 OpenCode 或替代评审方时，按需读取 `docs/phase2/REVIEW_EVIDENCE.md`，不据记录格式新增评审门禁。**不伪造桥字段，不把模型自述当服务端证明，不合并后补证。**

## 状态同步

- **A 类机械同步**只含基线数字、task_id、已决后继指针和本棒新欠债；不是 A 档风险分类。状态写 `docs/phase2/STATUS.md`，欠债写 `docs/phase2/PHASE2_PLAN.md`。
- 串行时由实现棒在同一个 payload commit 同步；不另开同步 commit、不 amend、不 force push。真正并行的多 write lane 不改共享状态/欠债文件，由独立 GOV-SYNC 批次同步。
- **B 类治理裁决**（跨棒裁决、推翻蓝图偏差的 ADR、Golden 策略、DAG 重排、跨棒欠债合并）无论串并行都归 GOV-SYNC。实现棒只传播已决且唯一的后继；不唯一则留空并登记待裁决。
- 治理文档不写 commit SHA / CI run id；证据留 PR，历史留 Git。状态保留当前结果、基线来源日期、阻塞与指针，不累积返修流水账，也不把旧数字标成当前实测。

## 验证策略

默认不新增自动化测试，以跨模型静态 Review 和获授权环境中的实际流程、结果、日志核对为主；已有测试只选受影响且有必要的部分。不按数量或覆盖率扩写测试，纯文档不启动应用。静态 Review 与日志不能证明运行正确或替代必要权限验证；环境不足则标记未实测，不拿 mock 或单测绿冒充真实验收。

新增/修复行为核对真实接线；运行验证记录操作、预期与实际结果。不要求每次修改都写测试；确需新增用例按当次明确验收要求执行，不因缺陷或风险标签自动追加。若用变异证明覆盖，保留测试只改生产路径；既有测试前后均绿只证明兼容。格式/链接/解析检查不要求变异，一次性检查不默认提交。

完成适用检查后即交付；仅因新改动、失败或具体未决风险重跑或扩大验证，不为安心反复检查。

- **说明文档**：diff 检查、受影响链接/路径/标题/术语与决定冲突检查、未跟踪文件清点；不跑无关 pytest、Golden 或端口测试。
- **机器消费文档与 skills**：增加对应解析器、skill validator 或最窄合同/加载检查；生成器会写文件时，只在已授权输出范围执行。
- **仅测试**：按当次明确任务选择对应检查，不弱化断言；不因测试文件变化启动本地全量。
- **普通生产改动**：覆盖改变的行为与必要失败路径，按问题选择实际操作或已有定向测试；涉及接缝检查真实接入。lint / typecheck / build 按实际影响选择，格式化和自动修复只处理授权文件。
- 依赖、manifest、lockfile、allowlist 与 import/registry/六边形边界变化做必要的依赖和边界静态核对；测试、Golden、架构回归按 required CI 或明确验证要求执行，不据主题自动新增本地套餐。
- 前端流程见 `.agents/skills/frontend-task/SKILL.md`；后端流程见 `.agents/skills/backend-task/SKILL.md`。入口按需读，不额外规定固定全量套餐。

### 本地测试与 required CI

默认不跑本地全量，高风险、核心文件、广泛重构或收口阶段本身不触发；只有雨爷明确要求本地全量才执行。现有 required CI、分支保护和明确验收标准照常满足，CI 全量不在本地默认重复。

环境前置见 `.agents/skills/backend-task/SKILL.md`；缺项说明事实，由雨爷决定补环境或是否使用 Mock，不冒称正式环境通过，不解除 required CI。不改凭证或连接目标换绿，不静默跳过、弱化断言、吞错或重试掩盖失败。

下列测试命令仅是 required CI 或当次明确验证的入口，不构成默认本地运行要求。

| 命令 | 使用时机 |
|---|---|
| `uv run python scripts/check_dev_environment.py --start-full-tests` | 雨爷明确要求本地全量；读取最终后台结果，日志/状态在 `_scratch/` |
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
