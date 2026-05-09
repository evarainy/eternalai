# Phase 0 Package Definition v1.0.11

Phase 0 文档中的 `package` 不是 Docker image，也不是随手 zip。v1.0.11 中，package confirmation 不再绑定强制人工 diff review。

## Definition

For Phase 0 v1.0.11, a fresh package means the current task's deliverable was regenerated, organized, or recorded from the current repository state.

Depending on task type, this may be:

- a git commit for a file-modifying implementation task;
- an ADR and evidence log for a Spike task;
- a Task Record and audit output for a preparation / review task;
- an updated execution pack artifact for documentation packaging work.

## Task Record fields

```yaml
package_confirmation_status: "created | not_created | not_applicable"
package_confirmation: "confirmation that a fresh package was created from the current repository state"
package_scope: ""
package_evidence: ""
```

## Rules

- The fixed confirmation phrase is retained.
- It means the task deliverable reflects the current repository state.
- It does not mean mandatory human diff review was completed.
- For read-only checks or Spike research where no code package exists, use `not_applicable` with reason and evidence.
- For file-modifying tasks, record the commit SHA when a commit is created.
- Do not push or merge without human instruction.
