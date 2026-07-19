# P1-INTENT-CAP-001 — B2: Intent → Capability 选择闭环

status: ready
batch: B2
spec_anchor: PHASE1_SPEC.md S-B2.1 / S-B2.2 / S-B2.3 / S-B2.4 / S-B2.5

## 执行方式授权（过渡期偏差记录）

本任务经雨爷批准，以 **Codex App Goal 模式**临时执行，不走 codex-claude v4 workflow skill（v4→v5 过渡期最小配置）。保留四项硬件：本 descriptor、`phase1/<task_id>` 分支、独立评审（Codex `/review` + Claude Opus 只读评审，均留档）、Task Record。其余 v4 仪式（contract 逐字段流转、多级 Gate 文书）对本任务不适用。本条即偏差的书面依据。

## 目标（done_when，全部为可观察结果）

对应 `docs/phase1/PHASE1_SPEC.md` §B2。完成判定以下列结果 + 验证命令全绿为准：

1. **入口归一**：Web/CLI 请求经现有 `RuntimePort` 进入后形成可校验的 intent 结果；解析走既有 `LLMProviderPort` + `StructuredOutputPort` 边界（raw OpenAI SDK + `response_format={"type":"json_object"}` + Pydantic v2 `model_validate`/`Literal` 基线，不得替换）。
2. **候选过滤与唯一选择**：只从 `CapabilityRegistryPort` 中状态可用且符合请求约束的既有 Capability 产生选择；选择可重复、可追溯。disabled Capability 不得被选中；多候选无法按规则唯一收敛时**不得调用 Adapter**。
3. **`no_capability_found` 路径保持**：无可用 Capability 时 Task 进入 `no_capability_found` 终态，返回 `capability_not_found` 语义的标准 ResponseEnvelope；Trace 至少含 `task_created`、`intent_parsed`、`no_capability_found`、`response_envelope_created`；不得进入 Identity、Policy、Gateway 或 Adapter。GT-008 短路必须继续成立。
4. **选择段 Trace**：intent、选择、无能力终态形成可审计 Trace 事件，与 `TaskStorePort` 终态一致。

## 硬约束（违反即停手上报，不得变通）

- `app/ports/` 13 个 Protocol **冻结，零修改**；`ResponseEnvelope` 冻结，不计为第 14 个 Port。发现冻结契约不足以支撑实现 → 停手上报，不得自行扩展。
- **不改 golden fixtures / `FROZEN_GT_IDS`**：S-B2.4 的 golden 增量属后续专门授权任务，本任务只需 GT-008 等既有 golden 回归通过。
- 不引入新依赖、不做 embedding/向量方案依赖决策、不实现 Controlled Exploration、不实现 Dynamic Tool Composition、不接真实业务系统写操作（S-B2.5 裁剪决策照单执行）。禁止 instructor / PydanticAI。
- 六边形边界：应用层只依赖 frozen Port，Port 不反向依赖 `app/infra/`；`tests/architecture` 必须全绿。
- 测试覆盖（S-B2.3 pytest 面）：Web/CLI 输入归一、structured-output 校验、候选过滤、唯一选择、未注册/disabled 边界、标准 ResponseEnvelope、失败不越过选择段。测试须过 weak-test checker。

## 验证命令（全绿才算完成）

```powershell
uv run pytest
uv run pytest tests/architecture/
uv run ruff check app/ tests/
uv run mypy app/
uv run python scripts/check_dependencies.py
uv run python scripts/run_golden_tasks.py --gate
git diff --cached --name-only; git diff --cached --stat; git diff --cached --check
git ls-files --others --exclude-standard
```

暂存前清理 `__pycache__/`、`*.pyc`、`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`；不扫 `.venv/`。

## 流程与停点

- 分支：`phase1/P1-INTENT-CAP-001`（从 `phase0/main` 拉出）；本 descriptor 随任务分支一并提交。
- Commit message：`phase1(P1-INTENT-CAP-001): <简要描述>`；merge message：`merge phase1(P1-INTENT-CAP-001): <简要描述>`。
- **merge 前置条件（机械判定，无等待型人工停点）**：① 验证命令全绿；② `/review` 完成并修完发现项，如实记录实际使用的 model 与 reasoning effort；③ Claude Opus 只读评审 PASS——由执行者自行调用 canonical bridge 脚本（`C:\Users\Administrator\.codex\skills\codex-claude\scripts\call_claude.py`，先读脚本用法再调用）对最终 staged diff 发起评审，meta（真实 model id、verdict）机械落盘并随证据归档。对话中「已完成 Opus 评审」的文字声明不构成证据；meta 缺失、model 不匹配或 verdict 非 PASS 一律不得 merge。bridge 跑不通时停下上报，不得跳过或以口头结论替代。
- **监理代裁（雨爷书面授权）**：普通 gate（实施计划确认、继续/重试/换策略、不破坏契约的方案取舍、空转叫停）由监理窗口代雨爷裁定，裁定须留痕。以下事项监理不得代裁、必须报雨爷本人：上节全部红线、`app/ports/` 冻结契约、golden fixtures / `FROZEN_GT_IDS`、新增依赖、新 Port 或架构级变更、批量删除、放宽本 descriptor 任何硬约束。监理只能把任务收窄回 done_when，不得扩需求。
- 完成后输出 ≤20 行中文结果摘要（含 /review 记录与 Opus meta 引用），供雨爷异步验收。
- Merge 后检查 GitHub Actions CI；随后补一条 commit 落盘 Task Record：`docs/phase1/task_logs/P1-INTENT-CAP-001_<YYYYMMDD_HHMMSS>_<passed|failed|blocked>.yaml`（schema v1.2.0，未触发的检查不得写成 passed）。
- 红线照旧：删除文件/目录/git 历史、改 `.env`/密钥/token、DB schema 变更、rebase / reset --hard / 强制推送、装全局依赖、公开发布 → 一律停下问雨爷。
