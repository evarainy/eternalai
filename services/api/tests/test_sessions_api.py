from fastapi.testclient import TestClient


def test_create_session_returns_201_and_payload(client: TestClient) -> None:
    response = client.post("/v1/sessions", json={"channel": "web", "title": "Phase 1B"})

    assert response.status_code == 201
    body = response.json()
    assert body["session_id"].startswith("sess_")
    assert body["channel"] == "web"
    assert body["status"] == "active"
    assert body["title"] == "Phase 1B"


def test_get_session_returns_created_session_and_turns(client: TestClient) -> None:
    created_response = client.post("/v1/sessions", json={"channel": "web"})
    assert created_response.status_code == 201
    created = created_response.json()

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
