"""OA password credential acquisition for unattended polling."""

from __future__ import annotations

from typing import Any, Callable

from app.infra.auth.oa import (
    OAAuthenticationError,
    OAHttpSession,
    OAInvalidResponseError,
    OANetworkUnavailableError,
    OATimeoutError,
    OAUpstreamServerError,
)
from app.ports.auth import AuthenticationPort, CredentialStoreError, LoginCredential, Principal
from app.ports.credential_binding import (
    BackgroundCredentialAcquirerPort,
    CredentialAcquisitionError,
    CredentialPollCandidate,
    PasswordBindingReaderPort,
)


class OAPasswordCredentialAcquirer:
    """First credential-acquisition implementation; callers remain mechanism-free."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], OAHttpSession],
        authentication: AuthenticationPort,
        binding_store: PasswordBindingReaderPort,
    ) -> None:
        self._session_factory = session_factory
        self._authentication = authentication
        self._binding_store = binding_store

    async def acquire(self, candidate: CredentialPollCandidate) -> Principal:
        if candidate.target_system != "oa":
            raise CredentialAcquisitionError("unsupported_target")
        await self._ensure_captcha_is_not_required()
        try:
            binding = await self._binding_store.load_password_for_poll(
                candidate.ai_user_id,
                candidate.target_system,
            )
            principal = await self._authentication.authenticate(
                LoginCredential(
                    loginid=binding.login_id,
                    userpassword=binding.password,
                ),
                reactivate_revoked_session=False,
            )
        except OAAuthenticationError as error:
            raise CredentialAcquisitionError(error.failure_kind) from None
        except CredentialStoreError:
            raise CredentialAcquisitionError("local_failure") from None
        except Exception:
            raise CredentialAcquisitionError("local_failure") from None
        if principal.ai_user_id != candidate.ai_user_id:
            raise CredentialAcquisitionError("identity_mismatch")
        return principal

    async def _ensure_captcha_is_not_required(self) -> None:
        try:
            payload = await self._session_factory().post_form(
                "/api/hrm/login/getLoginForm",
                {},
            )
            login_setting = payload.get("loginSetting")
            if not isinstance(login_setting, dict):
                raise TypeError
            value: Any = login_setting.get("hasValidateCode")
            normalized = value.strip().lower() if isinstance(value, str) else value
            if (
                normalized is True
                or normalized == 1
                or normalized == "1"
                or normalized == "true"
            ):
                raise CredentialAcquisitionError("captcha_required")
            if normalized is not False and normalized not in {"0", 0, "false"}:
                raise TypeError
        except CredentialAcquisitionError:
            raise
        except OANetworkUnavailableError:
            raise CredentialAcquisitionError("network_unreachable") from None
        except OATimeoutError:
            raise CredentialAcquisitionError("timeout") from None
        except OAUpstreamServerError:
            raise CredentialAcquisitionError("upstream_5xx") from None
        except OAInvalidResponseError:
            raise CredentialAcquisitionError("invalid_response") from None
        except Exception:
            raise CredentialAcquisitionError("local_failure") from None


def _protocol_check(
    acquirer: OAPasswordCredentialAcquirer,
) -> BackgroundCredentialAcquirerPort:
    return acquirer


__all__ = ("OAPasswordCredentialAcquirer",)
