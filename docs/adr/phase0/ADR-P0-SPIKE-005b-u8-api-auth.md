# ADR-P0-SPIKE-005b — 用友 U8+ API & Authentication Reconnaissance

- task_id: P0-SPIKE-005
- date: 2026-05-09
- status: accepted
- decision_makers: [Phase 0 Spike]

## 1. Context

本项目需要与用友 U8+ ERP 系统集成，实现基础档案查询、库存查询、单据读取等能力。Phase 0 Spike 阶段需要探查其 API 类型、认证机制和集成边界。

**重要声明**：本次探查基于公开资料，未连接任何生产系统。所有无法从公开资料确认的结论标记为 `needs_vendor_confirmation`。

## 2. System Profile

| 字段 | 值 |
|------|-----|
| system_name | 用友 U8+ |
| version_or_assumed_version | 用友 U8+ (U8 Cloud 或 U8+V16/V18)，具体版本待现场确认 |
| product_type | ERP |
| deployment_model | On-premise 为主; U8 Cloud 为 SaaS 模式 |

## 3. API Type

| API 类型 | 可用性 | evidence_tier | 说明 |
|----------|--------|--------------|------|
| COM/DCOM API | available | needs_vendor_confirmation | U8 传统集成方式，通过 COM 组件调用业务逻辑 |
| EAI (Enterprise Application Integration) | available | needs_vendor_confirmation | 内置 EAI 接口，支持 XML 格式业务数据交换 |
| Web Service (SOAP) | available | needs_vendor_confirmation | 部分版本提供 WSDL 接口 |
| REST API | needs_vendor_confirmation | community | 较新版本或 U8 Cloud 可能提供 REST 接口，需确认版本和可用性 |
| Open API (用友开放平台) | needs_vendor_confirmation | community | 用友开放平台是否覆盖 U8 待确认 |

## 4. Authentication Mode

| 认证方式 | evidence_tier | 状态 |
|----------|--------------|------|
| Application-level auth (操作员编号+密码) | needs_vendor_confirmation | U8 自身用户体系 |
| Database-level auth | needs_vendor_confirmation | SQL Server 数据库权限 |
| Windows Auth / AD | community | 部分部署场景支持 Windows 集成认证 |
| API Token / App Key | needs_vendor_confirmation | 新版本或 U8 Cloud 是否支持待确认 |
| OAuth2 | needs_vendor_confirmation | 用友开放平台是否为 U8 提供 OAuth2 待确认 |

## 5. Token / Session Lifecycle

- evidence_tier: needs_vendor_confirmation
- 传统 COM 调用通过登录接口获取 session，调用完毕后登出
- REST API（如果存在）的 token 机制待确认
- 是否支持长期 token 或 refresh_token — 待确认

## 6. User Identity Mapping Method

- evidence_tier: needs_vendor_confirmation
- AI 平台用户与 U8 操作员的映射方式待确认
- U8 操作员编号可能是 identity binding 的 key
- 最小需求：需要一个 U8 操作员账号用于 API 调用

## 7. Permission Source of Truth

- evidence_tier: needs_vendor_confirmation (无公开可复核来源)
- U8 有完整的操作员权限体系：功能权限、数据权限、字段权限
- API 调用时是否继承操作员权限 — 需确认
- 是否存在 API 专用角色 — 需确认

## 8. Read/Write API Availability

| 操作 | 读 | 写 | evidence_tier |
|------|----|----|--------------|
| 基础档案（客户/供应商/物料） | available | available | needs_vendor_confirmation |
| 库存数据 | available | available | needs_vendor_confirmation |
| 财务凭证 | available | available | needs_vendor_confirmation |
| 销售/采购单据 | available | available | needs_vendor_confirmation |

注：写入操作是否在当前权限和环境下安全可用需现场确认。

## 9. Callback / Webhook Availability

- evidence_tier: needs_vendor_confirmation
- U8 是否支持事件推送（如单据状态变更通知）待确认

## 10. Rate / Concurrency Limit

- evidence_tier: needs_vendor_confirmation
- API 频率限制和并发限制待确认
- COM 组件的并发处理能力可能有限

## 11. Audit Log Availability

- evidence_tier: needs_vendor_confirmation (无公开可复核来源)
- U8 有上机日志模块，记录操作员登录和操作

## 12. Sandbox / Test Environment Availability

- evidence_tier: needs_vendor_confirmation
- 是否存在测试账套或沙箱环境待确认

## 13. Irreversible Operation List

| 操作 | 不可逆风险 | evidence_tier |
|------|-----------|--------------|
| 凭证记账/审核 | 财务数据影响 | community |
| 库存出入库确认 | 影响库存数据 | community |
| 销售/采购订单审核 | 影响业务流程 | community |

完整列表需现场确认。

## 14. Phase 0 Allowed / Forbidden Operations

| 操作 | Phase 0 | Phase 1 建议 |
|------|---------|-------------|
| 读取基础档案 | forbidden | 允许只读 PoC（待确认） |
| 读取库存数据 | forbidden | 允许只读 PoC（待确认） |
| 读取财务凭证 | forbidden | 需谨慎，可能涉及敏感数据 |
| 创建单据 | forbidden | 建议禁止写入 |
| 审核单据 | forbidden | 建议禁止写入 |

## 15. Identity Binding Minimal Requirement

- 最小需求：需要一个 U8 操作员账号用于 API 调用
- 用户身份映射：需要确定 AI 平台用户与 U8 操作员的映射方案
- 待确认项：COM API 是否支持 user-delegated 调用、是否需要应用注册

## 16. Open Questions

1. 当前部署的是 U8+ 哪个具体版本？
2. 使用的是 U8+ 本地版还是 U8 Cloud？
3. 是否有可用的 REST API 或 Open API？
4. COM/DCOM 集成是否允许从外部应用调用？
5. 是否存在测试账套或沙箱环境？
6. AI 平台代表用户调用时，权限如何裁决？
7. 是否允许 Phase 1 只读 PoC？

## 17. Blocking Risks

1. COM/DCOM 依赖 — 可能限制跨平台调用，Adapter 需运行在 Windows 环境
2. 内网部署 — 可能需要 VPN 或代理才能访问
3. 无 REST API — 如果只有 COM/EAI 接口，Adapter 实现复杂度显著增加
4. 财务模块数据敏感 — 需要额外的安全评估

## 18. Credential Vault Requirement

- evidence_tier: needs_vendor_confirmation
- vault_requirement: 大概率需要 Credential Vault 或等价凭证托管，但仍需厂商/现场确认
- 说明：
  - U8 传统模式使用操作员编号+密码 → 需要 Vault 托管服务账号密码，不得写死在配置中
  - 如果 U8 Cloud 或新版本支持 API Token / appKey → 需要 Vault 托管 token/secret
  - COM/DCOM 调用中的 session 凭证也需要安全存储
- Phase 0 约束：只允许 mock credential reference，不保存真实账号密码、token、secret、api_key
- 待确认项：U8 版本、认证模式、凭证类型

### Evidence Sources

本 ADR 基于以下研究笔记中的 source_inventory：
- U8-SRC-01 ~ U8-SRC-04（详见 `docs/research/phase0/target_systems/u8_research_notes.md` Section 10）
- 所有 source 均标记为 needs_vendor_confirmation，无公开可复核 URL

## 19. Recommendation

`mock_only`, `can_build_adapter_later`, `needs_vendor_confirmation`

Phase 1 建议先做 Mock Adapter。U8 的 API 形态取决于具体版本和部署方式：
- 如果有 REST API → 可以较早构建真实 Adapter
- 如果只有 COM/DCOM → 需要 Windows 环境的中间层，复杂度高
- 如果是 U8 Cloud → 可能有标准 REST API

需现场确认后再决定是否以及如何构建真实 Adapter。
