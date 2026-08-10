# Capability Registry 部署 Bootstrap

Capability Registry 的 canonical OA 能力通过显式部署命令入库。应用进程启动时不会自动灌库，
也不会强制执行 Registry verify；这是刻意保留的部署边界，而不是遗漏。`python -m app.server`
及 FastAPI startup/lifespan 均不承担 Registry 写入或部署校验。

## 执行前核对

1. 在当前进程的部署配置中逐项核对 `DATABASE_URL` 的主机、端口和数据库名，确认它指向本次
   获准操作的非生产目标。不要把完整连接串或凭证打印到终端、日志或审计记录。
2. **不得**对生产库或 `eternalai_pilot` 执行 `--apply`。目标不明确时停止，不运行写命令。
3. 确认迁移与管理命令使用同一个已核对的 `DATABASE_URL`，且当前用户可写审计目录。

## 固定部署顺序

以下四步必须按顺序执行，不得省略只读干跑或把 verify 当作自动修复：

```powershell
uv run alembic upgrade head
uv run python -m scripts.manage_oa_capabilities
uv run python -m scripts.manage_oa_capabilities --apply
uv run python -m scripts.manage_oa_capabilities --verify
```

各步骤含义：

1. `alembic upgrade head`：先把已核对的目标库迁移到当前 schema head。
2. 无参数管理命令：只读生成计划，不写 Registry；它回答“将执行什么”。
3. `--apply`：在单个事务内显式应用计划。已是 canonical 状态时返回
   `registry_management=already_applied`，不重复写入。
4. `--verify`：独立、只读地核对必备 canonical 能力；它回答“部署契约是否已经满足”。缺失、
   非 active 或与 canonical 定义不一致时会列出不满足项并以退出码 `3` 阻断后续部署。

`--verify` 与 smoke preflight 使用同一份 `expected_oa_capabilities()` canonical 定义和
`REQUIRED_ACTIVE_OA_CAPABILITY_IDS` 必备能力集合；verify 不调用 apply，也不会补建缺失能力。

## 退出码

既有 `0` / `1` / `2` 语义保持不变；`3` 仅用于 verify 契约未满足：

| 退出码 | 含义 |
|---|---|
| `0` | 只读计划成功；或 apply 成功/幂等 no-op；或 verify 契约满足。 |
| `1` | 既有的不可安全执行状态，或配置、连接、载荷、数据库操作等失败。 |
| `2` | 既有的参数错误、管理不变量或事务后置条件失败。 |
| `3` | `--verify` 已完成只读核对，但必备 canonical 能力契约未满足。 |

任何非零退出码都必须视为部署失败并停止后续步骤。退出码 `3` 不能通过改跑 `--apply` 之外的
隐式修复绕过；先确认干跑计划与目标库，再按固定顺序处理。

## apply 审计记录

每次 `--apply` 尝试，无论成功还是失败，只保留一个独立 JSON 审计文件：

1. DB 访问前先原子持久化并 `fsync` 一条保守的 `apply_incomplete` 失败记录；若这一步失败，
   命令在任何 DB 访问前退出。
2. plan 生成后、任何 DML 前，用带 plan state、deployment path 和各项计数的失败记录原子替换
   同一个文件。
3. 执行结束后，再原子替换为实际最终结果和退出码。若命令异常中断，文件仍保留失败状态，
   不会留下“已写库但完全无审计”的空窗。

默认目录为：

```text
~/.eternalai/audit/capability-registry-bootstrap/
```

每次调用使用唯一的 `<UTC 时间>_<attempt_id>.json` 文件。部署者可显式覆盖目录：

```powershell
uv run python -m scripts.manage_oa_capabilities --apply --audit-dir <directory>
```

命令会打印 `registry_audit_path=<实际文件>`；用该输出值读取：

```powershell
Get-Content -LiteralPath '<registry_audit_path 输出值>' | ConvertFrom-Json
```

审计记录不包含 `DATABASE_URL`、连接串或凭证，也不写入 Git。保留文件作为部署证据，并按部署
环境的文件权限和留存策略管理。
