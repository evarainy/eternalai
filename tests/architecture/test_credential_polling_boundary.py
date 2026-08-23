"""Credential binding/polling must remain outside Trace and ResponseEnvelope."""

from __future__ import annotations

import ast
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_CREDENTIAL_PATHS = (
    Path("app/api/v1/credential_bindings.py"),
    Path("app/credential_polling.py"),
    Path("app/infra/auth/background.py"),
    Path("app/ports/credential_binding.py"),
)
_FORBIDDEN_IMPORTS = (
    "app.contracts.sdui",
    "app.infra.observability",
    "app.infra.sdui",
    "app.ports.response_envelope",
    "app.ports.trace",
)
_FORBIDDEN_NAMES = {
    "ResponseEnvelope",
    "ResponseEnvelopeBuilder",
    "TraceEvent",
    "TracePort",
    "record_event",
}


def _forbidden_uses(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(_FORBIDDEN_IMPORTS):
                    findings.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(_FORBIDDEN_IMPORTS):
                findings.append(module)
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            findings.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_NAMES:
            findings.append(node.attr)
    return findings


def _job_queue_wiring_findings(
    polling_path: Path,
    composition_path: Path,
) -> list[str]:
    polling_tree = ast.parse(
        polling_path.read_text(encoding="utf-8"),
        filename=str(polling_path),
    )
    composition_tree = ast.parse(
        composition_path.read_text(encoding="utf-8"),
        filename=str(composition_path),
    )
    findings: list[str] = []
    scheduler = next(
        (
            node
            for node in polling_tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "CredentialPollingScheduler"
        ),
        None,
    )
    initializer = next(
        (
            node
            for node in scheduler.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "__init__"
        ),
        None,
    ) if scheduler is not None else None
    initializer_args = (
        {argument.arg for argument in initializer.args.args + initializer.args.kwonlyargs}
        if initializer is not None
        else set()
    )
    if "job_queue" not in initializer_args:
        findings.append("scheduler_missing_job_queue")

    scheduler_nodes = list(ast.walk(scheduler)) if scheduler is not None else []
    enqueues_job_queue = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "enqueue"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "_job_queue"
        for node in scheduler_nodes
    )
    if not enqueues_job_queue:
        findings.append("scheduler_bypasses_job_queue")
    if any(
        isinstance(node, ast.Attribute) and node.attr == "_run_once"
        for node in scheduler_nodes
    ):
        findings.append("scheduler_calls_run_once_directly")

    composition_calls = [
        node
        for node in ast.walk(composition_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    if not any(node.func.id == "InMemoryJobQueue" for node in composition_calls):
        findings.append("production_missing_job_queue")
    work_object_calls = [
        node for node in composition_calls if node.func.id == "WorkObjectService"
    ]
    if not any(
        any(
            keyword.arg == "gateway"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "gateway"
            for keyword in node.keywords
        )
        for node in work_object_calls
    ):
        findings.append("production_work_objects_missing_gateway")
    polling_service_calls = [
        node for node in composition_calls if node.func.id == "CredentialPollingService"
    ]
    if not any(
        any(
            keyword.arg == "work_objects"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "work_object_service"
            for keyword in node.keywords
        )
        for node in polling_service_calls
    ):
        findings.append("production_polling_missing_work_object_boundary")
    polling_job = next(
        (
            node
            for node in ast.walk(composition_tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "run_credential_polling_job"
        ),
        None,
    )
    if not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run_due"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "credential_polling_service"
        for node in (ast.walk(polling_job) if polling_job is not None else ())
    ):
        findings.append("production_job_missing_polling_service")
    scheduler_calls = [
        node
        for node in composition_calls
        if node.func.id == "CredentialPollingScheduler"
    ]
    if not any(
        any(keyword.arg == "job_queue" for keyword in node.keywords)
        for node in scheduler_calls
    ):
        findings.append("production_scheduler_missing_job_queue")
    return findings


def test_credential_path_has_no_trace_or_response_envelope_dependency() -> None:
    violations = {
        str(relative_path): _forbidden_uses(_REPOSITORY_ROOT / relative_path)
        for relative_path in _CREDENTIAL_PATHS
    }

    assert violations == {str(path): [] for path in _CREDENTIAL_PATHS}


def test_guard_rejects_a_deliberate_trace_and_response_envelope_miswire(
    tmp_path: Path,
) -> None:
    miswired = tmp_path / "miswired.py"
    miswired.write_text(
        "from app.ports.response_envelope import ResponseEnvelope\n"
        "from app.ports.trace import TracePort\n"
        "async def leak(trace: TracePort, event):\n"
        "    await trace.record_event(event)\n"
        "    return ResponseEnvelope\n",
        encoding="utf-8",
    )

    findings = _forbidden_uses(miswired)

    assert "app.ports.response_envelope" in findings
    assert "app.ports.trace" in findings
    assert "record_event" in findings
    assert "ResponseEnvelope" in findings


def test_credential_polling_production_path_requires_job_queue_port() -> None:
    findings = _job_queue_wiring_findings(
        _REPOSITORY_ROOT / "app/credential_polling.py",
        _REPOSITORY_ROOT / "app/composition.py",
    )

    assert findings == []


def test_guard_rejects_deliberate_direct_polling_bypass(tmp_path: Path) -> None:
    polling = tmp_path / "polling.py"
    polling.write_text(
        "class CredentialPollingScheduler:\n"
        "    def __init__(self, run_once):\n"
        "        self._run_once = run_once\n"
        "    async def _run(self):\n"
        "        await self._run_once()\n",
        encoding="utf-8",
    )
    composition = tmp_path / "composition.py"
    composition.write_text(
        "from polling import CredentialPollingScheduler\n"
        "scheduler = CredentialPollingScheduler(run_once=service.run_due)\n",
        encoding="utf-8",
    )

    findings = _job_queue_wiring_findings(polling, composition)

    assert findings == [
        "scheduler_missing_job_queue",
        "scheduler_bypasses_job_queue",
        "scheduler_calls_run_once_directly",
        "production_missing_job_queue",
        "production_work_objects_missing_gateway",
        "production_polling_missing_work_object_boundary",
        "production_job_missing_polling_service",
        "production_scheduler_missing_job_queue",
    ]
