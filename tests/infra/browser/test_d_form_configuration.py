"""Non-secret local configuration regressions; no environment or deployment read."""

import json

import pytest

from app.infra.browser.d_form_configuration import (
    DFormConfigurationError,
    DFormTaskConfiguration,
    missing_conditions,
    read_local_model,
)


def local_config(tmp_path):
    return {
        "version": "d-form-task-v1", "environment_files": [str(tmp_path / "unused.env")],
        "origin": "https://synthetic.invalid", "python": str(tmp_path / "python.exe"),
        "playwright_node": str(tmp_path / "node.exe"), "receipts_root": str(tmp_path / "receipts"),
        "chromium": str(tmp_path / "chromium.exe"), "chromium_sha256": "a" * 64,
        "owned_runs_root": str(tmp_path / "runs"),
    }


def test_missing_login_controls_is_explicit_not_legacy_handoff(tmp_path):
    config = DFormTaskConfiguration.model_validate_json(json.dumps(local_config(tmp_path)))
    gaps = missing_conditions(config)
    assert "d_form_login_contract_missing" in gaps
    assert "d_form_reference_missing" not in gaps
    assert "d_form_owned_deployment_missing" not in gaps


def test_legacy_resource_record_is_refused_without_opening_it(tmp_path):
    raw = local_config(tmp_path)
    raw["resource_record"] = str(tmp_path / "never-created-private-record.json")
    config = DFormTaskConfiguration.model_validate_json(json.dumps(raw))
    assert "d_form_resource_scope_denied" in missing_conditions(config)
    assert not (tmp_path / "never-created-private-record.json").exists()


@pytest.mark.parametrize("controls", [{}, {"login_path": "/synthetic-login"}, "invalid"])
def test_invalid_controls_has_precise_code_without_reading_environment(tmp_path, controls):
    raw = local_config(tmp_path)
    raw["login_controls"] = controls
    source = tmp_path / "synthetic-config.json"
    source.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DFormConfigurationError) as error:
        read_local_model(source, DFormTaskConfiguration)
    assert error.value.code == "d_form_login_contract_missing"
    assert not (tmp_path / "unused.env").exists()
