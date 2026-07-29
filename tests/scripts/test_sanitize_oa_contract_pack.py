from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.infra.adapters.oa.contracts import build_structural_fingerprint

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "sanitize_oa_contract_pack.py"
COOKIE_VALUE = "fixture-cookie-secret-001"
TOKEN_VALUE = "fixture-token-secret-001"


def _har(*, status: str = "pending") -> dict[str, Any]:
    response_body = {
        "data": {
            "records": [
                {
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
            ],
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
                                "value": f"ecology_JSessionid={COOKIE_VALUE}",
                            },
                            {
                                "name": "Authorization",
                                "value": f"Bearer {TOKEN_VALUE}",
                            },
                        ],
                        "cookies": [
                            {
                                "name": "loginidweaver",
                                "value": COOKIE_VALUE,
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


def _run_script(input_har: Path, output_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input-har",
            str(input_har),
            "--output-dir",
            str(output_dir),
            "--profile-version",
            output_dir.name,
        ],
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


def test_cookie_value_reaching_whitelisted_field_fails_with_zero_output(
    tmp_path: Path,
) -> None:
    input_har = tmp_path / "cookie-leak.har"
    input_har.write_text(
        json.dumps(_har(status=COOKIE_VALUE)),
        encoding="utf-8",
    )
    output_dir = tmp_path / "cookie-negative-v1"

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "raw_sensitive_value_survived" in completed.stderr
    assert COOKIE_VALUE not in completed.stdout
    assert COOKIE_VALUE not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_token_pattern_reaching_whitelisted_field_fails_with_zero_output(
    tmp_path: Path,
) -> None:
    input_har = tmp_path / "token-pattern.har"
    input_har.write_text(
        json.dumps(_har(status="Bearer standalone-token-value")),
        encoding="utf-8",
    )
    output_dir = tmp_path / "token-negative-v1"

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "forbidden_output_value" in completed.stderr
    assert "standalone-token-value" not in completed.stdout
    assert "standalone-token-value" not in completed.stderr
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
