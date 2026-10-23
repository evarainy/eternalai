"""Internal work dispatch orchestration over organization and persistence ports."""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.organization_directory_access import (
    DEFAULT_MAX_AGE_S,
    DirectoryAccessError,
    FreshDirectoryView,
    read_fresh_directory_view,
)
from app.ports.auth import Principal
from app.ports.organization_directory import OrganizationDirectoryPort, OrganizationUserMembership
from app.ports.trace import TraceEvent, TracePort
from app.ports.work_object import (
    DispatchKind,
    DispatchReceipt,
    InternalWorkObjectRecord,
    ReminderChoice,
    WorkObjectStorePort,
)
from app.ports.work_object_dispatch import (
    DispatchOptionsView,
    DispatchRequest,
    DispatchResult,
    DispatchSubject,
    HandlingRelationRecord,
    PreparedDispatch,
    WorkDispatchError,
    dispatch_public_item,
    fingerprint,
    target_key,
)
from app.ports.work_object_lifecycle import (
    AssignCommand,
    LifecycleCommand,
    LifecycleEventsView,
    LifecycleMutationResult,
    LifecycleView,
    ReassignCommand,
    RemarkHistoryPage,
    RemarkMutationResult,
    RemarkView,
)
from app.ports.work_object_scope import (
    AuthorizedWorkObjectScope,
    is_department_head,
    is_leader,
    may_dispatch_to,
)

_LOGGER = logging.getLogger(__name__)


class WorkDispatchService:
    def __init__(
        self,
        *,
        store: WorkObjectStorePort,
        organization_directory: OrganizationDirectoryPort | None = None,
        trace_port: TracePort | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        directory_max_age_s: int = DEFAULT_MAX_AGE_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._organization_directory = organization_directory
        self._trace_port = trace_port
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._directory_max_age_s = directory_max_age_s
        self._monotonic = monotonic

    @staticmethod
    def subject(principal: Principal) -> DispatchSubject:
        return DispatchSubject(
            principal.org_ctx.tenant_id, principal.ai_user_id, principal.org_ctx.directory_user_id
        )

    async def _fresh(self, subject: DispatchSubject) -> FreshDirectoryView:
        if self._organization_directory is None or subject.tenant_id != "default":
            raise WorkDispatchError("organization_directory_unavailable")
        try:
            return await read_fresh_directory_view(
                self._organization_directory,
                max_age_s=self._directory_max_age_s,
                monotonic=self._monotonic,
            )
        except DirectoryAccessError as exc:
            raise WorkDispatchError(exc.code) from None

    @staticmethod
    def _recheck(fresh: FreshDirectoryView) -> None:
        try:
            fresh.recheck()
        except DirectoryAccessError as exc:
            raise WorkDispatchError(exc.code) from None

    @staticmethod
    def _member(subject: DispatchSubject, fresh: FreshDirectoryView) -> OrganizationUserMembership:
        members = fresh.memberships.get(subject.directory_user_id or "", [])
        if len(members) > 1:
            raise WorkDispatchError("directory_membership_ambiguous")
        if not members or members[0].department_id not in fresh.departments:
            raise WorkDispatchError("directory_membership_missing")
        return members[0]

    @staticmethod
    def _scope(
        subject: DispatchSubject, fresh: FreshDirectoryView | None = None
    ) -> AuthorizedWorkObjectScope:
        member = None
        if fresh is not None:
            members = fresh.memberships.get(subject.directory_user_id or "", [])
            if len(members) == 1 and members[0].department_id in fresh.departments:
                member = members[0]
        remaining = (
            None
            if fresh is None
            else fresh.max_age_s
            - max(
                (fresh.view.observed_at - fresh.view.source_fetched_at).total_seconds(),
                (fresh.view.observed_at - fresh.view.last_success_at).total_seconds(),
            )
        )
        heads = (
            [
                candidate
                for candidate in fresh.view.memberships
                if member is not None
                and candidate.department_id == member.department_id
                and is_department_head(candidate)
            ]
            if fresh is not None
            else []
        )
        return AuthorizedWorkObjectScope(
            principal_tenant_id=subject.tenant_id,
            principal_ai_user_id=subject.ai_user_id,
            principal_directory_user_id=subject.directory_user_id,
            principal_department_id=None if member is None else member.department_id,
            responsible_department_ids=(
                frozenset({member.department_id})
                if member is not None
                and is_department_head(member)
                and len(heads) == 1
                and heads[0].user_id == member.user_id
                else frozenset()
            ),
            directory_snapshot_version=None if fresh is None else fresh.view.snapshot_version,
            valid_until=None
            if fresh is None or remaining is None
            else fresh.started_at + remaining,
        )

    async def _read_scope(
        self, subject: DispatchSubject
    ) -> tuple[AuthorizedWorkObjectScope, str | None]:
        if self._organization_directory is None or subject.tenant_id != "default":
            return self._scope(subject), None
        try:
            fresh = await self._fresh(subject)
        except WorkDispatchError as exc:
            if exc.code in {"organization_directory_missing", "organization_directory_stale"}:
                return self._scope(subject), exc.code
            raise
        scope = self._scope(subject, fresh)
        try:
            self._member(subject, fresh)
        except WorkDispatchError as exc:
            if exc.code in {"directory_membership_missing", "directory_membership_ambiguous"}:
                return scope, exc.code
            raise
        return scope, None

    async def build_scope_for_subject(self, subject: DispatchSubject) -> AuthorizedWorkObjectScope:
        scope, _ = await self._read_scope(subject)
        return scope

    async def build_scope(self, principal: Principal) -> AuthorizedWorkObjectScope:
        # No directory adapter means no head privilege; authenticated direct identity survives.
        if self._organization_directory is None:
            return self._scope(self.subject(principal))
        return await self.build_scope_for_subject(self.subject(principal))

    @staticmethod
    def _validate_targets(
        subject: DispatchSubject, request: DispatchRequest, fresh: FreshDirectoryView
    ) -> None:
        actor = WorkDispatchService._member(subject, fresh)
        # Check the whole batch's authority before resolving any target's existence.
        for target in request.targets:
            if not may_dispatch_to(
                actor,
                target_kind=target.kind,
                target_department_id=target.department_id,
                target_directory_user_id=target.directory_user_id
                if target.kind == "user"
                else None,
            ):
                raise WorkDispatchError(
                    "cross_department_dispatch_denied"
                    if is_department_head(actor) or is_leader(actor)
                    else "not_department_head"
                )
        for target in request.targets:
            if target.department_id not in fresh.departments:
                raise WorkDispatchError("dispatch_target_department_missing")
            if target.kind == "user":
                members = fresh.memberships.get(target.directory_user_id, [])
                if len(members) > 1:
                    raise WorkDispatchError("dispatch_target_membership_ambiguous")
                if not members or members[0].department_id != target.department_id:
                    raise WorkDispatchError("dispatch_target_membership_missing")
            else:
                heads = [
                    m
                    for m in fresh.view.memberships
                    if m.department_id == target.department_id and is_department_head(m)
                ]
                if not heads:
                    raise WorkDispatchError("dispatch_department_head_missing")
                if len(heads) != 1 or len(fresh.memberships[heads[0].user_id]) != 1:
                    raise WorkDispatchError("dispatch_department_head_ambiguous")

    async def prepare_dispatch(
        self,
        subject: DispatchSubject,
        *,
        request: DispatchRequest,
        idempotency_key: UUID,
        scheduled_at: datetime | None = None,
    ) -> PreparedDispatch:
        fresh = await self._fresh(subject)
        self._validate_targets(subject, request, fresh)
        scope = self._scope(subject, fresh)
        now = self._clock()
        groups = (
            [[t] for t in request.targets]
            if request.dispatch_mode == "independent"
            else [request.targets]
        )
        records: list[InternalWorkObjectRecord] = []
        relations: list[HandlingRelationRecord] = []
        items = []
        for targets in groups:
            lead = targets[0] if request.dispatch_mode == "independent" else request.lead_target
            assert lead is not None
            work_id = self._id_factory()
            record = InternalWorkObjectRecord(
                work_object_id=work_id,
                state_authority="internal",
                source_system="eternalai",
                source_kind="manual_dispatch",
                source_ref=None,
                source_title=None,
                source_status=None,
                source_received_at=None,
                source_created_at=None,
                source_workflow_type_id=None,
                source_fetched_at=None,
                assignee_ai_user_id=None,
                assignee_display_name=fresh.departments[lead.department_id].display_name
                if lead.kind == "department"
                else None,
                tenant_id=subject.tenant_id,
                owner_department_id=lead.department_id,
                initiator_ai_user_id=subject.ai_user_id,
                target_kind=lead.kind,
                assignee_directory_user_id=lead.directory_user_id if lead.kind == "user" else None,
                title=request.title,
                kind=request.kind,
                requirement=request.requirement,
                receipt_requirement=request.receipt_requirement,
                due_at=request.due_at,
                reminder_choices=request.reminder_choices,
                status="assigned" if lead.kind == "user" else "department_pending",
                version=1,
                dispatch_model_version=2,
                dispatch_mode=request.dispatch_mode,
                completion_mode=request.completion_mode,
                handling_mark=None,
                handling_marked_by_ai_user_id=None,
                handling_marked_at=None,
                task_record_id=None,
                created_at=now,
                updated_at=now,
            )
            records.append(record)
            for target in targets:
                relations.append(
                    HandlingRelationRecord(
                        relation_id=uuid4(),
                        tenant_id=subject.tenant_id,
                        work_object_id=work_id,
                        target_kind=target.kind,
                        target_department_id=target.department_id,
                        target_directory_user_id=target.directory_user_id
                        if target.kind == "user"
                        else None,
                        role="single"
                        if request.dispatch_mode == "independent"
                        else ("lead" if target_key(target) == target_key(lead) else "collaborator"),
                        assignee_directory_user_id=target.directory_user_id
                        if target.kind == "user"
                        else None,
                        status="assigned" if target.kind == "user" else "department_pending",
                        created_at=now,
                        updated_at=now,
                    )
                )
            items.append(dispatch_public_item(record.model_dump(mode="json")))
        self._recheck(fresh)
        fp = fingerprint(request.model_dump(mode="json"))
        receipt = DispatchReceipt(
            tenant_id=subject.tenant_id,
            initiator_ai_user_id=subject.ai_user_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fp,
            result={"items": items, "created_count": len(items), "replayed": False},
            authorization_summary={"department_id": scope.principal_department_id},
            created_at=now,
        )
        assert scope.valid_until is not None
        return PreparedDispatch(
            subject,
            scope,
            request,
            fp,
            tuple(records),
            tuple(relations),
            receipt,
            tuple(request.attachment_ids),
            fresh.view.snapshot_version,
            scope.valid_until,
            scheduled_at,
        )

    async def _audit(
        self, subject: DispatchSubject, operation: str, *, reason: str | None = None
    ) -> None:
        if self._trace_port is None:
            raise WorkDispatchError("work_object_audit_unavailable")
        correlation_id = self._id_factory()
        try:
            await self._trace_port.record_event(
                TraceEvent(
                    trace_id=correlation_id,
                    task_id="dispatch:" + correlation_id,
                    session_id="dispatch:" + correlation_id,
                    tenant_id=subject.tenant_id,
                    ai_user_id=subject.ai_user_id,
                    event_type="user_action",
                    status="ok" if reason is None else "blocked",
                    error_code=None,
                    capability_id=None,
                    attributes={
                        "operation": operation,
                        "stage": "authorized" if reason is None else "denied",
                        **({"reason_code": reason} if reason is not None else {}),
                    },
                )
            )
        except Exception:
            _LOGGER.warning("work_dispatch stage=audit code=work_object_audit_unavailable")
            raise WorkDispatchError("work_object_audit_unavailable") from None

    async def _replay_receipt(
        self, principal: Principal, receipt: DispatchReceipt
    ) -> DispatchResult:
        scope = await self.build_scope(principal)
        try:
            return await self._store.replay_dispatch_receipt(receipt, scope)
        except WorkDispatchError as exc:
            if exc.code not in {"organization_directory_missing", "organization_directory_stale"}:
                raise
        return await self._store.replay_dispatch_receipt(
            receipt, self._scope(self.subject(principal))
        )

    async def dispatch_for_principal(
        self, principal: Principal, *, request: DispatchRequest, idempotency_key: UUID
    ) -> DispatchResult:
        started = self._monotonic()
        subject = self.subject(principal)
        try:
            receipt = await self._store.get_dispatch_receipt(
                tenant_id=subject.tenant_id,
                initiator_ai_user_id=subject.ai_user_id,
                idempotency_key=idempotency_key,
            )
            if receipt is not None:
                if receipt.request_fingerprint != fingerprint(request.model_dump(mode="json")):
                    raise WorkDispatchError("idempotency_key_reused")
                result = await self._replay_receipt(principal, receipt)
            else:
                prepared = await self.prepare_dispatch(
                    subject, request=request, idempotency_key=idempotency_key
                )
                await self._audit(prepared.subject, "dispatch")
                result = await self._store.create_prepared_dispatch(prepared)
        except WorkDispatchError as exc:
            if exc.code == "work_object_audit_unavailable":
                winner = await self._store.get_dispatch_receipt(
                    tenant_id=subject.tenant_id,
                    initiator_ai_user_id=subject.ai_user_id,
                    idempotency_key=idempotency_key,
                )
                if winner is None:
                    raise
                if winner.request_fingerprint != fingerprint(request.model_dump(mode="json")):
                    raise WorkDispatchError("idempotency_key_reused") from None
                result = await self._replay_receipt(principal, winner)
            else:
                if exc.code in {
                    "directory_membership_missing",
                    "directory_membership_ambiguous",
                    "not_department_head",
                    "cross_department_dispatch_denied",
                    "dispatch_target_membership_ambiguous",
                }:
                    await self._audit(subject, "dispatch", reason=exc.code)
                _LOGGER.warning(
                    "work_dispatch stage=dispatch code=%s elapsed_ms=%d",
                    exc.code,
                    int((self._monotonic() - started) * 1000),
                )
                raise
        _LOGGER.info(
            "work_dispatch stage=%s count=%d replayed=%s elapsed_ms=%d",
            "replayed" if result.replayed else "committed",
            result.created_count,
            result.replayed,
            int((self._monotonic() - started) * 1000),
        )
        return result

    async def list_dispatch_options(
        self,
        principal: Principal,
        *,
        kind: Literal["user", "department"],
        query: str = "",
        cursor: str | None = None,
        department_id: str | None = None,
        limit: int = 50,
    ) -> DispatchOptionsView:
        subject = self.subject(principal)
        if subject.tenant_id != "default":
            raise WorkDispatchError("directory_scope_denied")
        fresh = await self._fresh(subject)
        actor = self._member(subject, fresh)
        if kind not in {"user", "department"} or not 1 <= limit <= 100:
            raise WorkDispatchError("dispatch_options_request_invalid")
        if kind == "department" and not (is_department_head(actor) or is_leader(actor)):
            raise WorkDispatchError("not_department_head")
        if kind == "user" and department_id is not None:
            if department_id != actor.department_id and not is_leader(actor):
                raise WorkDispatchError(
                    "cross_department_dispatch_denied"
                    if is_department_head(actor)
                    else "not_department_head"
                )
            if department_id not in fresh.departments:
                raise WorkDispatchError("dispatch_target_department_missing")
        options = []
        unselectable: set[str] = set()
        if kind == "user":
            for member in fresh.view.memberships:
                if department_id and member.department_id != department_id:
                    continue
                if not may_dispatch_to(
                    actor,
                    target_kind="user",
                    target_department_id=member.department_id,
                    target_directory_user_id=member.user_id,
                ):
                    continue
                if (
                    query
                    and query.casefold() not in (member.display_name or member.user_id).casefold()
                ):
                    continue
                if (
                    len(fresh.memberships[member.user_id]) != 1
                    or member.department_id not in fresh.departments
                ):
                    unselectable.add(member.user_id)
                    continue
                options.append(
                    {
                        "kind": "user",
                        "directory_user_id": member.user_id,
                        "department_id": member.department_id,
                        "display_name": member.display_name or member.user_id,
                        "department_display_name": fresh.departments[
                            member.department_id
                        ].display_name,
                    }
                )
        else:
            for department in fresh.view.departments:
                if not may_dispatch_to(
                    actor, target_kind="department", target_department_id=department.department_id
                ):
                    continue
                if query and query.casefold() not in department.display_name.casefold():
                    continue
                heads = [
                    m
                    for m in fresh.view.memberships
                    if m.department_id == department.department_id and is_department_head(m)
                ]
                if len(heads) != 1 or len(fresh.memberships[heads[0].user_id]) != 1:
                    unselectable.add(department.department_id)
                    continue
                options.append(
                    {
                        "kind": "department",
                        "department_id": department.department_id,
                        "department_display_name": department.display_name,
                    }
                )
        options = [
            o
            for o in options
            if not query
            or query.casefold() in o.get("display_name", o["department_display_name"]).casefold()
        ]
        options.sort(key=lambda o: (o["department_id"], o.get("directory_user_id", "")))
        offset = 0
        if cursor:
            try:
                raw = base64.urlsafe_b64decode(cursor.encode()).decode()
                version, offset_text, cursor_kind = raw.split(":")
                offset = int(offset_text)
                if cursor_kind != kind or offset < 0:
                    raise ValueError
                if int(version) != fresh.view.snapshot_version:
                    raise WorkDispatchError("organization_directory_snapshot_changed")
            except (ValueError, UnicodeError):
                raise WorkDispatchError("dispatch_options_request_invalid") from None
        page = options[offset : offset + limit]
        next_cursor = (
            None
            if offset + limit >= len(options)
            else base64.urlsafe_b64encode(
                f"{fresh.view.snapshot_version}:{offset + limit}:{kind}".encode()
            ).decode()
        )
        self._recheck(fresh)
        return DispatchOptionsView(
            kind=kind,
            items=page,
            next_cursor=next_cursor,
            has_more=next_cursor is not None,
            unselectable_count=len(unselectable),
            snapshot_version=fresh.view.snapshot_version,
            can_dispatch_departments=is_department_head(actor) or is_leader(actor),
            can_dispatch_others=is_department_head(actor) or is_leader(actor),
        )

    async def get_lifecycle_for_principal(
        self, work_object_id: str, principal: Principal
    ) -> LifecycleView:
        scope, reason = await self._read_scope(self.subject(principal))
        try:
            view = await self._store.get_dispatch_lifecycle(work_object_id, scope)
        except WorkDispatchError as exc:
            if exc.code not in {"organization_directory_missing", "organization_directory_stale"}:
                raise
            _LOGGER.info(
                "work_dispatch stage=read_scope_expired resource=lifecycle code=%s", exc.code
            )
            reason = exc.code
            view = await self._store.get_dispatch_lifecycle(
                work_object_id, self._scope(self.subject(principal))
            )
        if reason is not None and not view.frozen:
            return view.model_copy(
                update={
                    "available_commands": [],
                    "unavailable_reason": reason,
                    "relations": [
                        relation.model_copy(update={"available_commands": []})
                        for relation in view.relations
                    ],
                }
            )
        return view

    async def command_lifecycle_for_principal(
        self,
        work_object_id: str,
        principal: Principal,
        *,
        command: LifecycleCommand,
        if_match: str,
        idempotency_key: UUID,
    ) -> LifecycleMutationResult:
        subject = self.subject(principal)
        fresh = await self._fresh(subject)
        member = self._member(subject, fresh)
        scope = self._scope(subject, fresh)
        record = await self._store.get_for_scope(work_object_id, scope)
        if record is None:
            raise WorkDispatchError("work_object_not_found")
        if (
            not isinstance(record, InternalWorkObjectRecord)
            or record.source_kind != "manual_dispatch"
        ):
            raise WorkDispatchError("work_object_lifecycle_unsupported")
        legacy_conversion_relation = None
        if record.dispatch_model_version is None and record.status not in {
            "completed",
            "withdrawn",
        }:
            legacy_conversion_relation = self._prepare_legacy_relation(record, scope, fresh)
        replacement = None
        replacement_department_name = None
        if isinstance(command, AssignCommand):
            memberships = fresh.memberships.get(command.directory_user_id, [])
            if len(memberships) > 1:
                raise WorkDispatchError("dispatch_target_membership_ambiguous")
            if not memberships or memberships[0].department_id != member.department_id:
                raise WorkDispatchError("work_object_action_forbidden")
        if isinstance(command, ReassignCommand):
            if (record.dispatch_mode or "independent") == "independent" and len(
                command.targets
            ) != 1:
                raise WorkDispatchError("work_object_lifecycle_request_invalid")
            request = DispatchRequest(
                kind=cast(DispatchKind, record.kind),
                title=cast(str, record.title),
                requirement=cast(str, record.requirement),
                receipt_requirement=cast(str, record.receipt_requirement),
                due_at=record.due_at,
                reminder_choices=cast(list[ReminderChoice], record.reminder_choices),
                targets=command.targets,
                dispatch_mode=record.dispatch_mode or "independent",
                completion_mode=record.completion_mode or "submit_completes",
                lead_target=command.lead_target,
            )
            prepared = await self.prepare_dispatch(
                subject, request=request, idempotency_key=idempotency_key
            )
            replacement_department_name = prepared.records[0].assignee_display_name
            replacement = tuple(
                r.model_copy(update={"work_object_id": work_object_id}) for r in prepared.relations
            )
        existing_event = await self._store.get_lifecycle_event_for_scope(
            work_object_id,
            scope,
            actor_ai_user_id=subject.ai_user_id,
            idempotency_key=idempotency_key,
        )
        if existing_event is None:
            await self._audit(subject, command.operation)
        self._recheck(fresh)
        result = await self._store.command_dispatch_lifecycle(
            work_object_id,
            scope,
            command=command,
            expected_etag=if_match,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint(command.model_dump(mode="json")),
            event_id=uuid4(),
            now=self._clock(),
            replacement_relations=replacement,
            replacement_department_name=replacement_department_name,
            legacy_conversion_relation=legacy_conversion_relation,
        )
        _LOGGER.info(
            "work_dispatch stage=lifecycle operation=%s task_id=%s replayed=%s",
            command.operation,
            work_object_id,
            result.replayed,
        )
        return result

    @staticmethod
    def _prepare_legacy_relation(
        record: InternalWorkObjectRecord,
        scope: AuthorizedWorkObjectScope,
        fresh: FreshDirectoryView,
    ) -> HandlingRelationRecord:
        department = record.owner_department_id
        if department not in fresh.departments:
            raise WorkDispatchError("work_object_legacy_conversion_blocked")
        assignee = record.assignee_directory_user_id
        if record.target_kind == "user":
            # Legacy user accept already bound accepted AI to this explicit directory target.
            # Preserve that historical pair even when the initiator performs this mutation.
            members = fresh.memberships.get(assignee or "", [])
            if len(members) != 1 or members[0].department_id != department:
                raise WorkDispatchError("work_object_legacy_conversion_blocked")
        else:
            heads = [
                m
                for m in fresh.view.memberships
                if m.department_id == department and is_department_head(m)
            ]
            if len(heads) != 1 or len(fresh.memberships[heads[0].user_id]) != 1:
                raise WorkDispatchError("work_object_legacy_conversion_blocked")
            assignee = None
            if record.accepted_by_ai_user_id is not None:
                if (
                    record.accepted_by_ai_user_id != scope.principal_ai_user_id
                    or scope.principal_department_id != department
                ):
                    raise WorkDispatchError("work_object_legacy_conversion_blocked")
                assignee = scope.principal_directory_user_id
        return HandlingRelationRecord(
            relation_id=uuid5(
                NAMESPACE_URL,
                "eternalai:legacy:" + str(record.tenant_id) + ":" + record.work_object_id,
            ),
            tenant_id=scope.principal_tenant_id,
            work_object_id=record.work_object_id,
            target_kind=cast(Literal["user", "department"], record.target_kind),
            target_department_id=department,
            target_directory_user_id=record.assignee_directory_user_id
            if record.target_kind == "user"
            else None,
            role="single",
            assignee_directory_user_id=assignee,
            assignee_ai_user_id=record.accepted_by_ai_user_id,
            status="in_progress"
            if record.accepted_at
            else "assigned"
            if assignee
            else "department_pending",
            accepted_at=record.accepted_at,
            accepted_by_ai_user_id=record.accepted_by_ai_user_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    async def list_lifecycle_events_for_principal(
        self,
        work_object_id: str,
        principal: Principal,
        *,
        after_version: int | None = None,
        limit: int = 50,
    ) -> LifecycleEventsView:
        scope = await self.build_scope(principal)
        try:
            return await self._store.get_dispatch_events(
                work_object_id, scope, after_version=after_version or 0, limit=limit
            )
        except WorkDispatchError as exc:
            if exc.code not in {"organization_directory_missing", "organization_directory_stale"}:
                raise
            _LOGGER.info("work_dispatch stage=read_scope_expired resource=events code=%s", exc.code)
        return await self._store.get_dispatch_events(
            work_object_id,
            self._scope(self.subject(principal)),
            after_version=after_version or 0,
            limit=limit,
        )

    async def get_remark(self, work_object_id: str, principal: Principal) -> RemarkView:
        scope = await self.build_scope(principal)
        try:
            return await self._store.get_remark(work_object_id, scope)
        except WorkDispatchError as exc:
            if exc.code not in {"organization_directory_missing", "organization_directory_stale"}:
                raise
            _LOGGER.info("work_dispatch stage=read_scope_expired resource=remark code=%s", exc.code)
        return await self._store.get_remark(work_object_id, self._scope(self.subject(principal)))

    async def edit_remark(
        self,
        work_object_id: str,
        principal: Principal,
        *,
        text: str,
        if_match: str,
        idempotency_key: UUID,
    ) -> RemarkMutationResult:
        if len(text) > 2000:
            raise WorkDispatchError("work_object_lifecycle_request_invalid")
        return await self._store.edit_remark(
            work_object_id,
            await self.build_scope(principal),
            text=text,
            expected_etag=if_match,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint({"text": text}),
            now=self._clock(),
        )

    async def list_remark_history(
        self,
        work_object_id: str,
        principal: Principal,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> RemarkHistoryPage:
        try:
            before = None if cursor is None else int(base64.urlsafe_b64decode(cursor).decode())
        except (ValueError, UnicodeError):
            raise WorkDispatchError("work_object_lifecycle_request_invalid") from None
        scope = await self.build_scope(principal)
        try:
            return await self._store.list_remark_history(
                work_object_id, scope, before_version=before, limit=limit
            )
        except WorkDispatchError as exc:
            if exc.code not in {"organization_directory_missing", "organization_directory_stale"}:
                raise
            _LOGGER.info(
                "work_dispatch stage=read_scope_expired resource=remark_history code=%s", exc.code
            )
        return await self._store.list_remark_history(
            work_object_id, self._scope(self.subject(principal)), before_version=before, limit=limit
        )
