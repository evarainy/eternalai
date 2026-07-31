# ADR-P2-IDENTITY-CREDENTIAL-001 — OA 凭证撤销与重置运营契约

- status: accepted
- date: 2026-07-31
- task_id: P2-IDENTITY-CREDENTIAL-001
- decision_makers: [雨爷, Codex]
- related_capability: `oa.list_pending_workflows`
- related_adrs:
  - `ADR-P2-OA-READ-002-live-credential-boundary.md`

## 1. Context

`P2-AUTH-001` 与 `P2-READ-ADAPTER-001` 已完成每用户 OA Session 的 AES-256-GCM
存取、TTL、服务端生成的 `credential_ref`、Gateway 注入和调用前晚解密。当前
`oa_session_credentials` 每个 `ai_user_id` 只有一行，
`binding_id = "oa-session-v1:<ai_user_id>"` 是该 credential row 的安全引用投影，
不是独立绑定实体。

本任务只补齐运营侧的单 binding 撤销与重置。撤销改变的是 EternalAI 是否继续代表该
用户使用已保存的 OA Session，不改变 OA 服务器自身的 Session 状态，也不创建共享账号、
管理员替身或匿名回落。

## 2. Authorization record and exact boundaries

雨爷于 2026-07-30 授权：

1. 只给 `oa_session_credentials` 新增一个 nullable
   `revoked_at TIMESTAMPTZ NULL` 列；不得新增第二列、ALTER 既有列、新建表或删除数据行。
2. 新增：
   - `POST /api/v1/admin/bindings/{binding_id}/revoke`
   - `POST /api/v1/admin/bindings/{binding_id}/reset`

雨爷于 2026-07-31 进一步裁决并解除在途语义阻断：

- 采纳本 ADR 第 3.6 节的残留窗口，不引入 advisory lock、DB lease 或请求注册表。
- `revoked_at` 检查紧贴取密之前；撤销提交后的新请求不得再次取用凭证。
- 只允许在 `app/admin/actions.py` 与 `app/admin/registry.py` 增加上述两个 action 所需的
  注册与独立实现；`AdminRegistryService` 现有类体必须零 diff，任何既有 action 条目与
  行为也必须零改动。
- downgrade 只可逆转本迁移新增的 `revoked_at`，并且只在
  `127.0.0.1:15432` 测试库执行；不得对开发库、生产库或任何其他数据库执行。
- 全链路过期边界统一为 `expires_at <= now()`；此前启动文件中的 `< now()` 作废，测试
  必须锁定 `expires_at == now()` 为 expired。

监理预裁同时要求 `IdentityMappingPort` 保留 revoke/reset 两个最小写契约，并在同一改动
同步全部实现与测试；不得把 reset 隐藏成 Admin 对 revoke 私有实现的直接复用。

## 3. Decision

### 3.1 State model and precedence

只从原有 `expires_at` 与新增 `revoked_at` 推导状态，优先级固定为：

1. `revoked_at IS NOT NULL` → `revoked`，`reason_code="identity_revoked"`；
2. 否则 `expires_at <= now()` → `expired`，`reason_code="identity_expired"`；
3. 否则 → `active`。

同一时刻 `expires_at == now()` 必须判定为 expired。撤销只设置 `revoked_at`，绝不把
`expires_at` 改到过去；因此管理员主动撤销与自然过期在状态及审计中始终可区分。

只要 credential row 存在，active、expired、revoked 三种投影均保留安全的
`binding_id`。Gateway 仍只在 `bind_status == "active"` 时注入 `credential_ref`，所以
保留引用不会释放执行权限，却允许管理员定位已过期或已撤销的 binding 并进行幂等操作。

### 3.2 Minimal `IdentityMappingPort` write contract

Port 新增一个安全结果模型、一个安全异常和两个最小方法：

```python
class IdentityMappingMutationResult(BaseModel):
    mapping: IdentityCheckResult
    previous_bind_status: IdentityBindStatus
    changed: bool


class IdentityMappingMutationError(RuntimeError):
    pass


async def revoke_mapping(
    self,
    binding_id: str,
) -> IdentityMappingMutationResult | None: ...


async def reset_mapping(
    self,
    binding_id: str,
) -> IdentityMappingMutationResult | None: ...
```

契约边界：

- 输入只有服务端生成的 `binding_id`；不接收 actor、角色、时间戳、密码、Cookie、
  credential 或任意 payload。
- 只处理 `oa-session-v1:<trusted ai_user_id surrogate>`；非法格式、非 OA binding 或不存在
  的 row 返回 `None`，Admin API 统一返回明确的 `binding_not_found`，不形成枚举差异。
- 成功结果的 `mapping.bind_status` 必须为 `revoked`，并保留安全 binding 引用。
- `changed=True` 只表示本次首次写入 `revoked_at`；重复操作返回
  `changed=False`，保留首次 `revoked_at`，不得报错。
- 存储或时钟失败抛出不含底层上下文的 `IdentityMappingMutationError`，不得泄露 SQL、
  credential 或异常链。
- PostgreSQL、Mock 与 Unconfigured 三个实现和对应测试必须在同一实现 commit 同步；不得
  用 PostgreSQL 私有方法、router 私取属性或具体实现直连绕开 Port。

两个方法具有相同的单列持久化效果，但按已裁定契约保留不同的领域意图，避免 Admin 层
伪造或猜测操作类型；这两个具名方法是获批的最小边界，不再增加通用 mutation、payload
或状态切换方法。

### 3.3 Revoke and reset semantics

- **revoke**：撤销当前 EternalAI OA Session binding；不自动启动认证流程，响应
  `action="revoke"`、`next_action="none"`。
- **reset**：先执行同一撤销动作，再明确要求用户通过既有登录入口重新认证；响应
  `action="reset"`、`next_action="reauthenticate"`。
- 两者都不保存或生成 OA 密码，不静默重登，不删除 credential row，不切换到 service
  account、管理员、匿名或默认身份。
- 后续一次成功的显式 OA 登录写入新的 Session credential 时，既有 UPSERT 同步把
  `revoked_at` 清回 `NULL`；失败登录不得清除撤销状态。

本单列模型不表达“永久禁止用户再次登录”。若未来需要 account disable、批量失效或
审批后恢复，必须另立任务并重新批准数据模型。

### 3.4 Admin API, authorization and response

新增两个 `AdminAction`：

| action | policy capability | endpoint |
|---|---|---|
| `bindings_revoke` | `admin_bindings_revoke` | `POST /api/v1/admin/bindings/{binding_id}/revoke` |
| `bindings_reset` | `admin_bindings_reset` | `POST /api/v1/admin/bindings/{binding_id}/reset` |

`AdminRegistryService` 现有类体保持逐行零 diff。`app/admin/registry.py` 仅在该类体之外
新增独立 `AdminBindingMutationService`，由它持有专用的 IdentityMapping Port、Policy 与
Trace 引用并实现 revoke/reset；再新增一个薄的
`AdminRegistryServiceWithBindingMutations` 组合子类。组合子类只保存 mutation service 并
把两个新公开方法委托给它，全部既有 Admin 方法原样继承，绝不访问父类 private attribute。

composition 用原有依赖构造独立 mutation service 与组合子类；
`ProductionComponents.admin_registry_service` 仍以 `AdminRegistryService` 基类暴露，因此
无需修改 `app/main.py`。router 继续只使用 `Depends(require_principal)` 产生
`AdminRequestContext`，并只调用公开 Admin service 方法；不得直接 import Identity Port、
infra、私取属性或复用 `bindings_list` 写权限。

由于 `make_router()` 仍接收基类，API 新增独立
`_configured_binding_mutations()`：它只接受现有 service 引用，以
`isinstance(AdminRegistryServiceWithBindingMutations)` 收窄并返回公开组合子类；缺失或类型
不匹配固定返回 503 `admin_binding_mutation_unavailable`。两个 mutation 路由只调用该公开
子类接口，不改变既有 `_configured()` 或任何既有 route。

授权必须先于任何 binding 查询或写入。现有 `admin` 角色拥有跨用户管理权限；普通已认证
用户或其他角色撤销/重置自己的或他人的 binding 均返回既有 403
`role_not_allowed`，且 Port 零调用。

成功响应固定为：

```json
{
  "action": "revoke | reset",
  "binding": {
    "binding_id": "oa-session-v1:<safe-reference>",
    "target_system": "oa",
    "execution_identity": "user_delegated",
    "bind_status": "revoked",
    "binding_scope": null,
    "account_set_id": null,
    "device_domain_id": null,
    "reason_code": "identity_revoked"
  },
  "changed": true,
  "next_action": "none | reauthenticate"
}
```

重复操作仍返回 200，但 `changed=false`。非法或不存在的 binding 返回 404
`binding_not_found`；安全存储失败返回 503 `binding_mutation_unavailable`。所有错误继续使用
现有 Admin `HTTPException(detail={code, message})` 信封，不返回 500、成功空结果或底层异常。
独立 mutation service 必须捕获 `IdentityMappingMutationError`，先写失败审计，再在离开
`except` 后抛出无异常链的 `AdminBindingMutationUnavailableError`；API 只把该异常映射为
上述 503。`None` 走独立的 `AdminBindingNotFoundError` → 404 链，不得与存储失败混淆。

### 3.5 Audit record

每次允许、拒绝、not-found、存储失败与幂等成功都写既有 `admin_action` Trace。成功记录只含：

- `action` 与对应 `policy_capability_id`；
- `authorization_decision` 与既有 authenticated-principal provenance；
- 安全 `binding_id` 引用；
- `previous_bind_status`、`after_bind_status="revoked"`、`changed`；
- `next_action`。

失败只再增加安全 `reason_code`。actor 继续由已认证的 Admin request session/Trace 关联，
不信任客户端自报身份。Trace、响应、日志、异常、`repr`、fixture 和报告均不得出现明文
或密文 Cookie、OA userid、密码、token、nonce 或 encrypted payload。

### 3.6 Revocation guard, residual window and OA boundary

每个新 OA capability request 仍先经 Gateway 的 IdentityMapping。正常撤销后调用必须在
Gateway 得到 `identity_revoked`，返回明确的 `binding_required` / `identity_revoked`
失败，且 SecretProvider 与 Live HTTP 均零调用。为封住 Gateway 检查与晚解密之间的
竞态，`PostgreSQLCredentialStore.load()` 还必须读取 `revoked_at`，并在任何解密之前
fail-closed；该竞态固定返回既有 `adapter_error`，不得返回 500 或成功空结果。不得从缓存
复用先前凭证。取密仍保持在 adapter 调用 provider 之前的最后必要位置。

撤销提交后，已经完成取密并已经发出的那一次 OA 调用仍可能返回结果；残留窗口上界 =
OA HTTP 调用超时时间。本任务明确不声称完全阻断或召回该次在途调用，也不为此引入
advisory lock、DB lease 或请求注册表。守卫测试只锁定撤销提交后新发起的调用和再次取密
必然失败，不编写“杀掉在途请求”的测试。

撤销绑定不等于让用户从 OA 登出；OA 侧断会话属于 OA 管理员动作，不在本系统范围。
用户在 OA 浏览器中的 Session 是否仍可用，不属于 EternalAI binding 撤销的保证。

### 3.7 Migration and downgrade

迁移 upgrade 只执行：

```sql
ALTER TABLE oa_session_credentials
ADD COLUMN revoked_at TIMESTAMPTZ NULL;
```

不得增加 default、索引、约束、第二列或数据回填，不得 ALTER 既有列、新建表或删除 row。
downgrade 只允许删除本迁移自己新增的 `revoked_at`，不得删除任何既有列、表或数据。
迁移往返命令只可对 `127.0.0.1:15432` 固定测试库执行。

生产环境执行 downgrade 会永久丢失撤销审计记录。

本任务不得在开发库、生产库或任何非上述固定测试库执行 upgrade、downgrade 或数据写入。

## 4. Required verification

实现与测试必须证明：

1. active、`expires_at == now()`、自然过期、主动撤销及“已过期后再撤销”按既定优先级
   区分，且撤销不改 `expires_at`。
2. revoke/reset 首次写入与重复调用均符合结果契约；不存在 binding 明确 404。
3. 非 admin 跨用户操作 403 且 Port 零调用；admin 跨用户操作只因既有角色权限而允许。
4. 撤销提交后，以同一 Gateway、adapter、SecretProvider 和连接池实例再次执行
   `oa.list_pending_workflows`，得到明确 `identity_revoked`，取密与 HTTP 均零调用；无
   service-account、管理员、匿名、默认身份或 Replay 回落。
5. Gateway 检查后、取密前发生撤销时，取密层在解密前以 `adapter_error` 安全失败，不
   返回 500 或空成功。
6. 明文 credential 在响应、日志、Trace、异常链与 `repr` 四条路径均无泄漏；审计仅含
   binding 引用。
7. 成功重新认证清除 `revoked_at`，失败认证不清除；不保存密码或静默重登。
8. migration 在固定测试库完成 `upgrade → downgrade → upgrade`，且只增/删本列，不损坏
   既有表、列或 synthetic sentinel row。
9. 全量 backend ≥1344、0 skipped、0 failed；Ruff、mypy、dependency、architecture、
   Golden 25/25 及前端 OpenAPI 无漂移测试全绿。

## 5. Out of scope

- Excel/HR 导入、待引导名单、多绑定与独立 binding 实体；
- Vault、KMS、密钥轮换、批量失效与正式 Secret 治理；
- advisory lock、DB lease、请求注册表或在途请求取消；
- OA 侧 Session 注销、OA 写操作、密码保存、静默重登；
- 前端、Orval/OpenAPI 生成、治理文档；
- `MockOAAdapter`、Frozen Golden、`FROZEN_GT_IDS` 与 golden fixtures；
- DB Gateway 或其他 target system 的写能力。

## 6. Rejected alternatives

- 删除 credential row：丢失主动撤销审计，并超出授权。
- 把 `expires_at` 改到过去：混淆 revoked 与 expired。
- API 直连 PostgreSQL、私取 `AdminRegistryService` 属性或仅扩具体实现：绕过 Port、Policy
  与 Trace。
- 复用 `admin_bindings_list` 写权限：把读授权错误升级为写授权。
- 共享账号、管理员、匿名、默认身份或 Replay fallback：把撤销伪装成成功。
- advisory lock、DB lease、请求注册表：超出本次裁决，并为不属于本系统的 OA 登出语义
  增加热路径依赖。
- 新 mapping 表或第二个 schema 字段：超出单列授权。
