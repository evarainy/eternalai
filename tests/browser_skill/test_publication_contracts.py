"""Immutable registered Site version and rollback provenance contracts."""

import pytest

from app.browser_skill.publication_contracts import (
    BrowserPublicationManifest,
    publication_digest,
    rollback_version_manifest,
)
from app.browser_skill.site_rules import (
    RegisteredExploration,
    RegisteredSitePlan,
    RegisteredTraversal,
)
from app.infra.persistence.browser.publications import _rollback_compatible
from app.infra.persistence.browser.runs import BrowserRunStoreError, PostgreSQLBrowserRunStore
from tests.infra.browser.test_local_read_execution import registered_site


def test_legacy_manifest_digest_remains_stable_without_rollback_provenance() -> None:
    manifest = registered_site().manifest
    payload = manifest.model_dump(mode="json")
    assert payload["rollback_source_digest"] is None
    assert publication_digest(manifest.schema_version, payload) == manifest.digest


def test_rollback_creates_new_version_with_exact_old_content() -> None:
    source = registered_site().manifest
    candidate = rollback_version_manifest(
        source, skill_version="rollback_skill_v2", site_version="rollback_site_v2",
    )
    assert candidate.digest != source.digest
    assert candidate.skill.version != source.skill.version
    assert candidate.site.version != source.site.version
    assert candidate.rollback_source_digest == source.digest
    assert _rollback_compatible(source, candidate)
    assert BrowserPublicationManifest.model_validate_json(
        candidate.model_dump_json()
    ) == candidate


def test_rollback_cannot_change_reviewed_content() -> None:
    source = registered_site().manifest
    candidate = rollback_version_manifest(
        source, skill_version="rollback_skill_v2", site_version="rollback_site_v2",
    )
    changed = candidate.model_copy(update={"output": source.output.model_copy(
        update={"maximum_bytes": source.output.maximum_bytes - 1},
    )})
    assert not _rollback_compatible(source, changed)
    with pytest.raises(ValueError, match="rollback_version_invalid"):
        rollback_version_manifest(
            source, skill_version=source.skill.version, site_version="rollback_site_v2",
        )


def test_exploration_route_is_bounded_to_a_validated_read_catalog() -> None:
    source = registered_site().manifest
    exploration = RegisteredExploration(
        allowed_regions=(source.site.steps[0].observation.region_id,),
        max_steps=2, max_seconds=30,
    )
    values = source.site_plan().model_dump(mode="python")
    values.update(
        primitive_rules=[rule.model_dump(mode="python") for rule in source.site.steps],
        exploration=exploration.model_dump(mode="python"),
    )
    plan = RegisteredSitePlan.model_validate(values)
    assert plan.exploration == exploration
    frozen = source.model_copy(update={
        "site": source.site.model_copy(update={
            "primitive_rules": source.site.steps,
            "exploration": exploration,
        }),
    })
    check = PostgreSQLBrowserRunStore._check_step_route
    step_id = source.skill.steps[0].step_id
    check(frozen, (step_id, step_id), complete=True)
    for route in ((), ("unregistered",), (step_id, step_id, step_id)):
        with pytest.raises(BrowserRunStoreError) as error:
            check(frozen, route, complete=True)
        assert error.value.code == "browser_step_audit_sequence_conflict"


def test_virtual_scroll_requires_a_frozen_read_target_and_bounded_traversal() -> None:
    source = registered_site().manifest
    values = source.site_plan().model_dump(mode="python")
    values["traversal"] = RegisteredTraversal(
        region_id=source.site.steps[0].observation.region_id,
        object_type=source.site.read_rule.object_type,
        mode="virtualized", advance_mode="scroll",
        advance_step_id=source.skill.steps[0].step_id,
        max_pages=3, max_rows=10,
        page_key_rule_digest="1" * 64,
        row_key_rule_digest="2" * 64,
        ordinal_rule_digest="3" * 64,
        total_rule_digest="4" * 64,
        terminal_rule_digest="5" * 64,
    ).model_dump(mode="python")
    assert RegisteredSitePlan.model_validate(values).traversal.advance_mode == "scroll"
    values["traversal"]["mode"] = "paged"
    with pytest.raises(ValueError, match="site_traversal_scroll_mode_invalid"):
        RegisteredSitePlan.model_validate(values)
