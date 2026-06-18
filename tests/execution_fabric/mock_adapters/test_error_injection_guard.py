"""Guard tests for the Phase 0 mock error injection control endpoint."""

from __future__ import annotations

from typing import get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.mock_control import router, should_register
from app.ports.adapter import MockErrorMode


def test_mock_control_endpoint_enabled_by_phase0_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHASE0_MOCK_MODE", "true")
    monkeypatch.delenv("ENV", raising=False)

    app = FastAPI()
    if should_register():
        app.include_router(router)

    response = TestClient(app).post(
        "/mock/oa.query/inject",
        json={"error_mode": "timeout", "duration": "next_1_call"},
    )

    assert response.status_code == 200


def test_mock_control_endpoint_absent_outside_testing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("PHASE0_MOCK_MODE", raising=False)
    assert should_register() is False

    app = FastAPI()
    response = TestClient(app).post(
        "/mock/oa.query/inject",
        json={"error_mode": "timeout", "duration": "next_1_call"},
    )

    assert response.status_code == 404


def test_invalid_error_mode_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHASE0_MOCK_MODE", "true")
    app = FastAPI()
    if should_register():
        app.include_router(router)

    response = TestClient(app).post(
        "/mock/oa.query/inject",
        json={"error_mode": "random_failure", "duration": "next_1_call"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("mode", get_args(MockErrorMode))
def test_all_mock_error_modes_are_accepted(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHASE0_MOCK_MODE", "true")
    modes = get_args(MockErrorMode)
    assert len(modes) == 6

    app = FastAPI()
    if should_register():
        app.include_router(router)

    response = TestClient(app).post(
        "/mock/oa.query/inject",
        json={"error_mode": mode, "duration": "next_1_call"},
    )

    assert response.status_code == 200
    assert response.json()["error_mode"] == mode


def test_error_injection_request_extra_field_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHASE0_MOCK_MODE", "true")
    app = FastAPI()
    if should_register():
        app.include_router(router)

    response = TestClient(app).post(
        "/mock/oa.query/inject",
        json={
            "error_mode": "timeout",
            "duration": "next_1_call",
            "unexpected": "bad",
        },
    )

    assert response.status_code == 422


def test_error_detail_arbitrary_string_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHASE0_MOCK_MODE", "true")
    app = FastAPI()
    if should_register():
        app.include_router(router)

    response = TestClient(app).post(
        "/mock/oa.query/inject",
        json={
            "error_mode": "timeout",
            "duration": "next_1_call",
            "error_detail": "some-arbitrary-string-xyzzy-2026",
        },
    )

    assert response.status_code == 200
    assert response.json()["error_detail"] == "some-arbitrary-string-xyzzy-2026"
