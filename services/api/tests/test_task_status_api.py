from fastapi.testclient import TestClient


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
