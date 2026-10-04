"""One bounded operator frame from the explicitly selected anonymous stdin pipe.

This does not discover credentials or accept paths. Only the existing fixed
operator ciphertext is unlocked; business and deactivation input are excluded.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from pydantic import SecretStr

from app.infra.browser.synthetic_configuration import SyntheticOperatorBundle
from app.infra.browser.synthetic_vault import read_private_operator_document

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


def read_private_operator_input() -> tuple[SyntheticOperatorBundle, SecretStr]:
    """Require non-TTY stdin, EOF and one closed, versioned JSON object."""
    try:
        if sys.stdin.isatty():
            raise ValueError
        payload = sys.stdin.buffer.read(MAX_FRAME_BYTES + 1)
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_FRAME_BYTES:
            raise ValueError
        frame = json.loads(payload.decode("ascii"), object_pairs_hook=_closed_object)
        if (type(frame) is not dict or set(frame) != _FIELDS
                or frame["version"] != FRAME_VERSION
                or type(frame["vault_passphrase"]) is not str
                or not 12 <= len(frame["vault_passphrase"]) <= 4096):
            raise ValueError
        # Match the existing vault passphrase contract, including Unicode/spaces.
        frame["vault_passphrase"].encode("utf-8")
        value = frame["jev_key"]
        if type(value) is not str or not 1 <= len(value) <= 4096 or any(
            ord(char) < 33 or ord(char) > 126 for char in value
        ):
            raise ValueError("jev_key_input_invalid")
        key = SecretStr(value)
        phrase = SecretStr(frame["vault_passphrase"])
    except (Exception, KeyboardInterrupt) as error:
        code = ("jev_key_input_invalid" if type(error) is ValueError
                and error.args == ("jev_key_input_invalid",)
                else "browser_private_input_invalid")
        raise ValueError(code) from None
    try:
        bundle = SyntheticOperatorBundle.model_validate(read_private_operator_document(phrase))
    except (Exception, KeyboardInterrupt):
        raise ValueError("browser_operator_bundle_invalid") from None
    return bundle, key
