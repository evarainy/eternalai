# Research Notes — 海康 iVMS API & Auth

task_id: P0-SPIKE-005
system: 海康 iVMS
date: 2026-05-09
executor_tool: "Codex"
executor_model: "GPT-5.5 high"
reviewer_tool: "Codex"
reviewer_model: "GPT-5.5 high"

## 1. System Identification

- system_name: 海康威视 iVMS (iVMS-8200 / iVMS-8700 等)
- product_type: 视频管理 / 安防综合管理平台
- version_or_assumed_version: "海康 iVMS，具体产品线和版本待现场确认（iVMS-8200 综合安防管理平台 / iVMS-8700 等）"
- deployment_model: On-premise（私有化部署为主）
- backend_stack: C/C++ (设备层); Java (平台层); 数据库: MySQL / PostgreSQL / Oracle

## 2. API Surface

### 2.1 Known API Types

| API Type | Evidence Tier | Notes |
|----------|--------------|-------|
| ISAPI (Internet Surveillance API) | official | 海康设备级 REST API 标准，基于 HTTP/HTTPS，支持设备管理和视频流控制 |
| OpenAPI (Hikvision Open Platform) | official | 海康开放平台提供的云端/本地 API，覆盖设备管理、视频预览、回放、报警等 |
| SDK (HCNetSDK / PlayCtrl) | official | 本地 C/C++ SDK，用于深度集成视频流 |
| EHome / ISUP | official | 设备接入协议，用于设备注册和信令交互 |

### 2.2 API Access Model

- ISAPI: 设备直连 REST API，endpoint 在设备 IP 上
- OpenAPI: 通过海康开放平台门户注册应用，获取 appKey/appSecret
- SDK: 需要本地集成，不适用于 Web 场景

## 3. Authentication & Authorization

### 3.1 Authentication Modes

| Mode | Evidence Tier | Status |
|------|--------------|--------|
| Digest Auth (HTTP Digest) | official | ISAPI 标准认证方式，基于用户名/密码的 HTTP Digest 认证 |
| Basic Auth (HTTPS) | official | 部分 ISAPI 接口在 HTTPS 下支持 Basic Auth |
| Token-based (OpenAPI) | official | 通过 appKey + appSecret 获取 access_token |
| OAuth2 | needs_vendor_confirmation | OpenAPI 是否支持标准 OAuth2 流程待确认，部分资料提及支持但需核实 |
| Session Cookie | needs_vendor_confirmation | iVMS Web 端管理界面使用 session cookie 认证，无官方文档 URL 可引用 |

### 3.2 Token / Session Lifecycle

- evidence_tier: official (OpenAPI 概述) + needs_vendor_confirmation (细节)
- OpenAPI access_token 有效期: 公开资料提及约 30 天（需确认具体值）
- 是否支持 refresh_token — 待确认
- ISAPI Digest Auth: 无 token 概念，每次请求携带认证头

### 3.3 User-Level Permission

- evidence_tier: needs_vendor_confirmation (无官方文档 URL 可引用)
- iVMS 平台有用户体系：管理员、操作员、普通用户等角色
- 设备级权限：可控制用户对特定设备/通道的访问权限
- API 调用时的权限裁决机制 — 需确认（OpenAPI 是否按 app 权限还是用户权限）

## 4. Identity Binding

- evidence_tier: needs_vendor_confirmation
- AI 平台用户与 iVMS 用户的映射方式 — 待确认
- OpenAPI 是否支持 user-delegated 调用（以特定用户身份操作）— 待确认
- 最小需求：需要一个 iVMS 账号或 OpenAPI 应用凭证

## 5. Integration Capabilities

| Capability | Evidence Tier | Status |
|-----------|--------------|--------|
| Webhook / Alarm Callback | official | iVMS 支持报警事件推送（HTTP 回调） |
| Video Stream (RTSP/HLS) | official | 视频流通过标准协议获取 |
| Rate Limit | needs_vendor_confirmation | API 频率限制待确认 |
| Audit Log | needs_vendor_confirmation | iVMS 有操作日志和报警日志，无官方文档 URL 可引用 |
| Sandbox / Test Env | needs_vendor_confirmation | 是否存在测试环境待确认 |

## 6. Irreversible Operations

基于公开资料的推断（evidence_tier: official/community）：
- 删除设备 — 从平台移除设备（可重新添加但配置丢失）
- 清除录像 — 录像文件删除不可恢复
- 修改设备配置 — 可能影响设备运行
- 门禁控制操作（开门/关门）— 物理操作不可撤回

## 7. Phase 0/1 Scope Assessment

| Operation | Phase 0 (Spike) | Phase 1 Recommendation |
|-----------|----------------|----------------------|
| 读取设备列表 | 不操作 | 允许只读 PoC |
| 读取实时视频流 | 不操作 | 允许只读 PoC（带宽和并发需评估） |
| 读取录像回放 | 不操作 | 允许只读 PoC |
| 读取报警事件 | 不操作 | 允许只读 PoC |
| 控制设备（PTZ、门禁） | 不操作 | Phase 1 建议禁止写入 |
| 修改设备配置 | 不操作 | Phase 1 建议禁止写入 |

## 8. Open Questions

1. 当前部署的是 iVMS 哪个产品线和版本？
2. 是否已注册海康开放平台应用（有 appKey/appSecret）？
3. 设备级 API (ISAPI) 和平台级 API (OpenAPI) 哪个为主要集成方式？
4. token 生命周期具体配置？
5. AI 平台代表用户调用时，权限如何裁决？
6. 是否存在测试环境或沙箱？
7. 是否允许 Phase 1 只读 PoC？
8. 网络拓扑：AI 平台是否能直接访问 iVMS 服务器？

## 9. Blocking Risks

1. 网络隔离 — iVMS 通常部署在安防专网，可能无法从 AI 平台直接访问
2. 设备 vs 平台 API 选择 — 需确定集成层次
3. 视频流带宽 — 实时视频流对网络带宽要求高
4. 物理操作风险 — 门禁控制等操作有安全风险

## 10. Source Inventory / Evidence Sources

| source_id | evidence_tier | source_type | title_or_doc_name | url_or_local_reference | accessed_or_recorded_date | supports_which_claims | confidence |
|-----------|--------------|-------------|-------------------|----------------------|--------------------------|----------------------|------------|
| HK-SRC-01 | official | vendor_doc | 海康威视 ISAPI 协议文档 | https://open.hikvision.com/docs — ISAPI 协议入口 | 2026-05-09 | ISAPI (Internet Surveillance API), HTTP Digest Auth, Basic Auth (HTTPS) | high — 官方公开文档 |
| HK-SRC-02 | official | vendor_doc | 海康开放平台开发者文档 | https://open.hikvision.com — OpenAPI 文档入口 | 2026-05-09 | OpenAPI (appKey/appSecret), Token-based auth, access_token 获取 | high — 官方公开平台 |
| HK-SRC-03 | official | SDK README | HCNetSDK / PlayCtrl SDK 文档 | 海康官网 SDK 下载页（需注册） | 2026-05-09 | SDK (HCNetSDK/PlayCtrl) | high — 官方 SDK |
| HK-SRC-04 | official | vendor_doc | EHome / ISUP 协议文档 | https://open.hikvision.com — 设备接入协议文档 | 2026-05-09 | EHome / ISUP 协议 | high |
| HK-SRC-05 | official | vendor_doc | 海康 OpenAPI access_token 说明 | https://open.hikvision.com — token 获取接口文档 | 2026-05-09 | token 有效期（约 30 天，需确认具体值） | medium — 具体值需现场确认 |
| HK-SRC-06 | needs_vendor_confirmation | community | 开发者论坛中关于 iVMS 报警回调的讨论 | needs_vendor_confirmation — 社区链接不稳定 | 2026-05-09 | Alarm Callback / Webhook (线索) | low — community 仅作线索 |
| HK-SRC-07 | needs_vendor_confirmation | community | 开发者论坛中关于 iVMS 用户体系的讨论 | needs_vendor_confirmation | 2026-05-09 | User-level permission (线索) | low |

说明：海康威视 ISAPI 和 OpenAPI 文档在海康开放平台 (open.hikvision.com) 公开发布，可复核。但 iVMS 平台层面的部分功能（审计日志、用户权限裁决）无单独公开文档，已降级为 needs_vendor_confirmation。

## 11. Recommendation

`mock_only`, `can_build_adapter_later`, `needs_vendor_confirmation`

Phase 1 建议先做 Mock Adapter。海康 iVMS 的 API 文档相对公开（ISAPI 文档可在海康官网获取），但具体部署环境、网络可达性和授权范围需现场确认。视频类 API 对带宽和延迟有特殊要求，需专项评估。
