from fastapi.testclient import TestClient


def test_submit_message_returns_202_and_creates_pollable_task(client: TestClient) -> None:
    created_response = client.post("/v1/sessions", json={"channel": "web"})
    assert created_response.status_code == 201
    created = created_response.json()

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
    created_response = client.post("/v1/sessions", json={"channel": "web"})
    assert created_response.status_code == 201
    created = created_response.json()
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
    created_response = client.post("/v1/sessions", json={"channel": "web"})
    assert created_response.status_code == 201
    created = created_response.json()

    response = client.post(
        f"/v1/sessions/{created['session_id']}/messages",
        json={"input_type": "audio", "text": "not allowed"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
