# 任务派发模块集成说明

业务唯一来源是 [WORK_DISPATCH.md](docs/phase2/WORK_DISPATCH.md)，当次 Schema A 清单见其 §11。本文记录模块与宿主接线。PR #225 已合并；专用 `eternalai_dispatch_test` 的一次性迁移往返及合成验证已完成，真实 E01–E10 仍开放。原阶段 B 时点详见[阶段 B 原记录](docs/phase2/archive/INTEGRATION_to_20261119.md)。

## 仓库与候选

PR #225 记录最终候选、独立评审和集成证据。2026-10-22 开工时工作树、基线和未推送/未合并状态见[阶段 B 原记录](docs/phase2/archive/INTEGRATION_to_20261119.md)。

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

2026-10-22 静态阶段仅有 Schema A 代码授权。后续专库一次性授权和迁移往返见 [WORK_DISPATCH §9](docs/phase2/WORK_DISPATCH.md#9-统一待确认和外部条件清单) X02；其他专项动作依各自授权办理。

## 静态结果与交付边界

2026-10-22 阶段 B 的静态结果、工具输出及当时尚未独立评审的时点见[阶段 B 原记录](docs/phase2/archive/INTEGRATION_to_20261119.md)。PR #225 已经独立静态评审、修复、专用库合成验证并合并；按已登记结果，2026-10-24/25 合成覆盖后端 E01–E10 和页面 E05/E06/E07。真实 E01–E10 与未完成的 M 项以 [WORK_DISPATCH §8](docs/phase2/WORK_DISPATCH.md#8-必要验证与真实业务验收) 和 [PLAN](docs/phase2/PHASE2_PLAN.md#既有活欠债) 为准。

Q03/Q05/Q06/Q07/Q08 已登记裁决，Q06 的生产账号停用事实仍未接；真实目录 Source、领导名单、唯一负责人资料及获准账号、环境和业务动作仍待现场证据。见 [WORK_DISPATCH §9](docs/phase2/WORK_DISPATCH.md#9-统一待确认和外部条件清单)。
