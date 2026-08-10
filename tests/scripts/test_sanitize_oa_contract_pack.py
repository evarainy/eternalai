from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.infra.adapters.oa.contracts import (
    EXTERNAL_SANITIZATION_WARNING,
    PENDING_WORKFLOW_DERIVATION_WARNING,
    OALegacyPendingWorkflowCollection,
    OAPendingWorkflowCollection,
    build_structural_fingerprint,
)
from scripts import sanitize_oa_contract_pack as sanitizer

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "sanitize_oa_contract_pack.py"
COOKIE_VALUE = "fixture-cookie-secret-001"
TOKEN_VALUE = "fixture-token-secret-001"
PROFILE_VERSION = "ecology9-pending-workflows-v1"
PROFILE_VERSION_V2 = "ecology9-pending-workflows-v2"
PROFILE_VERSION_V3 = "ecology9-pending-workflows-v3"
SYSTEM_MESSAGE_PROFILE_VERSION = "ecology9-system-messages-v1"
LEGACY_PENDING_PROFILE_VERSIONS = frozenset({PROFILE_VERSION, PROFILE_VERSION_V2})
TODO_SESSION_KEY = "synthetic-todo-session-key-" + ("x" * 42)
FROZEN_PENDING_PACK_FILE_SHA256 = {
    "ecology9-pending-workflows-v1/fingerprint.json": (
        "26aa1d354cb8c6056587bf7fcccd305139059796ed9b0ed2c26927c6c81137ef"
    ),
    "ecology9-pending-workflows-v1/profile.json": (
        "605673383921f3f65b296b25041be93963c20dcdaf79059f16cd2ad898d1774c"
    ),
    "ecology9-pending-workflows-v1/sample.json": (
        "83543add6d8cc6d642638f6147c92bef8fd6a2419a5c4f91ed1225ebfe6fb252"
    ),
    "ecology9-pending-workflows-v2/fingerprint.json": (
        "86c5bd727f29111a1baa4e4c21f3cec7eb873331d6c4fabf7b20c9b664084dfa"
    ),
    "ecology9-pending-workflows-v2/profile.json": (
        "1088ab942c4c204deb15e92b89ae17e6a150a2347a9f199d0dff89d6df1edef6"
    ),
    "ecology9-pending-workflows-v2/sample.json": (
        "eac04435b6b552924ef0b0dc05bee8b6e5889801ff03a08f97219bfecb6dd740"
    ),
}
# The category the pending pack claims to represent, and the sibling category a
# system-message capture is actually recorded under.
PENDING_CATEGORY_ID = "217"
SIBLING_CATEGORY_ID = "2,31"


def _assert_sanitizer_traceback_is_redacted(
    error: BaseException,
    marker: str,
) -> None:
    assert error.__context__ is None
    assert error.__cause__ is None
    sanitizer_frames = 0
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") == sanitizer.__name__:
            sanitizer_frames += 1
            assert all(marker not in repr(value) for value in frame.f_locals.values())
        traceback = traceback.tb_next
    assert sanitizer_frames > 0


def _har(
    *,
    status: str = "1",
    cookie_value: str = COOKIE_VALUE,
    category_id: str = SIBLING_CATEGORY_ID,
    extra_record_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        "messageid": "raw-message-employee-001",
        "title": "Raw confidential workflow title",
        "context": "Raw confidential workflow content",
        "name": "Synthetic Raw Message Type",
        "time": "2026-07-29 09:30:00",
        "bizstate": status,
        "link": "/workflow/desktop/raw-message-employee-001",
        "linkmobileurl": "/workflow/mobile/raw-message-employee-001",
        "gomethod": "",
        "gomethodpc": "",
        "showimage": "",
        "ignoredField": "must-not-be-copied",
    }
    if extra_record_fields is not None:
        record.update(extra_record_fields)
    response_body = {
        "status": "1",
        "data": [record],
        "maxtime": "synthetic-upper-bound",
        "mintime": "synthetic-lower-bound",
        "msgid": "synthetic-message-cursor",
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
                        "postData": {
                            "mimeType": "application/x-www-form-urlencoded",
                            "params": [
                                {"name": "id", "value": category_id},
                                {"name": "pagesize", "value": "20"},
                            ],
                            "text": f"id={category_id}&pagesize=20",
                        },
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


def _system_message_har() -> tuple[dict[str, Any], list[dict[str, str]]]:
    raw_records = [
        {
            "messageid": "83000001",
            "title": "真实系统消息标题甲乙丙丁戊己庚辛",
            "context": "真实系统消息正文甲乙丙丁戊己庚辛壬癸子丑寅卯",
            "name": "真实消息来源甲",
            "time": "2026-08-03 10:00:00",
            "bizstate": "0",
            "link": "https://internal.example.invalid/message/83000001/detail",
            "linkmobileurl": "https://internal.example.invalid/mobile/83000001/detail",
            "gomethod": "synthetic-desktop-method",
            "gomethodpc": "synthetic-mobile-method",
            "showimage": "synthetic-image-flag",
        },
        {
            "messageid": "83000002",
            "title": "真实系统消息标题第二条甲乙丙丁戊己庚辛壬癸",
            "context": "真实系统消息正文第二条甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳",
            "name": "真实消息来源乙",
            "time": "2026-08-03 09:00:00",
            "bizstate": "1",
            "link": "",
            "linkmobileurl": "",
            "gomethod": "synthetic-desktop-method",
            "gomethodpc": "synthetic-mobile-method",
            "showimage": "synthetic-image-flag",
        },
    ]
    image_bytes = b"\x89PNG\r\n\x1a\nsynthetic-image"
    return (
        {
            "log": {
                "version": "1.2",
                "entries": [
                    {
                        "request": {"headers": [], "cookies": []},
                        "response": {
                            "headers": [],
                            "cookies": [],
                            "content": {
                                "mimeType": "image/png",
                                "encoding": "base64",
                                "text": base64.b64encode(image_bytes).decode("ascii"),
                            },
                        },
                    },
                    {
                        "request": {
                            "headers": [
                                {
                                    "name": "Cookie",
                                    "value": "ecology_JSessionid=1",
                                }
                            ],
                            "cookies": [
                                {"name": "ecology_JSessionid", "value": "1"}
                            ],
                            "postData": {
                                "mimeType": "application/x-www-form-urlencoded",
                                "params": [
                                    {"name": "pagesize", "value": "2"},
                                    {"name": "selectState", "value": "0"},
                                ],
                                "text": "pagesize=2&selectState=0",
                            },
                        },
                        "response": {
                            "headers": [],
                            "cookies": [],
                            "content": {
                                "mimeType": "application/json",
                                "text": json.dumps(
                                    {
                                        "data": raw_records,
                                        "mintime": "2026-08-03 09:00:00",
                                        "msgid": "cursor-raw-001",
                                        "maxtime": "2026-08-03 10:00:00",
                                        "status": "1",
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        },
                    },
                ],
            }
        },
        raw_records,
    )


def _todo_list_har(
    *,
    session_key: str = TODO_SESSION_KEY,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    raw_records = [
        {
            "requestid": "481001",
            "requestname": "Synthetic confidential todo title alpha",
            "status": "Raw",
            "receivedate": "2026-08-09",
            "createdate": "2026-08-08",
            "workflowid": "713",
            "requestnamespan": "<span>never copy alpha</span>",
            "statusspan": "<span>never copy status alpha</span>",
            "userid": "synthetic-user-alpha",
        },
        {
            "requestid": "481002",
            "requestname": "Synthetic confidential todo title beta",
            "status": "New",
            "receivedate": "2026-08-10",
            "createdate": "2026-08-09",
            "workflowid": "1714",
            "requestnamespan": "<span>never copy beta</span>",
            "statusspan": "<span>never copy status beta</span>",
            "userid": "synthetic-user-beta",
        },
    ]

    def _post_entry(
        path: str,
        parameters: list[tuple[str, str]],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        encoded_parameters = "&".join(f"{name}={value}" for name, value in parameters)
        return {
            "request": {
                "method": "POST",
                "url": f"https://synthetic.invalid{path}",
                "headers": [
                    {
                        "name": "Cookie",
                        "value": f"ecology_JSessionid={COOKIE_VALUE}",
                    }
                ],
                "cookies": [],
                "postData": {
                    "mimeType": "application/x-www-form-urlencoded",
                    "params": [
                        {"name": name, "value": value}
                        for name, value in parameters
                    ],
                    "text": encoded_parameters,
                },
            },
            "response": {
                "headers": [],
                "cookies": [],
                "content": {
                    "mimeType": "application/json",
                    "text": json.dumps(payload),
                },
            },
        }

    split_page_key = _post_entry(
        "/api/workflow/reqlist/splitPageKey",
        [("viewcondition", "5")],
        {
            "sessionkey": session_key,
            "isQueryByNewTable": True,
            "sharearg": {},
        },
    )
    datas = _post_entry(
        "/api/ec/dev/table/datas",
        [("current", "1"), ("dataKey", session_key)],
        {"datas": raw_records, "status": True},
    )
    counts = _post_entry(
        "/api/ec/dev/table/counts",
        [("dataKey", session_key)],
        {"count": len(raw_records), "status": True},
    )
    return (
        {
            "log": {
                "version": "1.2",
                "entries": [split_page_key, datas, counts],
            }
        },
        raw_records,
    )


def _run_script(
    input_har: Path,
    output_dir: Path,
    *,
    entry_indices: list[int | str] | None = None,
    extra_args: list[str] | None = None,
    pending_category_id: str | None = PENDING_CATEGORY_ID,
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
    if (
        pending_category_id is not None
        and output_dir.name in LEGACY_PENDING_PROFILE_VERSIONS
    ):
        command.extend(["--pending-category-id", pending_category_id])
    command.extend(extra_args or [])
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
    output_dir = tmp_path / PROFILE_VERSION

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
        "profile_version": PROFILE_VERSION,
        "capability_id": "oa.list_pending_workflows",
        "source_kind": "derived_from_sibling_capture",
        "sanitizer_version": "2",
        "sample_file": "sample.json",
        "fingerprint_file": "fingerprint.json",
        "source_warning": PENDING_WORKFLOW_DERIVATION_WARNING,
    }
    assert sample == {
        "workflows": [
            {
                "message_id": "900000019000000190000001",
                "title": "统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容",
                "content": "统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知",
                "source_name": "统消息合成样本文本内容通知提醒待办查阅系统消息合成样",
                "occurred_at": "2000-01-01 00:00:00",
                "business_state": "2",
                "link": "/oa/system-messages/desktop/001xxxxxxxxxxx",
                "mobile_link": "/oa/system-messages/mobile/001xxxxxxxxxxx",
            }
        ],
        "returned_count": 1,
        "is_complete": True,
    }
    OALegacyPendingWorkflowCollection.model_validate(sample, strict=True)
    assert fingerprint == build_structural_fingerprint(sample)
    all_output = "\n".join(
        path.read_text(encoding="utf-8") for path in output_dir.iterdir()
    )
    for forbidden in (
        COOKIE_VALUE,
        TOKEN_VALUE,
        "raw-message-employee-001",
        "Raw confidential workflow title",
        "Raw confidential workflow content",
        "Synthetic Raw Message Type",
        "2026-07-29 09:30:00",
        "must-not-be-copied",
    ):
        assert forbidden not in all_output


def test_direct_pending_capture_is_never_labelled_derived(tmp_path: Path) -> None:
    input_har = tmp_path / "direct-pending.har"
    input_har.write_text(
        json.dumps(_har(category_id=PENDING_CATEGORY_ID)),
        encoding="utf-8",
    )
    output_dir = tmp_path / PROFILE_VERSION_V2

    completed = _run_script(input_har, output_dir)

    assert completed.returncode == 0
    assert completed.stderr == ""
    profile = json.loads((output_dir / "profile.json").read_text(encoding="utf-8"))
    assert profile["source_kind"] == "sanitized_capture"
    assert "source_warning" not in profile
    assert PENDING_WORKFLOW_DERIVATION_WARNING not in json.dumps(
        profile,
        ensure_ascii=False,
    )


def test_sibling_capture_is_never_labelled_a_direct_pending_capture(
    tmp_path: Path,
) -> None:
    input_har = tmp_path / "sibling-pending.har"
    input_har.write_text(
        json.dumps(_har(category_id=SIBLING_CATEGORY_ID)),
        encoding="utf-8",
    )
    output_dir = tmp_path / PROFILE_VERSION_V2

    completed = _run_script(input_har, output_dir)

    assert completed.returncode == 0
    assert completed.stderr == ""
    profile = json.loads((output_dir / "profile.json").read_text(encoding="utf-8"))
    assert profile["source_kind"] == "derived_from_sibling_capture"
    assert profile["source_warning"] == PENDING_WORKFLOW_DERIVATION_WARNING


def test_todo_list_v3_normalizes_three_linked_responses_without_raw_values(
    tmp_path: Path,
) -> None:
    har, raw_records = _todo_list_har()
    input_har = tmp_path / "todo-list.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION_V3

    completed = _run_script(input_har, output_dir, entry_indices=[2, 0, 1])

    assert completed.returncode == 0
    assert completed.stdout == "sanitized Contract Pack created\n"
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
        "profile_version": PROFILE_VERSION_V3,
        "capability_id": "oa.list_pending_workflows",
        "source_kind": "sanitized_capture",
        "sanitizer_version": "2",
        "sample_file": "sample.json",
        "fingerprint_file": "fingerprint.json",
    }
    assert sample["returned_count"] == len(raw_records) == 2
    assert sample["authoritative_count"] == len(raw_records)
    assert sample["is_complete"] is True
    assert len(sample["workflows"]) == len(raw_records)
    assert all(
        set(workflow)
        == {
            "todo_id",
            "title",
            "status",
            "received_at",
            "created_at",
            "workflow_type_id",
        }
        for workflow in sample["workflows"]
    )
    assert len({workflow["todo_id"] for workflow in sample["workflows"]}) == len(
        sample["workflows"]
    )
    for raw_record, workflow in zip(raw_records, sample["workflows"], strict=True):
        assert len(workflow["todo_id"]) == len(raw_record["requestid"])
        assert len(workflow["title"]) == len(raw_record["requestname"])
        assert len(workflow["status"]) == len(raw_record["status"])
        assert len(workflow["received_at"]) == len(raw_record["receivedate"])
        assert len(workflow["created_at"]) == len(raw_record["createdate"])
        assert len(workflow["workflow_type_id"]) == len(raw_record["workflowid"])
    OAPendingWorkflowCollection.model_validate(sample, strict=True)
    assert fingerprint == build_structural_fingerprint(sample)

    all_output = "\n".join(
        path.read_text(encoding="utf-8") for path in output_dir.iterdir()
    )
    raw_business_values = {
        str(raw_record[field])
        for raw_record in raw_records
        for field in (
            "requestid",
            "requestname",
            "status",
            "receivedate",
            "createdate",
            "workflowid",
            "requestnamespan",
            "statusspan",
            "userid",
        )
    }
    assert TODO_SESSION_KEY not in all_output
    assert "sessionkey" not in all_output.casefold()
    assert "datakey" not in all_output.casefold()
    assert all(value not in all_output for value in raw_business_values)


def test_committed_todo_list_v3_pack_is_self_consistent() -> None:
    pack = REPO_ROOT / "tests" / "contract_packs" / "oa" / PROFILE_VERSION_V3
    assert sorted(path.name for path in pack.iterdir()) == [
        "fingerprint.json",
        "profile.json",
        "sample.json",
    ]
    profile = json.loads((pack / "profile.json").read_text(encoding="utf-8"))
    sample = json.loads((pack / "sample.json").read_text(encoding="utf-8"))
    fingerprint = json.loads(
        (pack / "fingerprint.json").read_text(encoding="utf-8")
    )

    assert profile == {
        "profile_version": PROFILE_VERSION_V3,
        "capability_id": "oa.list_pending_workflows",
        "source_kind": "sanitized_capture",
        "sanitizer_version": "2",
        "sample_file": "sample.json",
        "fingerprint_file": "fingerprint.json",
    }
    validated = OAPendingWorkflowCollection.model_validate(sample, strict=True)
    assert validated.returned_count == 6
    assert validated.authoritative_count == 6
    assert validated.is_complete is True
    assert len({workflow.todo_id for workflow in validated.workflows}) == 6
    assert fingerprint == build_structural_fingerprint(sample)
    rendered = json.dumps(
        {"profile": profile, "sample": sample, "fingerprint": fingerprint},
        ensure_ascii=False,
        sort_keys=True,
    ).casefold()
    assert "sessionkey" not in rendered
    assert "datakey" not in rendered


def test_published_pending_v1_v2_pack_files_remain_byte_frozen() -> None:
    pack_root = REPO_ROOT / "tests" / "contract_packs" / "oa"
    # Git's canonical text bytes are LF; tolerate only the checkout's CRLF
    # projection so the same immutable blob guard runs on Windows and Linux.
    actual = {
        relative_path: hashlib.sha256(
            (pack_root / relative_path).read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for relative_path in FROZEN_PENDING_PACK_FILE_SHA256
    }

    assert actual == FROZEN_PENDING_PACK_FILE_SHA256


@pytest.mark.parametrize(
    ("failure_kind", "expected_error"),
    [
        ("viewcondition", "todo_list_viewcondition_invalid"),
        ("datas_key", "todo_list_session_association_invalid"),
        ("counts_key", "todo_list_session_association_invalid"),
        ("datas_status", "todo_list_datas_status_invalid"),
        ("counts_status", "todo_list_counts_status_invalid"),
        ("authoritative_count", "todo_list_incomplete_capture"),
    ],
)
def test_todo_list_v3_relationship_and_completeness_failures_leave_zero_output(
    tmp_path: Path,
    failure_kind: str,
    expected_error: str,
) -> None:
    har, _raw_records = _todo_list_har()
    entries = har["log"]["entries"]
    if failure_kind == "viewcondition":
        entries[0]["request"]["postData"]["params"][0]["value"] = "4"
        entries[0]["request"]["postData"]["text"] = "viewcondition=4"
    elif failure_kind == "datas_key":
        entries[1]["request"]["postData"]["params"][1]["value"] = "mismatch"
        entries[1]["request"]["postData"]["text"] = "current=1&dataKey=mismatch"
    elif failure_kind == "counts_key":
        entries[2]["request"]["postData"]["params"][0]["value"] = "mismatch"
        entries[2]["request"]["postData"]["text"] = "dataKey=mismatch"
    elif failure_kind == "datas_status":
        entries[1]["response"]["content"]["text"] = json.dumps(
            {"datas": _raw_records, "status": False}
        )
    elif failure_kind == "counts_status":
        entries[2]["response"]["content"]["text"] = json.dumps(
            {"count": len(_raw_records), "status": False}
        )
    else:
        entries[2]["response"]["content"]["text"] = json.dumps(
            {"count": len(_raw_records) + 1, "status": True}
        )
    input_har = tmp_path / "todo-list-invalid.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION_V3

    completed = _run_script(input_har, output_dir, entry_indices=[0, 1, 2])

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == f"sanitization failed: {expected_error}\n"
    assert TODO_SESSION_KEY not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))

    with pytest.raises(
        sanitizer.SanitizationError,
        match=expected_error,
    ) as exc_info:
        sanitizer.sanitize_har_to_contract_pack(
            input_har=input_har,
            output_dir=output_dir,
            profile_version=PROFILE_VERSION_V3,
            entry_indices=[0, 1, 2],
        )

    _assert_sanitizer_traceback_is_redacted(exc_info.value, TODO_SESSION_KEY)


@pytest.mark.parametrize("session_key", ["s" * 68, "s" * 70])
def test_todo_list_v3_requires_the_exact_session_key_length_without_output(
    tmp_path: Path,
    session_key: str,
) -> None:
    har, _raw_records = _todo_list_har(session_key=session_key)
    input_har = tmp_path / "todo-list-session-length.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION_V3

    with pytest.raises(
        sanitizer.SanitizationError,
        match="todo_list_session_association_invalid",
    ) as exc_info:
        sanitizer.sanitize_har_to_contract_pack(
            input_har=input_har,
            output_dir=output_dir,
            profile_version=PROFILE_VERSION_V3,
            entry_indices=[0, 1, 2],
        )

    _assert_sanitizer_traceback_is_redacted(exc_info.value, session_key)
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_todo_list_v3_rejects_duplicate_source_todo_ids_without_output(
    tmp_path: Path,
) -> None:
    har, raw_records = _todo_list_har()
    raw_records[1]["requestid"] = raw_records[0]["requestid"]
    har["log"]["entries"][1]["response"]["content"]["text"] = json.dumps(
        {"datas": raw_records, "status": True}
    )
    input_har = tmp_path / "todo-list-duplicate.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION_V3

    with pytest.raises(
        sanitizer.SanitizationError,
        match="todo_list_duplicate_record",
    ):
        sanitizer.sanitize_har_to_contract_pack(
            input_har=input_har,
            output_dir=output_dir,
            profile_version=PROFILE_VERSION_V3,
            entry_indices=[0, 1, 2],
        )

    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_todo_list_v3_rejects_message_center_substitution(tmp_path: Path) -> None:
    har, _raw_records = _todo_list_har()
    har["log"]["entries"][1]["request"]["url"] = (
        "https://synthetic.invalid/api/ec/dev/message/getMsgList"
    )
    input_har = tmp_path / "wrong-source.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION_V3

    completed = _run_script(input_har, output_dir, entry_indices=[0, 1, 2])

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == (
        "sanitization failed: todo_list_entry_selection_invalid\n"
    )
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_todo_list_v3_rejects_html_in_selected_bare_fields(tmp_path: Path) -> None:
    har, raw_records = _todo_list_har()
    raw_records[0]["requestname"] = "<span>synthetic raw title</span>"
    har["log"]["entries"][1]["response"]["content"]["text"] = json.dumps(
        {"datas": raw_records, "status": True}
    )
    input_har = tmp_path / "html-title.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION_V3

    completed = _run_script(input_har, output_dir, entry_indices=[0, 1, 2])

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == (
        "sanitization failed: todo_list_html_field_invalid\n"
    )
    assert "synthetic raw title" not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


@pytest.mark.parametrize(
    ("entry_indices", "expected_error"),
    [
        (None, "todo_list_entry_indices_required"),
        ([0, 1], "todo_list_entry_indices_invalid"),
        ([0, 1, 1], "todo_list_entry_indices_invalid"),
    ],
)
def test_todo_list_v3_requires_three_distinct_explicit_entries(
    tmp_path: Path,
    entry_indices: list[int] | None,
    expected_error: str,
) -> None:
    har, _raw_records = _todo_list_har()
    input_har = tmp_path / "entry-selection.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION_V3

    completed = _run_script(input_har, output_dir, entry_indices=entry_indices)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == f"sanitization failed: {expected_error}\n"
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_todo_list_session_value_is_collected_before_candidate_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    har, _raw_records = _todo_list_har()
    input_har = tmp_path / "session-leak.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION_V3

    monkeypatch.setattr(
        sanitizer,
        "_normalize_todo_list_sample",
        lambda _datas, _counts: {
            "workflows": [
                {
                    "todo_id": TODO_SESSION_KEY,
                    "title": "synthetic title",
                    "status": "pending",
                    "received_at": "2000-01-01",
                    "created_at": "2000-01-01",
                    "workflow_type_id": "synthetic-type",
                }
            ],
            "returned_count": 1,
            "authoritative_count": 1,
            "is_complete": True,
        },
    )

    with pytest.raises(
        sanitizer.SanitizationError,
        match="raw_sensitive_value_survived",
    ) as exc_info:
        sanitizer.sanitize_har_to_contract_pack(
            input_har=input_har,
            output_dir=output_dir,
            profile_version=PROFILE_VERSION_V3,
            entry_indices=[0, 1, 2],
        )

    assert TODO_SESSION_KEY not in str(exc_info.value)
    assert exc_info.value.__context__ is None
    assert exc_info.value.__cause__ is None
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_pending_capture_category_has_no_default_and_fails_closed(
    tmp_path: Path,
) -> None:
    input_har = tmp_path / "no-declared-category.har"
    input_har.write_text(
        json.dumps(_har(category_id=PENDING_CATEGORY_ID)),
        encoding="utf-8",
    )
    output_dir = tmp_path / PROFILE_VERSION_V2

    completed = _run_script(input_har, output_dir, pending_category_id=None)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert (
        completed.stderr
        == "sanitization failed: pending_capture_category_required\n"
    )
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


@pytest.mark.parametrize("declared", ["", " 217", "217 "])
def test_declared_pending_category_must_be_a_clean_value(
    tmp_path: Path,
    declared: str,
) -> None:
    input_har = tmp_path / "declared-category-invalid.har"
    input_har.write_text(
        json.dumps(_har(category_id=PENDING_CATEGORY_ID)),
        encoding="utf-8",
    )
    output_dir = tmp_path / PROFILE_VERSION_V2

    completed = _run_script(input_har, output_dir, pending_category_id=declared)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert (
        completed.stderr
        == "sanitization failed: pending_capture_category_invalid\n"
    )
    assert not output_dir.exists()


def test_pending_capture_without_a_recorded_category_fails_closed(
    tmp_path: Path,
) -> None:
    har = _har(category_id=PENDING_CATEGORY_ID)
    post_data = har["log"]["entries"][0]["request"]["postData"]
    post_data["params"] = [{"name": "pagesize", "value": "20"}]
    post_data["text"] = "pagesize=20"
    input_har = tmp_path / "unrecorded-category.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION_V2

    completed = _run_script(input_har, output_dir)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "sanitization failed: capture_category_id_invalid\n"
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_declared_pending_category_is_rejected_for_other_profiles(
    tmp_path: Path,
) -> None:
    har, _raw_records = _system_message_har()
    input_har = tmp_path / "system-messages.har"
    input_har.write_text(json.dumps(har, ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / SYSTEM_MESSAGE_PROFILE_VERSION

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[1],
        extra_args=["--pending-category-id", PENDING_CATEGORY_ID],
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert (
        completed.stderr
        == "sanitization failed: pending_capture_category_not_applicable\n"
    )
    assert not output_dir.exists()


def test_system_message_capture_is_shape_preserving_and_explicitly_partial(
    tmp_path: Path,
) -> None:
    har, raw_records = _system_message_har()
    input_har = tmp_path / "system-messages.har"
    input_har.write_text(json.dumps(har, ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / SYSTEM_MESSAGE_PROFILE_VERSION

    completed = _run_script(input_har, output_dir, entry_indices=[1])

    assert completed.returncode == 0
    assert completed.stderr == ""
    profile = json.loads((output_dir / "profile.json").read_text(encoding="utf-8"))
    sample = json.loads((output_dir / "sample.json").read_text(encoding="utf-8"))
    assert profile == {
        "profile_version": SYSTEM_MESSAGE_PROFILE_VERSION,
        "capability_id": "oa.list_system_messages",
        "source_kind": "externally_sanitized_capture",
        "sanitizer_version": "2",
        "sample_file": "sample.json",
        "fingerprint_file": "fingerprint.json",
        "source_warning": EXTERNAL_SANITIZATION_WARNING,
    }
    assert sample["returned_count"] == 2
    assert sample["is_complete"] is False
    assert len(sample["messages"]) == len(raw_records)
    for raw_record, synthetic_record in zip(raw_records, sample["messages"], strict=True):
        for raw_key, synthetic_key in (
            ("messageid", "message_id"),
            ("title", "title"),
            ("context", "content"),
            ("name", "source_name"),
            ("time", "occurred_at"),
            ("bizstate", "business_state"),
        ):
            assert len(synthetic_record[synthetic_key]) == len(raw_record[raw_key])
        assert all("\u4e00" <= character <= "\u9fff" for character in synthetic_record["title"])
        assert all("\u4e00" <= character <= "\u9fff" for character in synthetic_record["content"])
    assert sample["messages"][0]["link"].startswith("/")
    assert len(sample["messages"][0]["link"]) == len(raw_records[0]["link"])
    assert sample["messages"][1]["link"] is None
    assert sample["messages"][1]["mobile_link"] is None
    assert json.loads(
        (output_dir / "fingerprint.json").read_text(encoding="utf-8")
    ) == build_structural_fingerprint(sample)
    all_output = "\n".join(
        path.read_text(encoding="utf-8") for path in output_dir.iterdir()
    )
    for raw_record in raw_records:
        for key in (
            "messageid",
            "title",
            "context",
            "name",
            "time",
            "link",
            "linkmobileurl",
        ):
            raw_value = raw_record[key]
            if raw_value:
                assert raw_value not in all_output


def test_live_system_message_har_fingerprint_uses_actual_value_free_shape(
    tmp_path: Path,
) -> None:
    har, raw_records = _system_message_har()
    input_har = tmp_path / "system-messages.har"
    input_har.write_text(json.dumps(har, ensure_ascii=False), encoding="utf-8")

    fingerprint = sanitizer.build_live_system_message_har_fingerprint(
        input_har=input_har,
        entry_index=1,
    )

    nodes = {
        (node["path"], node["json_type"])
        for node in fingerprint["nodes"]
    }
    assert not {
        "$.messages[].wire_gomethod",
        "$.messages[].wire_gomethodpc",
        "$.messages[].wire_showimage",
    }.intersection(path for path, _json_type in nodes)
    rendered = json.dumps(fingerprint, sort_keys=True)
    for record in raw_records:
        for value in record.values():
            if len(value) >= 9:
                assert value not in rendered


def test_selected_textual_base64_matches_plaintext_pack(tmp_path: Path) -> None:
    plain_har, _raw_records = _system_message_har()
    encoded_har = json.loads(json.dumps(plain_har))
    encoded_content = encoded_har["log"]["entries"][1]["response"]["content"]
    plaintext = encoded_content["text"].encode("utf-8")
    encoded_content["encoding"] = "base64"
    encoded_content["text"] = base64.b64encode(plaintext).decode("ascii")
    plain_input = tmp_path / "plain-system-message.har"
    encoded_input = tmp_path / "base64-system-message.har"
    plain_input.write_text(json.dumps(plain_har, ensure_ascii=False), encoding="utf-8")
    encoded_input.write_text(
        json.dumps(encoded_har, ensure_ascii=False),
        encoding="utf-8",
    )
    plain_parent = tmp_path / "plain"
    encoded_parent = tmp_path / "encoded"
    plain_parent.mkdir()
    encoded_parent.mkdir()
    plain_output = plain_parent / SYSTEM_MESSAGE_PROFILE_VERSION
    encoded_output = encoded_parent / SYSTEM_MESSAGE_PROFILE_VERSION

    plain_completed = _run_script(plain_input, plain_output, entry_indices=[1])
    encoded_completed = _run_script(encoded_input, encoded_output, entry_indices=[1])

    assert plain_completed.returncode == 0
    assert encoded_completed.returncode == 0
    for file_name in ("profile.json", "sample.json", "fingerprint.json"):
        assert (encoded_output / file_name).read_bytes() == (
            plain_output / file_name
        ).read_bytes()


@pytest.mark.parametrize(
    "page_size_case",
    ("missing", "non_numeric", "zero", "too_large", "disagrees"),
)
def test_system_message_page_size_failures_leave_zero_output(
    tmp_path: Path,
    page_size_case: str,
) -> None:
    har, _raw_records = _system_message_har()
    post_data = har["log"]["entries"][1]["request"]["postData"]
    if page_size_case == "missing":
        post_data["params"] = [{"name": "selectState", "value": "0"}]
        post_data["text"] = "selectState=0"
    elif page_size_case == "disagrees":
        post_data["text"] = "pagesize=5&selectState=0"
    else:
        invalid_value = {
            "non_numeric": "abc",
            "zero": "0",
            "too_large": "10001",
        }[page_size_case]
        post_data["params"][0]["value"] = invalid_value
        post_data["text"] = f"pagesize={invalid_value}&selectState=0"
    input_har = tmp_path / f"system-message-page-size-{page_size_case}.har"
    input_har.write_text(json.dumps(har, ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / SYSTEM_MESSAGE_PROFILE_VERSION

    completed = _run_script(input_har, output_dir, entry_indices=[1])

    assert completed.returncode == 2
    assert "system_message_page_size_invalid" in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_system_message_short_page_is_explicitly_complete(tmp_path: Path) -> None:
    har, _raw_records = _system_message_har()
    post_data = har["log"]["entries"][1]["request"]["postData"]
    post_data["params"][0]["value"] = "3"
    post_data["text"] = "pagesize=3&selectState=0"
    input_har = tmp_path / "system-message-short-page.har"
    input_har.write_text(json.dumps(har, ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / SYSTEM_MESSAGE_PROFILE_VERSION

    completed = _run_script(input_har, output_dir, entry_indices=[1])

    assert completed.returncode == 0
    sample = json.loads((output_dir / "sample.json").read_text(encoding="utf-8"))
    assert sample["returned_count"] == 2
    assert sample["is_complete"] is True


def test_system_message_records_cannot_exceed_page_size(tmp_path: Path) -> None:
    har, _raw_records = _system_message_har()
    post_data = har["log"]["entries"][1]["request"]["postData"]
    post_data["params"][0]["value"] = "1"
    post_data["text"] = "pagesize=1&selectState=0"
    input_har = tmp_path / "system-message-overfull-page.har"
    input_har.write_text(json.dumps(har, ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / SYSTEM_MESSAGE_PROFILE_VERSION

    completed = _run_script(input_har, output_dir, entry_indices=[1])

    assert completed.returncode == 2
    assert "system_message_page_size_invalid" in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_selected_entry_must_have_system_message_shape(tmp_path: Path) -> None:
    har, _raw_records = _system_message_har()
    content = har["log"]["entries"][1]["response"]["content"]
    content["text"] = json.dumps({"data": {"records": []}})
    input_har = tmp_path / "wrong-system-message-shape.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / SYSTEM_MESSAGE_PROFILE_VERSION

    completed = _run_script(input_har, output_dir, entry_indices=[1])

    assert completed.returncode == 2
    assert "selected_entry_not_system_message_response" in completed.stderr
    assert not output_dir.exists()


def test_selected_system_message_response_requires_text(tmp_path: Path) -> None:
    har, _raw_records = _system_message_har()
    har["log"]["entries"][1]["response"]["content"].pop("text")
    input_har = tmp_path / "missing-system-message-text.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / SYSTEM_MESSAGE_PROFILE_VERSION

    completed = _run_script(input_har, output_dir, entry_indices=[1])

    assert completed.returncode == 2
    assert "selected_entry_invalid" in completed.stderr
    assert not output_dir.exists()


def test_system_message_auto_selection_requires_one_candidate(tmp_path: Path) -> None:
    har, _raw_records = _system_message_har()
    duplicate = json.loads(json.dumps(har["log"]["entries"][1]))
    har["log"]["entries"].append(duplicate)
    input_har = tmp_path / "ambiguous-system-message.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / SYSTEM_MESSAGE_PROFILE_VERSION

    completed = _run_script(input_har, output_dir)

    assert completed.returncode == 2
    assert "system_message_response_not_unique" in completed.stderr
    assert not output_dir.exists()


def test_system_message_profile_accepts_only_one_entry_index(tmp_path: Path) -> None:
    har, _raw_records = _system_message_har()
    input_har = tmp_path / "multiple-system-message-indices.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / SYSTEM_MESSAGE_PROFILE_VERSION

    completed = _run_script(input_har, output_dir, entry_indices=[1, 1])

    assert completed.returncode == 2
    assert "system_message_entry_index_invalid" in completed.stderr
    assert not output_dir.exists()


def test_system_message_optional_fields_reject_invalid_types(tmp_path: Path) -> None:
    har, _raw_records = _system_message_har()
    content = har["log"]["entries"][1]["response"]["content"]
    payload = json.loads(content["text"])
    payload["data"][0]["link"] = 42
    content["text"] = json.dumps(payload, ensure_ascii=False)
    input_har = tmp_path / "invalid-system-message-link.har"
    input_har.write_text(json.dumps(har, ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / SYSTEM_MESSAGE_PROFILE_VERSION

    completed = _run_script(input_har, output_dir, entry_indices=[1])

    assert completed.returncode == 2
    assert "response_optional_string_invalid" in completed.stderr
    assert not output_dir.exists()


def test_non_external_source_keeps_short_transport_values_strict() -> None:
    har, _raw_records = _system_message_har()
    request = har["log"]["entries"][1]["request"]
    header_value = "external-session-header-value"
    parameter_value = "external-account-password-value"
    request["headers"] = [{"name": "Cookie", "value": header_value}]
    request["cookies"] = [{"name": "r", "value": "7"}]
    request["url"] = f"https://synthetic.invalid/messages?password={parameter_value}"

    sensitive_values = sanitizer._collect_sensitive_values(har)

    assert header_value in sensitive_values
    assert "7" in sensitive_values
    assert parameter_value in sensitive_values


def test_external_source_long_session_cookie_cannot_survive_in_output() -> None:
    har, _raw_records = _system_message_har()
    synthetic_session_cookie = (
        "Q7mV2pN9xK4rT8wY1cD6fH3jL5sA0bE2uG7zC9qM4nR8tP1vX6kF3dJ5"
    )
    request = har["log"]["entries"][1]["request"]
    request["headers"] = [
        {
            "name": "Cookie",
            "value": f"ecology_JSessionid={synthetic_session_cookie}",
        }
    ]
    request["cookies"] = [
        {"name": "ecology_JSessionid", "value": synthetic_session_cookie}
    ]
    sensitive_values = sanitizer._collect_sensitive_values(
        har,
        short_transport_as_full_token=True,
    )
    candidate_payloads = {
        "sample.json": {"leaked_value": synthetic_session_cookie}
    }

    with pytest.raises(
        sanitizer.SanitizationError,
        match="raw_sensitive_value_survived",
    ):
        sanitizer._assert_sensitive_values_absent(
            sensitive_values,
            candidate_payloads,
        )


def test_external_short_cookie_is_checked_as_full_transport_token() -> None:
    har, _raw_records = _system_message_har()
    request = har["log"]["entries"][1]["request"]
    request["headers"] = [{"name": "Token", "value": "7"}]
    request["cookies"] = [{"name": "r", "value": "7"}]
    sensitive_values = sanitizer._collect_sensitive_values(
        har,
        short_transport_as_full_token=True,
    )

    assert "7" not in sensitive_values
    assert "r" not in sensitive_values
    assert "Token: 7" in sensitive_values
    assert "r=7" in sensitive_values
    sanitizer._assert_sensitive_values_absent(
        sensitive_values,
        {"sample.json": {"business_state": "7"}},
    )

    with pytest.raises(
        sanitizer.SanitizationError,
        match="raw_sensitive_value_survived",
    ):
        sanitizer._assert_sensitive_values_absent(
            sensitive_values,
            {"sample.json": {"transport": "r=7"}},
        )

    with pytest.raises(
        sanitizer.SanitizationError,
        match="raw_sensitive_value_survived",
    ):
        sanitizer._assert_sensitive_values_absent(
            sensitive_values,
            {"sample.json": {"transport": "Token: 7"}},
        )


def test_short_sensitive_value_requires_complete_token_match() -> None:
    short_value = "Q7mV2"

    sanitizer._assert_sensitive_values_absent(
        {short_value},
        {"sample.json": {"synthetic": f"prefix{short_value}suffix"}},
    )

    with pytest.raises(
        sanitizer.SanitizationError,
        match="raw_sensitive_value_survived",
    ):
        sanitizer._assert_sensitive_values_absent(
            {short_value},
            {"sample.json": {"synthetic": f"prefix-{short_value}-suffix"}},
        )


@pytest.mark.parametrize(
    ("length", "substring_must_match"),
    [
        (sanitizer._MIN_SENSITIVE_SUBSTRING_LENGTH - 1, False),
        (sanitizer._MIN_SENSITIVE_SUBSTRING_LENGTH, True),
    ],
)
def test_sensitive_substring_matching_starts_at_threshold(
    length: int,
    substring_must_match: bool,
) -> None:
    sensitive_value = ("Q7mV2pN9xK4rT8wY" * 2)[:length]
    candidate_payloads = {
        "sample.json": {"synthetic": f"prefix{sensitive_value}suffix"}
    }

    if substring_must_match:
        with pytest.raises(
            sanitizer.SanitizationError,
            match="raw_sensitive_value_survived",
        ):
            sanitizer._assert_sensitive_values_absent(
                {sensitive_value},
                candidate_payloads,
            )
    else:
        sanitizer._assert_sensitive_values_absent(
            {sensitive_value},
            candidate_payloads,
        )


def test_sensitive_profile_version_is_rejected_without_output(
    tmp_path: Path,
) -> None:
    sensitive_profile = "fake-workcode-927315"
    input_har = tmp_path / "synthetic.har"
    input_har.write_text(json.dumps(_har()), encoding="utf-8")
    output_dir = tmp_path / sensitive_profile

    completed = _run_script(input_har, output_dir)

    rendered_output = (
        "\n".join(
            path.read_text(encoding="utf-8")
            for path in output_dir.iterdir()
            if path.is_file()
        )
        if output_dir.exists()
        else ""
    )
    assert sensitive_profile not in rendered_output
    assert sensitive_profile not in completed.stdout
    assert sensitive_profile not in completed.stderr
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "sanitization failed: invalid_profile_version\n"
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


@pytest.mark.parametrize(
    "profile_version",
    [
        "ecology9-pending-workflows-v4",
        "ecology9-pending-workflows-v927",
        "ecology9-system-messages-v2",
    ],
)
def test_unapproved_profile_revision_fails_closed_without_output(
    tmp_path: Path,
    profile_version: str,
) -> None:
    input_har = tmp_path / "synthetic.har"
    input_har.write_text(json.dumps(_har()), encoding="utf-8")
    output_dir = tmp_path / profile_version

    completed = _run_script(input_har, output_dir)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "sanitization failed: invalid_profile_version\n"
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_v2_sensitive_value_still_exits_two_without_output(
    tmp_path: Path,
) -> None:
    har = _har()
    har["log"]["entries"].append(
        {
            "request": {
                "headers": [
                    {
                        "name": "Cookie",
                        "value": "derived_from_sibling_capture",
                    }
                ],
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
    input_har = tmp_path / "v2-sensitive.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION_V2

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[0],
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "sanitization failed: raw_sensitive_value_survived\n"
    assert "pending" not in completed.stdout
    assert "pending" not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


@pytest.mark.parametrize(
    "sensitive_cli_value",
    [
        "FAKE_SESSIONID_ARGPARSE_927315",
        "FAKE%5FSESSIONID%5FARGPARSE%5F927315",
        "RkFLRV9TRVNTSU9OSURfQVJHUEFSU0VfOTI3MzE1",
    ],
    ids=["plain", "url-encoded", "base64"],
)
def test_invalid_cli_value_is_rejected_without_echo_or_output(
    tmp_path: Path,
    sensitive_cli_value: str,
) -> None:
    input_har = tmp_path / "synthetic.har"
    input_har.write_text(json.dumps(_har()), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[sensitive_cli_value],
    )

    assert sensitive_cli_value not in completed.stdout
    assert sensitive_cli_value not in completed.stderr
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "sanitization failed: entry_index_invalid\n"
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))

    with pytest.raises(
        sanitizer.SanitizationError,
        match="entry_index_invalid",
    ) as exc_info:
        sanitizer._parse_entry_indices([sensitive_cli_value])

    assert exc_info.value.__context__ is None
    assert exc_info.value.__cause__ is None
    assert sensitive_cli_value not in (repr(exc_info.value) + str(exc_info.value))


@pytest.mark.parametrize(
    ("extra_args", "sensitive_cli_value"),
    [
        (
            ["--unknown-option", "FAKE_UNKNOWN_ARG_927315"],
            "FAKE_UNKNOWN_ARG_927315",
        ),
        (["--entry-index"], "FAKE_MISSING_VALUE_CONTEXT_927315"),
    ],
    ids=["unknown-option", "missing-option-value"],
)
def test_cli_syntax_errors_do_not_echo_known_values_or_retain_exception_context(
    tmp_path: Path,
    extra_args: list[str],
    sensitive_cli_value: str,
) -> None:
    input_har = tmp_path / f"{sensitive_cli_value}.har"
    input_har.write_text(json.dumps(_har()), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION

    completed = _run_script(
        input_har,
        output_dir,
        extra_args=extra_args,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "sanitization failed: invalid_arguments\n"
    assert sensitive_cli_value not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))

    cli_args = [
        "--input-har",
        str(input_har),
        "--output-dir",
        str(output_dir),
        "--profile-version",
        PROFILE_VERSION,
        *extra_args,
    ]
    with pytest.raises(
        sanitizer.SanitizationError,
        match="invalid_arguments",
    ) as exc_info:
        sanitizer._parse_cli_args(cli_args)

    assert exc_info.value.__context__ is None
    assert exc_info.value.__cause__ is None
    assert sensitive_cli_value not in (repr(exc_info.value) + str(exc_info.value))


def test_pending_profile_rejects_multiple_selected_message_center_pages(
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
    second_page = _har(status="2")["log"]["entries"][0]
    har["log"]["entries"] = [first_page, unrelated_entry, second_page]
    input_har = tmp_path / "multi-page.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[2, 0],
    )

    assert completed.returncode == 2
    assert "pending_workflow_entry_index_invalid" in completed.stderr
    assert not output_dir.exists()


def test_explicit_single_entry_selects_one_page_from_multiple_candidates(
    tmp_path: Path,
) -> None:
    har = _har()
    selected_page = _har(status="2")["log"]["entries"][0]
    har["log"]["entries"].append(selected_page)
    input_har = tmp_path / "single-selected-page.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[1],
    )

    assert completed.returncode == 0
    sample = json.loads((output_dir / "sample.json").read_text(encoding="utf-8"))
    assert len(sample["workflows"]) == 1
    assert sample["workflows"][0]["business_state"] == "2"
    assert sample["returned_count"] == 1
    assert sample["is_complete"] is True


def test_multiple_candidates_without_selector_fail_with_zero_output(
    tmp_path: Path,
) -> None:
    har = _har()
    har["log"]["entries"].append(_har()["log"]["entries"][0])
    input_har = tmp_path / "multiple-candidates.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "pending_workflow_response_not_unique" in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_sanitizer_accepts_empty_pending_workflow_list(tmp_path: Path) -> None:
    har = _har()
    entry = har["log"]["entries"][0]
    response_body = json.loads(entry["response"]["content"]["text"])
    response_body["data"] = []
    entry["response"]["content"]["text"] = json.dumps(response_body)
    input_har = tmp_path / "empty.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION

    completed = _run_script(input_har, output_dir)

    assert completed.returncode == 0
    sample = json.loads((output_dir / "sample.json").read_text(encoding="utf-8"))
    fingerprint = json.loads(
        (output_dir / "fingerprint.json").read_text(encoding="utf-8")
    )
    assert sample == {
        "workflows": [],
        "returned_count": 0,
        "is_complete": True,
    }
    assert fingerprint == build_structural_fingerprint(sample)
    assert fingerprint == build_structural_fingerprint(
        {
            "workflows": [
                {
                    "message_id": "different",
                    "title": "different",
                    "content": "different",
                    "source_name": "different",
                    "occurred_at": "different",
                    "business_state": "different",
                    "link": None,
                    "mobile_link": None,
                }
            ],
            "returned_count": 1,
            "is_complete": False,
        }
    )


def test_cookie_from_unselected_entry_is_scanned_across_whole_har(
    tmp_path: Path,
) -> None:
    har = _har()
    har["log"]["entries"].append(
        {
            "request": {
                "headers": [
                    {
                        "name": "Cookie",
                        "value": "derived_from_sibling_capture",
                    }
                ],
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
    output_dir = tmp_path / PROFILE_VERSION

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
    output_dir = tmp_path / PROFILE_VERSION

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


def test_invalid_base64_unselected_response_remains_fail_closed_with_selector(
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
    output_dir = tmp_path / PROFILE_VERSION

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[0],
    )

    assert completed.returncode != 0
    assert "encoded_response_invalid" in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_unsupported_unselected_response_encoding_remains_fail_closed(
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
                    "encoding": "gzip",
                    "text": "opaque-encoded-response",
                },
            },
        }
    )
    input_har = tmp_path / "multi-entry-unsupported-encoding.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION

    completed = _run_script(input_har, output_dir, entry_indices=[0])

    assert completed.returncode == 2
    assert "encoded_response_not_supported" in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_selected_response_is_checked_for_forbidden_keys(
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
    output_dir = tmp_path / PROFILE_VERSION

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[1],
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
        ([0, 0], "pending_workflow_entry_index_invalid"),
    ],
)
def test_invalid_entry_indices_fail_with_zero_output(
    tmp_path: Path,
    entry_indices: list[int],
    expected_error: str,
) -> None:
    input_har = tmp_path / "selector-invalid.har"
    input_har.write_text(json.dumps(_har()), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION

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
    output_dir = tmp_path / PROFILE_VERSION

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
    output_dir = tmp_path / PROFILE_VERSION

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
    output_dir = tmp_path / PROFILE_VERSION

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
    output_dir = tmp_path / PROFILE_VERSION

    completed = _run_script(
        input_har,
        output_dir,
        entry_indices=[1],
    )

    assert completed.returncode != 0
    assert "selected_entry_invalid" in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_cookie_value_reaching_candidate_metadata_fails_with_zero_output(
    tmp_path: Path,
) -> None:
    input_har = tmp_path / "cookie-leak.har"
    input_har.write_text(
        json.dumps(_har(cookie_value="derived_from_sibling_capture")),
        encoding="utf-8",
    )
    output_dir = tmp_path / PROFILE_VERSION

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
    output_dir = tmp_path / PROFILE_VERSION

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
    output_dir = tmp_path / PROFILE_VERSION

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
    output_dir = tmp_path / PROFILE_VERSION

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
    output_dir = tmp_path / PROFILE_VERSION

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
    output_dir = tmp_path / PROFILE_VERSION

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "raw_sensitive_value_survived" in completed.stderr
    assert other_response_value not in completed.stdout
    assert other_response_value not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_sensitive_value_in_base64_json_response_cannot_reach_output(
    tmp_path: Path,
) -> None:
    other_response_value = "pending"
    encoded_payload = base64.b64encode(
        json.dumps(
            {"unrelated": {"accessToken": other_response_value}}
        ).encode("utf-8")
    ).decode("ascii")
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
                    "text": encoded_payload,
                },
            },
        }
    )
    input_har = tmp_path / "base64-other-response.har"
    input_har.write_text(json.dumps(har), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION

    completed = _run_script(input_har, output_dir)

    assert completed.returncode == 2
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
    output_dir = tmp_path / PROFILE_VERSION

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
    output_dir = tmp_path / PROFILE_VERSION

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
    output_dir = tmp_path / PROFILE_VERSION

    completed = _run_script(input_har, output_dir)

    assert completed.returncode != 0
    assert "json_container_too_large" in completed.stderr
    assert sensitive_marker not in completed.stdout
    assert sensitive_marker not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))


def test_unmapped_business_state_is_not_copied_to_output(tmp_path: Path) -> None:
    private_status = "private-business-status-927315"
    input_har = tmp_path / "unrecognized-status.har"
    input_har.write_text(
        json.dumps(_har(status=private_status)),
        encoding="utf-8",
    )
    output_dir = tmp_path / PROFILE_VERSION

    completed = _run_script(input_har, output_dir)

    assert completed.returncode == 0
    assert private_status not in completed.stdout
    assert private_status not in completed.stderr
    rendered = "\n".join(
        path.read_text(encoding="utf-8") for path in output_dir.iterdir()
    )
    assert private_status not in rendered


def test_existing_output_is_rejected_without_overwrite_or_delete(tmp_path: Path) -> None:
    input_har = tmp_path / "synthetic.har"
    input_har.write_text(json.dumps(_har()), encoding="utf-8")
    output_dir = tmp_path / PROFILE_VERSION
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
    output_dir = tmp_path / PROFILE_VERSION
    forbidden_marker = "aaaaaaaa.bbbbbbbb.cccccccc"
    write_calls = 0

    def record_write(_path: Path, _payload: Any) -> None:
        nonlocal write_calls
        write_calls += 1

    monkeypatch.setattr(sanitizer, "_write_json", record_write)
    monkeypatch.setattr(
        sanitizer,
        "build_structural_fingerprint",
        lambda _sample: {"algorithm": forbidden_marker},
    )

    with pytest.raises(
        sanitizer.SanitizationError,
        match="forbidden_output_value",
    ) as exc_info:
        sanitizer.sanitize_har_to_contract_pack(
            input_har=input_har,
            output_dir=output_dir,
            profile_version=output_dir.name,
            pending_capture_category_id=PENDING_CATEGORY_ID,
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
    output_dir = tmp_path / PROFILE_VERSION

    with pytest.raises(
        sanitizer.SanitizationError,
        match="json_unreadable",
    ) as exc_info:
        sanitizer.sanitize_har_to_contract_pack(
            input_har=input_har,
            output_dir=output_dir,
            profile_version=output_dir.name,
            pending_capture_category_id=PENDING_CATEGORY_ID,
        )

    assert exc_info.value.__context__ is None
    assert exc_info.value.__cause__ is None
    assert sensitive_marker not in (repr(exc_info.value) + str(exc_info.value))
    assert not output_dir.exists()
    assert not list(tmp_path.glob(f".{output_dir.name}.*"))
