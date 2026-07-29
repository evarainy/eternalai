# ADR-P2-OA-READ-002 — OA Live 读取、凭证引用与生产接线

- status: accepted
- date: 2026-07-30
- task_id: P2-READ-ADAPTER-001
- decision_makers: [雨爷, Codex]
- related_capability: `oa.list_pending_workflows`
- supersedes: 仅补充 `ADR-P2-OA-READ-001` 留给本棒的 Live / Credential / Identity 部分

## 1. Context

`P2-OA-READ-CONTRACT-001` 已冻结零参数 capability、Replay Provider、归一化模型、
Contract Pack 和三层离线脱敏，但生产 composition 尚未把真实 OA 读路径通电：

- OA 登录成功后，`OACredentialVerifier` 已用 HMAC surrogate `ai_user_id` 作为主键，
  将 OA userid 与 Cookie 以 AES-256-GCM 写入 `oa_session_credentials`。
- `CredentialStorePort` 只有 `store()`；`SecretProviderPort` 只有不返回明文的 Phase 0
  redacted/mock 方法。
- Gateway 在 Identity 与 Policy 通过后仍只构造 `{}` 加可选
  `mock_error_mode`，`OAReadAdapter` 丢弃该 context。
- `LiveOAReadProvider` 是显式未实现出口，production composition 默认没有真实 OA
  adapter 注册。

本棒第一次真正读取用户 OA Session，且改变 `execution_context` 的公共形状。目标是只为
`oa.list_pending_workflows` 打通最短可信路径，不扩成 Vault、通用 Connector、自动登录或
写能力。

## 2. Decision

### 2.1 凭证引用与读取接口

每个凭证引用固定为：

```text
oa-session-v1:<ai_user_id>
```

其中 `ai_user_id` 必须是服务端认证生成的 HMAC surrogate。SecretProvider 从 namespaced
ref 解出的 surrogate 必须逐字等于 `CredentialStore` 查询主键，并继续作为
`aes256gcm-v1 + NUL + ai_user_id` AAD 的同一个 `ai_user_id`；不允许重写、别名或客户端
替换。引用可进入 `execution_context`、安全管理视图和 Trace 引用字段，但 Gateway/Trace
最多只见该引用，原始 OA userid、Cookie、密文、nonce 均不可进入。

`CredentialStorePort` 保留 `store()` 并增加唯一读取方法：

```python
async def load(ai_user_id: str) -> OASessionCredential | None
```

- `None` 只表示没有该用户的凭证行。
- PostgreSQL 实现必须校验 `cipher_version`、nonce 长度、AES-GCM AAD、认证 tag、JSON
  形状和 `expires_at` 类型；任何损坏或不支持版本均用不含底层详情的统一安全错误
  fail-closed。
- AAD 继续使用 `aes256gcm-v1 + NUL + ai_user_id`，不尝试空 AAD、旧 key、明文或其他
  fallback。
- 返回值继续使用现有 `OASessionCredential`，OA userid 和 Cookie value 保持
  `SecretStr`；仅在组装请求的局部作用域调用 `get_secret_value()`。

`SecretProviderPort` 保留既有 redacted/mock 方法，并增加窄类型方法：

```python
async def resolve_oa_session(
    credential_ref: str,
) -> OASessionCredential
```

真实实现只接受上述 namespaced ref，解析出 surrogate 后调用 `CredentialStorePort.load()`，
并在返回前再次检查 TTL。缺失、过期、非法引用和存储损坏分别进入安全的 typed error；
异常消息、`repr` 和日志不包含引用以外的凭证信息。`NoopSecretProvider` 对该方法始终
fail-closed，不返回合成明文。

现有表已包含 `ai_user_id`、`cipher_version`、`nonce`、`encrypted_payload`、
`expires_at` 和 `updated_at`，因此本棒不增加 migration。

### 2.2 最小 IdentityMapping

`IdentityMappingPort` 与 `IdentityCheckResult` 不扩字段。复用既有 `binding_id` 保存安全、
不透明的 `oa-session-v1:<ai_user_id>` 引用，避免把 Secret 语义扩散到通用 Identity
契约。

新增只读 PostgreSQL OA mapping，且只支持：

```text
target_system = "oa"
execution_identity = "user_delegated"
```

它只查询 `oa_session_credentials.ai_user_id/expires_at`，绝不读取或解密
`encrypted_payload`：

| 数据状态 | `bind_status` | `reason_code` | `binding_id` |
|---|---|---|---|
| 无凭证行 | `unbound` | `identity_unbound` | null |
| 本地 TTL 已过期 | `expired` | `identity_expired` | null |
| 有效 | `active` | null | 安全引用 |
| 非 OA、非 user-delegated 或请求了本映射不支持的 account/device scope | fail-closed | 不回落其他身份 | null |

`get_mapping()` / `list_mappings()` 只返回同一安全投影。数据库读取异常按
`verification_failed` fail-closed。不存在写 Port，不新建绑定表；一用户一 OA Session
继续由现有主键保证。

Live 的 `binding_id` 只能由
`IdentityMapping.resolve_execution_identity(server_principal.ai_user_id, ...)` 的
`active + user_delegated + target_system="oa"` 结果产生。静态 Mock mapping 行不得成为
Live 生产绑定来源；显式 mapping seam 只供测试，production Live 默认必须构造上述
PostgreSQL mapping。

### 2.3 `execution_context` 的冻结形状

`AdapterPort.execute(..., execution_context: dict[str, Any])` 的函数签名不变；稀疏字典只
允许两类既有/新增键：

```python
{
    "credential_ref": "oa-session-v1:<HMAC surrogate>",  # 可选，服务端生成
    "mock_error_mode": "...",                            # 既有测试注错
}
```

Gateway 保持 `Registry → Identity → Policy → adapter selection` 的安全顺序。只有
`active + user_delegated + target_system="oa"` Identity 与 allow Policy 均通过，且
`IdentityCheckResult.target_system/execution_identity` 逐字匹配本次 capability 请求后，才把
`identity_result.binding_id` 复制为 `credential_ref`；不匹配视为 `verification_failed` 并
fail-closed。引用不得从 `arguments`、请求体、header、客户端 `execution_context`、客户端
session id 或客户端自报 Principal 读取。Gateway 不读取、不解密、不缓存 Secret，也不把
context 交给 Trace。

Mock 与 Replay 允许 context 无 `credential_ref`；Live 强制要求有效引用。新增键是向后
兼容的稀疏扩展，因此禁改的 `MockOAAdapter`、Golden fixtures/evaluator 和 pilot e2e
无需修改。

### 2.4 OA Adapter / Provider 与明文生命周期

内部 Provider 接口改为：

```python
requires_credential: bool

async def list_pending_workflows(
    credential: OASessionCredential | None = None,
) -> OAPendingWorkflowCollection
```

- Replay 声明 `requires_credential = False`，不解析引用、不解密，继续逐字段读取并精确
  验证 Contract Pack。
- Live 声明 `requires_credential = True`。`OAReadAdapter` 只在即将调用 Live 时，通过
  `SecretProvider.resolve_oa_session()` 解密；返回后不缓存 credential、HTTP session、
  CookieJar 或请求。
- Live 收到 `None`、无效引用或失效凭证必须 fail-closed；不得回落 Replay、Mock、
  service account、匿名或管理员身份。
- 每次调用使用隔离的 HTTP session，并显式禁用 `HTTP_PROXY`、`HTTPS_PROXY`、
  `ALL_PROXY` 等环境代理。Cookie 只发送到经配置验证的 OA base host；不跟随跨 host
  redirect，也不得把部署侧 unset proxy 当成安全前提。URL、Request、headers、
  CookieJar、响应体和异常对象均不得记录。
- 沿用标准库 HTTP 能力，不新增依赖；timeout、单响应大小和最大页数均有界，不自动重登，
  不在本棒增加 retry 基建。

`oa.list_pending_workflows` 的 `arguments` 仍由零字段模型严格校验。endpoint path、HTTP
method 和分页 wire contract 属于进程配置/Provider constructor，不是 capability 参数。
Live 在 provider 内循环请求并聚合页面；页号/页大小/cursor 永不上浮到 Gateway 或
Capability。

Live 的 Provider constructor 固定该 OA wire profile 的精确分页信号键形状；本棒默认
Ecology9 形状为 `hasMore + total + nextCursor`，每页观察到的分页信号键必须与配置逐字
一致。同一次调用首页 `total` 冻结后不得跨页变化，非空 `nextCursor` 不做 trim，下一请求
逐字携带。分页同时具备最大页数与重复 cursor 防护；缺少可判定终止条件、信号形状变化、
越界、矛盾页信息或超过边界均失败，不把部分结果报告为成功。该 constructor 配置不改写
既有不可变 Contract Pack profile。

### 2.5 Session 失效与错误语义

本棒不新增相近的公共错误码。对 user-delegated OA，既有 `identity_expired` 扩充为
“本地 TTL 已过期或 OA 明确判定 Session 失效，需要重新认证”：

| 条件 | `AdapterResult.error_code` / Gateway 结果 |
|---|---|
| 无绑定、active 后竞态删除导致凭证缺失 | `identity_unbound` → `binding_required` |
| 本地 TTL 到期、HTTP 401、已确认的 OA Session 失效业务信号 | `identity_expired` → `binding_required`，用户动作明确为重新认证 |
| 绑定已撤销 | `identity_revoked` → `binding_required` |
| 非法 ref、cipher/AAD/tag/JSON 解密或完整性校验失败 | `adapter_error`，安全失败且不暴露底层详情 |
| 有效 Session 下的 HTTP 403 / 权限不足业务信号 | `upstream_permission_denied` |
| timeout | `adapter_timeout` |
| HTTP 5xx | `adapter_http_500` |
| JSON、字段或归一化结构非法 | `adapter_payload_invalid` |
| 未知能力、非法 capability 参数、其他安全分类外失败 | `adapter_error` |

以上任何路径均不得回落 Replay、service account、匿名或管理员身份，也不得静默重登。
adapter 的兜底宽捕获必须留下仅含 capability 与安全分类码的错误日志；不得记录
exception message、traceback locals、context、请求或响应。

### 2.6 配置驱动的生产接线

单一模式配置：

```text
OA_READ_ADAPTER_MODE = mock | replay | live
```

默认必须是 `mock`。

- `mock`：注册既有 `MockOAAdapter`。
- `replay`：要求显式、存在的 `OA_READ_CONTRACT_PACK_DIR`，注册
  `OAReadAdapter(ReplayOAReadProvider(...))`。
- `live`：要求同一 Contract Pack 目录和显式、相对 OA host 的
  `OA_PENDING_WORKFLOWS_PATH`，构造真实 SecretProvider 与 Live provider。
- 非法 mode、缺失 Live/Replay 必需配置或不安全 path 均在 composition 时 fail-fast。
- `mock` / `replay` 下，`build_production_components(..., adapters=...)` 和
  `identity_mapping=...` 的显式测试 seam 优先于默认构造，保证
  `test_pilot_foundation_e2e.py` 继续使用其显式 Mock；Golden runner 继续直接构造 Mock。
- `live` 下拒绝上述两个 override，强制使用 PostgreSQL OA IdentityMapping、真实
  SecretProvider 与 Live adapter，静态测试 mapping/adapter 不得成为真实凭证来源。
- CredentialStore 在 composition 中只构造一次，同时供认证写入、IdentityMapping
  metadata 查询所依赖的 session factory，以及 SecretProvider 读取使用。

任何 Live 启动或运行错误都不允许自动改用 Replay/Mock。

### 2.7 Live 指纹漂移

保留 Replay 对完整 `fingerprint.json` 的精确相等。Live 只将聚合并归一化后的
`{"workflows": ...}` 与选定 Contract Pack 的 `eternalai-structural-v1` 指纹比较，不
比较业务值或列表长度。

新增纯比较器返回安全 drift report：

- `matches`、algorithm、expected/actual structural SHA；
- added / removed / changed 节点，只含 `path`、`json_type`、`nullable`、
  `array_shape`。

报告不得包含原始响应、业务值、数组长度、Cookie/OA userid，也不得 hash 原始响应。
生产 composition 必须注入安全 reporter：匹配时保持安静，漂移时只记录 algorithm、
expected/actual structural SHA 与 added/removed/changed 节点路径；不得把报告混入成功
业务数据。测试可注入内存 reporter。归一化模型本身不成立时直接
`adapter_payload_invalid`。

### 2.8 一次性内网采集与脱敏

采集必须在一次内网窗口覆盖：

1. 多页待办及完整翻页终止证据；
2. 单页待办；
3. 合法空列表；
4. Session 失效或过期；
5. 权限不足或其他错误响应。

HAR 原件、Cookie、姓名、工号和 OA userid 永远在仓库外。每个成功场景生成独立、不可
覆盖的 Contract Pack；错误场景只保留安全分类证据，不伪装成成功 sample。

sanitizer 增加可重复的显式 `--entry-index`。一个场景可按给定顺序选择一个或多个 entry，
多页 records 聚合后再归一化；未指定 selector 时保留“必须唯一候选”的旧行为。越界、
重复、非目标或非法 entry 均非零失败。

smoke 的 selector 必须按 HAR 请求顺序严格递增；三个成功场景及其全部选中 entry 只接受
同一 HTTP(S) scheme/host/port/path 的 `GET` 请求，多页还必须逐页锁定
page/pageSize/cursor 链与明确终止信号。请求 query 只允许这些分页键，非末页必须提供
非空、无首尾空白的 `nextCursor`，不做 trim 且下一请求逐字携带；不能把逆序、跨
endpoint、额外 query 变化、修剪 cursor 或无 cursor 的 entry 拼成分页证据。多页首次
观察到的分页信号键形状和 `total` 还必须在后续页保持不变。

三层防线保持原强度：

1. 每个被选响应逐一执行禁止键递归检查和正向白名单提取；
2. 敏感值继续扫描整个 HAR 的全部 entry，而非只扫描 selector；除 header/cookie 和递归
   敏感键外，还覆盖 request URL/queryString、`postData.params` 与
   `application/x-www-form-urlencoded` 正文中的敏感参数，并断言这些原始值不出现在全部
   候选输出；
3. 全部候选输出继续做禁止键和值模式扫描。

仍在同级临时目录生成、全部检查通过后原子发布；任一失败时目标目录不存在、非零退出，
不提供 `--force`、跳过检查或覆盖开关。

## 3. Security and privacy consequences

- 明文只在 `CredentialStore.load → SecretProvider → OAReadAdapter → Live HTTP` 的单次
  调用局部变量中存活。Python 不保证可物理清零 immutable string/bytes，本 ADR 不作
  “已清零”虚假承诺；控制手段是晚解密、短作用域、不缓存、不序列化、不记录。
- 凭证不得出现在 Trace、日志、exception/`__context__`、`repr`、`model_dump`、
  `asdict`、fixture、Contract Pack、drift report、测试回执或任务报告。
- Identity/Policy 短路发生在任何解密前；未绑定、过期、拒绝请求均不得触发 Live HTTP。
- 用户密码仍只用于一次登录请求，不存储、不新增读取接口，也不用于静默重登。

## 4. Consequences

- 不增加数据库 migration 或依赖；复用已落地 AES-256-GCM 行与标准库 HTTP。
- 生产模式可安全地在 Mock、显式 Replay 和显式 Live 之间切换，默认 Mock 不破坏
  Golden/pilot。
- `binding_id` 在本最小 OA mapping 中承担不透明 credential ref；正式多绑定、解绑管理、
  Vault/KMS、轮换和批量失效通知仍归后续 Secret/运营任务。
- Live 只闭合一个只读 capability；不产生写能力、通用 schema 校验或通用 Connector。

## 5. Rejected alternatives

- 把 Cookie/OA userid 放进 `execution_context`：扩大到 Gateway、Trace、所有 adapter 与
  `repr` 的泄漏面。
- IdentityMapping 预先解密：被 Policy 拒绝的请求也会读取 Secret，违反最短生命周期。
- 新增绑定表或 migration：现有一用户一 Session 主键和加密行已满足本棒。
- 共享服务账号、管理员 fallback、匿名 fallback、自动登录：违反 user-delegated 和
  binding_required 冻结语义。
- 默认 Live 或失败后 Replay：会破坏 Golden/pilot 并把真实失败伪装成成功。
- 将分页做成 capability 参数：违反 GT-001 零参数 schema。
- hash 原始 Live 响应：可能形成敏感数据指纹且会把业务值变化误报为结构漂移。

## 6. Evidence

- 基线：base `89cd16e33020806c8a7a28170f93689963de5235` 上
  `1219 passed, 0 skipped, 0 failed`。
- 现有凭证表与 AES-GCM 写入：
  `alembic/versions/20260724_090000_auth_credentials.py`、
  `app/infra/auth/postgresql.py`。
- 可信 Principal 链：`app/infra/auth/oa.py`、`app/api/v1/auth.py`、
  `app/api/v1/runtime.py`。
- Replay/Contract 冻结：`ADR-P2-OA-READ-001-replay-contract.md`。

## 7. Risks and open questions

- 内网现场可能在本地 TTL 前撤销 Session；Live 必须以已确认的 HTTP/业务信号识别并返回
  `identity_expired`，不能把未知登录页当成功 JSON。
- Contract Pack 指纹是归一化结构，不覆盖 vendor envelope 的未知新增字段；原始 envelope
  指纹若未来确有需要，必须另立不含业务值的契约任务，不能偷换现有算法。
- Vault/KMS、key rotation、多账号 binding、正式解绑/重置、Session 主动健康检查和写能力
  均明确 deferred；本棒无其他未决设计选择。
