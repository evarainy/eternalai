# ADR-P2-ADMIN-CSRF-001 — Cookie 写接口的 Origin 与自定义头双层 CSRF 防护

- status: accepted
- date: 2026-08-01
- task_id: P2-ADMIN-CSRF-001
- decision_makers: [雨爷, Codex]
- related_incident: P2-PILOT-ENTRY-FE-001 security review

## 1. Context

EternalAI 使用 `eternalai_session` HttpOnly Cookie 认证 Runtime 与 Admin API。Cookie
当前为 `Secure; HttpOnly; SameSite=Lax; Path=/api/v1` 且未设置 `Domain`。`Lax` 只区分
site，不区分 origin；同一注册域下不受信任的另一个子域仍可能自动携带该 Cookie 发起
POST。Registry 的 enable/disable 还是无请求体 POST，普通 HTML form 即可触发。

当前 Cookie 认证且改变业务状态的接口固定为：

1. `POST /api/v1/admin/registry`
2. `POST /api/v1/admin/registry/{capability_id}/enable`
3. `POST /api/v1/admin/registry/{capability_id}/disable`
4. `POST /api/v1/admin/bindings/{binding_id}/revoke`
5. `POST /api/v1/admin/bindings/{binding_id}/reset`
6. `POST /api/v1/runtime/handle`

`POST /api/v1/auth/login` 在 Cookie 建立前执行，不属于 Cookie 已认证集合，但成功时会建立
或覆盖认证 Cookie。其请求解析器不以 Content-Type 作为安全边界，因此登录同样需要防止
跨源会话置换。

Admin 的六个 GET 是业务读接口，但会持久化审计 Trace。该审计副作用另记债务；本任务
不改变 GET 语义，也不把安全方法纳入 CSRF 拦截。

## 2. Decision

### 2.1 双层、无状态校验

对上述六个 Cookie 认证写接口以及登录接口同时要求：

- 请求必须且只能携带一个 `Origin`，其值逐字匹配进程配置的允许 origin 集合；
- 请求必须且只能携带一个 `X-EternalAI-CSRF`，其固定公开值为 `1`；
- 缺失、重复、来源不在允许集合或固定值不匹配时统一 fail-closed；
- 拒绝返回 HTTP 403，detail code 固定为 `csrf_validation_failed`，消息不回显 Origin、
  Cookie 或请求头内容；
- `GET`、`HEAD`、`OPTIONS` 不执行 CSRF 校验。

固定头不是秘密或持有者 token。它的作用是让跨源 JavaScript 必须经过预检，同时让 HTML
form 无法构造目标请求；严格 Origin 校验独立阻止同站不同源和跨站来源。两层必须同时
通过，CORS、SameSite 和 Cookie host-only 属性都只作为纵深，不计入修复证明。

### 2.2 认证顺序与路由范围

Runtime 与 Admin 使用组合 principal dependency：先执行既有 Session Cookie 验签，成功
后才校验 CSRF。这样缺失或无效 Cookie 继续返回原有 401，不会被 403 掩盖；既有角色、
租户、用户和对象授权检查不变。

登录没有前置 principal，直接执行同一 CSRF request dependency，再进入既有凭据解析、OA
认证和 Cookie 签发。不得以“请求前还没有 Cookie”为理由豁免登录。

依赖只读取 `Request`，不声明 FastAPI `Header` 参数，因此不会进入 OpenAPI。前端只在
现有共享 `fetch` mutator 中为非安全方法设置固定头；调用者提供的同名任意大小写头先删除
再由 mutator 重建。禁止修改 `web/openapi/*.json`、`web/src/generated/**` 或 Orval 配置。

### 2.3 Origin 配置

新增必填进程配置 `CSRF_ALLOWED_ORIGINS`，使用逗号分隔的规范 origin：

```text
https://app.example.gov.cn,https://admin.example.gov.cn:8443
```

每项必须是 ASCII、规范化的 `http://` 或 `https://` origin，只含 scheme、host 和可选
非默认 port；禁止凭据、路径、query、fragment、通配符、空项和重复项。缺失、空值或非法
配置在生产装配前 fail-fast。代码不硬编码部署域名，也不读取或修改 `.env`。

非浏览器客户端若调用登录、Runtime 或 Admin 写接口，也必须显式发送一个被允许的 Origin
和固定自定义头；不提供“无 Origin 即同源”的兼容回落。

## 3. Consequences

- 同站不同源 form POST、跨站 POST、缺 Origin 请求和缺固定头请求均在业务服务调用前被
  拒绝。
- Runtime、revoke 与 reset 和 Registry 写接口共享同一认证后防护，不会因前端当前未调用
  某接口而漏保。
- 登录 CSRF 被同一边界拒绝，但登录凭据解析与 OA 认证语义不变。
- GET/HEAD/OPTIONS、现有认证失败顺序、Admin 授权、撤销/重置语义以及冻结 OpenAPI/生成
  客户端保持不变。
- 本任务不新增 CSRF token 存储、CORS 中间件、第三方依赖、DB schema 或数据迁移。
- Admin GET 的审计 Trace 写入不在本任务修复；后续若要消除或隔离该副作用，必须另开范围。

## 4. Rejected Alternatives

- `SameSite=Strict`：仍按 site 判断，不能阻止同站不同源子域。
- 仅依赖 CORS：CORS 不是认证或 CSRF 边界，HTML form 也不依赖读取响应。
- Origin 缺失时放行或回落 Referer：形成经典 fail-open，且不满足本任务硬规则。
- 只要求自定义头：未来 CORS 配置漂移可能允许不受信任来源设置该头。
- 同步 token / double-submit token：可以成立，但本范围没有必要引入 token 生命周期或
  Session 侧存储；严格 Origin 与不可由 form 构造的固定头已经覆盖已确认路径。
- 把固定头声明进 OpenAPI：它是传输层防护，会无必要触发冻结产物与生成客户端漂移。
- 只保护 Admin：会遗漏同样通过 Cookie 认证并创建 session/task/trace 的 Runtime POST。
- 豁免登录：当前登录可以改变认证 Cookie，仍存在会话置换风险。

## 5. Verification

- 合法同源 Origin + 固定头可以进入原业务路径；
- 同站不同源且无固定头、跨站 Origin、缺 Origin、缺固定头分别返回明确 403；
- 核心无请求体 disable 在拒绝后业务 spy 调用数保持 0；
- 六个 Cookie 认证写接口、登录、revoke 和 reset 都有拒绝证明；
- Admin GET 无 CSRF 头仍保持原业务读行为；
- 前端 mutator 对非安全方法强制固定头，对 GET/HEAD/OPTIONS 不发送；
- OpenAPI 与生成客户端字节漂移测试通过，Git diff 证明冻结路径零改动。
