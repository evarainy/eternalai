"""Bounded Session Memory behavior and isolation."""

from __future__ import annotations

import pytest

from app.memory import (
    MAX_CONTEXTS,
    MAX_ENTRIES_PER_CONTEXT,
    SessionMemory,
    SessionMemoryKey,
    SessionMemorySummary,
)


def _key(
    *,
    tenant_id: str = "tenant-1",
    session_id: str = "session-1",
    ai_user_id: str = "user-1",
) -> SessionMemoryKey:
    return SessionMemoryKey(
        tenant_id=tenant_id,
        session_id=session_id,
        ai_user_id=ai_user_id,
    )


def test_memory_keeps_exactly_the_most_recent_five_entries_per_context() -> None:
    memory = SessionMemory()
    key = _key()

    for index in range(MAX_ENTRIES_PER_CONTEXT + 2):
        assert memory.remember_completed(key, capability_id=f"oa.query.{index}")

    assert memory.recall(key) == tuple(
        SessionMemorySummary(capability_id=f"oa.query.{index}")
        for index in range(2, MAX_ENTRIES_PER_CONTEXT + 2)
    )
    assert memory.context_count == 1
    assert memory.entry_count == MAX_ENTRIES_PER_CONTEXT


def test_memory_evicts_the_oldest_context_at_the_exact_global_limit() -> None:
    memory = SessionMemory()
    oldest_key = _key(session_id="session-0")

    for index in range(MAX_CONTEXTS):
        assert memory.remember_completed(
            _key(session_id=f"session-{index}"),
            capability_id=f"oa.query.{index}",
        )

    newest_key = _key(session_id=f"session-{MAX_CONTEXTS}")
    assert memory.remember_completed(newest_key, capability_id="oa.query.newest")

    assert memory.context_count == MAX_CONTEXTS
    assert memory.entry_count == MAX_CONTEXTS
    assert memory.recall(oldest_key) == ()
    assert memory.recall(newest_key) == (
        SessionMemorySummary(capability_id="oa.query.newest"),
    )


@pytest.mark.parametrize(
    "other_key",
    [
        _key(session_id="session-2"),
        _key(ai_user_id="user-2"),
        _key(tenant_id="tenant-2"),
    ],
    ids=["session", "ai-user", "tenant-structure"],
)
def test_memory_recall_requires_an_exact_tenant_session_user_key(
    other_key: SessionMemoryKey,
) -> None:
    memory = SessionMemory()
    owner_key = _key()
    assert memory.remember_completed(owner_key, capability_id="oa.owner.query")

    assert memory.recall(other_key) == ()
    assert memory.recall(owner_key) == (
        SessionMemorySummary(capability_id="oa.owner.query"),
    )


@pytest.mark.parametrize(
    "credential_assignment",
    [
        "authorization=" + "synthetic-value",
        "session" + "id=synthetic-value",
        "access_" + "token=synthetic-value",
        "refresh_" + "token=synthetic-value",
        "set_" + "cookie=synthetic-value",
        "password=" + "synthetic-value",
        "passwd=" + "synthetic-value",
        "api_" + "key=synthetic-value",
        "secret=" + "synthetic-value",
        "client_" + "secret=synthetic-value",
        "private_" + "key=synthetic-value",
        "Bearer " + "synthetic-value",
    ],
)
def test_memory_rejects_every_credential_assignment_family(
    credential_assignment: str,
) -> None:
    memory = SessionMemory()
    key = _key()

    assert not memory.remember_completed(
        key,
        capability_id="oa.query?" + credential_assignment,
    )
    assert memory.recall(key) == ()
    assert memory.context_count == 0
    assert credential_assignment not in repr(memory.recall(key))
