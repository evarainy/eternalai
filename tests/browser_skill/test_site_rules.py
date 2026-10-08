import pytest
from pydantic import ValidationError

from app.browser_skill.models import ActionCommand, ParameterRef
from app.browser_skill.site_rules import FrozenSiteAdapter, RegisteredSitePlan, navigation_allowed
from tests.browser_skill.fakes import FakeWorld, make_plan, make_skill


def test_registered_site_preserves_order_and_immutable_dependencies() -> None:
    skill = make_skill(two_steps=True)
    plan = make_plan(skill)
    site = FrozenSiteAdapter((plan,))
    assert site.bootstrap(skill) == plan
    assert tuple(r.step for r in plan.steps) == skill.steps
    with pytest.raises(ValidationError):
        plan.skill_version = "other"
    with pytest.raises(ValueError, match="site_skill_registration_mismatch"):
        plan.validate_skill(skill.model_copy(update={"steps": tuple(reversed(skill.steps))}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "other"),
        ("site_digest", "b" * 64),
        ("verifier_digest", "b" * 64),
        ("verifier_id", "other"),
        ("parameters", ("key",)),
    ],
)
def test_registration_rejects_version_dependency_and_parameter_substitution(
    field: str, value: object
) -> None:
    skill = make_skill()
    with pytest.raises(ValueError, match="site_skill_registration_mismatch"):
        make_plan(skill).validate_skill(skill.model_copy(update={field: value}))


def test_step_value_is_skill_owned_and_site_rejects_operation_substitution() -> None:
    world = FakeWorld("fill")
    step = world.skill.steps[0].model_copy(update={"value_ref": ParameterRef(name="expected")})
    command = ActionCommand(
        skill_digest=world.skill.digest,
        step=step,
        binding=world.binding,
        target=world.view.candidates[0].ref,
    )
    assert world.site.permits(world.skill, command) is False


@pytest.mark.parametrize("effect", ["may_write", "unknown"])
def test_read_only_step_label_cannot_override_registered_effect(effect: str) -> None:
    world = FakeWorld()
    rule = world.plan.steps[0]
    altered = rule.model_copy(
        update={"effect": rule.effect.model_copy(update={"actual_effect": effect})}
    )
    site = FrozenSiteAdapter((world.plan.model_copy(update={"steps": (altered,)}),))
    command = ActionCommand(
        skill_digest=world.skill.digest,
        step=rule.step,
        binding=world.binding,
        target=world.view.candidates[0].ref,
    )
    assert site.permits(world.skill, command) is False


@pytest.mark.parametrize(
    "origin",
    [
        "https://fixture.invalid/path",
        "https://*.invalid",
        "https://fixture.invalid:443",
        "https://FIXTURE.invalid",
        "https://fixture.invalid%2eevil.invalid",
        "https://fixture.invalid#x",
    ],
)
def test_registration_requires_exact_canonical_origins(origin: str) -> None:
    plan = make_plan(make_skill())
    values = {name: getattr(plan, name) for name in RegisteredSitePlan.model_fields}
    values["navigation_origins"] = (origin,)
    with pytest.raises(ValueError, match="site_"):
        RegisteredSitePlan(**values)


@pytest.mark.parametrize(
    "url,allowed",
    [
        ("https://fixture.invalid/path?public=1", True),
        ("https://fixture.invalid.evil.invalid/path", False),
        ("https://fixture.invalid@evil.invalid/path", False),
        ("https://fixture.invalid\\@evil.invalid", False),
        ("javascript:alert(1)", False),
        ("//fixture.invalid/path", False),
        ("https://fixture.invalid:443/path", False),
    ],
)
def test_navigation_applies_exact_registered_origin(url: str, allowed: bool) -> None:
    assert navigation_allowed(url, ("https://fixture.invalid",)) is allowed


def test_site_registry_has_no_unregistered_fallback_or_duplicate_entry() -> None:
    skill = make_skill()
    plan = make_plan(skill)
    with pytest.raises(ValueError, match="site_registry_invalid"):
        FrozenSiteAdapter((plan, plan))
    with pytest.raises(ValueError, match="site_skill_unregistered"):
        FrozenSiteAdapter(()).bootstrap(skill)
