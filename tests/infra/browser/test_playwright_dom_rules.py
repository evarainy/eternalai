import asyncio

import pytest

from app.infra.browser.playwright_dom_rules import (
    DOMBatchError, DOMStep, DOMValue, _FILTER_BATCH, _IDENTITY_BATCH,
    bounded_children, filter_private_candidates, match_actual_nodes, read_private,
)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"selector": ""},
        {"selector": "x" * 513},
        {"selector": "x", "kind": "attribute"},
        {"selector": "x", "attribute": "private"},
        {"selector": "x", "kind": "script"},
    ],
)
def test_value_registration_is_closed(kwargs) -> None:
    with pytest.raises(ValueError, match="registration_invalid"):
        DOMValue(**kwargs)


def test_key_locator_requires_registered_row_and_mapping() -> None:
    with pytest.raises(ValueError, match="registration_invalid"):
        DOMStep("open", "#control", row_selector=".row")
    value = DOMValue(".key")
    step = DOMStep("open", "#control", row_selector=".row", key=value)
    assert step.key is value and ".key" not in repr(step)


@pytest.mark.parametrize("selector", ["", "x" * 513])
def test_private_row_selector_is_bounded(selector: str) -> None:
    with pytest.raises(ValueError, match="registration_invalid"):
        DOMStep("open", "#control", row_selector=selector, key=DOMValue(".key"))


@pytest.mark.parametrize("result", [42, True, "x" * 17])
def test_private_result_types_and_bytes_are_bounded(result) -> None:
    class Root:
        async def evaluate(self, script, config):
            assert "TextEncoder" in script and config["limit"] == 16
            return result

    with pytest.raises(ValueError, match="read_bound"):
        asyncio.run(read_private(Root(), DOMValue(".value"), 16))


def test_children_budget_prevents_handle_allocation() -> None:
    class Root:
        async def evaluate(self, script, selector):
            assert "querySelectorAll" in script and selector == ".row"
            return 9

        async def query_selector_all(self, selector):
            raise AssertionError("must_not_allocate_overflow_handles")

    with pytest.raises(ValueError, match="children_bound"):
        asyncio.run(bounded_children(Root(), ".row", 8))


@pytest.mark.parametrize("packet", [
    None, {"status": "ok", "same_region": True, "indices": [True]},
    {"status": "ok", "same_region": True, "indices": [1]},
    {"status": "ok", "same_region": True, "indices": [0, 0]},
    {"status": "ok", "same_region": False, "indices": [0]},
    {"status": "ok", "same_region": True, "indices": [], "extra": 1},
])
def test_identity_packet_rejects_wrong_types_lengths_indices_and_duplicates(packet) -> None:
    class Root:
        async def evaluate(self, script, config):
            assert script == _IDENTITY_BATCH
            assert config["previous"] == [previous] and config["current"] == [current]
            return packet

    previous, current = object(), object()
    with pytest.raises(DOMBatchError) as failure:
        asyncio.run(match_actual_nodes(Root(), object(), (previous,), (current,)))
    assert failure.value.code == "invalid_response"


def test_identity_batch_preserves_order_and_borrows_handles() -> None:
    class Root:
        calls = 0

        async def evaluate(self, script, config):
            self.calls += 1
            assert script == _IDENTITY_BATCH and config["region"] is self
            assert config["previous"] == [first, second]
            assert config["current"] == [second, first]
            return {"status": "ok", "same_region": True, "indices": [1, 0]}

    first, second, root = object(), object(), Root()
    assert asyncio.run(match_actual_nodes(root, root, (first, second), (second, first))) == (True, (1, 0))
    assert root.calls == 1


def test_identity_batch_rejects_duplicate_indices_at_correct_length() -> None:
    class Root:
        async def evaluate(self, script, config):
            assert script == _IDENTITY_BATCH
            assert len(config["previous"]) == len(config["current"]) == 2
            return {"status": "ok", "same_region": True, "indices": [0, 0]}

    with pytest.raises(DOMBatchError) as failure:
        asyncio.run(match_actual_nodes(Root(), object(), (object(), object()), (object(), object())))
    assert failure.value.code == "invalid_response"


def test_identity_batch_transport_failure_is_never_new_nodes() -> None:
    class Root:
        async def evaluate(self, script, config):
            raise ConnectionError("synthetic transport")

    with pytest.raises(ConnectionError):
        asyncio.run(match_actual_nodes(Root(), None, (), (object(),)))


@pytest.mark.parametrize("code", ["denied", "invalid_response"])
def test_filter_fatal_prevents_returning_other_matching_candidates(code: str) -> None:
    class Root:
        async def evaluate(self, script, config):
            assert script == _FILTER_BATCH and len(config["nodes"]) == 2
            return [{"index": 0, "state": "match", "value": "synthetic_key", "code": None},
                    {"index": 1, "state": "fatal", "value": None, "code": code}]

    with pytest.raises(DOMBatchError) as failure:
        asyncio.run(filter_private_candidates(Root(), (object(), object()), ".control", ".row",
                                               DOMValue(".key"), 4096))
    assert failure.value.code == code


@pytest.mark.parametrize("packet", [
    [{"index": True, "state": "match", "value": None, "code": None}],
    [{"index": 0, "state": "no_match", "value": "unexpected", "code": None}],
    [{"index": 0, "state": "fatal", "value": None, "code": "unknown"}],
    [{"index": 0, "state": "match", "value": "雨" * 6, "code": None}],
    [{"index": 0, "state": "match", "value": False, "code": None}],
])
def test_filter_packet_is_closed_and_utf8_bounded(packet) -> None:
    class Root:
        async def evaluate(self, script, config):
            assert script == _FILTER_BATCH
            return packet

    with pytest.raises(DOMBatchError) as failure:
        asyncio.run(filter_private_candidates(Root(), (object(),), ".control", ".row",
                                               DOMValue(".key"), 16))
    assert failure.value.code == "invalid_response"
