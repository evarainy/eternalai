"""Deterministic Top-K capability candidate selection contract tests."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from app.knowledge.capability_selection import (
    CAPABILITY_CANDIDATE_PROTOCOL_VERSION,
    MAX_CAPABILITY_CANDIDATE_SEGMENT_BYTES,
    MAX_CAPABILITY_CONTRACT_BYTES,
    CapabilityCandidateSet,
    _normalize,
    _tokens,
    _validated_candidates,
    _with_score,
    canonical_json,
    select_capability_candidates,
)
from app.ports.capability_registry import CapabilitySpec

_CONTRACT_KEYS = {
    "capability_id",
    "capability_type",
    "target_system",
    "status",
    "allowed_argument_keys",
    "required_argument_keys",
    "additionalProperties",
    "short_description",
    "owner",
    "version",
    "risk_level",
    "input_summary",
    "output_summary",
}


def _spec(
    capability_id: str,
    *,
    name: str | None = None,
    short_description: str = "Synthetic capability.",
    intent_tags: list[str] | None = None,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    owner: str = "synthetic-owner",
    version: str = "1.0.0",
) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        name=name or f"Synthetic {capability_id}",
        type="query",
        intent_tags=intent_tags or [],
        input_schema=(
            {"type": "object", "properties": {}, "additionalProperties": False}
            if input_schema is None
            else input_schema
        ),
        output_schema={"type": "object"} if output_schema is None else output_schema,
        input_schema_digest=f"input-{capability_id}",
        output_schema_digest=f"output-{capability_id}",
        risk_level="low",
        owner=owner,
        version=version,
        status="active",
        short_description=short_description,
        target_system="oa",
        execution_identity="user_delegated",
        binding_required=False,
    )


def _ids(selection: CapabilityCandidateSet) -> list[str]:
    return [binding.capability_id for binding in selection.bindings]


def _score(message: str, capability: CapabilitySpec) -> tuple[int, ...]:
    (candidate,) = _validated_candidates([capability])
    return _with_score(candidate, _normalize(message), _tokens(message)).score


def _contract_bytes(capability: CapabilitySpec) -> int:
    (candidate,) = _validated_candidates([capability])
    return candidate.contract_bytes


def _sized(capability_id: str, target_bytes: int, *, name: str) -> CapabilitySpec:
    """Build a legal capability whose canonical contract is exactly target_bytes."""

    base = _spec(
        capability_id,
        name=name,
        input_schema={"type": "object", "properties": {"k": {"type": "string"}}},
        short_description="d",
    )
    delta = target_bytes - _contract_bytes(base)
    assert delta >= 0
    padding = delta % 2
    key = "k" * (1 + delta // 2)
    sized = _spec(
        capability_id,
        name=name,
        input_schema={"type": "object", "properties": {key: {"type": "string"}}},
        short_description="d" * (1 + padding),
    )
    assert _contract_bytes(sized) == target_bytes
    return sized


def test_projection_revalidates_bypassed_models() -> None:
    target = _spec("oa.target", name="Target lookup")
    bypassed = {
        "owner": target.model_copy(update={"owner": "owner-canary\x00"}),
        "description": target.model_copy(
            update={"short_description": "description-canary {system}"}
        ),
        "sensitive": target.model_copy(
            update={"short_description": "description-canary https://synthetic.invalid/x"}
        ),
        "tag": target.model_copy(update={"intent_tags": ["Tag Canary!"]}),
        "version": target.model_copy(update={"version": "1.0 version-canary"}),
        "key": target.model_copy(
            update={"input_schema": {"type": "object", "properties": {"key canary": {}}}}
        ),
        "required": target.model_copy(
            update={
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": ["required-canary"],
                }
            }
        ),
        "constructed": CapabilitySpec.model_construct(**{**target.model_dump(), "name": ""}),
    }

    for label, capability in bypassed.items():
        selection = select_capability_candidates(
            "oa.target",
            (_spec("oa.other"), capability.model_copy(update={"capability_id": "oa.bad"})),
        )
        assert selection.outcome == "catalog_invalid", label
        assert selection.contracts == ()
        assert selection.bindings == ()
        assert selection.payload_bytes == 0
        assert selection.payload_json == ""
        assert "canary" not in repr(selection)
    duplicate = select_capability_candidates("oa.target", (target, target))
    assert duplicate.outcome == "catalog_invalid"

    valid = select_capability_candidates("oa.target", (_spec("oa.other"), target))
    assert valid.outcome == "ready"
    assert _ids(valid)[0] == "oa.target"


@pytest.mark.parametrize("field", ["input_schema", "output_schema"])
@pytest.mark.parametrize(
    "key",
    [
        "password",
        "access_token",
        "auth_header",
        "sessionid",
        "refreshToken",
        "AuthHeader",
        "HTTPAuthHeader",
        "access.token",
        "client-secret",
        "sessionID",
    ],
)
def test_credential_property_segments_are_rejected(field: str, key: str) -> None:
    capability = _spec("oa.target").model_copy(
        update={field: {"type": "object", "properties": {key: {"type": "string"}}}}
    )
    selection = select_capability_candidates("oa.target", [capability])
    assert selection.outcome == "catalog_invalid"
    assert selection.contracts == selection.bindings == ()
    assert selection.payload_json == ""


@pytest.mark.parametrize(
    "key", ["authoritative_count", "author", "authorizationCounted", "item_count"]
)
def test_business_property_words_are_preserved(key: str) -> None:
    # authorizationCounted contains the actual authorization segment and is unsafe.
    selection = select_capability_candidates(
        "oa.target",
        [
            _spec(
                "oa.target",
                output_schema={"type": "object", "properties": {key: {"type": "integer"}}},
            )
        ],
    )
    if key == "authorizationCounted":
        assert selection.outcome == "catalog_invalid"
    else:
        assert selection.outcome == "ready"
        assert selection.contracts[0]["output_summary"]["properties"] == [
            {"key": key, "type": "integer"}
        ]


@pytest.mark.parametrize("keyword", ["$ref", "anyOf", "allOf", "oneOf", "not", "if"])
def test_composite_property_with_explicit_type_is_unspecified(keyword: str) -> None:
    selection = select_capability_candidates(
        "oa.target",
        [
            _spec(
                "oa.target",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string", keyword: {}}},
                },
            )
        ],
    )
    assert selection.outcome == "ready"
    summary = selection.contracts[0]["input_summary"]
    assert summary["properties"] == [{"key": "value", "type": "unspecified"}]
    assert summary["partial"] is True


@pytest.mark.parametrize("suffix", ["._extra", ".-extra", ".extra", "_extra", "-extra", "."])
def test_slug_prefix_is_not_an_exact_id_or_tag_phrase(suffix: str) -> None:
    capability = _spec("oa.work", intent_tags=["work-tag"])
    assert _score("oa.work" + suffix, capability)[0] == 0
    assert _score("work-tag" + suffix, capability)[1] == 0
    assert _score("查询 oa.work，请继续", capability)[0] == 1
    assert _score("use work-tag now", capability)[1] == 1


def test_summary_allowlist_is_complete_and_value_free() -> None:
    long_key = "synthetic_" + "long_key_" * 20
    capability = _spec(
        "oa.summary",
        name="name-marker 查询",
        short_description="查询合成摘要 summary text",
        owner="owner team",
        version="2.1.0+build.7",
        intent_tags=["tag-marker"],
        input_schema={
            "type": "object",
            "description": "schema-description-marker",
            "properties": {
                long_key: {"type": "string", "default": "default-marker"},
                "status": {"type": "string", "enum": ["enum-marker"]},
                "nested": {"anyOf": [{"type": "string"}], "examples": ["example-marker"]},
            },
            "required": [long_key],
            "additionalProperties": False,
        },
        output_schema={},
    )
    empty_arguments = _spec("oa.empty")

    selection = select_capability_candidates("查询", (capability, empty_arguments))

    assert selection.outcome == "ready"
    contract = next(item for item in selection.contracts if item["capability_id"] == "oa.summary")
    assert set(contract) == _CONTRACT_KEYS
    assert contract["short_description"] == "查询合成摘要 summary text"
    assert contract["owner"] == "owner team"
    assert contract["version"] == "2.1.0+build.7"
    assert contract["risk_level"] == "low"
    assert contract["allowed_argument_keys"] == sorted([long_key, "status", "nested"])
    assert contract["required_argument_keys"] == [long_key]
    assert contract["additionalProperties"] is False
    assert contract["input_summary"] == {
        "root_type": "object",
        "properties": [
            {"key": "nested", "type": "unspecified"},
            {"key": "status", "type": "string"},
            {"key": long_key, "type": "string"},
        ],
        "required": [long_key],
        "additionalProperties": False,
        "partial": True,
    }
    assert contract["output_summary"] == {
        "root_type": "unspecified",
        "properties": [],
        "required": [],
        "additionalProperties": "unspecified",
        "partial": True,
    }
    empty_contract = next(
        item for item in selection.contracts if item["capability_id"] == "oa.empty"
    )
    assert set(empty_contract) == _CONTRACT_KEYS | {"arguments_must_be"}
    assert empty_contract["arguments_must_be"] == {}
    assert empty_contract["input_summary"]["partial"] is False
    serialized = selection.payload_json
    for marker in (
        "name-marker",
        "tag-marker",
        "schema-description-marker",
        "default-marker",
        "enum-marker",
        "example-marker",
    ):
        assert marker not in serialized
    assert serialized == canonical_json(
        {
            "capability_candidates": {
                "protocol_version": CAPABILITY_CANDIDATE_PROTOCOL_VERSION,
                "coverage_complete": True,
                "omitted_count": 0,
                "truncated_by": [],
                "items": list(selection.contracts),
            }
        }
    )
    assert selection.payload_bytes == len(serialized.encode("utf-8"))


@pytest.mark.parametrize(
    ("message", "target", "weak_description"),
    [
        ("请执行 zz.tail-target", _spec("zz.tail-target"), "请执行其他操作"),
        ("帮我查询差旅补贴", _spec("zz.tail-target", name="差旅补贴查询"), "帮我查询"),
        (
            "expense reimbursement status",
            _spec("zz.tail-target", short_description="Expense reimbursement status."),
            "Synthetic status export.",
        ),
    ],
)
def test_ninth_candidate_wins_by_request_relevance(
    message: str,
    target: CapabilitySpec,
    weak_description: str,
) -> None:
    zero_group = [_spec(f"aa.item-{index}") for index in range(8)]
    weak_group = [
        _spec(f"aa.weak-{index}", short_description=weak_description) for index in range(8)
    ]

    beside_zero = select_capability_candidates(message, (*zero_group, target))
    beside_weak = select_capability_candidates(message, (*weak_group, target))

    for selection, reasons in (
        (beside_zero, ("relevance",)),
        (beside_weak, ("count",)),
    ):
        assert selection.outcome == "ready"
        assert _ids(selection) == ["zz.tail-target"]
        assert selection.visible_count == 9
        assert selection.selected_count == 1
        assert selection.omitted_count == 8
        assert selection.coverage_complete is False
        assert selection.truncated_by == reasons


def test_strict_eighth_and_ninth_scores_admit_exactly_the_top_eight() -> None:
    words = [f"w{index}" for index in range(10)]
    message = " ".join(words)
    capabilities = [
        _spec(f"aa.rank-{index}", name=" ".join(words[: 10 - index])) for index in range(10)
    ]

    selection = select_capability_candidates(message, list(reversed(capabilities)))

    assert selection.outcome == "ready"
    assert _ids(selection) == [f"aa.rank-{index}" for index in range(8)]
    assert selection.omitted_count == 2
    assert selection.truncated_by == ("count",)


def test_scoring_is_exact_deterministic_and_permutation_invariant() -> None:
    message = "查询OA待办 oa.pending-list please"
    exact_id = _spec(
        "oa.pending-list",
        name="OA 待办查询",
        short_description="查询 OA 待办列表",
        intent_tags=["pending-list"],
    )
    prefix_id = _spec(
        "oa.pending",
        name="Pending",
        short_description="Other",
        intent_tags=["please"],
    )

    assert _tokens(message) == {"oa", "pending", "list", "please", "查询", "待办"}
    assert _tokens("a 1 查 查询询") == {"查询", "询询"}
    assert _score(message, exact_id) == (1, 0, 0, 428, 375, 500)
    assert _score(message, prefix_id) == (0, 1, 1, 166, 0, 333)
    assert _score("ＯＡ．ＰＥＮＤＩＮＧ－ＬＩＳＴ", exact_id)[0] == 1
    assert _score("xoa.pending-list", exact_id)[0] == 0
    assert _score("oa.pending-list.extra", exact_id)[0] == 0
    # A dot is a legal ID character, so this is not the exact shorter slug.
    assert _score("oa.pending-list.", exact_id)[0] == 0

    catalog = [exact_id, prefix_id, *(_spec(f"oa.filler-{index}") for index in range(4))]
    expected = select_capability_candidates(message, catalog)
    assert expected.outcome == "ready"
    assert _ids(expected)[:2] == ["oa.pending-list", "oa.pending"]
    for order in itertools.islice(itertools.permutations(catalog), 0, None, 97):
        again = select_capability_candidates(message, order)
        assert again.payload_json == expected.payload_json
        assert again.bindings == expected.bindings
        assert again.contracts == expected.contracts


def test_cutoff_ties_and_zero_scores_are_explicit() -> None:
    nine_tied = [_spec(f"aa.tied-{index}", name="alpha") for index in range(9)]
    ambiguous = select_capability_candidates("alpha", [*nine_tied, _spec("zz.zero")])
    one_then_eight = select_capability_candidates(
        "alpha zz.best",
        [_spec("zz.best", name="alpha"), *nine_tied[:8]],
    )
    eight_zero = select_capability_candidates("nothing", nine_tied[:8])
    nine_zero = select_capability_candidates("nothing", nine_tied)
    empty = select_capability_candidates("alpha", ())

    assert (ambiguous.outcome, ambiguous.selected_count, ambiguous.omitted_count) == (
        "ambiguous",
        0,
        10,
    )
    assert ambiguous.truncated_by == ("count",)
    assert ambiguous.coverage_complete is False
    assert _ids(one_then_eight) == ["zz.best"]
    assert one_then_eight.truncated_by == ("count",)
    assert eight_zero.outcome == "ready"
    assert eight_zero.selected_count == 8
    assert eight_zero.coverage_complete is True
    assert eight_zero.truncated_by == ()
    assert (nine_zero.outcome, nine_zero.omitted_count, nine_zero.truncated_by) == (
        "low_confidence",
        9,
        ("relevance",),
    )
    assert nine_zero.contracts == ()
    assert (empty.outcome, empty.visible_count, empty.coverage_complete) == ("empty", 0, True)


def test_byte_limits_never_drop_or_slice_contracts() -> None:
    at_limit = _sized("oa.top", MAX_CAPABILITY_CONTRACT_BYTES, name="alpha")
    over_limit = _sized("oa.top", MAX_CAPABILITY_CONTRACT_BYTES + 1, name="alpha")
    small_low = _spec("oa.low", name="beta")

    fits = select_capability_candidates("alpha", (at_limit, small_low))
    head_too_large = select_capability_candidates("alpha", (over_limit, small_low))
    low_too_large = select_capability_candidates(
        "beta", (_sized("oa.big", MAX_CAPABILITY_CONTRACT_BYTES + 1, name="alpha"), small_low)
    )
    zero_score_large = select_capability_candidates(
        "oa.target",
        (
            _spec("oa.target"),
            _sized("zz.big", MAX_CAPABILITY_CONTRACT_BYTES + 1, name="gamma"),
            *(_spec(f"zz.filler-{index}") for index in range(7)),
        ),
    )

    assert fits.outcome == "ready"
    assert _ids(fits) == ["oa.top", "oa.low"]
    assert (head_too_large.outcome, head_too_large.contracts) == ("over_budget", ())
    assert head_too_large.truncated_by == ("bytes",)
    assert _ids(low_too_large) == ["oa.low"]
    assert low_too_large.truncated_by == ("bytes",)
    assert _ids(zero_score_large) == ["oa.target"]
    assert zero_score_large.truncated_by == ("relevance",)

    names = ["w1 w2 w3 w4", "w1 w2 w3", "w1 w2", "w1"]
    overhead = len(
        canonical_json(
            {
                "capability_candidates": {
                    "protocol_version": CAPABILITY_CANDIDATE_PROTOCOL_VERSION,
                    "coverage_complete": True,
                    "omitted_count": 0,
                    "truncated_by": [],
                    "items": [],
                }
            }
        ).encode("utf-8")
    ) + (len(names) - 1)
    first_three = MAX_CAPABILITY_CONTRACT_BYTES - 16
    last = MAX_CAPABILITY_CANDIDATE_SEGMENT_BYTES - overhead - 3 * first_three
    segment_items = [
        _sized(f"oa.segment-{index}", first_three, name=name)
        for index, name in enumerate(names[:3])
    ]
    exact_segment = select_capability_candidates(
        "w1 w2 w3 w4", (*segment_items, _sized("oa.segment-3", last, name=names[3]))
    )
    over_segment = select_capability_candidates(
        "w1 w2 w3 w4", (*segment_items, _sized("oa.segment-3", last + 1, name=names[3]))
    )
    tied_tail = select_capability_candidates(
        "w1 w2 w3 w4",
        (
            *segment_items,
            _sized("oa.segment-3", last + 1, name=names[3]),
            _spec("oa.segment-4", name=names[3]),
        ),
    )

    assert exact_segment.outcome == "ready"
    assert exact_segment.payload_bytes == MAX_CAPABILITY_CANDIDATE_SEGMENT_BYTES
    assert len(exact_segment.payload_json.encode("utf-8")) == exact_segment.payload_bytes
    assert exact_segment.selected_count == 4
    assert over_segment.outcome == "ready"
    assert _ids(over_segment) == ["oa.segment-0", "oa.segment-1", "oa.segment-2"]
    assert over_segment.truncated_by == ("bytes",)
    assert over_segment.omitted_count == 1
    assert over_segment.payload_bytes <= MAX_CAPABILITY_CANDIDATE_SEGMENT_BYTES
    # The equal-score tail group is removed as a whole, never split.
    assert _ids(tied_tail) == ["oa.segment-0", "oa.segment-1", "oa.segment-2"]
    assert tied_tail.omitted_count == 2
    for selection in (fits, low_too_large, exact_segment, over_segment, tied_tail):
        for contract in selection.contracts:
            assert set(contract) >= _CONTRACT_KEYS
            assert contract["required_argument_keys"] == []
