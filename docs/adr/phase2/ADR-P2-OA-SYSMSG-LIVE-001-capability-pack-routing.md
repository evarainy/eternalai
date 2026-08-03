# ADR-P2-OA-SYSMSG-LIVE-001 — OA Live 能力级路径与 Contract Pack 路由

- status: accepted
- date: 2026-08-03
- task_id: P2-OA-SYSMSG-LIVE-001
- decision_makers: [雨爷, Codex]
- related_capabilities: [`oa.list_pending_workflows`, `oa.list_system_messages`]
- supersedes: 仅补充 `ADR-P2-OA-READ-002` 的单 Live endpoint / 单 Contract Pack 配置

## 1. Context

`oa.list_system_messages` 已有零参数 capability、归一化模型和独立的
`ecology9-system-messages-v1` Contract Pack，但当前 `LiveOAReadProvider` 只装载 pending
pack，并在 system-message 调用时直接 fail-closed。现有 `OA_READ_CONTRACT_PACK_DIR` 只能
指向一个叶子 pack；若 Live 改指 system-message pack，pending 类型守卫会在启动时拒绝，
无法对两个能力分别做 Live 指纹漂移比对。

真实 system-message 请求形状只能来自未脱敏 HAR。实现只采用其 HTTP method、参数名、
分页参数名和 header 名；真实主机、API path、Cookie、Session、用户数据和参数业务值均
不得进入仓库、测试、日志、Trace、报告或 commit。

## 2. Decision

### 2.1 按 capability 显式配置 Live pack

Live 增加两个对称配置：

```text
OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR
OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR
```

`LiveOAReadProvider` 通过两个具名 constructor 参数分别装载并校验 pack：pending 参数必须
是 `oa.list_pending_workflows` pack，system-message 参数必须是
`oa.list_system_messages` pack。运行时按被调用的 capability 选择已经验证过的 expected
fingerprint，不扫描父目录、不按目录名猜类型，也不允许一个 pack 替代另一个。

Replay 语义不变：`OA_READ_CONTRACT_PACK_DIR` 仍选择一个叶子 pack，调用不匹配的
capability 仍按既有方式失败。为迁移既有 Live 部署，若未设置新的 pending pack 变量，
Live 可把既有 `OA_READ_CONTRACT_PACK_DIR` 明确作为 pending pack 回退；显式的新变量优先。
system-message 不设回退，缺失时必须准确报告
`OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR is required for live mode`。新部署应设置两个对称
变量；旧变量长期只保留 Replay 语义和上述 Live pending 兼容入口。

### 2.2 对称的 Live API path

新增：

```text
OA_SYSTEM_MESSAGES_PATH
```

它与 `OA_PENDING_WORKFLOWS_PATH` 使用同一个 path 校验器：必须是 OA host 上以单斜杠
开头、不含 scheme、host、query、fragment、反斜杠或 `..` 段的相对绝对路径。两个 path
在 Live 模式均为必填；缺哪一个就准确指出哪一个。代码和 `.env.example` 不提供真实
默认值，缺配置不得回落 Replay 或 Mock。

### 2.3 System-message 有界读取

system-message 复用既有 Live provider 的以下边界：

- 每次调用重新解析 user-delegated OA Session，检查本地 TTL，构造隔离且禁用环境代理的
  opener，并只向已验证的同源 OA endpoint 发送 Cookie；
- 沿用 timeout、单响应大小、同源 redirect、HTTP/业务错误分类、异常局部变量清理和安全
  日志规则；不自动登录、不 retry、不缓存 Cookie；
- 使用 HAR 证明的 POST form 形状以及六个具名表单参数：`pagesize`、`selectState`、
  `msgid`、`bizstate`、`mintime`、`id`。固定过滤值属于代码内非敏感协议常量，不复制 HAR
  中的真实业务值；真实 API path 只能由部署配置提供；
- 单次最多返回 20 条，与已落地 Contract Pack 的捕获页边界一致。记录数小于 20 时
  `is_complete=true`，恰为 20 时 `is_complete=false`；超过边界、顶层 `data` 非列表或
  记录不满足归一化模型均 fail-closed 为 `adapter_payload_invalid`，不得把部分结果冒充
  完整结果。

本棒不推断未被证据证明的跨页 cursor 协议。零参数 capability 仍返回最近一页的有界
集合和显式完整性，不把页号、游标或 filter 暴露给 Gateway。

### 2.4 Live 指纹与错误语义

system-message actual fingerprint 来自本次 Live 记录投影：八个已知 wire 字段映射为领域
字段名，三个已知但不进入领域模型的展示/跳转字段被显式忽略，其他新增字段只投影为
`unknown_field_NNN`。标量值先替换为同类型哨兵；fingerprint 和 drift report 不包含原始
字段名、业务值、Cookie、用户标识、响应体或数组长度。

实际记录仍必须独立通过严格归一化；exemplar 只用于保持 system-message pack 已冻结的
可空字段结构，不得把缺字段或非法值变成成功。比较器、value-free reporter 与
`matches` / added / removed / changed 语义沿用 pending。漂移只报告结构，不改变业务
成功或失败。

无绑定、凭证过期/撤销、HTTP 401/403/5xx、timeout、非法 JSON/字段继续映射到既有错误码；
system-message 不新增错误码，也不允许任何 Replay、Mock、匿名或 service-account fallback。

## 3. Consequences

- Live 启动需要两个 path 和两个 capability pack；既有只读 pending 部署至少要补充
  system-message path 与 pack，错误会逐项指出缺失变量。
- Replay 调用和单 pack 行为不变。
- `.env.example` 只新增注释占位符，不写真实路径。
- 不修改脱敏器、Golden fixtures、`FROZEN_GT_IDS`、公共 capability 参数或数据库 schema。
- 真实 HAR 只用于本棒的一次性 shape 取证和 staged/new-file 敏感值扫描，永不暂存。

## 4. Rejected Alternatives

1. **一个父目录自动发现 pack**：依赖目录命名和扫描顺序，缺配置无法准确指向具体
   capability，并引入隐式猜测。
2. **继续共用一个叶子目录**：两个 capability 必然有一个在 constructor 类型守卫处失败。
3. **复制一个独立 system-message HTTP 栈**：会分叉凭证、同源、代理、错误和清理规则，
   增加安全漂移面。
4. **猜测跨页游标**：HAR 与现有 pack 尚未证明稳定翻页协议；本棒保留显式不完整语义。

## 5. Verification

- 配置测试：新 path 与 pending 同构校验；Live 缺一项均准确 fail-closed；Replay 不回归。
- provider 测试：Live 成功、20 条/短页完整性、错误码、pack 类型守卫、漂移/不漂移。
- 结构守卫：新 path 无真实默认值，provider 不含 Cookie/Session 形态敏感字面量，缺配置
  不会回落 Replay。
- 运行仓库要求的全量 pytest、ruff、mypy、依赖、architecture 与 Golden gate。
- 从未脱敏 HAR 提取真实敏感值后，只对 staged diff 与全部新增文件做一次性扫描；只报告
  命中数。
