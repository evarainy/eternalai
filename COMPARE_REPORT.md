# P2-AUDIT-OA-TODO-CONVERGE-001 对比报告

VERDICT: DONE

本地 P 段实现与本阶段允许的验证已完成；不是审计 #6 收口，也不是已通过数据库及独立审查的候选。2026-09-16 补充裁定明确既有测试适配不受 §5.2 生产文件清单限制，本次续跑已解除 B1/B2。原建议补丁经复核后按相同语义应用，前端对象采用逐字段排版。未新增默认字段、旁路调用或弱化断言。

## 范围、基线与交付状态

- A 档；本工作树串行实现，与其他候选隔离；不承担 A 类同步，不改 STATUS / PLAN / DECISIONS。
- 工作树：`E:/code/eternalai-wt-OAT-ASTRA`；分支：`phase2/P2-AUDIT-OA-TODO-CONVERGE-001`。
- 开工 HEAD 与本地 `origin/phase0/main` 均为 `6c8f27173c1a866b18113de17baf4080bbb37301`；未 fetch，不声称已验证远端最新性。
- 当前提示词优先于方案历史“只出方案”文字；先读文末 13 条裁定，再读方案正文。
- 本地提交保留候选与本报告，提交 SHA 见交付回复；未 push、未开 PR、未合并，未运行 Monitor / 静态评审桥。
- 本次从上一候选 `6e696003d4917b69c14e2bd2933384d5c866c8c6` 继续；只新增两处测试适配及报告更新，生产实现没有再次改动。追加普通候选提交，不 amend，不改写既有提交。
- 新迁移仅写文件，未执行任何数据库连接、迁移命令、后端全量或 Golden；未读取 HAR、`_scratch/oa/`、凭证或环境文件内容。测试入口按授权自行加载环境。

## 上次阻塞的依据、合同分析与本次解除

### B1：既有架构守卫写死了旧列表接缝

- 方案 §3.3（原计划第 95 行）：`store增加 list_with_oa_sync_for_scope(scope, *, search_term, oa_view, limit) -> WorkObjectReadBatch`；`服务端列表改用新batch方法，不能两个独立事务拼出“新时间＋旧行”`。
- 现役 `tests/architecture/test_work_object_scope_boundary.py:136`：`("list_for_principal", "list_for_scope", 0)`；第 152 行要求 `assert len(calls) == 1`。
- 实际结果：新列表方法传入同一个已授权 scope，但守卫查旧方法名，得到 `assert 0 == 1`。架构集 124 passed / 1 failed。
- 最小适配：只把守卫中的调用名换成 `list_with_oa_sync_for_scope`，继续断言恰好一次调用、scope 参数位置与变量名，以及全部禁止依赖/越界调用断言。
- 上次把该文件不在 §5.2 清单内判为范围歧义，故只准备补丁。补充裁定已明确这是主控提示词的测试范围歧义，非实施判断错误，本次停手和续派不计自主完成度扣分；生产文件边界不变。
- 本次仅把方法名映射更新为 `list_with_oa_sync_for_scope`。AST 比对确认该文件 **26 条 assert 完全不变**，包括恰好一次调用、`scope` 在第 0 参数位、详情/标记原参数位、禁止 assignee 接缝与 infra 依赖。未伪造旧方法调用，未改 scope 授权设计；整个架构集 **125 passed**。

### B2：清单外视觉测试仍构造旧 DTO

- 方案 §3.3（第 91、93 行）：`OAWorkObjectView新增必填 oa_observation`；`WorkObjectListResponse ... 新增必填 oa_sync: OASyncStatusView`。
- 现役 `web/src/app/__tests__/blurBudget.test.tsx:66` 的 `workObjectList(): WorkObjectListResponse` 构造器没有这两个字段。
- 实测单用例：`keeps the work-objects screen on the three stationary surfaces` 失败，`TypeError: Cannot read properties of undefined (reading 'pending_state')`。其余 18 项是 `-t` 未选中项，未添加 skip。
- 本次只在 `workObjectList()` 夹具补合成 `oa_observation` 与 `oa_sync`，采用与原 `source_fetched_at` 一致的时间及合法 current/succeeded、revision=1 状态；所有 blur / DOM / 可见性断言保留。机械核对确认移除这两个新增字段块即与上一候选字节一致（统一换行后），完整文件 **19 passed**。
- 不能通过把公共 DTO 改为可选，或缺字段时默认为 current，绕过已批合同。

本次允许范围内的验证没有出现其他同类既有测试失败，因此没有新增测试适配。上次关于“不能用放松公共合同换绿”的分析保留；补充裁定解除的是测试改动范围歧义，不是降低验收标准。

## 按文件改动摘要

| 文件 | 承担的合同 |
|---|---|
| `app/ports/work_object.py` | §3.2–3.3：严格冻结 subject/ticket/status/view/observation/read batch；default 范围；必填观察；代次、时间和失败码一致性；完整集合严格布尔校验；保留正向兼容方法。 |
| `app/infra/persistence/work_object/postgresql.py` | §3.2：短事务分配票据；锁状态行、校验代次和归属；同事务 upsert 全集、消失对账、发布；空批次不短路；迟到失败不覆盖成功；提交确认丢失分类；§3.3：一致读取批次，list/mark 局部 REPEATABLE READ，详情 JOIN；所有过滤先于稳定排序和 LIMIT。 |
| `app/api/v1/work_objects.py` | default 在线/后台共用 begin→Gateway→完整集合→apply；原 HTTP 和后台分类保留，新增 oa_sync_superseded/outcome_unknown；非 default 保留正向兼容；oa_view 默认 active；历史详情只读；未新增端点，无现有响应头变更。 |
| `alembic/versions/20260915_120000_oa_pending_reconciliation.py` | §3.5：仅两张 P 表及索引；按裁定使用 last_attempt_* / last_error_code；不回填旧行；downgrade 先锁表并拒绝有数据或存在 D 表的回退；parent=20260914_120000。 |
| `web/src/pages/WorkObjectsPage.tsx` | §3.3：独立历史 query 和待办页折叠区、各自 overflow；空批次时间、状态提示、未核对标签；批次不一致显示对齐提示；观察/快照/痕迹分开合并，迟到 mark 不能恢复办理；保留 auth generation；刷新失效列表、历史、搜索与详情；结果未知后 GET 重读。 |
| `web/src/features/work-dispatch/WorkObjectSearchPage.tsx` | 显式 oa_view=all；query key 包含视图；历史状态标签；说明默认 active 不含未再确认记录。 |
| `web/openapi/work-objects.openapi.json` | 从真实 FastAPI OpenAPI 仅导出 workObjects 路由子集；必填字段和查询参数。 |
| `web/src/generated/work-objects/work-objects.schemas.ts` | Orval 派生的新 DTO/枚举/参数。客户端 `work-objects.ts` 同时再生，最终与基线字节一致，没有语义 diff；未覆盖其他项目生成物。 |
| `tests/ports/test_work_object_port.py` | 严格集合、重复/计数、布尔、subject、ticket、观察状态/时间、批次 metadata 的合法和病态值。 |
| `tests/api/test_work_objects.py` | 显式更新内存 store；HTTP 空批次、历史/搜索默认、重现、痕迹、部分失败、unknown outcome、begin 失败、后台无目录/list；另写真实 PG HTTP、提交确认丢失与非 default 旁表隔离用例（未运行）。 |
| `tests/infra/persistence/work_object/test_postgresql_work_object_store.py` | 新增真实 PG 的 205 条旧行空对账、A/B→B/C→A、三处回滚、并发新旧票据、时钟回拨、批次同一读快照、LIMIT 前过滤及错误观察归属用例（未运行）。 |
| `tests/db/test_oa_pending_reconciliation_migration.py` | 真实迁移空表往返、无回填、CHECK/NULL/FK、非空拒退、writer 与 downgrade 锁顺序测试（未运行）。 |
| `tests/test_credential_polling.py` | 共用服务真实本地分类：superseded/storage/unknown 不记成功、不消耗网络失败计数、不误报认证失效。 |
| `web/src/pages/__tests__/WorkObjectsPage.test.tsx` | 区分历史与当前请求的测试桩；保留旧安全/痕迹/内部事项断言；新增空成功时间、历史只读、迟到 running、迟到 mark 收窄测试。 |
| `web/src/features/work-dispatch/__tests__/WorkObjectSearchPage.test.tsx` | 原搜索规范断言保留并要求 all；新增历史标签及默认视图说明。 |
| `web/src/api/__tests__/apiClientsOpenapi.test.ts` | 保留真实导出和字节级再生测试；补必填字段、内部臂不带观察、状态字段闭集、active 默认值及 P 不装配 D 路由断言。 |
| `tests/architecture/test_work_object_scope_boundary.py` | 本次续跑：列表 store 方法名适配新接缝，全部 26 条 assert 不变；scope 位置、调用次数及禁止旁路的安全意图不变。 |
| `web/src/app/__tests__/blurBudget.test.tsx` | 本次续跑：只补 DTO 夹具必填 observation/status；所有 19 项视觉结构测试和断言不变。 |
| `COMPARE_REPORT.md` | 本报告、未决项和 D 段五字段义务。 |

未改 CSS：复用现有列表样式和组件。未改变部门派发授权、凭证 failure_code 类型、目录 error 类型、全局 session isolation、Golden fixture 或运行时能力闭集。

## 实际验证命令与结果

所有详细输出位于本工作树 `.compare_evidence/`。完整后端 nodeid 参数清单（含未运行的连库节点）见 `test-selection.json`。

本次续跑实测如下；下一张表保留上次原始验证结果，不能把已解除的历史失败误读为当前失败。生产文件未改且原接线恢复 SHA256 仍匹配，因此不重复运行已经通过、与本次两处适配无关的检查。

| 本次实际命令 | 结果与证据 |
|---|---|
| `uv run pytest tests/architecture/ -q -o cache_dir=.compare_evidence/pytest-cache` | **125 passed in 12.50s**；`resume-architecture.log`。 |
| `pnpm --dir web exec vitest --run src/app/__tests__/blurBudget.test.tsx` | 独立进程，**19 passed**；`resume-blur.log`。进程 TEMP/TMP 指向本树 `.compare_evidence/temp`。 |
| `uv run python scripts/check_weak_tests.py tests/architecture/test_work_object_scope_boundary.py` | **Weak-test check passed**；`resume-weak-tests.log`。 |
| `uv run ruff check .` | **All checks passed!**；`resume-ruff.log`。 |
| `uv run python -c "exec(open('.compare_evidence/resume-adaptation-audit.py.txt', encoding='utf-8-sig').read())"` | **PASS**；`resume-adaptation-audit.json`：26 条 AST assert 不变，前端仅两字段块增加，9 个生产/生成文件与前候选一致，两份接线恢复 SHA256 与实物一致。 |
| `git diff --check`、`git diff --cached --check`、`git ls-files --others --exclude-standard` | diff 检查通过；本次提交仅两测试和本报告；本树证据有意保留，逐项见 `retained-files.json`。 |

| 实际命令 / 入口 | 结果与证据 |
|---|---|
| `uv run pytest tests/ports/test_work_object_port.py <41个不连库API/轮询test函数nodeid> -q -o cache_dir=.compare_evidence/pytest-cache` | **80 passed**，`backend-unit-final.log`；按 fixture 参数排除所有 dispatch_db/migrated_database_url 依赖，非运行后跳过。初轮 76 passed，新增覆盖后 80。 |
| `uv run pytest tests/architecture/ -q -o cache_dir=.compare_evidence/pytest-cache` | **124 passed, 1 failed**，`architecture.log`；B1 原文见上。观察器自身 PASS 不等于架构测试全绿。 |
| `uv run ruff check .` | **All checks passed**，`ruff-final.log`；早期新代码有 import order/行宽问题，仅改授权文件新增/相关行；原始汇总 `ruff-initial.log`。 |
| `uv run mypy app/ --cache-dir .compare_evidence/mypy-cache` | **Success: no issues found in 128 source files**，`mypy-final.log`。 |
| 对五个改动 Python 测试文件逐个 `uv run python scripts/check_weak_tests.py <file>` | **5/5 passed**，逐文件记录在 `weak-tests-final.log`。 |
| `pnpm --dir web exec vitest --run src/pages/__tests__/WorkObjectsPage.test.tsx` | **30 passed**，`work-page-serial.log`；之后仅加强新迟到 mark 测试的延迟重读控制，该用例单独恢复绿，见接线证据。 |
| `pnpm --dir web exec vitest --run src/features/work-dispatch/__tests__/WorkObjectSearchPage.test.tsx` | **23 passed**，`search-page-2.log`。 |
| `pnpm --dir web test:openapi` | **4 passed**，`openapi-final.log`。运行时 TEMP/TMP 定向到 `.compare_evidence/temp`；全目标字节对比只在该临时根生成，不覆盖仓库其他目标。 |
| `pnpm --dir web exec vitest --run src/app/__tests__/blurBudget.test.tsx -t 'keeps the work-objects screen on the three stationary surfaces'` | **1 failed / 1 error**，18 项未选中；`blur-blocker.log`，B2 原文见上。 |
| `uv run python -c "exec(open('.compare_evidence/export_work_objects.py', encoding='utf-8').read())"`、`pnpm --dir web exec orval --config orval.config.ts --project workObjects` | 真实导出/单目标再生成功；helper 后移为 `.py.txt` 保留，生成物统一 LF，通过字节比对。 |
| 纯 AST 解析迁移 revision/down_revision | 新唯一 head `20260915_120000`，parent `20260914_120000`；`migration-static.json`；没有执行迁移或接触 DB。 |
| `git diff --check`、`git ls-files --others --exclude-standard` | whitespace 检查通过；逐项保留清单见 `retained-files.json`。Git 行尾转换提示不等于 diff 错误。 |

失败过程没有省略：待办页初轮 17 failed / 10 passed，第二轮 6 failed / 24 passed。根因是新增视图/重读导致单次测试响应被耗尽、旧 freshness 断言仍取条目时间，以及双表定位/异步详情尚未完成；适配稳定合成状态和精确新 query key，未降低超时、断言强度或删除用例。第三轮同时运行多组校验时，首项超过既有 5000ms，29 项通过；单独运行页面时 30 项通过。OpenAPI 初轮一项同样 5000ms 超时、另一项生成物 CRLF/LF 字节不一致；修复本棒生成物 LF 后，单独运行 4 项通过。没有提高 timeout/retry，没有将一次绿描述为已修复平台性能问题。React Router future flag / jsdom NaN style 警告如实留在日志。

## 接线反证：只回退生产代码，测试不动

1. **HTTP 共用同步接缝**：仅把 default 分支 `apply_oa_pending_snapshot` 调用替换回旧 `upsert_oa_pending_workflows` 正向同步；其余生产类型、测试与断言保持不变。`test_complete_empty_snapshot_history_search_default_and_reappearance` 从绿变红：`assert result.json()["items"] == []`，实际仍有旧待办。恢复原始字节后 **1 passed**。日志 `wiring-backend-red.log` / `wiring-backend-green.log`，恢复 SHA256 与实际命令在 `wiring-backend.json`。
2. **迟到 mark 办理资格**：仅把 `mergeWorkObjectView` 恢复为基线只按 source/handling 时间合并的函数，保留新测试；测试故意延迟后续 GET，避免重取掩盖短暂错误。断言变红：**expected 'self_serve' to be 'view_only'**。原始日志显示 pending_state 断言仍通过，失败的是办理动作；不是把该次结果宣称为 pending_state 变为 current。恢复原始字节后 **1 passed**。见 `wiring-frontend-red.log` / `wiring-frontend-green.log` / `wiring-frontend.json`。
3. 上述后端反证为真实 HTTP→共用服务→内存 store 接线，**不冒充真实 HTTP→PG 原子事务证据**。真实 PG 的对应测试已写，必须由中立验证方后续执行。

## 未运行项与原因

- 所有连库测试、两个真实 PG 测试模块、API/轮询中 13 个连库测试函数：本阶段硬禁任何数据库连接。它们负责证明 SQL 全集对账、隔离、事务回滚/确认丢失、读一致性、迁移与回退，不以本轮内存测试代替。
- `alembic upgrade/downgrade/current/check`、全量入口 `check_dev_environment.py --start-full-tests`、后端全量、Golden：任务明确禁止。没有 `--ignore=` 降级全量；只选择已批准的无 DB 定向测试。
- 前端全量、lint/typecheck/build：本阶段允许验证清单列的是定向 Vitest；未自行扩大命令集。B2 已补齐必填 DTO 夹具且完整 blurBudget 通过，没有宣称全仓 TypeScript 已通过。
- 真实 OA / 浏览器现场 / D 接源：缺少本轮具体现场授权与 D 外部输入；未执行。
- Monitor、静态评审桥、CI、push/PR/merge：当前任务明确留给后续。未将自查或测试绿替代独立结论。

## 已知风险及 D 段欠债

- B1/B2 已按补充裁定解除，P 在本阶段实现及允许验证范围内可交付候选；原建议补丁作为历史准备证据保留，不代表尚有待应用改动。数据库验证、Monitor/静态桥仍待后续阶段，不以本报告 DONE 替代其验收。
- SQL 和迁移未经真实 DB 执行，尤其并发、提交确认丢失、CHECK 与锁排除测试只有代码，没有本轮实测 PASS。
- default 旁表与旧 OA `tenant_id IS NULL` 共存。非 default 同 ai_user_id 仍可能更新旧 OA 行；旁表不变不证明第二租户安全，原 P2-TENANT-IDENTITY-001 欠债不关闭。
- 上游分页完整性不等于 OA 数据库跨页原子快照；无真实现场消失/重现验收。200 条仅有界展示，不声称完整业务总数。

| 义务 | reason | blocked_by_task_id | activation_task_id | expiry_condition | evidence |
|---|---|---|---|---|---|
| D 可信办结协议与语义输入 | 当前可用协议为 pending/system_messages；没有经确认的流程办结终态、完成时间/时区、todo_id 映射、分页窗口、撤销/重开/ref 复用合同。禁止把消失、个人已处理或 Gateway completed 当办结。 | 无已登记输入任务；阻塞于经确认的脱敏结构合同提供，不虚构 task_id。 | P2-AUDIT-OA-TODO-CONVERGE-001 的 D 部分；拆棒由 GOV-SYNC 分配。 | 输入补齐，冻结 provider/adapter/descriptor 映射及传输矩阵；真实受控读取→PG→HTTP→done 验证通过，完成与个人已处理清晰区分。 | 已批方案 §3.4、§7.1–7.2 与末尾裁定 11；本候选没有新增 D 表、能力、路由或伪造完成计数。 |
| 真实 OA 观察一致性及完成列表现场验收 | P 总数/分页校验不证明上游原子快照；本轮是合成边界验证，未真实读取或办理。 | 无已登记确定 task_id；需具体账号/环境/只读与持久化授权以及 D 输入。 | 本任务验收或 GOV-SYNC 分配的受控验收任务。 | 获授权后核对完整空/变化、时序与已办终态对应，留下脱敏最小证据。 | 已批方案 §4、§7.1；本报告未运行项。 |

审计 #6 的 done_when 保持 P+D，不能因为本阶段只施工 P 而缩小。已完成分类仍显示“—”，无新增可信完成数据源，未核销该欠债。

## 临时产物、隔离与 token 自估

- 自建 helper、日志、备份、补丁和清单均保留在 `.compare_evidence/`；逐文件用途在 `retained-files.json`。helper 后缀改为 `.py.txt`，避免临时脚本混入仓库 `ruff check .`；没有修改 lint 配置。
- 现有依赖由工具按锁文件建立本 worktree 的 `.venv` / `web/node_modules`，未修改依赖清单或锁文件，未安装全局依赖。未扫描/清理 `.venv`。
- 未读取其他 OAT 工作树；未修改主工作树文件或共享治理文档；没有删除工作树外文件，没有 rebase/reset/force 操作。
- 无可取得的真实 token 总量；以下是**上次实现 + 本次续跑累计估计占比**，不是计费统计：读取仓库与方案 32%；思考与规划 16%；编写修改代码 27%；运行测试与读取输出 11%；排错返工 9%；撰写报告 5%。上次只记录了占比（30% / 17% / 30% / 10% / 10% / 3%），无法据此恢复可靠绝对 token 数；本次读取续派/裁定及方案、测试适配、验证和报告开销已计入累计占比估算。
- 本次新增 `resume-architecture.log`、`resume-blur.log`、`resume-weak-tests.log`、`resume-ruff.log` 为实际验证输出；`resume-adaptation-audit.py.txt` / `.json` 为适配强度与既有接线证据复核；`resume-candidate.json` 记录最终候选 SHA 和无 push 状态。保留上次 `candidate.json` 的 BLOCKED 历史，不覆盖为本次状态。
