"""Explicit one-Run client for the fixed synthetic browser installation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx2

from app.browser_skill.run_contracts import BrowserAcceptedView
from app.infra.adapters.oa.contracts import OASystemMessageCollection
from app.infra.browser.fixed_synthetic_seed import _COLLECTION, SYNTHETIC_KEY, SYNTHETIC_USER
from app.infra.browser.synthetic_private_input import read_private_passphrase
from app.infra.browser.synthetic_trial import (
    DIAGNOSTIC_RUN_ID,
    DIAGNOSTIC_TASK_ID,
    DIAGNOSTIC_TRIAL,
    LEGACY_RUN_ID,
    LEGACY_TASK_ID,
    OBSERVE_TRIAL,
    ORIGINAL_TRIAL,
    VISIBLE_RUN_ID,
    VISIBLE_TASK_ID,
    VISIBLE_TRIAL,
    approved_attempt_id,
    create_trial_file,
    read_trial_file,
    trial_reference,
    trial_request_id,
)
from app.infra.browser.synthetic_trial import (
    diagnostic_reference as diagnostic_reference,
)
from app.infra.browser.synthetic_vault import (
    BUSINESS_FILE,
    VAULT_DIRECTORY,
    read_encrypted,
    read_private_business_document,
)
from app.ports.browser_chat import BrowserRunResponse
from app.ports.response_envelope import ResponseEnvelope

API_BASE = "http://browser-synthetic-api:8000"
_SKILL_ID = "fixed_synthetic_message_detail"
_REQUEST_ID = "P2-BROWSER-RUNTIME-V42-001-single-run"
_MAX_RESPONSE = 32768
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,96}\Z")


class TrialClientError(RuntimeError):
    """A fixed code only; upstream bodies, credentials and exception text stay private."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class TrialOutcome:
    code: str
    task_id: str | None = None
    run_id: str | None = None
    status: str | None = None
    result_code: str | None = None
    dispatch_failure_code: str | None = None
    exit_code: int = 2


def _receipt(trial_id: str = ORIGINAL_TRIAL, *,
             attempt_id: str | None = None) -> tuple[str, str]:
    try:
        attempt_id = approved_attempt_id(attempt_id, trial_id=trial_id)
        options: dict[str, Any] = {"trial_id": trial_id} if trial_id != ORIGINAL_TRIAL else {}
        if attempt_id is not None:
            options["attempt_id"] = attempt_id
        document = read_trial_file("trial.run.json", **options)
        binding = trial_reference(trial_id, attempt_id=attempt_id)
        if (type(document) is not dict or set(document) != {"task_id", "run_id", *binding}
                or any(document[name] != value for name, value in binding.items())):
            raise ValueError
        task_id, run_id = document["task_id"], document["run_id"]
        if (type(task_id) is not str or type(run_id) is not str
                or _IDENTIFIER.fullmatch(task_id) is None
                or _IDENTIFIER.fullmatch(run_id) is None):
            raise ValueError
        if trial_id == DIAGNOSTIC_TRIAL and (task_id == LEGACY_TASK_ID or run_id == LEGACY_RUN_ID):
            raise ValueError
        if trial_id == VISIBLE_TRIAL and (
            task_id in {LEGACY_TASK_ID, DIAGNOSTIC_TASK_ID}
            or run_id in {LEGACY_RUN_ID, DIAGNOSTIC_RUN_ID}
        ):
            raise ValueError
        if trial_id == OBSERVE_TRIAL and (
            task_id in {LEGACY_TASK_ID, DIAGNOSTIC_TASK_ID, VISIBLE_TASK_ID}
            or run_id in {LEGACY_RUN_ID, DIAGNOSTIC_RUN_ID, VISIBLE_RUN_ID}
        ):
            raise ValueError
        return task_id, run_id
    except Exception:
        raise TrialClientError("browser_trial_receipt_invalid") from None


def _token(*, private_stdin: bool = False, trial_id: str = ORIGINAL_TRIAL,
           attempt_id: str | None = None) -> str:
    try:
        document = (
            (
                read_private_business_document(read_private_passphrase())
                if trial_id == ORIGINAL_TRIAL
                else read_private_business_document(
                    read_private_passphrase(), trial_id=trial_id, attempt_id=attempt_id,
                )
            )
            if private_stdin
            else read_encrypted(VAULT_DIRECTORY / BUSINESS_FILE, expected_name=BUSINESS_FILE)
        )
        if type(document) is not dict or set(document) != {"business_token"}:
            raise ValueError
        token = document["business_token"]
        if (type(token) is not str or not 1 <= len(token) <= 4096
                or token.strip() != token or any(ord(c) < 33 or ord(c) > 126 for c in token)):
            raise ValueError
        return token
    except Exception:
        raise TrialClientError("browser_trial_token_unavailable") from None


async def _exchange(
    client: httpx2.AsyncClient, method: str, path: str, token: str, *,
    payload: dict[str, Any] | None = None, params: dict[str, str] | None = None,
    expected_status: int,
) -> Any:
    try:
        async with client.stream(
            method, path, json=payload, params=params,
            headers={"Origin": API_BASE, "X-EternalAI-CSRF": "1", "Cache-Control": "no-store"},
            cookies={"eternalai_session": token}, follow_redirects=False, timeout=10.0,
        ) as response:
            if response.status_code != expected_status:
                raise TrialClientError("browser_trial_http_rejected")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > _MAX_RESPONSE:
                    raise TrialClientError("browser_trial_response_invalid")
                data.extend(chunk)
        return json.loads(data)
    except TrialClientError:
        raise
    except httpx2.TimeoutException:
        raise TrialClientError("browser_trial_http_timeout") from None
    except httpx2.HTTPError:
        raise TrialClientError("browser_trial_http_unavailable") from None
    except (UnicodeError, ValueError, TypeError):
        raise TrialClientError("browser_trial_response_invalid") from None


def _checked_run(document: Any, task_id: str, run_id: str) -> BrowserRunResponse:
    try:
        # HTTP JSON arrays represent tuple-valued DTO fields such as artifacts.
        # Keep strict JSON validation rather than treating decoded arrays as Python tuples.
        response = BrowserRunResponse.model_validate_json(json.dumps(document, allow_nan=False))
        if (response.run.task_id, response.run.run_id) != (task_id, run_id):
            raise ValueError
        return response
    except Exception:
        raise TrialClientError("browser_trial_response_invalid") from None


async def run_trial(
    operation: Literal["submit", "inspect", "cancel"], *, enabled: bool = False,
    client: httpx2.AsyncClient | None = None,
    private_stdin: bool = False,
    trial_id: str = ORIGINAL_TRIAL,
    attempt_id: str | None = None,
) -> TrialOutcome:
    """Perform exactly one fixed HTTP operation; injected client is for local tests."""
    if enabled is not True or operation not in {"submit", "inspect", "cancel"}:
        raise TrialClientError("browser_trial_disabled")
    try:
        attempt_id = approved_attempt_id(attempt_id, trial_id=trial_id)
        if trial_id != ORIGINAL_TRIAL and not private_stdin:
            raise ValueError
    except ValueError:
        raise TrialClientError("browser_trial_arguments_invalid") from None
    binding = trial_reference(trial_id, attempt_id=attempt_id)
    marker = {"submission_attempts": 1, **binding}
    receipt_options: dict[str, Any] = {"trial_id": trial_id} if trial_id != ORIGINAL_TRIAL else {}
    if attempt_id is not None:
        receipt_options["attempt_id"] = attempt_id
    if operation == "submit":
        try:
            create_trial_file("trial.submit.json", marker, **receipt_options)
        except ValueError:
            # The receipt helper maps all OS create errors to one fixed code.
            # Confirm an existing marker before calling this a duplicate.
            try:
                if read_trial_file("trial.submit.json", **receipt_options) == marker:
                    raise TrialClientError("browser_trial_already_submitted")
            except TrialClientError:
                raise
            except Exception:
                pass
            raise TrialClientError("browser_trial_marker_unavailable") from None
        except Exception:
            raise TrialClientError("browser_trial_marker_unavailable") from None
    else:
        task_id, run_id = _receipt(trial_id, attempt_id=attempt_id)
    token = (_token(private_stdin=True, trial_id=trial_id, attempt_id=attempt_id)
             if private_stdin else _token())
    owned = client is None
    if owned:
        client = httpx2.AsyncClient(
            base_url=API_BASE, trust_env=False, follow_redirects=False, proxy=None,
            transport=httpx2.AsyncHTTPTransport(retries=0, trust_env=False, proxy=None),
        )
    assert client is not None
    try:
        if operation == "submit":
            document = await _exchange(client, "POST", "/api/v1/runtime/handle", token,
                payload={"channel": "api", "session_id": SYNTHETIC_USER,
                         "message": json.dumps(
                             {"business_key": SYNTHETIC_KEY}, separators=(",", ":")
                         ),
                         "client_capabilities": {"browser_async_v1": True,
                                                 "browser_skill_id": _SKILL_ID},
                         "client_request_id": trial_request_id(trial_id, attempt_id=attempt_id)},
                expected_status=200)
            try:
                envelope = ResponseEnvelope.model_validate(document)
                accepted = BrowserAcceptedView.model_validate(envelope.data)
                if (envelope.status != "running" or envelope.task_id != accepted.task_id
                        or _IDENTIFIER.fullmatch(accepted.task_id) is None
                        or _IDENTIFIER.fullmatch(accepted.run_id) is None):
                    raise ValueError
                if trial_id == DIAGNOSTIC_TRIAL and (
                    accepted.task_id == LEGACY_TASK_ID or accepted.run_id == LEGACY_RUN_ID
                ):
                    raise ValueError
                if trial_id == VISIBLE_TRIAL and (
                    accepted.task_id in {LEGACY_TASK_ID, DIAGNOSTIC_TASK_ID}
                    or accepted.run_id in {LEGACY_RUN_ID, DIAGNOSTIC_RUN_ID}
                ):
                    raise ValueError
                if trial_id == OBSERVE_TRIAL and (
                    accepted.task_id in {LEGACY_TASK_ID, DIAGNOSTIC_TASK_ID, VISIBLE_TASK_ID}
                    or accepted.run_id in {LEGACY_RUN_ID, DIAGNOSTIC_RUN_ID, VISIBLE_RUN_ID}
                ):
                    raise ValueError
            except Exception:
                raise TrialClientError("browser_trial_response_invalid") from None
            try:
                create_trial_file("trial.run.json", {"task_id": accepted.task_id,
                                                    "run_id": accepted.run_id, **binding},
                                  **receipt_options)
            except Exception:
                raise TrialClientError("browser_trial_receipt_unavailable") from None
            return TrialOutcome("browser_trial_accepted", accepted.task_id, accepted.run_id,
                                "running", exit_code=0)

        path = f"/api/v1/browser-runs/{task_id}/{run_id}"
        if operation == "cancel":
            document = await _exchange(client, "POST", path + "/cancel", token,
                                       payload={"session_id": SYNTHETIC_USER}, expected_status=202)
        else:
            document = await _exchange(client, "GET", path, token,
                                       params={"session_id": SYNTHETIC_USER}, expected_status=200)
        response = _checked_run(document, task_id, run_id)
        run = response.run
        result = run.result
        def outcome(code: str, *, exit_code: int = 2) -> TrialOutcome:
            return TrialOutcome(
                code, task_id, run_id, run.status,
                result.error_code if result else None,
                result.dispatch_failure_code if result else None, exit_code,
            )

        if operation == "cancel":
            if not run.cancel.requested or response.value is not None:
                raise TrialClientError("browser_trial_response_invalid")
            return outcome("browser_trial_cancel_requested", exit_code=0)
        if run.status not in {"completed", "failed", "cancelled"}:
            return outcome("browser_trial_nonterminal")
        # Observation success requires its own diagnostic receipt. This HTTP
        # inspection never turns an observe-only terminal Run into business success.
        if (trial_id == OBSERVE_TRIAL or run.status != "completed"
                or result is None or result.verification != "verified"):
            return outcome("browser_trial_terminal_unsuccessful")
        if result.cleanup not in {"released", "terminated"}:
            return outcome("browser_trial_cleanup_incomplete")
        try:
            actual = OASystemMessageCollection.model_validate(response.value)
            if (actual.returned_count < 1
                    or actual.model_dump(mode="json") != json.loads(_COLLECTION)):
                raise ValueError
        except Exception:
            return outcome("browser_trial_value_invalid")
        return outcome("browser_trial_verified", exit_code=0)
    finally:
        if owned:
            await client.aclose()
