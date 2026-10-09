"""Run API exposes only owner-bound previews, decisions and sealed artifacts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.api.v1.browser_runs import make_router
from app.browser_skill.run_contracts import BrowserCancelView, BrowserProgressView, BrowserRunView
from app.ports.auth import Principal, PrincipalOrgContext, SessionBindingError
from app.ports.browser_chat import BrowserChatError, BrowserRunResponse, BrowserWritePreview

OWNER = Principal(
    ai_user_id="synthetic_user", display_name="Synthetic", roles=(),
    org_ctx=PrincipalOrgContext(tenant_id="synthetic_tenant"),
)
SESSION = "sid_v1.synthetic"


def _run(*, preview: bool) -> BrowserRunResponse:
    return BrowserRunResponse(
        run=BrowserRunView(
            task_id="task_1", run_id="run_1", state_revision=1, status="running",
            progress=BrowserProgressView(phase="queued"),
            cancel=BrowserCancelView(requested=False, acknowledged=False),
            result=None,
        ),
        write_preview=(BrowserWritePreview(
            request_id="request_1", action_digest="a" * 64,
            request_digest="b" * 64, skill_id="skill_1", skill_version="v1",
            publication_digest="c" * 64, site_id="site_1", object_type="record",
            business_key_digest="d" * 64, parameter_set_digest="e" * 64,
            binding_revision=1, write_steps_digest="f" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        ) if preview else None),
    )


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.artifact_error: BrowserChatError | None = None

    async def get(self, principal, bound_session, task_id, run_id):
        self.calls.append(("get", principal.ai_user_id, bound_session, task_id, run_id))
        return _run(preview=True)

    async def cancel(self, principal, bound_session, task_id, run_id):
        self.calls.append(("cancel", principal.ai_user_id, bound_session, task_id, run_id))
        return _run(preview=False)

    async def decide_write(self, principal, bound_session, task_id, run_id, decision):
        self.calls.append(("decision", principal.ai_user_id, bound_session, decision))
        return _run(preview=False)

    async def read_artifact(self, principal, bound_session, task_id, run_id, reference):
        self.calls.append(("artifact", principal.ai_user_id, bound_session, reference))
        if self.artifact_error is not None:
            raise self.artifact_error
        return "application/pdf", b"%PDF-1.7\nsynthetic artifact\nstartxref\n%%EOF"


def _client(service: _Service) -> TestClient:
    async def require_principal(request: Request) -> Principal:
        if request.method == "POST" and request.headers.get("x-test-csrf") != "ok":
            raise HTTPException(403, {"code": "csrf_rejected"})
        return OWNER

    def bind(principal: Principal, requested: str) -> str:
        if principal != OWNER or requested != SESSION:
            raise SessionBindingError("synthetic wrong session")
        return requested

    app = FastAPI()
    app.include_router(make_router(service, require_principal, bind), prefix="/api/v1")
    return TestClient(app)


def test_get_exposes_only_digest_preview_for_bound_run() -> None:
    service = _Service()
    with _client(service) as client:
        response = client.get(f"/api/v1/browser-runs/task_1/run_1?session_id={SESSION}")
    assert response.status_code == 200
    assert service.calls == [("get", "synthetic_user", SESSION, "task_1", "run_1")]
    preview = response.json()["write_preview"]
    assert preview["business_key_digest"] == "d" * 64
    assert "business_key" not in preview and "arguments" not in preview
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


def test_decision_reuses_post_csrf_and_session_binding_without_echoing_input() -> None:
    service = _Service()
    url = "/api/v1/browser-runs/task_1/run_1/decision"
    with _client(service) as client:
        denied = client.post(url, json={"session_id": SESSION, "decision": "confirmed"})
        accepted = client.post(
            url, headers={"x-test-csrf": "ok"},
            json={"session_id": SESSION, "decision": "rejected"},
        )
        marker = "SYNTHETIC_PRIVATE_CANARY"
        malformed = client.post(
            url, headers={"x-test-csrf": "ok"},
            json={"session_id": SESSION, "decision": marker},
        )
        wrong_session = client.post(
            url, headers={"x-test-csrf": "ok"},
            json={"session_id": "other", "decision": "confirmed"},
        )
    assert denied.status_code == 403
    assert accepted.status_code == 200
    assert service.calls == [("decision", "synthetic_user", SESSION, "rejected")]
    assert malformed.status_code == 422 and marker not in malformed.text
    assert wrong_session.status_code == 404
    assert all(item.headers["cache-control"] == "no-store"
               for item in (denied, accepted, malformed, wrong_session))


def test_artifact_is_attachment_and_missing_owner_ref_is_404() -> None:
    service = _Service()
    url = f"/api/v1/browser-runs/task_1/run_1/artifacts/opaque_ref?session_id={SESSION}"
    with _client(service) as client:
        found = client.get(url)
        service.artifact_error = BrowserChatError("browser_file_unavailable", http_status=404)
        missing = client.get(url)
    assert found.status_code == 200
    assert found.headers["content-type"] == "application/pdf"
    assert found.headers["content-disposition"] == 'attachment; filename="browser-artifact"'
    assert found.headers["x-content-type-options"] == "nosniff"
    assert found.headers["cache-control"] == "no-store"
    assert found.content.startswith(b"%PDF-")
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"
    assert service.calls == [
        ("artifact", "synthetic_user", SESSION, "opaque_ref"),
        ("artifact", "synthetic_user", SESSION, "opaque_ref"),
    ]
