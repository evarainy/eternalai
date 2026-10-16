"""Pure fail-closed projection validation; no DOM or provider imports."""

from app.browser_skill.models import (
    Coverage,
    FrameObservation,
    ObservationPolicy,
    ScopeBinding,
    ScopedSnapshot,
    VisibleProjection,
)


def scope_snapshot(
    projection: VisibleProjection,
    binding: ScopeBinding,
    policy: ObservationPolicy,
) -> ScopedSnapshot:
    if projection.binding != binding:
        raise ValueError("browser_binding_stale")
    if (projection.policy_id, projection.policy_digest) != (policy.policy_id, policy.digest):
        raise ValueError("browser_projection_policy_mismatch")
    paths: dict[tuple[tuple[str, int], ...], Coverage] = {}
    seen_frame_ids: set[str] = set()

    def walk(frame: FrameObservation, parent: tuple[tuple[str, int], ...]) -> None:
        path = (*parent, (frame.frame.frame_id, frame.frame.frame_epoch))
        if (
            len(path) > 32
            or path in paths
            or len(paths) >= 256
            or frame.frame.frame_id in seen_frame_ids
        ):
            raise ValueError("browser_frame_tree_invalid")
        seen_frame_ids.add(frame.frame.frame_id)
        paths[path] = frame.coverage
        for child in frame.children:
            walk(child, path)

    walk(projection.frames, ())
    path = tuple((hop.frame_id, hop.frame_epoch) for hop in projection.scope.frame_path)
    if path not in paths:
        raise ValueError("browser_frame_stale")
    if (
        any(paths[path[:index]].state == "unsupported" for index in range(1, len(path) + 1))
        and projection.coverage.state != "unsupported"
    ):
        raise ValueError("browser_coverage_mismatch")
    if projection.coverage.state == "unsupported" and projection.candidates:
        raise ValueError("browser_unsupported_candidates")
    if len(projection.candidates) > policy.maximum_candidates:
        raise ValueError("browser_projection_budget")
    seen = set()
    for candidate in projection.candidates:
        if candidate.ref.scope != projection.scope or candidate.ref.target_id in seen:
            raise ValueError("browser_candidate_scope_invalid")
        seen.add(candidate.ref.target_id)
        if (
            not candidate.visible
            or candidate.role not in policy.allowed_roles
            or (candidate.name is not None and candidate.name not in policy.allowed_names)
            or any(text not in policy.allowed_context for text in candidate.context)
            or (
                candidate.row_label is not None
                and candidate.row_label not in policy.allowed_row_labels
            )
            or (
                candidate.column_label is not None
                and candidate.column_label not in policy.allowed_column_labels
            )
        ):
            raise ValueError("browser_projection_disallowed")
    if projection.coverage.trusted_empty and projection.candidates:
        raise ValueError("browser_empty_evidence_conflict")
    if projection.coverage.trusted_empty and any(
        paths[path[:index]].state != "complete" for index in range(1, len(path) + 1)
    ):
        raise ValueError("browser_empty_frame_coverage_incomplete")
    return ScopedSnapshot(
        **{name: getattr(projection, name) for name in type(projection).model_fields}
    )
