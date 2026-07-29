# ADR-P2-OA-READ-001 — OA 待办读取的 Replay / Live 接缝与 Contract Pack

- status: accepted
- date: 2026-07-29
- task_id: P2-OA-READ-CONTRACT-001
- decision_makers: [雨爷, Codex]
- related_capability: `oa.list_pending_workflows`
- related_plan: `docs/phase2/PHASE2_PLAN.md`

## 1. Context

`oa.list_pending_workflows` 的冻结 `input_schema` 是零参数对象。当前
`AdapterPort.execute()` 接受任意字典，Gateway 也没有执行期 schema 校验，因此真实 OA
adapter 必须自己阻止未知能力和参数透传。同时，真实 OA 响应只能在内网采集，Cookie、
token、用户姓名、工号等不得进入仓库。

本 ADR 只锁定 Replay / Contract 层。Live HTTP、凭证读取、IdentityMapping 注入、内网
smoke 和 Live 指纹漂移比较留给 `P2-READ-ADAPTER-001`。

## 2. Decision

### 2.1 Adapter 与 Provider 接缝

- 新建真实 `OAReadAdapter`，与 `MockOAAdapter` 并存；不得修改或替换后者。
- adapter 内部只允许静态 `capability_id -> handler` 映射。禁止 `getattr`、字符串拼接
  dispatch 和把 `arguments` 原样传给 provider。
- `oa.list_pending_workflows` 绑定具名的零字段 pydantic 参数模型，模型配置
  `extra="forbid"`。任何额外参数均 fail-closed 为
  `AdapterResult(status="error", error_code="adapter_error")`。
- 未知 `capability_id` 返回同一个 `adapter_error`；`capability_not_found` 仍只表示
  Registry 无能力或 adapter 未注册。
- 内部 Provider Protocol 的唯一方法形状为
  `async list_pending_workflows() -> OAPendingWorkflowCollection`。它不接收 capability
  参数、`execution_context`、凭证或任意字典。
- `ReplayOAReadProvider` 只从一个显式选择的、版本化 Contract Pack 读取。
  `LiveOAReadProvider` 在本棒只保留显式未实现出口；被调用时必须报错，且不得回落
  Replay。生产 composition 本棒不得注册 Replay 为 Live 替身。
- 冻结 capability 仍是零参数。Live provider 将在内部完成全部分页，分页游标和页大小
  不上浮为 capability 参数。

### 2.2 归一化领域模型与错误语义

`OAPendingWorkflowCollection` 只含 `workflows`。每项
`OAPendingWorkflow` 固定为：

| 字段 | 类型 | 可空 | 说明 |
|---|---|---:|---|
| `workflow_id` | string | 否 | 脱敏后的流程标识 |
| `title` | string | 否 | 脱敏后的标题 |
| `status` | string | 否 | 上游状态的归一化值 |
| `applicant` | string | 否 | 脱敏后的申请人标签 |
| `current_step` | string | 否 | 当前节点的归一化标签 |
| `approver` | string | 是 | 脱敏后的审批人标签 |
| `created_at` | string | 是 | ISO 8601 时间；未知时为 null |
| `expired` | boolean | 否 | 是否逾期 |

所有模型均 `extra="forbid"`。Replay 文件缺失、JSON 非法或违反领域模型时分别映射为
既有 `adapter_error` 或 `adapter_payload_invalid`，不得返回成功的空数据；合法的空待办
列表仍是 `success`。Live 未实现出口映射为 `adapter_error`。

### 2.3 Contract Pack

目录固定为：

```text
tests/contract_packs/oa/<profile_version>/
  profile.json
  sample.json
  fingerprint.json
```

- `<profile_version>` 只使用小写 ASCII、数字、点、下划线、连字符；已提交的版本目录不可
  原位改写。结构变化或新的现场版本必须新增目录。
- `profile.json` 只记录 profile 版本、capability、来源类别（本棒为
  `synthetic`）、sanitizer 版本及三个文件的关联，不记录 URL、用户名、Cookie 或原始
  HAR 路径。
- `sample.json` 只保存上述归一化模型的脱敏值。Replay 逐字段读取并验证，不接受未知键。
- `fingerprint.json` 使用 `eternalai-structural-v1`。算法深度遍历 `sample.json`，
  将每个节点归一化为按路径排序的
  `{path, json_type, nullable, array_shape}`；数组元素路径统一写成 `[]`，
  `array_shape` 只描述元素类型/对象/嵌套数组结构，不记录长度或值。最后只对这组结构
  节点的 canonical JSON（UTF-8、键排序、紧凑分隔符）计算 SHA-256。
- 指纹不得直接或间接 hash 原始响应、HAR、业务值或样本长度。

### 2.4 离线脱敏

仓库内脚本是纯离线转换器：只接受本地 HAR 文件和一个尚不存在的输出目录，不包含网络
客户端、默认内网地址或 `--force` / 跳过检查开关。原始 HAR 永远留在仓库外。

转换按以下三层 fail-closed：

1. **正向白名单提取**：只从已选响应体读取领域模型所需字段；标识、标题、姓名/工号类
   值替换为确定性的合成标签，未知字段不复制。
2. **原始敏感值不出现断言**：从 HAR headers/cookies 及敏感字段收集非空原值，逐个
   断言其字符串不出现在全部候选输出中。敏感原值若也出现在一个看似允许的字段中，仍
   必须失败。
3. **模式扫描兜底**：候选输出的键和值均扫描禁止键和 token/Cookie 模式。

禁止键按大小写、连字符和下划线归一化后至少包括：
`authorization`、`cookie`、`setcookie`、`token`、`accesstoken`、
`refreshtoken`、`session`、`sessionid`、`jsessionid`、
`ecologyjsessionid`、`loginidweaver`、`loginuuids`、`password`、
`passphrase`、`secret`、`apikey`、`loginid`、`userid`、`workcode`、
`employeeno`。

脚本先在输出目录的同级临时目录生成三个候选文件；三层检查和重新解析验证全部通过后才
原子发布。任一步失败时清理临时目录、返回非零且目标输出目录不存在。已有目标目录直接
拒绝，不能覆盖或删改。

## 3. Credential and Identity Boundary

Live 采用每用户复用自身 OA Session，保持 `execution_identity="user_delegated"` 与
`binding_required=true`。不使用共享服务账号，不自动切管理员，不保存用户密码，不静默
重登；Session 失效必须明确要求用户重新认证。凭证不进入 capability 参数、fixture、
Contract Pack、Trace、日志或报告。本 ADR 只记录决定，不实现读取链。

## 4. Consequences

- Replay 可以在气隙环境验证真实 adapter 的 dispatch、参数、归一化和错误语义。
- 白名单位于 adapter 内部，不借机扩成 Gateway 通用 schema 校验或通用 Connector。
- Contract Pack 能验证结构漂移而不被业务值或列表长度变化干扰。
- 下一棒必须显式实现 Live、凭证读取与身份映射；Replay 的存在不能解除这些依赖。

## 5. Rejected Alternatives

- 动态能力代理或反射 dispatch：无法形成固定能力边界。
- 将分页加入 capability 参数：违反 GT-001 冻结零参数 schema。
- hash 原始响应：业务数据变化会产生假漂移，且可能形成敏感数据指纹。
- Replay 静默充当 Live fallback：会把生产失败伪装成成功。
- 通用 Connector 服务、通用 Connector 规范或 MCP：超出本任务范围。
