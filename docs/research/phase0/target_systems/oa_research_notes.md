# Research Notes — 泛微 e-cology 9 API & Auth

task_id: P0-SPIKE-005
system: 泛微 e-cology 9
date: 2026-05-09
executor_tool: "Codex"
executor_model: "GPT-5.5 high"
reviewer_tool: "Codex"
reviewer_model: "GPT-5.5 high"

## 1. System Identification

- system_name: 泛微 e-cology 9
- product_type: OA (Office Automation) / BPM
- version_or_assumed_version: "泛微 e-cology 9，具体补丁版本 / 部署形态 / 已启用模块待厂商或现场确认"
- deployment_model: On-premise (常见); SaaS 租户模式亦有提供
- backend_stack: Java (Spring-based), 支持 Oracle / SQL Server / MySQL / PostgreSQL

## 2. API Surface

### 2.1 Known API Types

| API Type | Evidence Tier | Notes |
|----------|--------------|-------|
| REST API (E9 开放接口) | needs_vendor_confirmation | e-cology 9 提供 RESTful 开放接口，官方文档称为"集成中心"或"开放接口"模块 |
| Workflow API | needs_vendor_confirmation | 流程创建、提交、审批、查询等，通过 REST 接口暴露 |
| Organization API | needs_vendor_confirmation | 部门、人员、角色等组织架构查询接口 |
| Document API | needs_vendor_confirmation | 文档知识库读取接口 |
| Form Data API | needs_vendor_confirmation | 表单数据读写接口 |

### 2.2 API Access Model

- evidence_tier: needs_vendor_confirmation
- API 访问是否需要单独购买模块或 License — 待确认
- 是否有独立的 API Gateway / API 管理界面 — 待确认
- REST API base path 格式 — 公开资料提及类似 `/api/ecology/...` 但具体路径需现场确认

## 3. Authentication & Authorization

### 3.1 Authentication Modes

| Mode | Evidence Tier | Status |
|------|--------------|--------|
| Session-based (cookie/sessionid) | needs_vendor_confirmation | e-cology 9 原生认证，基于浏览器 session |
| Username + Password | needs_vendor_confirmation | 标准登录认证 |
| OAuth2 | needs_vendor_confirmation | 部分公开资料提及 e-cology 9 支持 OAuth2，但需确认是否为标准模块、是否需要额外购买 |
| SSO (LDAP/AD/CAS/SAML) | community | 社区资料提及支持 LDAP/AD 对接和 CAS 单点登录，需确认 e-cology 9 当前版本支持范围 |
| API Token / App Credential | needs_vendor_confirmation | 第三方应用注册和 token 发放机制不明确 |

### 3.2 Token / Session Lifecycle

- evidence_tier: needs_vendor_confirmation
- session 超时配置 — 待确认（通常在系统管理中可配置）
- OAuth2 token 有效期 — 待确认（是否支持 refresh_token）
- API 调用是否使用独立 token 还是复用 session — 待确认

### 3.3 User-Level Permission

- evidence_tier: needs_vendor_confirmation (无可复核 source_id / URL)
- e-cology 9 自身有完整的权限体系：角色、部门、岗位、数据权限
- API 调用时权限是否完全由 e-cology 9 权限体系裁决 — 待确认
- 是否存在 API 专用的权限角色或授权范围 — 待确认

## 4. Identity Binding

- evidence_tier: needs_vendor_confirmation
- AI 平台用户与 e-cology 9 用户的映射方式 — 待确认
- 是否支持 impersonation / user-delegated 调用 — 待确认
- 最小需求：需要一个 e-cology 9 账号用于 API 调用，以及用户身份映射方案

## 5. Integration Capabilities

| Capability | Evidence Tier | Status |
|-----------|--------------|--------|
| Webhook / Callback | needs_vendor_confirmation | 是否支持流程事件回调待确认 |
| Rate Limit | needs_vendor_confirmation | API 频率限制待确认 |
| Audit Log | needs_vendor_confirmation | e-cology 9 有操作日志模块，但 API 级别审计粒度待确认 |
| Sandbox / Test Env | needs_vendor_confirmation | 是否存在测试环境待确认 |

## 6. Irreversible Operations

基于公开资料的推断（evidence_tier: community）：
- 流程审批通过/拒绝 — 不可撤回（取决于流程配置）
- 数据删除 — 需确认是否有回收站机制
- 人员离职/删除 — 可能影响历史流程数据

需现场确认完整列表。

## 7. Phase 0/1 Scope Assessment

| Operation | Phase 0 (Spike) | Phase 1 Recommendation |
|-----------|----------------|----------------------|
| 读取组织架构 | 不操作 | 允许只读 PoC（待确认） |
| 读取待办列表 | 不操作 | 允许只读 PoC（待确认） |
| 读取流程详情 | 不操作 | 允许只读 PoC（待确认） |
| 发起流程 | 不操作 | Phase 1 建议禁止写入，除非有沙箱 |
| 审批流程 | 不操作 | Phase 1 建议禁止写入，除非有沙箱 |
| 写入表单数据 | 不操作 | Phase 1 建议禁止写入 |

## 8. Open Questions

1. 当前 e-cology 9 具体补丁版本是什么？
2. 是否已购买或启用开放接口 / 集成中心 / OAuth2 / SSO 相关模块？
3. 第三方应用如何注册？
4. token / session 生命周期如何配置？
5. AI 平台代表用户调用时，权限是否完全由 e-cology 9 自身权限体系裁决？
6. 流程发起、流程审批、待办读取、组织架构读取、表单数据读取分别需要哪些授权？
7. 是否存在测试环境或沙箱环境？
8. 是否允许 Phase 1 只读 PoC？

## 9. Blocking Risks

1. API 模块是否需要额外购买 — 可能导致 Phase 1 无法推进
2. 无测试环境 — 无法安全验证 API 行为
3. 认证机制不明确 — 影响 Gateway 集成设计
4. 用户身份映射方案不明确 — 影响 Identity Binding 实现

## 10. Source Inventory / Evidence Sources

| source_id | evidence_tier | source_type | title_or_doc_name | url_or_local_reference | accessed_or_recorded_date | supports_which_claims | confidence |
|-----------|--------------|-------------|-------------------|----------------------|--------------------------|----------------------|------------|
| OA-SRC-01 | needs_vendor_confirmation | vendor_doc | 泛微 e-cology 9 官方产品文档 | needs_vendor_confirmation — 泛微官方文档通常为内网/购买后获取，无公开 URL | 2026-05-09 | API types (REST/Workflow/Org/Document/Form), session-based auth, username+password auth | low — 无公开 URL 可复核 |
| OA-SRC-02 | needs_vendor_confirmation | vendor_doc | 泛微 e-cology 9 集成中心 / 开放接口模块文档 | needs_vendor_confirmation — 模块文档通常随购买提供 | 2026-05-09 | API access model, third-party app registration | low |
| OA-SRC-03 | needs_vendor_confirmation | community | 开发者社区/CSDN 博客中关于 e-cology 9 REST API 的讨论 | needs_vendor_confirmation — 社区链接不稳定，不作为确认来源 | 2026-05-09 | OAuth2/SSO support (线索), REST API base path (线索) | very low — community 仅作线索 |
| OA-SRC-04 | needs_vendor_confirmation | vendor_doc | 泛微 e-cology 9 权限管理文档 | needs_vendor_confirmation | 2026-05-09 | Permission source of truth (基础) | low |

说明：泛微 e-cology 9 为国产企业级 OA 系统，官方文档通常不在公网公开发布，多为购买后获取或内网部署。本次探查无法获取可复核的公开 URL，所有 evidence_tier 已降级为 needs_vendor_confirmation。

## 11. Recommendation

`mock_only`, `can_build_adapter_later`, `needs_vendor_confirmation`

Phase 1 建议先做 Mock Adapter，待厂商确认 API 可用性和测试环境后再考虑真实 Adapter。
