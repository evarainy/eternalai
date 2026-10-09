"""Frozen logical steps versus repeated, bounded dispatch sequence."""

from types import SimpleNamespace
from typing import cast

import pytest

from app.browser_skill.publication_contracts import BrowserPublicationManifest
from app.infra.persistence.browser.runs import PostgreSQLBrowserRunStore
from app.ports.browser_run_store import BrowserRunStoreError


def _manifest(*, max_pages: int = 3) -> BrowserPublicationManifest:
    # This test exercises the pure persisted-audit state machine. Publication
    # schema validation is covered at its own boundary.
    return cast(BrowserPublicationManifest, SimpleNamespace(
        skill=SimpleNamespace(steps=tuple(
            SimpleNamespace(step_id=step_id)
            for step_id in ("open", "next", "write")
        )),
        site=SimpleNamespace(exploration=None, traversal=SimpleNamespace(
            advance_step_id="next", max_pages=max_pages,
        )),
    ))


def test_repeated_page_advance_can_precede_a_write() -> None:
    assert PostgreSQLBrowserRunStore._check_step_route(
        _manifest(), ("open", "next", "next", "write"), complete=True,
        terminal_after=3,
    ) is None


def test_terminal_first_page_can_precede_a_write() -> None:
    assert PostgreSQLBrowserRunStore._check_step_route(
        _manifest(), ("open", "write"), complete=True, terminal_after=1,
    ) is None


@pytest.mark.parametrize("steps", [
    ("open", "write"),
    ("open", "next", "next", "next", "write"),
    ("open", "next", "write", "next"),
    ("open", "next", "write"),
])
def test_step_route_rejects_skips_overflow_and_reordering(
    steps: tuple[str, ...],
) -> None:
    with pytest.raises(BrowserRunStoreError):
        PostgreSQLBrowserRunStore._check_step_route(
            _manifest(), steps, complete=True,
        )


def test_terminal_proof_cannot_be_reused_after_an_advance() -> None:
    with pytest.raises(BrowserRunStoreError) as error:
        PostgreSQLBrowserRunStore._check_step_route(
            _manifest(), ("open", "next", "write"),
            complete=True, terminal_after=1,
        )
    assert error.value.code == "browser_step_audit_sequence_conflict"


def test_step_route_cannot_complete_before_following_write() -> None:
    with pytest.raises(BrowserRunStoreError) as error:
        PostgreSQLBrowserRunStore._check_step_route(
            _manifest(), ("open", "next"), complete=True,
        )
    assert error.value.code == "browser_step_audit_incomplete"
