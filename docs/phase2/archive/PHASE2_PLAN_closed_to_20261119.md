# Phase 2 已结项与历史计划（截至 2026-11-19）

> 原文逐字底稿见同目录的 [源文本](PHASE2_PLAN_closed_to_20261119.md.source)。本页调整了归档后的链接。

> 历史原文按需查阅，现役口径见其来源文档。

### 开发助手路由落地的剩余义务（2026-09-23）

| 项目 | reason | blocked_by_task_id | activation_task_id | expiry_condition | evidence |
|---|---|---|---|---|---|
| Fable 5.1 在本账户需额外用量额度 | 2026-09-23 冒烟：`claude-fable-5-1` 返回需要 usage credits（HTTP 429），当前主控与 plan 编写按回退链由 Opus 5.5 xhigh 承担 | 外部输入：Claude 账户 Fable 用量额度（雨爷） | 无（不需新棒） | Fable 5.1 冒烟调用成功，或雨爷改定主控模型 | 本棒 PR 验证段的冒烟记录 |


### 已结项摘要（现役规则或守卫仍生效）

| 已结项事项 | 仍生效的规则 / 测试 / 守卫指针 |
|---|---|
| 前端首屏 JS 体积随 `@ant-design/x` 增长并触发 Vite chunk 警告 | `P2-FE-BUNDLE-SPLIT-001` 按第一条 expiry path 关闭：路由级 `React.lazy` 将壳层、所有叶页与未打开 Dock 移出入口静态闭包；认证后才加载 `AntApp`，Dock 只在首次显式打开后常驻。带 manifest 的构建入口静态集合为 337,124 B JS + 9,657 B CSS，`index.html` 无未访问页 modulepreload，壳层、所有叶页与 Dock 均为 dynamic entry，且 Vite 无默认 500 kB chunk 警告；`/chat` 自动加载集合另行去重报告，不冒充入口下载量。`web/src/__tests__/App.test.tsx`、`web/src/app/__tests__/AppShell.test.tsx` 与 `blurBudget.test.tsx` 锁住加载/失败、401 迟到模块、首开常驻和原 6 层预算。未新增依赖、未改 Vite 阈值；目标 ARM 终端性能仍未实测。 |
| 没有服务端注销端点，退出登录只清前端状态 | `P2-AUDIT-LOGOUT-002` 已按 expiry_condition 关闭：真实 HMAC+PG 当前票据提交后吊销、同票据/签名编码等价别名重放为 401、其他会话保留；前端服务端确认、失败反馈、三时点旧数据/旧键清理均已实证。`tests/api/test_auth.py`、`tests/runtime/test_runtime_composition.py`、`web/src/__tests__/App.test.tsx`、`web/src/pages/__tests__/ChatPage.test.tsx` 及生产反证锁定合同；独立审查与集成状态见 `STATUS.md`，不宣称生产部署。 |
| 生命周期已吊销票据与注销后原 key 重放验证 | `P2-AUDIT-LOGOUT-002` 已取得真实注销链实证：三个生命周期端点拒绝旧票据，原已提交 key 重放与事件读取均为 401、业务 SQL 零增量、事件数不变；同主体未注销票据仍可读和合法重放。依据 `tests/api/test_work_object_lifecycle.py::test_logout_replay_of_original_key_cannot_read_event`、生产装配全路由用例及断开认证读库后的反证。按既定 expiry_condition 关闭该验证义务，独立审查与集成状态见 `STATUS.md`。 |
| OA 返回 `jobtitle: 0` 会中止整份组织目录快照 | `P2-ORGDIR-PERSON-SYNC-001` 按本次人工 R5 裁决把整数/文本零归一为无岗位；缺失/null/空串同义，其他非法值整批拒绝。importer→真实 PG→HTTP 验证科室/监区零岗位同部/跨部派发与重放均拒绝、候选拒绝；有权负责人仍可选无岗位目标。无岗位经真实 Store 保留现役本人/本部门查看，跨部/跨租户不可见且本人处理边界不扩大。该兼容欠债按已批语义与回归关闭；未声称真实 OA 采集已验收。**evidence**：`tests/infra/organization_directory/test_importer.py`；`tests/api/test_work_object_dispatch.py::test_zero_jobtitle_imported_to_pg_denies_dispatch_and_replay`；`tests/infra/persistence/work_object/test_postgresql_work_object_store.py::test_no_job_keeps_only_existing_visibility`。 |
| `monotonic_deadline` 严格递增断言偶发失败 | `P2-TEST-MONOTONIC-DEADLINE-001`：测试夹具在首次请求前接入可控 UTC / 单调时钟，两次确认间明确推进 1 秒；保留严格 `>` 并校验两个 deadline 的增量。**evidence**：`tests/runtime/test_runtime_user_action.py::test_second_structured_confirmation_uses_fresh_claim_and_succeeds` 修复前隔离重复跑 28/30 失败，修复后 30/30 通过；冻结单调时钟时严格递增断言变红，恢复后变绿；弱测试检查通过。该欠债按 expiry_condition 关闭；整文件验证另有已登记的 `InMemoryHumanGate` 幂等语义欠债失败（基线同样复现），不在本条关闭范围。 |
| 顶栏「部门 / 姓名」与用户头像无数据源 | `P2-USER-PROFILE-READ-001`：顶栏一行显示 `部门 / 姓名`，姓名取自服务端签名的会话票据（`Principal.display_name`），部门取自后端解析的 `orginfo`；头像走后端代理 `GET /api/v1/me/avatar`，前端只看到常量路径。取不到部门时顶栏只显示姓名且不加一个字的提示，取不到照片时退回姓氏首字、不留空位。`web/src/app/__tests__/AppShell.test.tsx` 覆盖真实值、缺部门、缺照片、`<img>` onError 与「后端未答复时不写占位」五条分支。 |
| 当前用户身份读取接口缺失 | `P2-USER-PROFILE-READ-001`：新增 `app/ports/user_profile.py` 契约与 `GET /api/v1/me`、`GET /api/v1/me/avatar` 两个零参数端点。未认证一律 401；OA 侧任何失败都不改变 `authenticated`，只体现在闭集 `org_status`；`orginfo` 原始 HTML 一个字节都不进响应。`tests/api/test_me.py`、`tests/infra/adapters/oa/test_profile_adapter.py`、`tests/infra/adapters/oa/test_orginfo.py`、`tests/ports/test_user_profile_port.py` 锁死认证、隔离、SSRF 与脱敏。 |
| 刷新页面丢失登录态并被重定向回 `/login` | `P2-USER-PROFILE-READ-001`：`authStore` 增加第三态 `unknown` 并以其为初值，启动时由 `useIdentityBootstrap` 向 `GET /api/v1/me` 确认一次；确认返回前 `ProtectedRoute` / `LoginRoute` 都渲染 `BootGate`，既不放行也不重定向。恢复以**后端确认**为准，前端不做任何持久化（`web/src/stores/authStore.test.ts` 以 `Storage.prototype.setItem` spy 断言零次写入）。后端不可达时停在 `unknown` 并给一行「连不上服务器」+「重试」，不翻译成「你没登录」。 |
| Top-K 方案恢复及剩余账目待治理 | 2026-09-20 本批在 `DECISIONS.md` 登记已批 §3.8 恢复，沿用 2026-09-16 R-a/R-b 对 §3.6.1/§3.1.4 的替代；b1/b2/b3 分项核对如下，文本误伤、b4 与组织 metadata 保留。治理登记此前滞后，不能倒写为集成前已落盘。 |
| 管理员 prompt-safe 校验旧债事实待结项 | b1 按 expiry_condition 关闭：已合并 `P2-AUDIT-CAPABILITY-TOPK-001` 保留 CapabilitySpec/Admin NFKC、长度/分隔符拒绝及前端镜像规则，补消费侧全量重校验；该棒 PR body 的 Registry/Admin 与前端定向记录、`tests/infra/persistence/capability_registry/test_capability_spec_validation.py`、`tests/api/test_admin_registry.py`、`web/src/pages/admin/__tests__/RegistryPage.test.tsx` 为依据。文本子串误伤另保留。 |
| Planner 摘要恢复待治理闭合 | b2 按 expiry_condition 关闭：安全摘要已随 Top-K 合并，`_project_contract` 的 allowlist/value-free 守卫及 `tests/runtime/test_intent_router.py` 锁定合法摘要与恶意原文隔离；2026-09-20 `DECISIONS.md` 补登已批恢复，2026-09-16 R-a/R-b 保持有效。依据：Top-K PR body、`tests/knowledge/test_capability_selection.py::test_summary_allowlist_is_complete_and_value_free`。 |
| Capability 字典序截断修复待集成 | b3 按 expiry_condition 关闭：Top-K 已合并，K=8、单条 4096/总 16384 bytes、完整同分组和低置信度状态已有回归；第九能力正式 HTTP 接线及生产回退反证留该棒 PR。依据：`app/knowledge/capability_selection.py::select_capability_candidates`；`tests/runtime/test_runtime_api.py::test_authenticated_handle_reaches_ninth_capability_through_topk`。 |
| 本棒 D-6 有限替换及旧债归档待 GOV-SYNC 登记 | `P2-AUDIT-LIST-ORDER-001` 已合并；本批 `DECISIONS.md` 原样收录获批方案 §3.5 全文，`ARCHITECTURE.md` D-6 补例外指针，可见集合债据实现关闭。cursor 分页与第二消费者的原 owner/激活条件保持；本登记义务按 expiry_condition 关闭。 |
| Work Object 列表截断的可见集合不确定 | 已合并 LIST-ORDER 满足确定排序的 expiry_condition。当前入口为 `app/infra/persistence/work_object/postgresql.py::_list_records`；`tests/infra/persistence/work_object/test_postgresql_work_object_store.py::test_bounded_list_orders_mixed_authorities_before_limit` 及 SQL/搜索/边界回归证明过滤后排序再取前 200，去掉 ORDER BY 的反证留实现证据。第二消费者旧激活前置已由明确排序授权提前解除，分页债不随之关闭。 |
| `WorkflowEngineAdapter` 在真实生产入口从未被实例化 | `P2-AUDIT-WORKFLOW-WIRING-001` 已合并（PR #196），满足生产装配及断言的 expiry_condition；`app/composition.py::build_production_components` 构造真实引擎并传入 build_runtime，`tests/runtime/test_runtime_composition.py::test_production_components_share_real_gateway_with_orchestration` 断言共享 adapter/engine 和概览定义，`tests/runtime/test_production_workflow.py` 覆盖生产入口。后续 #11 已合并首例核验，不代表真实 OA 验收。 |
| 内部派发非监区默认跨部门放权 | 仅修复部分结清：DEPT 已合并，显式允许集合、候选/POST/重放共用判定和非阻断诊断落地，原默认方向由本批 DECISIONS 替代。可信类型与人工维护残余仍在活债表；不宣称原整条 expiry_condition 已满足。 |
| Work Object 全局搜索服务端检索 | `P2-WO-SEARCH-SERVER-001`（原欠债 `activation_task_id`）：`GET /api/v1/work-objects?q=` 在 `assignee_ai_user_id` 主体谓词后按标题子串、来源编号等值、责任人等值收窄，参数化 SQL 继续 `LIMIT 201` 且无 `ORDER BY`；超限提示明确未排序、具体 200 条可能变化。`tests/infra/persistence/work_object/test_postgresql_work_object_store.py` 以真实 PostgreSQL 覆盖三字段与跨主体变异反证；表无租户列，租户不同且用户 ID 不同时的不可见性来自主体 ID 隔离，不构成独立租户谓词保证。 当前默认选集已由 LIST-ORDER 替代，见 `DECISIONS.md` 2026-09-20 D-6 有限替换；本行前述无排序描述仅为原交付历史。 |
| 页面上下文注册失败降级 | `web/src/app/usePageContextRegistration.ts` 只负责在 effect 中转调；`web/src/stores/aiDockStore.ts::registerPageContext` 在实际解析边界把 `PageContextValidationError` 降级为无上下文和用户可见提示，其他异常仍抛出；`web/src/pages/__tests__/WorkObjectsPage.test.tsx` 覆盖非 `Z` 时间戳与 15/18 位数字凭证形状来源引用。 |
| Dock 页面上下文提示与绑定一致 | `web/src/stores/aiDockStore.ts::registerPageContext` 在干净重绑时清除旧 notice；`web/src/app/__tests__/AppShell.test.tsx` 覆盖「事项页 → 落地页 → 再进事项页」。 |
| 通用会话剥离页面绑定并可回到 page 模式 | `web/src/contracts/pageContext.ts::createGeneralPageContext` 清空 `work_object_refs`、`source_refs`、`allowed_capabilities`；`web/src/stores/aiDockStore.ts::clearPageContext` 形成 `general → page` 正常回切；对应合同与 store 回归持续生效。 |
| PR #73 任务记录与合并授权守卫 | `AGENTS.md`「Git 与永久任务记录」：PR body 三段与欠债五字段须在合并前完成；checks 绿不等于合并授权。 |
| 静态评审桥安全摘要 | `AGENTS.md`「Git 与永久任务记录」与 `docs/phase2/REVIEW_EVIDENCE.md`：实际运行评审桥时才记录闭集 JSON 摘要并绑定最终候选，以 `observed_model` 如实记录模型；不因档位默认增加评审桥。PR #134 是历史合规实例。 |
| Golden fixture expectation 凭证扫描 | `scripts/golden_task_assertions.py::_assert_judgement_credentials_absent`；`tests/golden_tasks/test_runner_assertions.py`。 |
| Golden 扫描无值失败输出 | `scripts/golden_task_assertions.py::_raise_credential_failure`；无值失败回归。 |
| 引号包裹凭证键扫描 | `scripts/golden_task_assertions.py::_bounded_serialized_json_mapping`；serialized JSON 回归。 |
| `session_key` / `data_key` 变体扫描 | `_CREDENTIAL_EXACT_KEYS` / `_CREDENTIAL_MAPPING_RULES` 与对应回归。 |
| Pilot Ops B/C 工作台归并裁决 | `docs/phase2/DECISIONS.md` 2026-08-18：并进工作台；现役 DAG 见下。 |
| full-chain smoke 生产链守卫 | `scripts/smoke/full_chain.py::run_full_chain_check`；`tests/runtime/test_pilot_foundation_e2e.py` 与 `tests/scripts/test_smoke_full_chain.py`。 |
| 四项架构决定权威归属 | 唯一权威为 `docs/phase2/DECISIONS.md`；Golden 决定四仍由治理 SSOT 守卫保护。 |
| 企业密钥配置文件管理裁决 | `docs/phase2/DECISIONS.md` 决定三：运维配置文件管理，不建设运行时管理页面。 |
| Golden 策略唯一副本守卫 | `docs/phase2/DECISIONS.md` 决定四；`tests/architecture/test_governance_document_ssot.py` 守住唯一副本。 |
| OA Rehearse pack↔pack 比较守卫 | `scripts/smoke/runner.py::_run_rehearsal` 的 pack↔pack 比较及回归；保留以防按旧前提立项。 |
| CLAUDE/AGENTS 治理 SSOT 分层 | `AGENTS.md`「权威、范围与执行」的状态/计划/决策分层；`tests/architecture/test_governance_document_ssot.py`。 |
| Runtime Capability 咽喉动态观测 | `tests/architecture/runtime_schema_observer.py` 与 `tests/architecture/test_response_output_contracts.py` 的咽喉动态观测。 |
| Registry 确定性 bootstrap | `scripts/manage_oa_capabilities.py`；`tests/scripts/test_manage_oa_capabilities.py`；startup Registry DML 守卫。 |
| Golden 敏感父键容器扫描 | `scripts/golden_task_assertions.py`；`P2-GOLDEN-CREDENTIAL-CONTAINER-001` 的 structured/serialized 回归。 |
| SDUI 记录列表与可操作确认面 | `web/src/contracts/runtimeProjection.ts`、`web/src/components/RecordsList.tsx`、`web/src/components/ConfirmCard.tsx` 及对应前端/合同测试。 |
| Work Object 模型与持久化合同 | `app/ports/work_object.py`、`app/infra/persistence/work_object/`、`tests/ports/` 与 `tests/api/test_work_objects.py`。 |
| `confirm_card` capability 参数 allowlist | `app/runtime/runtime.py::_confirm_card_payload`；`tests/runtime/test_runtime_response_content.py`。 |
| Phase 2 架构重排裁决 | 已决任务去向唯一见 `docs/phase2/DECISIONS.md`；现役 DAG 见下。 |
| Work Object 多来源模型 | `state_authority` 判别联合、migration 与 `P2-INTERNAL-WO-MODEL-001` 回归仍生效。 |
| Capability 自动化程度与办理映射 | `app/ports/capability_registry.py::CapabilitySpec`、`app/ports/work_object_handling.py` 与映射负向测试。 |
| Work Object 与 OA 状态同步语义 | `docs/phase2/DECISIONS.md` 2026-08-19；页面始终展示数据时间与同步失败。 |
| OA 登录与用户凭证边界 | `app/credential_polling.py`、`app/infra/auth/postgresql.py` 及 credential polling 回归。 |
| Task 统一版本绑定 | `app/ports/human_gate.py`、`app/workflow/engine.py` 与“v1 确认不得执行 v2”负向回归。 |
| 任务派发 migration downgrade 守卫 | `docs/phase2/DECISIONS.md` 2026-08-27：删除 internal 数据须 `ALLOW_DESTRUCTIVE_DOWNGRADE=1`；四条 downgrade 测试。 |
| `P2-INTERNAL-WO-SCOPE-001` 的 list/detail 组织目录读取授权收益为零 | 原问题的 `principal_department_id` 无可信字段消费，导致 list/detail 依赖组织目录且授权收益为零；`P2-INTERNAL-WO-DISPATCH-001` 已让可信部门/发起人字段进入 `InternalWorkObjectRecord` 并由 list/detail 的真实 PostgreSQL 路径消费，组织目录授权收益与故障分类均有回归。该欠债按 expiry_condition 关闭，后续快照一致性另列活债。 |
| 后台同步路径可能逸出组织目录 503 | 原问题是同步 upsert 后复用 list 路径可能把目录故障误记为轮询失败；`P2-INTERNAL-WO-DISPATCH-001` 已让后台同步在提交后结束并保留 `BackgroundWorkObjectSyncError` 分类，`tests/test_credential_polling.py` 与派发 API 回归覆盖。该欠债按 expiry_condition 关闭。 |
| Work Object 组织目录故障的错误详情键与既有 HTTPException 不一致 | 原问题是组织目录故障使用 `error_code` 而非既有 `code` / `message`；`P2-INTERNAL-WO-DISPATCH-001` 已统一错误详情、OpenAPI 与前端 `ApiError` 消费，并由真实 HTTP 反证覆盖。该欠债按 expiry_condition 关闭。 |
| 未解析部门状态的负责人本部门 allow 分支缺测试 | `P2-INTERNAL-WO-DISPATCH-001` 已增加未解析部门负责人同部门 allow 与跨部门 deny 两条腿，并保留对生产谓词的变异反证；`tests/ports/test_work_object_scope.py::test_undeterminable_department_allows_only_same_department` 持续锁定。该欠债按 expiry_condition 关闭。 |
| Work Object scope 返回的 `reason_code` / `matched_rule` 仍是自由字符串 | `P2-INTERNAL-WO-DISPATCH-001` 已将两个字段收敛为获批 `Literal` 闭集，未知值保持 fail-closed，并由 scope 与派发 Trace 回归覆盖。该欠债按 expiry_condition 关闭。 |
| `set_handling_mark_for_principal` 未从 scope 对象取得主体 | `P2-INTERNAL-WO-DISPATCH-001` 已让 handling mark 写路径消费同一可信 scope 主体，真实 PostgreSQL UPDATE 与可区分 actor 反证持续通过。该欠债按 expiry_condition 关闭。 |
| 部门层级授权裁决 | `docs/phase2/DECISIONS.md` 2026-08-27 的查看轴约束继续有效：同级部门互不可见、下级不得反向查看上级、非主负责人不得跨部门；派发轴改按 2026-09-08 三轴模型判定，派发不授予目标部门资料可见性。由 `P2-INTERNAL-WO-SCOPE-001` 落地对应负向测试。 |
| 必达项 5 的 Golden 完成口径 | `docs/phase2/DECISIONS.md` 2026-08-28：只覆盖 Runtime 观察边界；API/单元层不得弱化。 |
| `UserAction` 结构化入口 | `/api/v1/runtime/action`、`RuntimePort.handle_user_action` 与 `tests/runtime/test_runtime_user_action.py`。 |
| `/runtime/action` 端点级安全负向 | `tests/api/test_csrf.py` 与 `tests/api/test_auth.py` 的正式路由回归。 |
| 结构化/自由文本 completed 文案合同 | `tests/runtime/test_runtime_user_action.py::test_completed_action_preserves_the_text_resume_message_and_fallback`。 |
| 通用 formatter 字段来源边界 | `app/runtime/runtime.py::_format_capability_response`；任意 capability value 不得进入通用文案的负向测试。 |
| `envelope.data` fail-closed 脱敏投影 | `app/runtime/response_projection.py::project_response_data` 与递归投影/凭证属性测试。 |
| DECISIONS 历史链 supersede 指针 | `docs/phase2/DECISIONS.md` 2026-08-28 两条 supersede 指针；现役 DAG 只读本文件与 `docs/phase2/STATUS.md`。 |
| TS/TSX 弱测试确定性门禁 | `scripts/check_weak_tests.py` 已支持前端测试；`uv run python scripts/check_weak_tests.py web/src/pages/__tests__/ChatPage.test.tsx` 确定性返回 `Weak-test check passed.`。 |
| 统一工作台外壳与首个薄查询层消费者 | `web/src/App.tsx`、`web/src/app/AppShell.tsx`、`web/src/shared/ui/QueryTable.tsx`、`web/src/shared/query/useTableQuery.ts` 与对应前端测试；第二个真实生产消费者仍作为活欠债保留。 |
| SDUI 具名跨语言 exact 合同 | `ActionResponseData`、`ConfirmCardPayload`、Runtime OpenAPI / Orval 生成物及 action/confirm 合同守卫；非阻断发现已由 `P2-SDUI-RENDERER-002` 收口。 |
| `P2-SDUI-RENDERER-002` 渲染边界与七项欠债收口 | `ConfirmCardPayload` 下沉 `app/contracts/sdui/models.py` 并由架构守卫禁止 contracts 反向依赖 runtime；OA 导航按部署 origin + 路径段前缀白名单投影，七类反证、拒绝保留记录及原因/下一步、44×44 新窗口安全链接均有前端回归；确认卡 target 复用生成闭集并处理冲突，部分列表在 `is_complete !== true`、返回计数缺失或与实际行数不一致、权威总数与返回数不一致任一成立时保留合法行并醒目标不完整，零行不伪装成正常空状态，`<dl>` 结构合法；`build_confirm_card` 必填 payload、action data 病态输入稳定 fail-closed、悬空 `UIComponentType` 移除、UI/Confirm 公共字段直接守卫、真实合同文件方向性 outcome 守卫及负向变量命名均已闭合。守卫指针：`tests/architecture/test_import_boundaries.py`、`tests/contracts/sdui/test_response_envelope_contract.py`、`tests/contracts/test_user_action_outcome_sync.py`、`tests/infra/sdui/test_response_envelope_builder.py`、`tests/runtime/test_runtime_api.py`、`web/src/contracts/runtimeProjection.test.ts`、`web/src/pages/__tests__/ChatPage.test.tsx`。 |
| 组织目录用途边界行为守卫 | 主防线为 `tests/infra/organization_directory/test_postgresql.py::test_list_user_memberships_returns_complete_set_across_organization_values`：真实 PostgreSQL reader 必须完整返回同一用户在 `organization_id` 与 `subcompany_id` 上同时具备非空重复值、双 `NULL` 与独有值的完整 membership 列表；`tests/architecture/test_organization_directory_boundary.py` 仅为补充层。按组织或分公司折叠、非空/具体值收窄、Python 截断或私有 predicate、SQL `LIMIT` 七类反证均会使主防线变红。 |
| OA 用户信息（含头像）接口的结构输入 | 原欠债「OA 头像接口的字段名、路径与返回形态未取得」：输入已到位，结构以脱敏结构描述落盘于 `docs/phase2/DECISIONS.md` 2026-09-02「事实确认：OA 用户信息接口的路径与返回形态」。该条同时写死四条边界：前端够不到该接口须由后端代持解析、头像取图的三项未知不得推定、另有五项无证据、姓名/单位/部门/头像均按人员信息脱敏不进 Trace 与报告。实现已由 `P2-USER-PROFILE-READ-001` 承接完成；仍未取得的是边界 2 的三项未知与 `isMobx` 取值，见活欠债表对应两行。 |
| `pinned` 语义措辞统一 | 原欠债「`pinned` 语义与 2026-08-27 §六 的「推挤主内容」描述不再一致」：§六 已就地加 2026-09-02 修订注记，`pinned` 改为「放大的浮动面板」、任何模式下主内容宽度不变，三态合同与 AppShell 单例、跨路由会话连续性不变。§六 第 2 条的原句按本文件既有体例（同 §三、§四）保留在历史位置，其现役解释由紧随其后的注记唯一给出；`docs/phase2/` 内不存在未被该注记覆盖的「`pinned` 推挤主内容」表述。§五「Dock `pinned` 时列表自动切紧凑双行」的存废另立一条待裁欠债。 |
| 软件中心 `/apps` 的排期漏项 | 原欠债「软件中心 `/apps` 经裁决列入 P2 一级导航，现役 DAG 无对应任务」：已补立 `P2-FE-APPS-001` 并进入现役 DAG（依赖 `P2-FE-VISUAL-REFACTOR-001`，B 档，按 2026-09-02 裁决前端先行、后端不做）。两份治理文档不再矛盾。 |
| 软件中心落地页只用文字预告三块结构 | 原欠债「软件中心落地页不再列出三块结构与自助登记计划」：`P2-FE-VISUAL-REFACTOR-001` 按定稿画板 `Apps.dc.html` 把 `/apps` 做成真实的三块分区（业务系统 / 单位软件 / 我的功能）加顶部「新建应用」入口，落地页那段文字预告随之下线，expiry_condition 达成。三块中只有「业务系统」的 OA 一张卡有真实数据源，其余缺口另立三条活欠债（业务系统其余三个系统、单位软件 / 我的功能的终端用户能力列表接口、新建应用的登记与审核端点），不随本条一并关闭。|
| 界面输入欠债的表结构归位 | 原欠债「五条界面输入欠债落在三列的「外部验收纪要」表内，后三个字段渲染时被截断」：四条仍未决的界面输入欠债已移入「## 3. 活欠债登记」的六列表，五个字段渲染后全部可见；第五条（OA 头像接口结构）按上文关闭。「### 外部验收纪要（待雨爷裁）」恢复为只承载「待雨爷裁的纪要」，保持三列。 |
| `/chat` 自由输入框的可见标签口径冲突 | 原欠债「`/chat` 输入框无可见标签，与 2026-08-27「禁止用 placeholder 代替可见标签」冲突，未裁」：本批补入 2026-09-07「自由输入框不设可见标签」裁决，明确自由文本输入框不加可见标签、由 `aria-label` 与示例文本承担无障碍名称和引导；结构化字段仍必须有可见标签。由本批裁决消解并关闭，不再作为活欠债登记。 |
| 新增前端页面目录合同缺少架构守卫 | 原欠债「现役裁决禁止新增页面进入扁平 `web/src/components/` / `web/src/pages/`，但 `tests/architecture/test_frontend_layer_boundaries.py` 只覆盖既有三条依赖边界，没有覆盖页面落点」：`P2-FE-DIR-GUARD-001` 新增 `_find_new_flat_directory_files` 判定规则——不用文件名启发式，改为「两个扁平目录下出现祖父化清单之外的任何 `.ts`/`.tsx` 文件」即变红；`_GRANDFATHERED_FLAT_FILES` 字面 frozenset 24 条覆盖全部存量文件并断言其在磁盘上确实存在。反证①：在 `pages/`、`components/` 各建一个未登记探针文件 → 守卫测试 FAIL，删除探针后复跑变绿；反证②：从祖父化清单临时删除真实存在的一条 → 同一测试 FAIL，恢复后变绿，证明清单是真实生效的白名单。另有合成单元测试证明规则不依赖文件名后缀。 |
| 缺少 reader 层「历史 NULL 归属行不可见」的直接断言 | 原欠债「done_when 的该 fail-closed 属性依赖 SQL `NULL = :x` 语义加 migration 测试，没有一条 reader 层直接断言」：`P2-AUDIT-READ-HARDENING-001` 新增 `test_reader_hides_historical_null_owned_row_from_any_tenant_query`，用原始 SQL 插入 `tenant_id`/`ai_user_id` 均为 NULL 的历史行与一条真实归属行并存，断言 `list_events_by_trace` / `_by_task` / `_by_session` 三入口均只返回真实归属行、按 `event_id` 精确比对不含 NULL 行，并验证另一租户视角同样看不到。反证：注释掉 `PostgreSQLTraceReader._list_events` 的 `filters.insert(0, "tenant_id = :tenant_id")` 后重跑，新断言变红（同批 9 项中 2 项 failed），恢复后 9/9 全绿。 |
| 2026-09-03「一屏一个重点、靠层级对比不靠多写说明」原则未落 `DECISIONS.md` | 原欠债「2026-09-03 原则未落盘」：本批按任务书将该裁决正文插入 `DECISIONS.md` 2026-09-02 与 2026-09-04 条目之间，写明「要不要写、写多少」与定稿画板「以谁为准」的分工。由本批裁决消解并关闭，不再作为活欠债登记。 |
| 工作树 Python 版本随本机漂移 | `P2-PYTHON-PIN-001` 已合并；仓库 `.python-version` 与 CI 均钉为 3.12.14，新工作树实测 `Python 3.12.14`，该欠债按 expiry_condition 关闭。证据：PR #181；`P2-PYTHON-PIN-001`。 |
| 两条 2026-09-04 裁决落盘 | 原欠债「两条 2026-09-04 裁决尚未落进 `DECISIONS.md`」：按钮边框与 `/chat` 欢迎语两条原话及各自适用面已由 `P2-GOV-SYNC-DECISIONS-055` 同步落盘，expiry_condition 达成。边框不外推玻璃本体，欢迎语例外不外推其他页面；依据见 `DECISIONS.md` 2026-09-04 对应两条裁决。 |
| `P2-USER-PROFILE-READ-001` 的 Opus 桥单次豁免裁决落盘 | 原欠债「`P2-USER-PROFILE-READ-001` 以 kimi k3 静态评审替代 Opus 评审桥（雨爷显式豁免）」：`DECISIONS.md` 2026-09-06 裁决明确单次动作授权、不构成先例，后续 A 档仍须执行 Opus 桥；`P2-GOV-SYNC-DECISIONS-055` 据此关闭，expiry_condition 第 ① 支已满足，不建立等效替代规则。本行记录当时的审查义务，不作为新任务路由；现役规则见 `AGENTS.md` 与 `DECISIONS.md` 最新路由裁决，历史补审义务不因换档自动关闭。 |
| Compose API LLM 默认值与运行时合同不一致 | 原欠债由 `P2-GOV-SYNC-058` 登记；`P2-COMPOSE-LLM-DEFAULT-001` 已将 `docker-compose.yml::services.api.environment` 的 `LLM_BASE_URL` / `LLM_MODEL` 缺省对齐 `app/config.py::_DEFAULT_LLM_BASE_URL` / `_DEFAULT_LLM_MODEL` 及 `AGENTS.md` 不变量 2，并完成定向配置验证。另实测确认旧的 `LLM_BASE_URL: ${LLM_BASE_URL:-}` 渲染为空串而非缺失；`app/config.py::_required` 仅在变量缺失时采用 `default`，空串直接抛出 `RuntimeError: LLM_BASE_URL is required`，因此不设该变量时 Compose API 会启动失败；该缺陷已随对齐一并修复。 |
| 玻璃拟态模糊层预算的可执行检查 | 原欠债「玻璃拟态的模糊层预算只写在裁决里，无可执行检查」：`P2-FE-VISUAL-REFACTOR-001` 落地 `web/src/test/blurLayers.ts` + `web/src/app/__tests__/blurBudget.test.tsx`，在真实渲染出的那一屏上用 `Element.matches()` 逐元素比对声明了 `backdrop-filter` 的 CSS 规则（含 antd / `@ant-design/x` 运行时注入的 `<style>`），并钉死每屏具体是哪几个面；上限 `BLUR_LAYER_BUDGET = 6` 由 `web/src/app/theme.ts` 单点承载。反证：给 `.signalButton` 加一层模糊 → 四条断言变红（含 `expected 8 to be less than or equal to 6`）；把项目 CSS 里的模糊整体回滚 → 每屏计数掉到 0、断言同样变红。|
| 「退出登录（本地）」的终态落点 | 原欠债「暂置左导航底部，不在终态的用户菜单里」：`P2-FE-VISUAL-REFACTOR-001` 把顶栏头像改为用户菜单触发器，该动作移入菜单；`web/src/app/__tests__/AppShell.test.tsx::carries logout in the user menu instead of the sidebar and keeps the help entry honest` 断言左导航与全局都不再有该按钮、菜单内有且点击后 `authStore` 转 `unauthenticated`，并断言菜单文本不含「内线」与「8012」。|
| 孤立动作 Trace 的租户断言是弱半断言 | 原欠债「`tests/runtime/test_runtime_user_action.py:1275` 断言 `("default", principal.ai_user_id)`，而默认租户正是 `default`，租户那一半无判别力」：`P2-TRACE-CONTRACT-HARDENING-001` 让 `_build_harness` / `_principal` 接受可选 `tenant_id`，`test_user_action_trace_records_inbound_before_outcome` 改用非默认租户 `tenant-action-scoped` 端到端构建 harness 并断言该租户与 `ai_user_id` 同时出现在 inbound/outcome 两条事件；反证：把 `handle_user_action` 两处 `record_step` 调用的 `tenant_id` 硬编码为 `"default"` → 该断言变红（`AssertionError: assert {('default', 'user-action')} == {('tenant-action-scoped', 'user-action')}`），恢复后全绿。 |
| `TracePersistedEvent` 的凭证形状校验无负向测试 | 原欠债「`tests/ports/test_trace_port.py` 只覆盖 `TraceEvent` 的顶层凭证形状拒绝，读取侧 `TracePersistedEvent`（含 `event_id`）无负向用例」：`P2-TRACE-CONTRACT-HARDENING-001` 新增 `test_trace_persisted_event_rejects_blank_trusted_owner`（`reject_blank_owner`）与 `test_trace_persisted_event_rejects_credential_shaped_top_level_identifiers`（含 `event_id` 在内的全部七个受校验字段）；反证：从 `TracePersistedEvent` 移除 `reject_top_level_credential_shape` → 新增 7 个负向用例全部变红，同时既有 `TraceEvent` 侧 6 个同类用例仍全绿（证明该覆盖此前确实缺失，不是靠 `TraceEvent` 用例顺带覆盖），恢复后全绿。 |
| 顶层凭证形状模式用位置切片选取，易被后续插入改变语义 | 原欠债「`_TOP_LEVEL_CREDENTIAL_VALUE_PATTERNS = _CREDENTIAL_VALUE_PATTERNS[1:]` 用位置切片，将来在索引 0 插入新模式会静默改变顶层策略」：`P2-TRACE-CONTRACT-HARDENING-001` 把四条模式具名为 `_LONG_DIGIT_RUN_PATTERN` / `_BEARER_TOKEN_PATTERN` / `_CREDENTIAL_URL_PATTERN` / `_KEY_VALUE_CREDENTIAL_PATTERN`，两个集合均按名字显式构成；新增 `test_top_level_credential_patterns_exclude_only_long_digit_run` 钉死顶层集合恰好排除长数字串、恰好包含其余三条。反证①：在 `_CREDENTIAL_VALUE_PATTERNS` 插入新模式但不同步顶层集合 → 新守卫变红（`test_governance` 未受影响；旧代码下同一插入方式因新模式恰好挤到原长数字串位置，被既有 `test_trace_event_accepts_opaque_hex_identifier_with_long_digit_run` 意外捕获变红——这是巧合而非专门守卫，且不覆盖"新模式本身未进入顶层集合"这一半，记入本棒「需裁决」）；反证②：把 `_LONG_DIGIT_RUN_PATTERN` 错误加入 `_TOP_LEVEL_CREDENTIAL_VALUE_PATTERNS` → 新守卫变红。两条反证恢复后全绿。语义未变：顶层集合实际包含的模式与改动前完全相同。 |
| 本机无法按 `.python-version` 获取 Python 3.12.14 | 按 expiry_condition 关闭：主控已核实用户级 3.12.14 安装及主树解释器 home 修正，不再依赖任务工作树；本批新工作树无额外解释器目录变量的 `uv run python -V` 和 SSOT 入口结果留 PR。旧 uv 下载清单/任务局部解释器描述仅为历史阻断。依据：2026-09-19 交接记录及本棒验证。 |


### 本批已结清欠债（2026-09-22）

| 项目 | reason | blocked_by_task_id | activation_task_id | expiry_condition | evidence |
|---|---|---|---|---|---|
| smoke 对源码版本新鲜度无自证 | `_REQUIRED_RUNTIME_KEYS` 旧 worktree 漂移可能造成 `missing=none` 假绿，仓库此前无确定性诊断 | 无硬依赖；不得把远端可达性或 dirty 状态当授权 | `P2-SMOKE-FRESHNESS-DIAG-001` | 已满足：smoke 启动输出无敏感值的当前源码版本标识，并对明显落后版本显式告警或 fail-closed，离线、detached 与未配置 remote 均有测试 | `P2-SMOKE-FRESHNESS-DIAG-001`（PR #205）交付与验证；`scripts/smoke/environment.py`、`scripts/smoke/runner.py`、`tests/scripts/test_smoke_runner.py` |
| `_read_selected_form()` 只解码值、不解码字段名 | params/text 两分支字段名和值解码语义不一致，编码字段名会错误地 fail-closed | 无硬依赖；需独立 HAR 解析 Scope | `P2-HAR-SELECTED-FORM-DECODE-001` | 已满足：params/text 两分支统一只解码一次字段名和值，并保留重复键、空白、控制字符与缺字段 fail-closed 的公开入口回归 | `P2-HAR-SELECTED-FORM-DECODE-001`（PR #203）交付与验证；`_read_selected_form`、`extract_todo_list_contract`、`tests/scripts/test_smoke_runner.py` |
| smoke 事后报告仍打印出发前操作说明 | `verify` 后报告仍含 `start` / 浏览器登录 / 再跑 `verify` 的出发前指引，时态与报告用途不符 | 无硬依赖；纯报告文案 Scope | `P2-SMOKE-REPORT-PHASE-COPY-001` | 已满足：事后报告只保留结果解释与下一步处置，出发前说明移出报告，结构化结果与安全守卫不变 | `P2-SMOKE-REPORT-PHASE-COPY-001`（PR #204）交付与验证；`scripts/smoke/runner.py::_build_report`、`tests/scripts/test_smoke_runner.py` |


### 已完成任务索引

| task_id | 完成态摘要 |
|---|---|
| `P2-PILOT-FOUNDATION-001` | 真实 LLM、可信身份与生产 composition 基线。 |
| `P2-IDENTITY-CREDENTIAL-001` | OA 绑定、凭证验证与撤销/重置闭环。 |
| `P2-OA-READ-CONTRACT-001` | OA Replay Provider、Contract Pack 与离线脱敏合同。 |
| `P2-READ-ADAPTER-001` | OA Live 只读 Adapter 全链。 |
| `P2-FE-API-CLIENTS-001` | Auth / Runtime / Admin-Trace OpenAPI 与 Orval 客户端。 |
| `P2-ADMIN-CSRF-001` | Cookie 写请求的 Origin + 自定义头 CSRF 守卫。 |
| `P2-RUNTIME-RESPONSE-CONTRACT-001` | Runtime OpenAPI 声明 ResponseEnvelope。 |
| `P2-PILOT-ENTRY-FE-001` | 登录、Session Cookie 与受保护前端入口。 |
| `P2-CHAT-ENTRY-FE-001` | 普通文本 `/chat` Runtime 入口。 |
| `P2-BE-SMALL-DEBT-001` | OA adapter 有界无值失败日志与配置占位。 |
| `P2-FE-TEST-FLAKE-001` | Vitest 文件级并发收敛。 |
| `P2-OA-SYSMSG-PACK-001` | OA 系统消息 Replay pack 与泄漏守卫。 |
| `P2-AUTH-USERID-TYPE-001` | OA userid 单点归一与唯一性守卫。 |
| `P2-OA-SYSMSG-LIVE-001` | OA 系统消息 Live 路由。 |
| `P2-OA-MSGCENTER-PROTOCOL-001` | OA 消息中心传输、cursor 与截断守卫。 |
| `P2-SMOKE-RUNNER-001` | 无凭证回显、结构化、fail-closed 的内网 smoke runner。 |
| `P2-SMOKE-AUTH-DIAG-001` | OA pending 合同与登录诊断加固。 |
| `P2-OA-TODOLIST-ADAPTER-001` | OA 待办事宜协议与 v3 Contract Pack。 |
| `P2-GOLDEN-CREDENTIAL-PATTERN-001` | Golden OA 凭证字段名规则。 |
| `P2-PILOT-OPS-A-001` | Registry 文本校验与绑定管理动作。 |
| `P2-REGISTRY-BOOTSTRAP-001` | 显式 apply / verify、durable 审计与事务回滚。 |
| `P2-SMOKE-E2E-CHAIN-001` | smoke 复用真实生产 composition 与 Runtime HTTP 全链。 |
| `P2-SESSION-COOKIE-TRANSPORT-001` | Session Cookie secure 传输合同。 |
| `P2-REGISTRY-DOC-FIX-001` | Registry bootstrap 目标指导修正。 |
| `P2-SMOKE-FAILURE-CODE-001` | full-chain 固定失败码闭集。 |
| `P2-HAR-FORM-DECODE-001` | 待办 HAR form 值解码。 |
| `P2-GOLDEN-CREDENTIAL-HARDENING-001` | 四个 judgement root 的有界凭证扫描。 |
| `P2-HAR-READ-FORM-DECODE-001` | 消息中心 HAR form 名和值解码。 |
| `P2-SMOKE-VERIFY-DIAGNOSTICS-001` | verify 失败协议与诊断保全。 |
| `P2-FE-ANTD6-001` | 前端升级 Ant Design 6 并移除 ProComponents。 |
| `P2-DECISIONS-SYNC-001` | 2026-08-17/18 决定组与规则 v2.4.1。 |
| `P2-DECISIONS-SYNC-002` | 工作台合并与候选正名裁决。 |
| `P2-SDUI-CONFIRM-PAYLOAD-001` | 确认卡五键 payload 基线。 |
| `P2-GOLDEN-001` | 冻结 GT-029～GT-033 的确认卡参数值 allowlist。 |
| `P2-ENVELOPE-MESSAGE-REDACTION-001` | ResponseEnvelope 单一外露合同与咽喉动态守卫。 |
| `P2-INTERNAL-WO-MODEL-001` | Work Object 双权威判别联合与 migration 守卫。 |
| `P2-CAPABILITY-AUTOMATION-LEVEL-001` | 办理能力自动化程度、Work Object 映射与值展示 allowlist。 |
| `P2-USER-ACTION-SEAM-001` | 结构化 `/runtime/action` 与一次性 claim。 |
| `P2-SDUI-RENDERER-001` | 记录列表、确认卡与 9 值 outcome 的前端投影。 |
| `P2-SDUI-SCHEMA-001` | Action data 与确认卡 payload 具名跨语言 exact 合同。 |
| `P2-SDUI-RENDERER-002` | OA 安全导航、确认卡目标闭集、部分列表与七项合同欠债收口。 |
| `P2-TEST-INFRA-WEAKCHECK-001` | 弱测试门禁支持 TypeScript / TSX。 |
| `P2-FE-WORKBENCH-001` | 统一 AppShell、AI Dock、工作事项与薄查询层首个生产消费者。 |
| `P2-WO-SEARCH-001` | 顶栏 Work Object 搜索、当前已加载授权批次范围披露、三字段匹配与九字段 Dock 上下文注册。 |
| `P2-CONFIRM-BINDING-001` | HumanGate 与不可变 Task 版本绑定。 |
| `P2-GOLDEN-CREDENTIAL-CONTAINER-001` | Golden 敏感父键容器继承守卫。 |
| `P2-WORK-OBJECT-001` | OA Work Object 与最小工作台。 |
| `P2-OA-CREDENTIAL-POLL-001` | 用户密码绑定、加密存储与后台轮询。 |
| `P2-ORGDIR-BOUNDARY-GUARD-001` | 组织目录完整结构关系的真实 PostgreSQL 行为守卫。 |
| `P2-AUDIT-READ-AUTHZ-001` | `audit_reader` 与管理角色动作分离，四个证据入口先授权后访问资源，认证主体组织上下文保真进入管理面 Policy。 |
| `P2-AUDIT-TRACE-SCOPE-001` | 新 Trace 固化可信租户/用户；三种读取强制租户谓词；历史 134 行无可信归属并保持 NULL；四个 Admin 证据入口以同租户 Trace 证明收窄对象。 |
| `P2-WO-SEARCH-SERVER-001` | 服务端 Work Object 检索：`GET /api/v1/work-objects` 可选 `q`，三字段匹配走与列表完全相同的授权路径，上限 200 显式溢出。 |
| `P2-TASK-TENANT-COLUMN-001` | `tasks` 表可信租户列与 Admin 直判归属；历史行按方案 A 保持 `NULL` 并继续 fail-closed 不可见。 |
| `P2-OA-ORGANIZATION-DIRECTORY-001` | OA 只读组织目录合同与本地组织镜像；分页完整性与用途边界修复，不承担授权判定。 |
| `P2-PAGE-CONTEXT-CONTRACT-001` | 页面上下文九字段安全合同；授权 provenance 改为不可伪造票据，且票据不携带授权状态。 |
| `P2-FE-NAV-SHELL-001` | 一级导航五项平铺并可折叠持久化、顶栏按六项重排并移除「当前位置」、不挤压正文的浮动 AI 面板、`/work-dispatch` 与 `/apps` 与 `/messages` 三条路由及落地页；顶栏「部门 / 姓名」与头像仍为 fail-closed 占位，不计入已实现。 |
| `P2-USER-PROFILE-READ-001` | 后端身份读取端点与前端消费：新增 `app/ports/user_profile.py` 契约、`GET /api/v1/me` 与 `GET /api/v1/me/avatar`；`orginfo` 用标准库有界解析，头像走后端代理并做六步 URL 校验 + 图片 MIME 白名单；前端 `authStore` 加 `unknown` 第三态并在启动时向后端确认会话，顶栏 / 用户菜单 / AI 助手问候语三处消费真实身份。 |
| `P2-TRACE-CONTRACT-HARDENING-001` | 收口三条 Trace 合同活欠债：孤立动作 Trace 的租户断言改用非默认租户获得判别力；`TracePersistedEvent` 凭证形状校验补常驻负向测试（含 `event_id`）；顶层凭证形状模式由位置切片改为具名选取并新增守卫，语义未变。六条反证全部实跑。 |
| `P2-FE-DIR-GUARD-001` | 新增扁平目录页面落点架构守卫（新增 TS/TSX 页面不得落入扁平 `web/src/components/` / `pages/`），祖父化存量文件，以故意放错新增页面给出反证。并行 lane，A 类同步由 `P2-GOV-SYNC-056` 补齐。 |
| `P2-AUDIT-READ-HARDENING-001` | 补齐 reader 层「历史 NULL 归属行不可见」三入口（`list_events_by_trace` / `_by_task` / `_by_session`）直接断言。只交付两条活欠债中的第二条；第一条（`/tasks/{id}/events` 存在性差异）经核实构成公共 API 契约变更，按人工停点上报，未做。并行 lane，A 类同步由 `P2-GOV-SYNC-056` 补齐。 |
| `P2-INTERNAL-WO-SCOPE-001` | 岗位负责人 allowlist、监区派发范围与本部门/发起人可见性 scope 接线；组织镜像增加 `job_title`；join key 查不到时 fail-closed。非阻断欠债保留；默认跨部门放权已由 DEPT 修复，人工授权集合维护残余另列。 |
| `P2-INTERNAL-WO-DISPATCH-001` | 首次内部任务派发写端点、可信部门/发起人字段消费、后台同步与组织目录错误分类；内部生命周期与前端接线已合并；多 membership、真实目录与人工授权集合维护残余按活债保留。 |
| `P2-WO-SEARCH-NORMALIZE-001` | 三端以显式 30 字符空白集合归一化并使用普通 `lower`，搜索过滤留在 SQL 内，保留 201 条溢出判定和三字段匹配合同；特殊脚本 lower 不承诺跨端等价。 |
| `P2-COMMIT-HYGIENE-001` | 采用方案 B：A 档棒将格式化改动与功能改动分开提交，不增加 CI 检查；条款已落 `AGENTS.md`。 |
| `P2-PORT-SEAM-001` | `WorkflowEnginePort` 接缝、`WorkflowEngineAdapter` 与 Runtime 抽象依赖已交付；六个 adapter 委托方法的转发守卫已补齐。 |
| `P2-AGENT-ORCH-SEAM-001` | `AgentOrchestrationPort` 编排接缝、生产 `AgentOrchestrationAdapter` 接线、Runtime 依赖切换，以及对应架构守卫、装配与编排回归。Workflow 生产引擎仍未启用，相关既存欠债按活债表保留。 |
| `P2-COMPOSE-LLM-DEFAULT-001` | Compose API 的 `LLM_BASE_URL` / `LLM_MODEL` 缺省与运行时 `glm-4.7` 合同对齐；同时修复缺省 URL 渲染为空串导致配置加载失败的运行配置问题。 |


### 本批已结清欠债（2026-09-30）

| 项目 | reason | blocked_by_task_id | activation_task_id | expiry_condition | evidence |
|---|---|---|---|---|---|


### 现役 DAG · `P2-AUDIT-LOGOUT-002`

| `P2-AUDIT-LOGOUT-002` | P2-AUDIT-WO-LIFECYCLE-001（已合并的新基线） | **A** | **已完成并合并（PR #207）**：当前会话服务端注销、持久吊销、票据 v2 nonce、前端注销确认与缓存 B2 合同已交付；独立监理第 3 轮与静态评审桥均 PASS。D1 与 D2 剩余义务见活欠债表；不新增后继或重排 DAG |

### 现役 DAG · `P2-WO-COMPLETED-MERGE-001`

| `P2-WO-COMPLETED-MERGE-001` | P2-AUDIT-WO-LIFECYCLE-001（已合并） | **B** | 串行修复已办结查询未接既有列表版本合并：只接 `completedQuery` 到 `mergeListResponse`、新增专门前端回归；不改合并算法、认证代次、缓存隔离或公共合同。已合并（PR #206），`P2-AUDIT-LOGOUT-002` 已合入修复并继续验证。无新增欠债。 |

### 现役 DAG · `P2-WO-ERROR-CONTRACT-001`

| `P2-WO-ERROR-CONTRACT-001` | P2-AUDIT-WO-LIFECYCLE-001、P2-AUDIT-LOGOUT-002（均已合并） | **A** | **已完成并合并（PR #211，2026-09-22）**：L-1 固定 completion 422 与声明/生成端一致，D2-2 三生命周期吊销读故障回归通过；审查与集成证据留 PR。Orval 诊断门禁债 WOERR-1 保留；不改权限设计、不新增后继 |

### 现役 DAG · `P2-AGENT-ORCH-SEAM-001`

| `P2-AGENT-ORCH-SEAM-001` | P2-PORT-SEAM-001 | **A** | **已完成并合并至 `phase0/main`**：`AgentOrchestrationPort`、`AgentOrchestrationAdapter` 与 Runtime 生产接线已交付；Workflow 生产引擎已由 `P2-AUDIT-WORKFLOW-WIRING-001` 合并交付 |

### 现役 DAG · `P2-INTERNAL-WO-DISPATCH-001`

| `P2-INTERNAL-WO-DISPATCH-001` | P2-INTERNAL-WO-SCOPE-001、P2-PAGE-CONTEXT-CONTRACT-001（均已完成） | **A** | **已完成并合并至 `phase0/main`**：可信部门/发起人字段消费、首次派发写端点、后台同步错误分类、前后端公开合同及对应回归已交付；前端发布/显示名与内部生命周期已合并；服务端草稿、真实目录、多 membership 与人工授权集合维护按活债保留 |

### 现役 DAG · `P2-INTERNAL-WO-ATTACHMENT-001`

| `P2-INTERNAL-WO-ATTACHMENT-001` | P2-INTERNAL-WO-DISPATCH-001 | **A** | 是：方案限额矛盾须先裁 |

### 现役 DAG · `P2-ORGDIR-PERSON-SYNC-001`

| `P2-ORGDIR-PERSON-SYNC-001` | P2-OA-ORGANIZATION-DIRECTORY-001（已完成） | **A** | **已完成并合并至 `phase0/main`**；生产 Source 仍由 G2 承接。已决后继 `P2-FE-DISPATCH-WIRING-001` 已合并；合同见 `docs/phase2/DECISIONS.md` 2026-09-14 |

### 现役 DAG · `P2-FE-DISPATCH-WIRING-001`

| `P2-FE-DISPATCH-WIRING-001` | P2-ORGDIR-PERSON-SYNC-001、P2-INTERNAL-WO-DISPATCH-001（均已合并） | **B** | **已完成并合并至 `phase0/main`**；目录选择、只读摘要、截止时区、派发与页内重试、显示名及内部 `view_only` 已接线。生产目录前置保留；解析/服务端草稿本棒不做，解析与服务端草稿分别保留活债，承担 task_id 尚待分配 |

### 现役 DAG · `P2-WO-SEARCH-TEST-FIDELITY-001`

| `P2-WO-SEARCH-TEST-FIDELITY-001` | P2-WO-SEARCH-SERVER-001（已完成） | **C** | **已完成并合并至 `phase0/main`**：前端搜索测试名称、断言与负向覆盖已对齐；不动生产代码，两条相关欠债已解除 |

### 现役 DAG · `P2-FE-VISUAL-REFACTOR-001`

| `P2-FE-VISUAL-REFACTOR-001` | P2-FE-NAV-SHELL-001（已完成） | **B** | **已完成并合并至 `phase0/main`**：玻璃拟态视觉、五页按定稿画板落地及五轮实机返修；交办页、软件中心与新建表单主体已建成，合同守卫与空态收口由 `P2-FE-PAGE-CONTRACT-001` 承接 |

### 现役 DAG · `P2-FE-BUNDLE-SPLIT-001`

| `P2-FE-BUNDLE-SPLIT-001` | P2-FE-VISUAL-REFACTOR-001（已完成） | **B** | **已完成并合并（PR #209）**：路由级代码分割、认证后壳层隔离、AI Dock 首开按需加载并常驻；无新增依赖，Vite 500 kB 警告已消除 |

### 现役 DAG · `P2-FE-DISPATCH-FORM-001`

| `P2-FE-DISPATCH-FORM-001` | P2-FE-VISUAL-REFACTOR-001（已完成） | **B** | **已合并交付并关闭**：雨爷 2026-09-07 裁定由 `P2-FE-PAGE-CONTRACT-001` 承担合同守卫与空态收口；页面形态已由 `P2-FE-VISUAL-REFACTOR-001` 建成，不再单独开棒 |

### 现役 DAG · `P2-FE-APPS-001`

| `P2-FE-APPS-001` | P2-FE-VISUAL-REFACTOR-001（已完成） | **B** | **已合并交付并关闭**：雨爷 2026-09-07 裁定由 `P2-FE-PAGE-CONTRACT-001` 承担合同守卫、状态/版本整块留位与命名守卫；软件中心与新建表单已由视觉棒建成，后端不做，不再单独开棒 |

### 现役 DAG · `P2-ADMIN-EVENTS-UNIFORM-001`

| `P2-ADMIN-EVENTS-UNIFORM-001` | 无（`/api/v1/admin/tasks/{task_id}/events` 方向 A 已于 2026-09-08 批准） | **B（高风险）** | **已完成并合并（PR #216）**：完成服务层统一、OpenAPI 声明、页面空态与逐字节负向回归；审查、required checks 与 merge Actions 均 success。仅对应 events 存在性差异债务关闭；内部审计差异与 TaskNotFound 组件/全局映射保留，其他多租户欠债及第二租户验收不变。 |

### 本批两棒 Opus 桥非阻断发现（2026-09-20 补登） · L-1 completion 422 错误合同缺口（已关闭）

| L-1 completion 422 错误合同缺口（已关闭） | 原未知值回显、重复值 body/schema 不符；本棒恢复固定错误并保留 oa_view 既有错误分支 | 无（非阻断） | `P2-WO-ERROR-CONTRACT-001` | 已满足：未知/重复 completion 固定不回显 422，真实两种 body 各命中 oneOf 一支，运行时/快照/生成端一致，正负例及生产变异通过 | 原 `P2-AUDIT-WO-LIFECYCLE-001/bridge-opus/review-output.md` 非阻断 1；`tests/api/test_work_object_error_contract.py`；`web/src/api/__tests__/apiClientsOpenapi.test.ts`；已合并 PR #211 验证记录 |

### 既有活欠债 · 新任务派发的旧跨部门允许名单输入义务（已被取代）

| 新任务派发的旧跨部门允许名单输入义务（已被取代） | 2026-10-18 业务裁决已替代旧负责人独占及部门类型限制，PR #225 已合并新派发判定；旧名单不再作为新任务派发前置，不代表真实目录或角色验收完成 | 无（新任务派发的旧判据已退役） |  | 仅按新任务派发的替代关系结项；真实目录、管理员维护的领导人员名单（由 `P2-WORK-DISPATCH-LEADER-ROSTER-001` 承接）及负责人唯一性资料由下列 X01 跟踪，其他既有诊断或审批策略用途不在本次结项范围 | `docs/phase2/DECISIONS.md`「2026-10-18 — 任务派发业务规则确认（待技术落地）」替代关系；`app/ports/work_object_scope.py::may_dispatch_to`；`app/work_dispatch/service.py::WorkDispatchService._validate_targets` |

### 既有活欠债 · BrowserRunCard 取消请求用例疑似不稳定（已关闭）

| BrowserRunCard 取消请求用例疑似不稳定（已关闭） | PR #273 已合并：两次点击分别等待 React act 更新，保留原断言；修前 1/30 失败、修后 0/30，历史失败与本轮结果均保留，不保证长期永不偶发 | 无 | `P2-BROWSER-CARD-FLAKY-001`（已合并） | 已满足：定位等待缺口、最小修复及有界稳定性验证 | PR #227、#268、#273；仓库外 `debt/FLAKY.md`、`debt/flaky-evidence/` |

### 既有活欠债 · OA 长会话心跳保活缺失（已关闭）

| OA 长会话心跳保活缺失（已关闭） | 原 OA 登录技术对接文档步骤 6 要求“启动心跳保活”而 `app/` 无心跳实现；现已由使用中会话续期 R-OA 实现用户活跃时的有界心跳，只顺延本地截止、不动轮询时间与写版本 | 无 | `P2-AUTH-RENEWAL-OA-001`（已合并） | 已满足：实现与测试已合并，真实环境已见心跳 alive 与本地截止延长；V3/V4/V5 重做与 V9 未验证见下文「R-OA 欠债 1」 | `OA登录技术对接文档.md` §4.1、§4.2；`POST /api/v1/credential-bindings/oa/keepalive`；R-OA 交付与验证记录 |

### 既有活欠债 · 一条前端负向测试已空转（已解除）

| 一条前端负向测试已空转（已解除） | `P2-WO-SEARCH-TEST-FIDELITY-001` 核对当前 `does not use substring matching for source reference or assignee: %s`：已返回带部分来源编号/责任人的标题命中结果，断言结果保留、客户端 `q`、标题命中标签及精确字段标签不出现；空响应分支另断言零结果、无列表项及客户端 `q`，名称与断言一致，无需修改用例 | 无 | `P2-WO-SEARCH-TEST-FIDELITY-001` | 已满足：现有两组用例独立进程验证通过，真实 PostgreSQL 的匹配合同覆盖保持原状 | `web/src/features/work-dispatch/__tests__/WorkObjectSearchPage.test.tsx::does not use substring matching for source reference or assignee: %s` |

### 既有活欠债 · 前端测试未锁住普通 `toLowerCase()` 而非 locale lower（已解除）

| 前端测试未锁住普通 `toLowerCase()` 而非 locale lower（已解除） | `P2-WO-SEARCH-TEST-FIDELITY-001` 增加真实方法调用守卫：保留原实现，断言无参数普通 `toLowerCase()` 调用且无 `toLocaleLowerCase()` 调用；分别替换为 `zh-CN`、`tr` 后该守卫均实际变红，还原后共享测试 73 条通过，生产文件 SHA-256 与改前一致 | 无 | `P2-WO-SEARCH-TEST-FIDELITY-001` | 已满足：两次 locale 替换均被判别；30 字符空白集合、普通 lower 及不承诺特殊脚本跨端等价的边界不变 | `web/src/shared/query/workObjectSearchNormalization.test.ts::uses ordinary toLowerCase without locale-dependent casing`；`web/src/shared/query/workObjectSearchNormalization.ts` |

### LOGOUT-002 评审桥非阻断发现（2026-09-22） · D2-1 STATUS 审阅状态过时（已关闭）

| D2-1 STATUS 审阅状态过时（已关闭） | 合并前 STATUS 仍写 Monitor r2 FAIL、待第 3 轮，未反映最终合并事实 | `P2-AUDIT-LOGOUT-002` | `P2-GOV-SYNC-066` | 已满足：STATUS 改写为已合并事实并同步最终验证基线 | PR #207 body D2-1；本次 `docs/phase2/STATUS.md` 同步 |

### LOGOUT-002 评审桥非阻断发现（2026-09-22） · D2-2 生命周期路由吊销存储读故障 503 无独立用例（已关闭）

| D2-2 生命周期路由吊销存储读故障 503 无独立用例（已关闭） | 原仅 `/me` 与装配测试间接覆盖；已补三生命周期端点独立参数用例 | `P2-AUDIT-LOGOUT-002` | `P2-WO-ERROR-CONTRACT-001` | 已满足：真实 HMAC、真实 PostgreSQL 吊销 SELECT 故障下精确 503/no-store/零业务调用，对象与事件不变、恢复成功；逐文件弱测试、定向、全量及生产变异通过 | PR #207 body D2-2；`P2-AUDIT-LOGOUT-002/bridge-opus/review-output.md` Non-blocking 1；`tests/api/test_work_object_lifecycle.py::test_authentication_store_read_failure_is_503_without_business_calls` |

### Runtime 替换 M0 剩余义务（2026-10-26同步） · M0 计数用内部认证路由的去留（已关闭）

| M0 计数用内部认证路由的去留（已关闭） | 2026-10-27 专项删除授权已由 M1A 执行，路由、runner、开关及接线已删；M1B 核对 `app/ scripts/ tests/ web/ docs/phase2/` 仅 DECISIONS 与本表历史记录提及该开关，仓库内部署脚本与仓库外启动脚本均不含该开关，仓库外计数证据保留 | 无 | `P2-RUNTIME-AGENTSCOPE-M1A-001`（已执行） | 已满足 | DECISIONS 2026-10-27 第 5 项；`app/application.py::create_app` |

### Runtime 替换 M0 剩余义务（2026-10-26同步） · 极简查人问法仍可能 low_confidence（已关闭）

| 极简查人问法仍可能 low_confidence（已关闭） | 不含「业务系统 / 人员 / 姓名 / 信息 / 查询」等词的问法可能与能力零词面重合；超过 8 个可见能力时仍可能未入选，PR #232 未扩 K 或改 selector；新路径由 M1A 去掉词面预筛承接，旧路径不修 | 无 | `P2-RUNTIME-AGENTSCOPE-M1A-001`（新路径） | 已满足：`P2-RUNTIME-AGENTSCOPE-M6-001` 起新路径为默认入口；显式 legacy 的同类限制归「旧路径八项限制保留至旧路径退役」行 | PR #232「本棒新增欠债」第 2 项 |

### Runtime 替换 M0 剩余义务（2026-10-26同步） · D 表单浏览器纵切在本机关闭（已关闭）

| D 表单浏览器纵切在本机关闭（已关闭） | 雨爷 2026-10-29 取消源码指纹锁及 D 池/发布摘要中的源码哈希；2026-10-30 现行发布已激活，D 新路径真实缺项追问、续填与填写后停止跑通，2026-10-31 两次真实填写复测通过。仅关闭本机启动、发布与填写后停止的验收缺口，不表示保存、提交或 M3 人工交接已验收 | 无 | P2-RUNTIME-AGENTSCOPE-M2-001（D 新路径真实验收） | 已满足；保存、提交仍分别取得授权 | PR #232、PR #242「真实验收」；仓库外 `M1_CONTROLLER_STATE.md` 2026-10-30～31 记录；[DECISIONS 2026-10-31](../DECISIONS.md#2026-10-31--裁决m2-d-报修收集口径d-护栏取消与提交拦截) |

### Runtime 替换 M1 已批分棒与新增欠债（2026-10-27） · required Golden 只覆盖旧路径（已关闭）

| required Golden 只覆盖旧路径（已关闭） | required Golden（CI 的 `run_golden_tasks.py --gate`）原只走旧 RuntimeImpl，34/34 只证明旧路径未回归，不覆盖 AgentScope 新路径。`P2-RUNTIME-AGENTSCOPE-M2-001` 已在同一候选上按 DECISIONS 2026-10-28 H4 合同完成新路径正式回放（`--engine agentscope --gate`：34 题中计分 26 题全部通过、8 题合同不适用并单列、GT-001～005 注入配对均已运行并通过），回放义务随 M2 合并关闭；M2 当时回放不进入 required CI（H4 第 6 条），`.github/workflows/ci.yml` 未改；后续按 [DECISIONS 2026-11-02](../DECISIONS.md#2026-11-02--裁决云端自动办理方向聊天上传填表后续技术选型定稿与开发顺序调整) 第 9 条在 M6 改走新路径。M6 已在 required CI 的 Backend full checks 增加新路径 `--engine agentscope --gate` 一步，并保留 legacy `--gate` 作显式兼容门 | 无 | `P2-RUNTIME-AGENTSCOPE-M6-001`（按 2026-11-02 裁决前移） | 已满足：M6 默认入口切换到 agentscope，required Golden 新路径与 legacy 两步并存；H4 合同不变，8 题再评估见「新路径回放 8 题…」行 | `.github/workflows/ci.yml` Backend full checks 两步 Golden；`scripts/run_golden_tasks.py::evaluate_agentscope_gate`、`scripts/golden_task_evaluator.py::AGENTSCOPE_NOT_APPLICABLE`；PR #242「验证结果」；仓库外 `runtime-agentscope-20261021/M2_DESIGN.md` §6 |

### Runtime 替换 M1 已批分棒与新增欠债（2026-10-27） · 新路径身份替换请求的回复措辞（已关闭）

| 新路径身份替换请求的回复措辞（已关闭） | 原真实验收第 14 题以他人身份查询时未执行任何查询，但回复“暂未接入该能力”而非明确拒绝；M1B 在提示词中加入明确拒绝要求 | 无 | `P2-RUNTIME-AGENTSCOPE-M1B-001`（已合并） | 已满足：M1B 真实验收第 14 题明确拒绝代他人查询，未执行任何查询、也未改查本人（2026-10-28，雨爷 15 题全部通过） | 仓库外 `runtime-agentscope-20261021/M1A_REAL_ACCEPTANCE.md`、`M1B_REAL_ACCEPTANCE.md` |

### Runtime 替换 M1B 新增欠债（2026-10-28） · Golden 合同冲突：新路径不暴露 action（已关闭）

| Golden 合同冲突：新路径不暴露 action（已关闭） | GT-006、GT-007、GT-029～033 依赖 action 能力或 `confirmed_mock` 直接执行；新路径不暴露 action，模型调用不到该能力。M2 正式回放对这 7 题照常执行主回放与（GT-007）注入配对，把判卷结论记为 observed，仅在执行完成且无运行器异常时置 `not_applicable`，单列报告 | 无 | `P2-RUNTIME-AGENTSCOPE-M2-001`（回放已实现，随合并关闭） | 已满足：回放按 H4 条文实现并达到门槛；“M6 退役前重新评估是否补等价回归”并入本节新增的 M6 一行 | `scripts/golden_task_evaluator.py::AGENTSCOPE_NOT_APPLICABLE`；PR #242「验证结果」 |

### Runtime 替换 M1B 新增欠债（2026-10-28） · Golden 合同冲突：别名选择器无承接（已关闭）

| Golden 合同冲突：别名选择器无承接（已关闭） | GT-014 的别名选择器在新路径上没有生产别名解析器，按原名调用得到失败而非 `no_capability_found`。M2 正式回放按 H4(e) 同上单列 `not_applicable` | 无 | `P2-RUNTIME-AGENTSCOPE-M2-001`（回放已实现，随合并关闭） | 已满足；重新评估并入 M6 一行 | 同上 |

### Runtime 替换 M1B 新增欠债（2026-10-28） · Golden 合同冲突：fixture 能力缺输入 schema（已关闭）

| Golden 合同冲突：fixture 能力缺输入 schema（已关闭） | GT-009、GT-015～024 的 fixture 能力未声明 `input_schema`，新路径目录按 `schema_invalid` 失败关闭。M2 在回放进程内对缺项能力补空对象 schema（不写 fixture，生产 `project_tool_input_schema` 仍拒绝缺项），这 11 题计分并通过；这 11 题均未启用注入，未另造注入配对 | 无 | `P2-RUNTIME-AGENTSCOPE-M2-001`（回放已实现，随合并关闭） | 已满足 | `scripts/golden_task_evaluator.py::_build_capability_spec`；PR #242「验证结果」 |

### Runtime 替换 M1B 新增欠债（2026-10-28） · 观察项：只读 workflow 进入写组（已关闭）

| 观察项：只读 workflow 进入写组（已关闭） | 新路径按设计把全部 workflow（含 OA 概览等只读 workflow）放入写组；M1B 评测与真实验收中库内 0 个 workflow，读题误转确认卡为 0（S9、S10），该分组路径现实中未被触发 | 无 | `P2-RUNTIME-AGENTSCOPE-M1B-001`（已合并） | 已满足：评测与真实验收未见读选择退化 | `app/infra/orchestration/agentscope_runtime/catalog.py::build_tool_catalog`；设计 §3.1；仓库外 `m1-eval/results/M1B_S9_RESULT.md`、`M1B_REAL_ACCEPTANCE.md` |

### Runtime 替换 M1 收口新增欠债与已批需求（2026-10-28） · R-OA 欠债 3：已激活浏览器绑定用户不自动恢复（已关闭）

| R-OA 欠债 3：已激活浏览器绑定用户不自动恢复（已关闭） | `P2-AUTH-OA-ACTIVE-RECOVERY-001` 实现“保留主体核验 + 绑定围栏”的恢复：active 本人绑定的会话证实失效时，用已保存密码自动登录一次，新登录主体与已核验主体一致才写入；按需查询、页面心跳、后台轮询、填表提案、报表与填表 worker 均已真实实测。主体不一致、恢复中解绑、交接窗口内失效等未实测分支另见[该棒新增欠债](../PHASE2_PLAN.md#oa-会话自动恢复-p2-auth-oa-active-recovery-001-新增欠债2026-11-17) | 无 | `P2-AUTH-OA-ACTIVE-RECOVERY-001`（已完成） | 已满足：2026-11-16/17 真实验收各入口失效后自动登录 1 次恢复，主体核验不变 | 仓库外 `RENEWAL_PLAN.md` §3.4；M3 承接边界：交接期间窗口内 OA 会话过期时 M3 不自动重登，未实测，不承接恢复设计，见 [M3 新增欠债](../PHASE2_PLAN.md#runtime-替换-m3-新增欠债2026-11-01)；activation 保持原值，待 GOV-SYNC 后续裁决；[DECISIONS 2026-11-01](../DECISIONS.md#2026-11-01--裁决m3-d-人工交接合同受限终结与先合并后验收) |

### Runtime 替换 M2 新增欠债（2026-10-29） · D 保存/提交拦截的真实填写复测（已关闭）

| D 保存/提交拦截的真实填写复测（已关闭） | 2026-10-31 雨爷在最终候选两次真实填写：首页、门户菜单与三个填写动作正常，填写后停止、未保存未提交，运行 completed、effect acknowledged、verification verified、清理 released。拦截在真实 OA 中未被触发，触发路径仍只由合成 Chrome 回归证明（POST=0、`FORM_SUBMIT_BLOCKED`/unknown） | 无 | P2-RUNTIME-AGENTSCOPE-M2-001 | 已满足 | PR #242 验证结果；tests/infra/browser/test_d_form_submit_guard.py |

### Runtime 替换 M2 新增欠债（2026-10-29） · m2-1 提示词的开发集评测与概览能力启用（已关闭）

| m2-1 提示词的开发集评测与概览能力启用（已关闭） | 2026-10-29 S8 完整开发集在 `m2-1` 上为 263/276（M1B 为 264/276），主控接受结果，835 次请求已计预算账本；不外推后续 `m2-5` 提示词。概览能力已按当次专项授权经 dry-run→启用→核对，R4 真实只读查询通过；SPEC 门槛欠债仍沿用 M1A | 无 | `P2-RUNTIME-AGENTSCOPE-M2-001`（S8，主控） | 已满足；不核销 M1A 评测门槛欠债 | PR #242「S8 完整开发集」「真实验收」；仓库外 `M1_CONTROLLER_STATE.md` 2026-10-29 记录；`M2_DESIGN.md` §7.1 S8、§10-② |

### Runtime 替换 M2 新增欠债（2026-10-29） · D 报修 schema 拒绝的现场触发项待复测（已关闭）

| D 报修 schema 拒绝的现场触发项待复测（已关闭） | 2026-10-30 主控重启后雨爷真实复测：“监控坏了”进入缺项卡并追问地点（物品未被当作地点），续填后一次准入且描述包含新增故障情况；2026-10-31 两次“三楼会议室投影仪坏了，没有画面”未追问即进入填写并完成三个字段（位置非空才能填写，首轮位置未被误删）；此后真实运行日志未再出现 `tool_input_rejected`。该次 07:50 的原始参数形态仍无证据 | 无 | P2-RUNTIME-AGENTSCOPE-M2-001 | 已满足 | PR #242；controller-dispatch/astra_m2_fix_repair_schema.out.md |

### Runtime 替换 M2 新增欠债（2026-10-29） · 新路径 MCP 未授权时无提示与授权入口（已关闭）

| 新路径 MCP 未授权时无提示与授权入口（已关闭） | 后端重启后未重新授权 MCP 时，新路径按 `mcp_candidate_owner_missing` 过滤掉全部 MCP 工具，模型没有工具可用而答非所问；雨爷 2026-10-29 要求未授权时提示“需要到软件中心授权综合业务系统后才能访问”，并能从聊天直达软件中心 `/apps` 的 MCP 授权区，随后决定推迟。`P2-POST-M6-FIX-001` 对仅因本人未授权而不可见的 MCP 能力注册无执行器的授权占位工具，模型选中后整轮在分派前终止，返回固定文案与站内入口 `/apps#mcp-connections`（内部 UI 原因码 `mcp_authorization_required`）；能否选中仍取决于模型对问句的工具选择 | 无 | `P2-POST-M6-FIX-001` | 已满足（2026-11-03 合并前在 18005 候选上经雨爷真实流程核对：未授权时问业务系统问题得到固定文案与“去授权”入口，入口定位到 `/apps` MCP 连接区，日志 `mcp_authorization_required`、MCP 外呼 0；授权后恢复真实查询因业务系统不可用未验，归上文「M6 缺省入口 MCP 验收待补」）。原条件：新路径对授权类不可见的 MCP 能力确定性回复固定文案并给出站内 `/apps` MCP 授权入口，MCP 外呼为 0，非授权原因与已授权路径不受影响 | `app/infra/adapters/business_mcp/fixed_read_profile.py`（候选可见性判定）、`web/src/features/apps/McpConnectionsPanel.tsx`；仓库外 `controller-dispatch/opus_m2_mcp_unauthorized_hint.md` |

### 云端自动办理与技术选型新增欠债（2026-11-02） · Procrastinate 队列 schema 待专项授权（已关闭）

| Procrastinate 队列 schema 待专项授权（已关闭） | 队列安装需要 DB schema 变更，本次只定选型；进程内调度迁移前须修正入队即完成假设，浏览器业务账本/租约/围栏仍由现有 PG run 存储负责，单一执行所有者。2026-11-03 `P2-BACKGROUND-FOUNDATION-001` 交付 R1/R2、同事务接收与独立 worker，开发库与测试库已升级，合并前在 18005 加 worker 上真实验证持久入队、独立 worker、重启续跑（强杀与 Ctrl+C 两路换代）、取消、限并发与单一执行所有者（换代后旧队列行不再执行），本行关闭；unknown 写入不重放与四个旧调度器迁移分别由下文[新功能基础总体方案批准登记](../PHASE2_PLAN.md#新功能基础总体方案批准登记2026-11-03)中“write 类后台作业协议”“四个旧调度器迁移”两行承接 | 无（雨爷 2026-11-03 已批准开发库与测试库的 schema upgrade，见 [DECISIONS 2026-11-03 方案批准](../DECISIONS.md#2026-11-03--裁决新功能基础总体方案批准与一次性授权) 第 2 条） | `P2-BACKGROUND-FOUNDATION-001`（后台任务基础；四个旧调度器的逐项迁移待分配，见 [DECISIONS 2026-11-03](../DECISIONS.md#2026-11-03--裁决新功能基础拆分与监理模板口径对齐) 第 3 条） | 获专项授权后安装 schema，并验证持久入队、独立 worker、重启续跑、取消、限并发与单一执行所有者；unknown 写入不重放 | [DECISIONS 2026-11-02](../DECISIONS.md#2026-11-02--裁决云端自动办理方向聊天上传填表后续技术选型定稿与开发顺序调整) 第 4 条；仓库外 `E:/code/eternalai-reviews/runtime-agentscope-20261021/M3_CONTROLLER_STATE.md` 末尾最终决定 |

### Runtime 替换 M6 登记（2026-11-02） · 执行确认卡片改为展示业务信息（已关闭）

| 执行确认卡片改为展示业务信息（已关闭） | 现文案「确认仅填写三项后停止……」为技术说明，使用者看不懂；要求卡片只说明将执行的操作与填写的业务内容。`P2-POST-M6-FIX-001` 将 D 填写确认改为“确认执行操作吗？即将执行工单报修流程”并列出报修类别/故障位置/故障描述三项原值（`BrowserWritePreview.fields`，公共响应合同变更已于 2026-11-03 获雨爷批准），删去本地协调框失实说明；普通确认卡与 MCP 恢复卡去掉能力标识、协议枚举与技术说明，缺业务名项见下文本棒欠债 | 无 | `P2-POST-M6-FIX-001` | 已满足（2026-11-03 合并前在 18005 候选上经雨爷真实流程核对：D 确认卡显示业务操作与三项原值、无技术说明；确认后 OA 三项一致，未保存未提交；治理 MCP 缺业务名项另列下文）。原条件：卡片只出现操作与业务字段、无技术说明，并经真实流程核对 | 仓库外 `POST_M6_BACKLOG.md` 第 3 条 |

### Runtime 替换 M6 登记（2026-11-02） · 刷新页面后追问卡片失去绑定（已关闭）

| 刷新页面后追问卡片失去绑定（已关闭） | 刷新后在原卡片回复被当作新一轮对话，原收集记录闲置到过期（2026-10-30 观察）。`P2-POST-M6-FIX-001` 把待续填引用纳入 AI 助手状态与同标签恢复快照，随会话恢复一并落入并绑定恢复后的登录代；旧快照只提示“原追问已失效”，404/409 显示失效或已更新并停止续填 | 无 | `P2-POST-M6-FIX-001` | 已满足（2026-11-03 合并前在 18005 候选上经雨爷真实流程核对：不完整报修追问后刷新，在原卡补充即续接原收集记录，请求携带 `d_form_collection` 引用）。原条件：刷新后原卡回复续接原收集记录，或明确提示失效且不留闲置记录，并经真实流程核对 | 仓库外 `POST_M6_BACKLOG.md` 第 4 条 |

### 新功能基础总体方案批准登记（2026-11-03） · XlsxWriter 推迟（已关闭）

| XlsxWriter 推迟（已关闭） | 基础三棒不生成报表；PR #275 已引入锁定的 xlsxwriter==3.3.1（BSD-2-Clause）、登记依赖策略，并经真实报表生成、回读与下载验收 | 无 | `P2-XLSX-REPORT-001`（已完成） | 已满足依赖登记、锁定与首份固定报表真实验收；多报表与目标机限制见下文报表新增欠债 | DECISIONS 2026-11-02 第 5 条；PR #275 |

### 新功能基础总体方案批准登记（2026-11-03） · 输出发布与产物棒正向链路（已关闭）

| 输出发布与产物棒正向链路（已关闭） | 输入链路已于 2026-11-05 在上传棒验收；2026-11-09 PR #275 首个业务生产者经 JobOutputPublisher 发布 OA 报表并实际下载通过 | 无 | `P2-CHAT-UPLOAD-001`（输入）；`P2-XLSX-REPORT-001`（输出，已完成） | 已满足：输入链路与首个输出生产者的发布、下载真实通过；R3 内容已由雨爷 2026-11-09 确认，不外推正式目标机 | 方案 J7；PR #275 R1/R2；PR #276 R9 补测 |

### 后台任务基础 `P2-BACKGROUND-FOUNDATION-001` 新增欠债（2026-11-03） · 18005 加 worker 真实验收未做（已关闭）

| 18005 加 worker 真实验收未做（已关闭） | 施工只获测试库授权；开发库 upgrade、18005 重启与 worker 启停、§1.6 第 13 项故障注入均由主控验收时执行，测试库冒烟不代替真实验收。2026-11-03 合并前已完成：开发库目标识别后升级，§1.6 第 1–13 项全部实测通过（第 8 项“取消中再次取消”界面不提供按钮，经浏览器控制台连续取消两次实测 202 后 200 且状态不变），本行关闭 | 无 | `P2-BACKGROUND-FOUNDATION-001`（合并前验收） | 开发库按方案 §4 目标识别后 upgrade；方案 §1.6 第 1–13 项在开发库 18005 加 worker 上实测；含 asyncpg 上的绑定与返回 id、同事务回滚、提交后通知、取消两分支与换代 | 方案 §1.6、§8.3；[DECISIONS 2026-11-03 方案批准](../DECISIONS.md#2026-11-03--裁决新功能基础总体方案批准与一次性授权) |

### 后台任务基础 `P2-BACKGROUND-FOUNDATION-001` 新增欠债（2026-11-03） · Windows Ctrl+C 优雅停止与停机中断原因值未实测（已关闭）

| Windows Ctrl+C 优雅停止与停机中断原因值未实测（已关闭） | worker 在 Windows 不装信号处理，Ctrl+C 由 `asyncio.Runner` 取消主任务，期望走官方“优雅停止，30 秒后以 SHUTDOWN 中断”。2026-11-03 第 9 项实测：Ctrl+C 后停止领取，30 秒宽限后队列行记 aborted、账本不写结果，对账换代后成功（派发 2 次），本行关闭 | 无 | `P2-BACKGROUND-FOUNDATION-001`（§1.6 第 9 项） | 第 9 项实测；不成立则记录实际行为并另定处置 | 方案 §1.4、§8.3；`app/worker.py::run_worker` |

### 文件产物 `P2-FILE-ARTIFACTS-001` 新增欠债（2026-11-05） · Windows 下载与清理互斥未实测（已关闭）

| Windows 下载与清理互斥未实测（已关闭） | 本棒没有 available 产物；清理与下载互斥依赖 `os.open` 句柄无删除共享。2026-11-05 上传棒 C9：页面读首块后暂停形成真实背压，该轮清理记 `file_in_use`、`failed=1`，下载 sha256 一致，下一轮 `removed`，本行关闭 | `P2-CHAT-UPLOAD-001` | `P2-CHAT-UPLOAD-001` | 样本根上限速下载跨越清理轮次：该轮记 `file_in_use` 失败、下载 sha256 一致、下一轮删除；不成立回评审 | 方案 §2.6、§8.3；`app/api/v1/file_artifacts.py::PinnedFileResponse` |

### 文件产物 `P2-FILE-ARTIFACTS-001` 新增欠债（2026-11-05） · 下载端点未导出 OpenAPI 文件（已关闭）

| 下载端点未导出 OpenAPI 文件（已关闭） | 后端导出脚本只覆盖已有端点，本棒不改 `web/`。`web/openapi/file-artifacts.openapi.json` 已随 PR #263 提交（前端上传调用为手写 `chatUploadApi.ts`），本行关闭 | `P2-CHAT-UPLOAD-001` | `P2-CHAT-UPLOAD-001` | 前端接入下载时补导出脚本与生成物 | `scripts/export_background_jobs_openapi.py` |

### IM 机会层 V3/布局新增欠债（2026-11-05） · 已关闭：典型等价问法有界验证已形成结论；主线代码在冻结问法集（直问、两种标准委托、后段路由 × 地点/代号/时间 6 问）纯 Runtime 90/90 答对、端到端 6/6 正确，未观察到答对/拒答翻转；任意表达与长期稳定性未证明

| 已关闭：典型等价问法有界验证已形成结论；主线代码在冻结问法集（直问、两种标准委托、后段路由 × 地点/代号/时间 6 问）纯 Runtime 90/90 答对、端到端 6/6 正确，未观察到答对/拒答翻转；任意表达与长期稳定性未证明 | 无 | P2-IM-QUESTION-STABILITY-001（有界验证完成，无生产改动、无 PR） | 已满足：典型问法形成可复核结论；仅关闭本次有界验证义务，不外推任意表达与长期稳定性 | 任务 P2-IM-QUESTION-STABILITY-001 验收记录；历史来源 PR #259/#261/#264 |

### IM 机会层 V3/布局新增欠债（2026-11-05） · 已关闭：A5/A6 本人窗口原生路径；2026-11-06 DEBT1 1/2 通过，A5 原生多人私聊有固定群状态、答案仅私回甲；A6 queued 时本人原生编辑后 im_source_changed，模型与投递均零

| 已关闭：A5/A6 本人窗口原生路径；2026-11-06 DEBT1 1/2 通过，A5 原生多人私聊有固定群状态、答案仅私回甲；A6 queued 时本人原生编辑后 im_source_changed，模型与投递均零 | 无 | P2-IM-PERSONAL-AGENT-READ-001（合成验收完成） | 已满足：甲乙本人独立会话的原生撰写/编辑与拒绝结果有真实证据，不外推正式环境 | PR #259/#261；仓库外 `debt/DEBT1.md` 1/2、A5/A6 截图及机器结果 |

### IM 机会层 V3/布局新增欠债（2026-11-05） · 已关闭：760px 顶栏横向溢出；旧 scrollWidth=1115，现 745=clientWidth

| 已关闭：760px 顶栏横向溢出；旧 scrollWidth=1115，现 745=clientWidth | P2-IM-MESSAGES-LAYOUT-001 | P2-IM-MESSAGES-LAYOUT-001 | 已满足：真实登录 760px 无横溢，两宽度折叠/深链/换记录保持 PASS | PR #259/#261；acc/LAYOUTFIX.md；acc/layout-fix-browser.json；acc/layout-fix-760-expanded.png |

### IM 机会层 V4 `P2-IM-AGENT-DELEGATION-001` 新增欠债（2026-11-06） · 已关闭：后段路由典型问法有界验证

| 已关闭：后段路由典型问法有界验证 | 保留正文、不清洗后段提及；冻结问法集整体纯 Runtime 90/90、端到端 6/6 正确，含后段路由，未观察到答对/拒答翻转；任意表达与长期稳定性未证明 | 无 | P2-IM-QUESTION-STABILITY-001（有界验证完成，无生产改动、无 PR） | 已满足：典型实际问法已形成有界结论，不外推任意表达与长期稳定性 | 任务 P2-IM-QUESTION-STABILITY-001 验收记录；历史来源 PR #264 |

### IM 机会层 V4 `P2-IM-AGENT-DELEGATION-001` 新增欠债（2026-11-06） · 合成 Zulip 误建的空频道 7 未删除（已关闭）

| 合成 Zulip 误建的空频道 7 未删除（已关闭） | 雨爷 2026-11-08 专项授权仅归档频道 7；名称“ACC1 合成验收”与 V4ACC2 记录一致，GET messages narrow 该频道 anchor=newest、num_before=1 返回空；DELETE streams/7 为 200，读回已归档且不在活跃列表 | 无 | `P2-GOV-SYNC-20261108-1` | 已满足：核对为空后归档，仅此频道 | 仓库外 `acc/V4ACC2-provision.json`、`gov/MERGE5_channel_archive.json`；Zulip 官方 archive-stream API |

### IM 部署配置 `P2-IM-DEPLOY-CONFIG-001` 新增欠债（2026-11-06） · 已关闭：`BrowserRunCard.test.tsx` 取消重试用例偶发失败，PR #273 以 await act 等待两次点击更新；修前 1/30、修后 0/30，原断言保留

| 已关闭：`BrowserRunCard.test.tsx` 取消重试用例偶发失败，PR #273 以 await act 等待两次点击更新；修前 1/30、修后 0/30，原断言保留 | 无 | `P2-BROWSER-CARD-FLAKY-001`（已合并） | 已满足本次定位与有界验证义务，不保证长期永不偶发 | PR #268、#273；仓库外 `debt/FLAKY.md`、`debt/flaky-evidence/` |

### IM 机会层 V2b `P2-IM-FILE-SHARE-001` 新增欠债（2026-11-08） · 真实第三人隔离（已关闭）

| 真实第三人隔离（已关闭） | 2026-11-09 甲乙丙三个真实工作台账号、18015/worker、15433 与合成 Zulip 补验：丙下载、核实与详情均 404，列表不可见，甲乙同版下载及权限历史不受影响 | 无 | `P2-IM-FILE-SHARE-001` 后续验收（F4C 已完成） | 已满足：丙本人隔离与甲乙不受影响均有实际证据；不外推正式组织/目标机 | PR #271；仓库外 `debt/F4C.md` |

### IM 机会层 V2b `P2-IM-FILE-SHARE-001` 新增欠债（2026-11-08） · 5432/15432 升级到 20261106_120000（已关闭）

| 5432/15432 升级到 20261106_120000（已关闭） | 雨爷 2026-11-08“升级。”专项授权后，先测试库 15432、后开发库 5432，均从 20261105_120000 升至 20261106_120000；revision、四个可空列与 artifact FK 读回通过；18005 未重启，本治理棒未连接数据库 | 无 | `P2-IM-FILE-SHARE-001` 合并后专项升级（已完成） | 已满足：两库获专项授权、升级至 20261106_120000 并读回结构 | PR #271；CONTEXT.md 2026-11-08“升级。”；gov/UPG5.md；gov/upg5-databases.json |

### OA 待办报表 `P2-XLSX-REPORT-001` 新增欠债（2026-11-09） · worker 内 OA 会话不续期，过期即失败（`oa_session_expired`）（已关闭）

| worker 内 OA 会话不续期，过期即失败（`oa_session_expired`）（已关闭） | 方案 §3.6 不给 worker 续期能力；`P2-AUTH-OA-ACTIVE-RECOVERY-001` 起，发起人本人绑定为 active 且会话证实失效时，worker 按作业台账的发起人身份自动登录一次恢复后继续；非 active 绑定或恢复失败仍报 `oa_session_expired` | 无 | `P2-AUTH-OA-ACTIVE-RECOVERY-001`（已完成） | 已满足：2026-11-16/17 报表与填表 worker 在自然失效后各自动登录 1 次并完成作业 | PLAN §3.6；`app/credential_polling.py::prepare_active_oa_read` |

### OA 待办报表 `P2-XLSX-REPORT-001` 新增欠债（2026-11-09） · R9（OA 会话失效）未实测（已关闭）

| R9（OA 会话失效）未实测（已关闭） | #276 自然失效补测得 `oa_unavailable`；PR #278 修复后 2026-11-09 再次自然失效实测得 `oa_session_expired` | 无 | — | 已满足 | PR #276 R9 段；PR #278 F1；仓库外 `bgjob-fix-20261109/acc/expiry_wait.jsonl` |

### OA 待办报表 `P2-XLSX-REPORT-001` 新增欠债（2026-11-09） · R3 内容人工核对待雨爷（已关闭）

| R3 内容人工核对待雨爷（已关闭） | 雨爷 2026-11-09 确认报表内容无问题 | 无 | — | 已满足 | 雨爷 2026-11-09 主控对话确认 |

### 聊天上传填表 `P2-SHEET-FILL-001` 新增欠债（2026-11-09） · 普通说法下模型提案的完整性不稳定：有时只映射一个工作表（已关闭）；OA 会话失效时会先去查询（已关闭）

| 普通说法下模型提案的完整性不稳定：有时只映射一个工作表（已关闭）；OA 会话失效时会先去查询（已关闭） | glm-4.7 的工具选择不稳定；PR #278 要求逐表交代后，普通说法新会话连续 3 次映射全部工作表；`P2-CHAT-SHEETFILL-POLISH-001` 改为提案阶段由系统只读心跳一次判定 OA 会话，失效即不出确认卡并提示重新授权，不再依赖模型先去查询 | 无 | `P2-CHAT-SHEETFILL-POLISH-001`（已完成） | 已满足；失效分支的真实验证见该棒新增欠债 | 本 PR 验收 S1 记录；PR #278 F2-2；`app/sheet_fill.py::SheetFillAdmission.propose` |

### 聊天上传填表 `P2-SHEET-FILL-001` 新增欠债（2026-11-09） · worker 无法识别 OA 会话过期，统一报 `oa_unavailable`（第一棒 R9 实测）（已关闭）

| worker 无法识别 OA 会话过期，统一报 `oa_unavailable`（第一棒 R9 实测）（已关闭） | PR #278 给 worker 加入只读心跳探活，仍不续期 | 无 | `P2-BGJOB-FIX-001`（已完成） | 已满足：2026-11-09 自然失效实测报出 `oa_session_expired` | 本 PR 验证 R9 段；PR #278 F1 |

### 聊天上传填表 `P2-SHEET-FILL-001` 新增欠债（2026-11-09） · S3 内容由人工核对，待雨爷（已关闭）

| S3 内容由人工核对，待雨爷（已关闭） | 雨爷 2026-11-09 确认填表结果内容无问题 | 无 | — | 已满足 | 雨爷 2026-11-09 主控对话确认 |

### 后台读 OA 与填表小修 `P2-BGJOB-FIX-001` 新增欠债（2026-11-09） · 确认卡不显示被跳过的工作表（已关闭）

| 确认卡不显示被跳过的工作表（已关闭） | `P2-CHAT-SHEETFILL-POLISH-001` 把 `skipped_sheets` 纳入 plan 合同（无跳过项时不出现，保持旧幂等指纹），确认卡列出「这次不填的工作表」 | 无 | `P2-CHAT-SHEETFILL-POLISH-001`（已完成） | 已满足：2026-11-16 真实验收确认卡列出不填的工作表，确认后作业成功 | PR #278 F2；`app/sheet_fill.py::SheetFillPlan`、`web/src/features/background-jobs/SheetFillPlanCard.tsx` |

### IM 自建前端、代理 B 与 V1 多来源新增欠债（2026-11-11） · 代理出错时的页面呈现未实测（已关闭）

| 代理出错时的页面呈现未实测（已关闭） | 2026-11-16 IM 补验停掉本任务后端：用户组区域与消息页如实提示失败、不泄露上游信息，恢复后自动接通 | 无 | `P2-IM-DEBT-ACC-20261116`（已完成） | 已满足 | PR #280；仓库外 `E:/code/eternalai-reviews/im-debt-20261116/ACC_r2.md` 第 1 项 |

### IM 自建前端、代理 B 与 V1 多来源新增欠债（2026-11-11） · 两个依赖 dispatch_db 的 logout 用例本地未运行

| 两个依赖 dispatch_db 的 logout 用例本地未运行 | fixture 会建/清 schema，超出代理棒授权的库边界；由 required CI 覆盖 | 无 | required CI | CI 全绿即覆盖（PR #280 已满足，本项关闭条件已达成，仅留记录） | PR #280；`proxyplan/PROXY_IMPL_REPORT.md` |

### IM 自建前端、代理 B 与 V1 多来源新增欠债（2026-11-11） · 所选消息撤回后服务端整组拒绝分支未实测（已关闭）

| 所选消息撤回后服务端整组拒绝分支未实测（已关闭） | 2026-11-16 实测撤回时限内撤回一条后「交给我的 AI」，服务端 404 `im_source_not_found` 整组拒绝、无部分来源；当时页面只显示泛化重试提示，由 `P2-IM-SOURCE-ERROR-001` 补齐错误信封后实测显示「请重新选择」 | 无 | `P2-IM-SOURCE-ERROR-001`（已完成） | 已满足 | PR #281；仓库外 `E:/code/eternalai-reviews/im-debt-20261116/ACC_r2.md` 第 2 项；仓库外 `im-source-error-20261116/IMPL_r2.md` |

### IM 自建前端、代理 B 与 V1 多来源新增欠债（2026-11-11） · 资料卡非空部门/职务/电话呈现未真实验证（已关闭）

| 资料卡非空部门/职务/电话呈现未真实验证（已关闭） | 2026-11-16 经雨爷授权用测试 Zulip 组织所有者能力建「部门」「职务」「办公电话」自定义资料字段并给甲乙填合成值（乙电话留空）：资料卡逐行显示合成值、空电话行隐藏，本人邮箱显示与 r2 一致 | 无 | `P2-IM-DEBT-ACC-20261116`（已完成） | 已满足 | PR #282；`feplan/acc3/ACC7_REPORT.md`、`ACC8_REPORT.md`；仓库外 `E:/code/eternalai-reviews/im-debt-20261116/ACC_r3.md` |

### IM 自建前端、代理 B 与 V1 多来源新增欠债（2026-11-11） · V3 授权与撤权未实测（已关闭）

| V3 授权与撤权未实测（已关闭） | 2026-11-16 经雨爷授权，在测试环境以合成资料完成一次授权与撤权，状态与界面一致 | 无 | `P2-IM-DEBT-ACC-20261116`（已完成） | 已满足 | PR #282；仓库外 `E:/code/eternalai-reviews/im-debt-20261116/ACC_r2.md` 第 4 项 |

### IM 自建前端、代理 B 与 V1 多来源新增欠债（2026-11-11） · 用户组展开成员未实测（已关闭）

| 用户组展开成员未实测（已关闭） | 2026-11-16 在测试 Zulip 建合成用户组，展开成员准确并能打开资料 | 无 | `P2-IM-DEBT-ACC-20261116`（已完成） | 已满足 | PR #282；仓库外 `E:/code/eternalai-reviews/im-debt-20261116/ACC_r2.md` 第 6 项 |

### 工作台前端改版 `P2-FE-WORKBENCH-V4-001` 新增欠债（2026-11-13） · 外观设置、案头排列未按用户保存，案头不能自定义（已关闭）

| 外观设置、案头排列未按用户保存，案头不能自定义（已关闭） | `P2-FE-WORKBENCH-V4-002` 已做自定义首页（拖动换位、改大小、移出、加回）；`P2-HOME-LAYOUT-PERSIST-001` 经雨爷 2026-11-17 授权新建 `user_preferences` 表与 `GET/PUT /api/v1/preferences`（身份只取自会话），外观四项与首页摆法按登录用户存取，浏览器本地只作首屏缓存 | 无 | `P2-HOME-LAYOUT-PERSIST-001`（已完成） | 已满足：2026-11-17 真实验收甲改外观与摆法后在全新 Chrome 用户目录登录完全一致；同一浏览器甲乙换人互不串写 | `web/src/app/userPreferencesSync.ts`、`app/api/v1/user_preferences.py`；仓库外 `home-layout-20261117/ACC_r1.md`；未实测分支见[该棒新增欠债](../PHASE2_PLAN.md#首页布局与外观按用户保存-p2-home-layout-persist-001-新增欠债2026-11-17) |

### 「我的功能」只读个人流程 `P2-READONLY-SKILL-001` 新增欠债（2026-11-18） · 执行记录与提案、试运行、运行快照只在会话内存（已关闭）

| 执行记录与提案、试运行、运行快照只在会话内存（已关闭） | 由 `P2-READONLY-SKILL-002` 改存会话两表，按租户、本人、绑定会话隔离；待接手跨重启等未实测项转记该棒欠债 | 无 | `P2-READONLY-SKILL-002`（已完成） | 已满足：2026-11-18 真实验收两次后端重启后「把刚才的查询保存为功能」均出含原两步的审阅卡 | `app/infra/persistence/user_skills.py::PostgreSQLUserSkillSessionStore`；仓库外 `readonly-skill-phase2-20261118/acc/ACC_r4.md` |

### 「我的功能」只读个人流程 `P2-READONLY-SKILL-001` 新增欠债（2026-11-18） · `non_empty`、`max_count` 断言审阅卡无选择入口（已关闭）

| `non_empty`、`max_count` 断言审阅卡无选择入口（已关闭） | `P2-READONLY-SKILL-002` 在审阅卡提供「列表不能为空 / 最多 N 条」检查，失败显示具体原因、不可保存、不可接手 | 无 | `P2-READONLY-SKILL-002`（已完成） | 已满足：2026-11-18 真实验收阈值超限提示失败且不可保存，调整后保存成功 | `app/user_skills/rendering.py::user_check_failure`；仓库外 `readonly-skill-phase2-20261118/acc/ACC_r1.md` V6 |

### 工作台前端改版第二棒 `P2-FE-WORKBENCH-V4-002` 新增欠债（2026-11-14） · 自定义首页的布局只存在浏览器本地（已关闭）

| 自定义首页的布局只存在浏览器本地（已关闭） | 由 `P2-HOME-LAYOUT-PERSIST-001` 改为按登录人存后端 | 无 | `P2-HOME-LAYOUT-PERSIST-001`（已完成） | 已满足：2026-11-17 真实验收换浏览器后摆法一致 | `web/src/features/desk/homeLayout.ts`、`web/src/app/userPreferencesSync.ts` |

### 工作台前端改版第二棒 `P2-FE-WORKBENCH-V4-002` 新增欠债（2026-11-14） · 助手回复的排版只认粗体和清单（已关闭）

| 助手回复的排版只认粗体和清单（已关闭） | `P2-FE-POLISH-001` 补小标题、表格（超过 5 行卡片内翻页、列多横向滚动）、链接（只认 http(s) 与本站路径）与行内代码，分隔线不显示 | 无 | `P2-FE-POLISH-001`（已完成） | 已满足：2026-11-16 真实对话中表格、小标题、链接、行内代码均正确排版，本站链接页内跳转后对话保留 | `web/src/shared/ui/AnswerText.tsx` |

### 工作台前端改版第二棒 `P2-FE-WORKBENCH-V4-002` 新增欠债（2026-11-14） · 添加软件对话框、周期任务页其余部分仍用旧样式变量（已关闭）

| 添加软件对话框、周期任务页其余部分仍用旧样式变量（已关闭） | `P2-FE-POLISH-001` 两处样式改用新令牌、不再引用 `--workbench-*` 别名，观感与新版一致；页面结构与 antd 控件未重做，若需按 v7 重做另开任务 | 无 | `P2-FE-POLISH-001`（已完成） | 已满足：2026-11-16 真实页面截图核对描边、选项卡片、聚焦环与列表/编辑视图 | `web/src/features/apps/NewSoftwareDialog.module.css`、`web/src/features/work-dispatch/WorkObjectSeriesPage.module.css` |

### OUT · 编排框架旧口径

| PydanticAI / 新编排框架默认引入 | **已拍板**：维持 raw SDK 默认，不投入 PydanticAI 内网复验；它不是 P2 目标的必要条件，既有 Spike 结论为 failed with caveat。 | 出现具体需求，且雨爷再次确认。 | 蓝图 §6.11 L1368-L1374、§13 L2716；`ADR-P0-SPIKE-007-pydanticai-qwen-vllm.md` L91-L103、L194-L202 |

### OUT · 平台与客户端旧口径

| Temporal/Celery/Milvus/OpenSearch/Next.js/原生 App 等平台升级 | P2 没有已证实规模触发条件，升级不会直接闭合部门试点。 | 对应规模、可靠性、门户或移动需求出现并通过 ADR。 | 蓝图 §12.2 L2552-L2603、§12.3 L2604-L2622、§13 L2719-L2767 |

### 外部验收纪要 · 未脱敏素材旧规则指针

> 未脱敏素材读取禁令唯一见 `AGENTS.md` 不可协商规则 9；历史裁决见 `docs/phase2/DECISIONS.md`。PLAN 不复制原始素材细节或现场 smoke 操作手册。

### MCP 离线接入后的启用义务 · 原合并状态

`P2-MCP-PLATFORM-001` 提供通用多服务底层及 13 工具离线开发候选；公司未确认事项按以下插口保持关闭。此记录不分配后继、不改变原 OA 必达链或第二租户禁启裁决。

### IM 环境新增欠债 · 测试 CA 原时点

| 测试 CA 2026-11-11 到期 | 合成 Zulip 19412/19415 使用的测试 CA 于 2026-11-11T01:44:24Z 到期；此前仅续叶证书且未超过 CA 截止，届时影响合成访问 | 无（需对应证书/信任变更授权，不由治理棒执行） | 到期前续 CA 或换新根的环境任务（待分配） | 到期前完成续 CA 或换新根，并在 19412/19415 及获准客户端验证信任与访问 | 仓库外 CONTEXT.md 2026-11-09 ACCN2 环境处置；`debt/F4C.md`；本任务 laneGOV9.md |


### IM V3/布局 · 历史表头原文

| reason | blocked_by_task_id | activation_task_id | expiry_condition | evidence |
|---|---|---|---|---|

### IM 部署配置 · 结项表头原文

| reason | blocked_by_task_id | activation_task_id | expiry_condition | evidence |
|---|---|---|---|---|

### 既有活欠债 · 「我的功能」原 status/version 映射

| 软件中心「我的功能」的 `status` / `version` 前台映射与版本格式未裁定（2026-11-18 已关闭：前提变化——「我的功能」由 `P2-READONLY-SKILL-001` 改为本人保存的查询功能，经独立接口 `/api/v1/my-skills` 显示「可用 / 已暂停 / 已停用」与整数版本，不再展示 Capability 的 status/version；下列原文保留备查） | **reason**：`P2-FE-PAGE-CONTRACT-001` 已核对 `CapabilityStatus` 为 `draft` / `active` / `disabled` / `deprecated`，`CapabilitySpec.version` 仅为 `str`；`active` 是否映射为「已发布」、其余状态的中文语义、版本格式与空值含义仍无裁决，不能照画板补 `v` 或造值 | **blocked_by_task_id**：面向终端用户的能力列表端点尚未排棒，且前台语义映射未裁定 | **activation_task_id**：待 GOV-SYNC 在用户列表合同与映射裁决到位后分配（原 `P2-FE-APPS-001` 的当前界面责任由 `P2-FE-PAGE-CONTRACT-001` 合并关闭，不关闭本债） | **expiry_condition**：用户列表合同及 status 中文映射、version 格式与空值含义明确并接入，未知值有中性文字与图标降级测试，不猜成「已发布」 | **evidence**：`app/ports/capability_registry.py::CapabilityStatus` / `CapabilitySpec`；`web/src/features/apps/AppsPage.tsx` 两块「暂时不能显示」留位；`web/src/features/apps/__tests__/AppsPage.test.tsx` 的 `keeps status` 参数化守卫覆盖管理员缓存中的四个已知值、未知值与空值，不冒称未来用户条目渲染器已验证 |


### Runtime M2 · D 同类字节和本机围栏结项

| D 其他同类字节/本机围栏（本棒已处理） | 雨爷 2026-10-29 先取消 D 源码指纹锁；2026-10-30 决定把 D 浏览器其余“基本不会出现”的护栏提前到本棒一次取消。`P2-RUNTIME-AGENTSCOPE-M2-001` 已盘点 D 填写后停止实际经过的路径并取消：环境变量黑名单、Chromium 二进制摘要比对、Playwright 驱动路径校验（10-30 00:23 browser_start 拒绝的触发点）、进程树/窗口/端口/目标与页面集合自证、control_fence 每步重查发布与预留、登录凭证与结果的重复复核、清理后的根进程身份自证；保留不保存不提交、凭证不外泄、身份与隔离、只开配置的 OA 站点、清理自启浏览器五条底线，保留的拒绝点写固定原因码。`playwright_node`、`chromium_sha256` 两个配置字段因本机任务配置与其他工作树共用暂留（后者仍参与池摘要，不再做比对）；`composition.py` 跨纵切共用的启动接线校验与 `app/infra/persistence/browser/` 内的检查不在本次范围 | 无 | 本棒 | 已满足；配置字段与持久层检查如需再收，另起任务 | PR #242；仓库外 `controller-dispatch/d_fence_inventory.md`、`controller-dispatch/opus_m2_remove_d_fences.out.md` |
