"""Single checkpoint Laya inference adapter. Loading is explicit and offline only."""

from __future__ import annotations

import asyncio
import importlib
import math
from pathlib import Path
from typing import Any, Literal

from services.decision.manifest import LayaInputAdapter, PinnedCheckpoint, strict_json
from services.decision.offline import inspect_offline
from services.decision.protocol import EngineInput, EngineResult, ProbabilityEntry, Usage
from services.decision.service import ServingFailure

_ENGINE_ISSUER = object()


def _checkpoint_dir(manifest: PinnedCheckpoint, root: Path) -> Path:
    checkpoint = root / manifest.subfolder if manifest.subfolder else root
    prefix = f"{manifest.subfolder}/" if manifest.subfolder else ""
    expected_roles = {
        prefix + "model.safetensors": "weights",
        prefix + "rl_agent_config.json": "config",
        prefix + "encoder/config.json": "config",
        prefix + "tokenizer/tokenizer.json": "tokenizer",
        prefix + "tokenizer/tokenizer_config.json": "tokenizer",
    }
    registered = {artifact.path: artifact.role for artifact in manifest.artifacts}
    if (
        any(registered.get(path) != role for path, role in expected_roles.items())
        or manifest.tokenizer_path != prefix + "tokenizer/tokenizer.json"
    ):
        raise ServingFailure("model_mismatch")
    return checkpoint


def _model_question(
    inputs: EngineInput, adapter: LayaInputAdapter
) -> tuple[dict[str, object], dict[str, object]]:
    """Project only registered choice text; no URLs, DOM, owner or credentials."""
    if len(inputs.options) < 2 or len(inputs.options) > 32 or sum(
        len(text) for text in (*inputs.criteria, *(
            text for option in inputs.options for text in (
                option.label or "", option.row_label or "", option.column_label or "",
                *option.context,
            )
        ))
    ) > 4096:
        raise ServingFailure("input_unsupported")
    state: dict[str, object] = {
        "text": "\n".join(inputs.criteria),
        "elements": [
            {
                "id": option.id,
                "role": option.role,
                "label": option.label or "",
                "context": list(option.context),
                "row_label": option.row_label or "",
                "column_label": option.column_label or "",
            }
            for option in inputs.options
        ],
    }
    choices = {
        option.id: " | ".join(
            part for part in (
                option.role, option.label, option.row_label,
                option.column_label, *option.context,
            ) if part
        )
        for option in inputs.options
    }
    questions: dict[str, object] = {
        "target": {
            "type": "choice",
            "instructions": adapter.instruction,
            "criteria": choices,
        }
    }
    return state, questions


def _model_result(raw: object, inputs: EngineInput, calibration_digest: str) -> EngineResult:
    """Keep choice probabilities and the distinct Laya answer-confidence score."""
    if not isinstance(raw, dict) or set(raw.get("answers", {})) != {"target"}:
        raise ValueError("decision_model_answer_invalid")
    answer = raw["answers"]["target"]
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise ValueError("decision_model_answer_invalid")
    if answer.get("low_confidence") is True:
        return EngineResult(
            outcome="abstained", selected_id=None, distribution=(), certainty=0.0,
            usage=_usage(raw), score_semantics="laya_answer_confidence",
            calibration_digest=calibration_digest,
        )
    choice = answer.get("choice")
    probabilities = answer.get("probabilities")
    ids = {option.id for option in inputs.options}
    if (
        type(choice) is not str or choice not in ids
        or not isinstance(probabilities, dict) or set(probabilities) != ids
    ):
        raise ValueError("decision_model_choice_invalid")
    entries = tuple(
        ProbabilityEntry(id=option.id, p=probabilities[option.id])
        for option in inputs.options
    )
    confidence = answer.get("answer_confidence")
    if (
        not isinstance(confidence, (float, int))
        or isinstance(confidence, bool)
        or not math.isfinite(float(confidence))
    ):
        raise ValueError("decision_model_confidence_invalid")
    return EngineResult(
        outcome="selected", selected_id=choice, distribution=entries,
        certainty=float(confidence), usage=_usage(raw),
        score_semantics="laya_answer_confidence",
        calibration_digest=calibration_digest,
    )


def _usage(raw: dict[str, Any]) -> Usage:
    usage = raw.get("usage")
    if (
        not isinstance(usage, dict)
        or usage.get("truncated") is not False
        or usage.get("state_tokens_dropped") != 0
        or usage.get("truncated_questions") != []
        or usage.get("options") not in (None, {})
    ):
        raise ValueError("decision_model_usage_invalid")
    return Usage.model_validate({
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
    })


class LayaEngine:
    """One loaded Laya agent; no registry, auto download or cloud fallback."""

    execution_kind: Literal["model"] = "model"

    def __init__(
        self, *, agent: Any, manifest: PinnedCheckpoint,
        adapter: LayaInputAdapter, device: Literal["cpu", "cuda"],
        issuer: object,
    ) -> None:
        if issuer is not _ENGINE_ISSUER:
            raise ValueError("decision_unverified_engine")
        self._agent = agent
        self._adapter = adapter
        self._device = device
        self._dtype = manifest.dtype
        self._calibration_digest = manifest.calibration.evidence_digest
        self.loaded_manifest_digest = manifest.digest

    @classmethod
    def load(
        cls, *, manifest: PinnedCheckpoint, artifact_root: Path,
        device: Literal["cpu", "cuda"],
    ) -> LayaEngine:
        """Load a physically verified local directory under an external network deny."""
        checked = PinnedCheckpoint.model_validate_json(manifest.model_dump_json())
        if checked.repository != "cklxx/laya-browser":
            raise ServingFailure("WAITING_ENV")
        if checked.device != device or checked.serving_backend != "laya_eager":
            raise ServingFailure("model_mismatch")
        inspection = inspect_offline(checked, artifact_root)
        if inspection.status == "WAITING_ENV":
            raise ServingFailure("WAITING_ENV")
        if inspection.status != "READY":
            raise ServingFailure("unavailable")
        root = artifact_root.resolve()
        checkpoint = _checkpoint_dir(checked, root)
        required_packages = {
            "laya", "torch", "transformers", "safetensors", "huggingface-hub",
        }
        if not required_packages <= {item.package for item in checked.runtime}:
            raise ServingFailure("model_mismatch")
        adapter_files = [item for item in checked.artifacts if item.role == "input_adapter"]
        if len(adapter_files) != 1:
            raise ServingFailure("model_mismatch")
        try:
            adapter_raw = (root / adapter_files[0].path).read_bytes()
            strict_json(adapter_raw)
            adapter = LayaInputAdapter.model_validate_json(adapter_raw)
            hub_constants = importlib.import_module("huggingface_hub.constants")
            transformers = importlib.import_module("transformers")
            if (
                getattr(hub_constants, "HF_HUB_OFFLINE", False) is not True
                or transformers.utils.is_offline_mode() is not True
            ):
                raise ServingFailure("unavailable")
            # A verified absolute local directory is passed to Laya. No repository
            # alias, revision resolver, dynamic model code or alternate serving path.
            laya = importlib.import_module("laya")
            agent = laya.load(str(checkpoint), device=device, fast=False, compile=False)
            actual_device = getattr(getattr(agent, "device", None), "type", None)
            actual_dtype = str(getattr(agent, "dtype", "")).removeprefix("torch.")
            if actual_device != device or actual_dtype != checked.dtype:
                raise ServingFailure("model_mismatch")
            # Catch file rewrites during load, including tokenizer repairs. The
            # deployment must additionally mount the artifact root read-only.
            if inspect_offline(checked, artifact_root).status != "READY":
                raise ServingFailure("model_mismatch")
        except ServingFailure:
            raise
        except Exception:
            raise ServingFailure("unavailable") from None
        return cls(
            agent=agent, manifest=checked, adapter=adapter, device=device,
            issuer=_ENGINE_ISSUER,
        )

    def _infer_sync(self, inputs: EngineInput) -> EngineResult:
        if getattr(self._agent.device, "type", None) != self._device:
            raise ValueError("decision_device_changed")
        state, questions = _model_question(inputs, self._adapter)
        before_fallbacks = getattr(self._agent, "cpu_fallback_count", 0)
        try:
            raw = self._agent.predict(state, questions)
        except Exception:
            raise ServingFailure("unavailable") from None
        if (
            getattr(self._agent.device, "type", None) != self._device
            or str(getattr(self._agent, "dtype", "")).removeprefix("torch.") != self._dtype
            or getattr(self._agent, "cpu_fallback_count", 0) != before_fallbacks
        ):
            raise ServingFailure("model_mismatch")
        try:
            return _model_result(raw, inputs, self._calibration_digest)
        except (ValueError, TypeError, AttributeError):
            raise ServingFailure("invalid_response") from None

    async def infer(self, inputs: EngineInput, cancellation: asyncio.Event) -> EngineResult:
        if cancellation.is_set():
            raise asyncio.CancelledError
        # A cancelled await does not stop a worker thread. Keep this coroutine
        # alive until the real forward finishes so DecisionService retains quota.
        forward = asyncio.create_task(asyncio.to_thread(self._infer_sync, inputs))
        try:
            result = await asyncio.shield(forward)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(forward)
            finally:
                raise
        if cancellation.is_set():
            raise asyncio.CancelledError
        return result
