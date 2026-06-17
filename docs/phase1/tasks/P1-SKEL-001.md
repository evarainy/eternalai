# P1-SKEL-001 — Single-task Prompt

Use this instead of pasting the full Phase 1 Plan or any blueprint into the session. Everything this task needs is in this file or referenced by exact path on disk.

> 落盘后该文件归属 `docs/phase1/tasks/P1-SKEL-001.md`。当前为 `_scratch/` 草稿。

## Background / 任务由来

Phase 0 已全部冻结并验收通过。Phase 1 启动 Plan（修订版 v4）已经过人类批准，当前以草稿形式存在于 `_scratch/phase1_plan_draft_v4.md`。P1-SKEL-001 是 Phase 1 首批（B1）的"启动准备"任务之一：把 `docs/phase1/` 目录骨架落盘、把已批准的 Plan 存档、并建立 Phase 1 自有的派生任务模板与索引骨架，使后续 B1-B5 任务有可执行的脚手架。

**范围说明（给 reviewer：本次执行的范围扩展，非 scope creep）**：已批准 Plan 的 §C.3 SKEL 卡片原文只覆盖下方"任务范围"的第 1-5 项（目录树 / PHASE1_PLAN / 派生模板 / TASK_INDEX 骨架 / task_logs INDEX 表头）。第 6 项——在同一次执行里把其余 B1 per-task prompts（GATE/ERRATA/PARAM/SPEC，及可选 TOOLCALL）一并用新派生模板写出来——是人类对本次执行的显式扩展指令。此处明确登记，便于下游审查者知道这是有意为之、已获人类授权，而不是任务越界。

**人类批准的决策登记（§F 6 决策点）**：人类已就 Plan §F 列出的全部 6 个待拍板决策点逐点按其**推荐选项**批准，本 prompt 在此登记为锁定结果（Plan §F 正文本身仍是"带推荐的待拍板"，锁定来自本登记，不要把 §F 推荐误读为 §F 已锁）：
1. `docs/phase1/` 新建独立 INDEX 与 TASK_INDEX，不混 phase0 75 行历史。
2. Phase 1 spec 为 B2-B5 硬前置，单列任务 **P1-SPEC-001** 产出 `docs/phase1/PHASE1_SPEC.md`；未批准不得进 B2。
3. 切分策略 = 纵切。
4. 首批 = 4 准备型（GATE/SKEL/ERRATA/PARAM）+ 1 可选（TOOLCALL）；GATE 为前置、PARAM 等 infra。
5. **F.5 = BOUNDARY_CHECKLIST 跨阶段沿用 phase0**（连同 CONTEXT_LOADING_STRATEGY / CODING_STYLE_BASELINE / REPOSITORY_CONTEXT_MAP / ROLE_AND_METHOD_GUARDRAILS 一并沿用）。
6. P1-PARAM-001 的 infra 参数由 infra 提供，未给值前天然 blocked。

## Required context（按需加载，不要整份粘贴）

- **CLAUDE.md（repo root）— Phase 1 governance MUST-READ（所有执行者必读，不只是 Claude Code/MiMo）**：commit `phase1(...)` / merge `merge phase1(...)`、主干 `phase0/main`、`app/ports/` 冻结、Phase 1 rules。你的工具 boot file 仍按工具加载（Claude Code/MiMo 读 CLAUDE.md，Codex 读 AGENTS.md），但 Phase 1 治理以 CLAUDE.md 为准。
  - 注意：`AGENTS.md` 可能仍带 Phase 0 措辞；与 CLAUDE.md Phase 1 规则冲突时以 CLAUDE.md Phase 1 规则为准。
- `_scratch/phase1_plan_draft_v4.md` — 已批准的 Phase 1 Plan（本任务的内容源；§B 目录布局、§B.2 21 条替换清单、§C.2 批次 DAG、§C.3 B1 任务卡片、§F 锁定决策）
- `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` — phase0 模板（派生模板的基底，逐条按 §B.2 改写）
- `docs/phase0/task_logs/INDEX.md` — phase0 INDEX 表头格式参照
- `docs/dev/task_record_schema.yaml` — Task Record schema（沿用，不新建）
- `docs/phase0/CONTEXT_LOADING_STRATEGY.md` — 上下文加载策略（跨阶段沿用，派生模板继续指向）

## Global hard rules

- 只执行 P1-SKEL-001，不执行任何其他 task_id。
- 本任务只动 `docs/phase1/**`。不动 `app/`（含冻结的 `app/ports/` 13 文件）、不动 `docs/phase0/`、不动 `docs/blueprint/`（冻结蓝图/MVP spec）、不动 `.github/workflows/`、不动依赖锁文件、不动 hooks / orchestrator 脚本。
- 不引入 instructor / PydanticAI（ADR 否定，内网复测亦确认 raw JSON 最优；见 `docs/phase0/PHASE1_TECHNICAL_BASELINE.md` §3.1）。本任务是文档型，本不涉及依赖，但派生模板措辞也不得弱化此基线。
- 不复制 phase0 的 75 行历史到 Phase 1 的 INDEX。
- 临时文件只进 `_scratch/`；stage 前清 `__pycache__/`、`*.pyc`、各 cache。
- No commit, no push, no merge —— 直到 Task Record 输出且人类确认。仅 stage for review。不得 `--no-verify`。
- 先输出 Plan，等人类确认后再改文件（套用 `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` 的 "行动前必须先输出 Plan" 流程）。

## method_profile

```yaml
method_profile:
  execution_role: "documentation"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "not_applicable"
  reason_for_owner_choice: >
    纯文档/脚手架任务（建目录、存档已批准 Plan、按穷尽清单派生模板、写 B1 per-task
    prompt 骨架），无生产代码、无运行时逻辑、无测试断言，故 method=not_applicable；
    默认 Claude Code/MiMo 执行 + Codex 独立审查。method=not_applicable 必须写明
    reason/scope/evidence（见下方 Task YAML 的 not_applicable 说明）。
```

## Task YAML

```yaml
task_id: P1-SKEL-001
title: Create docs/phase1/ skeleton, land approved Plan, derive Phase 1 task template, author B1 per-task prompts
type: documentation
source_plan: _scratch/phase1_plan_draft_v4.md   # 已批准 Plan，本任务把其内容落为 docs/phase1/PHASE1_PLAN.md
template_basis: docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
task_record_schema: docs/dev/task_record_schema.yaml
objective: >
  落盘 Phase 1 文档脚手架并为 B1 批次铺好可执行的 per-task prompt：
  (1) 建 docs/phase1/ 目录树（含 tasks/ 与 task_logs/ 子目录），布局严格对齐 Plan §B；
  (2) 把已批准 Plan（_scratch/phase1_plan_draft_v4.md 全文）落为 docs/phase1/PHASE1_PLAN.md；
  (3) 建 docs/phase1/TASK_PROMPT_TEMPLATE.md —— 以 phase0 模板为基底，按 Plan §B.2 的 21 条
      字面量清单逐条处置（两类：必须替换为 phase1 锚点；显式保留跨阶段通用文档并加一行沿用说明）；
  (4) 建 docs/phase1/TASK_INDEX.md 骨架，载入 Plan §C.2 的 B1-B5 批次 DAG（B1 列实际 task_id +
      depends_on；B2-B5 列规划切片），结构沿用 phase0 TASK_INDEX；
  (5) 建 docs/phase1/task_logs/INDEX.md，表头与 phase0 INDEX 完全一致，无 phase0 历史行；
  (6) 用新派生模板把其余 B1 per-task prompts 写入 docs/phase1/tasks/：P1-GATE-001、P1-ERRATA-001、
      P1-PARAM-001、P1-SPEC-001，并可选写 P1-TOOLCALL-002（标 optional/不阻塞）；
  (7) 为 docs/phase1/PHASE1_SPEC.md 建一份明确标注的 PLACEHOLDER 占位文件（见下方决策），
      使派生模板的 source_spec 锚点不悬空——其真实内容由 P1-SPEC-001 产出。
  本任务不实现任何业务逻辑、不动冻结契约、不改 CI/依赖/blueprint。
constraints:
  - 只动 docs/phase1/**
  - 不修改 app/ 或 app/ports/（13 文件 FROZEN）
  - 不修改 docs/phase0/ 任何文件（含 CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md，只读它作基底）
  - 不修改 docs/blueprint/（冻结蓝图 + MVP spec）
  - 不修改 .github/workflows/、依赖/锁文件（pyproject.toml/uv.lock 等）、hooks、orchestrator 脚本
  - 不新建 Task Record schema（沿用 docs/dev/task_record_schema.yaml）
  - 不引入 instructor / PydanticAI；派生模板措辞不得弱化 raw JSON 基线
  - 不复制 phase0 INDEX 的历史行
  - No commit / push / merge 直到 Task Record + 人类确认
acceptance_criteria:
  - AC-1: docs/phase1/ 目录树与 Plan §B（L34-46）一致：存在 docs/phase1/tasks/ 与 docs/phase1/task_logs/ 两子目录；PHASE1_PLAN.md / TASK_INDEX.md / TASK_PROMPT_TEMPLATE.md 均落盘
  - AC-2: docs/phase1/PHASE1_PLAN.md 内容 = _scratch/phase1_plan_draft_v4.md（已批准 Plan 全文落档）
  - AC-3: docs/phase1/TASK_PROMPT_TEMPLATE.md 对 Plan §B.2（L68-92）列出的 21 处 phase0 字面量逐处处置——(a) 必须替换类（标题/source_spec/task_index/current_task_prompt/commit 前缀/task_logs 路径/batch 编号/L64 与 L72 的 Phase0 措辞）已替换为 phase1 对应物；(b) 显式保留类（CONTEXT_LOADING_STRATEGY / CODING_STYLE_BASELINE / REPOSITORY_CONTEXT_MAP / ROLE_AND_METHOD_GUARDRAILS，及按人类批准（§F 6 决策点全按推荐批准，F.5 = BOUNDARY_CHECKLIST 跨阶段沿用 phase0）沿用的 BOUNDARY_CHECKLIST）每处带一行"跨阶段沿用"说明
  - AC-4: 派生模板 grep 自检无"应改未改"的 phase0 残留——残留口径见下方"grep 口径定义"；被显式注明跨阶段沿用的通用文档引用不算残留
  - AC-5: docs/phase1/TASK_INDEX.md 载入 Plan §C.2（L127-133）B1-B5 DAG；B1 列实际 task_id（P1-GATE-001/P1-SKEL-001/P1-ERRATA-001/P1-PARAM-001 + 可选 P1-TOOLCALL-002）与 depends_on；**且必须含 P1-SPEC-001 作为 B2 前的硬门 DAG 条目，带显式 depends_on（Plan §C.1 L119：PHASE1_SPEC 是 B2 硬前置）**；B2 条目的 prerequisite 必须写明 "P1-GATE-001 passed + PHASE1_SPEC (P1-SPEC-001) approved/landed"；B2-B5 列规划切片与前置；结构沿用 phase0 TASK_INDEX。下方 AC-5b 枚举哪些 task prompt / DAG 条目必须存在——P1-SPEC-001 在内，执行者不得遗漏
  - AC-5b: TASK_INDEX 与 docs/phase1/tasks/ 必须同时存在以下 task prompt / DAG 条目，缺一即不通过：P1-GATE-001、P1-SKEL-001、P1-ERRATA-001、P1-PARAM-001、**P1-SPEC-001**（pre-B2 硬门）、（可选）P1-TOOLCALL-002
  - AC-6: docs/phase1/task_logs/INDEX.md 表头与 phase0 INDEX 完全一致 = "| task_id | date | result | branch | git_sha | ci_run | reviewer |"，且无 phase0 历史行
  - AC-7: docs/phase1/tasks/P1-GATE-001.md 用派生模板写出，高保真转写 Plan §C.3 GATE 卡片（L137-153）：阈值来源 MVP spec §20.1 L4501-4503 + §14.5 L3951-3954 交叉印证 + §12.5 L3496 仅负向/边界清单、通过率口径（边界/安全并入 negative 计 100%、分母从 results 按 category 派生或补 *_total、排除 not_applicable）、gate 边界条件（not_applicable 记录不失败、skipped 默认失败可豁免、failed>0 直接失败）、type=infrastructure/test、method=TDD
  - AC-8: docs/phase1/tasks/P1-ERRATA-001.md 用派生模板写出，转写 Plan §C.3 ERRATA 卡片（L166-189）的 3 条正式条目（每条带可核行号锚点）+ 1 条 legacy note（标注来源=项目记忆，非蓝图）；method=not_applicable
  - AC-9: docs/phase1/tasks/P1-PARAM-001.md 用派生模板写出，明确标注 "ready only after infra values / 等 infra 回值前天然 blocked"（Plan §C.3 PARAM 卡片 L191-201）；其 method 字段值 = `PDR`，scope/type 标注为 `research-only`（即 `method: PDR` 是合法方法值、scope=research-only，执行者不得把 "PDR/research-only" 当成非法方法值；依据 Plan L195-196）
  - AC-10: docs/phase1/tasks/P1-SPEC-001.md 用派生模板写出，定义 Phase 1 详细 spec 任务：承接 blueprint §13 + §4.3 裁剪，产出物 = docs/phase1/PHASE1_SPEC.md，并标注 "B2 硬前置，未批准不得进 B2"（Plan §C.1 L119 / §F 决策 2）
  - AC-11:（可选）docs/phase1/tasks/P1-TOOLCALL-002.md 若写出则明确标 optional/不阻塞 Phase 1（Plan §C.3 L203-212）
  - AC-12: docs/phase1/PHASE1_SPEC.md 占位文件落盘，明确标 "PLACEHOLDER——未落盘，B2 硬前置，由 P1-SPEC-001 产出"，使派生模板 source_spec 锚点不悬空
  - AC-13: git diff --cached --check 干净；git ls-files --others --exclude-standard 无 _scratch 外残留；无 __pycache__/*.pyc/各 cache 被 stage
  - AC-14: 未触碰 app/、app/ports/、docs/phase0/、docs/blueprint/、.github/workflows/、依赖锁文件（git diff --cached --name-only 仅含 docs/phase1/** + 本任务 Task Record）
  - AC-15: Task Record 生成、YAML safe_load 通过、UniqueKeyLoader 无重复 key；method=not_applicable 的 reason/scope/evidence 字段非空；package_confirmation_status=not_applicable；changed_files 与 git diff --cached --name-only 完全一致（含顺序）
required_allowed_paths:
  - docs/phase1/PHASE1_PLAN.md
  - docs/phase1/TASK_PROMPT_TEMPLATE.md
  - docs/phase1/TASK_INDEX.md
  - docs/phase1/task_logs/INDEX.md
  - docs/phase1/tasks/P1-GATE-001.md
  - docs/phase1/tasks/P1-ERRATA-001.md
  - docs/phase1/tasks/P1-PARAM-001.md
  - docs/phase1/tasks/P1-SPEC-001.md
  - docs/phase1/PHASE1_SPEC.md           # PLACEHOLDER 占位（见决策）
  - docs/phase1/task_logs/P1-SKEL-001_<YYYYMMDD_HHMMSS>_passed.yaml
optional_allowed_paths:
  - docs/phase1/tasks/P1-TOOLCALL-002.md  # 可选 B1 任务；仅当本次执行决定一并写出时创建并标 optional
touched_paths:
  - docs/phase1/**
forbidden_paths:
  - app/
  - app/ports/
  - docs/phase0/
  - docs/blueprint/
  - .github/workflows/
  - pyproject.toml
  - uv.lock
  - requirements.txt
  - package.json
  - pnpm-lock.yaml
  - .claude/hooks/
  - .claude/skills/phase-task/
temp_paths:
  - _scratch/p1-skel-001-validation/      # 可选：grep 自检 / YAML safe_load 的临时输出；不得 stage
validation:
  - git status --short
  - git diff --cached --name-only            # 必须仅 docs/phase1/** + Task Record
  - git diff --cached --stat
  - git diff --cached --check                 # 无空白/冲突标记
  - git ls-files --others --exclude-standard  # 无 _scratch 外未跟踪残留
  - Test-Path docs/phase1/tasks 与 docs/phase1/task_logs（两子目录存在）
  - Select-String 派生模板 grep 口径自检（见下"grep 口径定义"）
  - Select-String docs/phase1/task_logs/INDEX.md 表头 == phase0 表头
  - Task Record YAML safe_load
  - Task Record UniqueKeyLoader 重复 key 检查
  - Task Record changed_files == git diff --cached --name-only（含顺序）
  - Task Record stat == git diff --cached --stat
  - package_confirmation_status is not_applicable
  - forbidden path 未改检查（app/ docs/phase0/ docs/blueprint/ .github/workflows/ 依赖锁）
```

## Part B 机读路径块（供 phase-task Part B / Codex spec 阶段直接采用）

ALLOWED_PATHS_START
docs/phase1/PHASE1_PLAN.md
docs/phase1/TASK_PROMPT_TEMPLATE.md
docs/phase1/TASK_INDEX.md
docs/phase1/task_logs/INDEX.md
docs/phase1/tasks/P1-GATE-001.md
docs/phase1/tasks/P1-ERRATA-001.md
docs/phase1/tasks/P1-PARAM-001.md
docs/phase1/tasks/P1-SPEC-001.md
docs/phase1/tasks/P1-TOOLCALL-002.md
docs/phase1/PHASE1_SPEC.md
docs/phase1/task_logs/P1-SKEL-001_*.yaml
ALLOWED_PATHS_END

REQUIRED_ALLOWED_PATHS_START
docs/phase1/PHASE1_PLAN.md
docs/phase1/TASK_PROMPT_TEMPLATE.md
docs/phase1/TASK_INDEX.md
docs/phase1/task_logs/INDEX.md
docs/phase1/tasks/P1-GATE-001.md
docs/phase1/tasks/P1-ERRATA-001.md
docs/phase1/tasks/P1-PARAM-001.md
docs/phase1/tasks/P1-SPEC-001.md
docs/phase1/PHASE1_SPEC.md
docs/phase1/task_logs/P1-SKEL-001_*.yaml
REQUIRED_ALLOWED_PATHS_END

OPTIONAL_ALLOWED_PATHS_START
docs/phase1/tasks/P1-TOOLCALL-002.md
OPTIONAL_ALLOWED_PATHS_END

FORBIDDEN_PATHS_START
app/
app/ports/
docs/phase0/
docs/blueprint/
.github/workflows/
pyproject.toml
uv.lock
requirements.txt
package.json
pnpm-lock.yaml
.claude/hooks/
.claude/skills/phase-task/
FORBIDDEN_PATHS_END

TEMP_PATHS_START
_scratch/p1-skel-001-validation/
TEMP_PATHS_END

> 注：`docs/phase1/task_logs/INDEX.md` 是受限元数据路径——本任务只允许新增 P1-SKEL-001 自己的行（且本任务还要建立表头），不得改动任何其他行。

## 派生模板的两类处置（来自 Plan §B.2，L68-92，已核 21 处命中）

派生模板 `docs/phase1/TASK_PROMPT_TEMPLATE.md` 必须逐处处置 Plan §B.2 表里列出的 21 个 phase0 字面量。两类：

**(a) 必须替换为 phase1 锚点：**

| 模板基底行（Plan §B.2 已核） | 替换 |
|---|---|
| L1 标题 `# CODEX_SINGLE_TASK_PROMPT_TEMPLATE — Phase 0 v2.0.0` | → `# TASK_PROMPT_TEMPLATE — Phase 1 v1.0.0` |
| L9 `source_spec: docs/blueprint/...mvp_spec_v1_0_11.md` | → `source_spec: docs/phase1/PHASE1_SPEC.md`（占位见决策） |
| L10 `task_index: docs/phase0/TASK_INDEX.md` | → `docs/phase1/TASK_INDEX.md` |
| L13 `current_task_prompt: docs/phase0/tasks/<task_id>.md` | → `docs/phase1/tasks/<task_id>.md` |
| L20 正文 "整份 Phase 0 spec" | → "整份 Phase 1 spec" |
| L64 `repository-changing Phase 0 / Phase 1 tasks default to codex_review` | → "repository-changing Phase 1 tasks default to `codex_review`" |
| L72 `P0-PREP-* 是 execution-pack-only ...` | → 改 `P1-PREP-*` 规则，或 Phase 1 无此类任务则删该行 |
| L101 / L220 commit 格式 `phase0(<task_id>): ...` | → `phase1(<task_id>): ...` |
| L157 / L204 `docs/phase0/task_logs/...` | → `docs/phase1/task_logs/...` |
| L123 interface_contract 引用 | → 指向 Phase 1 spec 章节；若继承冻结 Phase 0 spec §8.4.1 须**显式注明"继承冻结 Phase 0 spec §8.4.1"** |
| L248 `docs/phase0/tasks/<task_id>.md` | → `docs/phase1/tasks/<task_id>.md` |
| L265 / L269 `Batch 0/1 已内置 ... docs/phase0/tasks/<task_id>.md` | → 路径改 `docs/phase1/tasks/`；Batch 编号按 Phase 1 B1-B5 改写或泛化（"Batch 0/1 已内置"不适用 Phase 1） |

**(b) 显式保留 phase0 通用文档（跨阶段沿用，每处加一行说明）：**

| 模板基底行 | 处置 |
|---|---|
| L12 / L20 `CONTEXT_LOADING_STRATEGY.md` | 保留 phase0 路径 + 注 "跨阶段沿用" |
| L24 `CODING_STYLE_BASELINE.md` | 保留 + 注沿用 |
| L25 `REPOSITORY_CONTEXT_MAP.md` | 保留 + 注沿用 |
| L68 / L238 `ROLE_AND_METHOD_GUARDRAILS.md` | 保留 + 注沿用 |
| L11 `BOUNDARY_CHECKLIST.md` | 按本 prompt 登记的人类批准决策（§F 全部 6 决策点已按推荐批准，其中 F.5 = BOUNDARY_CHECKLIST 跨阶段沿用 phase0）= 沿用 phase0（跨阶段通用），保留 + 注沿用。（注：Plan §F.5 本身只是带推荐的待拍板决策点，非锁定；锁定来自下方人类批准登记） |

## grep 口径定义（AC-4 的可证伪含义）

"派生模板内无 phase0 残留"的精确口径（来自 Plan §B.2 说明，L92）：

- **残留 = 应改未改的 phase1 锚点**——即出现 `docs/phase0/tasks/`、`docs/phase0/TASK_INDEX.md`、`docs/phase0/task_logs/`、`source_spec: docs/blueprint/...`、`phase0(` commit 前缀、标题里的 "Phase 0"、batch "Batch 0/1 已内置" 等本应替换为 phase1 的字面量。
- **不算残留 = 被显式注明"跨阶段沿用"的通用文档引用**——即上表 (b) 类的 5 个 `docs/phase0/` 路径：`docs/phase0/CONTEXT_LOADING_STRATEGY.md` / `CODING_STYLE_BASELINE.md` / `REPOSITORY_CONTEXT_MAP.md` / `ROLE_AND_METHOD_GUARDRAILS.md` / `BOUNDARY_CHECKLIST.md`，因其阶段无关、刻意保留。
- **不算残留 = L123 派生处置允许的契约继承短语**——即**精确字面短语** `继承冻结 Phase 0 spec §8.4.1`（来自模板 L123：interface_contract 任务声明继承 Phase 0 冻结契约全局样例）。该例外**仅**当此短语**逐字出现**且**紧随其继承说明语境**（即用于 interface_contract 任务"声明继承冻结 Phase 0 spec §8.4.1"的引用）时成立；任何其他形式的 "Phase 0" 命中（含缺少该继承说明、或非此精确短语的 "Phase 0 spec"）一律仍按残留处理，不得援引本例外作为任意 "Phase 0" 残留的挡箭牌。
- 自检建议：`Select-String 'phase0\(|docs/phase0/tasks|docs/phase0/TASK_INDEX|docs/phase0/task_logs|Phase 0'` 对派生模板，逐条命中比对——命中项若属上述 (b) 类 5 文件、或为精确短语 `继承冻结 Phase 0 spec §8.4.1`（且带继承说明语境）则放行，否则即残留、必须修。
- 自检临时输出可写 `_scratch/p1-skel-001-validation/`，不得 stage。

## PHASE1_SPEC.md 占位决策（本任务的判断点，已拍板）

**决策：建一份明确标注的 PLACEHOLDER `docs/phase1/PHASE1_SPEC.md`，而非留空锚点。**

理由：派生模板的 `source_spec` 指向 `docs/phase1/PHASE1_SPEC.md`（Plan §B.2 L71）。若该文件不存在，模板锚点悬空，下游任何引用模板的 prompt 都会触发 `task_prompt_incomplete` 误报。但 PHASE1_SPEC 的**真实内容是 P1-SPEC-001 的产出，不是 SKEL 的**（Plan §C.1 L119）。因此 SKEL 只落一份占位 stub，正文写明：

> `# PHASE1_SPEC — PLACEHOLDER`
> 本文件未落盘正式内容。Phase 1 详细 spec 是 B2-B5 实现型纵切的**硬前置**，由 **P1-SPEC-001** 产出（承接 blueprint §13 + §4.3 裁剪）。未经 P1-SPEC-001 落盘并批准前，**不得进入 B2**。本占位仅为消除 TASK_PROMPT_TEMPLATE.md `source_spec` 锚点悬空。

占位文件不得包含任何臆造的 spec 内容（防止被误当真 spec）。

## 行动前必须先输出 Plan，等人类确认后再改文件

套用 `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` 的 "行动前必须先输出 Plan" 八节结构（Task Scope / Acceptance Criteria 逐条 / Blocking Examples / Step-by-step Plan / Step Verification Points / Files to Touch / Forbidden Paths / Test Commands / Stop Conditions）。本任务为 documentation/preparation，第 3 节用 blocking examples（例：`_scratch/phase1_plan_draft_v4.md` 缺失或非批准版 → `task_prompt_incomplete` 停手；phase0 模板基底不可读 → 停手）。

## Stop Conditions

出现以下情况停止并报告：
- 需修改 forbidden_paths（app/、docs/phase0/、docs/blueprint/、.github/workflows/、依赖锁、hooks、orchestrator）；
- `_scratch/phase1_plan_draft_v4.md` 缺失、或与"已批准"状态不符 → `task_prompt_incomplete`；
- 派生模板 grep 自检发现 (a) 类残留无法在 docs/phase1/** 内修复；
- 需新增依赖或改 schema；
- 任何 §B.2 行号锚点在 phase0 模板里核不到对应内容（说明基底已变）→ 停手报告，不臆改。

## Test Commands（最终前全跑，来自 CLAUDE.md §Validation）

```powershell
git status --short
git diff --cached --name-only
git diff --cached --stat
git diff --cached --check
git ls-files --others --exclude-standard
```

文档型任务不跑 pytest/ruff/mypy（无代码改动）；若 stage 内意外出现非 docs/phase1 路径，立即停手。

## Task Record 收尾

- 输出统一 Task Record，schema 沿用 `docs/dev/task_record_schema.yaml`。
- 保存路径：`docs/phase1/task_logs/P1-SKEL-001_<YYYYMMDD_HHMMSS>_passed.yaml`（failed/blocked 同 schema 改后缀）。
- 在 `docs/phase1/task_logs/INDEX.md` 追加 P1-SKEL-001 一行，列对齐 phase0 表头 `| task_id | date | result | branch | git_sha | ci_run | reviewer |`（git_sha 用 deferred convention；ci_run 文档型可填 not_applicable；reviewer 待 Codex review 后回填）。
- `method=not_applicable` 的 reason/scope/evidence 字段必须非空；`package_confirmation_status=not_applicable`。
- `changed_files` 在最终 stage 之后、commit 之前更新，与 `git diff --cached --name-only` 完全一致（含顺序）。

## Git 收尾（仅人类确认后）

- 分支 `phase1/P1-SKEL-001`；commit `phase1(P1-SKEL-001): <one-line summary>`。
- merge 目标 `phase0/main`，`--no-ff`，merge message `merge phase1(P1-SKEL-001): <short description>`（CLAUDE.md §Git workflow 约定）；不得 `--no-verify`。
- merge 后查 remote GitHub Actions CI。

## 执行指令

套用 `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` 于本任务。先输出 Plan，等人类确认后再改文件。
