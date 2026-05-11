# Phase 0 Role and Method Guardrails v1.0.0

Shared advisory guardrails for execution and review roles. Both Claude Code / MiMo and Codex / generic coding agents apply these rules according to their current role, as referenced by their respective boot files.

Centralizing these guardrails is meant to reduce duplicated boot context; it does not expand default context loading, restore cross-reading, or justify rule bloat.

## Relationship to Task Prompt / Phase 0 Rules / Codex Review

- These guardrails are advisory.
- They do not override the current task prompt, Phase 0 rules, forbidden paths, no-commit/no-push rules, or required review.
- Tasks may declare a role: `execution` / `review` / `mixed` / `documentation`.
- Tasks may declare an applicable method: `PDR` / `BDD` / `TDD` / `mixed` / `not_applicable`.
- This file is shared guidance for both execution and review roles, regardless of whether the tool is Claude Code / MiMo or Codex / generic coding agent.

## Execution Guardrails

1. **Think before coding.**
   - State assumptions when they affect implementation.
   - Surface ambiguity instead of silently choosing a risky interpretation.
   - Prefer a brief plan before editing multiple files.

2. **Keep changes simple.**
   - Implement the minimum change needed for the current task.
   - Do not add speculative abstractions, extra configurability, or unrelated features.
   - If the solution becomes large, reassess whether a smaller change would satisfy the task.

3. **Make surgical edits.**
   - Touch only files required by the current task.
   - Do not refactor, reformat, rename, or clean up unrelated code.
   - Remove only unused code introduced by the current change unless explicitly asked.

4. **Verify against clear success criteria.**
   - Translate the task into concrete checks before declaring completion.
   - Run relevant tests, lint, type checks, or documented validation commands when available.
   - Report what was verified and what was not verified.

5. **Respect project authority.**
   - These are advisory execution behavior rules.
   - They must not override the current task prompt, Phase 0 rules, forbidden paths, no-commit/no-push requirements, repository-specific instructions, or required review.

## Review Guardrails

1. **Review before suggesting changes.**
   - Understand the task prompt, relevant templates, staged diff, task record, and repository rules before giving a verdict.
   - Do not assume the implementation goal from code changes alone.
   - If the task scope is ambiguous, identify the ambiguity as a review risk.

2. **Prefer minimal compliant fixes.**
   - Recommend the smallest fix that satisfies the task and repository rules.
   - Do not suggest broad refactors, new abstractions, or unrelated cleanups unless required for correctness or safety.
   - Separate blocking issues from optional improvements.

3. **Enforce surgical scope.**
   - Every changed file should trace back to the current task.
   - Flag unrelated formatting, renames, refactors, generated-file churn, or changes outside allowed paths.
   - Do not reward extra work that expands the task without permission.

4. **Verify against explicit success criteria.**
   - Review against the task prompt, task record, tests, documented validation commands, and PDR / BDD / TDD when present.
   - Check whether claimed verification was actually run and whether failures are explained.
   - If verification is missing, stale, or insufficient, mark it clearly.

5. **Give an actionable verdict.**
   - Use PASS only when there are no blocking issues.
   - Use FAIL when the diff violates task scope, repository rules, required validation, security constraints, or task-record requirements.
   - Keep optional suggestions separate from required fixes.

6. **Respect project authority.**
   - These are advisory review behavior rules.
   - They must not override the current review task prompt, Phase 0 rules, forbidden paths, no-commit/no-push requirements, or project-specific review requirements.

## Engineering Method Selection

1. **PDR / design review:**
   - Use for ADR, spike, architecture boundary, dependency selection, safety/security boundary, feasibility decision.
   - Expected evidence: plan, acceptance criteria, alternatives, risks, blocking conditions, recommendation, verification, review.
   - Phase 0 document / spike tasks default to PDR-first unless task says otherwise.

2. **BDD:**
   - Use for business workflows, user-visible behavior, API behavior, workflow orchestration, Golden Task, adapter behavior contracts.
   - Express as Given / When / Then or input-action-expected-output.
   - Do not require .feature files by default; avoid file proliferation and context pollution.
   - BDD can live in task prompts, ADRs, fixtures, or test descriptions.

3. **TDD:**
   - Use for production code, runtime, gateway, policy, identity, trace, mock adapter, runner, bugfix, regression-sensitive changes.
   - Prefer minimal failing assertion first, then implementation, then regression.
   - Pure documentation, ADR, and research spikes do not require TDD.

4. **Mixed / not_applicable:**
   - Some tasks may combine PDR + BDD + TDD.
   - Some documentation-only tasks may mark BDD/TDD not_applicable.
   - Do not mechanically require all three methods for every task.

5. **Review expectation:**
   - Review whether the selected method fits the task type.
   - If a task claims BDD/TDD/PDR evidence, verify the evidence is real.
   - Do not fail a pure ADR task only because no TDD exists.

> Batch 1 completion will be followed by a separate P0-TEMPLATE-001-style task to upgrade Batch 2 / Phase 1 task templates with method_profile and BDD/TDD/PDR evidence rules. This document defines principles only; it does not perform that template upgrade.

## Code Style Discipline

- Follow existing project style.
- Do not reformat unrelated files.
- Do not introduce broad formatting or style churn.
- Prefer project formatter/linter rules when they exist.
- Reviewers should flag unrelated formatting or style drift as scope creep.

> Full coding style baseline will be defined in a future P0-STYLE-001 task.
> This section establishes principles only.

## Superpowers Usage

- Superpowers is installed in Claude Code as an advisory workflow aid for planning, self-review, and debugging.
- It must not override task prompt, Phase 0 rules, forbidden paths, no-commit/no-push, or Codex review.
- It must not automatically create extra files, commit, push, merge, or move to the next task.
- Do not modify CLAUDE.md, AGENTS.md, or repository rules just to enable Superpowers.

## External Guideline Handling

- Inspired by Karpathy-style Claude Code guidance; not copied wholesale.
- Does not replace repository CLAUDE.md or AGENTS.md.
- No dependency on the external repository.
- External guidance is not authoritative over Phase 0 rules.
