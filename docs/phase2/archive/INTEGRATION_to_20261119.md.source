# 任务派发模块集成说明

业务唯一来源是 [WORK_DISPATCH.md](docs/phase2/WORK_DISPATCH.md)，Schema A 唯一范围是该文件 §11。本文记录模块与宿主接线，不另设业务规则。当前是阶段 B 静态候选；独立 Opus Review、数据库执行和真实业务验收均未完成。

## 仓库与候选

- 工作树：`C:/Users/Administrator/.codex/worktrees/work-dispatch-001/eternalai`，分支 `phase2/P2-WORK-DISPATCH-001`。
- 已成功 fetch 的开发 base：`1801124622f50fd74e34ed5a7dfed288199cdb88`。静态集成前文档 HEAD：`cecad1caff8bf612967ea0ddae5716f7a1b8e5d3`；本文首次写入时实现尚未提交，具体文件由 W1–W5 分工维护。
- 最终候选 SHA、完整原始 diff、逐行规格、实际检查结果和评审记录绑定在仓库外 `C:/Users/Administrator/.codex/visualizations/2026/10/22/01a16307-69db-71d1-a0af-0b7682bf0a05/p2-work-dispatch-001/review-packet/`。从 Git 获取候选实际 HEAD；不将本文的文档 HEAD 冒称实现候选。
- 不从旧 ZIP/source/b4bab5b 施工，不改共享 checkout 或 AgentScope/runtime 工作树。没有 push、PR、merge 或部署。

## 模块与迁移接缝

| 文件组 | 责任与实际依赖 |
|---|---|
| `app/ports/work_object.py`、`work_object_dispatch.py`、`work_object_scope.py`、`work_object_lifecycle.py` | Work Object 记录、可信 scope、静态岗位名单、纯派发与流转规则、DTO/Store 合同。ports 不依赖 API/infra |
| `app/ports/work_object_recurrence.py`、`work_object_attachment.py` | 规则/期次/附件合同，标准库时区日历计算与基本文件格式验证 |
| `app/work_dispatch/service.py` | 派发、候选、办理/备注编排；使用目录、Store、Trace 端口，无 CapabilityGateway/Registry |
| `app/work_dispatch/recurrence.py`、`attachments.py` | 周期与附件应用编排；周期保存可信主体引用，每期重新取新鲜目录，不伪造 Principal |
| `app/infra/persistence/work_object/postgresql.py`、`authorization.py`、`dispatch_insert.py` | 核心事务、唯一根/子资源授权 SQL、无 begin/commit 的插入 helper；旧 OA 数据权威保留 |
| `app/infra/persistence/work_object/recurrence.py`、`attachments.py` | 周期外层事务、期次唯一性/历史、私有 BYTEA 和逐期独立快照；infra 只依赖 ports 与 peer infra |
| `app/api/v1/work_objects.py`、`work_object_series.py` | HTTP 薄适配、认证依赖、错误/ETag/幂等头与二进制输出；旧 OA sync、handling mark 和能力投影仍由原宿主服务维护 |
| `web/src/features/work-dispatch/` | 派发、周期页面与历史组件、办理面板、搜索、共享附件面板和 API facade；复用当前 React/AntD 6、会话草稿、PageContext |
| `web/src/api/mutator.ts`、`workDispatchTransport.ts` | 共用 CSRF、401、身份 generation、JSON+ETag/raw-body/Blob；UI 不各自裸 fetch |

这是集中模块加薄宿主适配，不是仅复制一个目录即可跨框架使用的声明。迁移时需一起携带中立 ports、SQL Store/新 revision、HTTP/身份/目录/Trace 接缝和前端共享传输依赖；新宿主仍需真正装配与验收。

## 当前宿主的真实接线

`app/composition.py` 使用原 `session_factory` 和 `organization_directory` 构造 PostgreSQLWorkObjectStore → WorkDispatchService；同一 factory 构造附件、周期 Store，再构造各自应用服务与 WorkObjectSeriesScheduler。原 WorkObjectService 接受这些实例并仅保留 HTTP/OA 宿主职责。

`app/application.py` 注册 `/api/v1/work-objects` 与 `/api/v1/work-object-series`，继续使用现有 `csrf_protected_principal`。生产工厂把同一周期 service/scheduler 交给 create_app；正常后台启用时由 lifespan `start()`，退出时 `await stop()`。现有明确关闭后台的安装方式不启动该 scheduler。这里记录源码接线，未启动后台作验收。

前端既有受保护路由新增 `/work-object-series`，原 AppShell 增加周期任务入口。实例链接 `/work-objects?work_object_id=...` 打开现有详情 Drawer；详情 `series_id` 由当前授权的 occurrence 关联只读派生，不加根字段。参与者的 SeriesHistoryPanel 只查询当前可见期次；规则管理/模板/操作历史仅 creator。

## 对外合同

OpenAPI 当前含 21 个路径、25 个操作，来自上述两个实际路由工厂。原 list/detail/search/sync/handling-mark 路径保留；新增或扩展包括派发/候选、生命周期全部命令/事件、备注及历史、私有附件上传/关联/软解绑/下载、周期 CRUD/启停/操作历史/期次。

- 关系命令必须携带稳定 relation ID；协作主办汇总和关系提交可区分。旧 complete 委托新提交规则，无无条件办结旁路。
- 生命周期 `wolc`、备注 `worm`、规则 `wosr` 使用各自强 ETag。mutating 请求携带 Idempotency-Key；versioned 请求还携带 If-Match。备注响应版本不改根版本/更新时间/完成时间。
- 附件原始 body 上传，服务端有界读取并验证基本格式/大小；下载先按当前根/关系授权，再读私有字节，返回 no-store 和安全文件名。没有公共 URL、文件平台、预览或病毒扫描声明。
- 根可见性与子记录可见性分别判断；负责人只来自当前有效 department 目标关系，不从 owner_department_id 或个人所属部门推导。旧承办人的独立派发人/负责人身份按 Q03 分别重判。
- 错误沿 `{detail:{code,message}}`，隐藏输入正文与凭证。中立 `precondition_required/version_conflict/transition_invalid` 在 HTTP 映射为既有标准 `work_object_*` 码；失败不报成功。
- 列表保留 oa_sync、limit、limit_exceeded，按 limit+1 获取；内部完成/撤回历史支持 UTC 日期半开区间与游标，新增 next_cursor。现有 OA 默认列表语义保留。

## Schema 与持久化状态

仅新增 `alembic/versions/20261022_120000_work_dispatch_v2.py`，父源码 head 为 `20261009_120000`；这是静态图，不是现场数据库 revision。两个现有表扩展、八张新表及约束详见规格 §11。没有修改旧 revision、Browser/Identity/Runtime 表、连接目标或凭证。

等价缩减：省去根 lead_relation_id/循环 FK，由当前 role=lead 关系的部分唯一索引和同根事务保证协作唯一主办，不放宽“至少一个主办”。初始根+关系+receipt 保存创建事实，不杜撰 creation lifecycle operation；附件 link/detach 的根版本可形成事件序列间隔。周期外层事务一次提交期次、全部任务/关系/receipt/附件快照及进度，不调用已提交的普通派发来拼事务。

没有读取真实存量，保留可验证身份下的显式旧任务转换，身份无法确定则明确受阻。周期每期/每目标独立 BYTEA 快照，不挪用历史任务 FK；100 MiB 每周单目标约 5.08 GiB/年，100 独立目标约 508 GiB/年只是容量计算，未测量实际数据。无新增全局存储门、共享 blob schema、自动清理或保留期政策。

Schema A 仅代码编写；没有数据库连接、查询、DDL、upgrade/downgrade、数据迁移或业务写入。Schema B 未批准，后续用户实机测试也不自动授予数据库、取密、登录、保存或提交权限。

## 静态结果与交付边界

- W1 核心和 W2 周期/附件已有各自定向 Ruff/mypy 静态结果，见仓库外 W1_RESULT/W2_RESULT；不是运行验收。
- W5 API/宿主/生成器授权文件 Ruff 通过；W5 五源 focused mypy 通过。完整 `mypy app/` 检查 278 源，剩 31 条既有 Browser/MCP/Windows 依赖/stub/类型错误，本任务代码未报错；原始输出为仓库外 mypy-app-final.txt，未顺手改范围外代码。
- 完整 `tsc --noEmit -p web/tsconfig.json` 剩两条既有测试夹具与新合同不兼容：dispatchApi.test.ts 缺新增必填 mode，workObjectLifecycleApi.test.ts 的 accept 缺 relation ID。生产源码报告中的一处 reminder 默认值由原 owner 修复。没有修改测试、削弱断言或改 tsconfig 排除测试；不能称全量 typecheck 通过；当前既有 build 的类型检查步骤也会被这两条测试夹具合同阻断，未擅自修改它们换绿。
- W5 改动前端文件 ESLint 通过。首次从仓库根调用因未找到 web 配置失败，改为 web 工作目录后通过。未新增配置。
- 导出器静态核对只导入路由/DTO/认证工厂、裸 FastAPI，无 production app/composition/config/数据库/服务构造或 lifespan 执行，只写 work-objects.openapi.json。已执行生成；Orval 7.21.0 只运行 `--project workObjects`，只生成其两个文件并仅对它们去尾空白。未调用全项目 generate:api/normalize。
- 任务 web/node_modules 是经允许建立的精确 junction，复用 `E:/code/eternalai/web/node_modules`；保留用于静态工具，不跟踪、不安装、不改锁、不删除共享目录。W2 留下的空 .venv 未扫描或删除。
- `git diff --check` 通过；最终精确清单、候选和状态以外部 packet 为准。没有新增/修改/执行测试、pytest/Golden/E2E、应用/后台/业务构建或 CI。required checks 未豁免；本轮无 PR。

独立 Opus 5.5 C 尚未执行。B01–B12 的代码路径与宿主接线已形成静态候选供 Review，不能据此宣称真实业务完成。E01–E10 和 M 项运行验收均未执行；用户在全部代码及独立 Review 修复后亲自实机测试。

Q07 唯一负责人已明确批准，缺失/多人在创建前整体拒绝。Q03/Q05 已批准。Q01/Q02/Q04、Q06 持续委托与停用联动、漏期策略、Q08 撤回终态仍按规格默认记录；无账号停用源不是禁止全部周期生成的新门。真实目录 Source、领导岗位名单、账号停用事实、旧数据分布、可操作账号/任务/附件和相应授权仍是现场缺项。没有用 Mock、空成功或额外门禁替代这些条件。
