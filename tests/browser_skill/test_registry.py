from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from pydantic import ValidationError

from app.browser_skill.models import BrowserOwner
from app.browser_skill.registry import (
    InMemoryPublicationAuthority,
    Publication,
    PublicationError,
    canonical_digest,
)
from app.ports.browser import BrowserSkillStorePort

MANIFESTS = Path(__file__).resolve().parents[2] / "skills/browser/ecology9"
SKILLS = ("login_assist", "query_todos", "check_messages", "open_todo", "search_contact")


def owner(**changes: str) -> BrowserOwner:
    return BrowserOwner(
        **{"tenant_id": "tenant", "user_id": "user", "session_id": "session", **changes}
    )


def publication(name: str = "query_todos") -> Publication:
    return Publication.model_validate_json((MANIFESTS / f"{name}.json").read_bytes())


def reseal(data: dict[str, Any]) -> Publication:
    for key, domain in (("site", "browser_dependency.v1"), ("verifier", "browser_dependency.v1")):
        data[key]["digest"] = canonical_digest(domain, data[key])
    skill = data["skill"]
    skill["site_digest"] = data["site"]["digest"]
    skill["verifier_digest"] = data["verifier"]["digest"]
    skill["digest"] = canonical_digest("browser_skill.v1", skill)
    descriptor = data["descriptor"]
    descriptor["skill_digest"] = skill["digest"]
    descriptor["skill_version"] = skill["version"]
    descriptor["version"] = data["version"]
    descriptor["digest"] = canonical_digest("browser_descriptor.v1", descriptor)
    data["dependency_digest"] = canonical_digest(
        "browser_dependencies.v1", {key: data[key] for key in ("site", "verifier", "descriptor")}
    )
    data["digest"] = canonical_digest("browser_publication.v1", data)
    return Publication.model_validate_json(json.dumps(data))


def next_version(first: Publication) -> Publication:
    data = first.model_dump(mode="json")
    data["version"] = "synthetic_v2"
    data["skill"]["version"] = data["version"]
    data["site"]["version"] = "v2"
    return reseal(data)


def authority() -> InMemoryPublicationAuthority:
    return InMemoryPublicationAuthority(lambda who, skill, operation: who == owner())


def activate(store: InMemoryPublicationAuthority, pub: Publication) -> None:
    result = store.prepare(owner(), pub, expected_revision=0)
    assert result.revision == 1
    assert result.publication_digest is None
    result = store.activate(owner(), pub.skill.skill_id, pub.digest, expected_revision=1)
    assert result.revision == 2
    assert result.publication_digest == pub.digest


@pytest.mark.parametrize("name", SKILLS)
def test_manifests_are_explicit_synthetic_contracts_with_bound_dependencies(name: str) -> None:
    pub = publication(name)
    assert pub.skill.skill_id == name
    assert pub.site.origin == "https://ecology9.invalid"
    assert pub.site.source_kind == "synthetic_fixture"
    assert pub.descriptor.execution_state == "synthetic_contract_only"
    assert pub.descriptor.http_api_preferred is True
    assert pub.descriptor.target_system == "oa"
    assert "registered_cloud_source" in pub.descriptor.deferred_capabilities
    assert all(step.locator.value.startswith("synthetic_") for step in pub.skill.steps)
    assert all(step.effect == "may_write" for step in pub.skill.steps if step.operation != "read")
    if name == "login_assist":
        assert pub.descriptor.visibility == "internal"
        assert {"real_login", "mfa", "credential_binding"} <= set(
            pub.descriptor.deferred_capabilities
        )
        assert pub.skill.parameters == ()
        assert [step.operation for step in pub.skill.steps] == ["read"]
    else:
        assert pub.descriptor.visibility == "public"


def test_exact_five_manifest_catalog() -> None:
    assert {path.stem for path in MANIFESTS.glob("*.json")} == set(SKILLS)


def test_draft_never_implicitly_activates_and_exact_views_share_publication() -> None:
    pub, store = publication(), authority()
    store.prepare(owner(), pub, expected_revision=0)
    view: BrowserSkillStorePort = store.view(owner())
    assert (
        asyncio.run(view.get_published(pub.skill.skill_id, pub.version, pub.skill.digest)) is None
    )
    assert store.read_active(owner(), pub.skill.skill_id) is None
    store.activate(owner(), pub.skill.skill_id, pub.digest, expected_revision=1)
    assert (
        asyncio.run(view.get_published(pub.skill.skill_id, pub.version, pub.skill.digest))
        == pub.skill
    )
    assert (
        asyncio.run(
            store.view(owner()).get_descriptor(
                pub.skill.skill_id, pub.version, pub.descriptor.digest
            )
        )
        == pub.descriptor
    )
    for skill_id, version, digest in (
        ("unknown", pub.version, pub.skill.digest),
        (pub.skill.skill_id, "wrong", pub.skill.digest),
        (pub.skill.skill_id, pub.version, "0" * 64),
    ):
        assert asyncio.run(view.get_published(skill_id, version, digest)) is None
    assert (
        asyncio.run(
            store.view(owner()).get_descriptor(pub.skill.skill_id, pub.version, pub.skill.digest)
        )
        is None
    )


def test_activation_and_rollback_only_change_new_requests_old_run_is_frozen() -> None:
    first, store = publication(), authority()
    activate(store, first)
    held = store.read_active(owner(), first.skill.skill_id)
    second = next_version(first)
    store.prepare(owner(), second, expected_revision=2)
    with pytest.raises(PublicationError, match="not_previously_active"):
        store.rollback(owner(), first.skill.skill_id, second.digest, expected_revision=3)
    store.activate(owner(), second.skill.skill_id, second.digest, expected_revision=3)
    view = store.view(owner())
    assert held == first
    assert (
        asyncio.run(view.get_published(first.skill.skill_id, first.version, first.skill.digest))
        is None
    )
    assert asyncio.run(
        view.get_published(second.skill.skill_id, second.version, second.skill.digest)
    ) == (second.skill)
    result = store.rollback(owner(), first.skill.skill_id, first.digest, expected_revision=4)
    assert result.revision == 5
    assert store.read_active(owner(), first.skill.skill_id) == first
    assert held == first
    assert (
        asyncio.run(view.get_published(second.skill.skill_id, second.version, second.skill.digest))
        is None
    )


@pytest.mark.parametrize("operation", ["prepare", "activate", "rollback", "read", "descriptor"])
def test_revoked_authorization_denies_every_operation(operation: str) -> None:
    allowed = True
    calls: list[tuple[BrowserOwner, str, str]] = []

    def authorize(who: BrowserOwner, skill: str, op: str) -> bool:
        calls.append((who, skill, op))
        return allowed

    store, pub = InMemoryPublicationAuthority(authorize), publication()
    activate(store, pub)
    allowed = False
    with pytest.raises(PublicationError, match="^publication_denied$"):
        if operation == "prepare":
            store.prepare(owner(), next_version(pub), expected_revision=2)
        elif operation == "activate":
            store.activate(owner(), pub.skill.skill_id, pub.digest, expected_revision=2)
        elif operation == "rollback":
            store.rollback(owner(), pub.skill.skill_id, pub.digest, expected_revision=2)
        elif operation == "descriptor":
            asyncio.run(
                store.view(owner()).get_descriptor(
                    pub.skill.skill_id, pub.version, pub.descriptor.digest
                )
            )
        else:
            store.read_active(owner(), pub.skill.skill_id)
    assert calls[-1] == (
        owner(),
        pub.skill.skill_id,
        "read" if operation == "descriptor" else operation,
    )


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "session_id"])
def test_owner_dimensions_partition_both_read_views_and_mutations(field: str) -> None:
    store = InMemoryPublicationAuthority(lambda who, skill, op: True)
    pub = publication()
    activate(store, pub)
    other = owner(**{field: "other"})
    assert store.read_active(other, pub.skill.skill_id) is None
    assert (
        asyncio.run(
            store.view(other).get_published(pub.skill.skill_id, pub.version, pub.skill.digest)
        )
        is None
    )
    assert (
        asyncio.run(
            store.view(other).get_descriptor(pub.skill.skill_id, pub.version, pub.descriptor.digest)
        )
        is None
    )
    with pytest.raises(PublicationError, match="not_prepared"):
        store.activate(other, pub.skill.skill_id, pub.digest, expected_revision=0)
    assert store.read_active(owner(), pub.skill.skill_id) == pub


@pytest.mark.parametrize("revision", [-1, True, "2", 0, 1])
def test_cas_rejects_invalid_or_stale_revision_without_changing_head(revision: Any) -> None:
    store, pub = authority(), publication()
    activate(store, pub)
    with pytest.raises(PublicationError, match="publication_revision_"):
        store.activate(owner(), pub.skill.skill_id, pub.digest, expected_revision=revision)
    assert store.read_active(owner(), pub.skill.skill_id) == pub


def test_competing_activations_have_exactly_one_cas_winner() -> None:
    store, first = authority(), publication()
    activate(store, first)
    second = next_version(first)
    store.prepare(owner(), second, expected_revision=2)
    barrier = Barrier(2)

    def switch(digest: str) -> str:
        barrier.wait()
        try:
            return (
                store.activate(
                    owner(), first.skill.skill_id, digest, expected_revision=3
                ).publication_digest
                or "missing"
            )
        except PublicationError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(switch, (first.digest, second.digest)))
    assert results.count("publication_revision_conflict") == 1
    held = store.read_active(owner(), first.skill.skill_id)
    assert held is not None
    assert held.digest in results


@pytest.mark.parametrize("part", ["skill", "site", "verifier", "descriptor", "publication"])
def test_model_copy_cannot_bypass_digest_checks(part: str) -> None:
    pub = publication()
    if part == "publication":
        corrupted = pub.model_copy(update={"digest": "0" * 64})
    else:
        nested = getattr(pub, part).model_copy(update={"version": "wrong"})
        corrupted = pub.model_copy(update={part: nested})
    store = authority()
    with pytest.raises(ValidationError):
        store.prepare(owner(), corrupted, expected_revision=0)
    assert store.read_active(owner(), pub.skill.skill_id) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "unknown"),
        ("dependency_digest", "0" * 64),
        ("version", "wrong"),
    ],
)
def test_recomputed_outer_digest_does_not_hide_contract_mismatch(field: str, value: str) -> None:
    data = publication().model_dump(mode="json")
    data[field] = value
    data["digest"] = canonical_digest("browser_publication.v1", data)
    with pytest.raises(ValidationError):
        Publication.model_validate_json(json.dumps(data))


def test_wrong_dependency_kind_and_unknown_manifest_fail_closed() -> None:
    data = publication().model_dump(mode="json")
    data["site"], data["verifier"] = data["verifier"], data["site"]
    with pytest.raises(ValidationError, match="dependency_kind_mismatch"):
        reseal(data)
    data = publication().model_dump(mode="json")
    data["site"]["contract"] = "unknown"
    with pytest.raises(ValidationError):
        reseal(data)


def test_version_content_is_immutable_and_caller_models_are_not_storage() -> None:
    store, pub = authority(), publication()
    activate(store, pub)
    data = pub.model_dump(mode="json")
    data["site"]["version"] = "another_site"
    changed = reseal(data)
    with pytest.raises(PublicationError, match="publication_version_conflict"):
        store.prepare(owner(), changed, expected_revision=2)
    returned = store.read_active(owner(), pub.skill.skill_id)
    assert returned is not None
    # Even deliberately bypassing Pydantic's frozen guard cannot mutate storage.
    object.__setattr__(returned.skill, "version", "bypassed")
    object.__setattr__(pub.skill, "version", "bypassed_input")
    current = store.read_active(owner(), "query_todos")
    assert current is not None
    assert current.skill.version == "synthetic_v1"


def test_authorizer_is_mandatory_and_truthy_non_boolean_does_not_grant() -> None:
    with pytest.raises(TypeError, match="authorizer_required"):
        InMemoryPublicationAuthority(None)  # type: ignore[arg-type]
    store = InMemoryPublicationAuthority(lambda who, skill, op: 1)  # type: ignore[arg-type,return-value]
    with pytest.raises(PublicationError, match="publication_denied"):
        store.prepare(owner(), publication(), expected_revision=0)


def test_canonical_digest_has_no_self_cycle_and_covers_nested_digest_and_domain() -> None:
    data: dict[str, object] = {"version": "v1", "digest": "0" * 64, "child": {"digest": "a" * 64}}
    first = canonical_digest("domain.v1", data)
    data["digest"] = first
    assert canonical_digest("domain.v1", data) == first
    assert canonical_digest("domain.v2", data) != first
    data["child"] = {"digest": "b" * 64}
    assert canonical_digest("domain.v1", data) != first
