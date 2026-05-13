# AI Agent Engineering Lessons — Phase 0 Reference

> **Loading policy:**
> - Do NOT load this full file by default.
> - Load only relevant sections based on current task type.
> - This is a lessons-learned reference, not a mandatory context file.
> - It does not replace task prompts, CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md, or REPOSITORY_CONTEXT_MAP.md.

**When to load sections from this file:**
- Creating or reviewing Task Records
- Handling changed_files / stat evidence
- Spike / provider API / Docker / Python runtime tasks
- Blocked / failed / rerun tasks
- Cleanup / index / manifest / handoff tasks
- Investigating Codex evidence-drift findings
- Stage-level /NEAT consistency audit

**When NOT to load:**
- Do not paste the entire file into every session.
- Do not load when the current task has no evidence, runtime, or documentation-sync dimension.
- This file belongs to Tier 2 (on-demand process references) per CONTEXT_LOADING_STRATEGY.md.

---

## 1. Task Record Evidence Rules

**changed_files must exactly match staged diff.**
Run `git diff --cached --name-only` after final staging; the Task Record `changed_files` list must match that output including order. Do not predict or assume file lists before staging.
Why: Early Task Records contained stale or predicted file lists that diverged from the actual staged diff, causing review failures.

**stat evidence must be fresh.**
Run `git diff --cached --stat` after final staging; record the output in the Task Record. Do not reuse an earlier stat snapshot.
Why: Stat evidence generated before a re-stage captures the wrong state and misleads reviewers.

**YAML safe_load + UniqueKeyLoader required.**
Plain `yaml.safe_load` silently accepts duplicate keys at the same indent level. Use a UniqueKeyLoader (dict subclass that raises on duplicate key) or equivalent check.
Why: Duplicate keys in Task Records were caught by Codex but not by naive YAML validation.

**not_applicable must have full metadata.**
Every `not_applicable` result must fill all six template fields: `not_applicable_reason`, `not_applicable_scope`, `blocked_by_task_id`, `activation_task_id`, `expiry_condition`, `evidence`. Empty strings or placeholders are invalid.
Why: Incomplete not_applicable entries hid real failures or deferred work.

**git_commit_sha uses deferred convention.**
Because committing a Task Record changes HEAD, do not embed the final SHA in the record. Use:
```yaml
git_commit_sha: "recorded_by_git_history_after_final_commit"
git_commit_sha_note: "The final commit SHA is not embedded in this Task Record because committing this file changes HEAD."
```
Why: Embedding SHA before commit creates a self-referential mismatch.

**No false package/archive claims.**
If no package was created, set `package_confirmation_status: "not_applicable"` with a meaningful description and scope. Do not write "fresh package created" when no packaging occurred.
Why: A prior Task Record falsely claimed a fresh package existed.

**Truthful status: blocked / failed / passed.**
A task that could not complete its acceptance criteria is `failed` or `blocked`, never `passed`. Do not use `not_applicable` to mask a failure.
Why: Masking failures with not_applicable delayed discovery of real blockers.

**Stale evidence cleanup.**
When a blocked/failed task is rerun and passes, remove or archive the stale blocked/failed YAML. The final state should have exactly one passing record (or the most recent status).
Why: Multiple conflicting Task Records for the same task_id created index confusion.

## 2. Git / Review Workflow Lessons

**Stage new files before requesting review.**
Untracked files do not appear in `git diff --cached`. New files must be `git add`-ed before generating changed_files or requesting a review.
Why: A review session saw an empty diff because newly created files were never staged.

**Intentional deletions must be documented.**
If a task deletes a file, the deletion must appear in changed_files and the Task Record must explain why.
Why: Undeclared deletions were flagged as evidence drift by Codex.

**Review before commit / push / merge.**
Stage → generate Task Record → review → human confirms → then commit. Never commit before review.
Why: Committing before review locked in stale evidence that could not be corrected without an amend.

**One task, one session preferred.**
Mixing multiple task_ids in a single session increases context pollution and evidence confusion.
Why: Cross-task context leaked into Task Records in early Phase 0 sessions.

## 3. Environment / Runtime Lessons

**Docker readiness check before compose operations.**
Verify Docker daemon is running (`docker info`) before executing `docker compose up`. In CI or fresh environments, the daemon may not be available.
Why: Spike tasks failed silently when Docker was not running, producing misleading "environment blocked" records.

**Python temp venv + PYTHONPYCACHEPREFIX.**
Use temporary virtual environments for spike experiments. Set `PYTHONPYCACHEPREFIX` to a temp directory to avoid polluting the repository with `__pycache__` directories.
Why: `__pycache__` directories appeared in staged diffs and had to be manually cleaned.

**Docker compose cleanup after experiments.**
Run `docker compose down -v` after spike experiments to remove containers, networks, and volumes. Orphaned containers consume resources and may conflict with future runs.
Why: Leftover containers from prior spikes caused port conflicts in subsequent experiments.

**Windows path escaping in YAML.**
On Windows, backslashes in paths must be escaped (`\\`) or replaced with forward slashes (`/`) in YAML files. Unescaped backslashes cause YAML parse errors or unexpected path resolution.
Why: Task Records with unescaped Windows paths failed YAML validation on CI.

## 4. Security / Secret Hygiene

**No real tokens, sessions, cookies, API keys, or full base URLs in committed files.**
Documentation may discuss these concepts using env var names or placeholders, but never commit real secret values.
Why: A spike test log inadvertently included a real session token that had to be scrubbed from history.

**Env var names must be labeled as such.**
When referencing `API_KEY`, `SESSION_TOKEN`, etc., add a note like "env var name only — not a real value" to prevent accidental secret scanning flags.
Why: Automated secret scanners flagged documentation that mentioned token field names without clarification.

**Sensitive value prohibition zones.**
The following must never contain real secret values: Trace persistence output, ResponseEnvelope JSON, mock adapter positive returns, task log / self-check log bodies, fixture expected-persisted output.
Why: These zones are read by downstream agents and may be committed to version control.

## 5. Provider API / Internal Validation Boundary

**public_vendor_api proves client-side compatibility only.**
Successfully calling a public provider API (e.g., OpenAI-compatible endpoint) proves the client library can send requests and parse responses. It does not prove the internal inference stack is validated.
Why: A spike report conflated public API success with internal vLLM/Qwen validation, which was factually incorrect.

**internal_endpoint_validation is deferred / not_executed.**
Unless intranet access is available and internal endpoints are explicitly tested, internal validation status must be `not_executed` or `deferred`.
Why: Claiming internal validation passed based on public API evidence was caught during Phase 0 audit.

**Never infer internal stack pass from public provider evidence.**
Public API compatibility and internal inference validation are separate evidence categories. Do not merge them.
Why: The distinction matters for Phase 1 baseline decisions where internal validation has different requirements.

## 6. Spike and Test Design Lessons

**No manual pre-setup hiding validation.**
If a test requires a running service, the test script must check for it and fail explicitly. Do not manually start a service, run the test, then report "passed" without noting the manual step.
Why: A spike appeared to pass because the operator manually pre-started the service; re-running in a clean environment revealed it was not self-contained.

**Assert, do not only print.**
Test scripts must use assertions (`assert`, `pytest`, or explicit exit codes). Print-only scripts that output "success" without verifying conditions produce false positives.
Why: A spike script printed "All tests passed" but had no assertions; Codex review flagged the output as unverifiable.

**Real schema validation required.**
When validating structured output, check actual field names, types, and required fields against the schema. Do not check only that the response is valid JSON.
Why: A model returned valid JSON with wrong field names, which passed a JSON-only check but failed schema validation.

**Enum validation.**
When output includes enum fields, validate against the actual enum definition, not just string type.
Why: A model returned a status string not in the allowed enum, which was caught only by enum-aware validation.

**Per-sample tool-calling scoring.**
For tool-calling evaluation, score each sample individually and report per-sample results. Aggregate pass rates alone hide failure patterns.
Why: An 80% aggregate pass rate masked that all failures were on the same tool-call pattern.

**Failure categories must reconcile.**
Provider failures, runtime failures, and model failures are distinct categories. A "provider timeout" is not a "model quality failure." Report them separately and ensure totals reconcile.
Why: A spike report lumped provider timeouts into model failures, inflating the model failure rate.

## 7. Documentation / Index Sync Lessons

**MANIFEST.md must register new canonical docs.**
When creating a new canonical document in `docs/dev/` or `docs/phase0/`, add its path to MANIFEST.md.
Why: Unregistered docs are invisible to new agents loading the execution pack.

**task_logs/INDEX.md must sync with actual YAML count.**
After creating a Task Record, add exactly one row to INDEX.md. Verify that the number of INDEX data rows equals the number of `docs/phase0/task_logs/*.yaml` files.
Why: INDEX rows and YAML file counts diverged when a Task Record was created but INDEX was not updated.

**REPOSITORY_CONTEXT_MAP.md updated when structure changes.**
New files in `docs/dev/` or `docs/phase0/` should get a navigation entry in the context map with appropriate load-when guidance.
Why: Agents could not find new reference documents because the context map did not list them.

**No invented paths.**
Every path mentioned in documentation must exist on disk. Do not reference files that were planned but not created.
Why: Codex review caught references to non-existent files that would cause agents to fail on load.

**No historical ADR / Task Record edits unless explicitly required.**
Closed ADRs and committed Task Records are historical evidence. Do not modify them unless a task prompt explicitly allows it.
Why: Early sessions accidentally edited historical records while working on unrelated tasks.

## 8. Scope / Context Hygiene Lessons

**Do not mix future tasks into the current task.**
Execute only the current task_id. Do not pre-implement features, create placeholder files, or generate prompts for future tasks.
Why: Scope creep into future tasks produced incomplete work that was later discarded.

**Progressive loading over full dump.**
Load only what the current step needs. Use REPOSITORY_CONTEXT_MAP.md and CONTEXT_LOADING_STRATEGY.md to decide what to load.
Why: Loading the full blueprint + all ADRs + all task records consumed context window and reduced reasoning quality.

**One task per session preferred.**
Starting a new session for each task_id reduces context carryover and evidence confusion.
Why: Multi-task sessions produced Task Records with evidence from the wrong task.

**This file is not default full load.**
AI_AGENT_ENGINEERING_LESSONS.md itself follows the same progressive-loading rule. Load specific sections on demand.
Why: Irony — a lessons file about context pollution should not become a source of context pollution.

## 9. Tool Assignment Lessons

**Task nature determines ownership.**
The `execution_owner` and `review_owner` fields should reflect what the task requires, not a blanket default.
Why: Assigning all tasks to one agent regardless of nature led to suboptimal execution.

**Claude Code / MiMo is better for:**
Documentation, environment setup, local interactive execution, repo navigation, prompt/template maintenance, cleanup.
Why: These tasks benefit from interactive file editing and local tool access.

**Codex is better for:**
Core code, schema, parser, validator, runtime, API contract, regression-sensitive logic, security-sensitive code.
Why: These tasks benefit from focused, structured coding with strong review isolation.

**Mixed ownership requires explicit assignment.**
When a task has both documentation and implementation dimensions, each dimension's owner must be declared. Do not use `mixed` to avoid specifying ownership.
Why: Ambiguous ownership led to both agents assuming the other would handle a subtask.

## 10. When to Update This File

Update this file when:
- Codex catches a recurring evidence or process issue across multiple tasks
- A blocked/failed rerun reveals a process flaw not yet documented here
- A stage-level /NEAT audit identifies a pattern of documentation drift
- A major phase handoff (e.g., Phase 0 to Phase 1) surfaces accumulated lessons

Do not update for:
- One-off typos or formatting issues (fix in place)
- Lessons already covered by existing sections
- Hypothetical future problems without real evidence
