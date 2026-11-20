# Phase 3 总目标、范围与任务 DAG

本文件写 P3 的目标、范围、现役 DAG 和 **P3 期间新增的欠债**。P2 转入的活欠债仍在 [P2 PLAN](../phase2/PHASE2_PLAN.md) 原行维护：那里不再新增条目，状态变化（关闭、改指向）在原行更新。裁决以 [P3 DECISIONS](DECISIONS.md) 和仍有效的 [P2 DECISIONS](../phase2/DECISIONS.md)（`docs/phase2/DECISIONS.md`）为准。当前状态与下一棒指针只见 [STATUS](STATUS.md)。

## 1. P3 总目标与收口标准

P3 在 P2 已收口的部门试点基础上，补上**系统代为提交的低风险写入**，并让对话能跨重启延续、记住本人偏好。依据：[P3 DECISIONS 2026-11-20「P3 范围与文档建立」](DECISIONS.md#2026-11-20--裁决p3-范围与文档建立)。

**必达**（全部达成即收口）：

| # | 内容 | 承接 |
|---|---|---|
| 1 | 系统代提交 OA 待办「审批同意」，经确认卡授权、受版本绑定保护；覆盖它的 Golden（含负向与边界） | `P2-LOW-RISK-WRITE-001`（BLOCKED 于 OA 审批提交接口结构）→ `P2-GOLDEN-002` |
| 2 | 对话历史持久化（侧栏重开、重启续聊、本人删除）与个人记忆（自动记住偏好与工作背景，本人可查改删） | `P2-CHAT-HISTORY-PERSIST-001`（进行中）→ `P2-MEMORY-001` |

**机会**（不阻塞收口）：知识库（个人/部门两级资料空间）；下表其余任务；P2 转入的真实验收欠债。

**排除**：IM 正式环境；问一问 / RAG；DB Gateway；Memory 六层的后四层；Skill CI/CD 完整生命周期；原生业务客户端；卡片多密度渲染；`WorkCandidate` 链路。

> 必达第 1 项仍取决于项目外的输入（OA 审批提交接口结构）。拿到前不猜 endpoint、字段、错误码或回滚语义。

## 2. 未到位的外部输入

| 阻塞项 | 必需外部输入 | 对收口的影响 |
|---|---|---|
| `P2-LOW-RISK-WRITE-001` | OA 审批提交动作的协议结构 | 必达阻塞；未落地则 `P2-GOLDEN-002` 无题可冻 |
| `P2-HIKVISION-ADAPTER-001` | 具体只读用例、现场版本、API/SDK、测试环境、部门共享账号与设备/区域范围 | 机会层 |
| `P2-FEEDBACK-LOOP-001` | 获批的 Scope | 机会层 |

## 3. 现役 DAG

P2 转入的任务保留原编号；详细边界见 P2 PLAN 原行（[现役 DAG](../phase2/PHASE2_PLAN.md#现役-dag未完成任务已完成视觉棒与本次合并关闭的原两行保留为承接说明)）。

| task_id | 层 | 预判档位 | 现状 / BLOCKED |
|---|---|---|---|
| `P2-LOW-RISK-WRITE-001` | 必达 | A | BLOCKED：OA 审批提交接口结构 |
| `P2-GOLDEN-002` | 必达 | A | 依赖上一项 |
| `P2-CHAT-HISTORY-PERSIST-001` | 必达 | A（高风险） | 进行中：施工、三轮评审与两轮真实验收已做，补验后合并 |
| `P2-MEMORY-001` | 必达 | A（高风险） | 依赖上一项合并；设计答案已定 |
| `P3-KNOWLEDGE-SPACE-001` | 机会 | A（预判） | 待开棒：先出方案（个人/部门两级、权限、文档入库），开棒前与雨爷确认范围 |
| `P2-WORK-DISPATCH-LEADER-ROSTER-001` | 机会 | B（高风险） | 可开工；新表迁移只在专用测试库 |
| `P2-ORGDIR-OA-SOURCE-001` | 机会 | B | 可开工；首步真实 OA 只读取数，执行前说明数据流向 |
| `P2-SKILL-CANDIDATE-001` | 机会 | A（预判） | 可开工；只允许人工登记 |
| `P2-TENANT-IDENTITY-001` S2/S3 | 机会 | A | S1 已合并；分段待分配；第二租户禁止启用 |
| `P2-HIKVISION-ADAPTER-001` | 机会 | A | BLOCKED：外部输入 |
| `P2-FEEDBACK-LOOP-001` | 机会 | A（预判） | BLOCKED：Scope 未定 |
| `P2-SEARCH-QUERY-EXPOSURE-001` | 机会 | A（预判） | 只剩核查部署侧访问日志 |
| `P2-RUNTIME-DIRECT-ANSWER-001` | 机会 | A | 独立设计聊天回退终态 |

## 4. P3 新增欠债

### 聊天总览绑定提示 `P2-OA-OVERVIEW-BINDING-HINT-001`（2026-11-20，PR #303）

| 项目 | reason | blocked_by_task_id | activation_task_id | expiry_condition | evidence |
|---|---|---|---|---|---|
| 总览三类身份阻断提示未真实实测 | OA 账号登录会写入 OA 会话，已登录用户自然走不到未绑定；已撤销需撤真实绑定；需选范围需多绑定，环境没有。只有定向测试、Golden（GT-012）与静态评审证据 | 无 | 出现未绑定、撤销或多绑定的真实样本时 | 三类情况分别显示绑定卡（带「去绑定」）或多绑定澄清 | PR #303；仓库外 `overview-binding-hint-20261120/acc/ACC_r1.md` |

### 「我的功能」指定调用按名称核对 `P2-READONLY-SKILL-003`（2026-11-20，PR #305）

| 项目 | reason | blocked_by_task_id | activation_task_id | expiry_condition | evidence |
|---|---|---|---|---|---|
| 改名互换后手输旧名先撤标而非改标 | 新名单里旧名已属于另一功能时，先按「原功能已改名」撤标，没有直接改标；不执行错功能，用户需重选 | 无 | 「我的功能」后续批次 | 该情形直接改标为新功能并拦一次 | 仓库外 `readonly-skill-003-20261120/acc/ACC_r2.md` S1 |
| 输入期标签显示瑕疵 r8 P1-01～04 未修 | 发送核对与服务端名称把关已保证不执行错功能，输入过程中标签显示仍可能短暂不准 | 无 | 「我的功能」后续批次 | 输入期标签与正文一致，或裁定不修 | 仓库外 `readonly-skill-phase2-20261118/review/CODE_REVIEW_astra_r8.md` |
