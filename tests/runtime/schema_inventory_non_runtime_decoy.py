"""Non-Runtime capability construction used to prove inventory directionality."""

from __future__ import annotations

from tests.runtime.registry_fakes import active_capability


def build_non_runtime_decoy() -> object:
    schema = {
        "type": "object",
        "properties": {"synthetic_password_property": {"type": "string"}},
    }
    return active_capability("synthetic.non-runtime-decoy", output_schema=schema)
