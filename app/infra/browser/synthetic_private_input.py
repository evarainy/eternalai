"""One bounded operator frame from the explicitly selected anonymous stdin pipe.

This does not discover credentials or accept paths. Only the existing fixed
operator ciphertext is unlocked by the operator reader. Phrase-only frames serve
the fixed business and deactivation recipients without any provider key.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from pydantic import SecretStr

from app.infra.browser.synthetic_configuration import SyntheticOperatorBundle
from app.infra.browser.synthetic_vault import ORIGINAL_TRIAL, read_private_operator_document

FRAME_VERSION = "browser.synthetic.private-input.v1"
MAX_FRAME_BYTES = 65536
_FIELDS = frozenset({"version", "vault_passphrase", "jev_key"})


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("browser_private_input_invalid")
        result[name] = value
    return result


def _read_frame(*, require_jev_key: bool) -> dict[str, Any]:
    """Require non-TTY stdin, EOF and one closed, versioned JSON object."""
    try:
        if sys.stdin.isatty():
            raise ValueError
        payload = sys.stdin.buffer.read(MAX_FRAME_BYTES + 1)
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_FRAME_BYTES:
            raise ValueError
        frame = json.loads(payload.decode("ascii"), object_pairs_hook=_closed_object)
        fields = _FIELDS if require_jev_key else _FIELDS - {"jev_key"}
        if (type(frame) is not dict or set(frame) != fields
                or frame["version"] != FRAME_VERSION
                or type(frame["vault_passphrase"]) is not str
                or not 12 <= len(frame["vault_passphrase"]) <= 4096):
            raise ValueError
        # Match the existing vault passphrase contract, including Unicode/spaces.
        frame["vault_passphrase"].encode("utf-8")
        value = frame.get("jev_key")
        if require_jev_key and (type(value) is not str or not 1 <= len(value) <= 4096 or any(
            ord(char) < 33 or ord(char) > 126 for char in value
        )):
            raise ValueError("jev_key_input_invalid")
    except (Exception, KeyboardInterrupt) as error:
        code = ("jev_key_input_invalid" if type(error) is ValueError
                and error.args == ("jev_key_input_invalid",)
                else "browser_private_input_invalid")
        raise ValueError(code) from None
    return frame


def read_private_passphrase() -> SecretStr:
    """Phrase-only closed frame: business/cleanup recipients never receive Jev key."""
    return SecretStr(_read_frame(require_jev_key=False)["vault_passphrase"])


def read_private_operator_input(
    *, trial_id: str = ORIGINAL_TRIAL
) -> tuple[SyntheticOperatorBundle, SecretStr]:
    frame = _read_frame(require_jev_key=True)
    key = SecretStr(frame["jev_key"])
    phrase = SecretStr(frame["vault_passphrase"])
    try:
        document = (read_private_operator_document(phrase) if trial_id == ORIGINAL_TRIAL
                    else read_private_operator_document(phrase, trial_id=trial_id))
        bundle = SyntheticOperatorBundle.model_validate(document)
    except (Exception, KeyboardInterrupt):
        raise ValueError("browser_operator_bundle_invalid") from None
    return bundle, key
