"""Replay and explicit-unimplemented Live providers for OA read capabilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from app.infra.adapters.oa.contracts import (
    OAContractPackProfile,
    OAPendingWorkflowCollection,
    build_structural_fingerprint,
)


class OAReadProvider(Protocol):
    """Narrow provider seam behind the real OA adapter."""

    async def list_pending_workflows(self) -> OAPendingWorkflowCollection: ...


class OAContractPackError(RuntimeError):
    """The selected Replay Contract Pack cannot be loaded safely."""


class OAContractPackPayloadInvalid(OAContractPackError):
    """The Contract Pack exists but violates its normalized payload contract."""


class LiveOAReadProviderNotImplemented(RuntimeError):
    """Live OA access is intentionally deferred to P2-READ-ADAPTER-001."""


class ReplayOAReadProvider:
    """Load one immutable, fingerprint-bound OA Contract Pack from disk."""

    def __init__(self, contract_pack_dir: Path) -> None:
        self._contract_pack_dir = contract_pack_dir

    async def list_pending_workflows(self) -> OAPendingWorkflowCollection:
        profile_payload = self._load_json("profile.json")
        try:
            profile = OAContractPackProfile.model_validate(
                profile_payload,
                strict=True,
            )
        except ValidationError as exc:
            raise OAContractPackError("Contract Pack profile is invalid") from exc

        if profile.profile_version != self._contract_pack_dir.name:
            raise OAContractPackError("Contract Pack directory and profile disagree")

        sample_payload = self._load_json(profile.sample_file)
        fingerprint_payload = self._load_json(profile.fingerprint_file)
        if fingerprint_payload != build_structural_fingerprint(sample_payload):
            raise OAContractPackError("Contract Pack structural fingerprint mismatch")

        try:
            return OAPendingWorkflowCollection.model_validate(
                sample_payload,
                strict=True,
            )
        except ValidationError as exc:
            raise OAContractPackPayloadInvalid(
                "Contract Pack sample violates the normalized OA model"
            ) from exc

    def _load_json(self, file_name: str) -> Any:
        path = self._contract_pack_dir / file_name
        try:
            raw = path.read_text(encoding="utf-8")
            return json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OAContractPackError("Contract Pack file cannot be loaded") from exc


class LiveOAReadProvider:
    """Deliberate fail-closed placeholder; it never falls back to Replay."""

    async def list_pending_workflows(self) -> OAPendingWorkflowCollection:
        raise LiveOAReadProviderNotImplemented(
            "Live OA read provider belongs to P2-READ-ADAPTER-001"
        )


__all__ = (
    "LiveOAReadProvider",
    "LiveOAReadProviderNotImplemented",
    "OAContractPackError",
    "OAContractPackPayloadInvalid",
    "OAReadProvider",
    "ReplayOAReadProvider",
)
