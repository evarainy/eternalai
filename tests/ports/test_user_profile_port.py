"""Contract tests for the user profile read port."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import get_args, get_type_hints

import pytest
from pydantic import ValidationError

from app.ports.user_profile import (
    AvatarMediaType,
    OrgProfileStatus,
    UserAvatar,
    UserOrgProfile,
    UserProfilePort,
    UserProfileSnapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
USER_PROFILE_SOURCE = REPO_ROOT / "app" / "ports" / "user_profile.py"


def test_port_exposes_only_self_scoped_reads() -> None:
    methods = {
        name
        for name, value in vars(UserProfilePort).items()
        if not name.startswith("_") and callable(value)
    }

    assert methods == {"get_profile", "get_avatar"}
    for name in sorted(methods):
        signature = inspect.signature(getattr(UserProfilePort, name))
        # One server-derived key and nothing else: no listing, no paging and no
        # "read somebody else" is expressible through this contract.
        assert list(signature.parameters) == ["self", "ai_user_id"]
        assert get_type_hints(getattr(UserProfilePort, name))["ai_user_id"] is str


def test_port_module_does_not_depend_on_infrastructure() -> None:
    source = USER_PROFILE_SOURCE.read_text(encoding="utf-8")

    assert "app.infra" not in source
    assert "import urllib" not in source


def test_status_and_media_type_are_closed_sets() -> None:
    assert set(get_args(OrgProfileStatus)) == {
        "ok",
        "unbound",
        "expired",
        "unavailable",
        "unparsable",
    }
    assert set(get_args(AvatarMediaType)) == {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    }
    # SVG can carry script; serving it back under our own origin would be an
    # XSS delivery channel.
    assert "image/svg+xml" not in get_args(AvatarMediaType)


def test_snapshot_requires_org_exactly_when_status_is_ok() -> None:
    ok = UserProfileSnapshot(
        org_status="ok",
        org=UserOrgProfile(department_name="部门甲"),
    )

    assert ok.org is not None

    for failing_status in ("unbound", "expired", "unavailable", "unparsable"):
        with pytest.raises(ValidationError):
            UserProfileSnapshot(
                org_status=failing_status,  # type: ignore[arg-type]
                org=UserOrgProfile(department_name="部门甲"),
            )

    with pytest.raises(ValidationError):
        UserProfileSnapshot(org_status="ok")


def test_snapshot_defaults_to_no_org_and_no_avatar() -> None:
    snapshot = UserProfileSnapshot(org_status="unavailable")

    assert snapshot.org is None
    assert snapshot.avatar_available is False


def test_avatar_availability_is_independent_of_org_status() -> None:
    snapshot = UserProfileSnapshot(org_status="unparsable", avatar_available=True)

    assert snapshot.org is None
    assert snapshot.avatar_available is True


def test_org_profile_rejects_empty_or_oversized_labels() -> None:
    with pytest.raises(ValidationError):
        UserOrgProfile(department_name="")
    with pytest.raises(ValidationError):
        UserOrgProfile(department_name="部" * 65)
    with pytest.raises(ValidationError):
        UserOrgProfile(department_name="部门甲", department_id="abc")
    with pytest.raises(ValidationError):
        UserOrgProfile(department_name="部门甲", unit_id="1;2")


def test_org_profile_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        UserOrgProfile(department_name="部门甲", job_title="主任科员")


def test_snapshot_carries_no_upstream_url_or_raw_markup_field() -> None:
    hints = get_type_hints(UserProfileSnapshot)

    assert set(hints) == {"org_status", "org", "avatar_available"}
    org_hints = get_type_hints(UserOrgProfile)
    assert set(org_hints) == {
        "department_name",
        "department_id",
        "unit_name",
        "unit_id",
    }
    # No field can statically carry a URL or an HTML fragment onward.
    assert all(
        "url" not in name and "html" not in name and "orginfo" not in name
        for name in {*hints, *org_hints}
    )


def test_avatar_repr_never_shows_the_image_bytes() -> None:
    avatar = UserAvatar(media_type="image/png", content=b"\x89PNG-synthetic")

    assert "PNG-synthetic" not in repr(avatar)
    assert "PNG-synthetic" not in str(avatar)
    assert "image/png" in repr(avatar)
    assert avatar.content == b"\x89PNG-synthetic"


def test_avatar_media_type_is_validated_against_the_whitelist() -> None:
    with pytest.raises(ValidationError):
        UserAvatar(media_type="image/svg+xml", content=b"<svg/>")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        UserAvatar(media_type="text/html", content=b"<html>")  # type: ignore[arg-type]
