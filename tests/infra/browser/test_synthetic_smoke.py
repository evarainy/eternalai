"""Offline smoke gates and real codec; credential sentinels exist only at runtime."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx2
import pytest
from pydantic import SecretStr

from app.browser_skill.models import DecisionSource, ModelManifest
from app.infra.browser.decision_adapters import TypeSafeCodec
from app.infra.browser.synthetic_smoke import (
    CASE_ID,
    MAX_CALLS,
    MAX_INPUT_TOKENS,
    ORIGIN,
    PROTOCOL,
    SmokeQuota,
    SmokeRegistration,
    SyntheticSmoke,
    TokenEvidence,
    fixture_digest,
    known_request,
    protected_client,
)
from app.infra.browser.systemone_http import DecisionDeployment
from scripts.browser_v42_synthetic_smoke import main


class FrozenTestCounter:
    """Test-only operator injection, not a claimed production tokenizer."""

    def __init__(self, evidence: TokenEvidence) -> None:
        self.evidence = evidence

    def count(self, body: bytes, manifest: ModelManifest) -> TokenEvidence:
        assert hashlib.sha256(body).hexdigest() == self.evidence.wire_digest
        assert manifest.manifest_digest == self.evidence.manifest_digest
        return self.evidence


def prepared(tokens: int = 30) -> SyntheticSmoke:
    manifest = ModelManifest(
        request_model="synthetic_alias",
        deployment_model="synthetic_deployment_v1",
        manifest_digest=hashlib.sha256(b"offline_manifest").hexdigest(),
    )
    source = DecisionSource(
        source_id=CASE_ID,
        origin="https://example.com",
        fixture_digest=fixture_digest(),
    )
    registration = SmokeRegistration(
        deployment=DecisionDeployment(
            disposition="cloud_synthetic",
            endpoint_origin=ORIGIN,
            manifest_digest=manifest.manifest_digest,
            registered_sources=(source,),
        ),
        manifest=manifest,
        source=source,
        protocol=PROTOCOL,
        case_id=CASE_ID,
        wire_digest="",
        token_evidence_digest="",
    )
    runner = SyntheticSmoke(registration=registration, quota=SmokeQuota())
    body = TypeSafeCodec().encode(known_request(), runner._context(known_request()))
    evidence = TokenEvidence(
        protocol=PROTOCOL,
        request_model=manifest.request_model,
        deployment_model=manifest.deployment_model,
        manifest_digest=manifest.manifest_digest,
        wire_digest=hashlib.sha256(body).hexdigest(),
        counter_revision="offline_frozen_count_v1",
        input_tokens=tokens,
    )
    runner.registration = replace(
        registration,
        wire_digest=evidence.wire_digest,
        token_evidence_digest=evidence.digest(),
    )
    runner.counter = FrozenTestCounter(evidence)
    return runner


def forbidden(*args: object, **kwargs: object) -> None:
    pytest.fail("local gate invoked a forbidden dependency")


@pytest.mark.parametrize("enabled,status", [(False, "DISABLED"), (True, "WAITING_ENV")])
def test_missing_environment_never_requests_secret_or_client(enabled, status):
    async def exercise():
        runner = SyntheticSmoke(client_factory=forbidden)
        result = await runner.run(enabled=enabled, secret_input=forbidden)
        assert result.status == status
        assert runner.quota.snapshot() == (0, 0)

    asyncio.run(exercise())


@pytest.mark.parametrize("argv,status", [([], "DISABLED"), (["--enable"], "WAITING_ENV")])
def test_cli_missing_evidence_does_not_prompt(argv, status, monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", forbidden)
    assert main(argv) == 2
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_cli_trusted_injection_runs_codec_and_secret_supplier(capsys):
    runner = prepared()
    protected = SecretStr(uuid.uuid4().hex)
    prompts = 0
    calls = 0

    def supplier():
        nonlocal prompts
        prompts += 1
        return protected

    def handler(request):
        nonlocal calls
        calls += 1
        authorized = hmac.compare_digest(
            request.headers["authorization"],
            "Bearer " + protected.get_secret_value(),
        )
        assert authorized
        assert request.url == httpx2.URL(ORIGIN + "/v1/systemone")
        assert json.loads(request.content)["model"] == "synthetic_alias"
        return httpx2.Response(
            200,
            json={
                "model": "synthetic_deployment_v1",
                "answers": {
                    "select_target": {
                        "type": "choice",
                        "choice": "open_button",
                        "probabilities": {"open_button": 1.0, "close_button": 0.0},
                        "confidence": 1.0,
                    }
                },
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    def factory(key):
        return httpx2.AsyncClient(
            base_url=ORIGIN,
            transport=httpx2.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
            headers={"Authorization": "Bearer " + key.get_secret_value()},
        )

    runner.client_factory = factory
    assert main(["--enable"], smoke=runner, secret_supplier=supplier) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "PASS", "code": "neutral_choice_verified"}
    leaked = protected.get_secret_value() in captured.out + captured.err
    assert not leaked
    assert (prompts, calls) == (1, 1)
    assert runner.quota.snapshot() == (1, 30)


@pytest.mark.parametrize(
    "fault",
    ["origin", "model", "manifest", "protocol", "source", "registry", "case", "counter"],
)
def test_frozen_registration_rejects_changes_before_secret(fault):
    async def exercise():
        runner = prepared()
        registration = runner.registration
        assert registration is not None
        if fault == "origin":
            registration = replace(
                registration,
                deployment=registration.deployment.model_copy(
                    update={"endpoint_origin": "https://other.invalid"},
                ),
            )
        elif fault == "model":
            registration = replace(
                registration,
                manifest=registration.manifest.model_copy(
                    update={"request_model": "different_model"},
                ),
            )
        elif fault == "manifest":
            registration = replace(
                registration,
                manifest=registration.manifest.model_copy(
                    update={"manifest_digest": "0" * 64},
                ),
            )
        elif fault == "protocol":
            registration = replace(registration, protocol="other_protocol")
        elif fault == "source":
            registration = replace(
                registration,
                source=registration.source.model_copy(
                    update={"fixture_digest": "0" * 64},
                ),
            )
        elif fault == "registry":
            registration = replace(
                registration,
                deployment=registration.deployment.model_copy(
                    update={"registered_sources": ()},
                ),
            )
        elif fault == "case":
            registration = replace(registration, case_id="unregistered_case")
        else:
            registration = replace(registration, token_evidence_digest="0" * 64)
        runner.registration = registration
        runner.client_factory = forbidden
        result = await runner.run(enabled=True, secret_input=forbidden)
        assert result.status == "REJECTED"
        assert runner.quota.snapshot() == (0, 0)

    asyncio.run(exercise())


def test_missing_counter_and_oversized_input_fail_closed():
    async def exercise():
        runner = prepared()
        runner.counter = None
        result = await runner.run(enabled=True, secret_input=forbidden)
        assert result.status == "WAITING_ENV"
        runner = prepared(MAX_INPUT_TOKENS + 1)
        result = await runner.run(enabled=True, secret_input=forbidden)
        assert result.code == "hard_budget"
        assert runner.quota.snapshot() == (0, 0)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "change",
    [
        {"protocol": "other_protocol"},
        {"request_model": "other_alias"},
        {"deployment_model": "other_deployment"},
        {"manifest_digest": "0" * 64},
        {"wire_digest": "0" * 64},
        {"counter_revision": ""},
        {"input_tokens": True},
        {"input_tokens": 0},
    ],
)
def test_counter_evidence_is_bound_to_exact_input_and_models(change):
    async def exercise():
        runner = prepared()
        original = runner.counter.evidence
        invalid = replace(original, **change)

        class InvalidCounter:
            def count(self, body, manifest):
                return invalid

        runner.counter = InvalidCounter()
        runner.registration = replace(runner.registration, token_evidence_digest=invalid.digest())
        result = await runner.run(enabled=True, secret_input=forbidden)
        assert result.code == "counter_evidence_mismatch"
        assert runner.quota.snapshot() == (0, 0)

    asyncio.run(exercise())


def test_concurrent_reservations_enforce_call_and_token_ceiling():
    quota = SmokeQuota()
    with ThreadPoolExecutor(max_workers=12) as executor:
        accepted = list(executor.map(quota.reserve, [MAX_INPUT_TOKENS] * 40))
    assert sum(accepted) == MAX_CALLS
    assert quota.snapshot() == (MAX_CALLS, MAX_CALLS * MAX_INPUT_TOKENS)
    assert not quota.reserve(1)
    assert not SmokeQuota().reserve(True)
    assert not SmokeQuota().reserve(0)


@pytest.mark.parametrize("mode", ["success", "mismatch", "timeout", "transport", "cancel"])
def test_real_codec_single_attempt_and_permanent_budget(mode, caplog, capsys):
    async def exercise():
        runner = prepared()
        protected = SecretStr(uuid.uuid4().hex)
        calls = 0
        entered = asyncio.Event()

        async def handler(request):
            nonlocal calls
            calls += 1
            assert request.url == httpx2.URL(ORIGIN + "/v1/systemone")
            assert request.method == "POST"
            authorized = hmac.compare_digest(
                request.headers["authorization"],
                "Bearer " + protected.get_secret_value(),
            )
            assert authorized
            payload = json.loads(request.content)
            assert payload["model"] == "synthetic_alias"
            assert set(payload["questions"]) == {"select_target"}
            assert hashlib.sha256(request.content).hexdigest() == runner.registration.wire_digest
            entered.set()
            if mode == "timeout":
                raise httpx2.ReadTimeout(protected.get_secret_value())
            if mode == "transport":
                raise httpx2.ConnectError(protected.get_secret_value())
            if mode == "cancel":
                await asyncio.Event().wait()
            return httpx2.Response(
                200,
                json={
                    "model": "different_deployment"
                    if mode == "mismatch"
                    else "synthetic_deployment_v1",
                    "answers": {
                        "select_target": {
                            "type": "choice",
                            "choice": "open_button",
                            "probabilities": {"open_button": 0.95, "close_button": 0.05},
                            "confidence": 0.95,
                        }
                    },
                    "usage": {"input_tokens": 999999, "output_tokens": 1},
                },
            )

        def factory(key):
            correct_key = hmac.compare_digest(key.get_secret_value(), protected.get_secret_value())
            assert correct_key
            return httpx2.AsyncClient(
                base_url=ORIGIN,
                transport=httpx2.MockTransport(handler),
                trust_env=False,
                follow_redirects=False,
                headers={"Authorization": "Bearer " + key.get_secret_value()},
            )

        runner.client_factory = factory
        task = asyncio.create_task(runner.run(enabled=True, secret_input=lambda: protected))
        if mode == "cancel":
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            expected = {
                "success": "neutral_choice_verified",
                "mismatch": "model_mismatch",
                "timeout": "timeout",
                "transport": "unavailable",
            }
            assert result.code == expected[mode]
            assert (result.status == "PASS") == (mode == "success")
            result_leaked = protected.get_secret_value() in repr(result)
            assert not result_leaked
        assert calls == 1
        assert runner.quota.snapshot() == (1, 30)
        captured = capsys.readouterr()
        output_leaked = protected.get_secret_value() in caplog.text + captured.out + captured.err
        assert not output_leaked

    asyncio.run(exercise())


def test_real_client_assembly_disables_ambient_proxy_redirect_and_retry(monkeypatch):
    captured = {}
    transport = object()
    client = object()
    protected = SecretStr(uuid.uuid4().hex)

    def transport_factory(**kwargs):
        captured["transport"] = kwargs
        return transport

    def client_factory(**kwargs):
        authorized = hmac.compare_digest(
            kwargs.pop("headers")["Authorization"],
            "Bearer " + protected.get_secret_value(),
        )
        assert authorized
        captured["client"] = kwargs
        return client

    monkeypatch.setattr(httpx2, "AsyncHTTPTransport", transport_factory)
    monkeypatch.setattr(httpx2, "AsyncClient", client_factory)
    assert protected_client(protected) is client
    assert captured["transport"] == {"retries": 0, "trust_env": False}
    options = captured["client"]
    assert options["base_url"] == ORIGIN
    assert options["trust_env"] is False
    assert options["follow_redirects"] is False
    assert options["transport"] is transport
    assert options["timeout"].connect == 20.0
    assert options["timeout"].read == 20.0


def test_failed_secret_input_consumes_reservation_without_factory():
    async def exercise():
        runner = prepared()
        runner.client_factory = forbidden

        def failed_input():
            raise EOFError()

        for _ in range(MAX_CALLS):
            result = await runner.run(enabled=True, secret_input=failed_input)
            assert result.code == "local_or_transport_failure"
        result = await runner.run(enabled=True, secret_input=forbidden)
        assert result.code == "hard_budget"
        assert runner.quota.snapshot() == (MAX_CALLS, MAX_CALLS * 30)

    asyncio.run(exercise())
