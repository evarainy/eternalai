from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.infra.adapters.oa.contracts import build_structural_fingerprint
from scripts import sanitize_oa_contract_pack as sanitizer

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "sanitize_oa_contract_pack.py"
COOKIE_VALUE = "fixture-cookie-secret-001"
TOKEN_VALUE = "fixture-token-secret-001"


def _har(
    *,
    status: str = "pending",
    cookie_value: str = COOKIE_VALUE,
    extra_record_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        "workflowId": "raw-workflow-employee-001",
        "title": "Raw confidential workflow title",
        "status": status,
        "applicant": "Synthetic Raw Person",
        "currentStep": "Raw manager review",
        "approver": "Synthetic Raw Approver",
        "createdAt": "2026-07-29T09:30:00+09:00",
        "expired": False,
        "ignoredField": "must-not-be-copied",
    }
    if extra_record_fields is not None:
        record.update(extra_record_fields)
    response_body = {
        "data": {
            "records": [record],
            "ignoredPageValue": "must-not-be-copied",
        }
    }
    return {
        "log": {
            "version": "1.2",
            "entries": [
                {
                    "request": {
                        "url": "https://synthetic.invalid/api/pending",
                        "headers": [
                            {
                                "name": "Cookie",
                                "value": f"ecology_JSessionid={cookie_value}",
                            },
                            {
                                "name": "Authorization",
                                "value": f"Bearer {TOKEN_VALUE}",
                            },
                        ],
                        "cookies": [
                            {
                                "name": "loginidweaver",
                                "value": cookie_value,
                            }
                        ],
                    },
                    "response": {
                        "headers": [
                            {"name": "Content-Type", "value": "application/json"}
                        ],
                        "cookies": [],
                        "content": {
                            "mimeType": "application/json",
                            "text": json.dumps(response_body),
                        },
                    },
                }
            ],
        }
    }


def _run_script(
    input_har: Path,
    output_dir: Path,
    *,
    entry_indices: list[int | str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--input-har",
        str(input_har),
        "--output-dir",
        str(output_dir),
        "--profile-version",
        output_dir.name,
    ]
    for entry_index in entry_indices or []:
        command.extend(["--entry-index", str(entry_index)])
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_sanitizer_whitelists_and_publishes_atomic_contract_pack(
    tmp_path: Path,
) -> None:
    input_har = tmp_path / "synthetic.har"
    input_har.write_text(json.dumps(_har()), encoding="utf-8")
    output_dir = tmp_path / "synthetic-profile-v1"

    completed = _run_script(input_har, output_dir)

    assert completed.returncode == 0
    assert completed.stdout.strip() == "sanitized Contract Pack created"
    assert completed.stderr == ""
    assert sorted(path.name for path in output_dir.iterdir()) == [
        "fingerprint.json",
        "profile.json",
        "sample.json",
    ]
    profile = json.loads((output_dir / "profile.json").read_text(encoding="utf-8"))
    sample = json.loads((output_dir / "sample.json").read_text(encoding="utf-8"))
    fingerprint = json.loads(
        (output_dir / "fingerprint.json").read_text(encoding="utf-8")
    )
    assert profile == {
        "profile_version": "synthetic-profile-v1",
        "capability_id": "oa.list_pending_workflows",
        "source_kind": "sanitized_capture",
        "sanitizer_version": "1",
        "sample_file": "sample.json",
        "fingerprint_file": "fingerprint.json",
    }
    assert sample == {
        "workflows": [
            {
                "workflow_id": "workflow-synthetic-001",
                "title": "workflow-title-synthetic-001",
                "status": "pending",
                "applicant": "applicant-synthetic-001",
                "current_step": "step-synthetic-001",
                "approver": "approver-synthetic-001",
                "created_at": "2000-01-01T00:00:00+00:00",
                "expired": False,
            }
        ]
    }
    assert fingerprint == build_structural_fingerprint(sample)
    all_output = "\n".join(
        path.read_text(encoding="utf-8") for path in output_dir.iterdir()
    )
    for forbidden in (
        COOKIE_VALUE,
        TOKEN_VALUE,
        "raw-workflow-employee-001",
        "Raw confidential workflow title",
        "Synthetic Raw Person",
        "Raw manager review",
        "Synthetic Raw Approver",
        "2026-07-29T09:30:00+09:00",
        "must-not-be-copied",
    ):
        assert forbidden not in all_output


def test_repeated_entry_indices_aggregate_pages_in_selector_order(
    tmp_path: Path,
) -> None:
    har = _har()
    first_page = har["log"]["entries"][0]
    unrelated_entry = {
        "request": {"headers": [], "cookies": []},
        "response": {
            "headers": [],
            "cookies": [],
            "content": {
                "mimeType": "application/json",
                "text": json.dumps({"message": "not a workflow response"}),
            },
        },
    }
    second_page = _har(
        extra_record_fields={
            "expired": True,
            "approver": None,
            "createdAt": None,
        }
    )["log"]["entries"][0]
    har["log"]["entries"] = [first_page, unrelated_entry, second_page]
    input_har = tmp_path / "multi-page.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / "multi-page-profile-v1"

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[2, 0],
    )

    assert completed.returncode == 0
    sample = json.loads((output_dir / "sample.json").read_text(encoding="utf-8"))
    assert [workflow["workflow_id"] for workflow in sample["workflows"]] == [
        "workflow-synthetic-001",
        "workflow-synthetic-002",
    ]
    assert [workflow["expired"] for workflow in sample["workflows"]] == [True, False]
    assert sample["workflows"][0]["approver"] is None
    assert sample["workflows"][0]["created_at"] is None
    assert sample["workflows"][1]["approver"] == "approver-synthetic-002"
    assert sample["workflows"][1]["created_at"] == "2000-01-01T00:00:00+00:00"


def test_explicit_single_entry_selects_one_page_from_multiple_candidates(
    tmp_path: Path,
) -> None:
    har = _har()
    selected_page = _har(
        extra_record_fields={
            "expired": True,
            "approver": None,
            "createdAt": None,
        }
    )["log"]["entries"][0]
    har["log"]["entries"].append(selected_page)
    input_har = tmp_path / "single-selected-page.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / "single-selected-profile-v1"

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[1],
    )

    assert completed.returncode == 0
    sample = json.loads((output_dir / "sample.json").read_text(encoding="utf-8"))
    assert len(sample["workflows"]) == 1
    assert sample["workflows"][0]["expired"] is True
    assert sample["workflows"][0]["approver"] is None
    assert sample["workflows"][0]["created_at"] is None


def test_multiple_candidates_without_selector_fail_with_zero_output(
    tmp_path: Path,
) -> None:
    har = _har()
    har["log"]["entries"].append(_har()["log"]["entries"][0])
    input_har = tmp_path / "multiple-candidates.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / "multiple-candidates-negative-v1"

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "pending_workflow_response_not_unique" in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_sanitizer_accepts_empty_pending_workflow_list(tmp_path: Path) -> None:
    har = _har()
    entry = har["log"]["entries"][0]
    response_body = json.loads(entry["response"]["content"]["text"])
    response_body["data"]["records"] = []
    entry["response"]["content"]["text"] = json.dumps(response_body)
    input_har = tmp_path / "empty.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / "empty-profile-v1"

    completed = _run_script(input_har, output_dir)

    assert completed.returncode == 0
    sample = json.loads((output_dir / "sample.json").read_text(encoding="utf-8"))
    fingerprint = json.loads(
        (output_dir / "fingerprint.json").read_text(encoding="utf-8")
    )
    assert sample == {"workflows": []}
    assert fingerprint == build_structural_fingerprint(sample)
    assert fingerprint == build_structural_fingerprint(
        {
            "workflows": [
                {
                    "workflow_id": "different",
                    "title": "different",
                    "status": "pending",
                    "applicant": "different",
                    "current_step": "different",
                    "approver": None,
                    "created_at": None,
                    "expired": False,
                }
            ]
        }
    )


def test_cookie_from_unselected_entry_is_scanned_across_whole_har(
    tmp_path: Path,
) -> None:
    har = _har()
    har["log"]["entries"].append(
        {
            "request": {
                "headers": [{"name": "Cookie", "value": "pending"}],
                "cookies": [],
            },
            "response": {
                "headers": [],
                "cookies": [],
                "content": {
                    "mimeType": "application/json",
                    "text": json.dumps({"message": "not selected"}),
                },
            },
        }
    )
    input_har = tmp_path / "multi-entry-cookie.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / "multi-entry-cookie-negative-v1"

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[0],
    )

    assert completed.returncode == 2
    assert "raw_sensitive_value_survived" in completed.stderr
    assert "pending" not in completed.stdout
    assert "pending" not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


@pytest.mark.parametrize(
    "request_fields",
    [
        {
            "url": "https://synthetic.invalid/other?access_token=pending",
        },
        {
            "url": "https://synthetic.invalid/other",
            "queryString": [{"name": "access_token", "value": "pending"}],
        },
        {
            "url": "https://synthetic.invalid/other",
            "postData": {
                "mimeType": "application/x-www-form-urlencoded",
                "params": [{"name": "password", "value": "pending"}],
            },
        },
        {
            "url": "https://synthetic.invalid/other",
            "postData": {
                "mimeType": "application/x-www-form-urlencoded; charset=utf-8",
                "text": "password=pending",
            },
        },
    ],
    ids=("url-query", "har-query", "har-form-params", "form-encoded-body"),
)
def test_unselected_query_and_form_credentials_fail_with_zero_output(
    tmp_path: Path,
    request_fields: dict[str, Any],
) -> None:
    har = _har()
    har["log"]["entries"].append(
        {
            "request": {
                "headers": [],
                "cookies": [],
                **request_fields,
            },
            "response": {
                "headers": [],
                "cookies": [],
                "content": {
                    "mimeType": "application/json",
                    "text": json.dumps({"message": "not selected"}),
                },
            },
        }
    )
    input_har = tmp_path / "multi-entry-parameter.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / "multi-entry-parameter-negative-v1"

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[0],
    )

    assert completed.returncode == 2
    assert "raw_sensitive_value_survived" in completed.stderr
    assert "pending" not in completed.stdout
    assert "pending" not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_encoded_unselected_response_remains_fail_closed_with_selector(
    tmp_path: Path,
) -> None:
    har = _har()
    har["log"]["entries"].append(
        {
            "request": {"headers": [], "cookies": []},
            "response": {
                "headers": [],
                "cookies": [],
                "content": {
                    "mimeType": "application/json",
                    "encoding": "base64",
                    "text": "opaque-encoded-response",
                },
            },
        }
    )
    input_har = tmp_path / "multi-entry-encoded.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / "multi-entry-encoded-negative-v1"

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[0],
    )

    assert completed.returncode != 0
    assert "encoded_response_not_supported" in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_every_selected_response_is_checked_for_forbidden_keys(
    tmp_path: Path,
) -> None:
    selected_cookie_value = "selected-page-cookie-927315"
    har = _har()
    har["log"]["entries"].append(
        _har(
            extra_record_fields={
                "metadata": {"cookie": selected_cookie_value},
            }
        )["log"]["entries"][0]
    )
    input_har = tmp_path / "selected-page-cookie.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / "selected-page-cookie-negative-v1"

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[0, 1],
    )

    assert completed.returncode != 0
    assert "forbidden_response_key" in completed.stderr
    assert selected_cookie_value not in completed.stdout
    assert selected_cookie_value not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


@pytest.mark.parametrize(
    ("entry_indices", "expected_error"),
    [
        ([-1], "entry_index_out_of_range"),
        ([1], "entry_index_out_of_range"),
        ([0, 0], "entry_index_duplicate"),
    ],
)
def test_invalid_entry_indices_fail_with_zero_output(
    tmp_path: Path,
    entry_indices: list[int],
    expected_error: str,
) -> None:
    input_har = tmp_path / "selector-invalid.har"
    input_har.write_text(json.dumps(_har()), encoding="utf-8")
    output_dir = tmp_path / f"{expected_error}-negative-v1"

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=entry_indices,
    )

    assert completed.returncode != 0
    assert expected_error in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_non_integer_entry_index_fails_with_zero_output(tmp_path: Path) -> None:
    input_har = tmp_path / "selector-not-integer.har"
    input_har.write_text(json.dumps(_har()), encoding="utf-8")
    output_dir = tmp_path / "selector-not-integer-negative-v1"

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=["not-an-index"],
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_selected_non_json_response_fails_with_zero_output(tmp_path: Path) -> None:
    har = _har()
    har["log"]["entries"].append(
        {
            "request": {"headers": [], "cookies": []},
            "response": {
                "headers": [],
                "cookies": [],
                "content": {
                    "mimeType": "application/json",
                    "text": "not-json",
                },
            },
        }
    )
    input_har = tmp_path / "selected-non-json.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / "selected-non-json-negative-v1"

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[1],
    )

    assert completed.returncode != 0
    assert "selected_entry_response_not_json" in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_selected_non_target_response_fails_with_zero_output(tmp_path: Path) -> None:
    har = _har()
    har["log"]["entries"].append(
        {
            "request": {"headers": [], "cookies": []},
            "response": {
                "headers": [],
                "cookies": [],
                "content": {
                    "mimeType": "application/json",
                    "text": json.dumps({"message": "not a workflow response"}),
                },
            },
        }
    )
    input_har = tmp_path / "selected-non-target.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / "selected-non-target-negative-v1"

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[1],
    )

    assert completed.returncode != 0
    assert "selected_entry_not_pending_workflow_response" in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_selected_structurally_invalid_entry_fails_with_zero_output(
    tmp_path: Path,
) -> None:
    har = _har()
    har["log"]["entries"].append({"request": {}})
    input_har = tmp_path / "selected-invalid-entry.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / "selected-invalid-entry-negative-v1"

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[1],
    )

    assert completed.returncode != 0
    assert "selected_entry_invalid" in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_cookie_value_reaching_whitelisted_field_fails_with_zero_output(
    tmp_path: Path,
) -> None:
    input_har = tmp_path / "cookie-leak.har"
    input_har.write_text(
        json.dumps(_har(cookie_value="pending")),
        encoding="utf-8",
    )
    output_dir = tmp_path / "cookie-negative-v1"

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "raw_sensitive_value_survived" in completed.stderr
    assert "pending" not in completed.stdout
    assert "pending" not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_nested_response_cookie_fails_closed_without_leaking_value(
    tmp_path: Path,
) -> None:
    nested_cookie_value = "nested-response-cookie-927315"
    input_har = tmp_path / "nested-cookie.har"
    input_har.write_text(
        json.dumps(
            _har(
                status=nested_cookie_value,
                extra_record_fields={
                    "metadata": {"cookie": nested_cookie_value},
                },
            )
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "nested-cookie-negative-v1"

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "forbidden_response_key" in completed.stderr
    assert nested_cookie_value not in completed.stdout
    assert nested_cookie_value not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_nested_response_workcode_fails_closed_without_leaking_value(
    tmp_path: Path,
) -> None:
    workcode_value = "GOV-EMP-927315"
    input_har = tmp_path / "nested-workcode.har"
    input_har.write_text(
        json.dumps(
            _har(
                status=workcode_value,
                extra_record_fields={
                    "identity": {"workCode": workcode_value},
                },
            )
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "nested-workcode-negative-v1"

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "forbidden_response_key" in completed.stderr
    assert workcode_value not in completed.stdout
    assert workcode_value not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_nested_response_token_fails_closed_without_leaking_value(
    tmp_path: Path,
) -> None:
    nested_token_value = "nested-response-token-927315"
    input_har = tmp_path / "nested-token.har"
    input_har.write_text(
        json.dumps(
            _har(
                status=nested_token_value,
                extra_record_fields={
                    "authentication": {"accessToken": nested_token_value},
                },
            )
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "nested-token-negative-v1"

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "forbidden_response_key" in completed.stderr
    assert nested_token_value not in completed.stdout
    assert nested_token_value not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_json_encoded_sensitive_key_in_selected_response_fails_closed(
    tmp_path: Path,
) -> None:
    embedded_value = "pending"
    input_har = tmp_path / "embedded-json.har"
    input_har.write_text(
        json.dumps(
            _har(
                extra_record_fields={
                    "metadata": {
                        "opaque": json.dumps(
                            json.dumps({"accessToken": embedded_value})
                        )
                    }
                },
            )
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "embedded-json-negative-v1"

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "forbidden_response_key" in completed.stderr
    assert embedded_value not in completed.stdout
    assert embedded_value not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_sensitive_value_in_other_json_response_cannot_reach_output(
    tmp_path: Path,
) -> None:
    other_response_value = "pending"
    har = _har()
    har["log"]["entries"].append(
        {
            "request": {"headers": [], "cookies": []},
            "response": {
                "headers": [],
                "cookies": [],
                "content": {
                    "mimeType": "application/json",
                    "text": json.dumps(
                        {
                            "unrelated": {
                                "accessToken": other_response_value,
                            }
                        }
                    ),
                },
            },
        }
    )
    input_har = tmp_path / "other-response.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / "other-response-negative-v1"

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "raw_sensitive_value_survived" in completed.stderr
    assert other_response_value not in completed.stdout
    assert other_response_value not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_overdeep_json_encoded_response_fails_closed_without_leak(
    tmp_path: Path,
) -> None:
    sensitive_value = "pending"
    encoded: Any = {"accessToken": sensitive_value}
    for _depth in range(12):
        encoded = json.dumps(encoded)
    input_har = tmp_path / "overdeep-json.har"
    input_har.write_text(
        json.dumps(
            _har(
                extra_record_fields={
                    "metadata": {"opaque": encoded},
                },
            )
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "overdeep-json-negative-v1"

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "embedded_json_depth_exceeded" in completed.stderr
    assert sensitive_value not in completed.stdout
    assert sensitive_value not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_oversized_har_fails_closed_before_parsing(tmp_path: Path) -> None:
    input_har = tmp_path / "oversized.har"
    input_har.write_bytes(b" " * (32 * 1024 * 1024 + 1))
    output_dir = tmp_path / "oversized-negative-v1"

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr.strip() == "sanitization failed: input_too_large"
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_oversized_non_candidate_response_is_rejected_before_candidate(
    tmp_path: Path,
) -> None:
    sensitive_marker = "oversized-container-sensitive-marker"
    har = _har()
    har["log"]["entries"].insert(
        0,
        {
            "request": {"headers": [], "cookies": []},
            "response": {
                "headers": [],
                "cookies": [],
                "content": {
                    "mimeType": "application/json",
                    "text": json.dumps(
                        {
                            "accessToken": sensitive_marker,
                            "padding": "x" * (1 * 1024 * 1024),
                        }
                    ),
                },
            },
        },
    )
    input_har = tmp_path / "oversized-container.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / "oversized-container-negative-v1"

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "json_container_too_large" in completed.stderr
    assert sensitive_marker not in completed.stdout
    assert sensitive_marker not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_unrecognized_status_is_not_copied_to_output(tmp_path: Path) -> None:
    private_status = "private-business-status-927315"
    input_har = tmp_path / "unrecognized-status.har"
    input_har.write_text(
        json.dumps(_har(status=private_status)),
        encoding="utf-8",
    )
    output_dir = tmp_path / "status-negative-v1"

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "response_status_invalid" in completed.stderr
    assert private_status not in completed.stdout
    assert private_status not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_existing_output_is_rejected_without_overwrite_or_delete(tmp_path: Path) -> None:
    input_har = tmp_path / "synthetic.har"
    input_har.write_text(json.dumps(_har()), encoding="utf-8")
    output_dir = tmp_path / "existing-output-v1"
    output_dir.mkdir()
    sentinel = output_dir / "preserve.txt"
    sentinel.write_text("preserve-existing-output", encoding="utf-8")

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "output_already_exists" in completed.stderr
    assert sentinel.read_text(encoding="utf-8") == "preserve-existing-output"
    assert [path.name for path in output_dir.iterdir()] == ["preserve.txt"]


def test_third_layer_pattern_scan_runs_before_any_candidate_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_har = tmp_path / "synthetic.har"
    input_har.write_text(json.dumps(_har()), encoding="utf-8")
    output_dir = tmp_path / "aaaaaaaa.bbbbbbbb.cccccccc"
    write_calls = 0

    def record_write(_path: Path, _payload: Any) -> None:
        nonlocal write_calls
        write_calls += 1

    monkeypatch.setattr(sanitizer, "_write_json", record_write)

    with pytest.raises(
        sanitizer.SanitizationError,
        match="forbidden_output_value",
    ) as exc_info:
        sanitizer.sanitize_har_to_contract_pack(
            input_har=input_har,
            output_dir=output_dir,
            profile_version=output_dir.name,
        )

    assert write_calls == 0
    assert exc_info.value.__context__ is None
    assert exc_info.value.__cause__ is None
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_malformed_har_exception_chain_never_retains_raw_input(
    tmp_path: Path,
) -> None:
    sensitive_marker = "malformed-har-sensitive-" + TOKEN_VALUE
    input_har = tmp_path / "malformed.har"
    input_har.write_text(
        '{"log":{"entries":"' + sensitive_marker,
        encoding="utf-8",
    )
    output_dir = tmp_path / "malformed-negative-v1"

    with pytest.raises(
        sanitizer.SanitizationError,
        match="json_unreadable",
    ) as exc_info:
        sanitizer.sanitize_har_to_contract_pack(
            input_har=input_har,
            output_dir=output_dir,
            profile_version=output_dir.name,
        )

    assert exc_info.value.__context__ is None
    assert exc_info.value.__cause__ is None
    assert sensitive_marker not in (repr(exc_info.value) + str(exc_info.value))
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))
