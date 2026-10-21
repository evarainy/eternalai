# MCP 离线验收与证据边界

本模块提供多服务 MCP 客户端、逐用户 OAuth、业务工具治理与持久恢复。默认没有可用的公司身份断言或输出合同，业务数据调用保持关闭。离线通过不表示公司接口、真实账号、外部网页确认或业务办理已验收。

## 装配与信任边界

- `app/composition.py` 将 `Settings.mcp_services` 装配为固定服务配置；空配置保持既有 OA 路径。服务标识贯穿注册、连接、grant、Capability 映射、授权上下文和操作记录。同一用户的两个服务允许具有同名远端工具。
- `app/infra/mcp/` 使用锁定的 MCP SDK 公开 Transport；支持配置选择的 `2025-11-25` 与 `2026-07-28`，现代模式显式发现。仅 POST，无 GET 恢复、Cookie、重定向、刷新或自动重试；调用前重新校验目录输入摘要、原始安全注解闭集和授权。安全注解缺失、新增或变化均拒绝，注解本身不授予权限。
- `app/ports/mcp.py`、`mcp_store.py`、`workflow_store.py` 是公共插口。秘密只在加密存储与发送边界流转，不进入模型、API 输出、Trace 或前端。每次 HTTP 前校验当前会话、连接、代次和有效期。
- `app/infra/adapters/business_mcp/catalog.py` 提供每服务 19 个描述符：7 读、6 个高风险外层 Workflow、6 个内部高风险 action。函数只生成描述符，不自动发布 Registry。内部叶不参与普通候选选择，直接调用缺少持久许可时拒绝。
- 外层沿 Runtime → Workflow → HumanGate → Gateway → Adapter 执行。操作记录和 checkpoint 由 PostgreSQL 作为唯一恢复依据；发送前 CAS 消耗一次许可并提交 `SENDING`。发送、显式恢复和取消持有同一操作级 PG advisory lock，活跃 `READY` / `SENDING` 返回 busy。进程退出释放锁后，受 CSRF 保护的 recover 将遗留 `SENDING` 转为 `UNKNOWN`；尚未消费许可的 `READY` 仅安全收尾为 `CANCELLED`，旧确认和发送许可不得复活，继续业务须重新发起并取得新的授权。GET 不改变状态，恢复不发写，协调层与 Adapter 均不得盲重试。
- 两个 submit 还必须通过独立 `McpSubmitPreconditionPort`：经核验的本人确认或公司批准的原子拒绝保障，许可精确绑定主体、登录、服务、client、操作、原参数与有效期；消费许可前及实际 HTTP 前均复验。输出 schema、普通 postcondition、本地确认和模型声明不能替代此依据，缺失时不调用 submit。
- 本地确认 TTL 与原制品/任务资格期限分别保存。确认到期不阻断经认证的只读回查；接管与获准重发创建新确认，旧确认不复活，原制品期限不延长。原期限未知或过期不得重新发送。
- `OutputContract` 独立定义模型、界面与持久化投影，并提供成功后态、网页确认解析插口。未批准输出、业务 `isError`、结构错误、目录漂移均不透传远端正文。合成合同仅在显式隔离测试边界使用，不能用于生产放行。

## 13 工具矩阵

全部输入来自同一授权 ZIP 的 full / modern 目录快照；输入摘要及来源映射保留在候选证据中。下表“已实现”仅指离线代码与合成验证，所有行的公司合同与真实业务验收均待确认。

| 工具 | 已实现的入口和约束 | 主要离线证据 | 启用前待确认 |
|---|---|---|---|
| `business_context_get` | 受治理读取、显式服务归属、投影 | 真实生产装配、同用户双服务同名工具、断开互不影响 | 当前用户上下文、权限与隐私字段 |
| `person_find` | 严格输入、受控查询输出 | 七读参数与投影矩阵 | 重名、分页、裁剪及稳定人员标识 |
| `talk_context_get` | 输入与来源字段隔离 | 七读与未知输出拒绝 | FORBIDDEN / EMPTY / TRUNCATED / UNAVAILABLE 的字段合同 |
| `clothing_options_get` | 严格选项输入，不推断库存或资格 | 七读与参数拒绝 | 被服标准、库存与缺货语义 |
| `clothing_result_get` | 原回执查询插口，不将状态等同实物交接 | 七读及原制品恢复 | 回执状态、本人权限与完成后态 |
| `talk_record_get` | 正式记录、来源与计次的分用途投影 | 七读及提交后的原记录恢复 | 字段来源、计次证据、权限裁剪 |
| `talk_tasks_list` | 严格分页/筛选，上海自然周辅助函数 | 七读、领取后 mine 回查 | 空池解释、规则实例与任务状态 |
| `clothing_plan_preview` | 本地确认、日期/条目校验、非幂等创建保护 | 六写真实 SDK/PG、直接调用拒绝、进程终止矩阵 | 草稿回执及已保存/未保存证明 |
| `clothing_plan_submit` | 原制品参数绑定、外部确认插口、原回执恢复 | 本地/外部确认、恢复后重新确认、重复发送计数 | 网页确认与 submit 原子拒绝保障 |
| `talk_preparation_save` | 准备保存，不自行宣称计次；UNKNOWN 禁再创建 | 六写与进程终止矩阵 | 准备回执、定位原制品的充分依据 |
| `talk_record_draft_save` | UTF-8 字节、时间/参与人校验；绑定任务使用获批规则插口 | 输入边界、规则周/人员不匹配零发送、六写恢复 | 纪要真实性、合格参与人、实际规则实例 |
| `talk_record_submit` | 绑定原记录参数；网页确认与正式记录回查 | 六写、外部确认、原记录恢复、进程终止 | 正式回执、共享计次与真实成功判据 |
| `talk_task_claim` | 原任务/version/本人绑定；UNKNOWN 先查 mine | 六写并发、原参数恢复、进程终止 | 冲突、本人重放、领取回执 |

## 可重复的离线验证

后端定向文件位于 `tests/infra/mcp/`、`tests/mcp/`、`tests/infra/adapters/business_mcp/`、`tests/workflow/test_mcp_recovery.py`、`tests/runtime/test_mcp_composition.py`、`tests/api/test_mcp.py`、`tests/infra/persistence/test_mcp_store.py`、`tests/db/test_mcp_migrations.py`。按现役 backend skill 运行最近路径，并运行 Gateway port、架构、依赖、弱测试、Golden、Ruff 和 mypy。

持久化夹具采用项目既有 Windows Selector / psycopg 接缝，DB fixture 实际 scope 为 module。完整运行必须使用固定测试库 `127.0.0.1:15432/eternalai_test`，缺失时失败。两份 MCP 迁移父链为既有 `20260922_190000` → `20260930_090000` → `20260930_100000`；测试覆盖升级与隔离事务内回退，不能外推正式数据库迁移权限。

六写的进程故障用例分别在“READY 已持久化但未消费许可”、“SENDING CAS 已提交但未 HTTP”与“HTTP 已有副作用但结果未持久化”终止真实子进程，另起进程读取同一数据库，再恢复引擎并核对发送次数。它们与一般引擎重建、内存假实现测试分开记录。循环回放、重复确认、并发恢复与换登录不能产生新的未授权发送。

前端 `/apps` 提供连接授权/断开与本人原操作列表；聊天返回受控原操作入口。面板显示动作、服务及 Capability 允许展示的参数摘要，每次操作绑定服务端摘要和 revision，不自动重试 UNKNOWN。API 变更运行 `pnpm --dir web test:openapi`；每个 `.test.tsx` 单独进程，完整覆盖使用 `pnpm --dir web test`，并执行 lint、typecheck、build。

API 为 `/api/v1/mcp` 下 services、connections、authorize、OAuth callback、disconnect、operations 列表、operation GET 和 resume 八个方法。身份来自当前认证会话，修改动作沿用 CSRF；回调必须同时匹配单次 state、issuer、回调归属和当前登录。上游真实 401/403（包括无请求 ID 的 initialized 通知）立即终止当前传输并仅持久失效匹配代次的连接/grant，后续 list/call 不再发送，旧响应不得撤销新授权。网页授权/业务审核链接只显示经固定来源校验的 URL，不自动跳转。

## 结果与限制的记录方式

每次交付的精确命令、计数、初次失败、修复复核、源文件摘要和候选 SHA 放仓库外证据及 PR；本文不是逐棒日志。证据分为：合同静态事实、未获公司签收的合成样例、真实本地 SDK/HTTP/PG/生产装配、真实供应商验收。后一项不能由前三项代替。

本次开发曾有固定测试库连接敏感值出现在工具会话诊断，未进入源码、commit 或 PR；既有会话暴露未撤回，凭证未轮换。官方后台测试入口会写原始诊断日志，其安全输出补丁两次被自动审批拒绝，未修改该脚本。本次经主控授权，在官方环境诊断通过后使用仓库外内存捕获 wrapper：早期等价全量执行 `uv run pytest -q`，最终完整回归执行 `uv run pytest -q -p mcp_failure_observer`，显式附加只读失败位置观察器。两者保持默认全部测试、顺序及断言，仅按获准方式调整诊断展示与安全观测，并记录实际命令、退出码和计数；未运行官方 `--start-full-tests` 后台入口。此方式仅约束本次验证，不表示全部诊断工具已修复。后续处理由 owner 在明确授权范围内安排。

公司问题及默认关闭范围见 [公司确认台账](mcp_company_questions.md)，上线步骤见 [启用清单](mcp_activation_checklist.md)。原 OA 审批提交阻塞、Golden 现场验收和第二租户禁止启用均不由本模块解除。
