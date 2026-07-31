# ADR-P2-OA-SANITIZE-003 — Capture 元数据与错误只报位置、不报内容

- status: accepted
- date: 2026-07-31
- task_id: P2-SANITIZE-LEAK-FIX-001
- decision_makers: [雨爷, Codex]
- related_capability: `oa.list_pending_workflows`
- supersedes: 仅收窄 `ADR-P2-OA-READ-001` 的 capture writer 命名输入；Replay reader 契约不变

## 1. Context

OA HAR 脱敏器是原始采集与可提交 Contract Pack 之间的唯一防泄漏屏障。原实现允许
调用者提供任意路径安全的 `profile_version` 并把它原样写入 `profile.json`；默认
`argparse` 也会在参数错误时把原始参数值写入 stderr。这两条路径都绕开了从 HAR
收集敏感值后再做缺席断言的防线。

CLI 参数和 capture 元数据同样是不可信输入。黑名单只能识别已知形态，不能证明任意
自由 metadata 不携带工号、身份标识或凭证，因此不能作为 profile 泄漏的主防线。

## 2. Decision

### 2.1 Capture profile 使用固定结构

本脚本使用完整 profile 显式 allowlist；当前只接受：

```text
ecology9-pending-workflows-v1
```

完整值不从 CLI 或 HAR 派生。未来若增加其他 capture profile，必须在代码 allowlist、
测试和 ADR 中显式增加完整值，不得开放自由 stem、revision 或其他可控片段。通用
Replay reader 仍按既有安全路径字符契约读取历史 Pack。

profile 校验必须先于 HAR 读取、候选构造和临时目录创建。失败只返回固定规则码，不得
创建目标目录或临时目录。

### 2.2 错误只报位置或规则码

允许进入错误信息的内容限于参数名、entry 索引、JSON path、字段/header 名、规则名、
值长度或不可逆哈希前缀。禁止进入错误信息、异常链和日志的内容包括：

- 原始值及任何明文片段；
- base64、URL encoding 或多层 JSON 等可逆编码；
- 整个输入对象的 `repr()` / `str()`；
- 携带原值的底层异常。

CLI 解析器不得调用默认的回显错误路径。参数类型转换先在内部完成；捕获块只记录失败
状态，离开捕获块后再抛固定 `SanitizationError`，确保公开异常没有携带原值的
`__context__` 或 `__cause__`。

### 2.3 原三层检测只增不减

以下检测继续逐项保留：

1. 正向白名单归一化；
2. 全 HAR 敏感值收集与候选/落盘重读后的原值缺席断言；
3. 禁止键和禁止值形态扫描。

结构化 profile 校验和无回显 CLI 边界是新增防线，不替代或缩窄上述任何规则。所有既有
深层负向测试必须使用合法 profile，确保仍到达原检测分支，而不是在 profile 校验处
提前失败。

## 3. Consequences

- 既有 `ecology9-pending-workflows-v1` Contract Pack 无需修改。
- `app/`、Golden fixtures、`FROZEN_GT_IDS` 和冻结产物不变。
- 非规范 capture profile 会 fail closed，退出非零且零目标/临时产出。
- CLI 错误的可操作信息来自固定位置和规则码，不再包含原始参数值。

## 4. Verification

- 用合成 profile 标识证明修复前会进入 `profile.json`，修复后被拒绝且零产出。
- 用合成非法 CLI 值证明修复前会进入 stderr，修复后 stdout/stderr、异常链和产物均
  不含该值。
- 对既有敏感规则集合做前后机械对比，并运行全部 sanitizer、weak-test、architecture、
  Golden 和全量 pytest 验证。
