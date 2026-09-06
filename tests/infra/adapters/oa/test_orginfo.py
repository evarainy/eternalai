"""Bounded, fail-closed parsing of the OA ``orginfo`` HTML fragment.

Every value in this file is synthetic. No real name, department or identifier
from OA appears here (AGENTS.md rule 4 and the 2026-09-02 personnel-information
boundary).
"""

from __future__ import annotations

import pytest

from app.infra.adapters.oa.orginfo import (
    MAX_ANCHOR_COUNT,
    MAX_LABEL_LENGTH,
    MAX_ORGINFO_LENGTH,
    normalize_label,
    parse_orginfo,
)

UNIT_ANCHOR = (
    '<a href="#" onclick="javascript:viewSubCompany(11)">单位甲</a>'
)
DEPARTMENT_ANCHOR = (
    '<a href="#" onclick="javascript:viewDepartment(22)">部门乙</a>'
)
FRAGMENT = f"{UNIT_ANCHOR}&nbsp;&gt;&nbsp;{DEPARTMENT_ANCHOR}"


def test_parses_unit_and_department_from_a_well_formed_fragment() -> None:
    parsed = parse_orginfo(FRAGMENT)

    assert parsed is not None
    assert parsed.unit_name == "单位甲"
    assert parsed.unit_id == "11"
    assert parsed.department_name == "部门乙"
    assert parsed.department_id == "22"


@pytest.mark.parametrize(
    "fragment",
    [
        # attribute order swapped
        "<a onclick=\"javascript:viewDepartment(22)\" href=\"#\">部门乙</a>",
        # single quotes around the attribute value
        "<a onclick='javascript:viewDepartment(22)'>部门乙</a>",
        # padding inside and around the call
        '<a onclick="  javascript:viewDepartment( 22 ) ; ">部门乙</a>',
        # uppercase tag name
        '<A ONCLICK="javascript:viewDepartment(22)">部门乙</A>',
        # surrounding whitespace in the label
        '<a onclick="javascript:viewDepartment(22)">\n  部门乙\t </a>',
    ],
)
def test_accepts_equivalent_markup_spellings(fragment: str) -> None:
    parsed = parse_orginfo(fragment)

    assert parsed is not None
    assert parsed.department_name == "部门乙"
    assert parsed.department_id == "22"
    assert parsed.unit_name is None
    assert parsed.unit_id is None


def test_decodes_character_references_in_the_label() -> None:
    parsed = parse_orginfo(
        '<a onclick="javascript:viewDepartment(22)">甲&amp;乙</a>'
    )

    assert parsed is not None
    assert parsed.department_name == "甲&乙"


def test_folds_runs_of_whitespace_including_the_full_width_space() -> None:
    parsed = parse_orginfo(
        '<a onclick="javascript:viewDepartment(22)">甲　  乙</a>'
    )

    assert parsed is not None
    assert parsed.department_name == "甲 乙"


def test_nested_element_inside_an_anchor_fails_instead_of_guessing() -> None:
    fragment = (
        '<a onclick="javascript:viewDepartment(22)">部门<span>乙</span></a>'
    )

    assert parse_orginfo(fragment) is None


@pytest.mark.parametrize(
    "fragment",
    [
        UNIT_ANCHOR,
        f"{DEPARTMENT_ANCHOR}{DEPARTMENT_ANCHOR}",
        (
            f"{DEPARTMENT_ANCHOR}"
            '<a onclick="javascript:viewDepartment(33)">部门丙</a>'
        ),
    ],
)
def test_department_anchor_must_be_exactly_one(fragment: str) -> None:
    # Zero means nothing to show; two means a multi-department shape we have no
    # evidence for. Picking "the first one" could file a person under the wrong
    # office, so both fail closed.
    assert parse_orginfo(fragment) is None


@pytest.mark.parametrize(
    "units",
    [
        "",
        f"{UNIT_ANCHOR}{UNIT_ANCHOR}",
    ],
)
def test_unit_anchor_is_best_effort_and_never_fails_the_parse(units: str) -> None:
    parsed = parse_orginfo(f"{units}{DEPARTMENT_ANCHOR}")

    assert parsed is not None
    assert parsed.department_name == "部门乙"
    assert parsed.unit_name is None
    assert parsed.unit_id is None


@pytest.mark.parametrize(
    "onclick",
    [
        "javascript:viewDepartment(22);alert(1)",
        "javascript:viewDepartmentX(22)",
        "javascript:viewDepartment('22')",
        "javascript:viewDepartment(1e3)",
        "javascript:viewDepartment(22, 33)",
        "viewDepartment(22)",
        "javascript:viewDepartment(-22)",
        "javascript:viewDepartment(9999999999999999999)",
    ],
)
def test_non_anchored_onclick_values_are_ignored(onclick: str) -> None:
    fragment = f'<a onclick="{onclick}">部门乙</a>'

    # The anchor is ignored rather than accepted, so no department candidate is
    # produced and the whole parse fails closed.
    assert parse_orginfo(fragment) is None


def test_unrelated_links_do_not_break_a_valid_fragment() -> None:
    fragment = f'<a href="/other">其他</a>{DEPARTMENT_ANCHOR}'

    parsed = parse_orginfo(fragment)

    assert parsed is not None
    assert parsed.department_name == "部门乙"


def test_duplicate_onclick_attributes_fail_closed() -> None:
    fragment = (
        '<a onclick="javascript:viewDepartment(22)" '
        'onclick="javascript:viewDepartment(33)">部门乙</a>'
    )

    assert parse_orginfo(fragment) is None


def test_oversized_input_is_rejected_before_parsing() -> None:
    padding = "x" * (MAX_ORGINFO_LENGTH + 1)

    assert parse_orginfo(padding) is None
    assert parse_orginfo(f"{DEPARTMENT_ANCHOR}{padding}") is None


def test_too_many_anchors_fail_closed() -> None:
    filler = '<a href="#">链接</a>' * (MAX_ANCHOR_COUNT + 1)

    assert parse_orginfo(f"{filler}{DEPARTMENT_ANCHOR}") is None


def test_oversized_label_fails_closed() -> None:
    label = "甲" * (MAX_LABEL_LENGTH + 1)
    fragment = f'<a onclick="javascript:viewDepartment(22)">{label}</a>'

    assert parse_orginfo(fragment) is None


def test_control_characters_in_the_label_fail_closed() -> None:
    fragment = '<a onclick="javascript:viewDepartment(22)">部门\x07乙</a>'

    assert parse_orginfo(fragment) is None


def test_escaped_markup_in_the_label_never_reaches_the_output() -> None:
    fragment = (
        '<a onclick="javascript:viewDepartment(22)">'
        "&lt;script&gt;alert(1)&lt;/script&gt;</a>"
    )

    # After character-reference decoding the label still carries angle
    # brackets, so we refuse it rather than forward markup-looking text.
    assert parse_orginfo(fragment) is None
    assert normalize_label("<script>") is None


def test_empty_label_fails_closed() -> None:
    assert parse_orginfo('<a onclick="javascript:viewDepartment(22)"></a>') is None
    assert (
        parse_orginfo('<a onclick="javascript:viewDepartment(22)">   </a>') is None
    )


def test_unclosed_anchor_is_dropped() -> None:
    assert parse_orginfo('<a onclick="javascript:viewDepartment(22)">部门乙') is None


@pytest.mark.parametrize(
    "value",
    [None, 22, 3.5, b"<a></a>", {"orginfo": DEPARTMENT_ANCHOR}, [DEPARTMENT_ANCHOR], ""],
)
def test_non_string_inputs_fail_closed_without_raising(value: object) -> None:
    assert parse_orginfo(value) is None


def test_broken_markup_does_not_raise() -> None:
    for fragment in ("<a", "<<a>>", "<a onclick=>x</a>", "</a>", "<!-- -->"):
        assert parse_orginfo(fragment) is None
