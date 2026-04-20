# Phase 1B Minimal HTTP Contract Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a testable in-memory-only HTTP loop for `POST /v1/sessions`, `GET /v1/sessions/{session_id}`, `POST /v1/sessions/{session_id}/messages`, and `GET /v1/tasks/{task_id}` while keeping `GET /health` unchanged.

**Architecture:** Add a single-process in-memory state container under `services/api/app/services/` and access it through a thin service layer so route handlers stay small. Mount focused session, message, and task routers under the existing `/v1` group, normalize contract-required `400` and `404` payloads, and update the YAML contracts plus docs to describe this as a Phase 1B ephemeral implementation rather than durable storage.

**Tech Stack:** FastAPI, Pydantic v2, FastAPI `TestClient`, process-local in-memory state, YAML contract/docs updates.

---

## Scope Guardrails

- Only touch `services/api`, `contracts/http/api.yaml`, and the docs/spec files needed to describe this Phase 1B in-memory slice truthfully.
- Do not add database models, migrations, Redis wiring, worker consumers, Langfuse integration, ASR execution, frontend work, or Docker-first assumptions.
- Keep `/health` behavior and contract compatibility unchanged.
- Keep the new task loop intentionally minimal: accepted turns and tasks may remain queued because worker execution is out of scope for this phase.

## Recommended Approach

1. **Recommended: module-level in-memory store plus reset helper**
   Smallest change set, easiest to test, and enough for one-process contract verification.
2. **Alternative: `app.state`-attached store**
   Cleaner dependency ownership, but it forces more app-factory plumbing and test setup churn for little Phase 1B value.
3. **Alternative: repository protocol plus fake implementation**
   Best long-term seam for DV-004, but over-designed for this phase because persistence is explicitly forbidden.

Use option 1 for Phase 1B, but keep the service API shaped so DV-004 can replace the store later.

## Planned File Map

**Create:**
- `services/api/app/errors.py`
- `services/api/app/routes/sessions.py`
- `services/api/app/routes/messages.py`
- `services/api/app/routes/tasks.py`
- `services/api/app/services/__init__.py`
- `services/api/app/services/in_memory_state.py`
- `services/api/app/services/session_service.py`
- `services/api/tests/conftest.py`
- `services/api/tests/test_sessions_api.py`
- `services/api/tests/test_message_submission.py`
- `services/api/tests/test_task_status_api.py`

**Modify:**
- `services/api/app/main.py`
- `services/api/app/routes/__init__.py`
- `services/api/app/routes/v1.py`
- `services/api/app/schemas/session.py`
- `services/api/app/schemas/task.py`
- `services/api/tests/test_contract_registry.py`
- `contracts/http/api.yaml`
- `contracts/http/README.md`
- `spec/resource_spec.yaml`
- `spec/invariant_spec.yaml`
- `docs/architecture/README.md`
- `services/api/README.md`

**Leave untouched:**
- `services/worker/**`
- `services/asr/**`
- `migrations/**`
- `apps/**`
- `docker-compose.yml`

### Task 1: Add Red Tests For The Four-Endpoint Loop

**Files:**
- Create: `services/api/tests/conftest.py`
- Create: `services/api/tests/test_sessions_api.py`
- Create: `services/api/tests/test_message_submission.py`
- Create: `services/api/tests/test_task_status_api.py`
- Modify: `services/api/tests/test_contract_registry.py`
- Test: `services/api/tests/test_health.py`, `services/api/tests/test_contract_registry.py`, `services/api/tests/test_sessions_api.py`, `services/api/tests/test_message_submission.py`, `services/api/tests/test_task_status_api.py`

- [ ] **Step 1: Add a resettable API test fixture**

```python
import pytest
from fastapi.testclient import TestClient

from services.api.app.main import app
from services.api.app.services.session_service import reset_in_memory_state


@pytest.fixture()
def client() -> TestClient:
    reset_in_memory_state()
    with TestClient(app) as test_client:
        yield test_client
    reset_in_memory_state()
```

- [ ] **Step 2: Write the failing session endpoint tests**

```python
def test_create_session_returns_201_and_payload(client: TestClient) -> None:
    response = client.post("/v1/sessions", json={"channel": "web", "title": "Phase 1B"})

    assert response.status_code == 201
    body = response.json()
    assert body["session_id"].startswith("sess_")
    assert body["channel"] == "web"
    assert body["status"] == "active"
    assert body["title"] == "Phase 1B"


def test_get_session_returns_created_session_and_turns(client: TestClient) -> None:
    created = client.post("/v1/sessions", json={"channel": "web"}).json()

    response = client.get(f"/v1/sessions/{created['session_id']}")

    assert response.status_code == 200
    assert response.json()["session_id"] == created["session_id"]
    assert response.json()["turns"] == []


def test_get_session_returns_contract_not_found_payload(client: TestClient) -> None:
    response = client.get("/v1/sessions/sess_missing")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Session not found",
            "resource": "session",
            "resource_id": "sess_missing",
        }
    }
```

- [ ] **Step 3: Write the failing message and task tests**

```python
def test_submit_message_returns_202_and_creates_pollable_task(client: TestClient) -> None:
    created = client.post("/v1/sessions", json={"channel": "web"}).json()

    message_response = client.post(
        f"/v1/sessions/{created['session_id']}/messages",
        json={"input_type": "text", "text": "What changed?"},
    )

    assert message_response.status_code == 202
    message_body = message_response.json()
    assert message_body["task_id"].startswith("task_")
    assert message_body["accepted_turn_id"].startswith("turn_")
    assert message_body["status"] == "queued"

    task_response = client.get(f"/v1/tasks/{message_body['task_id']}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "queued"
    assert task_response.json()["task_type"] == "answer"


def test_submit_message_updates_session_turn_projection(client: TestClient) -> None:
    created = client.post("/v1/sessions", json={"channel": "web"}).json()
    client.post(
        f"/v1/sessions/{created['session_id']}/messages",
        json={"input_type": "text", "text": "hello"},
    )

    session_response = client.get(f"/v1/sessions/{created['session_id']}")

    assert session_response.status_code == 200
    turn = session_response.json()["turns"][0]
    assert turn["user_input_type"] == "text"
    assert turn["user_input_text"] == "hello"
    assert turn["status"] == "accepted"


def test_submit_message_returns_400_for_invalid_payload_shape(client: TestClient) -> None:
    created = client.post("/v1/sessions", json={"channel": "web"}).json()

    response = client.post(
        f"/v1/sessions/{created['session_id']}/messages",
        json={"input_type": "audio", "text": "not allowed"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_get_task_returns_contract_not_found_payload(client: TestClient) -> None:
    response = client.get("/v1/tasks/task_missing")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Task not found",
            "resource": "task",
            "resource_id": "task_missing",
        }
    }
```

- [ ] **Step 4: Extend the contract registry test so it expects all implemented public routes**

```python
assert runtime_routes == {
    ContractRoute(operation_id="getApiHealth", method="GET", path="/health"),
    ContractRoute(operation_id="createSession", method="POST", path="/v1/sessions"),
    ContractRoute(operation_id="getSession", method="GET", path="/v1/sessions/{session_id}"),
    ContractRoute(operation_id="submitMessage", method="POST", path="/v1/sessions/{session_id}/messages"),
    ContractRoute(operation_id="getTask", method="GET", path="/v1/tasks/{task_id}"),
}
```

- [ ] **Step 5: Run the new tests to verify they fail for missing routes and missing registry entries**

Run: `.venv\Scripts\python.exe -m pytest services/api/tests/test_contract_registry.py services/api/tests/test_sessions_api.py services/api/tests/test_message_submission.py services/api/tests/test_task_status_api.py -v`

Expected: FAIL with `404 Not Found`, registry mismatch, or missing helper/import errors because the business route loop is not wired yet.

- [ ] **Step 6: Commit**

```bash
git add services/api/tests/conftest.py services/api/tests/test_contract_registry.py services/api/tests/test_sessions_api.py services/api/tests/test_message_submission.py services/api/tests/test_task_status_api.py
git commit -m "test: add phase1b http loop coverage"
```

- [ ] **Step 7: Emit thick receipt**

```text
THICK RECEIPT
Task: Phase 1B / Red tests
Scope: Added failing HTTP tests and expanded contract-registry expectations for the 4 target endpoints.
Files: services/api/tests/conftest.py, services/api/tests/test_contract_registry.py, services/api/tests/test_sessions_api.py, services/api/tests/test_message_submission.py, services/api/tests/test_task_status_api.py
Tests: .venv\Scripts\python.exe -m pytest services/api/tests/test_contract_registry.py services/api/tests/test_sessions_api.py services/api/tests/test_message_submission.py services/api/tests/test_task_status_api.py -v
Result: Red, expected; implementation paths are now pinned down.
Deferred: Route wiring, in-memory state, docs sync.
```

### Task 2: Build Resettable In-Memory State And Contract Error Handlers

**Files:**
- Create: `services/api/app/errors.py`
- Create: `services/api/app/services/__init__.py`
- Create: `services/api/app/services/in_memory_state.py`
- Create: `services/api/app/services/session_service.py`
- Modify: `services/api/app/main.py`
- Modify: `services/api/app/schemas/session.py`
- Modify: `services/api/app/schemas/task.py`
- Test: `services/api/tests/test_sessions_api.py`, `services/api/tests/test_message_submission.py`, `services/api/tests/test_task_status_api.py`

- [ ] **Step 1: Implement the in-memory state container and reset hook**

```python
from dataclasses import dataclass, field

from services.api.app.schemas.session import GetSessionResponse
from services.api.app.schemas.task import GetTaskResponse


@dataclass
class InMemoryAPIState:
    sessions: dict[str, GetSessionResponse] = field(default_factory=dict)
    tasks: dict[str, GetTaskResponse] = field(default_factory=dict)

    def reset(self) -> None:
        self.sessions.clear()
        self.tasks.clear()


STATE = InMemoryAPIState()
```

- [ ] **Step 2: Implement a thin session service over the in-memory state**

```python
from datetime import UTC, datetime
from uuid import uuid4

from services.api.app.errors import ResourceNotFoundError
from services.api.app.schemas.message import SubmitMessageRequest, SubmitMessageResponse
from services.api.app.schemas.session import ConversationTurn, CreateSessionRequest, CreateSessionResponse, GetSessionResponse
from services.api.app.schemas.task import GetTaskResponse
from services.api.app.services.in_memory_state import STATE


def _prefixed_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _now() -> datetime:
    return datetime.now(UTC)


class SessionService:
    def create_session(self, payload: CreateSessionRequest) -> CreateSessionResponse:
        now = _now()
        session = GetSessionResponse(
            session_id=_prefixed_id("sess"),
            channel=payload.channel,
            status="active",
            created_at=now,
            updated_at=now,
            title=payload.title,
            metadata=payload.metadata,
            turns=[],
        )
        STATE.sessions[session.session_id] = session
        return CreateSessionResponse(**session.model_dump(exclude={"turns"}))

    def get_session(self, session_id: str) -> GetSessionResponse:
        session = STATE.sessions.get(session_id)
        if session is None:
            raise ResourceNotFoundError(resource="session", resource_id=session_id, message="Session not found")
        return session

    def submit_message(self, session_id: str, payload: SubmitMessageRequest) -> SubmitMessageResponse:
        session = self.get_session(session_id)
        now = _now()
        task_type = "answer" if payload.input_type == "text" else "transcription"
        task = GetTaskResponse(
            task_id=_prefixed_id("task"),
            task_type=task_type,
            status="queued",
            created_at=now,
            updated_at=now,
            session_id=session_id,
        )
        turn = ConversationTurn(
            turn_id=_prefixed_id("turn"),
            session_id=session_id,
            user_input_type=payload.input_type,
            user_input_text=payload.text or "[audio pending]",
            task_id=task.task_id,
            status="accepted",
            created_at=now,
            transcript_text=None,
        )
        session.turns.append(turn)
        session.updated_at = now
        STATE.tasks[task.task_id] = task
        return SubmitMessageResponse(task_id=task.task_id, accepted_turn_id=turn.turn_id)

    def get_task(self, task_id: str) -> GetTaskResponse:
        task = STATE.tasks.get(task_id)
        if task is None:
            raise ResourceNotFoundError(resource="task", resource_id=task_id, message="Task not found")
        return task
```

- [ ] **Step 3: Add contract-shaped exception handlers so invalid bodies return `400` instead of FastAPI's default `422`**

```python
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from services.api.app.schemas.common import BadRequestErrorResponse, NotFoundErrorResponse


class ResourceNotFoundError(Exception):
    def __init__(self, *, resource: str, resource_id: str, message: str) -> None:
        self.resource = resource
        self.resource_id = resource_id
        self.message = message


async def handle_request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    payload = BadRequestErrorResponse.model_validate(
        {
            "error": {
                "code": "invalid_request",
                "message": "Invalid request payload",
                "details": {"errors": exc.errors()},
            }
        }
    )
    return JSONResponse(status_code=400, content=payload.model_dump(mode="json"))


async def handle_not_found(_: Request, exc: ResourceNotFoundError) -> JSONResponse:
    payload = NotFoundErrorResponse.model_validate(
        {
            "error": {
                "code": "not_found",
                "message": exc.message,
                "resource": exc.resource,
                "resource_id": exc.resource_id,
            }
        }
    )
    return JSONResponse(status_code=404, content=payload.model_dump(mode="json"))
```

- [ ] **Step 4: Register those handlers in the FastAPI app factory**

```python
def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(ResourceNotFoundError, handle_not_found)

    for registration in get_router_registrations(environment=settings.app_env):
        app.include_router(registration.router)

    return app
```

- [ ] **Step 5: Tighten schema defaults only if the service implementation needs them**

```python
class GetTaskResponse(APIModel):
    task_id: TaskId
    task_type: NonEmptyString
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    session_id: SessionId | None = None
    result_summary: str | None = None
    result: dict[str, Any] | None = None
    error: TaskError | None = None
```

Use this step only for real schema alignment; do not add speculative fields.

- [ ] **Step 6: Run the targeted API tests to confirm error payloads are now normalized but routing still fails**

Run: `.venv\Scripts\python.exe -m pytest services/api/tests/test_sessions_api.py services/api/tests/test_message_submission.py services/api/tests/test_task_status_api.py -v`

Expected: Some tests still FAIL on missing endpoints, but payload-shape and fixture issues are resolved.

- [ ] **Step 7: Commit**

```bash
git add services/api/app/errors.py services/api/app/main.py services/api/app/services/__init__.py services/api/app/services/in_memory_state.py services/api/app/services/session_service.py services/api/app/schemas/session.py services/api/app/schemas/task.py
git commit -m "feat: add in-memory api state and error handlers"
```

- [ ] **Step 8: Emit thick receipt**

```text
THICK RECEIPT
Task: Phase 1B / In-memory state
Scope: Added resettable in-memory session/task state plus contract-shaped 400/404 handlers.
Files: services/api/app/errors.py, services/api/app/main.py, services/api/app/services/__init__.py, services/api/app/services/in_memory_state.py, services/api/app/services/session_service.py, services/api/app/schemas/session.py, services/api/app/schemas/task.py
Tests: .venv\Scripts\python.exe -m pytest services/api/tests/test_sessions_api.py services/api/tests/test_message_submission.py services/api/tests/test_task_status_api.py -v
Result: Shared state and error surface are ready for route wiring.
Deferred: Public route modules and contract status flips.
```

### Task 3: Wire Session Routes And `/v1` Assembly

**Files:**
- Create: `services/api/app/routes/sessions.py`
- Modify: `services/api/app/routes/v1.py`
- Modify: `services/api/app/routes/__init__.py`
- Modify: `contracts/http/api.yaml`
- Modify: `services/api/tests/test_contract_registry.py`
- Test: `services/api/tests/test_sessions_api.py`, `services/api/tests/test_contract_registry.py`, `services/api/tests/test_health.py`

- [ ] **Step 1: Add the focused session router**

```python
from fastapi import APIRouter

from services.api.app.schemas.common import SessionId
from services.api.app.schemas.session import CreateSessionRequest, CreateSessionResponse, GetSessionResponse
from services.api.app.services.session_service import get_session_service


router = APIRouter(tags=["sessions"])


@router.post("/sessions", operation_id="createSession", response_model=CreateSessionResponse, status_code=201)
def create_session(payload: CreateSessionRequest) -> CreateSessionResponse:
    return get_session_service().create_session(payload)


@router.get("/sessions/{session_id}", operation_id="getSession", response_model=GetSessionResponse)
def get_session(session_id: SessionId) -> GetSessionResponse:
    return get_session_service().get_session(session_id)
```

- [ ] **Step 2: Mount the session router inside the existing `/v1` group**

```python
from fastapi import APIRouter

from services.api.app.routes.sessions import router as sessions_router


def build_v1_router() -> APIRouter:
    router = APIRouter(prefix="/v1")
    router.include_router(sessions_router)
    return router
```

- [ ] **Step 3: Extend the contract route registry with the now-implemented session operations**

```python
def get_contract_routes() -> tuple[ContractRoute, ...]:
    return (
        ContractRoute(operation_id="getApiHealth", method="GET", path="/health"),
        ContractRoute(operation_id="createSession", method="POST", path="/v1/sessions"),
        ContractRoute(operation_id="getSession", method="GET", path="/v1/sessions/{session_id}"),
    )
```

- [ ] **Step 4: Flip only the session endpoint statuses in `contracts/http/api.yaml` once the runtime routes exist**

```yaml
  - operation_id: createSession
    implementation_status: implemented
  - operation_id: getSession
    implementation_status: implemented
```

- [ ] **Step 5: Run the session and contract regression set**

Run: `.venv\Scripts\python.exe -m pytest services/api/tests/test_health.py services/api/tests/test_contract_registry.py services/api/tests/test_sessions_api.py -v`

Expected: PASS for `/health`, contract registry, and session creation/detail tests.

- [ ] **Step 6: Commit**

```bash
git add services/api/app/routes/sessions.py services/api/app/routes/v1.py services/api/app/routes/__init__.py services/api/tests/test_contract_registry.py contracts/http/api.yaml
git commit -m "feat: implement phase1b session routes"
```

- [ ] **Step 7: Emit thick receipt**

```text
THICK RECEIPT
Task: Phase 1B / Session routes
Scope: Implemented session create/detail HTTP loop and registered contract-backed runtime routes.
Files: services/api/app/routes/sessions.py, services/api/app/routes/v1.py, services/api/app/routes/__init__.py, services/api/tests/test_contract_registry.py, contracts/http/api.yaml
Tests: .venv\Scripts\python.exe -m pytest services/api/tests/test_health.py services/api/tests/test_contract_registry.py services/api/tests/test_sessions_api.py -v
Result: `POST /v1/sessions` and `GET /v1/sessions/{session_id}` are green.
Deferred: Message submission and task polling routes.
```

### Task 4: Implement Message Submission And Task Polling

**Files:**
- Create: `services/api/app/routes/messages.py`
- Create: `services/api/app/routes/tasks.py`
- Modify: `services/api/app/routes/v1.py`
- Modify: `services/api/app/routes/__init__.py`
- Modify: `contracts/http/api.yaml`
- Modify: `services/api/tests/test_contract_registry.py`
- Test: `services/api/tests/test_message_submission.py`, `services/api/tests/test_task_status_api.py`, `services/api/tests/test_contract_registry.py`

- [ ] **Step 1: Add the message submission router**

```python
from fastapi import APIRouter

from services.api.app.schemas.common import SessionId
from services.api.app.schemas.message import SubmitMessageRequest, SubmitMessageResponse
from services.api.app.services.session_service import get_session_service


router = APIRouter(tags=["messages"])


@router.post(
    "/sessions/{session_id}/messages",
    operation_id="submitMessage",
    response_model=SubmitMessageResponse,
    status_code=202,
)
def submit_message(session_id: SessionId, payload: SubmitMessageRequest) -> SubmitMessageResponse:
    return get_session_service().submit_message(session_id, payload)
```

- [ ] **Step 2: Add the task polling router**

```python
from fastapi import APIRouter

from services.api.app.schemas.common import TaskId
from services.api.app.schemas.task import GetTaskResponse
from services.api.app.services.session_service import get_session_service


router = APIRouter(tags=["tasks"])


@router.get("/tasks/{task_id}", operation_id="getTask", response_model=GetTaskResponse)
def get_task(task_id: TaskId) -> GetTaskResponse:
    return get_session_service().get_task(task_id)
```

- [ ] **Step 3: Mount both routers and extend the contract registry**

```python
from services.api.app.routes.messages import router as messages_router
from services.api.app.routes.sessions import router as sessions_router
from services.api.app.routes.tasks import router as tasks_router


def build_v1_router() -> APIRouter:
    router = APIRouter(prefix="/v1")
    router.include_router(sessions_router)
    router.include_router(messages_router)
    router.include_router(tasks_router)
    return router
```

```python
def get_contract_routes() -> tuple[ContractRoute, ...]:
    return (
        ContractRoute(operation_id="getApiHealth", method="GET", path="/health"),
        ContractRoute(operation_id="createSession", method="POST", path="/v1/sessions"),
        ContractRoute(operation_id="getSession", method="GET", path="/v1/sessions/{session_id}"),
        ContractRoute(operation_id="submitMessage", method="POST", path="/v1/sessions/{session_id}/messages"),
        ContractRoute(operation_id="getTask", method="GET", path="/v1/tasks/{task_id}"),
    )
```

- [ ] **Step 4: Flip the message/task endpoints to `implemented` in the HTTP contract**

```yaml
  - operation_id: submitMessage
    implementation_status: implemented
  - operation_id: getTask
    implementation_status: implemented
```

- [ ] **Step 5: Keep the queued-state loop intentionally minimal**

```python
assert task_response.json()["status"] == "queued"
assert task_response.json()["result"] is None
assert task_response.json()["error"] is None
```

Do not add worker transitions here. The Phase 1B definition of done is a pollable queued task, not background execution.

- [ ] **Step 6: Run the message/task regression set**

Run: `.venv\Scripts\python.exe -m pytest services/api/tests/test_message_submission.py services/api/tests/test_task_status_api.py services/api/tests/test_contract_registry.py -v`

Expected: PASS with `202` on submit, `200` on task polling, and `404` contract payloads for missing session/task IDs.

- [ ] **Step 7: Commit**

```bash
git add services/api/app/routes/messages.py services/api/app/routes/tasks.py services/api/app/routes/v1.py services/api/app/routes/__init__.py services/api/tests/test_message_submission.py services/api/tests/test_task_status_api.py services/api/tests/test_contract_registry.py contracts/http/api.yaml
git commit -m "feat: implement phase1b message and task routes"
```

- [ ] **Step 8: Emit thick receipt**

```text
THICK RECEIPT
Task: Phase 1B / Message and task routes
Scope: Implemented queued message acceptance and task polling without worker execution.
Files: services/api/app/routes/messages.py, services/api/app/routes/tasks.py, services/api/app/routes/v1.py, services/api/app/routes/__init__.py, services/api/tests/test_message_submission.py, services/api/tests/test_task_status_api.py, services/api/tests/test_contract_registry.py, contracts/http/api.yaml
Tests: .venv\Scripts\python.exe -m pytest services/api/tests/test_message_submission.py services/api/tests/test_task_status_api.py services/api/tests/test_contract_registry.py -v
Result: `POST /v1/sessions/{session_id}/messages` and `GET /v1/tasks/{task_id}` are green with contract-shaped responses.
Deferred: Worker orchestration, Redis, durable persistence, ASR execution.
```

### Task 5: Sync Specs, Docs, And Full Phase 1B Verification

**Files:**
- Modify: `contracts/http/README.md`
- Modify: `spec/resource_spec.yaml`
- Modify: `spec/invariant_spec.yaml`
- Modify: `docs/architecture/README.md`
- Modify: `services/api/README.md`
- Test: `services/api/tests/test_health.py`, `services/api/tests/test_contract_registry.py`, `services/api/tests/test_session_schemas.py`, `services/api/tests/test_message_schemas.py`, `services/api/tests/test_task_schemas.py`, `services/api/tests/test_sessions_api.py`, `services/api/tests/test_message_submission.py`, `services/api/tests/test_task_status_api.py`, `tests/smoke/test_repo_layout.py`

- [ ] **Step 1: Update the resource spec so it truthfully describes Phase 1B as ephemeral in-memory state**

```yaml
  - id: session
    status: implemented_phase1b
    storage:
      source_of_truth: process_memory
      durability: none
    contract_refs:
      - contracts/http/api.yaml#createSession
      - contracts/http/api.yaml#getSession

  - id: conversation_turn
    status: implemented_phase1b
    storage:
      source_of_truth: process_memory
      durability: none

  - id: assistant_task
    status: implemented_phase1b
    storage:
      source_of_truth: process_memory
      durability: none
      coordination: none
```

- [ ] **Step 2: Update the invariant note so the repository does not falsely claim durable state in this phase**

```yaml
  - id: INV-DATA-001
    scope: durable_state
    statement: Phase 1B uses process-local in-memory state for sessions, turns, and tasks; Postgres becomes the source of truth in the later persistence phase.
```

If a different phase-scoping sentence fits the existing style better, keep the meaning but do not leave the old Postgres-only statement unchanged.

- [ ] **Step 3: Update README-level docs for the new API surface and explicit non-goals**

```markdown
Current scope:
- keep `GET /health` stable
- expose a minimal in-memory HTTP loop for sessions, messages, and task polling
- keep request/response payloads aligned with `contracts/http/api.yaml`

Non-goals for Phase 1B:
- database persistence and migrations
- Redis-backed coordination
- worker-driven task execution
- Langfuse or ASR runtime wiring
```

- [ ] **Step 4: Update the architecture note to describe the new route and service boundaries**

```markdown
- `services/api/app/routes/sessions.py`, `messages.py`, and `tasks.py` own the four public Phase 1B endpoints
- `services/api/app/services/session_service.py` owns the in-memory session, turn, and task lifecycle
- `services/api/app/services/in_memory_state.py` is an ephemeral process-local placeholder until the persistence phase lands
```

- [ ] **Step 5: Run the full Phase 1B regression set**

Run: `.venv\Scripts\python.exe -m pytest services/api/tests/test_health.py services/api/tests/test_contract_registry.py services/api/tests/test_session_schemas.py services/api/tests/test_message_schemas.py services/api/tests/test_task_schemas.py services/api/tests/test_sessions_api.py services/api/tests/test_message_submission.py services/api/tests/test_task_status_api.py tests/smoke/test_repo_layout.py -v`

Expected: PASS for bootstrap health, schema-contract alignment, the new four-endpoint loop, and repo-layout smoke coverage.

- [ ] **Step 6: Run the host-level HTTP smoke command for the new entrypoint**

Run:

```powershell
powershell -Command "$env:API_PORT='18000'; $proc=Start-Process -FilePath .venv\Scripts\python.exe -ArgumentList '-m','uvicorn','services.api.app.main:app','--host','127.0.0.1','--port','18000' -PassThru; try { Start-Sleep -Seconds 1; Invoke-WebRequest -Method Post -Uri http://127.0.0.1:18000/v1/sessions -ContentType 'application/json' -Body '{\"channel\":\"web\"}' | Select-Object -ExpandProperty StatusCode } finally { Stop-Process -Id $proc.Id -Force }"
```

Expected: `201`

- [ ] **Step 7: Commit**

```bash
git add contracts/http/README.md spec/resource_spec.yaml spec/invariant_spec.yaml docs/architecture/README.md services/api/README.md
git commit -m "docs: sync phase1b in-memory http loop"
```

- [ ] **Step 8: Emit thick receipt**

```text
THICK RECEIPT
Task: Phase 1B / Docs and verification
Scope: Synced contracts, specs, architecture docs, service docs, and verified the 4-endpoint in-memory loop plus `/health`.
Files: contracts/http/README.md, spec/resource_spec.yaml, spec/invariant_spec.yaml, docs/architecture/README.md, services/api/README.md
Tests: .venv\Scripts\python.exe -m pytest services/api/tests/test_health.py services/api/tests/test_contract_registry.py services/api/tests/test_session_schemas.py services/api/tests/test_message_schemas.py services/api/tests/test_task_schemas.py services/api/tests/test_sessions_api.py services/api/tests/test_message_submission.py services/api/tests/test_task_status_api.py tests/smoke/test_repo_layout.py -v
HTTP Smoke: POST /v1/sessions returned 201 on a temporary uvicorn process.
Result: Phase 1B minimal HTTP contract loop is complete and ready for review.
Deferred: DV-004 durable persistence, worker state transitions, Redis, Langfuse, ASR execution, frontend clients.
```

## Final Review Checklist

- [ ] `GET /health` response shape is unchanged.
- [ ] Only the 4 approved business endpoints are marked `implemented` in `contracts/http/api.yaml`.
- [ ] No database, migration, Redis, worker, Langfuse, ASR runtime, or frontend code was added.
- [ ] `400` payloads use `BadRequestErrorResponse` instead of FastAPI's default `422`.
- [ ] `404` payloads use `NotFoundErrorResponse` for missing sessions and tasks.
- [ ] Session detail includes appended turns after message submission.
- [ ] Task polling returns queued state without pretending worker execution exists.
- [ ] Docs/specs describe the implementation as Phase 1B in-memory, not durable.

Plan complete and saved to `docs/superpowers/plans/2026-04-20-phase1b-http-minimal.md`. Per the current request, stop after planning and wait for review before implementation.
