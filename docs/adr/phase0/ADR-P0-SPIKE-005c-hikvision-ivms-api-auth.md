# ADR-P0-SPIKE-005c — 海康 iVMS API & Authentication Reconnaissance

- task_id: P0-SPIKE-005
- date: 2026-05-09
- status: accepted
- decision_makers: [Phase 0 Spike]

## 1. Context

本项目需要与海康威视 iVMS 视频管理平台集成，实现设备查询、视频预览、报警事件读取等能力。Phase 0 Spike 阶段需要探查其 API 类型、认证机制和集成边界。

**重要声明**：本次探查基于公开资料（海康官网 ISAPI 文档、海康开放平台文档、公开社区讨论），未连接任何生产系统。

## 2. System Profile

| 字段 | 值 |
|------|-----|
| system_name | 海康威视 iVMS |
| version_or_assumed_version | 海康 iVMS，具体产品线和版本待现场确认（iVMS-8200 综合安防管理平台 / iVMS-8700 等） |
| product_type | 视频管理 / 安防综合管理平台 |
| deployment_model | On-premise (私有化部署为主) |

## 3. API Type

| API 类型 | 可用性 | evidence_tier | 说明 |
|----------|--------|--------------|------|
| ISAPI (Internet Surveillance API) | available | official | 海康设备级 REST API 标准，基于 HTTP/HTTPS，官方文档公开发布 |
| OpenAPI (海康开放平台) | available | official | 覆盖设备管理、视频预览、回放、报警等，通过开放平台门户获取凭证 |
| SDK (HCNetSDK / PlayCtrl) | available | official | 本地 C/C++ SDK，适用于深度集成 |
| EHome / ISUP | available | official | 设备接入协议 |

## 4. Authentication Mode

| 认证方式 | evidence_tier | 状态 |
|----------|--------------|------|
| HTTP Digest Auth | official | ISAPI 标准认证方式 |
| Basic Auth (HTTPS) | official | 部分 ISAPI 接口在 HTTPS 下支持 |
| Token-based (OpenAPI appKey/appSecret) | official | 通过 appKey + appSecret 获取 access_token |
| OAuth2 | needs_vendor_confirmation | OpenAPI 是否支持标准 OAuth2 流程待确认 |
| Session Cookie | community | iVMS Web 端管理界面使用 |

## 5. Token / Session Lifecycle

- evidence_tier: official (OpenAPI 概述) + needs_vendor_confirmation (细节)
- OpenAPI access_token 有效期: 公开资料提及约 30 天（需确认具体值）
- 是否支持 refresh_token — 待确认
- ISAPI Digest Auth: 无 token 概念，每次请求携带认证头

## 6. User Identity Mapping Method

- evidence_tier: needs_vendor_confirmation
- AI 平台用户与 iVMS 用户的映射方式待确认
- OpenAPI 是否支持 user-delegated 调用（以特定用户身份操作）待确认
- 可能的方案：全局应用凭证 vs 用户级 token

## 7. Permission Source of Truth

- evidence_tier: needs_vendor_confirmation (无官方文档 URL 可引用)
- iVMS 平台有用户体系：管理员、操作员、普通用户等角色
- 设备级权限：可控制用户对特定设备/通道的访问权限
- API 调用时权限按 app 权限还是用户权限裁决 — 需确认

## 8. Read/Write API Availability

| 操作 | 读 | 写 | evidence_tier |
|------|----|----|--------------|
| 设备列表查询 | available | N/A | official |
| 实时视频预览 | available | N/A | official |
| 录像回放 | available | N/A | official |
| 报警事件查询 | available | N/A | official |
| PTZ 控制 | N/A | available | official |
| 门禁控制 | N/A | available | official |
| 设备配置修改 | N/A | available | official |

## 9. Callback / Webhook Availability

- evidence_tier: official
- iVMS 支持报警事件推送（HTTP 回调）
- 回调地址注册方式和安全性需确认

## 10. Rate / Concurrency Limit

- evidence_tier: needs_vendor_confirmation
- API 频率限制待确认
- 视频流并发数受服务器硬件和带宽限制

## 11. Audit Log Availability

- evidence_tier: needs_vendor_confirmation (无官方文档 URL 可引用)
- iVMS 有操作日志和报警日志

## 12. Sandbox / Test Environment Availability

- evidence_tier: needs_vendor_confirmation
- 是否存在测试环境或沙箱待确认
- 海康设备本身可作为测试目标（如果有测试设备）

## 13. Irreversible Operation List

| 操作 | 不可逆风险 | evidence_tier |
|------|-----------|--------------|
| 删除设备 | 从平台移除（可重新添加但配置丢失） | needs_vendor_confirmation |
| 清除录像 | 录像文件删除不可恢复 | needs_vendor_confirmation |
| 门禁控制（开门/关门） | 物理操作不可撤回 | needs_vendor_confirmation |
| 修改设备配置 | 可能影响设备运行 | community |

## 14. Phase 0 Allowed / Forbidden Operations

| 操作 | Phase 0 | Phase 1 建议 |
|------|---------|-------------|
| 读取设备列表 | forbidden | 允许只读 PoC |
| 读取实时视频流 | forbidden | 允许只读 PoC（带宽和并发需评估） |
| 读取录像回放 | forbidden | 允许只读 PoC |
| 读取报警事件 | forbidden | 允许只读 PoC |
| 控制设备（PTZ、门禁） | forbidden | 建议禁止写入 |
| 修改设备配置 | forbidden | 建议禁止写入 |

## 15. Identity Binding Minimal Requirement

- 最小需求：需要一个 iVMS 账号或 OpenAPI 应用凭证 (appKey/appSecret)
- 用户身份映射：需要确定 AI 平台用户与 iVMS 用户的映射方案
- 待确认项：OpenAPI 是否支持 user-delegated 调用

## 16. Open Questions

1. 当前部署的是 iVMS 哪个产品线和版本？
2. 是否已注册海康开放平台应用（有 appKey/appSecret）？
3. 设备级 API (ISAPI) 和平台级 API (OpenAPI) 哪个为主要集成方式？
4. token 生命周期具体配置？
5. AI 平台代表用户调用时，权限如何裁决？
6. 是否存在测试环境或沙箱？
7. 是否允许 Phase 1 只读 PoC？
8. 网络拓扑：AI 平台是否能直接访问 iVMS 服务器？

## 17. Blocking Risks

1. 网络隔离 — iVMS 通常部署在安防专网，可能无法从 AI 平台直接访问
2. 设备 vs 平台 API 选择 — 需确定集成层次
3. 视频流带宽 — 实时视频流对网络带宽要求高
4. 物理操作风险 — 门禁控制等操作有安全风险，Phase 1 必须禁止

## 18. Credential Vault Requirement

- evidence_tier: needs_vendor_confirmation
- vault_requirement: 需厂商/现场确认，取决于 AK/SK、appKey/appSecret、token/session 模式和现场网关配置
- 说明：
  - OpenAPI 模式使用 appKey + appSecret → 需要 Vault 托管 appSecret
  - OpenAPI access_token 也需要安全存储和轮换管理
  - ISAPI Digest Auth 使用用户名+密码 → 需要 Vault 托管设备凭证
  - 具体 Vault 方案取决于现场网关配置和安全策略
- Phase 0 约束：只允许 mock credential reference，不保存真实账号密码、token、secret、api_key、appKey、appSecret
- 待确认项：是否已注册海康开放平台应用、凭证类型和生命周期、现场网关配置

### Evidence Sources

本 ADR 基于以下研究笔记中的 source_inventory：
- HK-SRC-01 ~ HK-SRC-07（详见 `docs/research/phase0/target_systems/hikvision_ivms_research_notes.md` Section 10）
- HK-SRC-01 ~ HK-SRC-05 为 official（海康开放平台公开文档，可复核）
- HK-SRC-06 ~ HK-SRC-07 为 needs_vendor_confirmation

## 19. Recommendation

`mock_only`, `can_build_adapter_later`, `needs_vendor_confirmation`

海康 iVMS 的 API 文档相对公开（ISAPI 文档可在海康官网获取），认证方式较明确（Digest Auth / Token-based）。与另外两个系统相比，iVMS 的 API 可用性证据更充分，但以下事项仍需现场确认：
- 网络可达性
- 测试环境
- 用户级权限裁决
- 视频流集成的技术约束

Phase 1 建议先做 Mock Adapter，有条件时可优先对 iVMS 构建只读 Adapter。
