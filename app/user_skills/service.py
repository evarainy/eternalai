"""Use cases of personal flows shared by the chat runtime and the HTTP API.

Owner and session keys always come from the trusted caller. Session working
state (records, proposals, trials, run snapshots, the pending takeover) and
saved versions are persisted by the injected stores.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from app.infra.sdui.credential_markers import has_credential_marker
from app.knowledge.capability_selection import (
    ToolDefinitionRejected,
    capability_fingerprint,
    project_tool_input_schema,
    validate_tool_text,
)
from app.ports.capability_registry import (
    CAPABILITY_NAME_MAX_LENGTH,
    CAPABILITY_SHORT_DESCRIPTION_MAX_LENGTH,
    CapabilityName,
    CapabilityRegistryPort,
    CapabilityShortDescription,
    CapabilitySpec,
)
from app.ports.user_skills import (
    SessionKey,
    TrialSnapshot,
    UserSkillError,
    UserSkillOwner,
    UserSkillSessionStorePort,
    UserSkillState,
    UserSkillStorePort,
    UserSkillVersion,
    UserSkillVersionPayload,
)
from app.user_skills.definition import (
    NAME_MAX,
    UserSkillDefinition,
    canonical_definition_json,
    compile_definition,
    definition_digest,
    params_schema,
)
from app.user_skills.proposal import (
    ProposalRejected,
    TrialChoices,
    build_proposal,
    compile_trial,
    rebase_definition,
    summarize,
)
from app.user_skills.rendering import MAX_DISPLAY_ROWS
from app.user_skills.runner import (
    InputInvalid,
    RunContext,
    RunOutcome,
    RunTarget,
    UserSkillRunner,
    check_inputs,
)
from app.workflow.definition_run import DefinitionReferenceError, resolve_ref
from app.workflow.models import DefinitionRunResult

_LOGGER = logging.getLogger("uvicorn.error.user_skills")
MAX_CHOICE_OPTIONS = 5
TAKEOVER_ERRORS = frozenset(
    {
        "adapter_payload_invalid",
        "adapter_missing_required_field",
        "adapter_error",
        "adapter_http_500",
        "adapter_timeout",
        "adapter_empty_response",
        "capability_not_found",
        "capability_candidate_stale",
        "internal_error",
    }
)
_NAME = TypeAdapter(CapabilityName)
_PURPOSE = TypeAdapter(CapabilityShortDescription)


class UserSkillFailure(Exception):
    """A closed refusal of an API use case; ``status`` is the HTTP status."""

    def __init__(self, code: str, status: int, label: str) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.label = label


def norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split()).casefold()


def workflow_id(skill_id: str | None, trial_id: str | None = None) -> str:
    return f"user_skill.{skill_id}" if skill_id else f"user_skill.trial.{trial_id}"


def skill_uuid(raw: Any) -> str | None:
    """The canonical id of a client-supplied flow id, or None when it is not a UUID."""

    if not isinstance(raw, str):
        return None
    try:
        return str(UUID(raw))
    except ValueError:
        return None


def check_skill_texts(name: str, purpose: str | None) -> None:
    """The flow's name and purpose pass the run descriptor and model tool-text rules."""

    try:
        _NAME.validate_python(name)
        validate_tool_text(name, max_chars=CAPABILITY_NAME_MAX_LENGTH)
    except (ValidationError, ToolDefinitionRejected):
        raise UserSkillFailure("name_invalid", 422, "名称里有不能使用的字符，请修改") from None
    if purpose is None:
        return
    try:
        _PURPOSE.validate_python(purpose)
        validate_tool_text(purpose, max_chars=CAPABILITY_SHORT_DESCRIPTION_MAX_LENGTH)
    except (ValidationError, ToolDefinitionRejected):
        raise UserSkillFailure("purpose_invalid", 422, "用途里有不能使用的字符，请修改") from None


def unusable_label(current: UserSkillVersion) -> str:
    if current.state == "disabled":
        return "该功能已停用"
    if current.pause_reason == "capability_unavailable":
        return "该功能依赖的查询已不可用"
    return "该功能依赖的查询已变化，可在「我的功能」中重新生成"


class UserSkillService:
    def __init__(
        self,
        *,
        store: UserSkillStorePort,
        sessions: UserSkillSessionStorePort,
        runner: UserSkillRunner,
        registry: CapabilityRegistryPort,
        read_workflow_leaves: Mapping[str, Sequence[tuple[str, str, Mapping[str, Any]]]],
    ) -> None:
        self._read_workflow_leaves = dict(read_workflow_leaves)
        self._store = store
        self.sessions = sessions
        self._runner = runner
        self._registry = registry

    # ----------------------------------------------------------- listing
    async def usable(self, owner: UserSkillOwner) -> list[UserSkillVersion]:
        return await self._store.list_current(owner, frozenset({"usable"}))

    async def current(self, owner: UserSkillOwner, skill_id: str) -> UserSkillVersion | None:
        canonical = skill_uuid(skill_id)
        return None if canonical is None else await self._store.get_current(owner, canonical)

    async def list(self, owner: UserSkillOwner) -> list[dict[str, Any]]:
        versions = await self._store.list_current(
            owner, frozenset({"usable", "paused", "disabled"})
        )
        items: list[dict[str, Any]] = []
        for version in versions:
            definition = UserSkillDefinition.model_validate(version.payload.definition)
            items.append({
                "skill_id": version.skill_id,
                "name": version.display_name,
                "purpose": version.payload.purpose,
                "state": version.state,
                "pause_reason_label": (
                    None if version.pause_reason is None else _PAUSE_LABELS[version.pause_reason]
                ),
                "can_regenerate": (
                    version.state == "paused" and version.pause_reason == "capability_changed"
                ),
                "current_version": version.current_version,
                "updated_at": version.updated_at.isoformat(),
                "exportable": await self._exportable(definition),
                "form": _form(definition),
            })
        return items

    async def _exportable(self, definition: UserSkillDefinition) -> bool:
        for step in definition.steps:
            spec = await self._registry.get(step.capability_id)
            if spec is None or spec.target_system != "oa":
                return False
        return True

    async def set_enabled(
        self,
        owner: UserSkillOwner,
        skill_id: str,
        state: Literal["usable", "disabled"],
        expected_current_version: int,
    ) -> dict[str, Any]:
        source: frozenset[UserSkillState] = frozenset(
            {"disabled"} if state == "usable" else {"usable"}
        )
        version = await self.current(owner, skill_id)
        if version is None:
            raise UserSkillFailure("not_found", 404, "功能不存在")
        if state == "usable":
            definition = UserSkillDefinition.model_validate(version.payload.definition)
            admission = await self._runner.admit(definition, _admin_context(owner))
            if admission.error_code is not None:
                raise UserSkillFailure(
                    "not_admissible", 409, "该功能依赖的查询已不可用，不能启用"
                )
        head = await self._store.set_state(
            owner, version.skill_id, from_states=source, to_state=state, pause_reason=None,
            expected_current_version=expected_current_version,
        )
        if head is None:
            if await self.current(owner, skill_id) is None:
                raise UserSkillFailure("not_found", 404, "功能不存在")
            raise UserSkillFailure("conflict", 409, "功能状态已变化，请刷新后重试")
        return {"skill_id": head.skill_id, "state": head.state,
                "current_version": head.current_version}

    async def rename(self, owner: UserSkillOwner, skill_id: str, name: str) -> dict[str, Any]:
        canonical = skill_uuid(skill_id)
        if canonical is None:
            raise UserSkillFailure("not_found", 404, "功能不存在")
        name = name.strip()
        if not 1 <= len(name) <= NAME_MAX:
            raise UserSkillFailure("name_invalid", 422, f"名称要在 1 到 {NAME_MAX} 个字之间")
        check_skill_texts(name, None)
        try:
            head = await self._store.rename(owner, canonical, name)
        except UserSkillError as exc:
            if exc.code == "name_taken":
                raise UserSkillFailure("name_taken", 409, "已经有同名功能，请换个名称") from None
            raise
        if head is None:
            raise UserSkillFailure("not_found", 404, "功能不存在")
        _LOGGER.info("user_skill.renamed skill=%s", head.skill_id)
        return {"skill_id": head.skill_id, "name": head.display_name}

    async def delete(self, owner: UserSkillOwner, skill_id: str) -> None:
        canonical = skill_uuid(skill_id)
        if canonical is None or not await self._store.delete(owner, canonical):
            raise UserSkillFailure("not_found", 404, "功能不存在")
        _LOGGER.info("user_skill.deleted skill=%s", canonical)

    # ------------------------------------------------------------ record
    async def record_call(
        self,
        key: SessionKey,
        *,
        turn_id: str,
        capability_id: str,
        capability_type: str,
        fingerprint: str,
        arguments: Mapping[str, Any],
        data: Mapping[str, Any],
    ) -> None:
        """Record one completed read call (exposed data only) for a later save.

        A code-owned read workflow is recorded as its query leaves; any other
        workflow, action or mock call is not recordable.
        """

        if _has_credential(arguments):
            _LOGGER.info("user_skill.record skipped=credential_marker")
            return
        if capability_type == "query":
            await self.sessions.record(
                key, turn_id=turn_id, capability_id=capability_id,
                capability_fingerprint=fingerprint, arguments=dict(arguments), data=dict(data),
            )
            return
        leaves = self._read_workflow_leaves.get(capability_id)
        if capability_type != "workflow" or leaves is None:
            return
        for step_id, leaf_id, static_arguments in leaves:
            part = data.get(step_id)
            spec = await self._registry.get(leaf_id)
            if not isinstance(part, Mapping) or spec is None or spec.type != "query":
                continue
            await self.sessions.record(
                key, turn_id=turn_id, capability_id=leaf_id,
                capability_fingerprint=capability_fingerprint(spec),
                arguments=dict(static_arguments), data=dict(part),
            )

    async def has_records(self, key: SessionKey) -> bool:
        return await self.sessions.has_records(key)

    async def summary(self, key: SessionKey, user_texts: Sequence[str]) -> dict[str, Any]:
        records, _ = await self.sessions.records(key)
        specs = await self._specs({record.capability_id for record in records})
        return summarize(records, specs, user_texts)

    async def _specs(self, capability_ids: set[str]) -> dict[str, CapabilitySpec]:
        specs: dict[str, CapabilitySpec] = {}
        for capability_id in sorted(capability_ids):
            spec = await self._registry.get(capability_id)
            if spec is not None:
                specs[capability_id] = spec
        return specs

    # ---------------------------------------------------------- proposal
    async def propose(
        self, key: SessionKey, owner: UserSkillOwner, raw: Mapping[str, Any]
    ) -> dict[str, Any]:
        records, dropped = await self.sessions.records(key)
        if not records:
            raise ProposalRejected(
                "no_record", "这次会话还没有可以保存的查询，请先完成一次查询"
            )
        specs = await self._specs({record.capability_id for record in records})
        fingerprints = {cid: capability_fingerprint(spec) for cid, spec in specs.items()}
        view, internal = build_proposal(records, raw, specs, fingerprints)
        heads = await self._store.list_for_owner(owner)
        view["update_targets"] = [
            {"skill_id": head.skill_id, "name": head.display_name,
             "current_version": head.current_version}
            for head in heads
        ]
        if dropped:
            view["rejections"].append(
                {"code": "record_truncated", "label": "较早的查询记录已超出保留范围，未列入"}
            )
        await self.sessions.put_proposal(key, view["proposal_id"], internal)
        _LOGGER.info(
            "user_skill.proposal records=%d steps=%d bindings=%d tables=%d checks=%d",
            len(records), len(view["steps"]), len(view["bindings"]),
            len(view["column_options"]), len(view["check_offers"]),
        )
        return view

    # ------------------------------------------------------------- trial
    async def trial(
        self,
        key: SessionKey,
        owner: UserSkillOwner,
        ctx: RunContext,
        proposal_id: str,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        internal = await self.sessions.proposal(key, proposal_id)
        if internal is None:
            raise UserSkillFailure("proposal_expired", 410, "提案已过期，请重新说「保存为功能」")
        target = _target(raw.get("target"))
        if target is None:
            raise UserSkillFailure("trial_invalid", 422, "请选择新建还是更新已有功能")
        try:
            choices = TrialChoices.model_validate(
                {k: v for k, v in raw.items() if k != "target"}
            )
        except ValidationError:
            raise UserSkillFailure("trial_invalid", 422, "卡片内容不完整") from None
        check_skill_texts(choices.name, choices.purpose)
        try:
            definition, inputs = compile_trial(internal, choices)
        except ProposalRejected as rejected:
            raise UserSkillFailure(rejected.code, 422, rejected.label) from None
        _require_exposable(definition)
        trial_id = str(uuid4())
        run_target = RunTarget(
            workflow_id=workflow_id(None, trial_id), version_label=f"trial-{trial_id}",
            name=choices.name, purpose=choices.purpose, definition=definition,
        )
        try:
            outcome = await self._runner.run(run_target, inputs, ctx)
        except InputInvalid:
            raise UserSkillFailure("input_invalid", 422, "请把每次要填写的内容填完整") from None
        savable, reason = _savable(outcome)
        payload = {
            "definition": definition.model_dump(mode="json", by_alias=True),
            "name": choices.name, "purpose": choices.purpose, "rules": choices.rules,
            "target": target,
        }
        await self.sessions.put_trial(key, TrialSnapshot(trial_id, proposal_id, payload, savable))
        result = dict(outcome.result)
        result["trial_id"] = trial_id
        return {"trial_id": trial_id, "result": result, "savable": savable,
                "not_savable_reason": reason}

    async def save(
        self, key: SessionKey, owner: UserSkillOwner, trial_id: str
    ) -> dict[str, Any]:
        trial = await self.sessions.trial(key, trial_id)
        if trial is None:
            raise UserSkillFailure("trial_expired", 410, "试运行已过期，请重新试运行")
        if trial.saved_result is not None:
            return trial.saved_result
        if not trial.savable:
            raise UserSkillFailure("not_savable", 409, "试运行没有成功，不能保存")
        payload = trial.payload
        definition = UserSkillDefinition.model_validate(payload["definition"])
        admission = await self._runner.admit(definition, _admin_context(owner))
        version_payload = UserSkillVersionPayload(
            definition=definition.model_dump(mode="json", by_alias=True),
            definition_digest=definition_digest(definition),
            purpose=payload["purpose"],
            business_rules=payload["rules"],
        )
        try:
            # A refusal is raised in the transaction after the saved replay, so a
            # concurrent save's receipt always wins over a later admission change.
            saved = await self._store.save_trial(
                key, trial_id, version_payload, refused=admission.error_code is not None
            )
        except UserSkillError as exc:
            failure = _SAVE_FAILURES.get(exc.code)
            if failure is None:
                raise
            raise UserSkillFailure(*failure) from None
        _LOGGER.info("user_skill.saved skill=%s version=%d mode=%s",
                     saved["skill_id"], saved["version"], payload["target"]["mode"])
        return saved

    # -------------------------------------------------------- regenerate
    async def regenerate(
        self,
        key: SessionKey,
        owner: UserSkillOwner,
        ctx: RunContext,
        skill_id: str,
        expected_current_version: int,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Re-pin a paused flow's unchanged structure to the current contracts and try it."""

        current = await self.current(owner, skill_id)
        if current is None:
            raise UserSkillFailure("not_found", 404, "功能不存在")
        if current.state != "paused" or current.current_version != expected_current_version:
            raise UserSkillFailure("conflict", 409, "功能状态已变化，请刷新后重试")
        if current.pause_reason != "capability_changed":
            raise UserSkillFailure(
                "not_regenerable", 409, "依赖的查询已不可用，不能重新生成"
            )
        definition = UserSkillDefinition.model_validate(current.payload.definition)
        specs = await self._specs({step.capability_id for step in definition.steps})
        fingerprints = {cid: capability_fingerprint(spec) for cid, spec in specs.items()}
        try:
            rebased = rebase_definition(definition, specs, fingerprints)
        except ProposalRejected as rejected:
            _LOGGER.info("user_skill.regenerate rejected=%s reason=%s",
                         rejected.code, rejected.reason)
            raise UserSkillFailure(rejected.code, 409, rejected.label) from None
        check_skill_texts(current.display_name, current.payload.purpose)
        _require_exposable(rebased)
        clean = {name: value for name, value in inputs.items() if value not in (None, "")}
        trial_id = str(uuid4())
        run_target = RunTarget(
            workflow_id=workflow_id(None, trial_id), version_label=f"trial-{trial_id}",
            name=current.display_name, purpose=current.payload.purpose, definition=rebased,
        )
        try:
            outcome = await self._runner.run(run_target, clean, ctx)
        except InputInvalid:
            raise UserSkillFailure("input_invalid", 422, "请把每次要填写的内容填完整") from None
        savable, reason = _regenerate_savable(outcome, rebased)
        payload = {
            "definition": rebased.model_dump(mode="json", by_alias=True),
            "name": current.display_name, "purpose": current.payload.purpose,
            "rules": current.payload.business_rules,
            "target": {"mode": "update", "skill_id": current.skill_id,
                       "expected_current_version": current.current_version},
        }
        await self.sessions.put_trial(key, TrialSnapshot(trial_id, None, payload, savable))
        _LOGGER.info("user_skill.regenerate skill=%s savable=%s", current.skill_id, savable)
        result = dict(outcome.result)
        result["trial_id"] = trial_id
        return {"trial_id": trial_id, "result": result, "savable": savable,
                "not_savable_reason": reason}

    # --------------------------------------------------------------- run
    async def run_saved(
        self,
        key: SessionKey,
        owner: UserSkillOwner,
        ctx: RunContext,
        skill_id: str,
        version: int,
        inputs: Mapping[str, Any],
        *,
        expected_digest: bytes | None = None,
        max_rows: int | None = MAX_DISPLAY_ROWS,
    ) -> tuple[dict[str, Any], bool, str | None]:
        """(``data.user_skill`` payload, failed, error code) for a saved flow."""

        current = await self.current(owner, skill_id)
        if current is None:
            raise UserSkillFailure("not_found", 404, "功能不存在")
        if current.state != "usable":
            raise UserSkillFailure("not_usable", 409, unusable_label(current))
        if current.current_version != version or (
            expected_digest is not None and current.payload.definition_digest != expected_digest
        ):
            raise UserSkillFailure("capability_candidate_stale", 409, "功能已更新，请重新打开")
        definition = UserSkillDefinition.model_validate(current.payload.definition)
        clean = {name: value for name, value in inputs.items() if value not in (None, "")}
        try:
            check_inputs(definition, clean)
        except InputInvalid as invalid:
            return needs_input(current, definition, clean, invalid.missing), False, None
        target = RunTarget(
            workflow_id=workflow_id(current.skill_id), version_label=str(version),
            name=current.display_name, purpose=current.payload.purpose,
            definition=definition, skill_id=current.skill_id, version=version,
        )
        outcome = await self._runner.run(target, clean, ctx, max_rows=max_rows)
        if outcome.pause is not None:
            await self._store.set_state(
                owner, current.skill_id, from_states=frozenset({"usable"}), to_state="paused",
                pause_reason=outcome.pause, expected_current_version=version,
            )
            _LOGGER.info("user_skill.paused skill=%s reason=%s", current.skill_id, outcome.pause)
        result = dict(outcome.result)
        result["exportable"] = await self._exportable(definition)
        # Inputs that look like credentials are never kept for a takeover.
        takeover = _takeover_allowed(outcome) and not _has_credential(clean)
        result["takeover_allowed"] = takeover
        if ctx.entry != "worker" and not _has_credential(clean):
            # An export has no chat to hand a failure over to.
            await self.sessions.put_run(key, outcome.run_id, _run_snapshot(
                current, definition, clean, outcome, takeover
            ))
        return result, outcome.status == "failed", outcome.error_code

    async def export_refusal(
        self, owner: UserSkillOwner, skill_id: str, version: int
    ) -> str | None:
        """None when the flow may be exported now, else a closed job refusal code."""

        current = await self.current(owner, skill_id)
        if current is None:
            return "user_skill_unavailable"
        if current.state != "usable" or current.current_version != version:
            return "user_skill_changed"
        definition = UserSkillDefinition.model_validate(current.payload.definition)
        if not await self._exportable(definition):
            return "user_skill_export_unsupported"
        return None

    async def choice(
        self, owner: UserSkillOwner, first: Sequence[str]
    ) -> dict[str, Any]:
        usable = await self.usable(owner)
        ordered = [v for sid in first for v in usable if v.skill_id == sid]
        ordered += sorted((v for v in usable if v.skill_id not in first),
                          key=lambda v: norm(v.display_name))
        return {
            "kind": "choice",
            "options": [
                {"skill_id": v.skill_id, "version": v.current_version, "name": v.display_name,
                 "purpose": v.payload.purpose}
                for v in ordered[:MAX_CHOICE_OPTIONS]
            ],
        }

    # ---------------------------------------------------------- takeover
    async def request_takeover(
        self, key: SessionKey, owner: UserSkillOwner, run_id: str
    ) -> bool:
        del owner  # the trusted session key already names the owner
        return await self.sessions.claim_takeover(key, run_id)

    async def take_takeover(
        self, key: SessionKey, owner: UserSkillOwner
    ) -> dict[str, Any] | None:
        context = await self.sessions.take_takeover(key)
        if context is None or await self.current(owner, str(context.get("skill_id"))) is None:
            return None
        return context


_PAUSE_LABELS = {
    "capability_changed": "依赖的查询已变化",
    "capability_unavailable": "依赖的查询已不可用",
}
_SAVE_FAILURES: dict[str, tuple[str, int, str]] = {
    "name_taken": ("name_taken", 409, "已经有同名功能，请换个名称"),
    "not_found": ("not_found", 404, "功能不存在"),
    "conflict": ("conflict", 409, "功能已被更新，请重新试运行"),
    "trial_expired": ("trial_expired", 410, "试运行已过期，请重新试运行"),
    "not_savable": ("not_savable", 409, "试运行没有成功，不能保存"),
    "not_admissible": ("not_admissible", 409, "依赖的查询已变化，请重新试运行"),
}


def _has_credential(value: Any) -> bool:
    if isinstance(value, str):
        return has_credential_marker(value)
    if isinstance(value, Mapping):
        return any(has_credential_marker(str(key)) or _has_credential(item)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_has_credential(item) for item in value)
    return False


def _form(definition: UserSkillDefinition) -> dict[str, Any]:
    return {"schema": params_schema(definition),
            "labels": {param.name: param.label for param in definition.params}}


def _require_exposable(definition: UserSkillDefinition) -> None:
    """A saved flow must be offerable to the model as a tool (same projection as the catalog)."""

    try:
        project_tool_input_schema(params_schema(definition))
    except ToolDefinitionRejected:
        raise UserSkillFailure(
            "trial_invalid", 422, "该功能的填写项无法提供给助手，请调整后再试"
        ) from None


def needs_input(
    current: UserSkillVersion,
    definition: UserSkillDefinition,
    prefill: Mapping[str, Any],
    missing: Sequence[str],
) -> dict[str, Any]:
    return {
        "kind": "needs_input",
        "skill_id": current.skill_id,
        "version": current.current_version,
        "name": current.display_name,
        "form": {**_form(definition), "prefill": dict(prefill)},
        "missing": list(missing) or [param.name for param in definition.params
                                     if param.required and param.name not in prefill],
    }


def _target(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    if raw.get("mode") == "new":
        return {"mode": "new"}
    skill_id = skill_uuid(raw.get("skill_id"))
    expected = raw.get("expected_current_version")
    if raw.get("mode") == "update" and skill_id is not None and type(expected) is int:
        return {"mode": "update", "skill_id": skill_id, "expected_current_version": expected}
    return None


def _row_failures(outcome: RunOutcome) -> bool:
    return any(
        row.get("status") == "failed"
        for table in outcome.result.get("tables", [])
        for row in table.get("rows", [])
    )


def _takeover_allowed(outcome: RunOutcome) -> bool:
    """One rule for the card and the persisted snapshot; a user check never hands over."""

    return (
        outcome.status != "completed"
        and not outcome.result.get("check_failed")
        and (outcome.error_code in TAKEOVER_ERRORS or _row_failures(outcome))
    )


def _savable(outcome: RunOutcome) -> tuple[bool, str | None]:
    # Saveable only when every step ran; per-item gaps or an incomplete list are
    # data-dependent, a stopped run (deadline, stop code, failed step) is not.
    run = outcome.run_result
    if (
        run is not None and run.status == "completed" and outcome.status != "failed"
        and not _incomplete_list(run)
    ):
        return True, None
    if run is not None and _incomplete_list(run):
        return False, "这次查到的列表不完整，暂不能保存，请稍后再试"
    return False, outcome.result.get("reason_label") or "试运行没有全部完成，请重试后再保存"


def _regenerate_savable(
    outcome: RunOutcome, definition: UserSkillDefinition
) -> tuple[bool, str | None]:
    """Stricter than a first save: every step and every per-item call must be proven."""

    savable, reason = _savable(outcome)
    if not savable:
        return savable, reason
    run = outcome.run_result
    assert run is not None

    def proven(step_id: str) -> bool:
        step = run.steps.get(step_id)
        items = None if step is None else step.items
        return bool(items) and all(item.status == "ok" for item in items or ())

    unproven = any(
        step.for_each is not None and not proven(step.step_id) for step in definition.steps
    )
    if unproven:
        return False, "这次没有足够的数据验证新合同，请换个填写内容再试"
    return True, None


def _incomplete_list(run: DefinitionRunResult) -> bool:
    """A failed ``complete`` check on a non-per-item step (PLAN §3.11: not saveable)."""

    if run.failed_assertion is None or run.failed_assertion[1] != "complete":
        return False
    step = run.steps.get(run.failed_assertion[0])
    return step is not None and step.items is None


_SNAPSHOT_ITEMS = 5
_SNAPSHOT_TEXT = 200


def _bounded(value: Any) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(text) <= _SNAPSHOT_TEXT else text[:_SNAPSHOT_TEXT] + "…"


def _run_snapshot(
    current: UserSkillVersion,
    definition: UserSkillDefinition,
    inputs: Mapping[str, Any],
    outcome: RunOutcome,
    takeover_allowed: bool,
) -> dict[str, Any]:
    """Trusted facts of this run for a takeover: version, failed calls and their inputs."""

    labels = {param.name: param.label for param in definition.params}
    failed_steps: list[dict[str, Any]] = []
    run = outcome.run_result
    if run is not None:
        compiled = {
            step.step_id: step
            for step in compile_definition(
                definition, workflow_id="snapshot", version=str(current.current_version)
            ).steps
        }
        labelled = {step.step_id: step for step in definition.steps}
        projected = {
            step_id: step_outcome.data
            for step_id, step_outcome in run.steps.items()
            if step_outcome.status == "ok" and step_outcome.data is not None
        }

        def arguments(step_id: str, item: Any) -> dict[str, Any]:
            step = compiled[step_id]
            values: dict[str, Any] = dict(step.static_arguments)
            for name, ref in getattr(step, "arguments", {}).items():
                try:
                    values[name] = _bounded(resolve_ref(ref, inputs, projected, item))
                except DefinitionReferenceError:
                    values[name] = None
            return values

        for step_id, step_outcome in run.steps.items():
            failed_items = [i for i in (step_outcome.items or ()) if i.status != "ok"]
            if step_outcome.status == "ok" and not failed_items:
                continue
            entry: dict[str, Any] = {
                "step_label": labelled[step_id].label,
                "capability_id": labelled[step_id].capability_id,
                "error_code": step_outcome.error_code,
                "failed_items": len(failed_items),
            }
            step = compiled[step_id]
            loop = getattr(step, "for_each", None)
            if loop is None:
                entry["arguments"] = arguments(step_id, None)
            else:
                try:
                    listed = resolve_ref(loop.items, inputs, projected, None)
                except DefinitionReferenceError:
                    listed = []
                entry["failed_item_arguments"] = [
                    {"error_code": item.error_code,
                     "arguments": arguments(step_id, listed[item.index])}
                    for item in failed_items[:_SNAPSHOT_ITEMS]
                    if isinstance(listed, list) and item.index < len(listed)
                ]
            failed_steps.append(entry)
    return {
        "skill_id": current.skill_id,
        "version": current.current_version,
        "skill_name": current.display_name,
        "inputs": [{"label": labels[name], "value": str(value)}
                   for name, value in inputs.items() if name in labels],
        "failed_steps": failed_steps,
        "status": outcome.status,
        "takeover_allowed": takeover_allowed,
    }


def _admin_context(owner: UserSkillOwner) -> RunContext:
    return RunContext(tenant_id=owner.tenant_id, ai_user_id=owner.ai_user_id,
                      session_id="admission", channel="web", entry="run", deadline=0.0)


__all__ = (
    "UserSkillFailure",
    "UserSkillService",
    "canonical_definition_json",
    "check_skill_texts",
    "needs_input",
    "norm",
    "skill_uuid",
    "unusable_label",
    "workflow_id",
)
