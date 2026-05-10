# Research Notes — 用友 U8 API & Auth

task_id: P0-SPIKE-005
system: 用友 U8
date: 2026-05-09
executor_tool: "Codex"
executor_model: "GPT-5.5 high"
reviewer_tool: "Codex"
reviewer_model: "GPT-5.5 high"

## 1. System Identification

- system_name: 用友 U8+
- product_type: ERP (Enterprise Resource Planning)
- version_or_assumed_version: "用友 U8+ (U8 Cloud 或 U8+V16/V18)，具体版本待现场确认"
- deployment_model: On-premise 为主；U8 Cloud 为 SaaS 模式
- backend_stack: .NET / COM+ (传统); SQL Server 数据库

## 2. API Surface

### 2.1 Known API Types

| API Type | Evidence Tier | Notes |
|----------|--------------|-------|
| COM/DCOM API | needs_vendor_confirmation | U8 传统集成方式，通过 COM 组件调用业务逻辑 |
| EAI (Enterprise Application Integration) | needs_vendor_confirmation | U8 内置的 EAI 接口，支持 XML 格式的业务数据交换 |
| REST API (U8 Cloud / 新版本) | community | 较新版本或 U8 Cloud 可能提供 REST 接口，需确认版本和可用性 |
| Open API | needs_vendor_confirmation | 用友开放平台 (yonyou OpenAPI) 是否覆盖 U8 待确认 |
| Web Service (SOAP) | needs_vendor_confirmation | 部分版本提供 WSDL 接口 |

### 2.2 API Access Model

- evidence_tier: needs_vendor_confirmation
- COM/DCOM 方式通常需要在服务器本地或内网环境调用
- EAI 接口通过文件/XML 交换，非典型 REST 调用
- REST API 可用性取决于具体版本和购买的模块

## 3. Authentication & Authorization

### 3.1 Authentication Modes

| Mode | Evidence Tier | Status |
|------|--------------|--------|
| Database-level auth | needs_vendor_confirmation | U8 用户存储在 SQL Server 中，部分操作直接走数据库权限 |
| Application-level auth | needs_vendor_confirmation | U8 自身用户体系，基于操作员编号和密码 |
| API Token / App Key | needs_vendor_confirmation | 新版本或 U8 Cloud 是否支持 API Token 待确认 |
| OAuth2 | needs_vendor_confirmation | 用友开放平台是否为 U8 提供 OAuth2 待确认 |
| Windows Auth / AD | community | 部分部署场景支持 Windows 集成认证 |

### 3.2 Token / Session Lifecycle

- evidence_tier: needs_vendor_confirmation
- 传统 COM 调用通常通过登录接口获取 session，调用完毕后登出
- REST API（如果存在）的 token 机制待确认
- 是否支持长期 token 或 refresh_token — 待确认

### 3.3 User-Level Permission

- evidence_tier: needs_vendor_confirmation (无可复核 source_id / URL)
- U8 有完整的操作员权限体系：功能权限、数据权限、字段权限
- API 调用时是否继承操作员权限 — 需确认
- 是否存在 API 专用角色 — 需确认

## 4. Identity Binding

- evidence_tier: needs_vendor_confirmation
- AI 平台用户与 U8 操作员的映射方式 — 待确认
- U8 操作员编号是否可作为 identity binding key — 待确认
- 最小需求：需要一个 U8 操作员账号用于 API 调用

## 5. Integration Capabilities

| Capability | Evidence Tier | Status |
|-----------|--------------|--------|
| Webhook / Callback | needs_vendor_confirmation | U8 是否支持事件推送待确认 |
| Rate Limit | needs_vendor_confirmation | API 频率限制待确认 |
| Audit Log | needs_vendor_confirmation | U8 有上机日志模块 |
| Sandbox / Test Env | needs_vendor_confirmation | 是否存在测试账套或沙箱待确认 |

## 6. Irreversible Operations

基于公开资料的推断（evidence_tier: community）：
- 凭证记账 / 审核 — 财务模块操作可能不可撤回
- 库存出入库确认 — 影响库存数据
- 销售/采购订单审核 — 影响业务流程

需现场确认完整列表。

## 7. Phase 0/1 Scope Assessment

| Operation | Phase 0 (Spike) | Phase 1 Recommendation |
|-----------|----------------|----------------------|
| 读取基础档案（客户/供应商/物料） | 不操作 | 允许只读 PoC（待确认） |
| 读取库存数据 | 不操作 | 允许只读 PoC（待确认） |
| 读取财务凭证 | 不操作 | 需谨慎，可能涉及敏感数据 |
| 创建单据 | 不操作 | Phase 1 建议禁止写入 |
| 审核单据 | 不操作 | Phase 1 建议禁止写入 |

## 8. Open Questions

1. 当前部署的是 U8+ 哪个具体版本？
2. 使用的是 U8+ 本地版还是 U8 Cloud？
3. 是否有可用的 REST API 或 Open API？
4. COM/DCOM 集成是否允许从外部应用调用？
5. 是否存在测试账套或沙箱环境？
6. AI 平台代表用户调用时，权限如何裁决？
7. 是否允许 Phase 1 只读 PoC？

## 9. Blocking Risks

1. COM/DCOM 依赖 — 可能限制跨平台调用
2. 内网部署 — 可能需要 VPN 或代理才能访问
3. 无 REST API — 如果只有 COM/EAI 接口，Adapter 实现复杂度高
4. 财务模块数据敏感 — 需要额外的安全评估

## 10. Source Inventory / Evidence Sources

| source_id | evidence_tier | source_type | title_or_doc_name | url_or_local_reference | accessed_or_recorded_date | supports_which_claims | confidence |
|-----------|--------------|-------------|-------------------|----------------------|--------------------------|----------------------|------------|
| U8-SRC-01 | needs_vendor_confirmation | vendor_doc | 用友 U8+ 官方产品文档 / 安装手册 | needs_vendor_confirmation — 用友官方文档通常为内网/购买后获取 | 2026-05-09 | COM/DCOM API, EAI interface, Web Service (SOAP), application-level auth, database-level auth | low — 无公开 URL 可复核 |
| U8-SRC-02 | needs_vendor_confirmation | vendor_doc | 用友 U8+ 开发者文档 / 集成指南 | needs_vendor_confirmation | 2026-05-09 | API access model, EAI XML exchange | low |
| U8-SRC-03 | needs_vendor_confirmation | community | 开发者社区中关于 U8 COM 集成的讨论 | needs_vendor_confirmation — 社区链接不稳定 | 2026-05-09 | Windows Auth/AD (线索), REST API 可用性 (线索) | very low |
| U8-SRC-04 | needs_vendor_confirmation | vendor_doc | 用友开放平台文档 | needs_vendor_confirmation — 开放平台是否覆盖 U8 待确认 | 2026-05-09 | Open API, OAuth2 support | low |

说明：用友 U8+ 为国产企业级 ERP 系统，官方文档通常不在公网公开发布。COM/DCOM API 和 EAI 接口的存在是基于 U8 产品架构的普遍认知，但无公开 URL 可作为直接证据引用。所有 evidence_tier 已降级为 needs_vendor_confirmation。

## 11. Recommendation

`mock_only`, `can_build_adapter_later`, `needs_vendor_confirmation`

Phase 1 建议先做 Mock Adapter。U8 的 API 形态取决于具体版本和部署方式，需现场确认后再决定是否以及如何构建真实 Adapter。
