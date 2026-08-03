from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request

import pytest

from app.infra.adapters.oa.contracts import (
    build_structural_fingerprint,
    compare_structural_fingerprints,
)
from scripts.smoke import environment as smoke_environment
from scripts.smoke import runner as smoke_runner
from scripts.smoke.environment import parse_env_file, prepare_environment
from scripts.smoke.errors import SmokeError
from scripts.smoke.har import MessageCenterContract, extract_message_center_contract
from scripts.smoke.live import (
    ProtocolEvidence,
    ProtocolSummary,
    compare_record_structures,
)
from scripts.smoke.runner import (
    Layout,
    LiveOutcome,
    _assert_report_safe,
    _build_parser,
    _build_report,
    _classify_backend_failure,
    _cleanup_failed_start,
    _configuration_fingerprint,
)


def _message_center_entry(
    *,
    url: str = "https://synthetic.invalid/api/message-center",
    bizstate: str = "synthetic-biz",
    select_state: str = "synthetic-select",
) -> dict[str, object]:
    values = {
        "id": "synthetic-category",
        "pagesize": "20",
        "msgid": "",
        "mintime": "",
        "bizstate": bizstate,
        "selectState": select_state,
    }
    return {
        "request": {
            "url": url,
            "postData": {
                "params": [
                    {"name": key, "value": value}
                    for key, value in values.items()
                ]
            },
        },
        "response": {
            "content": {
                "text": json.dumps(
                    {
                        "data": [],
                        "maxtime": "",
                        "mintime": "",
                        "msgid": "",
                        "status": "1",
                    }
                )
            }
        },
    }


def _write_har(path: Path, entries: list[object]) -> None:
    path.write_text(
        json.dumps({"log": {"entries": entries}}),
        encoding="utf-8",
    )


@pytest.mark.parametrize("command", ["prepare", "rehearse", "start", "verify"])
def test_cli_parser_accepts_each_command(command: str) -> None:
    args = _build_parser().parse_args([command])

    assert args.command == command


def test_extract_message_center_contract_from_unique_fake_har(
    tmp_path: Path,
) -> None:
    path = tmp_path / "synthetic.har"
    _write_har(path, [{"request": {}}, _message_center_entry()])

    contract = extract_message_center_contract(path)

    assert contract.source_entry_index == 1
    assert contract.matching_entry_count == 1
    assert contract.base_url == "https://synthetic.invalid"
    assert contract.endpoint_path == "/api/message-center"
    assert repr(contract) == "MessageCenterContract(structural_only=True)"


def test_extract_message_center_contract_stops_when_unrecognized(
    tmp_path: Path,
) -> None:
    path = tmp_path / "synthetic.har"
    _write_har(path, [{"request": {"url": "https://synthetic.invalid/other"}}])

    with pytest.raises(SmokeError, match="message_center_entry_not_found"):
        extract_message_center_contract(path)


def test_extract_message_center_contract_rejects_multiple_signatures(
    tmp_path: Path,
) -> None:
    path = tmp_path / "synthetic.har"
    _write_har(
        path,
        [
            _message_center_entry(),
            _message_center_entry(
                url="https://synthetic.invalid/api/other-message-center"
            ),
        ],
    )

    with pytest.raises(SmokeError, match="message_center_entry_ambiguous"):
        extract_message_center_contract(path)


def test_prepare_is_idempotent_and_never_writes_base_env(
    tmp_path: Path,
) -> None:
    base_env = tmp_path / ".env"
    base_env.write_text(
        "DATABASE_URL=postgresql://synthetic.invalid/db\n"
        "REDIS_URL=redis://synthetic.invalid:6379/0\n",
        encoding="utf-8",
    )
    before_hash = hashlib.sha256(base_env.read_bytes()).hexdigest()
    smoke_env = tmp_path / ".env.smoke"
    for profile in (
        "ecology9-pending-workflows-v1",
        "ecology9-system-messages-v1",
    ):
        (tmp_path / "tests" / "contract_packs" / "oa" / profile).mkdir(
            parents=True
        )
    contract = MessageCenterContract(
        source_entry_index=7,
        matching_entry_count=1,
        base_url="https://synthetic.invalid",
        endpoint_path="/api/message-center",
        bizstate="synthetic-biz",
        select_state="synthetic-select",
    )

    first = prepare_environment(
        repo_root=tmp_path,
        base_env_path=base_env,
        smoke_env_path=smoke_env,
        contract=contract,
        process_environment={},
        check_infra=False,
    )
    first_values = parse_env_file(smoke_env)
    second = prepare_environment(
        repo_root=tmp_path,
        base_env_path=base_env,
        smoke_env_path=smoke_env,
        contract=MessageCenterContract(
            source_entry_index=8,
            matching_entry_count=1,
            base_url="https://different.invalid",
            endpoint_path="/different",
            bizstate="different-biz",
            select_state="different-select",
        ),
        process_environment={},
        check_infra=False,
    )

    assert first.added_keys
    assert first.missing_keys == ()
    assert second.added_keys == ()
    assert second.missing_keys == ()
    assert parse_env_file(smoke_env) == first_values
    assert hashlib.sha256(base_env.read_bytes()).hexdigest() == before_hash


def test_prepare_completes_a_half_filled_file_then_is_idempotent(
    tmp_path: Path,
) -> None:
    base_env = tmp_path / ".env"
    base_env.write_text(
        "DATABASE_URL=postgresql://synthetic.invalid/db\n"
        "REDIS_URL=redis://synthetic.invalid:6379/0\n",
        encoding="utf-8",
    )
    smoke_env = tmp_path / ".env.smoke"
    for profile in (
        "ecology9-pending-workflows-v1",
        "ecology9-system-messages-v1",
    ):
        (tmp_path / "tests" / "contract_packs" / "oa" / profile).mkdir(
            parents=True
        )
    contract = MessageCenterContract(
        source_entry_index=0,
        matching_entry_count=1,
        base_url="https://synthetic.invalid",
        endpoint_path="/api/message-center",
        bizstate="synthetic-biz",
        select_state="synthetic-select",
    )
    prepare_environment(
        repo_root=tmp_path,
        base_env_path=base_env,
        smoke_env_path=smoke_env,
        contract=contract,
        process_environment={},
        check_infra=False,
    )
    full_values = parse_env_file(smoke_env)
    retained = dict(list(full_values.items())[: len(full_values) // 2])
    smoke_env.write_text(
        "".join(f"{key}={value}\n" for key, value in retained.items()),
        encoding="utf-8",
    )

    completed = prepare_environment(
        repo_root=tmp_path,
        base_env_path=base_env,
        smoke_env_path=smoke_env,
        contract=contract,
        process_environment={},
        check_infra=False,
    )
    repeated = prepare_environment(
        repo_root=tmp_path,
        base_env_path=base_env,
        smoke_env_path=smoke_env,
        contract=contract,
        process_environment={},
        check_infra=False,
    )

    assert completed.added_keys
    assert completed.missing_keys == ()
    assert repeated.added_keys == ()
    assert parse_env_file(smoke_env) == full_values


def test_atomic_smoke_env_failure_preserves_original_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke_env = tmp_path / ".env.smoke"
    original = b"EXISTING_KEY=existing-value\n"
    smoke_env.write_bytes(original)

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(smoke_environment.os, "replace", fail_replace)

    with pytest.raises(SmokeError, match="smoke_env_write_failed"):
        smoke_environment._append_env_values(
            smoke_env,
            {"NEW_KEY": "new-value"},
        )

    assert smoke_env.read_bytes() == original
    assert not list(tmp_path.glob(".env.smoke.*.tmp"))


def test_prepare_does_not_replace_existing_blank_value(
    tmp_path: Path,
) -> None:
    base_env = tmp_path / ".env"
    base_env.write_text(
        "DATABASE_URL=postgresql://synthetic.invalid/db\n"
        "REDIS_URL=redis://synthetic.invalid:6379/0\n",
        encoding="utf-8",
    )
    smoke_env = tmp_path / ".env.smoke"
    smoke_env.write_text("OA_BASE_URL=\n", encoding="utf-8")
    for profile in (
        "ecology9-pending-workflows-v1",
        "ecology9-system-messages-v1",
    ):
        (tmp_path / "tests" / "contract_packs" / "oa" / profile).mkdir(
            parents=True
        )
    contract = MessageCenterContract(
        source_entry_index=0,
        matching_entry_count=1,
        base_url="https://synthetic.invalid",
        endpoint_path="/api/message-center",
        bizstate="synthetic-biz",
        select_state="synthetic-select",
    )

    result = prepare_environment(
        repo_root=tmp_path,
        base_env_path=base_env,
        smoke_env_path=smoke_env,
        contract=contract,
        process_environment={},
        check_infra=False,
    )

    assert parse_env_file(smoke_env)["OA_BASE_URL"] == ""
    assert "OA_BASE_URL" in result.missing_keys


def test_prepare_treats_explicit_blank_filter_values_as_complete(
    tmp_path: Path,
) -> None:
    base_env = tmp_path / ".env"
    base_env.write_text(
        "DATABASE_URL=postgresql://synthetic.invalid/db\n"
        "REDIS_URL=redis://synthetic.invalid:6379/0\n",
        encoding="utf-8",
    )
    smoke_env = tmp_path / ".env.smoke"
    for profile in (
        "ecology9-pending-workflows-v1",
        "ecology9-system-messages-v1",
    ):
        (tmp_path / "tests" / "contract_packs" / "oa" / profile).mkdir(
            parents=True
        )
    contract = MessageCenterContract(
        source_entry_index=0,
        matching_entry_count=1,
        base_url="https://synthetic.invalid",
        endpoint_path="/api/message-center",
        bizstate="",
        select_state="",
    )

    result = prepare_environment(
        repo_root=tmp_path,
        base_env_path=base_env,
        smoke_env_path=smoke_env,
        contract=contract,
        process_environment={},
        check_infra=False,
    )

    assert result.missing_keys == ()
    values = parse_env_file(smoke_env)
    assert values["OA_PENDING_WORKFLOWS_BIZSTATE"] == ""
    assert values["OA_SYSTEM_MESSAGES_SELECT_STATE"] == ""


def test_protocol_evidence_keeps_only_structure_counts_and_cursor_booleans() -> None:
    expected = {
        "id": "synthetic-category",
        "pagesize": "20",
        "bizstate": "synthetic-biz",
        "selectState": "synthetic-select",
    }
    evidence = ProtocolEvidence(expected_form=expected)
    first_form = {
        **expected,
        "msgid": "",
        "mintime": "",
    }
    first_payload = {
        "data": [{"messageid": "secret-id", "title": "secret-title"}],
        "maxtime": "secret-max",
        "mintime": "secret-next-time",
        "msgid": "secret-next-id",
        "status": "1",
    }
    evidence.observe(
        Request(
            "https://synthetic.invalid/api/message-center",
            data=urlencode(first_form).encode("ascii"),
        ),
        json.dumps(first_payload).encode("utf-8"),
    )
    second_form = {
        **expected,
        "msgid": "secret-next-id",
        "mintime": "secret-next-time",
    }
    evidence.observe(
        Request(
            "https://synthetic.invalid/api/message-center",
            data=urlencode(second_form).encode("ascii"),
        ),
        json.dumps(
            {
                "data": [],
                "maxtime": "secret-max",
                "mintime": "secret-final-time",
                "msgid": "secret-final-id",
                "status": "1",
            }
        ).encode("utf-8"),
    )

    summary = evidence.summary()
    rendered = repr(evidence) + repr(summary)
    assert summary.request_count == 2
    assert summary.response_count == 2
    assert summary.record_count == 1
    assert summary.terminal_empty_page is True
    assert summary.cursor_chain_matches is True
    assert summary.configured_form_matches is True
    assert summary.record_field_types == {
        "messageid": ("string",),
        "title": ("string",),
    }
    assert "secret-id" not in rendered
    assert "secret-title" not in rendered
    assert "secret-next-id" not in rendered


def test_compare_record_structures_lists_only_field_names() -> None:
    first = ProtocolEvidence(expected_form={})
    second = ProtocolEvidence(expected_form={})
    first.record_field_types = {"shared": {"string"}, "left": {"integer"}}
    second.record_field_types = {"shared": {"integer"}, "right": {"boolean"}}

    matches, added, removed, changed = compare_record_structures(
        first.summary(), second.summary()
    )

    assert matches is False
    assert added == ("right",)
    assert removed == ("left",)
    assert changed == ("shared",)


def test_report_is_built_only_from_structural_metadata() -> None:
    fingerprint = build_structural_fingerprint({"messages": []})
    drift = compare_structural_fingerprints(fingerprint, fingerprint)
    protocol = ProtocolSummary(
        request_count=2,
        response_count=2,
        record_count=3,
        terminal_empty_page=True,
        cursor_chain_matches=True,
        configured_form_matches=True,
        successful_envelopes=True,
        envelope_fields=("data", "mintime", "msgid", "status"),
        record_field_types={"messageid": ("string",), "title": ("string",)},
    )
    system = LiveOutcome(
        drift=drift,
        protocol=protocol,
        normalized=True,
        error_kind=None,
    )
    pending = LiveOutcome(
        drift=drift.model_copy(update={"matches": False}),
        protocol=protocol,
        normalized=False,
        error_kind="normalization_or_structure_drift",
    )
    sensitive_values = {
        "OA_BASE_URL": "https://private.synthetic.invalid",
        "OA_MESSAGE_CENTER_PATH": "/private/synthetic/path",
        "DATABASE_URL": "postgresql://private.synthetic.invalid/db",
        "REDIS_URL": "redis://private.synthetic.invalid:6379/0",
        "ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64": "sensitive-key-value-001",
        "ETERNALAI_IDENTITY_HMAC_KEY_B64": "sensitive-key-value-002",
        "ETERNALAI_SESSION_SIGNING_KEY_B64": "sensitive-key-value-003",
        "ETERNALAI_SESSION_BINDING_KEY_B64": "sensitive-key-value-004",
    }

    report = _build_report(system, pending, capture_created=False)
    _assert_report_safe(report, sensitive_values)

    for value in sensitive_values.values():
        assert value not in report
    for forbidden in ("隐式输入", "mock", "错误分类", "结构错误"):
        assert forbidden not in report
    assert "输入时屏幕不会显示内容" in report
    assert "只打开上面打印的 `/chat` 地址并试一次" in report
    assert "完成后回到命令行运行 `./smoke.ps1 verify`" in report
    assert "任一命令失败就马上停止" in report
    assert "不要自行改文件，也不要切换运行模式" in report
    assert "messageid" in report
    assert "title" in report


def test_configuration_fingerprint_is_deterministic_and_value_free() -> None:
    environment = {
        "OA_BASE_URL": "https://private.synthetic.invalid",
        "ETERNALAI_SESSION_SIGNING_KEY_B64": "sensitive-key-value-003",
    }

    first = _configuration_fingerprint(environment)
    second = _configuration_fingerprint(dict(environment))

    assert first == second
    assert len(first) == 64
    assert all(value not in first for value in environment.values())


class _FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid


def test_failed_start_cleanup_terminates_only_new_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = Layout(
        repo_root=tmp_path,
        shared_root=tmp_path,
        base_env=tmp_path / ".env",
        smoke_env=tmp_path / ".env.smoke",
        source_har=tmp_path / "synthetic.har",
        scratch=tmp_path / "_scratch",
    )
    layout.scratch.mkdir()
    environment = {"OA_BASE_URL": "https://synthetic.invalid"}
    backend_reused_pid = 41001
    frontend_new = _FakeProcess(41002)
    (layout.scratch / "smoke_processes.json").write_text(
        json.dumps(
            {
                "backend_pid": backend_reused_pid,
                "frontend_pid": frontend_new.pid,
                "configuration_sha256": _configuration_fingerprint(environment),
            }
        ),
        encoding="utf-8",
    )
    terminated: list[int] = []

    def terminate(process: _FakeProcess) -> bool:
        terminated.append(process.pid)
        return True

    monkeypatch.setattr(smoke_runner, "_terminate_new_process", terminate)

    _cleanup_failed_start(
        layout,
        backend=None,
        frontend=frontend_new,  # type: ignore[arg-type]
        environment=environment,
    )

    state = json.loads(
        (layout.scratch / "smoke_processes.json").read_text(encoding="utf-8")
    )
    assert terminated == [frontend_new.pid]
    assert state["backend_pid"] == backend_reused_pid
    assert state["frontend_pid"] is None


@pytest.mark.parametrize("failed_stage", ["backend", "frontend", "login"])
def test_each_start_failure_path_runs_retry_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed_stage: str,
) -> None:
    layout = Layout(
        repo_root=tmp_path,
        shared_root=tmp_path,
        base_env=tmp_path / ".env",
        smoke_env=tmp_path / ".env.smoke",
        source_har=tmp_path / "synthetic.har",
        scratch=tmp_path / "_scratch",
    )
    backend = _FakeProcess(42001)
    frontend = _FakeProcess(42002)
    cleanups: list[tuple[object | None, object | None]] = []
    monkeypatch.setattr(smoke_runner, "load_runtime_environment", lambda **_kwargs: {})
    monkeypatch.setattr(smoke_runner, "_validate_settings", lambda _environment: object())
    monkeypatch.setattr(smoke_runner, "_start_backend", lambda *_args: backend)
    monkeypatch.setattr(
        smoke_runner,
        "_wait_for_backend",
        lambda *_args, **_kwargs: (
            (False, "configuration_error")
            if failed_stage == "backend"
            else (True, None)
        ),
    )
    monkeypatch.setattr(smoke_runner, "_write_process_state", lambda *_args: None)
    monkeypatch.setattr(smoke_runner, "_start_frontend", lambda *_args: frontend)
    monkeypatch.setattr(
        smoke_runner,
        "_wait_for_frontend",
        lambda _process: failed_stage != "frontend",
    )
    monkeypatch.setattr(
        smoke_runner,
        "_cold_login_preflight",
        lambda: failed_stage != "login",
    )
    monkeypatch.setattr(
        smoke_runner,
        "_cleanup_failed_start",
        lambda _layout, started_backend, started_frontend, _environment: cleanups.append(
            (started_backend, started_frontend)
        ),
    )

    result = smoke_runner._command_start(layout)

    assert result == 1
    assert len(cleanups) == 1
    assert cleanups[0][0] is backend
    if failed_stage == "backend":
        assert cleanups[0][1] is None
        output = capsys.readouterr().out
        assert "backend_start_failed=configuration_error" in output
        assert "请停止操作" in output
    else:
        assert cleanups[0][1] is frontend


def test_successful_start_prints_plain_next_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    layout = Layout(
        repo_root=tmp_path,
        shared_root=tmp_path,
        base_env=tmp_path / ".env",
        smoke_env=tmp_path / ".env.smoke",
        source_har=tmp_path / "synthetic.har",
        scratch=tmp_path / "_scratch",
    )
    backend = _FakeProcess(43001)
    frontend = _FakeProcess(43002)
    monkeypatch.setattr(smoke_runner, "load_runtime_environment", lambda **_kwargs: {})
    monkeypatch.setattr(smoke_runner, "_validate_settings", lambda _environment: object())
    monkeypatch.setattr(smoke_runner, "_start_backend", lambda *_args: backend)
    monkeypatch.setattr(
        smoke_runner,
        "_wait_for_backend",
        lambda *_args, **_kwargs: (True, None),
    )
    monkeypatch.setattr(smoke_runner, "_write_process_state", lambda *_args: None)
    monkeypatch.setattr(smoke_runner, "_start_frontend", lambda *_args: frontend)
    monkeypatch.setattr(smoke_runner, "_wait_for_frontend", lambda _process: True)
    monkeypatch.setattr(smoke_runner, "_cold_login_preflight", lambda: True)
    monkeypatch.setattr(
        smoke_runner,
        "_cleanup_failed_start",
        lambda *_args: pytest.fail("successful start must not clean up services"),
    )

    result = smoke_runner._command_start(layout)

    output = capsys.readouterr().out
    assert result == 0
    assert "只打开上面打印的 /chat 地址并试一次" in output
    assert "完成后回到命令行运行 .\\smoke.ps1 verify" in output


@pytest.mark.parametrize(
    ("failed_checks", "process_exited", "log_text", "expected"),
    [
        (("database",), False, "", "database_unreachable"),
        (("redis",), False, "", "redis_unreachable"),
        (
            ("database", "redis"),
            False,
            "",
            "database_and_redis_unreachable",
        ),
        ((), True, "RuntimeError: OA_BASE_URL is required", "configuration_error"),
        ((), True, "synthetic process failure", "process_exited"),
        ((), False, "", "health_timeout"),
    ],
)
def test_backend_failure_classification_is_value_free(
    tmp_path: Path,
    failed_checks: tuple[str, ...],
    process_exited: bool,
    log_text: str,
    expected: str,
) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(log_text, encoding="utf-8")

    classification = _classify_backend_failure(
        failed_checks=failed_checks,
        process_exited=process_exited,
        log_path=log_path,
    )

    assert classification == expected
    if log_text:
        assert log_text not in classification
