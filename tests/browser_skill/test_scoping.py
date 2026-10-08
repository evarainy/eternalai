from __future__ import annotations

import pytest

from app.browser_skill.models import Coverage, FrameObservation
from app.browser_skill.scoping import scope_snapshot
from tests.browser_skill.factories import binding, policy, projection


def test_row_and_column_labels_need_separate_exact_policy_allowlists() -> None:
    item = projection()
    candidate = item.candidates[0].model_copy(update={"row_label": "R1", "column_label": "Action"})
    tagged = item.model_copy(update={"candidates": (candidate,)})
    allowed = policy().model_copy(
        update={"allowed_row_labels": ("R1",), "allowed_column_labels": ("Action",)}
    )
    assert scope_snapshot(tagged, binding(), allowed).candidates[0].row_label == "R1"
    for restricted in (
        allowed.model_copy(update={"allowed_row_labels": ()}),
        allowed.model_copy(update={"allowed_column_labels": ()}),
    ):
        with pytest.raises(ValueError, match="projection_disallowed"):
            scope_snapshot(tagged, binding(), restricted)


def test_unsupported_child_cannot_be_an_empty_business_result() -> None:
    item = projection()
    main = item.frames.frame
    nested = item.frames.children[0].frame
    tree = FrameObservation(
        frame=main,
        coverage=Coverage(state="partial", reason="unobservable"),
        children=(
            FrameObservation(
                frame=nested, coverage=Coverage(state="unsupported", reason="cross_origin")
            ),
        ),
    )
    unsupported = item.model_copy(
        update={
            "frames": tree,
            "coverage": Coverage(state="unsupported", reason="cross_origin"),
            "candidates": (),
        }
    )
    snapshot = scope_snapshot(unsupported, binding(), policy())
    assert snapshot.coverage.state == "unsupported"
    assert snapshot.coverage.trusted_empty is False


@pytest.mark.parametrize("partial_level", ["ancestor", "selected"])
def test_trusted_empty_rejected_if_relevant_frame_coverage_is_partial(
    partial_level: str,
) -> None:
    item = projection()
    main = item.frames.frame
    nested = item.frames.children[0].frame
    tree = FrameObservation(
        frame=main,
        coverage=Coverage(
            state="partial" if partial_level == "ancestor" else "complete",
            reason="unobservable" if partial_level == "ancestor" else "complete",
        ),
        children=(
            FrameObservation(
                frame=nested,
                coverage=Coverage(
                    state="partial" if partial_level == "selected" else "complete",
                    reason="unobservable" if partial_level == "selected" else "complete",
                ),
            ),
        ),
    )
    claimed_empty = item.model_copy(
        update={
            "frames": tree,
            "coverage": Coverage(state="complete", reason="complete", trusted_empty=True),
            "candidates": (),
        }
    )
    with pytest.raises(ValueError, match="empty"):
        scope_snapshot(claimed_empty, binding(), policy())


def test_unrelated_sibling_does_not_erase_registered_region_empty_proof() -> None:
    item = projection()
    main = item.frames.frame
    nested = item.frames.children[0].frame
    sibling = main.model_copy(update={"frame_id": "other"})
    sibling_child = main.model_copy(update={"frame_id": "other-child", "frame_epoch": 7})
    tree = FrameObservation(
        frame=main,
        coverage=Coverage(state="complete", reason="complete"),
        children=(
            FrameObservation(frame=nested, coverage=Coverage(state="complete", reason="complete")),
            FrameObservation(
                frame=sibling,
                coverage=Coverage(state="unsupported", reason="cross_origin"),
                children=(
                    FrameObservation(
                        frame=sibling_child,
                        coverage=Coverage(state="complete", reason="complete"),
                    ),
                ),
            ),
        ),
    )
    local_empty = item.model_copy(
        update={
            "frames": tree,
            "coverage": Coverage(state="complete", reason="complete", trusted_empty=True),
            "candidates": (),
        }
    )
    assert scope_snapshot(local_empty, binding(), policy()).coverage.trusted_empty is True


@pytest.mark.parametrize("keep_candidate", [False, True])
def test_unsupported_ancestor_rejects_descendant_complete_claim(
    keep_candidate: bool,
) -> None:
    item = projection()
    tree = FrameObservation(
        frame=item.frames.frame,
        coverage=Coverage(state="unsupported", reason="unobservable"),
        children=(
            FrameObservation(
                frame=item.frames.children[0].frame,
                coverage=Coverage(state="complete", reason="complete"),
            ),
        ),
    )
    descendant = item.model_copy(
        update={
            "frames": tree,
            "coverage": Coverage(state="complete", reason="complete"),
            "candidates": item.candidates if keep_candidate else (),
        }
    )
    with pytest.raises(ValueError, match="coverage_mismatch"):
        scope_snapshot(descendant, binding(), policy())

    unsupported = descendant.model_copy(
        update={"coverage": Coverage(state="unsupported", reason="unobservable")}
    )
    if keep_candidate:
        with pytest.raises(ValueError, match="unsupported_candidates"):
            scope_snapshot(unsupported, binding(), policy())
    else:
        assert scope_snapshot(unsupported, binding(), policy()).candidates == ()


def test_unsupported_branch_keeps_frame_budget_and_unique_id_checks() -> None:
    item = projection()
    main = item.frames.frame
    complete = Coverage(state="complete", reason="complete")
    unsupported = Coverage(state="unsupported", reason="unobservable")
    leaves = tuple(
        FrameObservation(
            frame=main.model_copy(update={"frame_id": f"leaf-{index}"}),
            coverage=complete,
            children=(
                FrameObservation(
                    frame=main.model_copy(update={"frame_id": f"grandchild-{index}"}),
                    coverage=complete,
                ),
            ),
        )
        for index in range(128)
    )
    branch = FrameObservation(
        frame=main.model_copy(update={"frame_id": "off-path"}),
        coverage=unsupported,
        children=leaves,
    )
    oversized = item.model_copy(
        update={
            "frames": FrameObservation(
                frame=main,
                coverage=complete,
                children=(item.frames.children[0], branch),
            ),
        }
    )
    with pytest.raises(ValueError, match="frame_tree_invalid"):
        scope_snapshot(oversized, binding(), policy())

    duplicate = branch.model_copy(
        update={
            "children": (
                leaves[0],
                leaves[0].model_copy(
                    update={
                        "frame": leaves[0].frame.model_copy(
                            update={"frame_epoch": leaves[0].frame.frame_epoch + 1}
                        ),
                    }
                ),
            ),
        }
    )
    duplicated = oversized.model_copy(
        update={
            "frames": oversized.frames.model_copy(
                update={"children": (item.frames.children[0], duplicate)}
            )
        }
    )
    with pytest.raises(ValueError, match="frame_tree_invalid"):
        scope_snapshot(duplicated, binding(), policy())


def test_candidate_cannot_move_to_sibling_frame_or_ancestor_scope() -> None:
    item = projection()
    candidate = item.candidates[0]
    wrong_path = item.scope.model_copy(update={"frame_path": item.scope.frame_path[:1]})
    moved = candidate.model_copy(
        update={"ref": candidate.ref.model_copy(update={"scope": wrong_path})}
    )
    with pytest.raises(ValueError, match="candidate_scope_invalid"):
        scope_snapshot(item.model_copy(update={"candidates": (moved,)}), binding(), policy())


@pytest.mark.parametrize("relationship", ["ancestor", "cousin"])
def test_frame_ids_are_unique_across_the_whole_tree(relationship: str) -> None:
    item = projection()
    complete = Coverage(state="complete", reason="complete")
    if relationship == "ancestor":
        descendant = FrameObservation(
            frame=item.frames.frame.model_copy(update={"frame_epoch": 9}),
            coverage=complete,
        )
        children = (item.frames.children[0].model_copy(update={"children": (descendant,)}),)
    else:
        leaf = FrameObservation(
            frame=item.frames.frame.model_copy(update={"frame_id": "shared-leaf"}),
            coverage=complete,
        )
        children = (
            item.frames.children[0].model_copy(update={"children": (leaf,)}),
            FrameObservation(
                frame=item.frames.frame.model_copy(update={"frame_id": "other-branch"}),
                coverage=Coverage(state="unsupported", reason="unobservable"),
                children=(
                    leaf.model_copy(
                        update={"frame": leaf.frame.model_copy(update={"frame_epoch": 9})}
                    ),
                ),
            ),
        )
    tree = item.frames.model_copy(update={"children": children})
    with pytest.raises(ValueError, match="browser_frame_tree_invalid"):
        scope_snapshot(item.model_copy(update={"frames": tree}), binding(), policy())


def test_distinct_frame_ids_preserve_selected_scope_and_unrelated_tree() -> None:
    item = projection()
    complete = Coverage(state="complete", reason="complete")
    first_leaf = FrameObservation(
        frame=item.frames.frame.model_copy(update={"frame_id": "first-leaf"}),
        coverage=complete,
    )
    tree = item.frames.model_copy(
        update={
            "children": (
                item.frames.children[0].model_copy(update={"children": (first_leaf,)}),
                FrameObservation(
                    frame=item.frames.frame.model_copy(update={"frame_id": "other-branch"}),
                    coverage=Coverage(state="unsupported", reason="unobservable"),
                    children=(
                        first_leaf.model_copy(
                            update={
                                "frame": first_leaf.frame.model_copy(
                                    update={"frame_id": "second-leaf"}
                                )
                            }
                        ),
                    ),
                ),
            ),
        }
    )
    snapshot = scope_snapshot(item.model_copy(update={"frames": tree}), binding(), policy())
    assert snapshot.scope == item.scope
    assert snapshot.candidates == item.candidates
    assert snapshot.frames == tree
