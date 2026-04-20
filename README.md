# Codex Business Repo PLAN Pack v2

用途：给 **真实业务仓库** 的 Codex PLAN 模式使用，生成可进入 Phase 0/0.5 的 spec-first 规划包。

## 使用方式

把本目录放到目标业务仓库根目录，然后在 Codex PLAN 模式里发送：

```text
/plan 请严格执行仓库中的 `prompts/business_repo_plan_prompt.md`，并同时参考 `templates/business_repo_output_contract.md` 与 `checklists/business_repo_review_checklist.md`。本轮目标只有一个：基于真实业务仓库源码与配置，产出尽可能详细、可机器消费、可用于后续自动执行的 spec-first 规划包。禁止进入大规模实现。任务清单和检查清单必须细到可直接用于调度、自动校验与人工复核。
```

## 本轮必须产出的核心文件

- `spec/product_spec.md`
- `spec/product_spec.yaml`
- `spec/module_spec.yaml`
- `spec/resource_spec.yaml`
- `spec/invariant_spec.yaml`
- `contracts/README.md`
- `planning/dev_plan.yaml`
- `planning/acceptance_spec.yaml`
- `planning/run_policy.yaml`
- `planning/implement.md`
- `planning/documentation.md`
- `planning/plan_review_checklist.md`
- `planning/phase0_result.json`

## 复核重点

1. 任务是否围绕真实业务模块，而不是围绕规划包自身。
2. `phase0_result.json` 是否把 `planning_ready` 与 `execution_ready` 分开。
3. `dev_plan.yaml` 是否包含 `layer`，且任务足够细。
4. `acceptance_spec.yaml` 的 `expect` 是否可自动判定，而不是自然语言空话。
5. `implement.md` 是否包含厚回执字段。
6. 是否明确了共享资源、不变量、Human Gates、Open Questions。
