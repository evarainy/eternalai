# Phase 1 Role Policy v3.0.0 — Codex-Claude V5

This file is the Phase 1 source of truth for model ownership, Review routing, human stops, and repository result contracts. Runtime choreography uses the Codex App native Goal, subagents, worktrees, Review, Git, and CI. No V4 command, custom lifecycle, or persistent run contract is required by this repository.

## Authority and roles

Authority, highest first: the user's latest explicit instruction in the current native Goal and applicable `AGENTS.md`; approved product/architecture/interface/milestone documents; current Goal Outcome/Constraints/Verification; repository code/tests/CI facts; then Worker Contracts and advisory material. Historical prompts and records retain their original versioned meaning and never manufacture current authorization.

- `codex_root` — Sol X-High Goal coordinator: owns Goal context, risk/Q classification, lane boundaries, Candidate ownership, evidence binding, Review routing, Git/CI decisions, and auto-next.
- `codex_worker` — Luna, Terra, or Sol implementation/research worker within one explicit Goal and single Scope. Luna/Terra write results are always provisional.
- `candidate_owner` — Sol High or Sol X-High owner of one coherent candidate: reads the actual complete diff, repairs quality issues, runs formal verification, and binds the candidate. Critical control-plane work uses Sol X-High.
- `codex_review` — native read-only Review with the fixed Sol Review profile. It reports findings and never edits the candidate.
- `claude_opus` — bounded read-only plan/final reviewer for Q2/Q3: Opus 4.8 High for Q2 and Opus 4.8 X-High for Q3.
- `human` — decides redlines, expansion, architecture/framework direction, major unresolved choices, and batch/milestone acceptance. Human acceptance never replaces verification or Review.

Review cannot approve its own candidate. A Worker summary, Candidate Owner self-check, or passing test is not independent Review. The Candidate Owner, original writer, and weak-model worker cannot approve their final delivery.

## Three separate decisions

### Risk and owner strength

`risk_tier` describes the real change surface and can only stay equal or increase automatically. It selects the minimum owner/model and contributes to Q classification; it does not create a waiting human Gate.

- Low: mechanical, deterministic, no semantic behavior change.
- Medium: ordinary local code or behavior with bounded impact.
- High: runtime core, public contracts, persistence, cross-module work, important CI, security, state machines, concurrency, transactions, idempotency, compensation, credentials, isolation, version locking, fallback, migration, control plane, or release paths.

Ordinary runtime core uses Sol High. Security, state machines, concurrency, transactions, idempotency, compensation, credentials, isolation, version locking, fallback, migration, control-plane switching, and release-critical paths use Sol X-High. Risk never silently downgrades to a weaker model.

### Q0-Q3 Review routing

| Tier | Typical surface | Plan Review | Final candidate Review |
| --- | --- | --- | --- |
| `Q0` | Pure mechanical deterministic generation; no semantic document or metadata change | none | deterministic checks only; no model Review |
| `Q1` | Ordinary local code or low-risk behavior correction | none | native Codex Review with the fixed Sol Review profile |
| `Q2` | Ordinary runtime core, public interfaces, persistence, cross-module work, important CI | Claude Opus 4.8 High | native Codex Review + Claude Opus 4.8 High |
| `Q3` | Architecture, security, state machine, concurrency, transaction, idempotency, compensation, credentials, isolation, version locking, fallback, migration, control plane, or release | Claude Opus 4.8 X-High | native Codex Review + Claude Opus 4.8 X-High |

`X-High` means the `xhigh` effort level, never `max`. The Codex Review profile is fixed globally and must be mechanically verified at the acceptance/cutover boundary; Q routing does not pretend to switch Codex Review effort per invocation.

Q2/Q3 plan Review occurs after a meaningful execution plan exists and before writes. It is not repeated for ordinary sequencing changes that preserve architecture, scope, risk, and approved design. A task whose already approved design/implementation plan explicitly waives a duplicate construction-time plan Review keeps the required final Q Review.

Ordinary V5 Review binds only an immutable `base_sha + head_sha` commit candidate. Exact pre-commit staged identity is a one-time exception solely for the last V4-governed migration task `P1-WORKFLOW-V5-001`; no other V5 task may use it. Candidate changes invalidate prior Review and require affected verification and Review again. Findings are repaired by an appropriate Sol owner; Review remains read-only.

### Human stops

Risk and Q level do not create a human stop. Waiting is required only for:

- deletion of files/directories/Git history, rebase, reset-hard, force push, or bypassing hooks/protection;
- `.env`, credentials, secrets, tokens, database schema/real-data migration, global/system changes, public release, or production deployment;
- scope/repository/product expansion;
- new or changed core architecture, framework, protocol, public contract, trust boundary, or semantic invariant;
- a major unresolved choice that materially changes cost, compatibility, risk, or product behavior;
- stricter target repository Gates;
- batch or milestone acceptance.

Legacy `controller_risk_tier`, `automation_class`, `authorization_mode`, `required_stops`, and `r3_authorization` remain valid only when interpreting a V4 descriptor/record. An `R3` or `Q3` label is not authorization. Exact redline approval is always action-specific.

## Native Goal, lanes, and auto-next

One native Goal may execute multiple task IDs and auto-next after dependencies, verification, required Review, integration policy, and result stops are satisfied. Each write lane has one explicit Goal and a single Scope, uses an isolated worktree/branch, and returns compact evidence. A new scope opens a new Worker Contract/lane; subagents cannot create subagents.

At most two write lanes run concurrently. When a base advances, the later candidate must be reformed on the new base and rerun affected verification and Review. Scope expansion, dependency failure, Review failure, validation/CI failure, or a human stop blocks auto-next; ordinary completion does not wait for a personal per-task acceptance Gate.

## Git, integration, and CI

There is no personal Gate for a local commit, ordinary non-force push, normal merge, CI/CD configuration changes, or CI runs. These actions still require current Goal authorization, exact scope/staging, fresh validation and required Review, valid authentication/mergeability, target repository branch protection, and all required checks final and green.

Never use force, rebase, reset-hard, `--no-verify`, hook/protection bypass, ref deletion, or stale Review. A non-triggered workflow is `not_triggered`, never green. Post-merge checks bind the actual merge SHA. Historical Gate 2 is result acceptance only and never Git/CI authorization.

## Evidence and completion

Candidate identity for every ordinary V5 task is only an immutable commit pair: `base_sha + head_sha`. Solely for `P1-WORKFLOW-V5-001`, the last V4 migration task, pre-commit Review instead binds `reviewed_parent_sha + staged_tree_oid + complete raw staged diff + exact paths + freshness`; the later commit must mechanically preserve that parent/tree/diff. This exception does not survive into V5.

A task is complete only when its Outcome/Done when, formal verification, required Review, candidate identity, integration policy, and disclosed-risk requirements are satisfied. Each completed task inside a V5 Goal writes its own Chinese result summary of at most 20 lines plus repository-external Candidate Manifest/Recovery Index/Review evidence; one Goal-wide summary cannot replace the per-task summaries. V5 tasks do not generate V4 long descriptors or full Task Records.

## V4 legacy interpretation

The V4 `TASK_PROMPT_TEMPLATE.md`, `task_record_schema.yaml`, existing task descriptors/records, and the final `P1-WORKFLOW-V5-001` migration evidence remain historical and are interpreted under their original version. They are not backfilled and are not V5 runtime dependencies.

| Historical value | Interpret as |
| --- | --- |
| `claude_code_mimo` | `claude_code` |
| `codex_review` | `independent_review` or `self_review` by artifact context |
| `self_check` | `self_review` |
| `human_optional` | `human_review` only if a review actually occurred, otherwise `none` |

On conflict, current Goal authority and this policy win for new work; historical evidence retains its original semantics.
