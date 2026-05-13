# Phase 0 Repository Context Map and Progressive Loading Guide v1.0.0

## 1. Purpose

This file is a navigation and context-loading map for the eternalai repository. It is **not** a new rules document and does not add constraints beyond what CLAUDE.md, AGENTS.md, task prompts, and Phase 0 governance files already define.

**Goal:** Help humans and AI agents quickly find the files they need while avoiding context pollution — loading too much, too little, or the wrong context for the current task.

**Scope:**
- Covers the Phase 0 repository structure as of this task.
- This is an advisory guide; it does not override task prompts, Phase 0 rules, forbidden paths, or no-commit/no-push rules.
- Update this map only when repository structure or canonical docs change.

## 2. Default minimal context

### Claude Code / MiMo — execution tasks

| File | Role |
|---|---|
| CLAUDE.md | Boot rules — always loaded |
| docs/phase0/tasks/\<task_id\>.md | Current task prompt |
| docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md | Task execution template |
| Relevant sections of docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md | According to current task role/method; do not load full file by default |
| docs/phase0/CONTEXT_LOADING_STRATEGY.md | Only as the routing guide required by current boot/task instructions; do not expand unrelated context |
| Currently touched files | Only files listed in current task touched_paths |

Do not read AGENTS.md by default.

### Codex / generic coding agent — execution tasks

| File | Role |
|---|---|
| AGENTS.md | Boot rules — always loaded |
| docs/phase0/tasks/\<task_id\>.md | Current task prompt |
| docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md | Task execution template |
| Current touched files | Only files listed in current task touched_paths |
| Relevant guardrails / style sections only | Not full ROLE_AND_METHOD_GUARDRAILS.md or CODING_STYLE_BASELINE.md by default |

Do not read CLAUDE.md by default.

### Codex / generic coding agent — review tasks

| File | Role |
|---|---|
| AGENTS.md | Boot rules — always loaded |
| Current review / task prompt | Defines review scope |
| Staged diff | The changes to review |
| Task Record | Historical evidence for the task under review |
| Relevant guardrails / style sections only | Not full documents by default |

### Not loaded by default

- Full frozen blueprint documents (`docs/blueprint/`)
- All historical task prompts (`docs/phase0/tasks/`)
- All historical task records (`docs/phase0/task_logs/`)
- All ADRs (`docs/adr/`)
- Full `docs/phase0/CODING_STYLE_BASELINE.md` — load relevant sections per applicability guide only
- Full `docs/phase0/BOUNDARY_CHECKLIST.md` — load every 3 tasks, not every session
- External documents, vendor URLs, or prior conversation logs
- The other tool's boot file (Claude Code does not read AGENTS.md; Codex does not read CLAUDE.md)

## 3. Key directories and files

All paths below were verified to exist at the time this file was created. Mandatory paths must pass `Test-Path` at execution time; if a mandatory path no longer exists, stop and fix before continuing.

| Path | Purpose | Load when |
|---|---|---|
| **Root** | | |
| CLAUDE.md | Claude Code / MiMo boot rules | Every Claude Code session |
| AGENTS.md | Codex / generic coding agent boot rules | Every Codex session |
| MANIFEST.md | Document inventory for Phase 0 execution pack | When verifying file completeness |
| .gitignore | Git ignore rules | Rarely; only when troubleshooting missing files |
| **Agent configurations** | | |
| .claude/agents/boundary-checker.md | Claude Code boundary-checker subagent definition | Optional; when running boundary checks |
| .codex/agents/boundary-checker.toml | Codex boundary-checker agent definition | Optional; when running boundary checks |
| **docs/phase0/** | | |
| docs/phase0/CONTEXT_LOADING_STRATEGY.md | Tiered context loading model (Tier 0-3) | When unsure what to load |
| docs/phase0/TASK_INDEX.md | Phase 0 task dependency DAG and batch order | When selecting next task or checking dependencies |
| docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md | Shared execution/review guardrails and method selection | Relevant sections per current task role |
| docs/phase0/CODING_STYLE_BASELINE.md | Coding style and quality baseline | Relevant sections per applicability guide |
| docs/phase0/BOUNDARY_CHECKLIST.md | Every-3-task self-check (import boundary, secrets, forbidden paths, etc.) | Every 3 completed tasks |
| docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md | Task execution template | Every task session |
| docs/phase0/FIRST_BATCH_TASKS.md | Batch 0 + Batch 1 task selection index | When selecting Batch 0/1 tasks |
| docs/phase0/ADR_TEMPLATE.md | ADR document template | When creating new ADRs |
| docs/phase0/README_FOR_CODEX.md | Codex quick-start guide | First-time Codex setup |
| docs/phase0/FRONTEND_MOCK_FIXTURES.md | Frontend mock fixture definitions | When working on frontend mocks |
| docs/phase0/REPOSITORY_CONTEXT_MAP.md | This file — repository navigation map | When orienting in the repo |
| docs/phase0/PHASE1_TECHNICAL_BASELINE.md | Phase 1 structured-output technical baseline decision summary | When implementing Phase 1 LLM structured output or changing output strategy |
| **docs/phase0/tasks/** | | |
| docs/phase0/tasks/*.md | Per-task prompts (one file per task_id) | Current task only |
| **docs/phase0/task_logs/** | | |
| docs/phase0/task_logs/*.yaml | Unified Task Records (historical evidence) | Current task record only; do not load all |
| docs/phase0/task_logs/INDEX.md | Human-maintained task log index | When reviewing task history |
| **docs/phase0/self_checks/** | | |
| docs/phase0/self_checks/README.md | Self-check documentation and template | When understanding self-check process |
| docs/phase0/self_checks/*.yaml | Boundary self-check logs — optional, if present; generated by future tasks | Every 3 tasks or when auditing |
| **docs/dev/** | | |
| docs/dev/task_record_schema.yaml | Unified Task Record YAML schema | When generating task records |
| docs/dev/git_workflow.md | Branch/commit/rollback rules | When unsure about git workflow |
| docs/dev/human_review_checklist.md | Optional human review checklist | After task completion (optional) |
| docs/dev/package_definition.md | Package confirmation semantics | When filling package_confirmation fields |
| docs/dev/codex_setup.md | Codex execution posture | First-time Codex setup |
| **docs/blueprint/** | | |
| docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md | Frozen Phase 0 spec | Only when resolving contradictions or generating task prompts |
| docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md | Frozen enterprise blueprint | Only when resolving contradictions |
| docs/blueprint/phase0_handoff_after_blueprint_freeze.md | Blueprint handoff notes | Only when resolving contradictions |
| **docs/adr/phase0/** | | |
| docs/adr/phase0/ADR-P0-SPIKE-004-redis-arq.md | Redis + ARQ spike ADR | When working on Redis/ARQ tasks |
| docs/adr/phase0/ADR-P0-SPIKE-005a-oa-api-auth.md | OA API auth spike ADR | When working on OA adapter |
| docs/adr/phase0/ADR-P0-SPIKE-005b-u8-api-auth.md | U8 API auth spike ADR | When working on U8 adapter |
| docs/adr/phase0/ADR-P0-SPIKE-005c-hikvision-ivms-api-auth.md | Hikvision iVMS API auth spike ADR | When working on Hik adapter |
| docs/adr/phase0/ADR-P0-SPIKE-006-s3-compatible-storage.md | S3-compatible storage spike ADR | When working on object storage |
| docs/adr/phase0/README.md | ADR directory index | When navigating ADRs |
| **docs/research/phase0/** | | |
| docs/research/phase0/target_systems/oa_research_notes.md | OA system research | When working on OA integration |
| docs/research/phase0/target_systems/u8_research_notes.md | U8 system research | When working on U8 integration |
| docs/research/phase0/target_systems/hikvision_ivms_research_notes.md | Hikvision iVMS research | When working on Hik integration |
| **experiments/phase0/** | | |
| experiments/phase0/redis_arq/ | Redis + ARQ spike experiments | When referencing spike code (not production) |
| experiments/phase0/s3_storage/ | S3-compatible storage spike experiments | When referencing spike code (not production) |
| **infra/** | | |
| infra/docker/ | Docker Compose and Dockerfile templates | When working on deployment/infrastructure |

## 4. Authority / source-of-truth relationships

| Source | Authority | Notes |
|---|---|---|
| Task prompt (`docs/phase0/tasks/<task_id>.md`) | Defines current task scope, acceptance criteria, touched_paths, forbidden_paths | Primary authority for the current task |
| Task Record (`docs/phase0/task_logs/*.yaml`) | Historical evidence after task close / commit / merge; do not rewrite closed historical task records | Current in-progress Task Record may be updated before commit to correct evidence, changed_files, or validation output |
| Frozen blueprint (`docs/blueprint/`) | Reference-only architecture and spec documents | Do not modify unless a task prompt explicitly allows it |
| Completed task prompts (`docs/phase0/tasks/`) | Should not be rewritten after task completion | Historical record of what was requested |
| Spike code (`experiments/phase0/`) | Not production code | Must not enter `app/` without a promotion task |
| MANIFEST.md | Document inventory for Phase 0 execution pack | Update when adding canonical docs |
| TASK_INDEX.md | Phase 0 task dependency DAG | Tracks batch order and dependencies |
| CODING_STYLE_BASELINE.md | Section reference for coding style | Not default full context; load relevant sections per applicability guide |
| CONTEXT_LOADING_STRATEGY.md | Tiered loading model (Tier 0-3) | Defines what to load and when |
| ROLE_AND_METHOD_GUARDRAILS.md | Shared execution/review guardrails | Advisory; does not override task prompts or Phase 0 rules |

## 5. Progressive loading guide by task type

| Task type | Load by default | Load on demand | Do not load |
|---|---|---|---|
| **1. Documentation / ADR task** | Boot file, task prompt, task template, ADR template (if creating ADR) | CODING_STYLE_BASELINE.md documentation/ADR section, existing related ADRs | Production code files, database specs, API specs, full blueprint |
| **2. Spike task** | Boot file, task prompt, task template | Related ADRs, research notes, experiment code, CODING_STYLE_BASELINE.md security/dependency sections if spike touches those domains | Production app/ code, full blueprint, unrelated spike ADRs |
| **3. Code task** | Boot file, task prompt, task template, touched files | CODING_STYLE_BASELINE.md Python/backend/testing sections, ROLE_AND_METHOD_GUARDRAILS.md execution guardrails, existing port/adapter interfaces | Full blueprint, all ADRs, all task records, documentation-only baseline sections |
| **4. Database / persistence task** | Boot file, task prompt, task template, touched files | CODING_STYLE_BASELINE.md database/schema section, ADR-P0-SPIKE-003 (if pgvector-related), existing migration files | Full blueprint, unrelated ADRs, frontend specs |
| **5. API / gateway task** | Boot file, task prompt, task template, touched files | CODING_STYLE_BASELINE.md API/interface section, interface contract docs, existing route definitions | Full blueprint, database migration specs, unrelated ADRs |
| **6. Capability / plugin / tool / skill task** | Boot file, task prompt, task template, touched files | CODING_STYLE_BASELINE.md extension-point/capability section, capability registry interface, existing adapter interfaces | Full blueprint, database migration specs, unrelated gateway code |
| **7. Review task** | Boot file, review prompt, staged diff, Task Record | Relevant guardrails/style sections for the domain under review, original task prompt being reviewed | Full blueprint, all historical task records, unrelated ADRs |
| **8. Debug task** | Boot file, task prompt (if one exists), touched files, error logs | Relevant ADR or task record for the feature being debugged, CODING_STYLE_BASELINE.md error-handling section, boundary checklist | Full blueprint, all historical prompts, unrelated experiments |

## 6. Debug / modification routing

| Problem | Where to look | Do not touch |
|---|---|---|
| **Task Record YAML invalid** | `docs/dev/task_record_schema.yaml` for schema; `python -c "import yaml; yaml.safe_load(...)"` for validation | Do not modify schema to fit a broken record; fix the record |
| **changed_files mismatch** | `git diff --cached --name-only` vs Task Record `changed_files` field; rerun staging if needed | Do not edit git output; update the Task Record to match staged diff |
| **duplicate YAML key** | UniqueKeyLoader check; scan Task Record YAML for repeated keys at same indent level | Do not use plain safe_load as duplicate-key validator; it silently accepts duplicates |
| **stale evidence / stat mismatch** | `git diff --cached --stat` vs Task Record stat evidence; Task Record may have been generated before final staging | Do not commit stale evidence; update Task Record after final staging |
| **forbidden path modified** | `git diff --cached --name-only` filtered against forbidden_paths list; `BOUNDARY_CHECKLIST.md` forbidden paths check | Revert the forbidden path change immediately; do not commit |
| **Docker / Redis / MinIO / dependency issue** | `infra/docker/` for Docker configs; `docs/adr/phase0/ADR-P0-SPIKE-004-redis-arq.md` for Redis; `docs/adr/phase0/ADR-P0-SPIKE-006-s3-compatible-storage.md` for S3/MinIO; `experiments/phase0/` for spike experiment code | Do not modify production dependency files (`pyproject.toml`, `uv.lock`, `package.json`, `pnpm-lock.yaml`) unless task allows |
| **context loading / cross-read confusion** | `docs/phase0/CONTEXT_LOADING_STRATEGY.md` for tiered model; this file Section 2 for default minimal context; boot files for tool-specific rules | Do not cross-read the other tool's boot file unless task explicitly requires; do not load Tier 3 docs by default |
| **style drift / formatting churn** | `docs/phase0/CODING_STYLE_BASELINE.md` relevant section; `docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md` Code Style Discipline section | Do not reformat unrelated files; do not introduce broad style changes outside current task scope |
| **database / API / plugin boundary confusion** | `docs/phase0/CODING_STYLE_BASELINE.md` database, API, and extension-point sections; `docs/phase0/BOUNDARY_CHECKLIST.md` import boundary check; spike ADRs for architecture decisions | Do not mix database schema changes with API contract changes; keep each task surgical |

## 7. Do not load by default

The following should not be loaded into a task session unless the task prompt explicitly requires them:

- **Entire frozen blueprint** (`docs/blueprint/`) — consult only when resolving contradictions or generating future task prompts
- **All historical task prompts** (`docs/phase0/tasks/`) — load only the current task prompt
- **All historical task records** (`docs/phase0/task_logs/`) — load only the current task's record if needed
- **All ADRs** (`docs/adr/`) — load only ADRs relevant to the current task domain
- **All experiments** (`experiments/phase0/`) — load only when debugging or referencing specific spike results
- **Full `docs/phase0/CODING_STYLE_BASELINE.md`** — load relevant sections per the applicability guide; do not paste the full document
- **Full `docs/phase0/BOUNDARY_CHECKLIST.md`** — run every 3 tasks, not in every session
- **External documents** — vendor docs, URLs, AI-generated summaries from prior sessions
- **Previous conversation logs** — start fresh; do not carry over full prior session context
- **The other tool's boot file** — Claude Code does not read AGENTS.md; Codex does not read CLAUDE.md

## 8. Do not modify by default

The following should not be modified unless the current task prompt explicitly allows it:

- **`docs/blueprint/`** — frozen architecture and spec documents
- **Completed task prompts** — historical record; do not rewrite after task completion
- **Historical closed task records** — evidence of past work; do not edit after task close / commit / merge
- **Unrelated ADRs** — only modify ADRs that the current task prompt explicitly lists
- **Production dependency / lock files** — `pyproject.toml`, `requirements.txt`, `uv.lock`, `package.json`, `pnpm-lock.yaml`
- **`app/`**, **`runtime/`**, **`gateway/`**, **`execution_fabric/`** — production code directories; Phase 0 tasks do not write here unless explicitly allowed
- **Generated artifacts / caches** — build outputs, `.pyc`, `__pycache__`, `node_modules`, `.venv`

## 9. Phase 0 status and pending stack

### Completed:

- P0-PREP-001 — Repository and Environment Readiness Check
- P0-PREP-002 — Phase 0 Docs Directory and Template Setup
- P0-PREP-003 — Freeze Spec v1.0.11 Placement Check
- P0-SPIKE-004 — Redis + ARQ Baseline Spike
- P0-SPIKE-005 — Target Business Systems API and Authentication Reconnaissance
- P0-SPIKE-006 — S3-compatible Object Storage Candidate Spike
- P0-RULES-001 — Align tool-specific rule loading
- P0-RULES-002 — Role-based guardrails and method selection
- P0-STYLE-001 — Coding style and quality baseline

### Current:

- P0-NAV-001 — Create repository context map, progressive loading guide, and Phase 0 task stack

### Next:

- Continue remaining Batch 1 spike tasks after P0-NAV-001 (P0-SPIKE-001, P0-SPIKE-002, P0-SPIKE-003, P0-SPIKE-007).
- After Batch 1 completion, open a P0-TEMPLATE-001-style task to upgrade Batch 2 / Phase 1 task templates with method_profile and BDD/TDD/PDR evidence rules.
- Then proceed to Batch 2 / Phase 1 tasks according to the upgraded templates.

### Deferred:

- During future persistence/database tasks, refine detailed PostgreSQL / schema / migration / ORM / index / audit / JSON / rollback conventions.
- After major stage milestones, consider running khazix-skills / neat-freak style cleanup in read-only / Plan mode as advisory knowledge consistency check.
- If Phase 0 status grows, split into PHASE0_STATUS_AND_NEXT_STEPS.md in a separate task.

## 10. Knowledge cleanup / handoff maintenance

neat-freak / khazix-skills style cleanup may be used at stage boundaries as an **advisory aid** for knowledge consistency checks. It is not part of the standard task execution flow.

**When to consider:**
- After completing a batch of tasks (e.g., after P0-NAV-001, after Batch 1, after Batch 2)
- Before entering Phase 1
- Before handoff to teammates or other agents

**Boundaries:**
- Must first run in read-only / Plan mode — inspect, do not auto-modify.
- Must not automatically modify files, commit, push, or merge.
- Must not overwrite Phase 0 rules, Task Records, Codex review results, or forbidden paths.
- Must not casually rewrite CLAUDE.md, AGENTS.md, docs/blueprint/, completed task prompts, historical closed task records, production code, or dependency / lock files.
- May suggest documentation updates, status updates, or todo-stack updates when appropriate — but suggestions require human approval before execution.
- Does not replace P0-NAV-001 or any Phase 0 task prompt.
- Not installed or run as part of this task.

## 11. Maintenance rules

- **Adding canonical docs:** Update MANIFEST.md with the new file path.
- **Adding task prompts:** Generate a corresponding Task Record in `docs/phase0/task_logs/`.
- **Out-of-band tasks:** State whether TASK_INDEX.md is updated or intentionally not updated (e.g., tasks with no rules/out-of-band section in TASK_INDEX).
- **Historical closed task records:** Do not rewrite after task close / commit / merge.
- **This file:** Keep navigation compact and factual. Update only when repository structure or canonical docs change. Do not add rules, policies, or new constraints here.
