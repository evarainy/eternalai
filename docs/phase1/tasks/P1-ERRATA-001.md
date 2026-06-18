# P1-ERRATA-001 — Single-task Prompt

## Background / 任务由来

Phase 0 验收后发现蓝图/spec 若干处与实际基线不符或需澄清，需集中登记于 `docs/phase1/BLUEPRINT_ERRATA.md`，**不改冻结蓝图正文**（蓝图 freeze，只旁注）。

本任务登记 3 条正式勘误/澄清条目（每条带可核行号锚点）+ 1 条 legacy note（来源=项目记忆，非蓝图）。

## method_profile

```yaml
method_profile:
  execution_role: "documentation"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "not_applicable"
  reason_for_owner_choice: >
    纯文档登记任务，无生产代码、无测试断言。
    method=not_applicable 理由：scope=documentation-only，
    evidence=行号锚点可逐一核到文件，无需 TDD/BDD/PDR 方法框架。
```

## Task YAML

```yaml
task_id: P1-ERRATA-001
title: "BLUEPRINT_ERRATA.md 勘误 + 澄清登记（3 条正式 + 1 legacy note）"
type: documentation
method: not_applicable
not_applicable_reason: "纯文档登记，无生产代码变更、无测试用例，不适用 TDD/BDD/PDR"
not_applicable_scope: "current_phase_only"
not_applicable_evidence: "touched_paths 仅含 docs/phase1/BLUEPRINT_ERRATA.md + Task Record；无 app/ 改动"
objective: >
  在 docs/phase1/BLUEPRINT_ERRATA.md 集中登记蓝图勘误与澄清，
  文档命名/分节为"Errata & Clarifications"。
  不改冻结蓝图正文；只新增 ERRATA 文件。
errata_entries:
  formal_errata:
    - id: "E-001"
      type: "Erratum"
      title: "instructor 非基线"
      anchors:
        - "blueprint v3.2.4 §6.11 L1332"
        - "blueprint v3.2.4 §12.1.3 L2505"
      original: "Phase 1 默认采用 OpenAI SDK + instructor + Pydantic v2"
      correction: >
        已被 ADR-P0-SPIKE-002 否定。PHASE1_TECHNICAL_BASELINE.md §2 基线 =
        raw OpenAI SDK，无 wrapper（无 instructor / PydanticAI）。
        冻结正文未注记，本条勘误补充。
    - id: "E-002"
      type: "Clarification"
      title: "ARQ 层级澄清"
      anchors:
        - "blueprint §12.1.4 L2517-2518（L0/L1）"
        - "blueprint §12.1.4 L2525（结论）"
        - "blueprint 升级路线 L2580-2585"
      clarification: >
        ARQ 是 L1 候选（部门试点才启用），Phase 1 主线 L0 = BackgroundTasks/in-process。
        蓝图已明示"ARQ 不作为长期不可替换底座"（L2518/L2526）。
        属澄清，避免实现误读为 Phase 1 必装，非蓝图错误。
    - id: "E-003"
      type: "Erratum"
      title: "adapter_error_mapped 错位"
      anchors:
        - "MVP spec §8.6.7 TraceEvent.event_type L878-903（缺 adapter_error_mapped）"
        - "MVP spec §12.4.1 trace 矩阵 L3490（引用 adapter_error_mapped）"
        - "app/ports/trace.py L25（端口已含，正确）"
      correction: >
        实现（端口）正确，spec §8.6.7 漏列 adapter_error_mapped。
        勘误指向 spec §8.6.7，不动端口（端口冻结且正确）。
  legacy_notes:
    - id: "L-001"
      source: "项目记忆（MEMORY），蓝图正文无引用（grep docs/ 0 命中）"
      note: >
        jsjy 库已废弃。登记废弃事实防误引。
        因不满足 ERRATA"权威文件+行号"规则，移出正式勘误，归入 legacy note 并显式标注来源=记忆，非蓝图依据。
acceptance_criteria:
  - AC-1: docs/phase1/BLUEPRINT_ERRATA.md 落盘，分节为"Errata & Clarifications"
  - AC-2: 3 条正式条目每条带可核行号锚点（blueprint/spec 文件 + 章节号 + 行号）
  - AC-3: legacy note 明确标注"来源=项目记忆，非蓝图"
  - AC-4: 不改 docs/blueprint/ 任何文件（蓝图冻结）
  - AC-5: 不改 app/、app/ports/
  - AC-6: method=not_applicable 的 reason/scope/evidence 字段非空
touched_paths:
  - docs/phase1/BLUEPRINT_ERRATA.md
  - docs/phase1/task_logs/P1-ERRATA-001_*.yaml
forbidden_paths:
  - app/
  - app/ports/
  - docs/phase0/
  - docs/blueprint/
  - .github/workflows/
```

## Stop Conditions

- 需修改 `docs/blueprint/` 任何文件 → 停手（蓝图冻结，只新增 ERRATA 旁注文件）
- 正式勘误条目行号锚点无法在目标文件中核到 → 停手报告，不臆改行号
- 需修改 `app/` 任何文件 → 停手

## Test Commands（文档型任务）

```powershell
git diff --cached --name-only   # 仅含 docs/phase1/BLUEPRINT_ERRATA.md + Task Record
git diff --cached --check        # 无空白/冲突标记
git ls-files --others --exclude-standard  # 无 _scratch 外未跟踪残留
```
