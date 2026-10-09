"""Installed manifest selection stays exact across Skills and Run versions."""

from collections.abc import Callable

import pytest

from app.browser_skill.publication_contracts import (
    BrowserPublicationManifest,
    publication_digest,
    rollback_version_manifest,
)
from app.browser_skill.registry import BrowserPublicationCatalog, PublicationCatalogError
from tests.infra.browser.test_local_read_execution import registered_site


def another_skill(source: BrowserPublicationManifest, skill_id: str) -> BrowserPublicationManifest:
    data = source.model_dump(mode="json")
    data["site"]["version"] = f"{skill_id}_site_v1"
    data["site"]["digest"] = publication_digest(
        data["site"]["schema_version"], data["site"],
    )
    data["verifier"]["version"] = f"{skill_id}_verifier_v1"
    data["verifier"]["digest"] = publication_digest(
        data["verifier"]["schema_version"], data["verifier"],
    )
    data["skill"]["skill_id"] = skill_id
    data["skill"]["site_digest"] = data["site"]["digest"]
    data["skill"]["verifier_digest"] = data["verifier"]["digest"]
    data["skill"]["digest"] = publication_digest("browser_skill.v1", data["skill"])
    data["digest"] = publication_digest("browser_publication.durable.v1", data)
    return BrowserPublicationManifest.model_validate(data)


def assert_code(code: str, action: Callable[[], object]) -> None:
    with pytest.raises(PublicationCatalogError) as error:
        action()
    assert error.value.code == code


def test_multiple_skills_and_pinned_old_version_select_their_own_manifest() -> None:
    first = registered_site().manifest
    second = another_skill(first, "check_messages")
    old_version = rollback_version_manifest(
        first, skill_version="query_v2", site_version="site_v2",
    )
    catalog = BrowserPublicationCatalog((first, second, old_version))

    assert catalog.digests() == (first.digest, second.digest, old_version.digest)
    assert catalog.resolve_exact(
        second.skill.skill_id, second.skill.version, second.digest,
    ) == second
    assert catalog.by_digest(first.digest) == first
    assert catalog.by_digest(old_version.digest) == old_version
    assert catalog.by_digest(first.digest).site.digest != old_version.site.digest
    assert catalog.by_digest(second.digest).skill.skill_id == "check_messages"
    assert catalog.by_digest(second.digest).site.digest != first.site.digest
    assert catalog.by_digest(second.digest).verifier.digest != first.verifier.digest

    for skill_id, version, digest in (
        (first.skill.skill_id, first.skill.version, second.digest),
        (second.skill.skill_id, second.skill.version, first.digest),
        (first.skill.skill_id, old_version.skill.version, first.digest),
    ):
        assert_code(
            "browser_publication_catalog_not_found",
            lambda: catalog.resolve_exact(skill_id, version, digest),
        )


def test_catalog_rejects_duplicate_versions_and_invalid_manifests() -> None:
    first = registered_site().manifest
    assert_code(
        "browser_publication_catalog_empty",
        lambda: BrowserPublicationCatalog(()),
    )
    with pytest.raises(PublicationCatalogError) as error:
        BrowserPublicationCatalog((first, first))
    assert error.value.code == "browser_publication_catalog_version_conflict"
    changed = another_skill(first, first.skill.skill_id)
    assert changed.digest != first.digest
    assert_code(
        "browser_publication_catalog_version_conflict",
        lambda: BrowserPublicationCatalog((first, changed)),
    )
    forged = first.model_copy(update={"digest": "0" * 64})
    assert_code(
        "browser_publication_catalog_manifest_invalid",
        lambda: BrowserPublicationCatalog((forged,)),
    )
    catalog = BrowserPublicationCatalog((first,))
    assert_code(
        "browser_publication_catalog_not_found",
        lambda: catalog.by_digest("0" * 64),
    )
    assert_code(
        "browser_publication_catalog_reference_invalid",
        lambda: catalog.by_digest(None),  # type: ignore[arg-type]
    )


def test_catalog_detaches_input_and_returned_models() -> None:
    first = registered_site().manifest
    skill_id = first.skill.skill_id
    catalog = BrowserPublicationCatalog((first,))
    returned = catalog.by_digest(first.digest)
    object.__setattr__(first.skill, "skill_id", "corrupted_input")
    object.__setattr__(returned.site, "version", "corrupted_output")
    assert catalog.by_digest(first.digest).skill.skill_id == skill_id
    assert catalog.by_digest(first.digest).site.version != "corrupted_output"
    assert catalog.resolve_exact(
        skill_id, returned.skill.version, first.digest,
    ).digest == first.digest


def test_another_registered_skill_needs_no_catalog_code_branch() -> None:
    first = registered_site().manifest
    sixth = another_skill(first, "sixth_read")
    catalog = BrowserPublicationCatalog((first, sixth))
    assert catalog.resolve_exact(
        sixth.skill.skill_id, sixth.skill.version, sixth.digest,
    ) == sixth
