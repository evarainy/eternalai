# Phase 2 Decisions

这里只收**现在仍然有效**的架构、产品与治理裁决，按日期排列，新裁决追加在末尾。已失效、已执行完的一次性内容和每条的完整原文在 [archive/DECISIONS_full_to_20261119.md](archive/DECISIONS_full_to_20261119.md)（标题与本文件一致，按标题搜索即可）。本文件与代码、测试不一致时，先核实再改，不要以旧原文为准。

## 2026-07-24 — P2-AUTH-001 `cryptography` runtime dependency
## 2026-07-27 — P2-PILOT-FOUNDATION-001 `uvicorn` runtime dependency

已批准运行时依赖 `cryptography`（OA 登录 RSA 加密）和 `uvicorn`（仅 ASGI 进程启动），登记于 `docs/dev/dependency_policy.md`。

## 2026-08-03 — 决定一：写操作凭证与问责

写操作用用户本人的 OA 凭证执行；每次写操作须用户在本系统点确认，确认留痕。

## 2026-08-03 — 决定二：数据库访问边界

本系统数据库不算目标系统：不进外部系统名册、不做 IdentityMapping、不加 `db` 枚举值；AI 每次访问数据库都须在 Trace 可查。

## 2026-08-03 / 2026-08-12 修订 — 决定三：企业级密钥

企业级密钥由运维负责，不定期轮换，改配置文件手工更新，不建管理页面。

## 2026-08-03 — 决定四：Golden 策略（逐字）

负向、边界和安全拒绝用例的题面、预期、禁止项、分类及判卷契约冻结，修改需雨爷明确批准。所有既有正向题面同样不可原地改写，只能新增后继题并在题外生命周期清单中停止旧题运行。判卷契约或运行选择规则变更时，必须按同一版本包全量回放并明确披露影响。每修复一个真实缺陷，必须新增一条能在未修代码上失败、修复后通过、且走原缺陷路径的永久回归证据；缺陷属于 Golden Runtime 观察边界时才新增 Golden Task，否则放在最小且忠实的单元/集成/API/浏览器层。

其中“每修复一个真实缺陷必须新增永久回归”已由 [2026-10-21 精简开发约束](#2026-10-21--精简开发约束与按影响验证) 替代；冻结与判卷合同继续有效。

## 2026-08-17 — 蓝图原则修订：企业控制内核自有，通用执行内核复用
## 2026-08-17 — 蓝图优化的落地顺序（四步走）
## 2026-08-19 — 裁决：两个执行内核接缝从现有实现提取，不等第三方框架

- 企业控制内核（Gateway / Policy / Identity / Secret / Trace / Evidence）必须自有；通用执行内核（agent loop、上下文、checkpoint、沙箱、子 Agent）复用成熟组件并保持可替换（覆盖蓝图 L2435 / L2613）。
- 先有接缝再装框架，框架类型不得进入业务代码和端口签名。
- `WorkflowEnginePort` 只管可恢复的流程执行，`AgentOrchestrationPort` 只管代理循环与工具编排，两者不互为别名。

## 2026-08-17 — DeepSeek Harness：不引入运行时插件容器
## 2026-08-18 — 插槽式模块边界：可替换，但不引入无特权核心的插件容器
## 2026-08-18 — DeepSeek Harness 评估结项：经验证不适合，不再重开

- 不用运行时插件容器（动态加载第三方代码、热插拔、无特权核心）；DeepSeek Harness 不再重开。
- 进入模型请求的内容应能从持久日志完整重建（先核实现有 Trace，未核实不排期）；能力对模型的可见面只走单一 allowlist 投影。
- 模块可做成插槽，但只能经稳定契约、静态注册表和 allowlist 在构建或部署时装配；插槽不得绕过 Identity、Policy、Gateway、Evaluator、Trace、Evidence，不得扩大读取范围、自创动作或直接操作目标系统。

## 2026-08-18 — 产品定位：AI 工作操作系统与三种工作表面

- EternalAI 是建在现有业务系统之上的 AI 工作操作系统，把各类输入变成受治理的工作对象，投射到 IM（人与人）、AI 共事界面（人与 AI）、工作台（人与工作）三个表面，三者身份、上下文、状态和审计贯通。
- 消息以 IM 为准，业务状态以 OA 等源系统为准；Work Object 只管跨系统编排状态，不做第二套业务真相。

## 2026-08-18 — 聊天事件候选闸门：AI 止于候选，执行责任归人

- AI 从聊天里最多只能生成 Work Candidate / 待办候选；转正式 Work Object、启动流程或外部写操作都须人明确确认，执行仍走 Gateway → Policy → Adapter → Evaluator → Trace。
- 领导发言、`@`、表情、已读、「好的/收到」都不算授权；责任归确认人，Trace 记确认人、时间、候选版本、来源消息、补充字段、结果和后续 Work Object。
- 聊天原文不得灌入全局知识库或无边界 RAG；只能在显式触发且 Policy 允许的范围内读取当前消息、引用链及必要的有限上下文。

## 2026-08-18 — 聊天只做卡片路由，不承载自由文本执行
## 2026-08-18 — 卡片动作的授权边界：引用不传递权限

- 聊天可做只读查询和生成草稿；正式写入须本人确认卡；普通聊天和引文不算授权（已由 2026-11-04「IM 消息功能机会层…」第 4 条放宽）。
- 卡片按钮只做查看、接受/驳回/稍后处理候选、打开工作台页面、签收订阅跳转，不得直接调业务 Adapter；在工作台核对后点「确认执行 / 提交」才算授权；「驳回候选」不等于驳回源事项。
- 卡片动作用 `action_id`、对象引用、版本号和幂等键，消息里不放 Tool 名、任意 URL 或可执行参数。
- 授权只认服务端签发、绑定 `(tenant, principal, work_object, card_version)` 的不透明引用；转发、复制、截图不传递权限；每次动作重新授权，主体不符即拒绝留痕；明文业务 ID 不作授权依据。

## 2026-08-18 — 工作台向聊天反向推送：把注意力逻辑带回沟通现场

- 工作台可向 IM 推提醒卡（将超时、逾期、待确认等），只给摘要和「去处理」按钮，不在聊天里办业务；每张卡对应唯一 Work Object，状态变了更新原卡。
- 默认私发责任人，制度或升级规则允许才推群；不得由模型公开点名排名；推送范围、频率、升级路径须可配置、可审计。

## 2026-08-18 — Ant Design 6 卡片复用与多 Surface 渲染
## 2026-08-18 — 前端技术栈与多 Surface 渲染的衔接

- Web 基线：React 18 + Vite + antd 6 + `@ant-design/icons` 6 + `@rjsf/antd` 6.7.x + `@ant-design/x` 2。`@ant-design/x` 做 AI 对话壳（工作台首版 Dock 不用），RJSF 做结构化表单。
- 同一卡片语义在聊天、工作台、事项工作室三档密度复用，渲染分档实现；RJSF 紧凑密度未验证。
- 卡片协议权威源是自有 SDUI / Work Card Schema，与框架无关；IM 消息只带协议版本、对象引用和允许动作。
- secure context 两条路径（内部 CA、Chrome 企业策略）未内网实测。

## 2026-08-18 — 当前前端与通信技术路线
## 2026-08-18 — 通信内核的当前定性：初步筛查，未定选型

- 自有 antd 6 Web 外壳，经自有 Communication Adapter 接通信产品；工作对象、身份、权限、Policy、卡片协议和执行链归 EternalAI。
- IM 选型见 2026-11-04「IM 消息功能机会层…」，客户端见 2026-11-04「允许成熟壳客户端…」，分别替代 JuggleIM 与「不做客户端」口径。
- 候选闸门、卡片路由、反向推送三条边界与通信产品无关，继续有效。

## 2026-08-18 — 本地 capability provider 的边界澄清
## 2026-08-18 — 部署目标平台确认：ARM 架构银河麒麟 V10

- 内网终端是 ARM 银河麒麟 V10，Chrome 101。
- 可用本地 capability provider：无浏览器内核、无 UI，经 `localhost` 供 Web 调用，只做浏览器做不到的事（Office COM、扫描仪、老软件、后台任务），麒麟侧用 Python 3。

## 2026-08-18 — 裁决：Ant Design 6 采用「移除 ProComponents 后独立升级」
## 2026-08-20 — 裁决：前端视觉基线（Pro 风格用原生复刻，装 Ant Design X，默认蓝）

- 不装 ProComponents，Pro 布局用 antd 6 原生组件复刻；3.x 进入 `latest` 且 `#9656`/`#9661` 关闭后再评估。
- 装 `@ant-design/x`；`colorPrimary` 用默认蓝。视觉方向现按 2026-11-13「视觉方向改为「墨、纸、印」」。

## 2026-08-18 — 裁决：蓝图中限定 Phase 1 的技术禁令不自动继承到 Phase 2

蓝图中限定 Phase 1 的技术禁令不适用 Phase 2；§12.5 其余条款仍有效，尤其第 9 条：所有组件服从 Gateway / Policy / SecretProviderPort / Trace Redaction 安全边界。

## 2026-08-18 — 架构说明书 v0.2 的地位：停止修订
## 2026-08-18 — 蓝图对照冲突清单：五条 🟡 的前提已失效，裁决前必须重述

- `_scratch/` 下架构说明书 v0.2 和前端体验架构草案已停用，改引本文件。
- 冲突清单 Y-01、Y-03、Y-04、Y-05、Y-08 须先把主语改为「待选通用执行内核」再裁。
- 跨文档对照须同时输入冻结蓝图和 `docs/phase2/DECISIONS.md`。

## 2026-08-18 — 前端 ADR 草案不进仓库，硬约束就地固化

前端决定一律写本文件，版本区间写 `docs/dev/dependency_policy.md`。硬约束：

1. React 固定 18.x，按 React 19 兼容写。
2. `@rjsf/antd`、`@rjsf/core`、`@rjsf/utils`、`@rjsf/validator-ajv8` 同在 6.7.1 版本线。
3. 不得用 `overrides` / `resolutions` / 强制 dedupe 改写 RJSF 的 `react-is` 解析树。
4. RJSF `defaultProps.options` 合并与 forwardRef widget 分支升级后不得退化（是否新增测试按 2026-10-21）。
5. 协议包用语言中立契约，不依赖 React、antd 或渲染器，可无 DOM 校验；渲染器只消费其 TypeScript 产物。
6. React 升主版本、antd 落回 `≤6.3.5`、`@rjsf/*` 离开 6.7.x 或改写 `react-is` 时须重跑 RJSF PoC。

## 2026-08-18 — `P2-PILOT-OPS-B-001` / `C-001` 并进工作台，不单独交付管理面
## 2026-08-19 — 裁决：`P2-PILOT-OPS-001` 拆解与工作台 DAG 重排

- 管理员视角是工作台里的角色视图，用同一套 Policy 与 Identity，不另建权限体系。
- 旧决定不回改，变化写新记录替代。
- 共享查询层等第二个真实列表出现再抽；不做服务端分页（排序已由 2026-09-20「D-6 有界列表排序的有限替换」放开）。

## 2026-08-18 — 「候选」正名：`WorkCandidate` 与 `SkillCandidate` 不得简称
## 2026-08-18 修订 — 「候选」正名的适用面收窄

- `WorkCandidate`（AI 提取的工作，人确认后转 Work Object）与 `SkillCandidate`（Skill 提案，不能自动发布或执行）必须区分，新的候选类也须带限定词。
- 代码标识符一律全称；文档每条内首次出现和标题、跨文档引用用全称，之后可简称；「候选方案」这类普通词不管。

## 2026-08-18 — Skill 候选来源语义：只允许人工登记

- `SkillCandidate` 只能人工登记，系统不自动提议；普通用户提建议，由管理员转正，管理员是最终负责人。
- 须附记录编号、反馈、已登记能力、业务说明；去重由人最终决定；被拒 3 天后可再提。
- 业务内容内网不脱敏，但凭证、token、cookie、session 标识一律不得出现。

## 2026-08-19 — 裁决：Work Object 首棒走 OA 直通，`WorkCandidate` 留位不实现
## 2026-08-19 — 裁决：不为已成立的正式事项设「确认它是不是工作」这一步

- OA 待办等源系统已成立的事项直接生成 Work Object，不经 `WorkCandidate`；AI 从非结构化内容提取的才走 `WorkCandidate`。
- 不为已成立事项设「确认是不是工作」步骤；确认只用于改变外部状态、补信息、变更责任或权限、裁决 AI 不确定的提取。用户几乎总选同一答案的确认应删掉。
- Work Object 只含来源、责任人、时限、状态、确认记录，其他只存 ID；`TaskRecord` 仍是一次执行记录。

## 2026-08-19 — 裁决：`HumanGatePort` 独立，`CredentialBindingPort` 不新建
## 2026-08-19 — 裁决：版本绑定推广既有 Workflow 锁定模式，并前置低风险写入

- 不建 `CredentialBindingPort`，凭证绑定由 `IdentityMappingPort` 和 `app/ports/auth.py` 承载。
- `HumanGatePort` 的请求由服务端生成并持久化，绑定主体、动作 digest、版本、有效期和请求 ID；动作一变旧确认即失效。
- 一次 Task 的确认、恢复、执行校验同一份版本清单（优先存 digest），缺失或不一致即停止，不得按 v1 确认、执行 v2。

## 2026-08-19 — 裁决：Work Object 与 OA 的状态同步策略

业务状态以 OA 为准，本系统只存上次看到的样子和自己的处理痕迹。

1. 存 OA 字段快照、拉取时间和本系统处理痕迹。
2. 后台轮询、打开工作台、手动刷新三种方式同步。
3. 界面显示上次拉到的状态并始终显示数据截至时间，不显示自行推算的状态。
4. 用户标已办而 OA 未变时为 `待同步完成情况`，不算完成。
5. 同步失败显示旧数据加醒目提示，凭证失效引导重新绑定。

另设「已在别处处理」出口，只影响本系统展示，不抹掉 OA 在办事项。

## 2026-08-19 — 凭证模型变更：改为保存用户 OA 密码并支持后台定时轮询（过渡方案）

- 保存用户本人账号密码，供离线自动登录和定时拉取，适用所有目标系统（见 2026-08-20「澄清：凭证模型适用于所有目标系统…」）。原「不存密码」「不静默重登」作废，仍不用共享服务账号。
- 写操作仍须用户本人点确认，这是问责的唯一支点。

**过渡方案**：将来改用泛微官方应用 Token，凭证获取须留接缝，不得把密码登录写死在业务逻辑里。

**硬约束**：

1. 密码加密存储、密钥分离（`SecretProviderPort` + AES-256-GCM），明文不得进 Trace、ResponseEnvelope、日志、fixture、报告或错误信息。
2. 密码错误一次即标失效、停轮询、提示重绑，不重试。
3. 遇验证码即停轮询告警，不绕过。
4. 轮询限频（初值每用户 10 分钟）、仅工作时段、失败退避、全局并发上限。
5. 凭证失效不自动换账号。

## 2026-08-19 — P2 收口标准修订：必达 / 机会 / 排除三层

- **必达**（全部完成即收口）：① OA 只读全链（已完成）；② Work Object + 最小工作台（`P2-WORK-OBJECT-001`）；③ 后台轮询（`P2-OA-CREDENTIAL-POLL-001`）；④ 一个获批低风险写入（`P2-LOW-RISK-WRITE-001`）；⑤ 覆盖上述路径的 Golden（`P2-GOLDEN-001`，口径见 2026-08-21「Golden 拆两棒…」与 2026-08-28「必达项 5 的完成判据口径…」）。
- **机会**（不阻塞收口）：`SkillCandidate` 池、Memory、海康 Adapter 及后续补入项。
- **排除**：IM 机会层首切以外的 IM 与 `WorkCandidate` 链路；多密度渲染；Memory 后四层；Skill CI/CD 全生命周期；另写原生业务客户端（成熟薄壳允许）；知识库；DB Gateway。「装第三方执行内核」已由 2026-10-27「AgentScope Runtime 选型…」替代。
- 「顺便做 X」先对照排除清单。

## 2026-08-19 — 裁决：首个低风险写入定为 OA 待办事项审批同意
## 2026-08-19 — 裁决：确认卡展示真实业务内容，不设字段白名单

- 首个写入是 OA 待办「审批同意」；只能用操作者本人的真实待办；确认卡即授权，不叠加审批层；确认即生效，本系统不能撤销。
- 确认卡须写明提交后不能自行撤销；未确认不执行、不重复提交、确认与提交内容不一致即拒绝，Golden 须覆盖这三条。
- 确认卡展示姓名、标题、正文、附件名（默认只显示姓名与标题），不设展示字段白名单，但预览只能由服务端按权威绑定从 OA / Work Object 补取；用户或 LLM 传入的参数值仍走 allowlist（见 2026-08-28「参数值 allowlist…」）。
- 凭证字段（`sessionkey`、`datakey`、`password`、`token`、`cookie`、`sessionid` 等）不得进入 ResponseEnvelope、Trace、日志或 fixture。

## 2026-08-19 — 裁决：DB Gateway 移出 P2
## 2026-08-19 — 裁决：知识库不进 P2；Memory 边界收窄

- DB Gateway 和知识库都不进 P2（知识库放 P3，与 Memory 是两回事）。
- Memory 来源限于用户自编、聊天、办理记录；身份证号、银行账号、家庭住址等敏感信息和密码不得进入；部门之间隔离。

## 2026-08-19 — 裁决：第二个真实 Adapter 定为海康监控

- 第二个 Adapter 是海康监控（机会项），用例、版本、接入方式、环境、凭证形态、设备与区域范围到齐才开工。
- 海康是部门共享账号，设备与区域范围须由 Policy Guard 控制。

## 2026-08-20 — 裁决：每个前端棒的最低体验要求（三条）

1. 取数页面都要有加载中、空、出错三态，出错不得显示成空列表。
2. 数据截至时间、逾期、同步失败等关键信息不折叠。
3. 不用固定像素宽度堵死布局。

## 2026-08-20 — 裁决：HAR 清洗脚本进仓库，被清洗的 HAR 素材不进

HAR 清洗脚本进 `scripts/`，独立实现，不挂靠 `scripts/sanitize_oa_contract_pack.py`（那不是 HAR 清洗器）；HAR 文件绝不进仓库。

## 2026-08-20 — 并行 lane 的测试库约束

- 并行时同一时刻只有一条 lane 可新增 migration 并用共享测试库，其余用独立库。
- 冲突时先等该 lane 合入；另建独立库须雨爷专项授权，只限新建空库并跑 migration。
- 开棒前写明是否新增 migration 和测试库归属。
- 严禁对共享库 reset / downgrade / stamp（唯一例外：2026-11-19 常设授权第 4 项的固定测试库迁移版本回退），不得改仓库内数据库配置文件。

## 2026-08-20 — 裁决：办理按钮按自动化程度择一显示

- 每个待办按 `CapabilitySpec` 声明的自动化程度只显示一个按钮：「AI 办理」「AI 辅助」或「回原系统办」；都不绕过人工确认门。
- 未声明时一律显示「回原系统办」。

## 2026-08-20 — 澄清：凭证模型适用于所有目标系统，不只 OA

- 凭证按「每用户 × 每目标系统」一套，以 `(用户, 目标系统)` 为键，不按单系统建表。
- 各系统凭证分别绑定、更新、解绑，一个失效不影响其他。
- 2026-08-19 五条硬约束（加密存储且密钥分离、密码错一次即持久失效、遇验证码停轮询并告警、轮询限频退避、不自动切换账号）逐系统适用。

## 2026-08-20 — 事实确认：OA 中每个用户只能看到自己的待办

- 用户在 OA 只能看到自己的待办；部门级看板不能用 OA 数据做，只能用本系统派发的任务。
- 要从 OA 取部门级数据，只能用本人已有的部门权限；引入有部门权限的账号与「不用共享服务账号」冲突，须单独裁决。
- 后续 Adapter（如 `P2-HIKVISION-ADAPTER-001`）的可见范围也以用户自身权限为准；与 PLAN 外部输入中的「部门级共享账号」待核。

## 2026-08-20 — 裁决：任务派发方案归机会层，拆为两棒

任务派发属机会层；拆棒与目录范围已由 2026-08-21「任务派发拆四棒」、2026-08-30「组织目录不承担授权判定，权限由 OA 自身执行」替代，现役规则见 `docs/phase2/WORK_DISPATCH.md`。仍有效：

- 派发的任务、备忘、提醒都是 Work Object，不建第二套任务对象。
- `state_authority` 为 `external_snapshot`（权威在 OA，本地不改）或 `internal`（权威在本系统），各层防止两者互相污染。
- 人工派发直接建内部 Work Object（不经 `WorkCandidate`、不走 Human Gate）；OA 待办直接同步；AI 推断的事项走 `WorkCandidate` → 人工确认。
- 既有 OA 行不搬表、不重写；将来拆表搬迁须重新专项授权。
- 组织目录信息不完整时停止并报告，不做猜测式映射。

## 2026-08-20 — 裁决：Excel / HR 批量映射导入划入 P3，并修正 IN 段的失真表述

- Excel / HR 批量映射导入归 P3。
- 拆解或合并任何棒时，原交付的每一项都要写明去向：由哪个 task_id 承接、推迟到哪个阶段，或取消。

## 2026-08-21 — 裁决：Golden 拆两棒，解开与低风险写入的 DAG 循环
## 2026-08-27 — 裁决：Golden fixture 的人工批准留痕
## 2026-08-28 — 裁决：必达项 5 的完成判据口径（Golden 只覆盖 Runtime 观察边界）

- `P2-GOLDEN-002` 在低风险写入落地后增量冻写入路径，不改 `P2-GOLDEN-001` 已冻题面；必达项 5 要两棒都完成。
- Golden 只覆盖 Runtime 观察边界；工作台、隔离、审计归 API / 单元层，覆盖不得弱化。不得为此新增第二判卷面；要重开须先拿出 API / 单元层保护不足的真实缺陷。
- 两棒已获批：可新增 GT 题面并把新题加入 `FROZEN_GT_IDS`；不得改删现有题面的 expected，不得从 `FROZEN_GT_IDS` 移除条目。必须改现有题面时停手，单独申请。
- 负向与边界路径 100% 通过，含 GT-012；下游 descriptor 存在不释放依赖门。

## 2026-08-21 — 裁决：门禁覆盖性写入验证策略

- 跑门禁要证明它覆盖了本次改动，重跑不覆盖的门禁不算数；证明方法按 2026-09-07「开发助手治理与 skills 收敛（P2-GOV-SYNC-ASTRA-001）」的覆盖性修正。
- 不借此扩成全量套餐，不得改题换绿。

## 2026-08-21 — 裁决：个人备忘的隐私边界，管理员不得读取他人备忘

- admin 不得读取他人个人备忘/提醒的正文和附件，只有本人可看可操作；越权返回 404，不泄露对象是否存在。

## 2026-08-27 — 裁决：部门层级在授权中的作用（上级可看下级，可跨层派发）

- 「上级主负责人递归查看、跨层派发」已由 2026-09-08「内部任务派发的三轴授权模型」与 2026-10-18「任务派发业务规则确认（待技术落地）」替代；默认可见范围按 2026-10-18 该条第 6 条。
- 仍有效：同级部门互不可见；下级不能查看上级；非主负责人不得跨部门查看。
- 部门树成环时报错，不得静默截断；越权拒绝要记审计。

## 2026-08-27 — 裁决：双权威 migration 的可逆性边界（downgrade 删内部数据，但须显式守卫）

- 该 migration 的 downgrade 可删除 internal 行，授权只限这条路径。
- 只有设置 `ALLOW_DESTRUCTIVE_DOWNGRADE=1` 才删除；未设置且有 internal 行时报错退出、数据不动，错误信息写明「表中存在内部任务数据，结构回滚将删除它们；确认后设置 ALLOW_DESTRUCTIVE_DOWNGRADE=1 重试」。空表或只有 OA 行时直接成功。

## 2026-08-27 — 裁决：前端信息架构与终态导航（原 13 项，经同日后续裁定修订）

初版五项导航、`/sessions` 会话页和 `/ai/sessions/:id` 冷启动不得引用。

### 一、终态导航：四项一级平铺；会话降为筛选视图

- 导航项与筛选已由 2026-09-02「一级导航改为五项，工作事项筛选改为三状态互斥，顶栏取消当前位置」替代。
- 一级平铺；超过 6 项才分组；不设单项分组；管理员页面放折叠二级。
- 一件事只在一个列表里：会话产出 Work Object 后离开「我问过的」。「我问过的」依赖会话持久化，归 P3。

### 二、落地页 = `ChatPage`，AI 不占菜单位

- 落地页是 `ChatPage`；现行路由为 `/chat`，`/` 重定向过去。Logo 不可点、AI 助手为一级首项、浮动入口必须有（2026-09-02 同条）。
- 落地页起新事，Dock 处理当前页的事；Dock 须能「新建通用会话」（不带 `work_object_refs`）。
- 「最近问过的」归 P3，P2 不做刷新即丢的历史入口。

### 三、软件中心、消息与外部系统工作区

- 软件中心 `/apps` 分业务系统、单位软件、我的功能。只展示 `name`、`short_description`、`owner`、`risk_level`、`target_system`、`binding_required`、`status`、`version`，不展示技术字段。
- 「用」走 `input_schema` 生成的 RJSF 表单，`binding_required=true` 先引导绑定；「新建应用」前端先行、后端不做（2026-09-02 同名裁决）。`/admin/registry` 与软件中心不合并。
- 前端不二次选能力。旧路径按 `Workflow > Published Skill > Single Tool > no_capability_found`；AgentScope 新路径按 2026-10-27「AgentScope Runtime 选型、M1 工具组替代 Top-K 与运行时工具协议」的工具组选择。
- 消息：AI 识别出的工作先成 `WorkCandidate`，人工确认后才成 Work Object；聊天可做只读查询和草稿，正式写入须本人确认卡（2026-11-04「IM 消息功能机会层、选型更新与聊天交互边界放宽」）；工作台状态变化用状态卡推回原会话。
- 外部系统不占导航位：有 Adapter / Workflow 走原生工作台，允许 iframe 的可内嵌，否则新窗口并保留来源引用。OA 固定新窗口。

### 四、AppShell、顶栏、搜索与空状态

- 左导航（形态按 2026-11-13：76px 窄栏）+ 主内容；Dock 是跨路由单例浮层，不改主内容宽度。不写死页面宽度。
- 顶栏系统状态与通知分开计数，凭证失效时系统状态显示红点和数字并可重新绑定；「部门 / 姓名」只读，不做全局选择器；顶栏 AI 入口只打开 Dock。
- P2 搜索只搜 Work Object 的标题、来源编号、责任人，授权范围与列表相同；不做 AI 补全、语义改写、「猜你想问」；搜索框与 AI 输入框分开。
- 空状态、失败态、降级态都要用人话说明原因并给出下一步。

### 五、工作事项列表、工作室与办理按钮

- 主行四列 `标题 / 责任人或责任部门 / 截止时间 / 下一动作`；灰色副行 `来源系统 · 来源编号 · 当前步骤 · 数据截至时间` 常驻，不靠 hover。Trace、阻塞说明、附件只在工作室。
- 用动作集和 `OA` / `内部` 徽标区分来源，不露内部词；外部快照只有「查看 / 去 OA 办」。AI 结果须用户显式保存才共享。
- 只显示一个办理按钮：`让 AI 先写` / `我自己办` / `去 OA 办` / `先看看`；未确认角标 `AI 写的，还没确认`。Dock `pinned` 时列表切紧凑双行。
- `CapabilitySpec` 自动化程度为 `full` / `assisted` / `manual`，未声明按 `manual`；按钮由自动化程度、办理映射、当前步骤和来源权威确定，未知组合不得当作可自动办理。

### 六、AI Dock、会话阶段边界与页面上下文安全合同

- P2 不扩 `sessions` 表、不建会话 API、不做会话列表和 `/ai/sessions/:id`，冷启动恢复归 P3。
- Dock 三态 `closed` / `drawer` / `pinned`，界面只有开/关，都不改主内容宽度。展示模式和临时会话放非持久化 Zustand，跳转不换 `sessionId`。
- 页面上下文固定九字段：`surface_id`、`organization_scope`、`work_object_refs`、`source_refs`、`filters`、`selected_metric`、`allowed_capabilities`、`freshness`、`visibility`。
- 上下文不放整个 DOM、隐藏行、别页缓存或不可见字段；切页提示「上下文已切换」；续旧会话只带用户确认的摘要与引用；跨部门或数据范围默认新会话。

### 七、任务交办

- 单一入口：自然语言输入后同页展开预填表单，也可直接填；不做并列入口。
- 发布前固定展示九类字段：类型、标题、责任人或责任部门、截止时间、办理要求与交付物、回执要求、提醒策略、可见范围、批量目标解析与去重结果（闭集见 2026-09-02「任务交办界面的控件闭集、附件字段与主动作按钮样式」第 2 条；责任人与可见范围为只读摘要，见 2026-09-10「前端交办接线方案的六项未决点」第 4 条）。
- AI 草稿不是发布，发布必须由人显式操作。

### 八、前端目录、状态与 API 边界

- 目录：`app/`（壳与路由）、`features/`（页面编排，feature 间不互相 import）、`entities/`（领域展示）、`shared/`（不反向依赖 feature）、`contracts/`（语言中立协议，不依赖渲染器）、`generated/`（Orval 生成，禁止手改）。
- 新页面代码进 `features/<name>/`，**不得再进入扁平 `components/` / `pages/`**；老代码原地保留。
- 守卫：`contracts/**` 不 import `react` / `antd` / `@ant-design/*`；`shared/**` 不 import `features/**`；`features/A/**` 不 import `features/B/**`。
- 服务端状态归 TanStack Query，界面本地状态归 Zustand，可分享的筛选与视图归 URL；不把 Work Object 或会话历史复制进 Zustand。
- 业务 API 用 OpenAPI + Orval，组件里不直接写 `fetch` / `axios`；不从自然语言推断授权，前端只消费后端授权结果。

### 九、命名、状态色与现役文案

- 前台叫「功能」（`Skill` / `Capability`）、「功能草稿」（`SkillDraft`）、「待审核功能」（`SkillCandidate`，只能人主动登记）、「软件」（`App`）；前台不出现内部名和技术字段。
- 状态五色：蓝 = 当前/AI，绿 = 完成/可用，黄 = 临期/待确认，红 = 失败/越权/过期，灰 = 未开始/不可用；必须文字加图标。

### 十、任务承载

- `P2-WO-SEARCH-001` 注册搜索命中集时须使用页面上下文合同。

## 2026-08-27 — 裁决：低数字素养用户的界面硬约束

对所有前端棒生效：用用户熟悉的界面语言，不削减功能。放大字号、增补说明类约束已由 2026-09-04「定稿画板…」废止；字号与行高按现行设计稿（2026-11-13「墨、纸、印」）。

### 一、有规范原文支撑的（强）

- 文本对比度 ≥4.5:1，大文本 3:1；示例文字不豁免。
- 点击目标 ≥44×44 CSS px，桌面不放宽。
- 中文连续长文每行 ≤40 字。
- 结构化表单字段必须有可见标签，不用 placeholder 代替；自由文本输入框除外（2026-09-07「自由输入框不设可见标签，「禁止 placeholder 代替可见标签」在此处不适用」）。
- 高后果提交须可逆、自动检查或提交前复核；派发任务给他人用「提交前逐项可改的复核 + 发布后短时撤销」，不能只靠「确定 / 取消」框。
- 验收含工信部 2021 要求：键盘可全程操作、可放大、焦点明显、控件有用途说明、告知位置与状态、可撤销上一步。

### 二、有证据但需限定的（中）

- 图标配文字；人人要看的内容不放折叠区；大输入框配明显提交按钮和示例。

### 三、明确没有依据的（不得当作依据引用）

- 「7±2 个选项」、三次点击法则、「低素养用户很少滚动」、桌面中文正文最低 px，以及另外 13 项未查到依据的问题（导航层级阈值、同屏选项上限、字间距、空白输入框恐惧、AI 接受度等），都不得作为设计依据；要用须重新调研并给出处。

### 四、一条对本项目有利的规范澄清

- 需要快速切换任务的政府内部服务可以合并页面，任务交办单页顺序式有此依据。

## 2026-08-27 — 裁决：cursor 分页合同移出 MODEL-001，该棒解除对组织目录的依赖

- cursor 分页记为欠债，`P2-INTERNAL-WO-SCOPE-001` 完成后重评；现在列表用有界批次。
- `msw` 不在 npm allowlist，不得直接安装；前端测试用 `vi.mock` 即可。
- 前端正式合同必须来自 FastAPI 导出的 OpenAPI，不得手写私有合同；前端组件完成不等于整棒完成。

## 2026-08-27 — 裁决：工作台首版 AI Dock 不使用 `@ant-design/x`

- 已执行完；`@ant-design/x` 现已安装，本条只剩历史意义。

## 2026-08-27 — 裁决：落地页目标态形态
## 2026-08-27 — 裁决：聊天附件与 Work Object 附件的边界

- 落地页目标态（可切换历史会话、对话中传附件）归 P3；会话持久化 schema 须按「会话切换 + 附件绑定」设计。
- 附件存储层（两阶段上传、类型与 ZIP 校验、配额、私有存储）聊天和 Work Object 共用；授权主体是参数，不得把 Work Object 写死进存储接口或做成非空外键。

## 2026-08-27 — 裁决：外部系统接入、录制路径与任务交办插槽应用

### 一、OA 页面内嵌 iframe 实测排除

- OA 不能 iframe 内嵌，不剥除其 `X-Frame-Options`，固定新窗口；其他系统另测。

### 二、「录制成功能」抓网络请求，不读 DOM

- 录制抓网络请求，不记 DOM；有正式接口后升级为 Adapter / Capability / Workflow。

### 三、录制的安全边界

- 每条都要有测试：不把 DOM、隐藏字段、Cookie 交给模型；不记录密码、Token、Session、验证码、原始按键；只记动作，不记个人数据；页面文字不得当 AI 指令；录制结果只是草稿，不得直接发布或执行；页面变化即暂停，不猜着点。
- 用户主动录制生成 `SkillDraft`，再主动提交审核才进 `SkillCandidate`。

### 四、录制前置空白

- Local Worker 与浏览器扩展的信任边界未定前，不得把本地抓包或浏览器扩展当已定设计。

### 五、任务交办是既有插槽式模块边界的具体应用

- 任务交办按稳定合同独立开发、构建时装配，不用运行时插件容器。
- 交办软件管表单、草稿、对象解析、提醒与预览；核心管授权、Work Object 事实、最终权限判定、Policy / Gateway / Trace、幂等与隔离。
- 交办软件只能经 Work Object Command Port 创建 Work Object，不得自建任务库、直写数据库或绕过身份、授权与审计链。

## 2026-08-28 — 裁决：参数值 allowlist 与「不设字段白名单」分层共存，互不推翻

- 用户或 LLM 传入的参数值（`ui.payload.displayed_argument_values`）走 allowlist，默认空集，须在 `CapabilitySpec.displayable_argument_fields` 声明。
- 服务端按权威绑定从 OA / Work Object 补取的业务对象预览不设白名单。两侧都做凭证扫描。

## 2026-08-28 — 裁决：插入 `P2-USER-ACTION-SEAM-001`，结构化 `UserAction` 先于 Renderer

**`P2-USER-ACTION-SEAM-001` 六项硬要求**：

1. 按钮只提交结构化 `UserAction`，不合成也不解析自由文本；
2. 服务端按 Principal、session、`response_id` 查 pending，只执行服务端保存的请求，不信卡片回传参数；
3. 校验 action / request / binding manifest digest，并一次性原子 claim；
4. 缺失、过期、不匹配、重复点击一律明确失败，不跌回意图路由；
5. 仍经 Runtime、Gateway、Policy、Evaluator、Trace，所有点击与确认进 Trace；
6. 原有的手打「确认」入口继续可用。

- 业务预览键归 `P2-LOW-RISK-WRITE-001`，在此之前 `ConfirmCardPayload` 五键不变。

## 2026-08-28 — 裁决：`P2-USER-ACTION-SEAM-001` 的四项合同边界

### 一、租户维度：本棒沿用现有 `default` 语义，不统一改造主体合同

- `handle_user_message` 签名受规范 §8.6.8 和测试锁定，改它须经 GOV-SYNC。授权引用必须带租户并比对 `decided_tenant_id`；启用第二个租户前须先完成 `P2-TENANT-IDENTITY-001`。

### 二、Trace 合同：新增最小 `user_action` 事件

- `TraceEventType` 只加 `user_action`，入站与结果用 attributes 的 phase 区分，不再扩枚举。

### 三、Action 响应合同：`data` 两层命名空间

- `data = {"action_outcome": ..., "result": ...}`，不与结果平铺；`UserActionOutcome` 覆盖全部拒绝分支并有闭合守卫；不扩 `ErrorCode`。

### 四、本棒只承接 `confirm`

- 已由 2026-09-09「`P2-USER-ACTION-TERMINAL-001` 收口 reject/cancel 终态与确认上下文方案 C」替代。

## 2026-08-30 — 裁决：`CapabilitySpec.output_schema` 是规范化输出暨 `ResponseEnvelope` 外露合同
## 2026-08-30 — 覆盖 2026-08-29 两入口 completed 文案差异

- `CapabilitySpec.output_schema` 是能力规范化输出能进入 `ResponseEnvelope`（`data`、`message`、`fallback_text`）的唯一外露合同，不另设 display allowlist；未声明字段不得外露。默认 `additionalProperties: true` 不算授权，只有具名 `properties` 或带子 schema 的 `additionalProperties` 才算。
- 递归 property 名命中 `_CREDENTIAL_MARKER` 则合同无效，projector 返回 `data=None`；动态键运行时逐键校验同一 marker。`ResponseEnvelopeBuilder._sanitize_value` 继续保留，不得删除或绕过。
- 凭证不得放进任何获准字段；本合同不授权 raw payload、内部错误、Trace、Cookie 或凭证进入响应。
- projector 只做外露塑形，不做 `required`、`const`、`enum`、`format` 等返回校验（欠债未结）。进入 envelope 不等于可渲染成 `href`、导航或 Action。
- 要让某字段「在输出里但不外露」，须先另立 ADR 推翻本条，不得私设第二份字段清单。
- `/action` 与 `/handle` 的 completed 响应都用 `_build_envelope` 生成的安全 message 与 fallback_text；只保留结构差异：`/handle.data` 是投影结果，`/action.data` 是 `{action_outcome, result}`。自然语言投影须保留字段来源或先完成等价脱敏，marker-free 敏感值不得进入 `message` / `fallback_text` / `envelope.data`。

## 2026-08-30 — P4 采用静态合同枚举与测试期 Runtime 咽喉观测的混合守卫

- 静态校验生产 canonical schema、Golden 注册能力的 `output_schema` 与 `VALID_RUNTIME_OUTPUT_SCHEMAS`；其余使用面由 pytest 在测试进程内包装 `RuntimeImpl._build_envelope` 观测，复用 `schema_has_credential_property`，只记 nodeid、surface、digest 与 reason code。
- 只有实际观测到 architecture integration sentinel 的 session 才能判 P4 PASS。`completed` 时空 schema（`empty_projected`）失败，其余状态空 schema 只报告；零观测、marker、不可读、wrapper 丢失等一律 fail-closed。
- 单一全量 pytest session 是权威门禁，session 间不累加结果；CI 若拆 shard，须保留一个不分片的 Runtime + P4 sentinel required check 或最终汇总 check。

## 2026-08-30 — 裁定：组织目录不承担授权判定，权限由 OA 自身执行
## 2026-08-31 — 事实确认：`managerid` 不可用于部门主负责人，部门级来源另在部门表
## 2026-08-31 — 授权：本地组织镜像表 schema

- OA 数据的权限由 OA 自身执行，本系统不另建部门授权模型；部门上下文只作展示，不作授权、可见性或路由判据。组织目录与本地镜像用于登录身份展示和聊天收件人选择（内部任务派发对镜像的使用见 2026-09-08「裁决：内部任务派发的三轴授权模型」及其后续替代条目）。
- 不用共享服务账号取部门级数据；如需要须单独裁决。
- 「不依赖 OA 判谁有权」不等于不需要部门树；依赖部门结构的功能以组织目录为前置。
- 目录接口结构只记在 `docs/adr/phase2/ADR-P2-OA-DIRECTORY-001-readonly-directory-contract.md`（只记路径、参数名、字段名与类型，不记主机、姓名、组织 ID、令牌）；采集件永不进仓库。
- `hrmresource.managerid` 已验证不可用（语义是直接上级，现场全为 `0`），不得用来推导部门负责人，目录也不建主负责人列。负责人判定见 2026-09-08 三轴授权模型（`jobtitle`）。
- 本地组织镜像表的 schema 授权已执行完，只覆盖当时的新表与索引，不覆盖现役表结构变更、历史数据迁移、Golden fixture 与 `FROZEN_GT_IDS`。

## 2026-08-30 — 裁决：SDUI 导航、跨语言合同与两棒排期

1. **OA 链接导航**：只接受绝对 HTTP(S) 或根相对路径；`http:` 仅当白名单显式登记且规范化 origin 完全相等才放行。可信基准只来自 `app/config.py::ProductionSettings.oa_base_url`，不从 OA 返回值、`window.location` 或 `ENV` 标签反推。白名单缺失或非法则整体拒绝。规范化后须 origin 相等，且 pathname 等于前缀或以 `前缀 + "/"` 开头；不做原始字符串前缀比较。桌面端只用 `link`，不回退 `mobile_link`。被拒时仍显示记录正文，不渲染链接，并按三类原因给出下一步；放行时显示「去 OA 查看（新窗口）」，`noopener noreferrer`，点击目标 ≥44×44 CSS px。
2. **`target_system` 单一值权威**：`app/ports/capability_registry.py::CapabilityTargetSystem`，经 OpenAPI 与 Orval 派生到前端；未知或不一致的 target 使确认卡不可操作；`targetSystemLabels` 只做显示名映射。
3. **计数不一致**：计数只接受非负 safe integer；`is_complete !== true`、`returned_count !== items.length` 或待办 `authoritative_count !== returned_count` 即为不完整。仍显示合法行，但须醒目标明「列表可能不完整」并给下一步；零行不得伪装成正常空状态；不完整结果不得用于批量动作、全量统计或「全部已处理」。
4. **具名 exact 合同**：`/action` 的 data 只有 `{action_outcome, result}` 两个 required 键且 `additionalProperties: false`，`result` 必须是 `output_schema` 投影结果。`ConfirmCardPayload` 固定五键（`capability_id`、`operation_summary`、`target_system`、`field_names`、`displayed_argument_values`），`additionalProperties: false`，只有 `displayed_argument_values` 键集合动态且值为 string。五键实际值进入 `immutable_request_digest`，改动须保持 wire 等价与摘要兼容。
5. **outcome 防漂移**：`test_frontend_user_action_outcomes_match_runtime_port` 守两侧各自唯一、长度相等、集合相等，不守顺序。

## 2026-08-30 — 裁决：Admin 审计读取面的对象级授权模型
## 2026-09-01 — 授权：`P2-TASK-TENANT-COLUMN-001` 的 `tasks` 可信租户切片

- 仅有 `admin` 不能读 `list_tasks`、`list_task_events`、`list_bindings`、`list_traces`；须另持只读审计角色，该角色不能做任何变更。持有两类角色不扩大对象范围。
- 同租户可跨用户读取；跨租户一律不可见，集合查询返回空集合（`/tasks/{task_id}/events` 见 2026-09-08「裁决：`/api/v1/admin/tasks/{task_id}/events` 的存在性差异统一收敛为 `200 []`」）。无角色或仅 `admin` 在资源 Port 调用前即 403。
- 主体租户只取认证后的 `Principal.org_ctx`，原样进入 Policy 的类型化字段，不由查询参数、目标标识或 `ENV` 标签补齐；部门字段只作展示或审计元数据。调用方给的标识只能收窄查询。
- Policy 管谁能读，service 管指向哪个对象，Trace reader 无条件按可信租户过滤，三层不能互相替代。
- 历史行不得用默认值猜测归属，不得删除；无可信归属的 Trace 与 `tenant_id = NULL` 的 Task 对 Admin 不可见，恢复路径须另行裁定。
- `tasks` 租户列授权已执行完，不外推到其他表、Golden、`FROZEN_GT_IDS` 或第二租户接入。`P2-TENANT-IDENTITY-001` 完成须含 Admin 四个读取面的跨租户反证。

## 2026-08-31 — 事实确认：共享测试库未被污染，两个偶发失败是本机 Windows 环境缺陷

偶发失败源于测试替身保真度与 Windows 事件循环，不是测试库污染。固定测试库回退边界见 [11-19 裁决](#2026-11-19--裁决开发助手分工型号更新与常设授权)。

## 2026-08-31 — 流程违规记录：`P2-OA-ORGANIZATION-DIRECTORY-001` 在缺 Review 前置时被合并

PR #144 永久记为不合规，事后 Review 不得冒充合并前记录。合并前逐项核对审核结论绑定最终 base/head，不以 PR 状态色代替。

### 三、事后监理的 FAIL 说明的是什么

守卫覆盖缺陷不等于代码已越界，两者分开报。同一门禁被绕过第二次就换路线；数据完整性边界优先用行为断言，不靠静态检测实现写法。

## 2026-09-01 — 裁决：监理职责收窄为独立执行取证，返修上限三轮

- 默认五类取证清单与「A 档必配监理、必过 Opus 桥」已由 2026-10-21「精简开发约束与按影响验证」与 2026-11-19「裁决：开发助手分工、型号更新与常设授权」替代；Monitor 只在 Goal 明确要求时执行。
- 能读代码判定的事实归静态评审，必须执行才能判定的事实（如越权是否真不可达）归监理；按事实分，不按主题整块分。
- 取证不得改 Golden fixture 或 `FROZEN_GT_IDS`。同一 task_id 至多三轮，第三轮仍非 PASS 即停手交雨爷。

## 2026-09-01 — 事实确认：`infra → admin` 的角色常量引用不单独立项

`app/infra/policy/minimal_policy_guard.py` 引用 `AUDIT_READER_ROLE` 不算违规、不立项。出现多个 Policy 实现共享该角色，或架构规则禁止 `infra → admin` 时再议。

## 2026-09-01 — 事实确认：OA 深链前缀与后端 OA API 路径不同源

- `OA_BASE_URL` + `ETERNALAI_OA_ALLOWED_PATH_PREFIXES` 管用户点击的 OA 深链，由 `web/vite.config.ts` 构建期注入前端；现役 origin `http://34.64.8.50`，放行前缀 `/wui`。
- 后端调 OA 的路径是 `.env` 的 `OA_*_PATH` 系列。两侧只共用 origin，路径互不反推、改一侧不带动另一侧。

## 2026-09-02 — 裁决：视觉方向改为 iOS 26 玻璃拟态，推翻 2026-08-20 的「拟态玻璃本阶段不做」

已由 2026-11-13 视觉裁决替代。

## 2026-09-02 — 裁决：一级导航改为五项，工作事项筛选改为三状态互斥，顶栏取消当前位置
## 2026-09-02 — 裁决：顶栏「组织范围」改为「部门 / 姓名」，并移除内线电话行

- 一级导航五项：AI 助手 / 工作事项 / 任务交办 / 软件中心 / 消息；管理员页面折叠为二级。导航形态按 2026-11-13（76px 窄栏）。
- 右下角常驻 AI 浮动入口必须保留，不因有菜单项省掉。
- 工作事项分「紧急 / 待办 / 已完成」三类，互斥：紧急 = 已逾期、今明到期、派发标紧急、等本人确认；待办 = 其余未办结；已完成 = 近 30 天办结。规则在标签栏下用一行字写明。「我问过的」放在 AI 助手页左栏会话列表。
- 顶栏不显示当前位置；只读显示「部门 / 姓名」，不做选择器。全站不出现内线电话；用户菜单保留「怎么用 / 找人帮忙」（内容未定）。
- 系统状态与通知分开计数；全局搜索只搜 Work Object（标题 / 来源编号 / 责任人）。

## 2026-09-02 — 裁决：软件中心「新建应用」界面先行，不受「创建功能属后续」阻断

- 前端可先做「新建应用」表单，产出物只是草稿，界面写明「提交审核后才对他人可见」。
- 后端不做：不加创建 API、不改 `capability_registry`、不写库，不为让按钮能用临时加接口。接后端时在当次 GOV-SYNC 改 2026-08-27 §三。
- 前台只展示白名单字段（`name`、`short_description`、`owner`、`risk_level`、`target_system`、`binding_required`、`status`、`version`），不展示 `capability_id`、`intent_tags`、`input_schema`；不与 `/admin/registry` 合并。建的是软件（`App`），不是功能。

## 2026-09-02 — 裁决：任务交办界面的控件闭集、附件字段与主动作按钮样式

1. 字段用政务用语，与 2026-08-27 §七 九类字段逐字对应，分三段呈现。
2. 闭集是合同，前后端一致，改动走 GOV-SYNC：类型 `通知` / `督办令` / `工作任务` / `提醒`（单选）；截止时间用日期时间选择器；提醒策略 `提前 7 天` / `提前 3 天` / `提前 1 天` / `逾期当天`（可多选，每档一次）。
3. 附件是 Work Object 附件（归 `P2-INTERNAL-WO-ATTACHMENT-001`），不是聊天附件：两阶段上传，先入私有存储，发布时才绑定；存储层不以 Work Object 为非空外键，授权主体是参数。界面显示文件名、大小、移除，并标注允许类型与大小上限。
4. 主动作按钮样式已由 2026-11-13 视觉裁决替代。

- 自然语言输入后同页展开预填表单，不做并列入口；AI 草稿不等于发布，发布须人工显式操作；九类字段发布前全部可见。

## 2026-09-02 — 事实确认：OA 提供用户头像接口，顶栏头像取用户本人照片
## 2026-09-02 — 事实确认：OA 用户信息接口的路径与返回形态

- 顶栏显示本人照片，取不到则显示姓氏。
- 接口：`GET <OA_BASE>/api/hrm/resource/getResourceBaseTitle`，query 参数 `id`、`isMobx`、`__random__`。响应 `{ result, hasRight, id }`：
  - `result.lastname`：完整姓名（不是姓氏）。
  - `result.messagerurl`：头像相对路径，如 `/messager/usericon/<x>.jpg`。
  - `result.orginfo`：HTML `<a>` 片段，单位 / 部门 ID 在 `viewSubCompany(<id>)` / `viewDepartment(<id>)` 里，无平铺字段。
  - `result.requestParams`：二次序列化的 JSON 字符串，含 `shortname`、`lastname`、`gender`、`headformat`、`isDefault`、`background`、`fontcolor`、`defaultmessagerurl`。
  - `result.workcode`（string）、`result.sex`（`{ name, value }`）、`hasRight`（boolean）、`id`（字符串）。
- 边界：
  1. 浏览器不直连 OA，由后端代持用户 OA session 读取并解析 `orginfo`。
  2. 未知且不得推定：取头像图是否要带认证、图片响应类型与状态码、取不到时字段形态；取不到即退回姓氏。
  3. 无证据：错误响应形态、`sex.value` 闭集、空 `workcode` / `defaultmessagerurl` 是否常态、多尺寸头像、`isDefault` 等字段含义。
  4. 头像、姓名、单位、部门属人员信息，不进 Trace、fixture expected、日志与报告。

## 2026-09-02 — 事实确认与范围裁决：意图路由缺「都不匹配」出口，`P2-RUNTIME-NO-CAPABILITY-COPY-001` 范围据此扩大

意图路由必须能合法判定「不对应任何已注册能力」，纯问答不得被硬套成能力调用；改意图匹配须跑 Golden gate。能力少是既定范围，匹配不上如实告知。

## 2026-09-03 — 裁决：一屏一个重点，靠层级对比分主次，不靠多写说明

一屏只突出一个重点，主次靠版面层级区分，不靠加说明文字；每段说明须自证必要，否则不写。不以「用户年龄大、数字素养低」为由放大字号或加解释。

## 2026-09-04 — 事实确认：交办页滚动卡顿的真因是 `background-attachment: fixed`，不是 `backdrop-filter`
## 2026-09-06 — 裁决：`P2-USER-PROFILE-READ-001` 的 Opus 桥豁免是单次动作授权，不构成先例
## 2026-09-07 — 裁决：监理 BLOCKED（真实 vLLM 不可达）仍放行合并，属单次动作授权
## 2026-09-07 — 事实确认：`http://34.74.11.38:8011/v1` 是单位内网端点，不得据 IP 段推断对外可达
## 2026-09-20 — 裁决：LOGOUT 一次重新立项及新的监理周期
## 2026-09-22 — P2-GOV-SYNC-066：子进程集成用例计时预算

- 09-04：滚动卡顿先查 `background-attachment: fixed` 和光栅路径，再怀疑 `backdrop-filter`。
- 09-06：该桥豁免只管当次合并，不是先例。
- 09-07 放行：只管那一次合并；外部依赖不可达不能当放行理由，取不到必需证据就停下报告。
- 09-07 端点：该地址是单位内网端点；能否访问只能实测，不能按 IP 段推断。
- 09-20：换 task_id 不会重置监理轮次；重开要明确批准。
- 09-22：该用例的 60s 计时预算不是放宽超时掩盖失败的先例。

## 2026-09-07 — 开发助手治理与 skills 收敛（P2-GOV-SYNC-ASTRA-001）

- 证明新增行为有覆盖：保留测试，只回退生产改动或注入故障，看断言变红。旧测试前后都绿只说明兼容。是否加测试按 2026-10-21。
- 合并授权只卡合并，其余实现、验证、PR 准备照做。B 类治理裁决总归 GOV-SYNC。
- 规则放 `AGENTS.md`，项目 skills 放 `.agents/skills/`，`STATUS.md` 只写结果、日期、阻塞和指针。

## 2026-09-07 — 裁决：聊天回退拆两棒，身份注入只给部门名与当前时间

`P2-RUNTIME-DIRECT-ANSWER-001` 做聊天回退时，只注入部门名和当前时间，不注入姓名。

## 2026-09-07 — 授权：新增 GT-034 / GT-035，既有题面一字不改

`FROZEN_GT_IDS` 只增不改不删；以后每次新增 Golden 题面都要单独授权。

## 2026-09-07 — 裁决：自由输入框不设可见标签，「禁止 placeholder 代替可见标签」在此处不适用

- 只有自由文本输入框（AI 助手提问框、交办页生成草稿框）可以不显示标签。
- 结构化表单字段和下拉、日期、多选控件必须有可见标签。
- 输入框仍要有 `aria-label`；示例文字仍要满足 4.5:1 对比度；焦点要看得清。

## 2026-09-08 — 裁决：`/api/v1/admin/tasks/{task_id}/events` 的存在性差异统一收敛为 `200 []`

- task 不存在和属于其他租户，都返回一模一样的 `200 []`，不返回 `404`。回归测试要证明两种请求逐字节一致。
- 其他端点不跟着改；`AdminTaskNotFoundError` 是否保留尚未裁定。

## 2026-09-08 — 裁决：内部任务派发的三轴授权模型
## 2026-09-20 — 补登：内部派发改为显式跨部门允许集合

任务派发的派发与查看授权以 2026-10-18「任务派发业务规则确认（待技术落地）」及 [WORK_DISPATCH §2](WORK_DISPATCH.md) 为准。下面标「旧路径」的跨部门、监区集合规则**不参与新派发授权**，只说明旧路径与代码常量现状；其余为仍有效的通用要求。

- **三轴分开**：谁能派、能派给谁、能看什么分开判断。把任务派到别的部门，不会因此看到那个部门的资料。
- **负责人身份**（新派发仍沿用，见 WORK_DISPATCH §2.1）：只看 `jobtitle` 数字 ID 是否在 `{75, 380, 1405, 1701, 1999}`。不在集合里就不是负责人，admin 也一样。旧路径的「负责人独占派发」已由 2026-10-18 第 1 条替代。
- **跨部门（旧路径）**：只有部门 ID 列在代码常量 `_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS` 里的负责人能跨部门派发，该集合初始为空。监区只能派本部门，部门判不出来也只能派本部门。
- **名单写在代码里**：负责人、跨部门、监区集合都是代码常量。不从数据库、配置或环境变量读，改动要在 diff 里看得见。领导集合除外，见 [2026-10-26 裁决](#2026-10-26--裁决领导名单按单位存库由管理员维护通讯录本地镜像同步)。
- **集合冲突（旧路径）**：如果一个部门同时在监区集合和跨部门集合里，它不能跨部门派发，并且要通过守卫或诊断报出来。诊断本身不给权限。
- **授权一致**：候选列表、派发提交、重放用同一个授权函数，每次重新查目录。候选、cursor、旧回执都不能当授权凭证。批量里有一个目标没权限，整批拒绝。
- **Trace**：只记部门 ID、分类结果和命中的规则，不记岗位、人员关联键、姓名或凭证。

## 2026-09-08 — 裁决：`P2-INTERNAL-WO-SCOPE-001` 的组织目录前置三项
## 2026-09-14 — 裁决：组织目录姓名镜像、读端点与定时同步合并为一棒

- 查不到当前用户的目录记录就拒绝，不能悄悄当成普通人。
- 部门和岗位每次现查，不存进登录 token。
- `userid` 这类关联键不写进 Trace 和日志。
- 目录超过陈旧上限仍没同步成功，就拒绝依赖目录的授权。同步失败要看得到。
- `jobtitle: 0` 算无岗位。目录状态只用于 `/health` 诊断。

## 2026-09-09 — 裁决：搜索归一化口径与 SQL 内过滤
## 2026-09-09 — 裁决：`P2-SEARCH-QUERY-EXPOSURE-001` 采用方案 A

- **空白**：前端、后端、数据库三端用同一组 30 个空白字符：Unicode `White_Space` 25 个，加 U+001C–U+001F、U+FEFF；U+200B、U+180E 不算。
- **归一化**：连续空白合成一个空格，去掉首尾，再转小写。
- **匹配**：标题按包含匹配，来源编号和责任人按完全相等匹配；`%`、`_` 当普通字符。
- **在 SQL 里过滤**：先过滤，再取 201 条判断是否超出，返回 200 条。不能先截断再过滤，也不能拉到应用层过滤。PostgreSQL 的 `\s` 不能代替这组空白字符。
- 搜索词可以出现在 URL 里。

## 2026-09-09 — 裁决：「跨身份不可区分」只适用于他人引用存在性
## 2026-09-09 — 裁决：确认卡不做持久化，把失败显式化
## 2026-09-09 — 裁决：`P2-USER-ACTION-TERMINAL-001` 收口 reject/cancel 终态与确认上下文方案 C
## 2026-09-09 — 裁决：自由文本「裸确认」的口径（收窄计划 §3.3.4）

- **他人引用**：查不存在的 `response_id` 和查别人的 `response_id`，不论调用者自己有没有 pending，返回都必须一样（不比较 `response_id`、`task_id`、`session_id`、`trace_id`）。自己的引用过期或失效，返回 `confirmation_invalidated`。
- **确认卡不落库**：进程重启、上下文丢失、异常中断都明确报失败，不能假装成功。以后要落库须另行设计并取得授权。
- **reject / cancel**：决定、Task 终态、Trace 作为一条完整链路闭合。
- **裸确认**：用户直接打「确认 / confirm」仍然有效，不要求带引用。文字入口和按钮入口共用同一套 pending 和防重放。
- `tests/runtime/test_runtime_workflow.py::test_each_waiting_action_gets_a_fresh_action_bound_request` 与 `tests/runtime/test_runtime_user_action.py::test_completed_action_preserves_the_text_resume_message_and_fallback` 前后必须绿，不得改弱。
- 异常后，已退役的确认不能再被重放。没有 pending 时，打「确认」按普通消息处理。
- 收尾失败时保留防重放标记并报错，不能当作取消成功。

## 2026-09-09 — 裁决：`P2-COMMIT-HYGIENE-001` 采用方案 B

A 档的格式化改动和功能改动分开提交，不加 CI 检查。

## 2026-09-10 — 裁决：`access violation` 按非致命环境噪音放行

- Windows 上 greenlet 报的 `access violation` 当作环境噪音，不阻塞交付。
- 不许为消掉它改弱测试。
- 一旦它让该失败的测试没有失败，或在 CI 上出现，本条作废，重新评估。

## 2026-09-10 — 裁决：前端交办接线方案的六项未决点

- **第 2 条**：截止时间按浏览器时区解释并显示实际偏移；没有时区的旧草稿要用户重新确认，夏令时歧义不自行默认。
- **第 4 条**：交办页「责任人 / 可见范围」是只读摘要，保留九字段版位；不能悄悄忽略用户输入后照常发布。
- **第 5 条**：跨刷新恢复不做；刷新后如实显示「结果待确认」，不说已完成。
- **第 6 条**：内部事项当时按 `view_only` 呈现、不扩生命周期；任务派发的办理生命周期现以 [WORK_DISPATCH](WORK_DISPATCH.md) 为准。

第 1、3 条的目录前置与端点已交付，原文见底本。

## 2026-09-16 — 裁决：候选标签碰撞与属性键敏感检测
## 2026-09-20 — 补登：Top-K 安全摘要恢复及 b1/b2/b3 结项

以下只管旧 IntentRouter 路径；AgentScope 路径按 2026-10-27 裁决第 2 条。

- 标签撞车按 GT-014：返回 `no_capability_found`。
- 属性键敏感检测按词段匹配，不按子串匹配；只对 OA 正式业务计数字段（如 `authoritative_count`）开窄白名单。
- 能力候选最多八项，只注入白名单字段。
- 能力被列为候选不代表有执行权限。

## 2026-09-16 — 裁决：Workflow 失败映射到既有响应终态

Workflow 被拒返回 `blocked`，超时用尽返回 `failed`，原 `error_code` 保留，不新增状态值。

## 2026-09-16 — 裁决：已批生产改动引起的既有测试机械适配

已批的生产改动把旧测试弄红时，可以直接改测试。但不能放松公共合同、不能把必填字段改成可选、不能加 skip 或删断言，每处改动要说明原因。

## 2026-09-16 — 裁决：OA 待办收敛的三项长期合同

- 同步状态表统一用 `last_attempt_started_at` / `last_attempt_finished_at` / `last_attempt_status` / `last_error_code` 这几个列名。
- 前端只按 HTTP `code` 判断，不读 `oa_sync.failure_code`。
- OA 待办搜索默认 `oa_view=active`，不含 `unconfirmed`。

## 2026-09-20 — 补登：D-6 有界列表排序的有限替换

- **排序**：Work Object 列表先过滤，再按 due_at ASC NULLS LAST、created_at DESC NULLS LAST、work_object_id COLLATE "C" ASC 排序，再读最多 201 条判断溢出，保序返回前 200 条并带 limit_exceeded。
- 不加客户端排序或分页。「近 30 天已完成」视图按 completed_at DESC、work_object_id ASC 排序。

## 2026-09-22 — P2-GOV-SYNC-066：LOGOUT-002 缓存 B2 判据精化

LOGOUT-002 测试里，空身份查询只在以下条件同时成立时豁免：key 为 `['me', g]`、`g` 经实测从未认证、`data === undefined`、没有启用的 observer、`fetchStatus === 'idle'`。来源说不清的代次不豁免。

## 2026-10-18 — 任务派发业务规则确认（待技术落地）
## 2026-10-18 — 任务派发补充裁决与本轮执行安排
## 2026-10-22 — 任务派发 Schema A 与 Q03/Q05 确认
## 2026-10-22 — 任务派发开工前计划复核修订
## 2026-10-22 — 任务派发 Q07 唯一负责人确认
## 2026-10-23 — 任务派发 Q06/Q08 确认与阶段 D 测试库授权

任务派发的具体规则以 [WORK_DISPATCH.md](WORK_DISPATCH.md) 为准，下面是要点：

- **谁能派给谁**：单位领导、分管领导可以派给所有部门和个人；部门领导可以派给所有部门和本部门的人；普通人只能给自己建任务。领导身份不能看自己没参与的任务。
- **默认谁能看**：派发人、承办人、相关部门负责人。「相关部门」只看当前有效的部门办理关系。
- **唯一负责人**：部门要有唯一负责人才能派发；没有或有多个都拒绝，批量整批失败。
- **改派**：原承办人失去访问权；如果他另有派发人或负责人身份，按那个身份判断。
- **办结和撤回**：办结或撤回后任务冻结，只能编辑备注；撤回不算完成。
- **周期任务**：周期规则持续生效，创建人退出登录也继续生成。每期截止 = 计划日 + N 天的固定时刻。账号停用让当期受阻这一条还没实现。
- **数据库**：Schema A 只授权写 WORK_DISPATCH §11 清单里的代码，不授权执行升级、降级或写数据。当时的测试库授权已用完，不是长期授权。
- **不授权的事**：以上各条都不授权读真实目录或数据、连接或写入数据库、真实业务派发。
- 这个专项按「静态开发 → 独立 Review → 修复 → 真实验收」的顺序推进，不用 Mock 冒充验收。条件不够就标「未验收」，不读未脱敏素材，也不动凭证或生产数据。

## 2026-10-21 — 精简开发约束与按影响验证

- 少做设计和防御，改动尽量小，以跨模型 Review 和实际运行为主，默认不加测试。
- 本地全量只在雨爷明确要求时跑；required CI 照常。
- 不再要求每修一个缺陷都加永久测试；只有重复出现的缺陷、重要权限边界或验收明确要求时才补。
- 自审、Monitor、本地全量都要明确要求才做。

## 2026-10-26 — 裁决：领导名单按单位存库由管理员维护，通讯录本地镜像同步

- **通讯录同步**：用一个指定普通账号同步全单位通讯录，存成本地镜像并定时同步。联系方式不入库，开发测试用合成数据。
- **真实同步**：执行前先向雨爷说明数据流向并取得确认；结果只报人数、部门数和是否完整，不输出人员行。
- **领导名单**：单位领导、分管领导存在数据库里，按单位分开。只有本单位管理员能改，改动留痕，撤销只做标记。名单只对当前目录里的人生效，名单为空就没有领导权限。
- **建表授权**：批准为领导名单建表，只能在专用测试库执行；真实库、生产库升级要另行确认。
- 只有领导集合改为存库，负责人等其他集合仍是代码常量。

## 2026-10-27 — 裁决：AgentScope Runtime 选型、M1 工具组替代 Top-K 与运行时工具协议

1. **选型边界**：新 Runtime 用 AgentScope SDK，同进程集成；框架类型只在 `app/infra/`，ports 与业务合同不暴露。Gateway、Policy、企业控制内核与确认执行权不变。不启用 `agentscope.app`、Hub 或外部追踪导出。
2. **D1：新路径不做 Top-K**：本请求全部可见能力按代码映射表分组，用 `ToolGroup` + `reset_tools` 由模型开关；不截断、不做词面预筛。每个工具恰属一组，组超 `G_MAX`（初值 10）就拆，不截掉成员。只暴露名称、简述和白名单投影后的入参 schema（`type`、`properties`、`items`、`enum`、`minimum`、`maximum`、`minLength`、`maxLength`、`minItems`、`maxItems`、`format`、属性 `description`，递归适用；`description` 与简述限长初值 120 字，属性键沿用 `_require_safe_property_key`）；删除 `required`，其余关键字丢弃；投影 digest 按调用版本绑定；超 2 KiB 或校验不合格的排除并标记目录不完整。组激活不产生执行授权。旧 IntentRouter 路径仍按 2026-09-20 最多八项，直至退役。
3. **D2：工具协议**：按 [AGENTS.md 不变量第 2 条](../../AGENTS.md#项目不变量与红线)：选工具用原生 tools，固定格式数据用 JSON Schema 优先；模型、模板、解析器、思考模式或 vLLM 版本变化后重验。
4. **已完成**：AgentScope 已是默认入口，保留显式 legacy 路径；M0 计数路由删除授权不外溢。历史见[归档](archive/DECISIONS_dev_assistant_history_to_20261119.md#2026-10-27--agentscope-runtime-选型的历史记录)。
- **第 6 条 · 离线评测**：留出集只在最终配置跑一次，不达标不降目标。目标：C-real/C-50/C-100 单工具正确率 ≥95%/92%/90%，单能力 ≥8/10，零词面与接上文 ≥90%，不调用/追问 ≥95%，跨组 ≥85%，身份注入 0，p95 ≤30 秒、超时 0。离线评测不证明隔离、授权或写入。

## 2026-10-28 — 裁决：使用中会话续期、确认卡账号绑定与 M1 收口

1. **续期方向**：OA 会话与业务 MCP 授权都要使用中自动续期、不能断。R-MCP 等外部 E1（见第 4 条）。
2. **D1**：应用登录无操作 2 小时（7200 s）失效，不设绝对上限（可选配置默认关）。遇 401 或需重登时保留未发送输入和当前页面，重登后回到原处。
3. **HQ4**：确认卡绑定 tenant + ai_user_id，不绑登录令牌指纹；确认时须当前登录有效；同账号重授权仍有效，换账号作废。
4. **D5**：MCP 不做“到期提醒 + 人工重授权”过渡；等授权服务提供 refresh_token、吊销端点与 offline_access（E1）后再做 R-MCP，此前到期即需手动重授权，记欠债。
5. **模型可切换**：开发与验收模型可随时切换，不改运行时合同。
6. **H4：新路径 Golden 合同**：本条只约束新路径（`RUNTIME_ENGINE=agentscope`）的 Golden 回放；required Golden 的现状（新旧两条路径）见 [PLAN「Runtime 替换 M6 登记」](PHASE2_PLAN.md#runtime-替换-m6-登记2026-11-02)。**不修改 Golden fixtures，不改 `FROZEN_GT_IDS`，不改判卷断言。**
   - (a) `match=none` 时回放模型只发一次 `report_unsupported()`，映射为 `no_capability_found`。
   - (b) 命中能力先 `reset_tools` 激活所在组（`catalog.group_of`），再按 fixture 参数原样调用，读最后一个工具结果的 `message` 作文字回复。
   - (c) 写工具调用后生成确认卡并 `waiting_user`；第二轮“确认”由前置入口处理，不再调模型（回放模型被调用即报错）。
   - (d) 新路径只暴露 `query`、`workflow`；GT-006、GT-007、GT-029～033 不适用，单列报告。
   - (e) GT-014 别名选择不承接，单列报告。
   - (f) 缺 `input_schema` 的 fixture 只在回放进程内补空对象 schema；生产目录缺 schema 仍失败关闭。
   - 新路径分母 26 题；旧路径退役前重评这 8 题的等价回归。判卷或运行选择规则变更须全量回放并披露影响。
7. **后继排序**：已由 2026-11-02 第 9 条替代。

聊天页模型下拉框新增接口属公共契约，开工前须出短方案。

## 2026-10-31 — 裁决：M2 D 报修收集口径、D 护栏取消与提交拦截

1. **不用源码哈希**：D 的稳定身份用 `app/infra/browser/d_form_manifest.py::registration_digest`；D 代码变更靠评审与 CI 把关。
2. **类别**（仅新路径）：模型从 D 任务配置的候选里选类别，JSON Schema 枚举 + 服务端校验，候选外拒绝；不向用户要类别。续填用 JSON 字段提取。配置在 D 安装阶段经 `install_browser_handoff` 注入。
3. **报修只收地点、物品、故障情况**：缺哪项问哪项，不问姓名、电话、紧急程度、类别。续填只允许 description 以旧值为前缀追加，其他已有字段不可变。
4. **五条底线**：①填写后停，不保存、不提交；②凭证与受保护数据不外泄；③身份与 tenant/user/session 隔离、HumanGate 绑定；④只访问配置的 OA 主机；⑤清理自启浏览器进程、临时 profile 与租约。其余护栏已取消，不恢复按 HTTP 方法/frame 的全局拦截。
5. **M3-1 只拦提交**：填写阶段拦 D 表单保存/提交，触发即 `FORM_SUBMIT_BLOCKED`、effect `unknown` 报失败；同主机非提交 POST 放行。落点 `app/infra/browser/d_form_flow.py::Flow.guard_form_submission`。该次 Monitor r3 永久记为 FAIL。
6. **确认回执范围**：只覆盖带 `client_request_id` 的请求；无标识的 cli/api/mock 渠道接受旧语义（裸“确认”可跨卡重放），不扩大到前端。回执表在进程内，多进程部署义务见 [PLAN M1B 欠债](PHASE2_PLAN.md#runtime-替换-m1b-新增欠债2026-10-28)。
7. **M6 后续待办**：见 PLAN。

五条底线不替代 M3 人工交接合同，也不豁免专项授权。

## 2026-11-01 — 裁决：M3 D 人工交接合同、受限终结与先合并后验收

1. **Q1 受限终结**：注销、会话过期/吊销/缺失、绑定失效，或策略/发布失效后封存时限已过且 worker 失联的交接 run，只能走“仅终结 + 清理”：历史授权限同 tenant/user/binding/发布、binding revision 不新于安装值，并核对精确 run 行；在既定锁序内用 SQL 重核负面事实或封存时限，且要求 worker 失联，仅续期延迟、时限未到且无负面事实时拒绝。不调用 `validate`、不解密、不做浏览器 IO、无用户入口，只写 `failed/browser_verification_failed/incomplete`。入口 `PostgreSQLBrowserRunStore.expire_lost_handoff`、`record_historical_handoff_quarantine`（`app/infra/persistence/browser/runs.py`）。终结不等于释放租约；worker 卡住时不承诺滞留上限。
2. **Q2 交接合同**：`BrowserRunResponse.handoff` 含 `sealed_at`、`deadline` 和本人三项 `fields`（category、position、description），受当前读授权约束，只回本人本会话。`POST /api/v1/browser-runs/{task_id}/{run_id}/verify-result` 复用认证、CSRF 与 bound session，返回 202；`handoff_check` 按所有者 + 当前授权判定（同取消授权口径）。“我已提交”精确匹配，取可信 bound session，恰一个活动交接才核实。交接期间新路径隐藏同会话 D 工具。交接期间同会话新 D 请求返回 409 `browser_handoff_active`。
3. **Q3**：助手不取密、不代登录；开发库租约人工恢复、重启后端须执行时另行授权。
4. **Q4**：核实器 `DFormRuntime.check_handoff` 恒 `unsupported`，“我已提交”不写 completed；提示用户到 OA 核对、勿重复提交。历史 D completed 只表示填写完成。
5. **Q5**：重启后无法证明旧窗口已关，租约不自动回收（欠债）；已请求取消的封存 run 写 `failed/browser_verification_failed/incomplete`，不写 cancelled。人工恢复不能核销自动回收。
6. **Q6**：底线④只约束自动化，不拦用户在交接窗口手动浏览。
7. **离开期间授权**：已执行完；现按 2026-11-19 第 2 节第 1 项。
8. **首版验证 run**：只有已激活发布的业务 run 进入人工交接；首版验证 run 由已持久化、MAC 绑定的 `TeachingPlan.purpose == initial_validation` 判定（不用 prepared 状态、HTTP 参数、配置模式或管理员身份判定，坏计划报错不回退），填写核验通过即 completed 并关窗，不进人工交接；不放宽业务 verified 条件。
9. **verify-result 503**：`browser_chat_unavailable` 或 `authentication_unavailable`，仅说明，不新增失败条件。
10. **验收义务**：H 系列未取证项、actors 首版管理全链、legacy D 人工交接仍须真实验收；CI 与静态 PASS 不能核销。见 [PLAN M3 新增欠债](PHASE2_PLAN.md#runtime-替换-m3-新增欠债2026-11-01)。

## 2026-11-02 — 裁决：云端自动办理方向、聊天上传填表、后续技术选型定稿与开发顺序调整

1. **云端自动办理**：浏览器任务在服务端执行，用户离线照常继续。确认卡用业务语言写清办什么、填什么；确认后自动执行到最终提交。真实启用自动提交按流程逐项授权，测试阶段由人手动提交。自动提交复用 `BrowserSubmissionService.decide_write`、`reserve_write_dispatch`、`resolve_dispatch` 的 unknown 不重放语义；每个流程须有结果查询方式与成功判据，按原身份与业务唯一键核对单号或业务后态；结果不明就保持待核实、不重复提交。
2. **远程画面**：可选，默认只看、可接管；浏览器不在用户终端，不迁移 cookie。用 Playwright screencast + 服务端白名单输入转发；人机切换：停收新动作 → 排空在途 → 人工独占 → 交还后重新观察。输入法不过关时改 KasmVNC。
3. **上传 Excel 填表**：模型只提列到字段的映射，不产生行数据；确认卡绑定原件哈希、工作表、范围与映射，服务端确定性填写，查不到显式标注。首版只收 `.xlsx`；LibreOffice 兼容导入在样表验收后才开。默认在原表副本只填确认过的空格。
4. **后台执行**：所有任务类型确认后持久入队，由独立 worker 执行（离线继续、重启续跑、取消、限并发），统一用 Procrastinate（PostgreSQL）。浏览器业务账本与租约仍以 PG run 存储为准，队列只派发；重启续跑不重放 unknown 写入。四个旧进程内调度器逐步迁移，迁移前修正“入队即完成”假设。队列 schema 安装须专项授权。
5. **技术选型**：不整体引入外部平台或 agent runtime。

   | 用途 | 技术 | 许可 |
   |---|---|---|
   | 新建报表 | XlsxWriter（合计公式 + 缓存值） | BSD-2-Clause |
   | 上传读取/回填 | openpyxl | MIT |
   | 上传安全 | zipfile + defusedxml；oletools 仅旧格式 | PSF；BSD |
   | 后台任务 | Procrastinate | MIT |
   | 远程画面 | Playwright screencast + OpenBot/Steel 小模块 | Apache-2.0；MIT |
   | 预教采集 | Playwright pick_locator/recorder，回放仍经 `replay_draft` | Apache-2.0 |
   | 产物存储 | 私有卷 + PG 元数据（HMAC） | 仓内实现 |
   | 下载 | 同源 GET + FileResponse | BSD-3-Clause |
   | 上传入口 | Ant Design X Attachments | MIT |
   | 容器编排 | Docker SDK for Python | Apache-2.0 |
   | 镜像 | Debian slim（API）+ Playwright noble ARM64（浏览器 worker） | 各按原许可 |
   | Runtime 与 MCP | AgentScope 2.0.11；MCP 2.2.0 | Apache-2.0；MIT |

6. **许可**：MIT/Apache/BSD 可复用，保留声明并登记来源；GPL/AGPL/ELv2、带品牌或商用条款、无明确许可的项目只借思路，不复制代码、UI、提示词或资源。
7. **部署推后**：先在本机开发，服务器迁移另开任务。目标为银河麒麟 V11 ARM + Docker，迁移时核实页大小、cgroup v2、KYSEC 与 Chromium 沙箱。每任务独立隔离资源。不含部署授权。
8. **产物保留 14 天**：输入自封存、输出自发布起算；到期先拒绝下载，再幂等删除。不改现有浏览器产物 TTL。
9. **开发顺序**：GOV-SYNC → AgentScope 2.0.11（`P2-RUNTIME-AGENTSCOPE-GA-001`）→ 默认入口切 agentscope、Golden 改走新路径（`P2-RUNTIME-AGENTSCOPE-M6-001`；删旧代码另行授权）→ 新功能基础 → 远程画面 → 依赖业务输入的功能（填表映射、新报表、自动提交、预教流程）。M1B、R-APP、R-OA 真实验收随时可做，不因排序核销。M5 暂缓，R-MCP 等 E1。

## 2026-11-03 — 裁决：新功能基础拆分与监理模板口径对齐

1. **三棒串行**：`P2-BACKGROUND-FOUNDATION-001` → `P2-FILE-ARTIFACTS-001` → `P2-CHAT-UPLOAD-001`。共享依赖文件、`app/composition.py`、`app/application.py` 与 Alembic 链单 writer。
2. **总体方案**：已批准，见下一条。
3. **入队合同**：确认后入队必须与业务写入同一事务提交，不得把“待投递”报成“已入队”（实现方式见同日 O1 裁决）。
4. **依赖按棒引入**：首棒 procrastinate；上传棒 openpyxl、et-xmlfile、defusedxml；XlsxWriter 随新报表棒。每项先登记 `docs/dev/dependency_policy.md` allowlist 与许可来源，再改 manifest 与锁文件。
5. **删除另授权**：产物到期删除须列出精确存储根与对象范围，申请专项授权。
6. `phase2/P2-GOV-SYNC-DISPATCH-20261018` 的 3 个提交不并入；删分支另需授权。
7. 监理模板只禁 commit SHA 与 CI run id，不比 `AGENTS.md` 另设门禁。

## 2026-11-03 — 裁决：新功能基础总体方案批准与一次性授权

方案以仓库外 `FOUNDATION_PLAN.v1.6.approved.md` 为准。

1. **批准**：三棒按方案 v1.6 施工。
2. **一次性授权（已结束）**：原限三棒施工与验收期；三棒已合并，本授权到此结束，后续验收或迁移需要时重新申请，不外推到后续任务。以下为原授权范围，仅供追溯与迁移文件引用：
   - Schema 只 upgrade（R1、R2 随首棒，R3 随产物棒），内容以方案附录 A/B 为准，哈希不符即停；目标只限开发库 `127.0.0.1:5432/eternalai`（容器 `eternalai-postgres-1`）与测试库 `127.0.0.1:15432/eternalai_test`（容器 `eternalai_test_db-postgres-1`），每次执行前按方案 §4 核对目标。
   - 两库中只写 `public.background_jobs`、`public.file_artifacts` 与 `eternalai_queue.*` 记账行，不改其他表数据。
   - 雨爷本人账号验收：后台服务自检的提交、重放、取消；指定非敏感 xlsx 样表与仓库外合成测试文件的上传、引用、下载。跨账号隔离只用雨爷提供并本人登录的第二测试账号，没有则标未真实验收。
   - 进程：启动或重启本机 18005；`python -m app.worker` 的启动、Ctrl+C 停止与强杀。
   - 存储根：创建 `E:/eternalai-data/file-artifacts-standard` 与 `E:/eternalai-data/file-artifacts-acceptance-sample`；只在两根下按方案 §6 的 D1a、D1b、D2 删除元数据对应文件，D4 为队列库内部记账行删除。
   - 依赖：项目内 `uv sync` 下载方案 §5 所列版本，按棒引入，wheel 哈希不符即停。
   - 密钥：产物 MAC 钥由雨爷本人生成并写入 `.env`，施工方不生成、不读取、不写入。
   - 第 8 条：在测试库 `eternalai_test` 执行一次受控故障注入脚本，验证接收事务回滚；脚本不提交。
   - **不含**：合并、部署、downgrade；其他库（含同实例 `eternalai_dispatch_test`、`eternalai_pilot`）、路径与进程；OA 或业务凭据的获取与持有；两根以外的文件删除，以及根目录、元数据行、已完成作业行的删除；生产或真实业务数据。
3. **已决项**：J1 后台骨架以自检 + `background_jobs.reconcile` 验收，不代表业务处理或旧调度器迁移已验收；J5 本机上传检查只剥离环境变量（开发期例外），网络与文件隔离随 Linux 容器实现；J6 标准根保留 14 天；J9 MAC 钥 `key_id=v1`，暂不轮换；J10 附件只在当前会话界面可见，401 重登可恢复，刷新或主动退出后不显示，本人直链有效至到期。
4. **作业恢复**：一条队列行最多执行一次，不用 Procrastinate 原地 retry；按 `claim_token` 与账本心跳判停，恢复换新行，派发含首次不超过 3 次。

## 2026-11-03 — 裁决：后台作业同事务入队改用官方 SQL 函数与开发库目标识别口径

1. **O1**：在现有 SQLAlchemy 会话的同一事务内调用 Procrastinate 官方 SQL 函数入队与取消（`app/infra/jobs/queue_sql.py::defer_job`、`cancel_job`），由该会话提交。worker 是独立 psycopg 进程。升级 Procrastinate 须重核 `queue_sql.py` 的 SQL 合同。
2. **目标识别**：`docker port` 输出含 `127.0.0.1:5432` 即通过第 2 项；其他项任一不符即停。

## 2026-11-04 — 裁决：IM 消息功能机会层、选型更新与聊天交互边界放宽

1. **机会层**：IM 与基础棒并行，不改 P2 必达条件。
2. **同页与离线**：IM 必须嵌入 `/messages`，不另开标签。首切只做：选本人可见消息 → 同页本人 AI 只读查询 → 返回原消息。内网无域名、只用 IP，须支持离线。
3. **选型**：JuggleIM 因越权读群历史退出；Zulip 是继续验证的首选，不是生产定案。身份、权限、Policy、卡片与执行链归 EternalAI。
4. **聊天放宽**：**允许只读查询和生成草稿；正式业务写入仍须本人确认卡；普通聊天及引文不作为执行授权**。草稿不等于可以保存或提交。
5. **可替换**：通信层经 ports 与自有桥协议隔离，可换掉 Zulip。
6. **边界**：不含 schema、凭据、证书、部署或合并授权；`WorkCandidate`、IM 出站、个人 Agent、手机正式交付不在首切。

## 2026-11-04 — 裁决：允许成熟壳客户端（PC Electron、手机 WebView/ArkWeb 薄壳）

1. 允许用成熟壳承载同一份 Web，不另写业务客户端；壳只做证书信任、托盘通知、文件相机等浏览器做不到的事。
2. PC 用 Electron 薄壳；Win7 仍用浏览器。
3. 手机：Android 与鸿蒙 4.x 共用一个 APK 壳，鸿蒙 NEXT 用 ArkWeb 壳。不等于手机正式交付已放行。
4. 不含签名证书、信任根分发、正式分发、部署或系统配置授权。

## 2026-11-04 — 裁决：IM 机会层执行口径（评审、数据库、SSO 与部署条件）

1. **IP + HTTPS**：内网只用 IP；HTTPS 用内部 CA，证书含实际 IP SAN。
2. **SSO 例外**：只有专用 SSO 登录接口可把 **60 秒短期登录 token** 交给**已认证的本人**浏览器内存，用于 **POST 到固定 IM 登录地址**；不得扩展到普通业务响应、URL、postMessage 或持久化，不得写日志；其余凭据边界不变。
3. **数据库**：IM 升级只在复制出的新容器、新端口测试，现有两库不动；通过后才申请合并。
4. **评审**：改按 2026-11-19 分工；先完成功能再评审，重点删过度设计。
5. **最低安全四条**：①不串身份；②凭据不进日志、普通业务响应、URL、postMessage 与持久化（例外仅第 2 条）；③未经本人确认不写业务；④引文不当指令。其余不加无依据的护栏。
6. **CI 临时库迁移**：允许；开 PR、触发 CI 不用请示。不授权本机或真实库迁移。

## 2026-11-05 — 裁决：IM 验收自动执行与持久登录口径

1. 内网默认开，不再问；IM 验收复用各账号持久 profile，授权内自动推进。
2. IM 验收中会话失效时，脚本只可在对应账号窗口点击登录、使用浏览器已保存的凭据；不读、不输入、不另存账号密码，身份不符即停，不扩大账号或业务动作范围。
3. 合并仍等雨爷说“合并”；正式写入仍须本人确认卡，未授权的保存和提交不因自动验收而放行。

## 2026-11-06 — 裁决：IM 机会层 V4 委托触发语义、schema 与第 4 轮监理

1. **委托语义**：群内“委托 @本人助手 @目标助手 问题”即本人确认转交。A 只回固定回执；B 只用 B 已授权给请求人的已发布资料，答案只私回请求人；两位主人的权限都不借用。前提是 A、B 两个助手 Bot 都订阅该频道；不符合格式的消息沿用 V3。
2. **schema**：`im_inbox.delegate_agent_id`，migration `20261105_120000`，两库已 upgrade。
3. 合并与升级授权已执行完。
4. **前缀剥离**：只剥离开头的“委托”和两个已验证提及，正文不动。第 4 轮监理是本 task 特例。

## 2026-11-06 — 裁决：远程画面全程观看、取消独占连接限制与第 3 轮后处置

1. **全程可看**：登录、填写、交接全程可看；已取消 CDP 独占连接检查（保留 `SESSION_BUSY`）。登录阶段不能接管；填写中接管转人工交接，交还后只看，机器续填记欠债。
2. **接口**：WebSocket `GET(ws) /api/v1/browser-runs/{task_id}/{run_id}/screen` + 服务端白名单键鼠/文本转发。
3. 验收授权已执行完，不外推。
4. 第 3 轮后不复审直接验收是本 task 特例。

## 2026-11-06 / 2026-11-08 — 裁决：V2b 文件来源、schema、下载信任边界与 IM 身份方向

1. **文件来源**：私发授权绑定 artifact_id + sha256。
2. **schema**：`im_deliveries` 加四个可空列 `artifact_id UUID REFERENCES file_artifacts(artifact_id)`、`artifact_sha256 BYTEA`、`grant_expires_at TIMESTAMPTZ`、`grant_revoked_at TIMESTAMPTZ`，migration `20261106_120000`，两库已 upgrade。
3. **下载边界**：甲本人确认私发后，乙以本人登录在原统一下载地址下载同一版本，至多到文件到期（≤14 天）；甲可撤销。撤销或到期后乙不能新下载或新增核实，可看历史；已下载不可召回，转发无效。乙不获得甲的会话、聊天附件读取或其他文件权限；持有卡片或 URL 不算权限。
4. **身份**：工作台账号即 IM 账号，一人一号；`im_account_bindings` 只是技术对应。映射保留/删除期限到 V5 试点前再定。

## 2026-11-09 — 裁决：IM 通用交互先纯配置试用，再定统一会话左栏

1. 先在合成 Zulip 用原生设置试用简化操作。
2. 统一会话左栏等雨爷试用后再定；改样式不等于获得开发授权。
3. 不复制品牌素材，不建完整客户端或消息库。

## 2026-11-13 — 裁决：视觉方向改为「墨、纸、印」

雨爷 2026-11-13 决定（随 PR #284 工作台改版生效，本条为补登）：

- 视觉方向由 2026-09-02「玻璃拟态」改为「墨、纸、印」。
- 2026-09-04 的定稿画板与字号闭集废止。
- 2026-11-11 的 224px 可折叠左导航改为 76px 窄栏。
- 目标浏览器下限升到 Chrome 130；IM 内部仍不使用 Chrome 101 不支持的特性。
- 按钮与输入框的静息边线照 v7（白底加 15%～22% 的细线），不再要求与相邻色对比 3:1。
- 后端没有的数据和功能，前端不摆占位、不编数字，登记缺项。

## 2026-11-19 — 裁决：开发助手分工、型号更新与常设授权

**来源**：雨爷 2026-11-19 原话：「claudecode主控、写plan、所有前端代码、主要框架代码，codex负责review、杂活、搜索、机械活等。昨天更新了6.2SOL与6.2astra，所以之前的6.1都改为6.2。」「四项授权同意……第4项……按照你的建议来就行。」本条是开发助手分工的唯一现役入口，替代 2026-09-07「裁决：开发助手的模型路由」、2026-09-23「P2-GOV-SYNC-068」、2026-09-24「P2-GOV-SYNC-069」、2026-09-29「P2-GOV-SYNC-070」「P2-GOV-SYNC-071」及 2026-09-30 补充中的路由与分工条目；2026-10-21「精简开发约束与按影响验证」继续有效。分档、风险、验证与合并规则仍以 `AGENTS.md` 为准。

**1. 分工**

| 环节 | 执行方 |
|---|---|
| 主控、调度、结果判断、真实验收判断 | Claude Code（Opus 5.5） |
| plan | Claude Code 写，粒度与人工停点按 `AGENTS.md` 分档表；Goal 要求或主控判断需要独立计划评审时，由 codex `gpt-6.2-astra` high 只读评审。评审结论不代替任何人工授权 |
| 全部前端代码；主要框架代码（Runtime、核心框架、跨层合同与公共 ports） | Claude Code（Opus 5.5） |
| 其他常规后端实现 | Claude 按复杂度自写，或派 codex `gpt-6.2-sol`（雨爷 2026-10-25「核心、复杂任务你用opus5.5去完成，其他的工作可以直接安排codex」） |
| 生产改动跨模型静态评审 | 按实际作者分配，评审模型不同于作者模型，且独立于对应施工上下文：Claude 写的由 codex `gpt-6.2-sol` 审（雨爷 2026-10-22「代码评审用 Sol」）；codex `gpt-6.2-sol` 写的由 codex `gpt-6.2-astra` 审（雨爷 2026-11-19「sol写代码 review是astra 所以算是跨模型评审」）；`gpt-6.2-astra` 写的由 `gpt-6.2-sol` 或 Claude 审。混合作者的候选按作者划定评审范围，各评审方绑定同一最终 base/head 并核对相关接缝 |
| 杂活与机械活（commit/push/PR/CI 核查、测试迁移、环境排查、原文搬运与核对） | codex `gpt-6.2-sol`；effort 按任务 |
| 给人读、会反复加载的文字（DECISIONS、STATUS、PLAN、AGENTS、PR 说明、汇报） | Claude Code 亲自写；codex 只做机械搬运、核对与评审（雨爷 2026-11-19「这几个文件你亲自改，sol模型不说人话」） |
| 联网搜索与调研 | codex `gpt-6.2-sol` |
| 反复未达验收的根因分析 | 主控判断需要时可派 codex `gpt-6.2-astra` max 只读分析（雨爷 2026-10-27）；不是自动门禁 |
| Monitor | 仅 Goal 明确要求时，codex `gpt-6.2-sol` 独立会话 |

此前的 6.1 型号一律改用 6.2。不再使用 Fable 主控、GPT Pro 浏览器 plan 通道、外部评审桥、Luna 和新上下文自审；常用工具只有 Claude Code 与 codex 及其模型。历史记录中的旧型号、实得模型与原授权措辞不改写。2026-11-04 IM 机会层「执行交 codex、主控只派发与检查」只描述当时的执行事实，其「Sol 写、Astra 审」的做法已由本表常设化；此后 IM 前端与主要框架的执行和评审按本表，IM 产品范围仍按原条款。

**2. 常设授权**（严格限于下列范围，不外推）

1. **离开期间普通 PR 合并**：仅当雨爷明确表示离开，且仅对其离开前已交代的 EternalAI Goal 内的普通 PR；雨爷返回、撤回或该任务结束即失效。条件：required checks 全绿、分支保护满足、适用 Review（及 Goal 要求的 Monitor）PASS 且绑定最终 base/head。排除红线动作，以及架构、框架、公共合同/API/协议、信任边界、核心不变量变更和其他 `AGENTS.md` 人工停点。雨爷在场时按 `AGENTS.md` 等待当次明确合并授权。
2. **内网模型处理未脱敏素材**：本项仅授权经确认的 OpenCode 内网 qwen 模型，对 EternalAI 已交代任务所需的既有最小范围素材（`_scratch/oa/` 原始件、登录日志、截图、已有的真实业务数据与内网材料）做内网只读分析，只回传脱敏结论（不含人名、账号、令牌、内网地址与原文片段）。后端切到外部模型或身份不明时不适用。不含新的信息采集、取密、登录、写业务数据、外传，也不得把原件转交其他模型。Claude 与 codex 不得凭本项经手原件；其他读取仍按 `AGENTS.md` 不变量 7 取得当次授权。
3. **IM 验收测试证书续签**：仅限 IM 验收环境（19412/19415 验收实例）原有 CA 与叶证书（`E:/code/eternalai-im-infra/build/tls/`）用同一原 CA 密钥续签、替换续签件、装回原本机信任位置对应的证书，并重启该验收实例所用的原代理或服务。更换信任根、私钥外传、真实凭据、生产证书及其他信任根仍须对应授权。
4. **固定测试库迁移版本回退**：仅限 `127.0.0.1:15432/eternalai_test` 这一实例，由写入该 revision 的原工作树在无其他使用者的串行窗口执行。唯一恢复目标是本候选写入前记录的该库主线 revision。执行前核对实际连接的主机、端口、库名、当前 revision 与该目标，以及其间每一步都属于本候选、可在原工作树解析且符合祖先关系；任一项无法核验即停止。执行后复核最终 revision 并记录。分支已分叉、同名异库、需处理数据或需额外 upgrade 时停下说明缺项。不含 `eternalai_pilot`、开发库、任何真实库、新 schema 设计或数据迁移。

## 2026-11-20 — 裁决：对话历史持久化、个人记忆、概览绑定提示与分支保护

**来源**：雨爷 2026-11-20 原话：「对话历史要持久化，还需要增加个人记忆功能，记住个人聊天喜好等信息（具体开任务时再详细讨论实现）。」「下一棒可以做历史持久化和个人记忆；三个绑定卡片那个做。」

1. **对话历史持久化**：要做，另立 `P2-CHAT-HISTORY-PERSIST-001`。目标是后端重启后模型仍能看到本会话此前的对话内容，而不是只剩已完成查询的名称。实现方案开棒时与雨爷细谈；需要新表或迁移时按红线另取授权。对话历史只属于本人本会话，不得跨用户、跨租户读取。
2. **个人记忆**：要做，由 `P2-MEMORY-001` 承接，先做本人的个人记忆（记住本人的聊天喜好等）。本条解除该棒「用户数据边界未到位」的阻塞，范围只限本人自己的记忆。2026-08-19「知识库不进 P2；Memory 边界收窄」继续有效：来源只限本人自编、聊天与办理记录；身份证号、银行账号、家庭住址等敏感信息和密码不进；部门之间隔离；知识库（个人/部门资料空间）仍放 P3。记什么、怎么记、本人怎么查看和删除，开棒时与雨爷细谈。
3. **概览绑定提示**：聊天里的 OA 总览查询（`oa.read_overview`）遇到未绑定（`identity_unbound`）、已撤销（`identity_revoked`）、需选择绑定范围（`needs_binding_scope`）时，不再显示「操作失败」，改为给出对应提示：前两种给绑定入口，需选范围沿用现有的多绑定范围澄清，GT-012 照常 100% 通过。由 `P2-OA-OVERVIEW-BINDING-HINT-001` 实现，`tests/runtime/test_production_workflow.py::test_overview_short_circuits_each_failure` 的预期随本合同调整。
4. **分支保护不开「Do not allow bypassing」**：雨爷 2026-11-20 原话「分支保护不开」。管理员账号仍能在技术上绕过 required checks；「一律走 PR、required checks 全绿后才合并、不直推 `phase0/main`」继续由 `AGENTS.md` 约束，执行方不得使用绕过能力。

先后：下一棒做第 1、2 项（合为一棒还是拆两棒，开棒时定）；第 3 项随后，或按 `AGENTS.md` 另开并行 lane。
