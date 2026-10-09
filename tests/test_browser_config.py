from collections.abc import Iterator, Mapping

import pytest

from app.config import BrowserSettings, ProductionSettings
from tests.test_config import _environment


class SwitchOnly(Mapping[str, str]):
    def __getitem__(self, key: str) -> str:
        if key == "BROWSER_ENABLED":
            return "false"
        raise AssertionError("disabled configuration read a provider setting")

    def __iter__(self) -> Iterator[str]:
        return iter(("BROWSER_ENABLED",))

    def __len__(self) -> int:
        return 1


def test_disabled_browser_does_not_parse_provider_configuration() -> None:
    assert BrowserSettings.from_env({}) == BrowserSettings()
    assert BrowserSettings.from_env(SwitchOnly()) == BrowserSettings()
    settings = ProductionSettings.from_environment(
        {**_environment(), "BROWSER_TRANSPORT": "invalid"}
    )
    assert settings.browser.enabled is False
    assert settings.llm_model == "glm-4.7"


def test_enabled_browser_has_explicit_transport_and_model_pin() -> None:
    env = {
        "BROWSER_ENABLED": "true",
        "BROWSER_TRANSPORT": "cdp",
        "BROWSER_DECISION_ORIGIN": "https://decision.invalid",
        "BROWSER_DECISION_REQUEST_MODEL": "alias",
        "BROWSER_DECISION_DEPLOYMENT_MODEL": "pinned-v1",
    }
    settings = BrowserSettings.from_env(env)
    assert settings.enabled and settings.transport == "cdp"
    for field, value in (
        ("BROWSER_TRANSPORT", "auto"),
        ("BROWSER_DECISION_DEPLOYMENT_MODEL", "model-latest"),
        ("BROWSER_DECISION_ORIGIN", "https://decision.invalid/path"),
    ):
        with pytest.raises(RuntimeError):
            BrowserSettings.from_env({**env, field: value})


def test_browser_config_allows_absent_model_triple_but_rejects_partial() -> None:
    env = {
        "BROWSER_ENABLED": "true",
        "BROWSER_TRANSPORT": "playwright",
    }
    settings = BrowserSettings.from_env(env)
    assert (settings.decision_origin, settings.request_model,
            settings.deployment_model) == (None, None, None)
    assert ProductionSettings.from_environment({**_environment(), **env}).browser == settings
    for name in (
        "BROWSER_DECISION_ORIGIN", "BROWSER_DECISION_REQUEST_MODEL",
        "BROWSER_DECISION_DEPLOYMENT_MODEL",
    ):
        with pytest.raises(RuntimeError, match="configuration must be complete"):
            BrowserSettings.from_env({**env, name: "placeholder"})
    # Missing configuration is not a grant: signed Site budget and install
    # authority are checked separately by production composition.
