from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from app.infra.browser.browserless_provider import BrowserlessProvider, SubjectEvidence
from app.infra.browser.browserless_wire import WireResponse
from app.infra.browser.deployment_manifest import (
    BrowserDeployment,
    CapabilityEvidence,
    SyntheticSource,
)
from app.infra.browser.playwright_actions import LiveBrowser
from app.infra.browser.profile_codec import DIAGNOSTICS
from app.infra.browser.resource_lifecycle import TerminationEvidence
from tests.browser_skill.factories import DIGEST, binding

ORIGIN = "https://mock-oa.example.com"


def source():
    return SyntheticSource(source_id="mock_oa", fixture_digest=DIGEST, origins=(ORIGIN,))


def deployment(transport="cdp", evidence=True):
    return BrowserDeployment(
        deployment_id="cloud_fixture",
        manifest_digest=DIGEST,
        endpoint_origin="https://production-sfo.browserless.io",
        transport=transport,
        registered_sources=(source(),),
        evidence=CapabilityEvidence(
            manifest_digest=DIGEST,
            evidence_digest="b" * 64,
            transport=transport,
            cookies=True,
            local_storage=True,
            indexed_db=True,
            immutable_capture=True,
            confirmed_termination=True,
        )
        if evidence
        else None,
    )


def state():
    return {
        "cookies": [],
        "origins": [
            {
                "origin": ORIGIN,
                "localStorage": [],
                "indexedDB": [
                    {
                        "name": "synthetic",
                        "version": 1,
                        "stores": [
                            {
                                "name": "objects",
                                "keyPath": None,
                                "autoIncrement": False,
                                "records": [{"key": 1, "value": {"label": "fixture"}}],
                                "indexes": [],
                            }
                        ],
                    }
                ],
            }
        ],
    }


class Authority:
    def __init__(self):
        self.binding = binding()
        self.cleanup_binding = self.binding
        self.cleanup_calls = []
        self.registration = source()
        self.subject_digest = DIGEST
        self.calls = 0

    async def current(self, expected):
        self.calls += 1
        return self.binding

    async def source(self, expected):
        return self.registration

    async def cleanup(self, original):
        self.cleanup_calls.append(original)
        return self.cleanup_binding

    async def subject(self, session, live):
        return SubjectEvidence(
            binding=self.binding,
            subject_digest=self.subject_digest,
            evidence_digest="b" * 64,
        )


class Context:
    def __init__(self):
        self.raw = state()
        self.pages = []

    async def storage_state(self, *, indexed_db):
        assert indexed_db is True
        return self.raw


class Closable:
    def __init__(self):
        self.closed = 0
        self.failure = None

    async def close(self):
        self.closed += 1
        if self.failure:
            raise self.failure

    async def stop(self):
        await self.close()


class Connector:
    def __init__(self):
        self.calls = []
        self.live = None
        self.failure = None

    def preflight(self, config):
        return None

    async def connect(self, endpoint, session, config, registration):
        self.calls.append((endpoint, config.transport))
        if self.failure:
            raise self.failure
        self.live = LiveBrowser(session, Closable(), Context(), Closable())
        return self.live


class HTTP:
    def __init__(self, credential):
        self.credential = credential
        self.calls = []
        self.status = 200
        self.corrupt = None
        self.failure = None
        self.names = set()

    async def request(self, method, url, body, timeout, limit):
        self.calls.append((method, url, body))
        if self.failure:
            raise self.failure
        payload = json.loads(body) if body else {}
        base = "https://production-sfo.browserless.io"
        if method == "DELETE":
            return WireResponse(self.status, b"")
        if "/profile/upload?" in url:
            name = payload["name"]
            if name in self.names:
                return WireResponse(400, b"{}")
            self.names.add(name)
            result = {
                "id": uuid4().hex,
                "name": name,
                "cookieCount": len(payload["state"]["cookies"]),
                "originCount": len(payload["state"]["origins"]),
                "lastUsedAt": None,
                "createdAt": "2026-10-02T00:00:00Z",
                "updatedAt": "2026-10-02T00:00:00Z",
                "diagnostics": dict.fromkeys(DIAGNOSTICS, 0),
            }
        else:
            identity = uuid4().hex
            result = {
                "id": identity,
                "connect": f"wss://production-sfo.browserless.io/session/connect/{identity}"
                f"?token={self.credential}",
                "stop": f"{base}/session/{identity}?token={self.credential}",
                "browserQL": f"{base}/session/bql/{identity}?token={self.credential}",
                "ttl": payload["ttl"],
                "cloudEndpointId": "fixture",
            }
        if self.corrupt:
            self.corrupt(result)
        return WireResponse(self.status, json.dumps(result).encode())


async def prove(request):
    return TerminationEvidence(
        session_ref=request.session.session_ref,
        manifest_digest=request.manifest_digest,
        resource_digest=request.resource_digest,
        challenge=request.challenge,
        evidence_digest="c" * 64,
        remote_terminated=True,
    )


def fixture(transport="cdp", evidence=True, prover=prove, capacity=2):
    credential = uuid4().hex  # Runtime-only synthetic secret, never fixture expected or logging.
    authority, connector, http = Authority(), Connector(), HTTP(credential)
    provider = BrowserlessProvider(
        enabled=True,
        deployment=deployment(transport, evidence),
        authority=authority,
        credential=lambda: credential,
        connector=connector,
        http=http,
        termination_prover=prover,
        capacity=capacity,
    )
    return SimpleNamespace(provider=provider, authority=authority, connector=connector, http=http)
