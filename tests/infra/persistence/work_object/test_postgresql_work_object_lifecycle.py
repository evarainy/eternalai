"""Lifecycle concurrency and atomicity against fixture-owned PostgreSQL schemas."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, text

from app.infra.persistence.work_object import postgresql as pg
from app.ports.work_object_lifecycle import (
    LifecycleActor,
    LifecycleCommand,
    LifecycleStoreError,
    lifecycle_etag,
)
from app.ports.work_object_scope import AuthorizedWorkObjectScope
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db
from tests.api.test_work_object_dispatch import request_body, run


def actor(user):
    return LifecycleActor("default", "ai-" + user, user, "office-a", 1, time.monotonic() + 3600)


def scope(who):
    return AuthorizedWorkObjectScope(
        principal_tenant_id=who.tenant_id,
        principal_ai_user_id=who.ai_user_id,
        principal_department_id=who.department_id,
    )


async def prepared(db, object_id, who, operation="accept", message=None, key=None):
    record = await db.store.get_for_scope(object_id, scope(who))
    representation = {
        "work_object_id": object_id,
        "status": record.status,
        "version": record.version,
        "accepted_at": record.accepted_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if record.accepted_at
        else None,
        "completed_at": record.completed_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if record.completed_at
        else None,
        "available_commands": ["accept"] if operation == "accept" else ["feedback", "complete"],
        "unavailable_reason": None,
    }
    tag = lifecycle_etag(representation)
    fingerprint = hashlib.sha256(
        json.dumps(
            {"work_object_id": object_id, "operation": operation, "text": message, "if_match": tag},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    return dict(
        actor=who,
        command=LifecycleCommand(operation=operation, text=message),
        idempotency_key=key or uuid4(),
        request_fingerprint=fingerprint,
        expected_etag=tag,
        event_id=uuid4(),
    )


def publish_department(db):
    response = db.post(request_body(targets=[{"kind": "department", "department_id": "office-a"}]))
    assert response.status_code == 201
    return response.json()["items"][0]["work_object_id"]


async def write(db, object_id, args):
    return await db.store.apply_lifecycle_command_for_scope(object_id, scope(args["actor"]), **args)


def counts(db, object_id):
    with db.sql.connect() as connection:
        row = connection.execute(
            text(
                (
                    "SELECT status,version,accepted_by_ai_user_id FROM work_objects W"
                    "HERE work_object_id=:id"
                )
            ),
            {"id": object_id},
        ).one()
        events = connection.execute(
            text("SELECT count(*) FROM work_object_lifecycle_events WHERE work_object_id=:id"),
            {"id": object_id},
        ).scalar_one()
    return tuple(row), events


def test_two_claimants_cannot_both_accept(dispatch_db):
    db = dispatch_db
    object_id = publish_department(db)

    async def compete():
        requests = [await prepared(db, object_id, actor(user)) for user in ("b", "c")]
        results = await asyncio.gather(
            *(write(db, object_id, args) for args in requests), return_exceptions=True
        )
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, LifecycleStoreError)]
        assert len(successes) == len(failures) == 1
        assert failures[0].code == "work_object_transition_invalid"
        winner = successes[0].event.actor_ai_user_id
        loser = actor("c" if winner == "ai-b" else "b")
        denied = await prepared(db, object_id, loser, "feedback", "No")
        with pytest.raises(LifecycleStoreError, match="^work_object_action_forbidden$"):
            await write(db, object_id, denied)
        return winner

    winner = run(compete())
    assert counts(db, object_id) == (("in_progress", 2, winner), 1)


def test_concurrent_same_key_and_restart_replay_are_one_event(dispatch_db):
    db = dispatch_db
    object_id = publish_department(db)

    async def exercise():
        args = await prepared(db, object_id, actor("b"))
        results = await asyncio.gather(
            write(db, object_id, args), write(db, object_id, {**args, "event_id": uuid4()})
        )
        assert sorted(result.replayed for result in results) == [False, True]
        assert results[0].event == results[1].event
        restarted = pg.PostgreSQLWorkObjectStore(db.factory)
        replay = await restarted.apply_lifecycle_command_for_scope(
            object_id, scope(args["actor"]), **args
        )
        assert replay.replayed is True
        assert replay.event == results[0].event
        with pytest.raises(LifecycleStoreError, match="^idempotency_key_reused$"):
            await write(db, object_id, {**args, "request_fingerprint": "0" * 64})

    run(exercise())
    assert counts(db, object_id) == (("in_progress", 2, "ai-b"), 1)


@pytest.mark.parametrize("first", ["feedback", "complete"])
def test_feedback_and_complete_conflict_without_post_terminal_write(dispatch_db, first):
    db = dispatch_db
    object_id = publish_department(db)

    async def exercise():
        who = actor("b")
        await write(db, object_id, await prepared(db, object_id, who))
        feedback = await prepared(db, object_id, who, "feedback", "Progress")
        complete = await prepared(db, object_id, who, "complete", "Done")
        before, after = (feedback, complete) if first == "feedback" else (complete, feedback)
        async with db.factory() as blocker:
            async with blocker.begin():
                blocker_pid = (await blocker.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                await blocker.execute(
                    text(
                        "SELECT work_object_id FROM work_objects "
                        "WHERE work_object_id=:id FOR UPDATE"
                    ),
                    {"id": object_id},
                )
                tasks = []
                for request in (before, after):
                    tasks.append(asyncio.create_task(write(db, object_id, request)))
                    for _ in range(100):
                        async with db.factory() as observer:
                            waiting = (
                                await observer.execute(
                                    text(
                                        "WITH RECURSIVE waiters(pid) AS ("
                                        "SELECT pid FROM pg_stat_activity "
                                        "WHERE :blocker = ANY(pg_blocking_pids(pid)) UNION "
                                        "SELECT a.pid FROM pg_stat_activity a JOIN waiters w "
                                        "ON w.pid = ANY(pg_blocking_pids(a.pid))) "
                                        "SELECT count(*) FROM waiters"
                                    ),
                                    {"blocker": blocker_pid},
                                )
                            ).scalar_one()
                        if waiting == len(tasks):
                            break
                        await asyncio.sleep(0.01)
                    assert waiting == len(tasks), "Both independent transactions must contend"
                    assert all(not task.done() for task in tasks)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert not isinstance(results[0], Exception)
        expected = (
            "work_object_version_conflict"
            if first == "feedback"
            else "work_object_transition_invalid"
        )
        assert isinstance(results[1], LifecycleStoreError)
        assert results[1].code == expected

    run(exercise())
    assert counts(db, object_id) == (
        ("in_progress" if first == "feedback" else "completed", 3, "ai-b"),
        2,
    )


def test_event_insert_failure_rolls_back_work_object(dispatch_db):
    db = dispatch_db
    object_id = publish_department(db)
    before = db.rows("work_objects")

    def reject_event(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO work_object_lifecycle_events"):
            raise RuntimeError("Synthetic event insertion failure")

    async def exercise():
        args = await prepared(db, object_id, actor("b"))
        event.listen(db.engine.sync_engine, "before_cursor_execute", reject_event)
        try:
            with pytest.raises(RuntimeError, match="^Synthetic event insertion failure$"):
                await write(db, object_id, args)
        finally:
            event.remove(db.engine.sync_engine, "before_cursor_execute", reject_event)
        assert db.rows("work_objects") == before
        assert counts(db, object_id) == (("department_pending", 1, None), 0)
        await write(db, object_id, args)

    run(exercise())
    assert counts(db, object_id) == (("in_progress", 2, "ai-b"), 1)


@pytest.mark.parametrize("expired", [False, True])
def test_expiry_while_waiting_for_row_lock_blocks_mutation(dispatch_db, monkeypatch, expired):
    db = dispatch_db
    object_id = publish_department(db)
    clock = [100.0]
    monkeypatch.setattr(pg, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def exercise():
        who = replace(actor("b"), valid_until=101.0)
        args = await prepared(db, object_id, who)
        async with db.factory() as blocker:
            async with blocker.begin():
                blocker_pid = (await blocker.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                await blocker.execute(
                    text(
                        (
                            "SELECT work_object_id FROM work_objects WHERE work_object_id=:id"
                            " FOR UPDATE"
                        )
                    ),
                    {"id": object_id},
                )
                task = asyncio.create_task(write(db, object_id, args))
                for _ in range(100):
                    async with db.factory() as observer:
                        waiting = (
                            await observer.execute(
                                text(
                                    (
                                        "SELECT count(*) FROM pg_stat_activity "
                                        "WHERE :blocker = ANY(pg_blocking_pids(pid))"
                                    )
                                ),
                                {"blocker": blocker_pid},
                            )
                        ).scalar_one()
                    if waiting:
                        break
                    await asyncio.sleep(0.01)
                assert waiting >= 1, (
                    "The real competing transaction must be blocked on its row lock"
                )
                assert not task.done()
                clock[0] = 101.000001 if expired else 101.0
            if expired:
                with pytest.raises(LifecycleStoreError, match="^organization_directory_stale$"):
                    await task
            else:
                assert (await task).event.result_version == 2

    run(exercise())
    assert counts(db, object_id) == (
        (("department_pending", 1, None), 0) if expired else (("in_progress", 2, "ai-b"), 1)
    )


def test_events_apply_scope_and_version_paging_before_limit(dispatch_db):
    db = dispatch_db
    object_id = publish_department(db)

    async def exercise():
        who = actor("b")
        await write(db, object_id, await prepared(db, object_id, who))
        for index in range(4):
            await write(db, object_id, await prepared(db, object_id, who, "feedback", str(index)))
        first = await db.store.list_lifecycle_events_for_scope(
            object_id, scope(who), after_version=0, limit=3
        )
        second = await db.store.list_lifecycle_events_for_scope(
            object_id, scope(who), after_version=first[-1].result_version, limit=3
        )
        assert [item.result_version for item in first + second] == [2, 3, 4, 5, 6]
        for denied in (replace(who, tenant_id="foreign"), replace(who, department_id="office-b")):
            assert (
                await db.store.list_lifecycle_events_for_scope(
                    object_id, scope(denied), after_version=0, limit=3
                )
                is None
            )

    run(exercise())


def test_completed_window_and_scope_are_filtered_before_limit(dispatch_db):
    db = dispatch_db
    object_id = publish_department(db)

    async def exercise():
        who = actor("b")
        async with db.factory() as session, session.begin():
            await session.execute(
                text("""
                INSERT INTO work_objects
                SELECT (jsonb_populate_record(NULL::work_objects, to_jsonb(w) || jsonb_build_object(
                  'work_object_id', 'window-' || n, 'title', 'Window evidence',
                  'tenant_id', CASE WHEN n=204 THEN 'foreign' ELSE 'default' END,
                  'owner_department_id', CASE WHEN n=205 THEN 'office-b' ELSE 'office-a' END,
                  'status','completed', 'version',3,
                  'created_at',CURRENT_TIMESTAMP-interval '40 days',
                  'accepted_by_ai_user_id','ai-b',
                  'accepted_at',CURRENT_TIMESTAMP-interval '39 days',
                  'completed_by_ai_user_id','ai-b', 'completed_at',stamp.at,
                  'updated_at',stamp.at))).*
                FROM work_objects w CROSS JOIN generate_series(0,205) n
                CROSS JOIN LATERAL (SELECT CASE
                  WHEN n=0 THEN CURRENT_TIMESTAMP-interval '30 days'
                  WHEN n=1 THEN CURRENT_TIMESTAMP
                  WHEN n=202 THEN CURRENT_TIMESTAMP-interval '30 days 0.000001 seconds'
                  WHEN n=203 THEN CURRENT_TIMESTAMP+interval '0.000001 seconds'
                  ELSE CURRENT_TIMESTAMP-interval '1 day' END AS at) stamp
                WHERE w.work_object_id=:id
            """),
                {"id": object_id},
            )
            # The same transaction fixes both the SQL clock and both inclusive endpoints.
            records = await db.store._list_records(
                session,
                scope(who),
                search_term="Window evidence",
                oa_view="active",
                completion="completed",
                limit=201,
            )
            ids = {record.work_object_id for record in records}
            assert len(records) == 201
            assert "window-1" in ids
            assert ids.isdisjoint({"window-202", "window-203", "window-204", "window-205"})
            # Separate q selects each endpoint without a LIMIT cutting the oldest valid row.
            await session.execute(
                text(
                    "UPDATE work_objects SET title='Endpoint' WHERE work_object_id IN "
                    "('window-0','window-1','window-202','window-203')"
                )
            )
            endpoints = await db.store._list_records(
                session,
                scope(who),
                search_term="Endpoint",
                oa_view="active",
                completion="completed",
                limit=201,
            )
            assert {record.work_object_id for record in endpoints} == {"window-0", "window-1"}
            active = await db.store._list_records(
                session,
                scope(who),
                search_term=None,
                oa_view="active",
                completion="active",
                limit=201,
            )
            assert [record.work_object_id for record in active] == [object_id]

    run(exercise())
