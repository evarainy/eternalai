"""Use cases of personal flows shared by the chat runtime and the HTTP API.

Owner and session keys always come from the trusted caller. Proposal, trial and
run snapshots stay in this process's session memory; only saved versions are
persisted.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from app.knowledge.capability_selection import capability_fingerprint
from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
from app.ports.user_skills import (
    UserSkillError,
    UserSkillOwner,
    UserSkillState,
    UserSkillStorePort,
    UserSkillVersion,
    UserSkillVersionPayload,
)
from app.user_skills.definition import (
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
    check_texts,
    compile_trial,
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
from app.user_skills.session_store import SessionKey, TrialSnapshot, UserSkillSessionStore
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


class UserSkillService:
    def __init__(
        self,
        *,
        store: UserSkillStorePort,
        sessions: UserSkillSessionStore,
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

    async def list(self, owner: UserSkillOwner) -> list[dict[str, Any]]:
        versions = await self._store.list_current(
            owner, frozenset({"usable", "paused", "disabled"})
        )
        heads = {head.skill_id: head for head in await self._store.list_for_owner(owner)}
        items: list[dict[str, Any]] = []
        for version in versions:
            definition = UserSkillDefinition.model_validate(version.payload.definition)
            head = heads.get(version.skill_id)
            items.append({
                "skill_id": version.skill_id,
                "name": version.display_name,
                "purpose": version.payload.purpose,
                "state": version.state,
                "pause_reason_label": (
                    None if head is None or head.pause_reason is None
                    else _PAUSE_LABELS[head.pause_reason]
                ),
                "current_version": version.current_version,
                "updated_at": None if head is None else head.updated_at.isoformat(),
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
        version = await self._store.get_current(owner, skill_id)
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
            owner, skill_id, from_states=source, to_state=state, pause_reason=None,
            expected_current_version=expected_current_version,
        )
        if head is None:
            raise UserSkillFailure("conflict", 409, "功能状态已变化，请刷新后重试")
        return {"skill_id": head.skill_id, "state": head.state,
                "current_version": head.current_version}

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

        if capability_type == "query":
            self.sessions.record(
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
            self.sessions.record(
                key, turn_id=turn_id, capability_id=leaf_id,
                capability_fingerprint=capability_fingerprint(spec),
                arguments=dict(static_arguments), data=dict(part),
            )

    def has_records(self, key: SessionKey) -> bool:
        return self.sessions.has_records(key)

    async def summary(self, key: SessionKey, user_texts: Sequence[str]) -> dict[str, Any]:
        records, _ = self.sessions.records(key)
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
        records, dropped = self.sessions.records(key)
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
        self.sessions.put_proposal(key, view["proposal_id"], internal)
        _LOGGER.info(
            "user_skill.proposal records=%d steps=%d bindings=%d tables=%d",
            len(records), len(view["steps"]), len(view["bindings"]),
            len(view["column_options"]),
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
        internal = self.sessions.proposal(key, proposal_id)
        if internal is None:
            raise UserSkillFailure("proposal_expired", 410, "提案已过期，请重新说「保存为功能」")
        target_raw = raw.get("target")
        try:
            choices = TrialChoices.model_validate(
                {k: v for k, v in raw.items() if k != "target"}
            )
        except ValidationError:
            raise UserSkillFailure("trial_invalid", 422, "卡片内容不完整") from None
        target = _target(target_raw)
        if target is None:
            raise UserSkillFailure("trial_invalid", 422, "请选择新建还是更新已有功能")
        try:
            definition, inputs = compile_trial(internal, choices)
        except ProposalRejected as rejected:
            raise UserSkillFailure(rejected.code, 422, rejected.label) from None
        trial_id = str(uuid4())
        run_target = RunTarget(
            workflow_id=workflow_id(None, trial_id), version_label=f"trial-{trial_id}",
            name=choices.name, purpose=choices.purpose, definition=definition,
        )
        async with self.sessions.lock(key):
            try:
                outcome = await self._runner.run(run_target, inputs, ctx)
            except InputInvalid:
                raise UserSkillFailure("input_invalid", 422, "请把每次要填写的内容填完整") from None
        savable, reason = _savable(outcome)
        payload = {
            "definition": definition.model_dump(mode="json", by_alias=True),
            "name": choices.name, "purpose": choices.purpose, "rules": choices.rules,
            "target": target, "inputs": inputs,
        }
        self.sessions.put_trial(key, TrialSnapshot(
            trial_id=trial_id, proposal_id=proposal_id, created=self.sessions.now(),
            payload=payload, savable=savable,
        ))
        result = dict(outcome.result)
        result["trial_id"] = trial_id
        return {"trial_id": trial_id, "result": result, "savable": savable,
                "not_savable_reason": reason}

    async def save(
        self, key: SessionKey, owner: UserSkillOwner, trial_id: str
    ) -> dict[str, Any]:
        async with self.sessions.lock(key):
            trial = self.sessions.trial(key, trial_id)
            if trial is None:
                raise UserSkillFailure("trial_expired", 410, "试运行已过期，请重新试运行")
            if trial.state == "saved":
                assert trial.saved_result is not None
                return trial.saved_result
            if trial.state == "saving":
                raise UserSkillFailure("trial_busy", 409, "正在保存，请稍候")
            if not trial.savable:
                raise UserSkillFailure("not_savable", 409, "试运行没有成功，不能保存")
            payload = trial.payload
            definition = UserSkillDefinition.model_validate(payload["definition"])
            internal = self.sessions.proposal(key, trial.proposal_id)
            samples = set() if internal is None else set(internal["samples"])
            try:
                check_texts([payload["name"], payload["purpose"], payload["rules"]], samples)
            except ProposalRejected as rejected:
                raise UserSkillFailure(rejected.code, 422, rejected.label) from None
            admission = await self._runner.admit(definition, _admin_context(owner))
            if admission.error_code is not None:
                raise UserSkillFailure("not_admissible", 409, "依赖的查询已变化，请重新试运行")
            version_payload = UserSkillVersionPayload(
                definition=definition.model_dump(mode="json", by_alias=True),
                definition_digest=definition_digest(definition),
                purpose=payload["purpose"],
                business_rules=payload["rules"],
            )
            trial.state = "saving"
            try:
                target = payload["target"]
                if target["mode"] == "new":
                    head = await self._store.create(owner, payload["name"], version_payload)
                else:
                    head = await self._store.add_version(
                        owner, target["skill_id"], target["expected_current_version"],
                        version_payload,
                    )
            except UserSkillError as exc:
                trial.state = "ready"
                if exc.code == "name_taken":
                    raise UserSkillFailure(
                        "name_taken", 409, "已经有同名功能，请换个名称"
                    ) from None
                if exc.code == "not_found":
                    raise UserSkillFailure("not_found", 404, "功能不存在") from None
                if exc.code == "conflict":
                    raise UserSkillFailure(
                        "conflict", 409, "功能已被更新，请重新试运行"
                    ) from None
                raise
            except BaseException:
                trial.state = "ready"
                raise
            trial.state = "saved"
            trial.saved_result = {"skill_id": head.skill_id, "version": head.current_version,
                                  "state": head.state}
            _LOGGER.info("user_skill.saved skill=%s version=%d mode=%s",
                         head.skill_id, head.current_version, target["mode"])
            return trial.saved_result

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

        current = await self._store.get_current(owner, skill_id)
        if current is None:
            raise UserSkillFailure("not_found", 404, "功能不存在")
        if current.state != "usable":
            raise UserSkillFailure(
                "not_usable", 409,
                "该功能已暂停，需要重新查询并保存新版本" if current.state == "paused"
                else "该功能已停用",
            )
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
            workflow_id=workflow_id(skill_id), version_label=str(version),
            name=current.display_name, purpose=current.payload.purpose,
            definition=definition, skill_id=skill_id, version=version,
        )
        outcome = await self._runner.run(target, clean, ctx, max_rows=max_rows)
        if outcome.pause is not None:
            await self._store.set_state(
                owner, skill_id, from_states=frozenset({"usable"}), to_state="paused",
                pause_reason=outcome.pause, expected_current_version=version,
            )
            _LOGGER.info("user_skill.paused skill=%s reason=%s", skill_id, outcome.pause)
        result = dict(outcome.result)
        result["exportable"] = await self._exportable(definition)
        result["takeover_allowed"] = (
            outcome.status != "completed"
            and (outcome.error_code in TAKEOVER_ERRORS or _row_failures(outcome))
        )
        self.sessions.put_run(key, outcome.run_id, _run_snapshot(current, definition, clean,
                                                                 outcome))
        return result, outcome.status == "failed", outcome.error_code

    async def export_refusal(
        self, owner: UserSkillOwner, skill_id: str, version: int
    ) -> str | None:
        """None when the flow may be exported now, else a closed job refusal code."""

        current = await self._store.get_current(owner, skill_id)
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
    def request_takeover(self, key: SessionKey, run_id: str) -> bool:
        snapshot = self.sessions.run(key, run_id)
        if snapshot is None or not snapshot.get("takeover_allowed"):
            return False
        self.sessions.set_takeover(key, snapshot)
        return True

    def take_takeover(self, key: SessionKey) -> dict[str, Any] | None:
        return self.sessions.take_takeover(key)


_PAUSE_LABELS = {
    "capability_changed": "依赖的查询已变化",
    "capability_unavailable": "依赖的查询已不可用",
}


def _form(definition: UserSkillDefinition) -> dict[str, Any]:
    return {"schema": params_schema(definition),
            "labels": {param.name: param.label for param in definition.params}}


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
    if (
        raw.get("mode") == "update"
        and isinstance(raw.get("skill_id"), str)
        and isinstance(raw.get("expected_current_version"), int)
    ):
        return {"mode": "update", "skill_id": raw["skill_id"],
                "expected_current_version": raw["expected_current_version"]}
    return None


def _row_failures(outcome: RunOutcome) -> bool:
    return any(
        row.get("status") == "failed"
        for table in outcome.result.get("tables", [])
        for row in table.get("rows", [])
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
        "takeover_allowed": outcome.status != "completed",
    }


def _admin_context(owner: UserSkillOwner) -> RunContext:
    return RunContext(tenant_id=owner.tenant_id, ai_user_id=owner.ai_user_id,
                      session_id="admission", channel="web", entry="run", deadline=0.0)


__all__ = (
    "UserSkillFailure",
    "UserSkillService",
    "canonical_definition_json",
    "needs_input",
    "norm",
    "workflow_id",
)
