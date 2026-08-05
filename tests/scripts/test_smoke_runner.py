from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from dataclasses import replace
from http.client import RemoteDisconnected
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request

import pytest
from sqlalchemy.exc import DBAPIError

from app.infra.adapters.oa.contracts import (
    build_structural_fingerprint,
    compare_structural_fingerprints,
)
from app.infra.auth.oa import OAAuthenticationError
from app.ports.auth import AuthenticationError
from app.ports.capability_registry import CapabilitySpec
from scripts.smoke import environment as smoke_environment
from scripts.smoke import runner as smoke_runner
from scripts.smoke.capabilities import expected_oa_capabilities
from scripts.smoke.environment import parse_env_file, prepare_environment
from scripts.smoke.errors import SmokeError
from scripts.smoke.har import MessageCenterContract, extract_message_center_contract
from scripts.smoke.live import (
    ProtocolEvidence,
    ProtocolSummary,
    RecordingOpener,
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
    msgid: str = "0",
    mintime: str = "0",
) -> dict[str, object]:
    values = {
        "id": "synthetic-category",
        "pagesize": "20",
        "msgid": msgid,
        "mintime": mintime,
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


def test_rehearse_prints_full_value_free_actual_drift_nodes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected = build_structural_fingerprint(
        {"messages": [{"message_id": ""}]}
    )
    actual = build_structural_fingerprint(
        {
            "messages": [
                {
                    "message_id": "",
                    "wire_gomethod": "sensitive-value-not-rendered",
                }
            ]
        }
    )
    drift = compare_structural_fingerprints(expected, actual)
    layout = Layout(
        repo_root=tmp_path,
        shared_root=tmp_path,
        base_env=tmp_path / ".env",
        smoke_env=tmp_path / ".env.smoke",
        source_har=tmp_path / "system-messages.har",
        scratch=tmp_path / "_scratch",
    )
    monkeypatch.setattr(
        smoke_runner,
        "load_runtime_environment",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        smoke_runner,
        "_validate_settings",
        lambda _environment: SimpleNamespace(),
    )
    monkeypatch.setattr(
        smoke_runner,
        "_run_rehearsal",
        lambda _layout, _environment: smoke_runner.RehearsalResult(
            node_count=len(actual["nodes"]),
            added_count=1,
            removed_count=0,
            changed_count=0,
            sha_matches=False,
            replay_composition_ok=True,
            drift=drift,
        ),
    )

    result = smoke_runner._command_rehearse(layout)

    output = capsys.readouterr().out
    assert result == 1
    assert (
        'fingerprint_drift_added_001={"array_shape":null,'
        '"json_type":"string","nullable":false,'
        '"path":"$.messages[].wire_gomethod"}'
    ) in output
    assert "sensitive-value-not-rendered" not in output


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


def test_extract_message_center_contract_rejects_nonzero_initial_cursor(
    tmp_path: Path,
) -> None:
    path = tmp_path / "synthetic.har"
    _write_har(path, [_message_center_entry(msgid="synthetic-cursor")])

    with pytest.raises(SmokeError, match="message_center_initial_cursor_mismatch"):
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
        "msgid": "0",
        "mintime": "0",
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
        transport_failure_kind="remote_disconnected",
        http_status_code=502,
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
    assert "命令行里的登录只是在检查 OA；浏览器登录是另外一回事" in report
    assert "如果看到登录页，就在浏览器登录" in report
    assert "登录后只查询一次" in report
    assert "完成后回到命令行运行 `./smoke.ps1 verify`" in report
    assert "任一命令失败就马上停止" in report
    assert "不要自行改文件，也不要切换运行模式" in report
    assert "传输失败细分：remote_disconnected" in report
    assert "HTTP 状态码：502" in report
    assert "messageid" in report
    assert "title" in report


def _verification_outcome(
    *,
    drift_matches: bool = True,
    normalized: bool = True,
    error_kind: str | None = None,
) -> LiveOutcome:
    fingerprint = build_structural_fingerprint({"messages": []})
    actual = (
        fingerprint
        if drift_matches
        else build_structural_fingerprint(
            {"messages": [{"added_field": "synthetic-value"}]}
        )
    )
    drift = compare_structural_fingerprints(fingerprint, actual)
    return LiveOutcome(
        drift=drift,
        protocol=ProtocolSummary(
            request_count=1,
            response_count=1,
            record_count=0,
            terminal_empty_page=True,
            cursor_chain_matches=True,
            configured_form_matches=True,
            successful_envelopes=True,
            envelope_fields=("data", "status"),
            record_field_types={},
            http_status_code=200,
        ),
        normalized=normalized,
        error_kind=error_kind,
    )


def test_verify_success_rejects_normalized_pending_fingerprint_drift() -> None:
    system = _verification_outcome()
    pending = _verification_outcome(drift_matches=False)

    assert smoke_runner._verify_success(system, pending) is False


@pytest.mark.parametrize(
    ("failed_side", "normalized", "error_kind"),
    [
        ("system", False, None),
        ("system", True, "normalization_or_structure_drift"),
        ("pending", False, None),
        ("pending", True, "normalization_or_structure_drift"),
    ],
)
def test_verify_success_rejects_non_normalized_or_classified_live_outcome(
    failed_side: str,
    normalized: bool,
    error_kind: str | None,
) -> None:
    system = _verification_outcome()
    pending = _verification_outcome(drift_matches=False)
    failed = _verification_outcome(
        drift_matches=failed_side == "system",
        normalized=normalized,
        error_kind=error_kind,
    )
    if failed_side == "system":
        system = failed
    else:
        pending = failed

    assert smoke_runner._verify_success(system, pending) is False


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (_verification_outcome(), "none"),
        (_verification_outcome(drift_matches=False), "added"),
        (
            _verification_outcome(
                drift_matches=False,
                normalized=False,
                error_kind="normalization_or_structure_drift",
            ),
            "normalization_failed",
        ),
    ],
)
def test_drift_state_fixed_classification_priority(
    outcome: LiveOutcome,
    expected: str,
) -> None:
    assert smoke_runner._drift_state(outcome) == expected


def test_drift_state_prioritizes_removed_or_changed_over_added() -> None:
    expected = build_structural_fingerprint(
        {"messages": [{"removed": "synthetic", "changed": "synthetic"}]}
    )
    actual = build_structural_fingerprint(
        {"messages": [{"added": "synthetic", "changed": 7}]}
    )
    base = _verification_outcome()
    outcome = LiveOutcome(
        drift=compare_structural_fingerprints(expected, actual),
        protocol=base.protocol,
        normalized=True,
        error_kind=None,
    )

    assert smoke_runner._drift_state(outcome) == "removed_or_changed"


def test_both_live_checks_continue_after_system_added(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    system = _verification_outcome(drift_matches=False)
    pending = _verification_outcome()

    async def run_one(
        _settings: object,
        _credential: object,
        *,
        capability: str,
    ) -> LiveOutcome:
        calls.append(capability)
        return system if capability == "system_messages" else pending

    monkeypatch.setattr(smoke_runner, "_run_one_live_check", run_one)

    result = asyncio.run(
        smoke_runner._run_both_live_checks(
            SimpleNamespace(),
            object(),
        )
    )

    assert calls == ["system_messages", "pending_workflows"]
    assert result == (system, pending)
    assert smoke_runner._drift_state(result[0]) == "added"
    assert smoke_runner._drift_state(result[1]) == "none"
    layout = Layout(
        repo_root=tmp_path,
        shared_root=tmp_path,
        base_env=tmp_path / ".env",
        smoke_env=tmp_path / ".env.smoke",
        source_har=tmp_path / "synthetic.har",
        scratch=tmp_path / "_scratch",
    )
    monkeypatch.setattr(smoke_runner, "load_runtime_environment", lambda **_kw: {})
    monkeypatch.setattr(
        smoke_runner,
        "_validate_settings",
        lambda _environment: SimpleNamespace(),
    )
    monkeypatch.setattr(
        smoke_runner,
        "_run_capability_registry_preflight",
        lambda _settings: True,
    )
    monkeypatch.setattr(
        smoke_runner,
        "_run_live_checks_with_supported_loop",
        lambda _settings: result,
    )

    return_code = smoke_runner._command_verify(
        layout,
        timestamp="20260805_132000",
        har_directory=None,
    )

    output = capsys.readouterr().out
    assert return_code == 1
    assert "system_messages_drift=added" in output
    assert "pending_workflows_drift=none" in output
    assert "system_messages_drift_added_count=1" in output
    assert "pending_workflows_drift_added_count=0" in output
    report_text = (
        layout.scratch / "smoke_result_20260805_132000.md"
    ).read_text(encoding="utf-8")
    assert "- 系统消息结构漂移：added" in report_text
    assert "- 待办结构漂移：none" in report_text


def test_both_live_checks_continue_after_unexpected_system_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    pending = _verification_outcome()

    async def run_one(
        _settings: object,
        _credential: object,
        *,
        capability: str,
    ) -> LiveOutcome:
        calls.append(capability)
        if capability == "system_messages":
            raise RuntimeError("sensitive exception content")
        return pending

    monkeypatch.setattr(smoke_runner, "_run_one_live_check", run_one)

    system_result, pending_result = asyncio.run(
        smoke_runner._run_both_live_checks(
            SimpleNamespace(),
            object(),
        )
    )

    assert calls == ["system_messages", "pending_workflows"]
    assert smoke_runner._drift_state(system_result) == "normalization_failed"
    assert system_result.error_kind == "unexpected_error"
    assert pending_result == pending


def test_verify_none_for_both_capabilities_returns_success(
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
    outcomes = (_verification_outcome(), _verification_outcome())
    monkeypatch.setattr(smoke_runner, "load_runtime_environment", lambda **_kw: {})
    monkeypatch.setattr(
        smoke_runner,
        "_validate_settings",
        lambda _environment: SimpleNamespace(),
    )
    monkeypatch.setattr(
        smoke_runner,
        "_run_capability_registry_preflight",
        lambda _settings: True,
    )
    monkeypatch.setattr(
        smoke_runner,
        "_run_live_checks_with_supported_loop",
        lambda _settings: outcomes,
    )

    result = smoke_runner._command_verify(
        layout,
        timestamp="20260805_133000",
        har_directory=None,
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "system_messages_drift=none" in output
    assert "pending_workflows_drift=none" in output
    assert "system_messages_http_status=200" in output
    assert "pending_workflows_http_status=200" in output
    report_text = (
        layout.scratch / "smoke_result_20260805_133000.md"
    ).read_text(encoding="utf-8")
    assert "- 系统消息结构漂移：none" in report_text
    assert "- 待办结构漂移：none" in report_text


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


def test_configuration_fingerprint_changes_for_llm_runtime_configuration() -> None:
    first = _configuration_fingerprint({"LLM_MODEL": "model-a"})
    second = _configuration_fingerprint({"LLM_MODEL": "model-b"})

    assert first != second


def test_code_fingerprint_changes_with_uncommitted_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diff = {"value": b"first diff"}

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        if "rev-parse" in command:
            stdout = b"synthetic-head\n"
        elif "ls-files" in command:
            stdout = b""
        else:
            stdout = diff["value"]
        return SimpleNamespace(returncode=0, stdout=stdout)

    monkeypatch.setattr(smoke_runner.subprocess, "run", run)

    first = smoke_runner._code_fingerprint(tmp_path)
    diff["value"] = b"second diff"
    second = smoke_runner._code_fingerprint(tmp_path)

    assert first != second


def test_code_fingerprint_includes_untracked_runtime_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script_path = tmp_path / "scripts" / "new_runtime_file.py"
    script_path.parent.mkdir()
    script_path.write_text("first = True\n", encoding="utf-8")

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        if "rev-parse" in command:
            stdout = b"synthetic-head\n"
        elif "ls-files" in command:
            stdout = b"scripts/new_runtime_file.py\x00"
        else:
            stdout = b""
        return SimpleNamespace(returncode=0, stdout=stdout)

    monkeypatch.setattr(smoke_runner.subprocess, "run", run)

    first = smoke_runner._code_fingerprint(tmp_path)
    script_path.write_text("first = False\n", encoding="utf-8")
    second = smoke_runner._code_fingerprint(tmp_path)

    assert first != second


def test_owned_service_reuse_requires_same_candidate_and_process_identity(
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
    identity = "b" * 64
    candidate = "c" * 64
    (layout.scratch / "smoke_processes.json").write_text(
        json.dumps(
            {
                "state_version": smoke_runner._PROCESS_STATE_VERSION,
                "backend_pid": 40001,
                "backend_identity_sha256": identity,
                "backend_candidate_sha256": candidate,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        smoke_runner,
        "_process_identity",
        lambda _pid: identity,
    )

    assert (
        smoke_runner._owned_service_status(layout, "backend_pid", candidate)
        == "reusable"
    )
    assert (
        smoke_runner._owned_service_status(layout, "backend_pid", "d" * 64)
        == "stale_owned"
    )

    monkeypatch.setattr(
        smoke_runner,
        "_process_identity",
        lambda _pid: "e" * 64,
    )
    assert (
        smoke_runner._owned_service_status(layout, "backend_pid", candidate)
        == "unowned"
    )


def test_pid_identity_mismatch_never_invokes_process_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        smoke_runner,
        "_process_identity",
        lambda _pid: "current-process-identity",
    )
    monkeypatch.setattr(
        smoke_runner.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "PID reuse must not terminate the current process"
        ),
    )

    terminated = smoke_runner._terminate_owned_pid(
        40001,
        "recorded-old-process-identity",
    )

    assert terminated is False


def test_restarting_backend_does_not_mark_old_frontend_as_current(
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
    old_candidate = "a" * 64
    new_candidate = "b" * 64
    backend_identity = "c" * 64
    frontend_identity = "d" * 64
    (layout.scratch / "smoke_processes.json").write_text(
        json.dumps(
            {
                "state_version": smoke_runner._PROCESS_STATE_VERSION,
                "backend_pid": 40001,
                "backend_identity_sha256": backend_identity,
                "backend_candidate_sha256": old_candidate,
                "frontend_pid": 40002,
                "frontend_identity_sha256": frontend_identity,
                "frontend_candidate_sha256": old_candidate,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        smoke_runner,
        "_process_identity",
        lambda pid: backend_identity if pid == 40001 else frontend_identity,
    )
    monkeypatch.setattr(smoke_runner, "_terminate_owned_pid", lambda *_args: True)

    smoke_runner._restart_stale_owned_service(
        layout,
        "backend_pid",
        {},
        new_candidate,
        ready=lambda: False,
    )

    state = json.loads(
        (layout.scratch / "smoke_processes.json").read_text(encoding="utf-8")
    )
    assert state["backend_candidate_sha256"] is None
    assert state["frontend_candidate_sha256"] == old_candidate
    assert (
        smoke_runner._owned_service_status(
            layout,
            "frontend_pid",
            new_candidate,
        )
        == "stale_owned"
    )


def _legacy_process_layout(tmp_path: Path) -> Layout:
    layout = Layout(
        repo_root=tmp_path,
        shared_root=tmp_path,
        base_env=tmp_path / ".env",
        smoke_env=tmp_path / ".env.smoke",
        source_har=tmp_path / "synthetic.har",
        scratch=tmp_path / "_scratch",
    )
    layout.scratch.mkdir()
    (layout.scratch / "smoke_processes.json").write_text(
        json.dumps({"backend_pid": 40001, "frontend_pid": 40002}),
        encoding="utf-8",
    )
    return layout


def test_legacy_backend_state_never_terminates_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = _legacy_process_layout(tmp_path)
    monkeypatch.setattr(smoke_runner, "_backend_health", lambda: (True, ()))
    monkeypatch.setattr(
        smoke_runner,
        "_terminate_owned_pid",
        lambda *_args: pytest.fail("legacy PID must never be terminated"),
    )

    with pytest.raises(SmokeError) as error:
        smoke_runner._start_backend(layout, {}, "a" * 64)

    assert error.value.code == "backend_already_running_not_owned"


def test_legacy_frontend_state_never_terminates_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = _legacy_process_layout(tmp_path)
    monkeypatch.setattr(smoke_runner, "_frontend_ready", lambda: True)
    monkeypatch.setattr(
        smoke_runner,
        "_terminate_owned_pid",
        lambda *_args: pytest.fail("legacy PID must never be terminated"),
    )

    with pytest.raises(SmokeError) as error:
        smoke_runner._start_frontend(layout, {}, "a" * 64)

    assert error.value.code == "frontend_already_running_not_owned"


@pytest.mark.parametrize(
    ("service", "expected_code"),
    [
        ("backend", "backend_port_in_use_unhealthy"),
        ("frontend", "frontend_port_in_use_unhealthy"),
    ],
)
def test_unhealthy_occupied_port_has_fixed_failure_without_spawn_or_terminate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    expected_code: str,
) -> None:
    layout = _legacy_process_layout(tmp_path)
    monkeypatch.setattr(smoke_runner, "_backend_health", lambda: (False, ()))
    monkeypatch.setattr(smoke_runner, "_frontend_ready", lambda: False)
    monkeypatch.setattr(smoke_runner, "_local_port_in_use", lambda _port: True)
    monkeypatch.setattr(
        smoke_runner,
        "_spawn_service",
        lambda *_args, **_kwargs: pytest.fail("occupied port must not spawn"),
    )
    monkeypatch.setattr(
        smoke_runner,
        "_terminate_owned_pid",
        lambda *_args: pytest.fail("unhealthy unowned process must not terminate"),
    )

    with pytest.raises(SmokeError) as error:
        if service == "backend":
            smoke_runner._start_backend(layout, {}, "a" * 64)
        else:
            smoke_runner._start_frontend(layout, {}, "a" * 64)

    assert error.value.code == expected_code


def test_new_processes_upgrade_legacy_state_to_v3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = _legacy_process_layout(tmp_path)
    backend = SimpleNamespace(pid=41001)
    frontend = SimpleNamespace(pid=41002)
    identities = {41001: "b" * 64, 41002: "c" * 64}
    spawned = iter((backend, frontend))
    monkeypatch.setattr(smoke_runner, "_backend_health", lambda: (False, ()))
    monkeypatch.setattr(smoke_runner, "_frontend_ready", lambda: False)
    monkeypatch.setattr(smoke_runner, "_local_port_in_use", lambda _port: False)
    monkeypatch.setattr(smoke_runner.shutil, "which", lambda _name: "npm.cmd")
    monkeypatch.setattr(
        smoke_runner,
        "_spawn_service",
        lambda *_args, **_kwargs: next(spawned),
    )
    monkeypatch.setattr(
        smoke_runner,
        "_process_identity",
        lambda pid: identities.get(pid),
    )

    started_backend = smoke_runner._start_backend(layout, {}, "d" * 64)
    started_frontend = smoke_runner._start_frontend(layout, {}, "d" * 64)
    smoke_runner._write_process_state(
        layout,
        started_backend,
        started_frontend,
        {"LLM_MODEL": "synthetic-model"},
        "d" * 64,
    )

    state = json.loads(
        (layout.scratch / "smoke_processes.json").read_text(encoding="utf-8")
    )
    assert state["state_version"] == smoke_runner._PROCESS_STATE_VERSION
    assert state["backend_identity_sha256"] == identities[41001]
    assert state["frontend_identity_sha256"] == identities[41002]
    assert state["backend_candidate_sha256"] == "d" * 64
    assert state["frontend_candidate_sha256"] == "d" * 64


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
    monkeypatch.setattr(
        smoke_runner,
        "_candidate_fingerprint",
        lambda *_args: "a" * 64,
    )
    monkeypatch.setattr(
        smoke_runner,
        "_validate_settings",
        lambda _environment: SimpleNamespace(
            oa_base_url="https://synthetic.invalid"
        ),
    )
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
    monkeypatch.setattr(
        smoke_runner,
        "_run_capability_registry_preflight",
        lambda _settings: True,
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
        lambda _oa_base_url, **_kwargs: failed_stage != "login",
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
    monkeypatch.setattr(
        smoke_runner,
        "_candidate_fingerprint",
        lambda *_args: "a" * 64,
    )
    monkeypatch.setattr(
        smoke_runner,
        "_validate_settings",
        lambda _environment: SimpleNamespace(
            oa_base_url="https://synthetic.invalid"
        ),
    )
    monkeypatch.setattr(smoke_runner, "_start_backend", lambda *_args: backend)
    monkeypatch.setattr(
        smoke_runner,
        "_wait_for_backend",
        lambda *_args, **_kwargs: (True, None),
    )
    monkeypatch.setattr(
        smoke_runner,
        "_run_capability_registry_preflight",
        lambda _settings: True,
    )
    monkeypatch.setattr(smoke_runner, "_write_process_state", lambda *_args: None)
    monkeypatch.setattr(smoke_runner, "_start_frontend", lambda *_args: frontend)
    monkeypatch.setattr(smoke_runner, "_wait_for_frontend", lambda _process: True)
    monkeypatch.setattr(
        smoke_runner,
        "_cold_login_preflight",
        lambda _oa_base_url, **_kwargs: True,
    )
    monkeypatch.setattr(
        smoke_runner,
        "_cleanup_failed_start",
        lambda *_args: pytest.fail("successful start must not clean up services"),
    )

    result = smoke_runner._command_start(layout)

    output = capsys.readouterr().out
    assert result == 0
    assert "命令行里的登录只是在检查 OA；浏览器登录是另外一回事" in output
    assert "如果看到登录页，就在浏览器登录" in output
    assert "登录后只查询一次" in output
    assert "完成后回到命令行运行 .\\smoke.ps1 verify" in output


def test_start_registry_preflight_failure_stops_before_frontend_and_login(
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
    backend = _FakeProcess(44001)
    cleanups: list[tuple[object | None, object | None]] = []
    monkeypatch.setattr(smoke_runner, "load_runtime_environment", lambda **_kw: {})
    monkeypatch.setattr(
        smoke_runner,
        "_candidate_fingerprint",
        lambda *_args: "a" * 64,
    )
    monkeypatch.setattr(
        smoke_runner,
        "_validate_settings",
        lambda _environment: SimpleNamespace(),
    )
    monkeypatch.setattr(smoke_runner, "_start_backend", lambda *_args: backend)
    monkeypatch.setattr(
        smoke_runner,
        "_wait_for_backend",
        lambda *_args, **_kwargs: (True, None),
    )
    monkeypatch.setattr(
        smoke_runner,
        "_run_capability_registry_preflight",
        lambda _settings: False,
    )
    monkeypatch.setattr(
        smoke_runner,
        "_start_frontend",
        lambda *_args: pytest.fail("failed Registry preflight must stop before frontend"),
    )
    monkeypatch.setattr(
        smoke_runner,
        "_cold_login_preflight",
        lambda *_args, **_kwargs: pytest.fail(
            "failed Registry preflight must stop before credential input"
        ),
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
    assert cleanups == [(backend, None)]
    assert "请停止操作" in capsys.readouterr().out


def _capability_copy(
    capability: CapabilitySpec,
    **updates: object,
) -> CapabilitySpec:
    return capability.model_copy(update=updates)


@pytest.mark.parametrize(
    (
        "catalog",
        "expected_state",
        "expected_found",
        "expected_valid",
        "expected_unexpected",
    ),
    [
        ((expected_oa_capabilities()[0],), "missing", 1, 1, 0),
        (
            (
                _capability_copy(
                    expected_oa_capabilities()[0],
                    status="disabled",
                ),
                expected_oa_capabilities()[1],
            ),
            "inactive",
            2,
            1,
            0,
        ),
        (
            (
                _capability_copy(
                    expected_oa_capabilities()[0],
                    short_description="wrong contract",
                ),
                expected_oa_capabilities()[1],
            ),
            "contract_mismatch",
            2,
            1,
            0,
        ),
        (
            expected_oa_capabilities()
            + (
                _capability_copy(
                    expected_oa_capabilities()[0],
                    capability_id="oa.unsupported_placeholder",
                ),
            ),
            "unexpected_active",
            2,
            2,
            1,
        ),
    ],
)
def test_registry_preflight_fails_closed_for_invalid_or_extra_active_capability(
    catalog: tuple[CapabilitySpec, ...],
    expected_state: str,
    expected_found: int,
    expected_valid: int,
    expected_unexpected: int,
) -> None:
    result = smoke_runner._classify_capability_registry(catalog)

    assert result.state == expected_state
    assert result.found_count == expected_found
    assert result.valid_count == expected_valid
    assert result.unexpected_active_count == expected_unexpected


def test_registry_preflight_passes_only_exact_runtime_visible_contracts() -> None:
    result = smoke_runner._classify_capability_registry(
        expected_oa_capabilities()
    )

    assert result.state == "passed"
    assert result.active_total_count == 2
    assert result.visible_probe_count == 2


def test_registry_preflight_rejects_required_capabilities_truncated_from_context() -> None:
    pending = expected_oa_capabilities()[0]
    earlier_capabilities = tuple(
        _capability_copy(
            pending,
            capability_id=f"aaa.synthetic.{index}",
            target_system="u8",
        )
        for index in range(7)
    )

    result = smoke_runner._classify_capability_registry(
        earlier_capabilities + expected_oa_capabilities()
    )

    assert result.state == "context_truncated"
    assert result.active_total_count == 9
    assert result.visible_probe_count == 0


class _FakeRegistryEngine:
    def __init__(self) -> None:
        self.dispose_count = 0

    async def dispose(self) -> None:
        self.dispose_count += 1


class _FakeRegistrySnapshot:
    def __init__(
        self,
        catalog: tuple[CapabilitySpec, ...] = (),
        *,
        failure: str | None = None,
    ) -> None:
        self.catalog = catalog
        self.failure = failure
        self.list_count = 0

    async def list(self) -> list[CapabilitySpec]:
        self.list_count += 1
        if self.failure == "payload":
            CapabilitySpec.model_validate({})
        if self.failure == "connection":
            raise OSError("synthetic private endpoint")
        if self.failure == "database_query":
            raise DBAPIError(
                "synthetic statement",
                {},
                RuntimeError("synthetic query failure"),
                False,
            )
        if self.failure == "inspection":
            raise RuntimeError("synthetic registry implementation detail")
        return list(self.catalog)


def _inspect_fake_registry(
    monkeypatch: pytest.MonkeyPatch,
    registry: _FakeRegistrySnapshot,
) -> tuple[smoke_runner.CapabilityRegistryPreflight, _FakeRegistryEngine]:
    engine = _FakeRegistryEngine()
    monkeypatch.setattr(smoke_runner, "make_async_engine", lambda _url: engine)
    monkeypatch.setattr(
        smoke_runner,
        "make_async_session_factory",
        lambda _engine: object(),
    )
    monkeypatch.setattr(
        smoke_runner,
        "PostgreSQLCapabilityRegistry",
        lambda _session_factory: registry,
    )
    result = asyncio.run(
        smoke_runner._inspect_capability_registry(
            SimpleNamespace(database_url="synthetic-database-url")
        )
    )
    return result, engine


def test_registry_inspection_uses_one_catalog_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _FakeRegistrySnapshot(expected_oa_capabilities())

    result, engine = _inspect_fake_registry(monkeypatch, registry)

    assert result.state == "passed"
    assert registry.list_count == 1
    assert engine.dispose_count == 1


@pytest.mark.parametrize(
    ("failure", "expected_state"),
    [
        ("payload", "registry_payload_invalid"),
        ("connection", "connection_failed"),
        ("database_query", "registry_inspection_failed"),
        ("inspection", "registry_inspection_failed"),
    ],
)
def test_registry_inspection_keeps_safe_failure_categories(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_state: str,
) -> None:
    registry = _FakeRegistrySnapshot(failure=failure)

    result, engine = _inspect_fake_registry(monkeypatch, registry)

    assert result.state == expected_state
    assert result.found_count == 0
    assert registry.list_count == 1
    assert engine.dispose_count == 1


def test_verify_registry_preflight_failure_stops_before_live_checks(
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
    monkeypatch.setattr(smoke_runner, "load_runtime_environment", lambda **_kw: {})
    monkeypatch.setattr(
        smoke_runner,
        "_validate_settings",
        lambda _environment: SimpleNamespace(),
    )
    monkeypatch.setattr(
        smoke_runner,
        "_run_capability_registry_preflight",
        lambda _settings: False,
    )
    monkeypatch.setattr(
        smoke_runner,
        "_run_live_checks_with_supported_loop",
        lambda _settings: pytest.fail("failed Registry preflight must stop verify"),
    )

    result = smoke_runner._command_verify(
        layout,
        timestamp="20260804_150000",
        har_directory=None,
    )

    assert result == 1
    assert "请停止操作" in capsys.readouterr().out
    assert not layout.scratch.exists()


def test_verify_pending_normalization_failure_returns_nonzero(
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
    system = _verification_outcome()
    pending = _verification_outcome(
        drift_matches=False,
        normalized=False,
        error_kind="normalization_or_structure_drift",
    )
    monkeypatch.setattr(smoke_runner, "load_runtime_environment", lambda **_kw: {})
    monkeypatch.setattr(
        smoke_runner,
        "_validate_settings",
        lambda _environment: SimpleNamespace(),
    )
    monkeypatch.setattr(
        smoke_runner,
        "_run_capability_registry_preflight",
        lambda _settings: True,
    )
    monkeypatch.setattr(
        smoke_runner,
        "_run_live_checks_with_supported_loop",
        lambda _settings: (system, pending),
    )

    result = smoke_runner._command_verify(
        layout,
        timestamp="20260805_130000",
        har_directory=None,
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "pending_workflows_drift=normalization_failed" in output
    assert "pending_workflows_drift=added" not in output
    assert "请停止操作" in output
    assert (layout.scratch / "smoke_result_20260805_130000.md").is_file()


def test_verify_prints_deterministic_value_free_drift_nodes(
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
    added_value_canary = f"sensitive-added-{secrets.token_hex(24)}"
    removed_value_canary = f"sensitive-removed-{secrets.token_hex(24)}"
    changed_value_canary = f"sensitive-changed-{secrets.token_hex(24)}"
    pending_value_canary = f"sensitive-pending-{secrets.token_hex(24)}"
    expected = build_structural_fingerprint(
        {
            "messages": [
                {
                    "removed_a": removed_value_canary,
                    "removed_z": removed_value_canary,
                    "changed_array": [changed_value_canary],
                    "changed_nullable": changed_value_canary,
                },
                {
                    "changed_array": [changed_value_canary],
                    "changed_nullable": changed_value_canary,
                },
            ]
        }
    )
    actual = build_structural_fingerprint(
        {
            "messages": [
                {
                    "added_a": added_value_canary,
                    "added_z": added_value_canary,
                    "changed_array": [7],
                    "changed_nullable": changed_value_canary,
                },
                {
                    "changed_array": [7],
                    "changed_nullable": None,
                },
            ]
        }
    )
    system = _verification_outcome()
    system = LiveOutcome(
        drift=compare_structural_fingerprints(expected, actual),
        protocol=replace(
            system.protocol,
            record_field_types={
                "sensitiveFieldCanary": ("string",),
                "title": ("string",),
            },
        ),
        normalized=True,
        error_kind=None,
    )
    pending_expected = build_structural_fingerprint({"messages": []})
    pending_actual = build_structural_fingerprint(
        {"messages": [{"pending_added": pending_value_canary}]}
    )
    pending_base = _verification_outcome()
    pending = LiveOutcome(
        drift=compare_structural_fingerprints(
            pending_expected,
            pending_actual,
        ),
        protocol=pending_base.protocol,
        normalized=True,
        error_kind=None,
    )
    monkeypatch.setattr(smoke_runner, "load_runtime_environment", lambda **_kw: {})
    monkeypatch.setattr(
        smoke_runner,
        "_validate_settings",
        lambda _environment: SimpleNamespace(),
    )
    monkeypatch.setattr(
        smoke_runner,
        "_run_capability_registry_preflight",
        lambda _settings: True,
    )
    monkeypatch.setattr(
        smoke_runner,
        "_run_live_checks_with_supported_loop",
        lambda _settings: (system, pending),
    )

    result = smoke_runner._command_verify(
        layout,
        timestamp="20260805_131500",
        har_directory=None,
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "system_messages_drift=removed_or_changed" in output
    assert "pending_workflows_drift=added" in output
    assert (
        'system_messages_drift_added_001={"array_shape":null,'
        '"json_type":"string","nullable":false,'
        '"path":"$.messages[].added_a"}'
    ) in output
    assert (
        'system_messages_drift_added_002={"array_shape":null,'
        '"json_type":"string","nullable":false,'
        '"path":"$.messages[].added_z"}'
    ) in output
    assert (
        'system_messages_drift_removed_001={"array_shape":null,'
        '"json_type":"string","nullable":false,'
        '"path":"$.messages[].removed_a"}'
    ) in output
    assert (
        'system_messages_drift_removed_002={"array_shape":null,'
        '"json_type":"string","nullable":false,'
        '"path":"$.messages[].removed_z"}'
    ) in output
    assert (
        'system_messages_drift_changed_001={"actual":'
        '{"array_shape":"items:integer","json_type":"array",'
        '"nullable":false,"path":"$.messages[].changed_array"},'
        '"expected":{"array_shape":"items:string","json_type":"array",'
        '"nullable":false,"path":"$.messages[].changed_array"}}'
    ) in output
    assert (
        'system_messages_drift_changed_002={"actual":'
        '{"array_shape":null,"json_type":"integer","nullable":false,'
        '"path":"$.messages[].changed_array[]"},"expected":'
        '{"array_shape":null,"json_type":"string","nullable":false,'
        '"path":"$.messages[].changed_array[]"}}'
    ) in output
    assert (
        'system_messages_drift_changed_003={"actual":'
        '{"array_shape":null,"json_type":"string","nullable":true,'
        '"path":"$.messages[].changed_nullable"},"expected":'
        '{"array_shape":null,"json_type":"string","nullable":false,'
        '"path":"$.messages[].changed_nullable"}}'
    ) in output
    assert "system_messages_drift_added_count=2" in output
    assert "system_messages_drift_removed_count=2" in output
    assert "system_messages_drift_changed_count=3" in output
    assert "pending_workflows_drift_added_count=1" in output
    assert (
        'pending_workflows_drift_added_001={"array_shape":null,'
        '"json_type":"string","nullable":false,'
        '"path":"$.messages[].pending_added"}'
    ) in output
    assert "pending_workflows_drift_removed_count=0" in output
    assert "pending_workflows_drift_changed_count=0" in output
    assert "system_messages_http_status=200" in output
    assert "pending_workflows_http_status=200" in output
    assert output.index("system_messages_drift_added_001=") < output.index(
        "system_messages_drift_added_002="
    )
    assert output.index("system_messages_drift_removed_001=") < output.index(
        "system_messages_drift_removed_002="
    )
    assert output.index("system_messages_drift_changed_001=") < output.index(
        "system_messages_drift_changed_002="
    ) < output.index("system_messages_drift_changed_003=")
    report_text = (
        layout.scratch / "smoke_result_20260805_131500.md"
    ).read_text(encoding="utf-8")
    expected_report_lines = (
        '- 新增结构节点 001：{"array_shape":null,'
        '"json_type":"string","nullable":false,'
        '"path":"$.messages[].added_a"}',
        '- 新增结构节点 002：{"array_shape":null,'
        '"json_type":"string","nullable":false,'
        '"path":"$.messages[].added_z"}',
        '- 缺失结构节点 001：{"array_shape":null,'
        '"json_type":"string","nullable":false,'
        '"path":"$.messages[].removed_a"}',
        '- 缺失结构节点 002：{"array_shape":null,'
        '"json_type":"string","nullable":false,'
        '"path":"$.messages[].removed_z"}',
        '- 变化结构节点 001：{"actual":'
        '{"array_shape":"items:integer","json_type":"array",'
        '"nullable":false,"path":"$.messages[].changed_array"},'
        '"expected":{"array_shape":"items:string","json_type":"array",'
        '"nullable":false,"path":"$.messages[].changed_array"}}',
        '- 变化结构节点 002：{"actual":'
        '{"array_shape":null,"json_type":"integer","nullable":false,'
        '"path":"$.messages[].changed_array[]"},"expected":'
        '{"array_shape":null,"json_type":"string","nullable":false,'
        '"path":"$.messages[].changed_array[]"}}',
        '- 变化结构节点 003：{"actual":'
        '{"array_shape":null,"json_type":"string","nullable":true,'
        '"path":"$.messages[].changed_nullable"},"expected":'
        '{"array_shape":null,"json_type":"string","nullable":false,'
        '"path":"$.messages[].changed_nullable"}}',
    )
    assert "- 新增结构节点数：2" in report_text
    assert "- 缺失结构节点数：2" in report_text
    assert "- 变化结构节点数：3" in report_text
    assert (
        '- 新增结构节点 001：{"array_shape":null,'
        '"json_type":"string","nullable":false,'
        '"path":"$.messages[].pending_added"}'
    ) in report_text
    for line in expected_report_lines:
        assert line in report_text
    assert report_text.index(expected_report_lines[0]) < report_text.index(
        expected_report_lines[1]
    )
    assert report_text.index(expected_report_lines[2]) < report_text.index(
        expected_report_lines[3]
    )
    assert report_text.index(expected_report_lines[4]) < report_text.index(
        expected_report_lines[5]
    ) < report_text.index(expected_report_lines[6])
    for canary in (
        added_value_canary,
        removed_value_canary,
        changed_value_canary,
        pending_value_canary,
        "sensitiveFieldCanary",
    ):
        assert canary not in output
        assert canary not in report_text


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


def test_spawn_service_discards_old_configuration_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "smoke_backend.log"
    log_path.write_text("RuntimeError: OA_BASE_URL is required", encoding="utf-8")
    process = _FakeProcess(44001)
    monkeypatch.setattr(
        smoke_runner.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )

    spawned = smoke_runner._spawn_service(
        ["synthetic-service"],
        cwd=tmp_path,
        environment={},
        log_path=log_path,
    )
    classification = _classify_backend_failure(
        failed_checks=(),
        process_exited=True,
        log_path=log_path,
    )

    assert spawned is process
    assert log_path.read_bytes() == b""
    assert classification == "process_exited"


def test_cold_login_unreachable_stops_before_credential_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        smoke_runner,
        "_oa_endpoint_reachable",
        lambda _base_url: False,
    )
    monkeypatch.setattr(
        smoke_runner,
        "_prompt_credentials",
        lambda: pytest.fail("unreachable OA must not prompt for credentials"),
    )

    result = smoke_runner._cold_login_preflight(
        "https://synthetic.invalid",
        backend_log_path=tmp_path / "backend.log",
    )

    assert result is False
    assert capsys.readouterr().out.splitlines() == [
        "oa_reachability=false",
        "cold_login_preflight=connection_failed",
    ]


def test_latest_authentication_failure_stage_uses_only_last_allowlisted_marker(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "oa_authentication_failure_stage=oa_rsa_request_failed\n"
        "oa_authentication_failure_stage=untrusted_raw_detail\n"
        "oa_authentication_failure_stage=oa_credentials_rejected\n",
        encoding="utf-8",
    )

    stage = smoke_runner._latest_authentication_failure_stage(log_path)

    assert stage == "oa_credentials_rejected"


def test_latest_authentication_failure_stage_does_not_reuse_old_marker(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "oa_authentication_failure_stage=oa_credentials_rejected\n",
        encoding="utf-8",
    )
    start_offset = log_path.stat().st_size
    with log_path.open("a", encoding="utf-8") as writer:
        writer.write("unrelated current request line\n")

    stage = smoke_runner._latest_authentication_failure_stage(
        log_path,
        start_offset=start_offset,
    )

    assert stage is None


def test_latest_authentication_failure_diagnostics_are_allowlisted_shape_only(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "oa_authentication_failure_stage=oa_rsa_flag_type_invalid\n"
        "oa_authentication_failure_diagnostic_rsa_response_field_count=3\n"
        "oa_authentication_failure_diagnostic_rsa_pub_present=true\n"
        "oa_authentication_failure_diagnostic_rsa_pub_type=string\n"
        "oa_authentication_failure_diagnostic_rsa_pub_character_count=392\n"
        "oa_authentication_failure_diagnostic_rsa_code_present=true\n"
        "oa_authentication_failure_diagnostic_rsa_code_type=string\n"
        "oa_authentication_failure_diagnostic_rsa_code_character_count=8\n"
        "oa_authentication_failure_diagnostic_rsa_flag_present=true\n"
        "oa_authentication_failure_diagnostic_rsa_flag_type=string\n"
        "oa_authentication_failure_diagnostic_rsa_flag_character_count=7\n"
        "oa_authentication_failure_diagnostic_untrusted=secret\n"
        "oa_authentication_failure_diagnostic_rsa_pub_type=secret\n"
        "oa_authentication_failure_diagnostic_rsa_pub_character_count="
        "12345678\n",
        encoding="utf-8",
    )

    diagnostics = smoke_runner._latest_authentication_failure_diagnostics(log_path)

    assert diagnostics == {
        "rsa_response_field_count": "3",
        "rsa_pub_present": "true",
        "rsa_code_present": "true",
        "rsa_code_type": "string",
        "rsa_code_character_count": "8",
        "rsa_flag_present": "true",
        "rsa_flag_type": "string",
        "rsa_flag_character_count": "7",
    }


def test_latest_authentication_failure_diagnostics_do_not_reuse_old_marker(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "oa_authentication_failure_stage=oa_rsa_flag_type_invalid\n"
        "oa_authentication_failure_diagnostic_rsa_response_field_count=3\n",
        encoding="utf-8",
    )
    start_offset = log_path.stat().st_size
    with log_path.open("a", encoding="utf-8") as writer:
        writer.write("unrelated current request line\n")

    diagnostics = smoke_runner._latest_authentication_failure_diagnostics(
        log_path,
        start_offset=start_offset,
    )

    assert diagnostics == {}


def test_cold_login_prints_current_backend_failure_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "oa_authentication_failure_stage=oa_rsa_request_failed\n",
        encoding="utf-8",
    )

    class _FailedLoginResponse:
        def __enter__(self) -> _FailedLoginResponse:
            with log_path.open("a", encoding="utf-8") as writer:
                writer.write(
                    "oa_authentication_failure_stage=oa_rsa_flag_type_invalid\n"
                    "oa_authentication_failure_diagnostic_"
                    "rsa_response_field_count=3\n"
                    "oa_authentication_failure_diagnostic_rsa_flag_present=true\n"
                    "oa_authentication_failure_diagnostic_rsa_flag_type=integer\n"
                    "oa_authentication_failure_diagnostic_untrusted=secret\n"
                )
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return b'{"authenticated": false}'

    monkeypatch.setattr(smoke_runner, "_oa_endpoint_reachable", lambda _url: True)
    monkeypatch.setattr(
        smoke_runner,
        "_prompt_credentials",
        lambda: ("synthetic-account", "synthetic-password"),
    )
    monkeypatch.setattr(
        smoke_runner._LOCAL_OPENER,
        "open",
        lambda *_args, **_kwargs: _FailedLoginResponse(),
    )

    result = smoke_runner._cold_login_preflight(
        "https://synthetic.invalid",
        backend_log_path=log_path,
    )

    output = capsys.readouterr().out
    assert result is False
    assert "cold_login_preflight=oa_rsa_flag_type_invalid" in output
    assert "rsa_response_field_count=3" in output
    assert "rsa_flag_present=true" in output
    assert "rsa_flag_type=integer" in output
    assert "untrusted" not in output
    assert "secret" not in output
    assert "oa_rsa_request_failed" not in output
    assert "synthetic-account" not in output
    assert "synthetic-password" not in output


def test_authentication_failure_output_is_fixed_and_value_free(
    capsys: pytest.CaptureFixture[str],
) -> None:
    smoke_runner._print_authentication_failure(
        "oa_credentials_rejected",
        result_name="cold_login_preflight",
    )

    output = capsys.readouterr().out
    assert "cold_login_preflight=oa_credentials_rejected" in output
    assert "OA 登录接口明确拒绝了本次账号密码" in output
    assert "loginid=" not in output
    assert "userpassword=" not in output


def test_prompt_credentials_prints_only_input_shape(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    values = iter((" synthetic-account ", "synthetic-password"))
    monkeypatch.setattr(smoke_runner.getpass, "getpass", lambda _prompt: next(values))

    account, password = smoke_runner._prompt_credentials()

    output = capsys.readouterr().out
    assert account == " synthetic-account "
    assert password == "synthetic-password"
    assert "account_input_characters=19" in output
    assert "password_input_characters=18" in output
    assert "account_has_outer_whitespace=true" in output
    assert "password_has_outer_whitespace=false" in output
    assert account not in output
    assert password not in output


def test_verify_unreachable_stops_before_credential_prompt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        smoke_runner,
        "_oa_endpoint_reachable",
        lambda _base_url: False,
    )
    monkeypatch.setattr(
        smoke_runner,
        "_prompt_credentials",
        lambda: pytest.fail("unreachable OA must not prompt for credentials"),
    )

    with pytest.raises(SmokeError, match="oa_unreachable"):
        asyncio.run(
            smoke_runner._run_live_checks(
                SimpleNamespace(oa_base_url="https://synthetic.invalid")
            )
        )

    assert capsys.readouterr().out.splitlines() == ["oa_reachability=false"]


def test_verify_uses_production_supported_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop_factory_calls = 0
    running_loop_is_selector: list[bool] = []
    expected = (object(), object())

    def supported_loop_factory() -> asyncio.AbstractEventLoop:
        nonlocal loop_factory_calls
        loop_factory_calls += 1
        return asyncio.SelectorEventLoop()

    async def fake_live_checks(_settings: object) -> tuple[object, object]:
        running_loop_is_selector.append(
            isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)
        )
        return expected

    monkeypatch.setattr(smoke_runner, "make_event_loop", supported_loop_factory)
    monkeypatch.setattr(smoke_runner, "_run_live_checks", fake_live_checks)

    result = smoke_runner._run_live_checks_with_supported_loop(  # type: ignore[arg-type]
        object()
    )

    assert result == expected
    assert loop_factory_calls == 1
    assert running_loop_is_selector == [True]


def test_recording_opener_classifies_disconnect_without_detail_leak() -> None:
    marker = "synthetic-sensitive-transport-detail"
    evidence = ProtocolEvidence(expected_form={})
    opener = RecordingOpener(evidence)

    class _DisconnectingOpener:
        def open(self, _request: Request, timeout: float) -> object:
            assert timeout > 0
            raise RemoteDisconnected(marker)

    opener._opener = _DisconnectingOpener()  # type: ignore[assignment]

    with pytest.raises(RemoteDisconnected):
        opener.open(Request("https://synthetic.invalid"), timeout=3)

    summary = evidence.summary()
    assert summary.request_count == 1
    assert summary.transport_failure_kind == "remote_disconnected"
    assert summary.http_status_code is None
    assert marker not in repr(summary)


def test_recording_opener_records_only_http_error_status() -> None:
    marker = "synthetic-sensitive-http-detail"
    evidence = ProtocolEvidence(expected_form={})
    opener = RecordingOpener(evidence)

    class _FailingOpener:
        def open(self, request: Request, timeout: float) -> object:
            assert timeout > 0
            raise HTTPError(request.full_url, 503, marker, None, None)

    opener._opener = _FailingOpener()  # type: ignore[assignment]

    with pytest.raises(HTTPError):
        opener.open(Request("https://synthetic.invalid"), timeout=3)

    summary = evidence.summary()
    assert summary.request_count == 1
    assert summary.response_count == 0
    assert summary.http_status_code == 503
    assert marker not in repr(summary)


def test_verify_reachable_authentication_error_is_classified_without_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _FakeEngine:
        async def dispose(self) -> None:
            return None

    class _FakeAuthentication:
        async def authenticate(self, _credential: object) -> object:
            raise AuthenticationError("synthetic-sensitive-detail")

    settings = SimpleNamespace(
        oa_base_url="https://synthetic.invalid",
        database_url="postgresql://synthetic.invalid/db",
        credential_encryption_key="synthetic-encryption-key",
        oa_timeout_seconds=5,
        identity_hmac_key="synthetic-hmac-key",
        oa_credential_ttl_seconds=60,
    )
    monkeypatch.setattr(
        smoke_runner,
        "_oa_endpoint_reachable",
        lambda _base_url: True,
    )
    monkeypatch.setattr(
        smoke_runner,
        "_prompt_credentials",
        lambda: ("synthetic-account", "synthetic-password"),
    )
    monkeypatch.setattr(smoke_runner, "make_async_engine", lambda _url: _FakeEngine())
    monkeypatch.setattr(
        smoke_runner,
        "make_async_session_factory",
        lambda _engine: object(),
    )
    monkeypatch.setattr(smoke_runner, "build_credential_store", lambda **_kwargs: object())
    monkeypatch.setattr(
        smoke_runner,
        "build_principal_role_reader",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        smoke_runner,
        "build_authentication_port",
        lambda **_kwargs: _FakeAuthentication(),
    )

    with pytest.raises(SmokeError, match="authentication_failed") as exc_info:
        asyncio.run(smoke_runner._run_live_checks(settings))

    output = capsys.readouterr().out
    assert output.splitlines() == ["oa_reachability=true"]
    assert "synthetic-sensitive-detail" not in str(exc_info.value)


def test_verify_typed_authentication_error_prints_fixed_stage_without_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _FakeEngine:
        async def dispose(self) -> None:
            return None

    class _FakeAuthentication:
        async def authenticate(self, _credential: object) -> object:
            raise OAAuthenticationError(
                "oa_rsa_flag_type_invalid",
                diagnostics={
                    "rsa_response_field_count": "3",
                    "rsa_flag_present": "true",
                    "rsa_flag_type": "string",
                    "rsa_flag_character_count": "7",
                    "untrusted": "synthetic-sensitive-detail",
                },
            )

    settings = SimpleNamespace(
        oa_base_url="https://synthetic.invalid",
        database_url="postgresql://synthetic.invalid/db",
        credential_encryption_key="synthetic-encryption-key",
        oa_timeout_seconds=5,
        identity_hmac_key="synthetic-hmac-key",
        oa_credential_ttl_seconds=60,
    )
    monkeypatch.setattr(smoke_runner, "_oa_endpoint_reachable", lambda _url: True)
    monkeypatch.setattr(
        smoke_runner,
        "_prompt_credentials",
        lambda: ("synthetic-account", "synthetic-password"),
    )
    monkeypatch.setattr(smoke_runner, "make_async_engine", lambda _url: _FakeEngine())
    monkeypatch.setattr(
        smoke_runner,
        "make_async_session_factory",
        lambda _engine: object(),
    )
    monkeypatch.setattr(smoke_runner, "build_credential_store", lambda **_kw: object())
    monkeypatch.setattr(
        smoke_runner,
        "build_principal_role_reader",
        lambda **_kw: object(),
    )
    monkeypatch.setattr(
        smoke_runner,
        "build_authentication_port",
        lambda **_kw: _FakeAuthentication(),
    )

    with pytest.raises(SmokeError, match="oa_rsa_flag_type_invalid"):
        asyncio.run(smoke_runner._run_live_checks(settings))

    output = capsys.readouterr().out
    assert "verify_login=oa_rsa_flag_type_invalid" in output
    assert "OA 返回的 RSA 标记类型无效" in output
    assert "rsa_response_field_count=3" in output
    assert "rsa_flag_present=true" in output
    assert "rsa_flag_type=string" in output
    assert "rsa_flag_character_count=7" in output
    assert "untrusted" not in output
    assert "synthetic-sensitive-detail" not in output
    assert "synthetic-account" not in output
    assert "synthetic-password" not in output
