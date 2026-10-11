"""Fixed MCP URL configuration accepts HTTP(S) without weakening origin checks."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from app.mcp.models import ServiceConfig

URL_PATHS = {
    "endpoint": "/mcp",
    "issuer": "/issuer",
    "resource": "/resource",
    "registration_endpoint": "/register",
    "authorization_endpoint": "/authorize",
    "token_endpoint": "/token",
    "callback_uri": "/callback",
}
OAUTH_FIELDS = ("registration_endpoint", "authorization_endpoint", "token_endpoint")


def configuration() -> dict[str, Any]:
    return {
        "service_config_id": "synthetic-service",
        "service_config_version": 1,
        "deployment_id": "synthetic-deployment",
        "tenant_id": "synthetic-tenant",
        "display_name": "Synthetic service",
        **{name: "https://provider.invalid" + path for name, path in URL_PATHS.items()},
        "endpoint": "https://endpoint.invalid/mcp",
        "resource": "https://resource.invalid/resource",
        "callback_uri": "https://client.invalid/callback",
        "callback_config_version": 1,
    }


def set_url(config: dict[str, Any], field: str, value: str) -> None:
    if field in ("issuer", *OAUTH_FIELDS):
        parsed = urlsplit(value)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        for name in ("issuer", *OAUTH_FIELDS):
            config[name] = origin + URL_PATHS[name]
    config[field] = value


@pytest.mark.parametrize("field", URL_PATHS)
@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize(
    "host",
    ["provider.invalid", "192.0.2.20", "[2001:db8::20]", "localhost", "127.0.0.1", "[::1]"],
    ids=["domain", "ipv4", "ipv6", "localhost", "loopback-v4", "loopback-v6"],
)
def test_http_and_https_accept_all_host_types(field: str, scheme: str, host: str) -> None:
    config = configuration()
    if field == "callback_uri":
        set_url(config, "issuer", "https://issuer.invalid/issuer")
    set_url(config, field, f"{scheme}://{host}{URL_PATHS[field]}")

    validated = ServiceConfig.model_validate(config)

    assert {name: getattr(validated, name) for name in URL_PATHS} == {
        name: config[name] for name in URL_PATHS
    }


@pytest.mark.parametrize("field", URL_PATHS)
@pytest.mark.parametrize(
    "value",
    [
        pytest.param("ftp://provider.invalid/path", id="ftp"),
        pytest.param("file://provider.invalid/path", id="file"),
        pytest.param("wss://provider.invalid/path", id="wss"),
        pytest.param("https://synthetic-user@provider.invalid/path", id="username"),
        pytest.param("https://:synthetic-value@provider.invalid/path", id="password"),
        pytest.param("https://provider.invalid/path?synthetic=1", id="query"),
        pytest.param("https://provider.invalid/path#synthetic", id="fragment"),
        pytest.param("https:///path", id="missing-host"),
        *[
            pytest.param(f"https://provider.invalid/pa{chr(code)}th", id=f"control-{code:02d}")
            for code in range(33)
        ],
    ],
)
def test_non_fixed_urls_are_rejected(field: str, value: str) -> None:
    config = configuration()
    set_url(config, field, value)

    with pytest.raises(ValidationError) as caught:
        ServiceConfig.model_validate(config)

    errors = caught.value.errors(include_url=False, include_context=False, include_input=False)
    assert len(errors) == 1
    assert errors[0]["msg"] == "Value error, mcp_configuration_invalid"


@pytest.mark.parametrize("field", OAUTH_FIELDS)
@pytest.mark.parametrize(
    "origin",
    ["http://provider.invalid", "https://other.invalid", "https://provider.invalid:8443"],
    ids=["scheme", "hostname", "port"],
)
def test_oauth_endpoints_must_match_issuer_origin(field: str, origin: str) -> None:
    config = configuration()
    config[field] = origin + URL_PATHS[field]

    with pytest.raises(ValidationError) as caught:
        ServiceConfig.model_validate(config)

    errors = caught.value.errors(include_url=False, include_context=False, include_input=False)
    assert len(errors) == 1
    assert errors[0]["msg"] == "Value error, mcp_configuration_invalid"


@pytest.mark.parametrize(
    "callback",
    [
        "https://127.0.0.1/callback",
        "https://127.0.0.1:8443/callback",
        "http://127.0.0.1/callback",
    ],
    ids=["same-origin", "different-port", "different-scheme"],
)
def test_callback_must_have_a_different_hostname(callback: str) -> None:
    config = configuration()
    set_url(config, "issuer", "https://127.0.0.1/issuer")
    config["callback_uri"] = callback

    with pytest.raises(ValidationError) as caught:
        ServiceConfig.model_validate(config)

    errors = caught.value.errors(include_url=False, include_context=False, include_input=False)
    assert len(errors) == 1
    assert errors[0]["msg"] == "Value error, mcp_callback_host_invalid"
