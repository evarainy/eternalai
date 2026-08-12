"""Run one credential-safe OA smoke through the complete Runtime chain."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from collections.abc import Sequence
from typing import Any, cast
from urllib.request import OpenerDirector, Request

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import BaseModel, ValidationError

from app.api.v1.csrf import CSRF_HEADER_NAME, CSRF_HEADER_VALUE
from app.composition import (
    build_credential_store,
    build_principal_role_reader,
    build_production_components,
)
from app.config import ProductionSettings
from app.contracts.sdui.models import ResponseEnvelope
from app.db.session import make_async_session_factory
from app.event_loop import make_event_loop
from app.infra.adapters.oa.contracts import (
    OAPendingWorkflowCollection,
    OASystemMessageCollection,
)
from app.infra.auth.oa import OACredentialVerifier
from app.infra.llm.openai_compatible import OpenAICompatibleLLMProvider
from app.infra.observability.postgresql_trace import PostgreSQLTraceReader
from app.main import create_app
from app.ports.auth import AuthenticationPort, LoginCredential
from app.ports.llm_provider import LLMProviderPort
from app.ports.trace import TraceQueryPort
from scripts.smoke.capabilities import (
    OA_CAPABILITY_CONTEXT_PROBES,
    REQUIRED_ACTIVE_OA_CAPABILITY_IDS,
)
from scripts.smoke.full_chain_contract import (
    FULL_CHAIN_SCHEMA_VERSION,
    CapabilityFullChainOutcome,
    FullChainFailure,
    FullChainFailureCode,
    FullChainOutcome,
)
from scripts.smoke.trace_contract import REQUIRED_TRACE_EVENTS

_MAX_CREDENTIAL_INPUT_BYTES = 16_384
_DEFAULT_PROBES = tuple(
    zip(
        OA_CAPABILITY_CONTEXT_PROBES,
        REQUIRED_ACTIVE_OA_CAPABILITY_IDS,
        strict=True,
    )
)
_OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "oa.list_pending_workflows": OAPendingWorkflowCollection,
    "oa.list_system_messages": OASystemMessageCollection,
}
_REPLAY_COOKIES = {
    "ecology_JSessionid": "replay-cookie-a",
    "loginidweaver": "replay-cookie-b",
    "loginuuids": "replay-cookie-c",
}


class _FullChainCheckError(RuntimeError):
    def __init__(self, code: FullChainFailureCode) -> None:
        super().__init__(code)
        self.code = code


class _ReplayOAHttpSession:
    def __init__(self, rsa_public_key: str) -> None:
        self._rsa_public_key = rsa_public_key

    async def get_json(
        self,
        path: str,
        parameters: dict[str, str],
    ) -> dict[str, Any]:
        if path == "/rsa/weaver.rsa.GetRsaInfo" and parameters:
            return {
                "rsa_pub": self._rsa_public_key,
                "rsa_code": "replay-code",
                "rsa_flag": "RSA",
            }
        if path == "/api/hrm/usericon/getUserIcon" and parameters:
            return {"lastname": "Replay Smoke User"}
        raise RuntimeError("replay authentication request is invalid")

    async def post_form(
        self,
        path: str,
        fields: dict[str, str],
    ) -> dict[str, Any]:
        if (
            path != "/api/hrm/login/checkLogin"
            or not fields.get("loginid")
            or not fields.get("userpassword")
        ):
            raise RuntimeError("replay authentication form is invalid")
        return {
            "msgcode": "0",
            "loginstatus": True,
            "userid": "replay-oa-user",
        }

    def cookies(self) -> dict[str, str]:
        return dict(_REPLAY_COOKIES)


class _ReplayLLMResponse:
    def __init__(self, capability_id: str) -> None:
        content = {
            "capability_id": capability_id,
            "arguments": {},
            "target_system": "oa",
            "capability_type": "query",
        }
        self._raw = json.dumps(
            {
                "model": "replay-smoke-model",
                "choices": [
                    {"message": {"content": json.dumps(content)}}
                ],
                "usage": {"total_tokens": 1},
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def __enter__(self) -> _ReplayLLMResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def getcode(self) -> int:
        return 200

    def read(self, _limit: int) -> bytes:
        return self._raw


class _ReplayLLMOpener:
    def open(self, request: Request, timeout: float) -> _ReplayLLMResponse:
        if timeout <= 0 or request.data is None:
            raise RuntimeError("replay LLM request is invalid")
        try:
            payload = json.loads(request.data.decode("utf-8"))
            messages = payload["messages"]
            user_message = next(
                item["content"]
                for item in reversed(messages)
                if item.get("role") == "user"
            )
            capability_id = dict(_DEFAULT_PROBES)[user_message]
        except (KeyError, TypeError, ValueError, StopIteration):
            raise RuntimeError("replay LLM request is invalid") from None
        return _ReplayLLMResponse(capability_id)


async def run_full_chain_check(
    settings: ProductionSettings,
    credential: LoginCredential,
    *,
    probes: Sequence[tuple[str, str]] = _DEFAULT_PROBES,
    llm_provider: LLMProviderPort | None = None,
    authentication: AuthenticationPort | None = None,
    trace_query: TraceQueryPort | None = None,
) -> FullChainOutcome:
    """Use production composition and the protected HTTP entry for each probe."""

    resolved_probes = tuple(probes)
    if (
        not resolved_probes
        or len({message for message, _ in resolved_probes})
        != len(resolved_probes)
        or len({capability_id for _, capability_id in resolved_probes})
        != len(resolved_probes)
        or any(
            not message.strip() or capability_id not in _OUTPUT_MODELS
            for message, capability_id in resolved_probes
        )
    ):
        raise _FullChainCheckError("probe_argv_invalid")
    try:
        if not settings.csrf_allowed_origins:
            raise ValueError
        components = build_production_components(
            settings,
            llm_provider=llm_provider,
            authentication=authentication,
        )
        application = create_app(
            runtime=components.runtime,
            admin_registry_service=components.admin_registry_service,
            authentication=components.authentication,
            session_tokens=components.session_tokens,
            session_binder=components.session_binder.bind,
            session_cookie_ttl_seconds=components.session_cookie_ttl_seconds,
            csrf_allowed_origins=settings.csrf_allowed_origins,
            health_checks=dict(components.health_checks),
            health_timeout_seconds=components.health_timeout_seconds,
        )
        resolved_trace_query = trace_query or PostgreSQLTraceReader(
            make_async_session_factory(database_url=settings.database_url)
        )
        origin = sorted(settings.csrf_allowed_origins)[0]
        headers = {
            "Origin": origin,
            CSRF_HEADER_NAME: CSRF_HEADER_VALUE,
        }
        transport = httpx.ASGITransport(app=application)
        client_context = httpx.AsyncClient(
            transport=transport,
            base_url=origin,
        )
    except Exception:
        raise _FullChainCheckError("composition_build_failed") from None
    login_payload = {
        "loginid": credential.loginid.get_secret_value(),
        "userpassword": credential.userpassword.get_secret_value(),
    }
    outcomes: list[CapabilityFullChainOutcome] = []
    try:
        async with client_context as client:
            try:
                login_response = await client.post(
                    "/api/v1/auth/login",
                    headers=headers,
                    json=login_payload,
                )
            except Exception:
                raise _FullChainCheckError("authentication_failed") from None
            if login_response.status_code != 200:
                raise _FullChainCheckError("authentication_failed")
            for message, capability_id in resolved_probes:
                outcomes.append(
                    await _run_capability_probe(
                        client=client,
                        headers=headers,
                        trace_query=resolved_trace_query,
                        message=message,
                        capability_id=capability_id,
                    )
                )
    except _FullChainCheckError:
        raise
    except Exception:
        raise _FullChainCheckError("unknown_error") from None
    finally:
        login_payload.clear()
        headers.clear()
        resolved_probes = ()

    return FullChainOutcome(
        schema_version=FULL_CHAIN_SCHEMA_VERSION,
        required_trace_event_count=len(REQUIRED_TRACE_EVENTS),
        capabilities=tuple(outcomes),
    )


async def _run_capability_probe(
    *,
    client: httpx.AsyncClient,
    headers: dict[str, str],
    trace_query: TraceQueryPort,
    message: str,
    capability_id: str,
) -> CapabilityFullChainOutcome:
    try:
        response = await client.post(
            "/api/v1/runtime/handle",
            headers=headers,
            json={
                "channel": "cli",
                "session_id": "oa-smoke-full-chain",
                "message": message,
                "client_capabilities": {},
            },
        )
    except Exception:
        raise _FullChainCheckError("runtime_request_failed") from None
    if response.status_code != 200:
        raise _FullChainCheckError("runtime_request_failed")
    try:
        envelope = ResponseEnvelope.model_validate(response.json(), strict=True)
    except (ValueError, ValidationError):
        raise _FullChainCheckError("envelope_invalid") from None

    normalized_data = False
    try:
        _OUTPUT_MODELS[capability_id].model_validate(envelope.data, strict=True)
        normalized_data = True
    except (KeyError, ValidationError):
        normalized_data = False

    try:
        trace_events = await trace_query.list_events_by_trace(envelope.trace_id)
        event_types = frozenset(event.event_type for event in trace_events)
        selected_capability_ids = {
            event.capability_id
            for event in trace_events
            if event.event_type == "capability_selected"
        }
        return CapabilityFullChainOutcome(
            capability_id=capability_id,
            successful_envelope=envelope.status == "completed",
            normalized_data=normalized_data,
            selected_capability=selected_capability_ids == {capability_id},
            trace_events_complete=REQUIRED_TRACE_EVENTS <= event_types,
            observed_trace_event_count=len(event_types),
        )
    except Exception:
        raise _FullChainCheckError("trace_incomplete") from None


def _build_replay_dependencies(
    settings: ProductionSettings,
) -> tuple[LLMProviderPort, AuthenticationPort]:
    if settings.oa_read_adapter_mode != "replay":
        raise RuntimeError("replay dependencies require replay mode")
    session_factory = make_async_session_factory(
        database_url=settings.database_url
    )
    public_key = _replay_rsa_public_key()
    authentication = OACredentialVerifier(
        session_factory=lambda: _ReplayOAHttpSession(public_key),
        credential_store=build_credential_store(
            session_factory=session_factory,
            encryption_key=settings.credential_encryption_key,
        ),
        role_reader=build_principal_role_reader(
            session_factory=session_factory
        ),
        identity_hmac_key=settings.identity_hmac_key,
        credential_ttl_seconds=settings.oa_credential_ttl_seconds,
    )
    llm_provider = OpenAICompatibleLLMProvider(
        base_url=settings.llm_base_url,
        timeout_seconds=settings.llm_timeout_seconds,
        max_tokens=settings.llm_max_tokens,
        temperature=settings.llm_temperature,
        top_p=settings.llm_top_p,
        top_k=settings.llm_top_k,
        enable_thinking=settings.llm_enable_thinking,
        opener=cast(OpenerDirector, _ReplayLLMOpener()),
    )
    return llm_provider, authentication


def _replay_rsa_public_key() -> str:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(public_der).decode("ascii")


def _selected_probes(arguments: Sequence[str]) -> tuple[tuple[str, str], ...]:
    if not arguments:
        return _DEFAULT_PROBES
    if len(arguments) != 2 or arguments[0] != "--capability-id":
        raise _FullChainCheckError("probe_argv_invalid")
    capability_id = arguments[1]
    selected = tuple(
        probe for probe in _DEFAULT_PROBES if probe[1] == capability_id
    )
    if len(selected) != 1:
        raise _FullChainCheckError("probe_argv_invalid")
    return selected


def _read_login_credential() -> LoginCredential:
    raw = bytearray(sys.stdin.buffer.read(_MAX_CREDENTIAL_INPUT_BYTES + 1))
    payload: dict[str, Any] = {}
    try:
        if len(raw) > _MAX_CREDENTIAL_INPUT_BYTES:
            raise ValueError
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise TypeError
        payload = parsed
        credential = LoginCredential.model_validate(payload)
        if (
            not credential.loginid.get_secret_value()
            or not credential.userpassword.get_secret_value()
        ):
            raise ValueError
        return credential
    except Exception:
        raise _FullChainCheckError("authentication_failed") from None
    finally:
        for index in range(len(raw)):
            raw[index] = 0
        payload.clear()


def _write_failure(error_code: FullChainFailureCode) -> None:
    failure = FullChainFailure(
        error_code=error_code,
        schema_version=FULL_CHAIN_SCHEMA_VERSION,
    )
    sys.stdout.write(failure.model_dump_json())


def main(argv: Sequence[str] | None = None) -> int:
    credential: LoginCredential | None = None
    try:
        probes = _selected_probes(
            tuple(sys.argv[1:]) if argv is None else tuple(argv)
        )
        credential = _read_login_credential()
        try:
            settings = ProductionSettings.from_environment()
        except Exception:
            raise _FullChainCheckError("composition_build_failed") from None
        llm_provider: LLMProviderPort | None = None
        authentication: AuthenticationPort | None = None
        if settings.oa_read_adapter_mode == "replay":
            try:
                llm_provider, authentication = _build_replay_dependencies(settings)
            except Exception:
                raise _FullChainCheckError("composition_build_failed") from None
        with asyncio.Runner(loop_factory=make_event_loop) as runner:
            outcome = runner.run(
                run_full_chain_check(
                    settings,
                    credential,
                    probes=probes,
                    llm_provider=llm_provider,
                    authentication=authentication,
                )
            )
        expected_capability_ids = tuple(
            capability_id for _, capability_id in probes
        )
        failure_code = outcome.failure_code(
            expected_capability_ids=expected_capability_ids
        )
        if failure_code is not None:
            _write_failure(failure_code)
            return 1
        sys.stdout.write(outcome.model_dump_json())
        return 0
    except _FullChainCheckError as error:
        _write_failure(error.code)
        return 1
    except Exception:
        _write_failure("unknown_error")
        return 1
    finally:
        credential = None
        probes = ()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("run_full_chain_check",)
