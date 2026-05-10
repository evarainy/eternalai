# ADR-P0-SPIKE-005a — 泛微 e-cology 9 API & Authentication Reconnaissance

- task_id: P0-SPIKE-005
- date: 2026-05-09
- status: accepted
- decision_makers: [Phase 0 Spike]

## 1. Context

本项目需要与泛微 e-cology 9 OA 系统集成，实现流程审批、待办查询、组织架构读取等能力。Phase 0 Spike 阶段需要探查其 API 类型、认证机制和集成边界，为 Phase 1 的 Adapter 设计提供依据。

**重要声明**：本次探查基于公开资料（厂商官网、公开开发者文档、公开社区讨论），未连接任何生产系统。所有无法从公开资料确认的结论标记为 `needs_vendor_confirmation`。

## 2. System Profile

| 字段 | 值 |
|------|-----|
| system_name | 泛微 e-cology 9 |
| version_or_assumed_version | 泛微 e-cology 9，具体补丁版本 / 部署形态 / 已启用模块待厂商或现场确认 |
| product_type | OA / BPM |
| deployment_model | On-premise (常见); SaaS 租户模式亦有提供 |

## 3. API Type

| API 类型 | 可用性 | evidence_tier | 说明 |
|----------|--------|--------------|------|
| REST API (开放接口) | available | needs_vendor_confirmation | e-cology 9 提供 RESTful 开放接口，通过"集成中心"模块管理 |
| Workflow API | available | needs_vendor_confirmation | 流程创建、提交、审批、查询 |
| Organization API | available | needs_vendor_confirmation | 部门、人员、角色等组织架构查询 |
| Document API | available | needs_vendor_confirmation | 文档知识库读取 |
| Form Data API | available | needs_vendor_confirmation | 表单数据读写 |

## 4. Authentication Mode

| 认证方式 | evidence_tier | 状态 |
|----------|--------------|------|
| Session-based (cookie/sessionid) | needs_vendor_confirmation | e-cology 9 原生认证 |
| Username + Password | needs_vendor_confirmation | 标准登录认证 |
| OAuth2 | needs_vendor_confirmation | 部分资料提及支持，需确认是否为标准模块、是否需额外购买 |
| SSO (LDAP/AD/CAS/SAML) | needs_vendor_confirmation | 社区资料提及 LDAP/AD/CAS 支持，e-cology 9 当前版本支持范围待确认 |
| API Token / App Credential | needs_vendor_confirmation | 第三方应用注册和 token 发放机制不明确 |

## 5. Token / Session Lifecycle

- evidence_tier: needs_vendor_confirmation
- session 超时配置 — 待确认
- OAuth2 token 有效期 — 待确认（是否支持 refresh_token）
- API 调用使用独立 token 还是复用 session — 待确认

## 6. User Identity Mapping Method

- evidence_tier: needs_vendor_confirmation
- AI 平台用户与 e-cology 9 用户的映射方式待确认
- 是否支持 impersonation / user-delegated 调用待确认
- e-cology 9 用户账号可能是 identity binding 的 key

## 7. Permission Source of Truth

- evidence_tier: needs_vendor_confirmation (无公开可复核来源)
- e-cology 9 有完整的权限体系：角色、部门、岗位、数据权限
- API 调用时权限是否完全由 e-cology 9 权限体系裁决 — 待确认
- 是否存在 API 专用的权限角色或授权范围 — 待确认

## 8. Read/Write API Availability

| 操作 | 读 | 写 | evidence_tier |
|------|----|----|--------------|
| 流程操作（发起/审批） | available | available | needs_vendor_confirmation |
| 待办查询 | available | N/A | needs_vendor_confirmation |
| 组织架构 | available | needs_vendor_confirmation | needs_vendor_confirmation |
| 表单数据 | available | available | needs_vendor_confirmation |
| 文档知识库 | available | needs_vendor_confirmation | needs_vendor_confirmation |

## 9. Callback / Webhook Availability

- evidence_tier: needs_vendor_confirmation
- 是否支持流程事件回调（如流程到达某节点时推送通知）待确认
- 回调地址注册方式和安全性待确认

## 10. Rate / Concurrency Limit

- evidence_tier: needs_vendor_confirmation
- API 频率限制和并发限制待确认

## 11. Audit Log Availability

- evidence_tier: needs_vendor_confirmation (无公开可复核来源)
- e-cology 9 有操作日志模块
- API 级别审计粒度待确认

## 12. Sandbox / Test Environment Availability

- evidence_tier: needs_vendor_confirmation
- 是否存在测试环境或沙箱环境待确认

## 13. Irreversible Operation List

| 操作 | 不可逆风险 | evidence_tier |
|------|-----------|--------------|
| 流程审批通过/拒绝 | 取决于流程配置，可能不可撤回 | community |
| 数据删除 | 可能无回收站机制 | community |
| 人员离职/删除 | 可能影响历史流程数据 | community |

完整列表需现场确认。

## 14. Phase 0 Allowed / Forbidden Operations

| 操作 | Phase 0 | Phase 1 建议 |
|------|---------|-------------|
| 读取组织架构 | forbidden (spike 阶段不操作) | 允许只读 PoC（待确认） |
| 读取待办列表 | forbidden | 允许只读 PoC（待确认） |
| 读取流程详情 | forbidden | 允许只读 PoC（待确认） |
| 发起流程 | forbidden | 建议禁止写入，除非有沙箱 |
| 审批流程 | forbidden | 建议禁止写入，除非有沙箱 |
| 写入表单数据 | forbidden | 建议禁止写入 |

## 15. Identity Binding Minimal Requirement

- 最小需求：需要一个 e-cology 9 账号用于 API 调用
- 用户身份映射：需要确定 AI 平台用户与 e-cology 9 用户的映射方案
- 待确认项：是否支持 user-delegated 调用、是否需要应用注册

## 16. Open Questions

1. 当前 e-cology 9 具体补丁版本是什么？
2. 是否已购买或启用开放接口 / 集成中心 / OAuth2 / SSO 相关模块？
3. 第三方应用如何注册？
4. token / session 生命周期如何配置？
5. AI 平台代表用户调用时，权限是否完全由 e-cology 9 自身权限体系裁决？
6. 流程发起、流程审批、待办读取、组织架构读取、表单数据读取分别需要哪些授权？
7. 是否存在测试环境或沙箱环境？
8. 是否允许 Phase 1 只读 PoC？

## 17. Blocking Risks

1. API 模块是否需要额外购买 — 可能导致 Phase 1 无法推进
2. 无测试环境 — 无法安全验证 API 行为
3. 认证机制不明确 — 影响 Gateway 集成设计
4. 用户身份映射方案不明确 — 影响 Identity Binding 实现

## 18. Credential Vault Requirement

- evidence_tier: needs_vendor_confirmation
- vault_requirement: 需厂商/现场确认
- 说明：是否需要 Credential Vault 取决于 e-cology 9 实际采用的认证模式：
  - 如果采用 OAuth2 / SSO / app credential → 需要 Vault 托管 client_secret / app_secret
  - 如果采用 session credential（cookie/sessionid）→ 需要 Vault 托管服务账号密码
  - 如果采用 API Token → 需要 Vault 托管 token
- Phase 0 约束：只允许 mock credential reference，不保存真实账号密码、token、secret、api_key
- 待确认项：e-cology 9 启用的认证模块、凭证类型和轮换策略

### Evidence Sources

本 ADR 基于以下研究笔记中的 source_inventory：
- OA-SRC-01 ~ OA-SRC-04（详见 `docs/research/phase0/target_systems/oa_research_notes.md` Section 10）
- 所有 source 均标记为 needs_vendor_confirmation，无公开可复核 URL

## 19. Recommendation

`mock_only`, `can_build_adapter_later`, `needs_vendor_confirmation`

Phase 1 建议先做 Mock Adapter，待厂商确认以下事项后再考虑真实 Adapter：
- API 模块购买和启用状态
- 认证方式和 token 机制
- 测试环境可用性
- 用户身份映射方案
