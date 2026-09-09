import json
from pathlib import Path

import pytest

from app.ports.work_object_search import normalize_search_query, normalize_search_value

CONTRACT = json.loads(
    (Path(__file__).parents[1] / "contracts/work_object_search_normalization.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("vector", CONTRACT["normalization"], ids=lambda row: row["id"])
def test_shared_vectors_match_the_normalization_contract(vector: dict) -> None:
    value, expected = vector["input"], vector["expected"]
    if value is not None:
        assert normalize_search_value(value) == expected
    assert normalize_search_query(value) == (expected or None)


@pytest.mark.parametrize("value", [None, "", " ", "\u3000", "\u00a0", "\t"])
def test_none_query_stays_none_and_empty_normalized_query_is_none(value: str | None) -> None:
    assert normalize_search_query(value) is None


@pytest.mark.parametrize("vector", CONTRACT["matching"])
def test_shared_matching_vectors(vector: dict) -> None:
    field = normalize_search_value(vector["input"])
    query = normalize_search_value(vector["query"])
    matched = query in field if vector["field"] == "title" else query == field
    assert matched is vector["expected"]
