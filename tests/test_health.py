import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.infra.health.redis import RedisHealthCheck
from app.main import app, create_app


async def _healthy_check() -> bool:
    return True


client = TestClient(
    create_app(
        health_checks={
            "database": _healthy_check,
            "redis": _healthy_check,
            "vllm": _healthy_check,
        }
    )
)


def test_health_returns_200() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_health_response_shape() -> None:
    response = client.get("/api/v1/health")
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {
        "database": "ok",
        "redis": "ok",
        "vllm": "ok",
    }


def test_health_fails_closed_without_exposing_checker_exception() -> None:
    async def failed_check() -> bool:
        raise RuntimeError("connection-secret-must-not-escape")

    response = TestClient(
        create_app(health_checks={"database": failed_check})
    ).get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unhealthy",
        "checks": {"database": "failed"},
    }
    assert "connection-secret-must-not-escape" not in response.text


def test_health_hides_password_from_redis_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password_marker = "health-password-marker"

    async def failed_open_connection(
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[Any, Any]:
        raise OSError(f"connection failed with {password_marker}")

    monkeypatch.setattr(asyncio, "open_connection", failed_open_connection)
    redis_check = RedisHealthCheck(
        redis_url=(
            f"redis://pilot:{password_marker}@redis.invalid:6379/0"
        ),
        timeout_seconds=1,
    )

    response = TestClient(
        create_app(health_checks={"redis": redis_check})
    ).get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unhealthy",
        "checks": {"redis": "failed"},
    }
    assert password_marker not in response.text


def test_health_fails_closed_without_configured_checks() -> None:
    response = TestClient(create_app()).get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "checks": {}}


def test_health_times_out_a_hanging_checker() -> None:
    async def hanging_check() -> bool:
        await asyncio.Event().wait()
        return True

    response = TestClient(
        create_app(
            health_checks={"database": hanging_check},
            health_timeout_seconds=0.01,
        )
    ).get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unhealthy",
        "checks": {"database": "failed"},
    }


def test_formal_app_and_factory_register_health_and_runtime_routes() -> None:
    expected_paths = {
        "/api/v1/health",
        "/api/v1/auth/login",
        "/api/v1/runtime/handle",
        "/api/v1/admin/registry",
        "/api/v1/admin/registry/{capability_id}",
        "/api/v1/admin/registry/{capability_id}/enable",
        "/api/v1/admin/registry/{capability_id}/disable",
    }

    assert expected_paths <= set(app.openapi()["paths"])
    assert expected_paths <= set(create_app().openapi()["paths"])


def test_health_openapi_matches_success_and_failure_response_model() -> None:
    schema = create_app().openapi()
    operation = schema["paths"]["/api/v1/health"]["get"]

    for status_code in ("200", "503"):
        response_schema = operation["responses"][status_code]["content"][
            "application/json"
        ]["schema"]
        assert response_schema == {
            "$ref": "#/components/schemas/HealthResponse"
        }

    health_schema = schema["components"]["schemas"]["HealthResponse"]
    assert health_schema["required"] == ["status", "checks"]
    assert health_schema["properties"]["status"]["enum"] == [
        "ok",
        "unhealthy",
    ]
