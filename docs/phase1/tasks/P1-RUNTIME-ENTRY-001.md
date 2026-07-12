# P1-RUNTIME-ENTRY-001 — Runtime Composition Root 与 Golden Harness 解耦

```yaml
task_id: "P1-RUNTIME-ENTRY-001"
task_type: "implementation"
goal: "建立唯一 application Runtime composition seam，把真实 FastAPI Runtime route 接通到可注入的 RuntimeImpl，并把 Golden evaluator 从 tests 实现中解耦且保持逐项行为等价"
non_goals:
  - "不修改 P1-OBS-001 已冻结的 Runtime/Gateway/Observability/Trace 行为"
  - "不修改 frozen ports、Golden fixture/ID/threshold/skip exemption、CI、DB、env/secret、依赖/lockfile"
  - "不把测试 mock/fixture 当生产默认，不声称未实现的生产 provider 已接通"
  - "不删除旧测试文件、不执行 P1-SPEC-001 或 B2"
method_profile:
  execution_role: "mixed"
  execution_owner: "codex"
  review_owner: "claude_code"
  review_mode: "independent_review"
  risk_tier: "high"
  method: "mixed"
  model_note: "TDD for composition/CLI boundaries plus BDD HTTP smoke; Claude Opus is bounded read-only reviewer"
  reason_for_owner_choice: "Phase 1 default split"
controller_risk_tier: "R2"
risk_classification_reason: "Application composition, public HTTP route, shared Golden evaluator and cross-module behavior"
automation_class: "human_pre_apply"
authorization_mode: "standard"
required_stops:
  - kind: "human_pre_apply"
    phase: "pre_apply"
  - kind: "human_result_acceptance"
    phase: "post_integration"
r3_authorization: []
auto_next_policy: "blocked"
integration_policy:
  mode: "git"
  remote_strategy: "task_branch_pr_merge"
  task_branch_ci: "required"
  post_merge_ci: "required"
touched_paths:
  - "app/composition.py"
  - "app/main.py"
  - "app/api/v1/runtime.py"
  - "scripts/run_golden_tasks.py"
  - "scripts/golden_task_assertions.py"
  - "scripts/golden_task_fixture_support.py"
  - "scripts/golden_task_evaluator.py"
  - "tests/test_health.py"
  - "tests/runtime/test_runtime_api.py"
  - "tests/runtime/test_runtime_composition.py"
  - "tests/golden_tasks/assertions.py"
  - "tests/golden_tasks/conftest.py"
  - "tests/golden_tasks/test_fixture_schema.py"
  - "tests/golden_tasks/test_golden_gate.py"
  - "tests/golden_tasks/test_golden_tasks.py"
  - "tests/golden_tasks/test_mock_state_loader.py"
  - "tests/golden_tasks/test_runner_assertions.py"
  - "tests/golden_tasks/test_runner_negative_assertions.py"
  - "tests/golden_tasks/test_runner_cli.py"
  - "docs/phase1/task_logs/P1-RUNTIME-ENTRY-001_*.yaml"
  - "docs/phase1/task_logs/INDEX.md"
forbidden_paths:
  - "docs/phase1/tasks/P1-RUNTIME-ENTRY-001.md"
  - "app/runtime/runtime.py"
  - "app/infra/gateway/**"
  - "app/infra/observability/**"
  - "app/ports/**"
  - "app/contracts/**"
  - "app/db/**"
  - "alembic/**"
  - "migrations/**"
  - ".github/**"
  - "pyproject.toml"
  - "uv.lock"
  - ".env"
  - ".env.*"
  - "web/**"
  - "tests/golden_tasks/fixtures/**"
  - "docs/dev/task_record_schema.yaml"
  - "docs/blueprint/**"
acceptance_criteria:
  - id: "RUNTIME-AC-01"
    text: "真实 app 或 create_app() 的 OpenAPI 同时含 GET /api/v1/health 与 POST /api/v1/runtime/handle；health=200、extra field=422、未配置 runtime 稳定 fail-closed 而非 404/import crash"
  - id: "RUNTIME-AC-02"
    text: "至少一个 HTTP smoke 经正式 app factory/router 与 canonical builder 构造的真实 RuntimeImpl，测试侧仅注入确定性 ports，并验证 TaskStore/Trace 与合法 ResponseEnvelope"
  - id: "RUNTIME-AC-03"
    text: "application 与 Golden evaluator 的 RuntimeImpl 装配知识只存在于 canonical builder；runner/wrapper 不复制 constructor 参数，builder 不含业务 mapping、fixture 或 test condition"
  - id: "RUNTIME-AC-04"
    text: "import app.main 与 health 不连接 DB/外部系统；未配置生产 provider 时 Runtime route 返回稳定不泄漏的 unavailable，绝不静默使用 mock"
  - id: "RUNTIME-AC-05"
    text: "Golden runner 静态 import shared evaluator；runner/shared modules 对 tests.* 和 pytest 实现依赖零命中"
  - id: "RUNTIME-AC-06"
    text: "test_golden_tasks.py 只保留 pytest 调用/参数化/断言，conftest.py 只保留 fixture 职责，不再承载通用 assembly/evaluator/CLI/assertion library"
  - id: "RUNTIME-AC-07"
    text: "改动前后 GT IDs、逐项 status/reasons、total/positive/negative/not_applicable、JSON/中文、gate thresholds、exit 0/1/2 与 P1-OBS terminal matrix完全等价"
  - id: "RUNTIME-AC-08"
    text: "从临时非 repo cwd 调真实 runner 仍成功，路径不依赖 shell cwd"
  - id: "RUNTIME-AC-09"
    text: "app 不 import tests，shared scripts 不 import pytest/test Python；fixture 只按数据路径读取；无新依赖、frozen ports 或 fixture diff"
  - id: "RUNTIME-AC-10"
    text: "保留 malformed HTTP 422、Golden missing-event/credential/adapter-must-not-call/unknown-forbidden、positive<80%、negative<100%、infrastructure exception exit 2 的负向能力"
failure_examples:
  - "真实 app 仍无 Runtime route，或未配置依赖时 404/import crash/静默 mock -> application_entry_incomplete"
  - "application 与 Golden 各自复制 RuntimeImpl constructor knowledge -> composition_duplication"
  - "runner/shared modules import tests.* 或 pytest，或从非 repo cwd 失败 -> harness_not_decoupled"
  - "任一 GT ID/status/reason/threshold/exit 变化且无原契约依据 -> golden_equivalence_failure"
  - "需要修改 Runtime/Gateway/Observability/ports/fixture/threshold/CI/DB/env/dependency或删除内容 -> scope_expansion_required"
step_verification_points:
  - "preflight: dependency/descriptor/current CI and saved pre-change Golden summary/gate JSON/hash"
  - "red: real app route/fail-closed/RuntimeImpl HTTP smoke plus no-tests-import/cross-cwd/exit 0-1-2 tests fail for intended reasons"
  - "green: minimal builder/app DI and shared evaluator make targeted tests pass without behavior drift"
  - "equivalence: pre/post per-GT IDs/status/reasons/counts/thresholds/outputs/exits match"
  - "pre-review/integration: exact scope, weak-test/secret/forbidden checks, current pre/post CI, Gate 2"
validation_commands:
  - "uv run pytest tests/test_health.py tests/runtime/test_runtime_api.py tests/runtime/test_runtime_composition.py -q"
  - "uv run pytest tests/golden_tasks/ -q"
  - "uv run python scripts/run_golden_tasks.py --summary"
  - "uv run python scripts/run_golden_tasks.py --gate"
  - "uv run pytest tests/architecture/ -q -p no:cacheprovider"
  - "uv run ruff check app/ scripts/ tests/"
  - "uv run mypy app/"
  - "uv run python scripts/check_dependencies.py"
  - "uv run pytest"
  - "uv run python scripts/check_weak_tests.py <each changed test file>"
  - >-
    rg -n 'import_module\("tests\.|from tests\.|import tests\.' scripts/run_golden_tasks.py scripts/golden_task_assertions.py scripts/golden_task_fixture_support.py scripts/golden_task_evaluator.py
evidence_requirements:
  - "pre/post Golden summary/gate raw outputs, SHA-256 and per-GT equivalence ledger"
  - "real OpenAPI/HTTP health/422/unavailable/RuntimeImpl smoke evidence and non-leaking response"
  - "canonical builder call-site scan, no app->tests and no shared-script->tests/pytest scan"
  - "real subprocess runner from non-repo cwd and exit 0/1/2 negative evidence"
  - "unchanged fixture/threshold/CI/DB/env/dependency/port hashes or diff assertions"
  - "Codex/Claude findings ledger, exact staged/secret checks, Task Record, pre-merge and merge-SHA post-CI"
stop_conditions:
  - "P1-OBS-001 is not merged with current pre/post CI green, or descriptor/DAG/base is stale"
  - "formal source/task contract conflicts or old workflow/Gate rules remain active"
  - "app and Golden cannot share a minimal builder without widening the authorized architecture surface"
  - "Golden equivalence cannot be proved, packet needs truncation, or Review/CI evidence is invalid/stale/red"
  - "fix requires a forbidden path, dependency/lockfile, DB/env/secret, deletion or any R3 action"
  - "mock would become a production default or production readiness would be overstated"
local_commit_policy: "after_review_pass"
depends_on:
  - "P1-OBS-001 completed with pre/post CI green and final Trace contract bound"
branch: "phase1/P1-RUNTIME-ENTRY-001"
references:
  - "docs/phase1/ROLE_POLICY.md"
  - "docs/phase1/TASK_PROMPT_TEMPLATE.md"
  - "docs/dev/task_record_schema.yaml"
```

## Composition and Golden boundaries

- Prefer one flat `app/composition.py` seam that only wires existing ports into `RuntimeImpl`. `app.main` and the Golden evaluator use it; narrow unit tests may still construct Runtime directly.
- The real app always registers the Runtime route. Missing production providers produce a stable unavailable result while import and health remain side-effect free.
- Shared Golden code lives in the authorized flat `scripts/golden_task_*.py` modules. Tests may call it and fixtures remain data under `tests/golden_tasks/fixtures/`, but production/shared Python never imports test implementation.

CODEX_CLAUDE_TASK_CONTRACT_BEGIN
{
  "task_id": "P1-RUNTIME-ENTRY-001",
  "allowed_paths": [
    "app/composition.py",
    "app/main.py",
    "app/api/v1/runtime.py",
    "scripts/run_golden_tasks.py",
    "scripts/golden_task_assertions.py",
    "scripts/golden_task_fixture_support.py",
    "scripts/golden_task_evaluator.py",
    "tests/test_health.py",
    "tests/runtime/test_runtime_api.py",
    "tests/runtime/test_runtime_composition.py",
    "tests/golden_tasks/assertions.py",
    "tests/golden_tasks/conftest.py",
    "tests/golden_tasks/test_fixture_schema.py",
    "tests/golden_tasks/test_golden_gate.py",
    "tests/golden_tasks/test_golden_tasks.py",
    "tests/golden_tasks/test_mock_state_loader.py",
    "tests/golden_tasks/test_runner_assertions.py",
    "tests/golden_tasks/test_runner_negative_assertions.py",
    "tests/golden_tasks/test_runner_cli.py",
    "docs/phase1/task_logs/P1-RUNTIME-ENTRY-001_*.yaml",
    "docs/phase1/task_logs/INDEX.md"
  ],
  "forbidden_paths": [
    "docs/phase1/tasks/P1-RUNTIME-ENTRY-001.md",
    "app/runtime/runtime.py",
    "app/infra/gateway/**",
    "app/infra/observability/**",
    "app/ports/**",
    "app/contracts/**",
    "app/db/**",
    "alembic/**",
    "migrations/**",
    ".github/**",
    "pyproject.toml",
    "uv.lock",
    ".env",
    ".env.*",
    "web/**",
    "tests/golden_tasks/fixtures/**",
    "docs/dev/task_record_schema.yaml",
    "docs/blueprint/**"
  ],
  "required_deliverables": [
    "app/composition.py",
    "app/main.py",
    "app/api/v1/runtime.py",
    "scripts/golden_task_evaluator.py",
    "docs/phase1/task_logs/P1-RUNTIME-ENTRY-001_*.yaml"
  ],
  "acceptance_ids": [
    "RUNTIME-AC-01",
    "RUNTIME-AC-02",
    "RUNTIME-AC-03",
    "RUNTIME-AC-04",
    "RUNTIME-AC-05",
    "RUNTIME-AC-06",
    "RUNTIME-AC-07",
    "RUNTIME-AC-08",
    "RUNTIME-AC-09",
    "RUNTIME-AC-10"
  ]
}
CODEX_CLAUDE_TASK_CONTRACT_END
