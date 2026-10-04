"""Explicit existing publication operations; never bootstrap identities or grants."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

from sqlalchemy.exc import SQLAlchemyError

from app.infra.browser.openrouter_jev import prompt_openrouter_key
from app.infra.browser.synthetic_configuration import SyntheticDeactivationBundle
from app.infra.browser.synthetic_operator import (
    deactivate_synthetic_publication,
    open_synthetic_operator,
    prompt_deactivation_bundle,
    prompt_operator_bundle,
)
from app.infra.browser.synthetic_private_input import (
    read_private_operator_input,
    read_private_passphrase,
)
from app.infra.browser.synthetic_vault import (
    DIAGNOSTIC_TRIAL,
    OBSERVE_TRIAL,
    ORIGINAL_TRIAL,
    VISIBLE_TRIAL,
    approved_trial_id,
    read_private_deactivation_document,
)
from app.ports.auth import AuthenticationError
from app.ports.browser_publication_store import BrowserPublicationError

_INPUT_CODES = {
    "bundle": frozenset({
        "browser_operator_bundle_invalid", "browser_operator_private_console_required",
        "browser_private_input_invalid",
    }),
    "key": frozenset({"jev_key_input_invalid", "jev_secure_console_required"}),
}
_INPUT_DIAGNOSTICS = {
    "bundle": "browser_synthetic_publication_bundle_input_invalid",
    "key": "browser_synthetic_publication_key_input_invalid",
}
_AUTHORIZATION_CODES = frozenset({
    "browser_operator_publication_denied", "browser_operator_business_denied",
    "browser_publication_denied", "browser_publication_source_denied",
    "browser_operator_publication_authority_invalid",
    "browser_operator_business_authority_invalid", "browser_operator_cleanup_authority_invalid",
})
_CONFIG_CODES = frozenset({
    "browser_operator_key_invalid", "browser_operator_keyring_invalid",
    "browser_operator_manifest_invalid", "browser_operator_image_invalid",
    "browser_database_password_file_invalid", "browser_operator_installation_unavailable",
    "browser_operator_capability_registration_required",
    "browser_operator_active_publication_required",
    "browser_operator_binding_capacity_registration_required", "jev_installation_invalid",
    "browser_local_cleanup_grant_invalid", "browser_local_enablement_invalid",
    "browser_local_installation_invalid", "browser_local_source_invalid",
    "browser_local_chat_provider_required", "browser_local_input_mode_invalid",
    "browser_local_pool_registration_invalid", "browser_local_composition_unavailable",
    "browser_vertical_authority_configuration_invalid", "browser_vertical_digest_key_invalid",
    "browser_vertical_trusted_dependency_required", "browser_vertical_enablement_invalid",
})


class _PublicationDiagnostic(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def _has_known_code(error: ValueError, codes: frozenset[str]) -> bool:
    # Only compare a single plain string against a closed set; never render the error.
    return (len(error.args) == 1 and type(error.args[0]) is str
            and error.args[0] in codes)


class _SilentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError("browser_operator_arguments_invalid")


async def _operate(
    operation: str, *, input_mode: str = "chat", operator_vault: Path | None = None,
    deactivation_vault: Path | None = None,
    private_stdin: bool = False,
    trial_id: str = ORIGINAL_TRIAL,
) -> None:
    approved_trial_id(trial_id)
    if trial_id != ORIGINAL_TRIAL and not private_stdin:
        raise ValueError("browser_operator_arguments_invalid")
    trial_options: dict[str, Any] = {"trial_id": trial_id} if trial_id != ORIGINAL_TRIAL else {}
    stage = "bundle"
    try:
        if operation == "deactivate":
            if operator_vault is not None or (private_stdin and deactivation_vault is not None):
                raise ValueError("browser_operator_arguments_invalid")
            deactivation_bundle = (SyntheticDeactivationBundle.model_validate(
                read_private_deactivation_document(read_private_passphrase(), **trial_options)
            ) if private_stdin else prompt_deactivation_bundle(deactivation_vault))
            stage = "deactivate"
            await deactivate_synthetic_publication(deactivation_bundle, enabled=True,
                                                    **trial_options)
            return
        if deactivation_vault is not None:
            raise ValueError("browser_operator_arguments_invalid")
        if private_stdin:
            if operator_vault is not None or operation not in {"prepare", "activate"}:
                raise ValueError("browser_operator_arguments_invalid")
            bundle, key = (read_private_operator_input() if trial_id == ORIGINAL_TRIAL
                           else read_private_operator_input(trial_id=trial_id))
        else:
            bundle = prompt_operator_bundle(operator_vault)
            stage = "key"
            key = prompt_openrouter_key()
        stage = "operator"
        async with open_synthetic_operator(
            bundle, jev_key=key, enabled=True, require_active_publication=False,
            input_mode=input_mode,
            **trial_options,
        ) as components:
            vertical, owner = components.vertical, components.publication_owner
            if operation == "prepare":
                stage = "prepare"
                if trial_id == OBSERVE_TRIAL:
                    await vertical.publications.prepare_observe_only(owner, vertical._seed)
                elif trial_id == VISIBLE_TRIAL:
                    await vertical.publications.prepare_visible_complete(owner, vertical._seed)
                elif trial_id == DIAGNOSTIC_TRIAL:
                    await vertical.publications.prepare_diagnostic_second(owner, vertical._seed)
                else:
                    await vertical.prepare_seed(owner)
            elif operation == "activate":
                stage = "activate"
                await vertical.activate_seed(owner)
            else:
                raise ValueError("browser_operator_arguments_invalid")
    except AuthenticationError:
        raise _PublicationDiagnostic("browser_synthetic_publication_authorization_denied") from None
    except SQLAlchemyError:
        raise _PublicationDiagnostic("browser_synthetic_publication_database_unavailable") from None
    except BrowserPublicationError as error:
        if _has_known_code(error, _AUTHORIZATION_CODES):
            raise _PublicationDiagnostic(
                "browser_synthetic_publication_authorization_denied",
            ) from None
        raise
    except ValueError as error:
        if type(error) is not ValueError:
            raise
        if private_stdin and stage == "bundle" and _has_known_code(error, _INPUT_CODES["key"]):
            raise _PublicationDiagnostic(_INPUT_DIAGNOSTICS["key"]) from None
        if _has_known_code(error, _INPUT_CODES.get(stage, frozenset())):
            raise _PublicationDiagnostic(_INPUT_DIAGNOSTICS[stage]) from None
        if stage in {"operator", "prepare", "activate", "deactivate"}:
            if _has_known_code(error, _AUTHORIZATION_CODES):
                raise _PublicationDiagnostic(
                    "browser_synthetic_publication_authorization_denied",
                ) from None
            if _has_known_code(error, _CONFIG_CODES):
                raise _PublicationDiagnostic(
                    "browser_synthetic_publication_configuration_invalid",
                ) from None
        raise


def main(argv: list[str] | None = None) -> int:
    parser = _SilentParser(allow_abbrev=False, add_help=False)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--operation", choices=("prepare", "activate", "deactivate"))
    parser.add_argument("--input-mode", choices=("chat", "structured"), default="chat")
    parser.add_argument("--operator-vault", type=Path)
    parser.add_argument("--deactivation-vault", type=Path)
    parser.add_argument("--private-stdin", action="store_true")
    parser.add_argument(
        "--trial-id", choices=(ORIGINAL_TRIAL, DIAGNOSTIC_TRIAL, VISIBLE_TRIAL, OBSERVE_TRIAL),
        default=ORIGINAL_TRIAL,
    )
    try:
        args = parser.parse_args(argv)
        if not args.enable or args.operation is None:
            raise ValueError("browser_operator_disabled")
        asyncio.run(_operate(
            args.operation, input_mode=args.input_mode,
            operator_vault=args.operator_vault, deactivation_vault=args.deactivation_vault,
            private_stdin=args.private_stdin,
            trial_id=args.trial_id,
        ))
    except _PublicationDiagnostic as error:
        print(error.code, file=sys.stderr)
        return 2
    except BrowserPublicationError as error:
        code = str(error)
        print(code if re.fullmatch(r"browser_publication_[a-z_]{1,80}", code)
              else "browser_synthetic_publication_unavailable", file=sys.stderr)
        return 2
    except BaseException:
        print("browser_synthetic_publication_unavailable", file=sys.stderr)
        return 2
    print("browser_synthetic_publication_updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
