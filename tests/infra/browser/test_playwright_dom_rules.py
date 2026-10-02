import asyncio

import pytest

from app.infra.browser.playwright_dom_rules import DOMStep, DOMValue, bounded_children, read_private


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
