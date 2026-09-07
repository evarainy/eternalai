"""OA-backed implementation of :class:`app.ports.user_profile.UserProfilePort`.

Two things in here are new attack surface for this repository and are treated
accordingly:

* ``result.orginfo`` is upstream-controlled **markup** — parsed by
  :mod:`app.infra.adapters.oa.orginfo`, never forwarded.
* ``result.messagerurl`` is an upstream-controlled **request target**.  It
  decides which URL we fetch with the user's OA cookies attached, which is a
  textbook SSRF shape.  :func:`validate_avatar_path` is therefore applied
  *before* the transport is touched at all, so a rejected value produces zero
  outbound requests rather than a request that merely fails later.

Every failure is classified into the closed ``OrgProfileStatus`` set; upstream
status codes, response bodies and exception text never leave this module.  Only
value-free stage strings are logged — no names, no paths, no cookies.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import OpenerDirector, ProxyHandler, Request, build_opener

from app.infra.adapters.oa.orginfo import parse_orginfo
from app.infra.adapters.oa.provider import (
    OALiveIdentityExpired,
    OALiveIdentityUnbound,
    OALivePermissionDenied,
    SameOriginRedirectHandler,
    build_cookie_header,
    origin_tuple,
    raise_for_http_status,
    validate_base_url,
    validate_endpoint_path,
)
from app.ports.auth import OASessionCredential
from app.ports.secret_provider import (
    CredentialExpiredError,
    CredentialNotFoundError,
    SecretProviderPort,
)
from app.ports.user_profile import (
    AvatarMediaType,
    OrgProfileStatus,
    UserAvatar,
    UserOrgProfile,
    UserProfileSnapshot,
)

OA_SESSION_REF_PREFIX = "oa-session-v1:"

DEFAULT_USER_PROFILE_PATH = "/api/hrm/resource/getResourceBaseTitle"
DEFAULT_MAX_PROFILE_RESPONSE_BYTES = 256 * 1024
DEFAULT_MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_AVATAR_PATH_LENGTH = 256

_ALLOWED_AVATAR_MEDIA_TYPES: Mapping[str, AvatarMediaType] = {
    "image/jpeg": "image/jpeg",
    "image/png": "image/png",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
}
_AVATAR_PATH_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-"
)
_AVATAR_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp")

_BROWSER_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 6.1; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/86.0.4240.111 Safari/537.36"
    ),
}


def validate_avatar_path(value: object, *, base_url: str) -> str | None:
    """Accept only a host-relative image path on the configured OA origin.

    Six independent checks, all of which must pass.  Anything else returns
    ``None`` and — crucially — the caller must then make no request at all.
    """

    try:
        # 1. must be a bounded string
        if not isinstance(value, str):
            return None
        if not 1 <= len(value) <= MAX_AVATAR_PATH_LENGTH:
            return None
        # 2. host-relative, and not a protocol-relative "//host/path"
        if not value.startswith("/") or value.startswith("//"):
            return None
        # 3. closed character set plus an image extension; this alone excludes
        #    "?", "#", "%", "\\", "@", ":", whitespace and non-ASCII
        lowered = value.casefold()
        if not lowered.endswith(_AVATAR_EXTENSIONS):
            return None
        if not set(value) <= _AVATAR_PATH_CHARACTERS:
            return None
        # 4. no empty, "." or ".." segments
        segments = value.split("/")[1:]
        if any(segment in {"", ".", ".."} for segment in segments):
            return None
        # 5. joining it onto the configured base must still land on that origin
        normalized_base, allowed_origin = validate_base_url(base_url)
        parsed = urlsplit(f"{normalized_base}{value}")
        if origin_tuple(parsed) != allowed_origin:
            return None
        if parsed.path != value or parsed.query or parsed.fragment:
            return None
        # 6. and the joined URL must carry no credentials
        if parsed.username is not None or parsed.password is not None:
            return None
        return value
    except Exception:
        _log_profile_failure("avatar_path_validation")
        return None


def allowed_avatar_media_type(content_type: object) -> AvatarMediaType | None:
    """Map an upstream Content-Type onto our whitelist, or reject it."""

    if not isinstance(content_type, str):
        return None
    mime = content_type.split(";", 1)[0].strip().casefold()
    return _ALLOWED_AVATAR_MEDIA_TYPES.get(mime)


class OAProfileTransport(Protocol):
    """Minimal outbound surface; kept tiny so tests can count avatar calls."""

    async def fetch_profile(
        self,
        credential: OASessionCredential,
    ) -> dict[str, Any]: ...

    async def fetch_avatar(
        self,
        credential: OASessionCredential,
        path: str,
    ) -> UserAvatar | None: ...


class LiveOAProfileTransport:
    """Bounded standard-library HTTP against one fixed OA origin."""

    def __init__(
        self,
        *,
        base_url: str,
        profile_path: str = DEFAULT_USER_PROFILE_PATH,
        timeout_seconds: float,
        max_response_bytes: int = DEFAULT_MAX_PROFILE_RESPONSE_BYTES,
        max_avatar_bytes: int = DEFAULT_MAX_AVATAR_BYTES,
        opener_factory: Callable[[], OpenerDirector] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._base_url, self._allowed_origin = validate_base_url(base_url)
        self._profile_path = validate_endpoint_path(profile_path)
        if timeout_seconds <= 0:
            raise ValueError("OA profile timeout must be positive")
        if max_response_bytes <= 0 or max_avatar_bytes <= 0:
            raise ValueError("OA profile size limits must be positive")
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._max_avatar_bytes = max_avatar_bytes
        self._opener_factory = opener_factory or self._build_isolated_opener
        self._clock = clock

    def _build_isolated_opener(self) -> OpenerDirector:
        # No proxies, and no cookie-bearing hop to another host.
        return build_opener(
            ProxyHandler({}),
            SameOriginRedirectHandler(self._allowed_origin),
        )

    async def fetch_profile(
        self,
        credential: OASessionCredential,
    ) -> dict[str, Any]:
        cookie_header = build_cookie_header(credential.cookies)
        try:
            parameters = urlencode(
                {
                    "id": credential.oa_user_id.get_secret_value(),
                    "__random__": str(int(self._clock().timestamp() * 1000)),
                }
            )
            request = Request(
                f"{self._base_url}{self._profile_path}?{parameters}",
                headers={**_BROWSER_HEADERS, "Cookie": cookie_header},
                method="GET",
            )
            return await asyncio.to_thread(self._open_json, request)
        finally:
            cookie_header = ""
            del credential

    async def fetch_avatar(
        self,
        credential: OASessionCredential,
        path: str,
    ) -> UserAvatar | None:
        # Re-validate at the transport boundary: the caller already did, but a
        # future caller must not be able to make this method fetch elsewhere.
        validated = validate_avatar_path(path, base_url=self._base_url)
        if validated is None:
            return None
        cookie_header = build_cookie_header(credential.cookies)
        try:
            request = Request(
                f"{self._base_url}{validated}",
                headers={
                    **_BROWSER_HEADERS,
                    "Accept": "image/*",
                    "Cookie": cookie_header,
                },
                method="GET",
            )
            return await asyncio.to_thread(self._open_avatar, request)
        finally:
            cookie_header = ""
            del credential

    def _open_json(self, request: Request) -> dict[str, Any]:
        opener = self._opener_factory()
        with opener.open(request, timeout=self._timeout_seconds) as response:
            raise_for_http_status(int(response.getcode()))
            raw = response.read(self._max_response_bytes + 1)
        if len(raw) > self._max_response_bytes:
            raise ValueError("OA profile response exceeds the size limit")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("OA profile response must be a JSON object")
        return {str(key): value for key, value in payload.items()}

    def _open_avatar(self, request: Request) -> UserAvatar | None:
        opener = self._opener_factory()
        with opener.open(request, timeout=self._timeout_seconds) as response:
            if int(response.getcode()) != 200:
                return None
            media_type = allowed_avatar_media_type(response.headers.get("Content-Type"))
            if media_type is None:
                # An expired OA session answers with an HTML login page; the
                # whitelist stops it here instead of shipping a broken image.
                return None
            raw = response.read(self._max_avatar_bytes + 1)
        if not raw or len(raw) > self._max_avatar_bytes:
            return None
        return UserAvatar(media_type=media_type, content=raw)


class OAUserProfileAdapter:
    """Read one user's own OA profile with that same user's OA Session."""

    def __init__(
        self,
        *,
        secret_provider: SecretProviderPort,
        transport: OAProfileTransport,
        base_url: str,
    ) -> None:
        self._secret_provider = secret_provider
        self._transport = transport
        self._base_url, _ = validate_base_url(base_url)

    async def get_profile(self, ai_user_id: str) -> UserProfileSnapshot:
        credential: OASessionCredential | None = None
        try:
            credential, status = await self._resolve_credential(ai_user_id)
            if credential is None:
                return UserProfileSnapshot(org_status=status)
            payload = await self._fetch_profile_payload(credential)
            if payload is None:
                return UserProfileSnapshot(org_status="unavailable")
            result = _result_object(payload)
            if result is None:
                return UserProfileSnapshot(org_status="unavailable")
            avatar_available = (
                validate_avatar_path(
                    result.get("messagerurl"),
                    base_url=self._base_url,
                )
                is not None
            )
            placement = parse_orginfo(result.get("orginfo"))
            if placement is None:
                return UserProfileSnapshot(
                    org_status="unparsable",
                    avatar_available=avatar_available,
                )
            return UserProfileSnapshot(
                org_status="ok",
                org=UserOrgProfile(
                    department_name=placement.department_name,
                    department_id=placement.department_id,
                    unit_name=placement.unit_name,
                    unit_id=placement.unit_id,
                ),
                avatar_available=avatar_available,
            )
        except Exception:
            _log_profile_failure("profile_read")
            return UserProfileSnapshot(org_status="unavailable")
        finally:
            credential = None

    async def get_avatar(self, ai_user_id: str) -> UserAvatar | None:
        credential: OASessionCredential | None = None
        try:
            credential, _ = await self._resolve_credential(ai_user_id)
            if credential is None:
                return None
            payload = await self._fetch_profile_payload(credential)
            if payload is None:
                return None
            result = _result_object(payload)
            if result is None:
                return None
            path = validate_avatar_path(
                result.get("messagerurl"),
                base_url=self._base_url,
            )
            if path is None:
                # Rejected target: no outbound avatar request is made at all.
                return None
            return await self._transport.fetch_avatar(credential, path)
        except Exception:
            _log_profile_failure("avatar_read")
            return None
        finally:
            credential = None

    async def _resolve_credential(
        self,
        ai_user_id: str,
    ) -> tuple[OASessionCredential | None, OrgProfileStatus]:
        if not isinstance(ai_user_id, str) or not ai_user_id:
            return None, "unavailable"
        try:
            credential = await self._secret_provider.resolve_oa_session(
                f"{OA_SESSION_REF_PREFIX}{ai_user_id}"
            )
        except CredentialNotFoundError:
            return None, "unbound"
        except CredentialExpiredError:
            return None, "expired"
        except Exception:
            return None, "unavailable"
        if not isinstance(credential, OASessionCredential):
            return None, "unavailable"
        return credential, "ok"

    async def _fetch_profile_payload(
        self,
        credential: OASessionCredential,
    ) -> dict[str, Any] | None:
        try:
            payload = await self._transport.fetch_profile(credential)
        except (OALiveIdentityUnbound, OALiveIdentityExpired):
            return None
        except OALivePermissionDenied:
            return None
        except HTTPError:
            return None
        except (TimeoutError, socket.timeout):
            return None
        except URLError:
            return None
        except (UnicodeError, json.JSONDecodeError, ValueError):
            return None
        except OSError:
            return None
        except Exception:
            _log_profile_failure("profile_transport")
            return None
        if not isinstance(payload, dict):
            return None
        return payload


def _result_object(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if payload.get("hasRight") is not True:
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    return result


def _log_profile_failure(stage: str) -> None:
    logging.getLogger(__name__).warning(
        "oa_user_profile_failure stage=%s classification=adapter_error",
        stage,
    )


__all__ = (
    "DEFAULT_MAX_AVATAR_BYTES",
    "DEFAULT_MAX_PROFILE_RESPONSE_BYTES",
    "DEFAULT_USER_PROFILE_PATH",
    "LiveOAProfileTransport",
    "MAX_AVATAR_PATH_LENGTH",
    "OA_SESSION_REF_PREFIX",
    "OAProfileTransport",
    "OAUserProfileAdapter",
    "allowed_avatar_media_type",
    "validate_avatar_path",
)
