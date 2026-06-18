# P0-DOMAIN-007c - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- app/runtime/runtime.py (RuntimeImpl — the main chain this task wires up; read in full)
- app/infra/gateway/capability_gateway.py (CapabilityGateway — the gateway this task fixes; read in full)
- app/infra/sdui/response_envelope_builder.py (ResponseEnvelopeBuilder — read: understand build_message / build_operator_handback / build_binding_required / build_failed / build_confirm_card)
- app/ports/trace.py (TracePort, TraceEvent, TraceEventType — read: verify discrete event_type Literal values before coding)
- app/ports/capability_gateway.py (CapabilityGatewayPort, ExecutionResult, ExecutionStatus, ErrorCode, RequestOrgContext)
- app/ports/runtime.py (RuntimePort — the frozen port contract; must not be changed without explicit human approval)
- app/ports/response_envelope.py (ResponseEnvelope, ResponseEnvelopeStatus, UIComponent, ConfirmCard, OperatorHandbackCard, BindingRequiredCard)
- app/contracts/sdui/models.py (authority for UIComponent / OperatorHandbackCard / BindingRequiredCard / ConfirmCard action + component_type Literals; verify-target for response-builder fixes)
- tests/golden_tasks/fixtures/GT-001.json (read: understand exact event_sequence + message_contains assertions)
- tests/golden_tasks/fixtures/GT-008.json (read: no_capability_found trace + envelope assertions)
- tests/golden_tasks/fixtures/GT-009.json (read: policy_denied short-circuit trace sequence)
- tests/golden_tasks/fixtures/GT-010.json (read: identity_unbound trace + ui.action=bind_required assertions)
- tests/golden_tasks/fixtures/GT-012.json (read: needs_binding_scope + ui.action=clarify_scope assertions)
- docs/phase0/tasks/P0-DOMAIN-007b.md (the skeleton this task completes — mirror its section structure)
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md: read ONLY targeted sections:
  - §5.1 (lines 263–276): Phase 0 conclusions required before Phase 1 spec
  - §5.2 (lines 279–288): Phase 1 spec prerequisite gate — the hard pass condition this task targets
  - §6.1 (lines 309–320): Runtime Phase 0 scope
  - §6.2 (lines 329–342): Capability Gateway Phase 0 scope — full call chain requirement
  - §12.4.1 (lines 3479–3492): short-circuit terminal-state Trace event matrix — the canonical source of truth for must-have / must-not-have events
  - §13.4 (lines 3808–3824): forbidden behaviors

## Source spec paths

| Section | Lines | Authority |
|---|---|---|
| §5.2 Phase 1 spec prerequisite gate | 279–288 | Hard pass condition for this task (positive ≥80%, negative/boundary/security 100%) |
| §6.1 Runtime Phase 0 scope | 309–320 | Forbids complex Planner / Dynamic Tool Composition |
| §6.2 Gateway Phase 0 scope | 329–342 | Requires full call chain: Registry read + Policy Guard + Trace Pre/Post + Mock Adapter + result |
| §12.4.1 terminal-state matrix | 3479–3492 | Canonical source of must-have / must-not-have events per terminal state |
| §13.4 forbidden behaviors | 3808–3824 | Forbids modifying frozen blueprint, expanding Phase 1 matrix, execution_fabric import |

## Global hard rules

- Execute only this task_id.
- Branch from phase0/P0-GT-002 @ d804611 (NOT phase0/main) — the golden-task runner is verified only on that branch.
- Start only after all depends_on tasks have been reviewed, approved, and merged to the Phase 0 base branch.
- Output a Plan first and wait for human confirmation before modifying files.
- Do not modify frozen blueprint files.
- Do not implement Phase 1 features (§6.1: no complex Planner, no Dynamic Tool Composition).
- Do not add unapproved dependencies.
- Do not weaken tests to pass.
- **ANTI-CHEAT (mandatory)**: the golden-task runner (`scripts/run_golden_tasks.py`) and all files under `tests/golden_tasks/` (fixtures + runner support) are the FROZEN judge. Make GTs pass by fixing `app/runtime/`, `app/infra/gateway/`, and `app/infra/sdui/` ONLY. Forbidden to weaken/modify the runner, edit fixtures, or fake responses. Violation = immediate stop.
- Stop after Unified Task Record and wait for human confirmation.
- No commit, no push, no merge.
- Any execution/pass claim must include exact command, exit code, and evidence output in the Task Record.
- Independent staged review is required before any commit, push, or merge.

## Task YAML

```yaml
task_id: P0-DOMAIN-007c
branch: "phase0/P0-DOMAIN-007c"
base_commit: "phase0/P0-GT-002 @ d804611"
title: "Runtime/Gateway Main-Chain Trace & Response Integration"
type: implementation
priority: P0
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

depends_on:
  - P0-GT-002        # golden-task runner (the integration verifier; lives on the base branch)
  - P0-DOMAIN-007b   # Runtime main chain minimal skeleton
  - P0-DOMAIN-003b1  # CapabilityRegistry implementation
  - P0-DOMAIN-003b2  # CapabilityGateway skeleton
  - P0-DOMAIN-008b   # PolicyGuard implementation (or relevant 008x)
  - P0-DOMAIN-008c
  - P0-DOMAIN-008d
  - P0-DOMAIN-008e
  - P0-DOMAIN-005b   # IdentityMapping implementation
  - P0-DOMAIN-009b   # TracePort implementation

method_profile:
  execution_role: "execution"
  execution_owner: "codex"
  review_owner: "separate_session"
  review_mode: "codex_review"
  method: "TDD"
  reason_for_owner_choice: >
    This is the highest-risk integration seam in Phase 0: it wires Runtime →
    CapabilityGateway trace threading, discrete gateway trace events, and
    fixture-asserted response content. All three failure categories (trace
    threading, discrete events, response content) are regression-sensitive and
    require test-first proof before the frozen golden-task runner is re-run.
    Codex executes TDD; independent review happens in a separate session.

base_commit_note: >
  Branch from phase0/P0-GT-002 @ d804611, NOT phase0/main. The golden-task
  runner (scripts/run_golden_tasks.py, tests/golden_tasks/) lives on the
  unmerged GT-002 branch and is the integration verifier. The runner is FROZEN
  — do not modify the runner or any fixture to make GTs pass.

objective: >
  Add the discrete TraceEvent record_step emissions missing from
  CapabilityGateway, fix response content, and fix UI card construction so
  that the golden-task runner re-run yields positive_passed ≥ 6/7 (≥80%) AND
  negative_passed = 4/4 (100%), satisfying the §5.2 Phase 1 spec prerequisite
  gate. Concretely:
    1. DISCRETE GATEWAY EVENTS: capability_gateway.py currently has ZERO
       record_step calls. Add all discrete record_step emissions that §12.4.1
       requires at each short-circuit point: `identity_check`, `policy_checked`,
       `blocked_by_policy`, `blocked_by_identity`, `confirm_required`,
       `adapter_called`, `adapter_error_mapped` (the §12.4.1 adapter_timeout
       terminal event — requires adding `adapter_error_mapped` to the
       TraceEventType Literal, see approved touched_paths entry). Use only
       event_type values that exist in the TraceEventType Literal in
       app/ports/trace.py after the approved addition.
    2. GATEWAY PRE/POST RELOCATION: gateway_pre_recorded and
       gateway_post_recorded are currently emitted by the runtime
       (runtime.py:140-147 and 157-165). They must be REMOVED from the runtime
       and emitted via record_step INSIDE the gateway, immediately before/after
       the adapter call, because §6.2 places Trace Pre/Post-Record in the
       gateway chain and the GT sequence assertions are order-sensitive.
    3. RESPONSE CONTENT: replace the generic "操作完成" response with
       capability-specific content sourced from adapter output data; replace
       "暂未找到匹配的能力" with "暂未接入" + operator_handback_card for the
       no_capability_found path; fix ui.action values to match fixture assertions.
    4. BINDING REQUIRED UI: distinguish identity_unbound (ui.action=bind_required,
       OperatorHandbackCard with action=bind_required, target_system populated)
       from needs_binding_scope (ui.action=clarify_scope, OperatorHandbackCard);
       fix GT-010 and GT-012.

per_gt_targets:
  GT-001:
    category: positive
    target: "response message_contains ['OA','待办']; trace includes identity_check and policy_checked"
    currently_failing_because: "gateway does not emit the discrete record_step events (identity_check, policy_checked) that the shared SpyTracePort captures; record_gateway_call and finalize_task_trace are spy no-ops and invisible to the judge"
  GT-002:
    category: positive
    target: "response message_contains ['OA-WF-2026-0001']; adapter output data surfaced in message"
    currently_failing_because: "Runtime returns generic '操作完成' instead of capability-specific adapter output"
  GT-003:
    category: positive
    target: "status=completed; adapter actually called; response message_contains ['U8-AP-2026-0033','posted']"
    currently_failing_because: "status=blocked — RuntimeImpl builds RequestOrgContext with account_set_id=None (runtime.py:139); FakeIdentityMapping returns needs_binding_scope when a target_system has >1 active binding and the context has no account_set_id. GT-003 has two active u8 bindings and account_set_id only inside when.arguments. Fix: RuntimeImpl must inject account_set_id (and resource_scope if present) from CapabilityRef.arguments into RequestOrgContext so GT-003 resolves identity and reaches the adapter."
  GT-004:
    category: positive
    target: "response message_contains ['供应商']; trace includes policy_checked"
    currently_failing_because: "gateway does not emit the discrete record_step events (policy_checked) that the shared SpyTracePort captures; record_gateway_call and finalize_task_trace are spy no-ops and invisible to the judge"
  GT-005:
    category: positive
    target: "response message_contains ['CAM-A-001']; adapter output data surfaced"
    currently_failing_because: "generic response content, missing adapter data in message"
  GT-006:
    category: positive
    target: "status=waiting_user; response message_contains ['确认','提交']; ui.component_type=confirm_card; ui.target_system=oa; trace terminal=confirm_required; identity_check present"
    currently_failing_because: "confirm_required event not emitted; identity_check missing from Runtime trace; build_confirm_card currently has no target_system param"
  GT-007:
    category: positive
    target: "status=completed; response message_contains ['已提交','OA-DRAFT-2026-0009']"
    special_handling: >
      GT-007 is the one-shot confirm CONTINUATION. The runner feeds it via the
      normal handle_user_message(message=...) path (test_golden_tasks.py:605-612,
      651-668); given.user_action/prior_response_id are NOT consumed by the call
      path. A RuntimePort change is a remote contingency, almost certainly NOT
      needed — the expected path is message-based. app/ports/runtime.py remains
      a STOP-AND-ASK conditional touched path, but the executor should implement
      GT-007 entirely within app/runtime/runtime.py first. See GT-007 section below.
  GT-008:
    category: negative
    target: "status=no_capability_found; message_contains ['暂未接入','能力']; ui.component_type=operator_handback_card; ui.action=none"
    currently_failing_because: "Runtime returns '暂未找到匹配的能力' and plain build_message, not operator_handback_card"
  GT-009:
    category: negative
    target: "status=blocked; message_contains ['无权限','拒绝']; ui.action=none; trace terminal blocked_by_policy includes identity_check; event_sequence ends with task_failed"
    currently_failing_because: "blocked_by_policy not emitted; identity_check missing; task_failed terminal event not emitted after response_envelope_created"
  GT-010:
    category: negative
    target: "status=blocked; ui.action=bind_required; ui.component_type=operator_handback_card; ui.target_system=oa"
    currently_failing_because: "Runtime builds wrong card: build_binding_required yields component_type=binding_required_card (models.py:67-69) but GT-010 asserts component_type=operator_handback_card. Fix: produce an OperatorHandbackCard with action='bind_required' + target_system (OperatorHandbackCard.action Literal allows bind_required per models.py:62-64). Do NOT use build_binding_required for this path."
  GT-012:
    category: negative
    target: "status=blocked; message_contains ['账套','选择']; ui.action=clarify_scope; ui.target_system=u8"
    currently_failing_because: "needs_binding_scope mapped same as identity_unbound; blocked_by_identity + identity_check not emitted"

constraints_to_carry_forward:
  - "app/ports/runtime.py handle_user_message signature is frozen; do NOT change it without explicit human approval (see GT-007 section)."
  - "app/ports/trace.py TraceEventType Literal: the ONLY permitted edit is the approved addition of `adapter_error_mapped` (human-approved 2026-06-11) to align with spec §12.4.1 line 3490. No other app/ports/trace.py change without approval."
  - "The gateway trace context must be the SAME trace_id started by Runtime — already threaded via RequestOrgContext.request_id (runtime.py:139 → gateway:77). The runner also already injects the shared SpyTracePort into the gateway (test_golden_tasks.py:598). The real gap is that capability_gateway.py has ZERO record_step calls — fix by ADDING record_step emissions."
  - "Discrete gateway events must be recorded via trace_port.record_step(); do NOT call finalize_task_trace() at short-circuit points — only at the true terminal step. record_gateway_call is a spy no-op and invisible to the judge."
  - "gateway_pre_recorded and gateway_post_recorded must be emitted via record_step INSIDE the gateway (immediately before/after the adapter call). Remove them from the runtime (runtime.py:140-147 and 157-165) to fix order and GT-006/GT-009 must-not-have constraints."
  - "identity_unbound path (GT-010): produce OperatorHandbackCard with action='bind_required' + target_system. Do NOT use build_binding_required — it yields component_type=binding_required_card, but GT-010 asserts component_type=operator_handback_card."
  - "needs_binding_scope path (GT-003/GT-012): resolve identity by injecting account_set_id (and resource_scope if present) from CapabilityRef.arguments into RequestOrgContext. For the response: use build_operator_handback with action=clarify_scope + target_system; must NOT produce ui.action=bind_required."
  - "no_capability_found path: message must contain '暂未接入'; produce operator_handback_card with ui.action=none and envelope status=no_capability_found. Use a dedicated builder method — do NOT use build_operator_handback directly (it hardcodes action=clarify_scope and status=blocked)."
  - "policy_denied path: message must contain '无权限' and '拒绝'; ui.action=none. Use a dedicated builder variant — do NOT use build_operator_handback directly (it hardcodes action=clarify_scope). Do NOT attempt payload-based action override (payload lands in ui.payload, not ui.action)."
  - "completed path: message must be sourced from adapter output data fields (e.g. format a summary from exec_result.data); not a hardcoded generic string."
  - "No new Python dependencies; no pyproject.toml / uv.lock changes; no __init__.py files."
  - "No plaintext credential/token/password/cookie/sessionid/access_token/refresh_token in trace attributes, ResponseEnvelope, fixtures, logs, or Task Record evidence."
  - "Phase 1 user-value boundary preserved: no real LLM calls, no real system calls, no complex planner, no direct Adapter imports in Runtime."
  - "RuntimeImpl must not import from app/execution_fabric/."

deliverables:
  - app/runtime/runtime.py       # trace threading + response content fix
  - app/infra/gateway/capability_gateway.py  # discrete event emission
  - app/infra/sdui/response_envelope_builder.py  # REQUIRED: (1) operator_handback with action=none (GT-008/009); (2) handback card target_system population (GT-010/012); (3) bind_required operator_handback (GT-010); (4) confirm card target_system param (GT-006)
  - tests/runtime/test_runtime_trace_threading.py   # new: proves Runtime→Gateway trace_id threading
  - tests/runtime/test_runtime_response_content.py  # new: proves capability-specific message content
  - tests/infra/gateway/test_gateway_discrete_events.py  # new: proves each discrete TraceEvent
  - tests/infra/gateway/test_gateway_short_circuit.py    # new or update: identity/policy short-circuit paths

acceptance_criteria:
  - criterion: "Golden-task runner minimum bar: uv run python scripts/run_golden_tasks.py --summary yields positive_passed >= 1 AND negative_passed >= 1 (flips GT-002 happy-minimum green)"
    result: "pending"
    evidence: ""
  - criterion: "Golden-task runner §5.2 gate: positive_passed >= 6 (of 7, ≥80%) AND negative_passed = 4 (of 4, 100%)"
    result: "pending"
    evidence: ""
  - criterion: "GT-001: response message contains 'OA'; trace event_sequence includes identity_check and policy_checked"
    result: "pending"
    evidence: ""
  - criterion: "GT-002: response message contains 'OA-WF-2026-0001' (sourced from adapter data)"
    result: "pending"
    evidence: ""
  - criterion: "GT-003: status=completed; adapter actually called; response message contains 'U8-AP-2026-0033'"
    result: "pending"
    evidence: ""
  - criterion: "GT-004: response message contains '供应商'; trace includes policy_checked"
    result: "pending"
    evidence: ""
  - criterion: "GT-005: response message contains 'CAM-A-001'"
    result: "pending"
    evidence: ""
  - criterion: "GT-006: status=waiting_user; message contains ['确认','提交']; ui.component_type=confirm_card; ui.target_system=oa; trace terminal confirm_required; trace includes identity_check"
    result: "pending"
    evidence: ""
  - criterion: "GT-008: status=no_capability_found; message contains '暂未接入'; ui.component_type=operator_handback_card; ui.action=none"
    result: "pending"
    evidence: ""
  - criterion: "GT-009: status=blocked; ui.action=none; trace includes blocked_by_policy and identity_check; event_sequence ends with task_failed; adapter NOT called"
    result: "pending"
    evidence: ""
  - criterion: "GT-010: status=blocked; ui.action=bind_required; ui.component_type=operator_handback_card (NOT binding_required_card); ui.target_system=oa; adapter NOT called"
    result: "pending"
    evidence: ""
  - criterion: "GT-012: status=blocked; message contains ['账套','选择']; ui.action=clarify_scope; trace includes blocked_by_identity and identity_check"
    result: "pending"
    evidence: ""
  - criterion: "New unit tests: test_gateway_discrete_events.py passes — each discrete TraceEventType emitted at the correct gateway short-circuit point"
    result: "pending"
    evidence: ""
  - criterion: "New unit tests: test_runtime_trace_threading.py passes — Runtime and Gateway share the same trace_id; gateway events appear in the runtime trace"
    result: "pending"
    evidence: ""
  - criterion: "app/ports/trace.py TraceEventType includes adapter_error_mapped (approved addition, 2026-06-11); GT-001/GT-005/GT-007 injection-companion adapter_timeout matrix passes"
    result: "pending"
    evidence: ""
  - criterion: "Existing tests/runtime/, tests/architecture/ stay green (no regression)"
    result: "pending"
    evidence: ""
  - criterion: "RuntimeImpl does not import any symbol from app/execution_fabric/"
    result: "pending"
    evidence: ""
  - criterion: "All depends_on Task Records exist as passed"
    result: "pending"
    evidence: ""

failure_examples:
  - name: anti_cheat_runner_modification
    trigger: "any file under tests/golden_tasks/ is modified"
    expected_result: "immediate stop; task is failed"
    forbidden_shortcut: "Forbidden to modify runner or fixtures to make GTs pass regardless of reason"
  - name: generic_completed_message
    trigger: "exec_result.status=completed"
    expected_result: "message is sourced from exec_result.data fields (e.g. workflow list summary, document status)"
    forbidden_shortcut: "Forbidden to return hardcoded '操作完成' for completed path"
  - name: no_capability_found_wrong_message_or_ui
    trigger: "parse_to_schema returns no match OR gateway returns no_capability_found"
    expected_result: "message contains '暂未接入'; ui.component_type=operator_handback_card; ui.action=none"
    forbidden_shortcut: "Forbidden to return '暂未找到匹配的能力' or plain build_message (component_type=none)"
  - name: identity_unbound_wrong_ui
    trigger: "Gateway returns ExecutionResult(status='binding_required', error_code='identity_unbound')"
    expected_result: "OperatorHandbackCard with action='bind_required' + target_system populated (component_type=operator_handback_card); GT-010 asserts operator_handback_card NOT binding_required_card"
    forbidden_shortcut: "Forbidden to use build_binding_required → BindingRequiredCard (produces component_type=binding_required_card) for GT-010 identity_unbound path. Also forbidden to produce component_type=binding_required_card for any GT-010 assertion."
  - name: needs_binding_scope_wrong_ui
    trigger: "Gateway returns ExecutionResult(status='binding_required', error_code='needs_binding_scope')"
    expected_result: "build_operator_handback; ui.action=clarify_scope; message contains '账套' and '选择'"
    forbidden_shortcut: "Forbidden to call build_binding_required (produces bind_required) for needs_binding_scope"
  - name: policy_denied_wrong_ui_action
    trigger: "Gateway returns ExecutionResult(status='denied', error_code='policy_denied')"
    expected_result: "ui.action=none (not clarify_scope); message contains '无权限' and '拒绝'"
    forbidden_shortcut: "Forbidden to use default build_operator_handback action=clarify_scope for policy_denied; override or use a dedicated builder variant"
  - name: gateway_trace_not_threaded
    trigger: "RuntimeImpl calls gateway.execute_capability without passing the Runtime trace_id"
    expected_result: "golden-task trace event_sequence missing identity_check, policy_checked, adapter_called"
    forbidden_shortcut: "Forbidden to leave gateway operating on its own disconnected trace_id"
  - name: discrete_event_wrong_type
    trigger: "Gateway emits event_type not in TraceEventType Literal"
    expected_result: "ValidationError at TraceEvent construction; test fails"
    forbidden_shortcut: "Forbidden to invent new event_type strings; only use values from app/ports/trace.py TraceEventType. Note: after the approved addition, the Literal includes `adapter_error_mapped` — use it for the §12.4.1 adapter_timeout/error-mapping terminal event. `adapter_error` remains available for generic adapter errors."
  - name: finalize_at_short_circuit
    trigger: "Gateway calls trace_port.finalize_task_trace() at a short-circuit point (identity, policy, confirm)"
    expected_result: "only record_step() at short-circuit; finalize only at the true terminal event"
    forbidden_shortcut: "Forbidden to call finalize_task_trace at every early return — this corrupts the trace sequence"
  - name: ports_runtime_changed_without_approval
    trigger: "app/ports/runtime.py is modified"
    expected_result: "stop and ask human; this is a STOP-AND-ASK condition"
    forbidden_shortcut: "Forbidden to change the RuntimePort signature without explicit human approval"

step_verification_points:
  - step: "Preflight: verify branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git diff --name-only; git diff --cached --name-only"
    evidence: ""
  - step: "Verify base is phase0/P0-GT-002 @ d804611"
    result: "pending"
    command: "git log --oneline -5"
    evidence: "Expected: d804611 in recent ancestry"
  - step: "Verify all depends_on passed Task Records exist"
    result: "pending"
    command: "$tasks = @('P0-GT-002','P0-DOMAIN-007b','P0-DOMAIN-003b1','P0-DOMAIN-003b2','P0-DOMAIN-008b','P0-DOMAIN-008c','P0-DOMAIN-008d','P0-DOMAIN-008e','P0-DOMAIN-005b','P0-DOMAIN-009b'); $missing = @(); foreach ($tid in $tasks) { if (-not (Get-ChildItem docs/phase0/task_logs/${tid}_*_passed.yaml -ErrorAction SilentlyContinue | Select-Object -First 1)) { $missing += $tid } }; if ($missing.Count -gt 0) { throw \"Missing depends_on Task Record(s): $($missing -join ', ')\" } else { 'PASSED' }"
    evidence: ""
  - step: "Run golden-task runner baseline (before any edits)"
    result: "pending"
    command: "uv run python scripts/run_golden_tasks.py --summary 2>&1 | tail -20"
    evidence: "Document current positive_passed and negative_passed counts as baseline"
  - step: "Write failing tests first (TDD red phase) — trace threading"
    result: "pending"
    command: "uv run pytest tests/runtime/test_runtime_trace_threading.py -v 2>&1 | tail -20"
    evidence: "Expected: non-zero exit (tests exist but implementation not yet fixed)"
  - step: "Write failing tests first (TDD red phase) — gateway discrete events"
    result: "pending"
    command: "uv run pytest tests/infra/gateway/test_gateway_discrete_events.py -v 2>&1 | tail -20"
    evidence: "Expected: non-zero exit (tests exist but gateway not yet emitting events)"
  - step: "Add approved adapter_error_mapped to TraceEventType Literal in app/ports/trace.py"
    result: "pending"
    command: "uv run python -c \"from app.ports.trace import TraceEventType; print('adapter_error_mapped' in TraceEventType.__args__)\""
    evidence: "Expected: True"
  - step: "Implement gateway record_step emissions to make trace threading tests pass (trace_id already threaded via RequestOrgContext; runner already wires shared SpyTracePort into gateway)"
    result: "pending"
    command: "uv run pytest tests/runtime/test_runtime_trace_threading.py -v"
    evidence: ""
  - step: "Implement discrete gateway trace events (identity_check, policy_checked, blocked_by_policy, blocked_by_identity, confirm_required, adapter_called, adapter_error_mapped) + relocate gateway_pre/post_recorded from runtime into gateway"
    result: "pending"
    command: "uv run pytest tests/infra/gateway/test_gateway_discrete_events.py -v"
    evidence: ""
  - step: "Fix no_capability_found response content and UI (暂未接入 + operator_handback_card)"
    result: "pending"
    command: "uv run pytest tests/runtime/ -v -k 'no_capability_found' 2>&1 | tail -20"
    evidence: ""
  - step: "Fix completed response content (source message from exec_result.data)"
    result: "pending"
    command: "uv run pytest tests/runtime/test_runtime_response_content.py -v"
    evidence: ""
  - step: "Fix identity_unbound vs needs_binding_scope UI distinction"
    result: "pending"
    command: "uv run pytest tests/infra/gateway/test_gateway_short_circuit.py -v 2>&1 | tail -20"
    evidence: ""
  - step: "Run full golden-task runner after all fixes"
    result: "pending"
    command: "uv run python scripts/run_golden_tasks.py --summary 2>&1 | tail -30"
    evidence: "Must show positive_passed >= 6 AND negative_passed = 4"
  - step: "Run full test suite — no regressions"
    result: "pending"
    command: "uv run pytest -v 2>&1 | tail -30"
    evidence: "All pre-existing tests must remain green"
  - step: "Run architecture tests — import boundary still clean"
    result: "pending"
    command: "uv run pytest tests/architecture/ -v"
    evidence: ""
  - step: "Run lint and type checks on touched files"
    result: "pending"
    command: "uv run ruff check app/runtime/ app/infra/gateway/ app/infra/sdui/ app/ports/trace.py tests/runtime/ tests/infra/gateway/; uv run mypy app/runtime/ app/infra/gateway/ app/infra/sdui/ app/ports/trace.py"
    evidence: ""
  - step: "Verify RuntimeImpl does not import from app/execution_fabric/"
    result: "pending"
    command: "$hits = Get-ChildItem app/runtime/ -Filter '*.py' -Recurse | Select-String -Pattern 'from app\\.execution_fabric|import app\\.execution_fabric' -Quiet; if ($hits) { throw 'execution_fabric import detected in app/runtime/' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify tests/golden_tasks/ was NOT modified"
    result: "pending"
    command: "$changed = git diff --cached --name-only; $hits = $changed | Where-Object { $_ -like 'tests/golden_tasks/*' }; if ($hits) { $hits; throw 'ANTI-CHEAT: tests/golden_tasks/ is in staged diff — forbidden' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify app/ports/ changes are only approved edits (trace.py adapter_error_mapped addition; runtime.py only if GT-007 approved)"
    result: "pending"
    command: "$changed = git diff --cached --name-only; $hits = $changed | Where-Object { $_ -like 'app/ports/*' }; if ($hits) { $unapproved = $hits | Where-Object { $_ -ne 'app/ports/trace.py' }; if ($unapproved) { $unapproved; throw 'Unapproved app/ports/ change in staged diff — stop and ask human' } else { 'PASSED: only approved trace.py edit' } } else { 'PASSED' }"
    evidence: ""
  - step: "Verify no __init__.py files created in new directories"
    result: "pending"
    command: "$dirs = @('tests/runtime','tests/infra/gateway'); $hits = foreach ($d in $dirs) { if (Test-Path $d) { Get-ChildItem $d -Filter '__init__.py' -Recurse -ErrorAction SilentlyContinue } }; if ($hits) { $hits | ForEach-Object { $_.FullName }; throw '__init__.py detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Staged secret scan"
    result: "pending"
    command: "$secretPattern = '(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|authorization|bearer|cookie|session[_-]?id|dsn|connection[_-]?string)\\s*[:=]\\s*[\"'']?[^\"''\\s]{6,}'; $hits = git diff --cached -U0 | Select-String -Pattern $secretPattern; if ($hits) { 'SECRET SCAN FAIL:'; $hits | ForEach-Object { $_.Line } } else { 'SECRET SCAN: no hits' }"
    evidence: ""

final_test_commands:
  - "uv run python scripts/run_golden_tasks.py --summary"
  - "uv run pytest tests/runtime/ -v"
  - "uv run pytest tests/infra/gateway/ -v"
  - "uv run pytest tests/architecture/ -v"
  - "uv run pytest -v 2>&1 | tail -30"
  - "uv run ruff check app/runtime/ app/infra/gateway/ app/infra/sdui/ app/ports/trace.py tests/runtime/ tests/infra/gateway/"
  - "uv run mypy app/runtime/ app/infra/gateway/ app/infra/sdui/ app/ports/trace.py"

touched_paths:
  - app/runtime/runtime.py
  - app/infra/gateway/capability_gateway.py
  - app/infra/sdui/response_envelope_builder.py     # REQUIRED (see deliverables note)
  - app/ports/trace.py                              # APPROVED 2026-06-11 — add `adapter_error_mapped` to the TraceEventType Literal ONLY (aligns to spec §12.4.1 line 3490); no other app/ports/ change
  - tests/runtime/test_runtime_trace_threading.py   # new
  - tests/runtime/test_runtime_response_content.py  # new
  - tests/infra/gateway/test_gateway_discrete_events.py  # new
  - tests/infra/gateway/test_gateway_short_circuit.py    # new or update

conditional_touched_paths:
  - path: "app/ports/runtime.py"
    condition: >
      ONLY if GT-007 confirm-continuation requires a RuntimePort signature change
      (e.g. adding prior_user_action or prior_response_id parameter), AND only
      after explicit human approval. The expected path for GT-007 is message-based
      (no port change needed) — implement entirely within app/runtime/runtime.py
      first. See GT-007 section below. This file must NOT be touched without
      explicit human approval.

forbidden_paths:
  - tests/golden_tasks/             # FROZEN JUDGE — never modify runner or fixtures
  - app/ports/                      # frozen port contracts (except: app/ports/trace.py adapter_error_mapped addition — approved; app/ports/runtime.py — conditional GT-007 only)
  - app/execution_fabric/           # forbidden in Runtime per §13.4 #4
  - app/main.py
  - pyproject.toml
  - uv.lock
  - docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md  # frozen blueprint

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-007c"
  - "Base is not phase0/P0-GT-002 @ d804611"
  - "Working tree is dirty at task start"
  - "Any depends_on passed Task Record is missing"
  - "Any file under tests/golden_tasks/ is in the staged diff (ANTI-CHEAT)"
  - "app/ports/ is modified without explicit human approval"
  - "app/execution_fabric/ is imported in app/runtime/"
  - "New Python dependency added (pyproject.toml or uv.lock changed)"
  - "__init__.py file created in new namespace-package directories"
  - "event_type not in TraceEventType Literal is emitted (ValidationError at TraceEvent) — still valid post-addition; `adapter_error_mapped` is the approved addition and must be present in the Literal before use"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
  - "GT-007 requires RuntimePort change and human has not approved"
```

## GT-007 special handling

GT-007 ("用户一次性确认后继续执行 Mock 动作") is the one-shot confirm continuation.

The fixture's `given` block includes:

```json
{
  "prior_response_id": "resp_gt_006",
  "user_action": {
    "action_type": "confirm",
    "response_id": "resp_gt_006",
    "confirmed": true
  }
}
```

**Key fact (confirmed from runner source):** The runner feeds GT-007 via the normal `handle_user_message(message=...)` path (test_golden_tasks.py:605-612, 651-668). The `given.user_action` and `given.prior_response_id` fields are NOT consumed by the actual call path. A `RuntimePort` signature change is almost certainly NOT needed.

**Decision rule:**

1. First, read `scripts/run_golden_tasks.py` to confirm exactly how the runner invokes `handle_user_message` for GT-007 — this is a verification step, not an investigation for a problem.
2. The expected path: the runner passes a message string and the runtime routes on confirm-continuation logic entirely within `app/runtime/runtime.py`. No port change needed.
3. If (unexpectedly) the runner needs a new parameter on `handle_user_message`, this is a **STOP-AND-ASK** condition: report the exact required change to the human and wait for approval before touching `app/ports/runtime.py`.

**Gate independence and reachability:** The §5.2 gate (positive ≥6/7, negative 4/4) is NOW REACHABLE once:
1. `adapter_error_mapped` is added to the TraceEventType Literal (approved), AND
2. The gateway emits the required discrete record_step events for GT-001/GT-005/GT-007 injection companions.

With GT-001 through GT-006 (excluding GT-007) all passing = 6/7 ≈ 86% ≥ 80%, and all 4 negatives (GT-008, GT-009, GT-010, GT-012) passing = 100%, the §5.2 prerequisite is satisfied. GT-007 may be deferred to a follow-up task (`P0-DOMAIN-007d`) without blocking Phase 1 spec readiness.

**Ordering:** implement GT-007 as the LAST sub-goal, after all other GTs are green.

## Decomposition note

This is a single integration task. If the Plan stage finds the scope too large (estimate: >300 lines of net change), the approved fallback split is:

- **Part A** — positive-path completion (GT-001..GT-006 happy path + adapter trace/content + identity/policy discrete events): touches `app/infra/gateway/capability_gateway.py` and `app/runtime/runtime.py` primarily.
- **Part B** — negative/short-circuit completion (GT-008..GT-012 + no_capability_found / blocked_by_identity / blocked_by_policy / confirm_required discrete events + binding UI distinction): touches `app/runtime/runtime.py` response-building logic and `app/infra/gateway/` short-circuit emission.

This split is guidance, not a mandate. A single PR covering all targets is preferred if the diff is tractable.

## Design notes

### Gap 1 — Gateway record_step emissions (NOT a wiring or threading gap)

**Confirmed facts:**
- `trace_id` is ALREADY threaded: runtime.py:139 sets `RequestOrgContext(request_id=trace_id)` and the gateway reads it back at capability_gateway.py:77. No data-flow fix needed.
- The frozen golden-task runner ALREADY injects a single shared `SpyTracePort` into BOTH `CapabilityGateway(trace_port=...)` (test_golden_tasks.py:598) AND `RuntimeImpl(trace_port=...)` (test_golden_tasks.py:601). The runner's fixture wiring is correct and must NOT be touched.
- `SpyTracePort` records ONLY `record_step` calls (runner lines 164-183). `record_gateway_call` and `finalize_task_trace` are no-ops in the spy (runner lines 197-219) and are **invisible to the judge**.
- `capability_gateway.py` has **ZERO** `record_step` calls today (only `finalize_task_trace` at lines 84/115/139/153/214 and `record_gateway_call` at line 187).

**The REAL gap is in the gateway itself**: add `record_step` emissions at every short-circuit point in `capability_gateway.py`. Do NOT touch `tests/golden_tasks/` — the runner wiring is correct.

**DO NOT** instruct the executor to "verify or fix the runner's fixture setup" — that would be an anti-cheat violation.

### Gap 2 — Discrete gateway trace events + gateway_pre/post relocation

**Current state (capability_gateway.py):**

- Lines 82–96: capability not found → calls `trace_port.finalize_task_trace()` directly. Should instead call `record_step(event_type="no_capability_found")` then leave finalization to the Runtime.
- Lines 98–128: identity check → calls `trace_port.finalize_task_trace()` on failure. Should instead call `record_step(event_type="identity_check", status="ok")` when identity resolves, or `record_step(event_type="identity_check")` + `record_step(event_type="blocked_by_identity")` on failure.
- Lines 130–166: policy guard → calls `trace_port.finalize_task_trace()` on deny or confirm. Should call `record_step(event_type="policy_checked", status="ok")` on pass, or `record_step(event_type="policy_checked")` + `record_step(event_type="blocked_by_policy")` on deny, or `record_step(event_type="confirm_required")` on confirm.
- Lines 186–194: adapter call → calls `record_gateway_call()` (spy no-op, invisible). Must ALSO call `record_step(event_type="gateway_pre_recorded", ...)` immediately BEFORE the adapter call, and `record_step(event_type="adapter_called", status="ok")` immediately after.
- Lines 200–221: adapter result → calls `finalize_task_trace()`. On adapter failure, should call `record_step(event_type="adapter_error_mapped")` (§12.4.1 terminal event — requires the approved Literal addition; see touched_paths) before finalize. `adapter_error` remains available for generic adapter errors. Also call `record_step(event_type="gateway_post_recorded", ...)` after the adapter returns (success or failure).

**gateway_pre/post_recorded relocation (mandatory):**
Runtime currently emits `gateway_pre_recorded` BEFORE calling the gateway (runtime.py:140-147) and `gateway_post_recorded` unconditionally after (runtime.py:157-165). These MUST be removed from the runtime and relocated inside the gateway, because:
- GT-001/GT-003 require `gateway_pre_recorded` AFTER `policy_checked` in the event sequence (assert_trace_sequence_contains is order-sensitive subsequence matching, assertions.py:164-179).
- GT-006/GT-009 terminal matrices have `gateway_post_recorded` as must-not-have, but the runtime emits it unconditionally.

**Valid event_type values from app/ports/trace.py TraceEventType Literal** (post-approved addition):
```
task_created, intent_parsed, capability_selected, no_capability_found,
identity_check, blocked_by_identity, policy_checked, blocked_by_policy,
confirm_required, gateway_pre_recorded, adapter_called, adapter_error,
adapter_error_mapped, adapter_result_invalid, gateway_post_recorded,
response_envelope_created, task_completed, task_failed
```

Note: `adapter_error_mapped` is the §12.4.1 adapter_timeout/error-mapping terminal event; it must be added to the Literal per the approved touched_paths entry before use. `adapter_error` remains in the Literal for generic adapter errors. Do NOT invent other new event_type strings.

### Gap 3 — Response content

**no_capability_found path** (runtime.py lines 107–113): Currently calls `build_message(...)` producing `component_type=none`. Fix: add a dedicated builder method (e.g. `build_no_capability_found()`) to `ResponseEnvelopeBuilder` that produces `operator_handback_card` with `action="none"` and envelope `status="no_capability_found"`. OperatorHandbackCard does NOT allow `action="none"`; therefore GT-008, which the fixture asserts requires `ui.action="none"`, MUST be produced by constructing the base `UIComponent` class with `component_type="operator_handback_card"` and `action="none"` (constructible because base `UIAction` includes `"none"` in models.py), NOT via `OperatorHandbackCard`. Do NOT attempt `OperatorHandbackCard(action="none")`: it raises `ValidationError`, which the builder catches and silently degrades to a failed envelope. Note: `build_operator_handback` hardcodes `action="clarify_scope"` — do NOT use it directly for this path. Also change message from "暂未找到匹配的能力" to "暂未接入" (GT-008 asserts message_contains "暂未接入"). Note: `build_operator_handback` also hardcodes `status="blocked"` but GT-008 needs `status="no_capability_found"` — so the no_capability_found path needs its own builder path.

**completed path** (runtime.py lines 215–225): Currently returns hardcoded "操作完成". Fix: format a content-aware message from `exec_result.data`. GT-002 expects message_contains "OA-WF-2026-0001", meaning the adapter data must be summarized into the message. Implement a `_format_capability_response(capability_id, data)` helper in runtime.py that produces a human-readable summary from the data dict. Fall back to "操作完成" only if data is None or empty.

**identity_unbound path** (runtime.py — GT-010): Currently calls `build_operator_handback(...)` which produces `action=clarify_scope`. Fix: branch on `exec_result.error_code`:
- `identity_unbound` / `identity_expired` / `identity_revoked` → produce `OperatorHandbackCard` with `action="bind_required"` + `target_system` populated. Add a dedicated builder method (e.g. `build_operator_handback_bind_required(target_system=...)`) to `ResponseEnvelopeBuilder`. Do NOT use `build_binding_required` — it yields `component_type=binding_required_card` but GT-010 asserts `component_type=operator_handback_card`.
- `needs_binding_scope` → `build_operator_handback(...)` with message containing "账套" and "选择" (clarify_scope, target_system=u8).

**policy_denied path** (runtime.py lines 226–233 — GT-009): Currently calls `build_operator_handback(...)` which sets `action=clarify_scope`. GT-009 asserts `ui.action=none`. Fix: add a `build_policy_denied()` or `build_operator_handback_none()` helper to `ResponseEnvelopeBuilder`. OperatorHandbackCard does NOT allow `action="none"`; therefore GT-009, which the fixture asserts requires `ui.action="none"`, MUST be produced by constructing the base `UIComponent` class with `component_type="operator_handback_card"` and `action="none"` (constructible because base `UIAction` includes `"none"` in models.py), NOT via `OperatorHandbackCard`. Do NOT attempt `OperatorHandbackCard(action="none")`: it raises `ValidationError`, which the builder catches and silently degrades to a failed envelope. Do NOT attempt to "override the action in the payload" — `build_operator_handback`'s payload arg lands in `ui.payload`, not `ui.action` (builder lines 115-143).

**confirm_required path** (GT-006): `build_confirm_card` currently has no `target_system` parameter. GT-006 asserts `ui.target_system=oa`. Add a `target_system` parameter to `build_confirm_card` in `ResponseEnvelopeBuilder`.

**failed/blocked terminals** (GT-009 + general): Failed and blocked terminals (blocked_by_policy, blocked_by_identity) must emit `task_failed` via runtime `record_step` AFTER `response_envelope_created`. Note: `finalize_task_trace` does not land in the judge's steps (spy no-op); use `record_step(event_type="task_failed")` explicitly.

### GT-007 confirm-continuation lookup

Before implementing GT-007, read `scripts/run_golden_tasks.py` to find how it calls `handle_user_message` with the GT-007 `user_action` fixture. Report findings to human before any port change.

### Test patterns

- All async methods tested with `asyncio.run()` in synchronous test functions (no pytest-asyncio).
- Use `SpyTracePort` (a duck-typed mock that records all `record_step` calls) to assert exact event sequences.
- Test `CapabilityGateway` in isolation with injected `SpyTracePort` to prove discrete events.
- Test `RuntimeImpl` end-to-end with a real `CapabilityGateway(trace_port=spy_trace_port)` to prove trace threading.

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm:

1. `tests/golden_tasks/` was NOT modified at any point (ANTI-CHEAT).
2. Only event_type values from `app/ports/trace.py` TraceEventType Literal are emitted.
3. The gateway `trace_id` is the same `trace_id` started by the Runtime (`RequestOrgContext.request_id`).
4. `finalize_task_trace()` is called only at the true terminal step (not at every early return).
5. `identity_unbound` produces `OperatorHandbackCard` with `ui.action=bind_required` and `ui.target_system` populated (NOT `BindingRequiredCard` / `binding_required_card`).
6. `needs_binding_scope` produces `OperatorHandbackCard` with `ui.action=clarify_scope`.
7. `no_capability_found` message contains "暂未接入"; component_type=operator_handback_card; ui.action=none.
8. `policy_denied` message contains "无权限" and "拒绝"; ui.action=none.
9. `completed` message is sourced from adapter output data, not hardcoded "操作完成".
10. `app/ports/runtime.py` was NOT modified (or: human approved the specific change for GT-007).
11. No new Python dependencies. No pyproject.toml or uv.lock changes. No __init__.py files.
12. No plaintext credential/token/password/cookie/sessionid/access_token/refresh_token in any output.
13. RuntimeImpl does not import from `app/execution_fabric/`.
14. §5.2 gate confirmed by runner output: positive_passed >= 6, negative_passed = 4. Gate is reachable once `adapter_error_mapped` is added to the Literal and the gateway emits discrete record_step events.
15. GT-003: RuntimeImpl injects `account_set_id` (and `resource_scope` if present) from `CapabilityRef.arguments` into `RequestOrgContext` so GT-003's dual-binding u8 scenario resolves identity and reaches the adapter.
16. `gateway_pre_recorded` and `gateway_post_recorded` are emitted via record_step INSIDE the gateway (not the runtime).

## Structured-output baseline applicability

not_applicable

- This task does not modify the structured-output path (StructuredOutputPort, MockStructuredOutputProvider, CapabilityRef parsing).
- Plan B baseline (raw OpenAI SDK + response_format=json_object + Pydantic model_validate) is unchanged.
- The changes are in the post-parse execution flow (gateway trace threading, discrete events, response content).
