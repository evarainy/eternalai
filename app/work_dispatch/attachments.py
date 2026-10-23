"""Private attachment operations using the same current Work Object scope."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from app.ports.auth import Principal
from app.ports.work_object_attachment import (
    AttachmentDownload,
    AttachmentLinkRequest,
    AttachmentMutationResult,
    AttachmentPurpose,
    AttachmentView,
    WorkObjectAttachmentStorePort,
    validate_attachment,
)
from app.ports.work_object_dispatch import WorkDispatchError
from app.ports.work_object_recurrence import DispatchPreparationPort
from app.ports.work_object_scope import compute_visibility_scope

_LOGGER = logging.getLogger(__name__)


class WorkObjectAttachmentService:
    def __init__(
        self,
        store: WorkObjectAttachmentStorePort,
        dispatch: DispatchPreparationPort,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._dispatch = dispatch
        self._clock = clock

    async def upload(
        self,
        principal: Principal,
        *,
        filename: str,
        media_type: str,
        purpose: AttachmentPurpose,
        payload: bytes,
        idempotency_key: UUID,
    ) -> AttachmentView:
        digest = validate_attachment(filename, media_type, payload)
        scope = await self._dispatch.build_scope(principal)
        result = await self._store.upload(
            scope,
            filename=filename,
            media_type=media_type,
            purpose=purpose,
            payload=payload,
            sha256=digest,
            idempotency_key=idempotency_key,
            now=self._clock(),
        )
        _LOGGER.info(
            "work_attachment stage=uploaded attachment_id=%s size_bytes=%d",
            result.attachment_id,
            result.size_bytes,
        )
        return result

    async def list_for_task(
        self, principal: Principal, work_object_id: str
    ) -> list[AttachmentView]:
        scope = await self._dispatch.build_scope(principal)
        try:
            return await self._store.list_for_task(scope, work_object_id)
        except WorkDispatchError as exc:
            if exc.code not in {"organization_directory_missing", "organization_directory_stale"}:
                raise
            _LOGGER.info("work_attachment stage=read_scope_expired resource=list code=%s", exc.code)
        return await self._store.list_for_task(
            compute_visibility_scope(
                principal_tenant_id=scope.principal_tenant_id,
                principal_ai_user_id=scope.principal_ai_user_id,
                principal_directory_user_id=scope.principal_directory_user_id,
                principal_department_id=None,
            ),
            work_object_id,
        )

    async def link(
        self,
        principal: Principal,
        work_object_id: str,
        *,
        request: AttachmentLinkRequest,
        if_match: str,
        idempotency_key: UUID,
    ) -> AttachmentMutationResult:
        result = await self._store.link(
            await self._dispatch.build_scope(principal),
            work_object_id,
            request=request,
            if_match=if_match,
            idempotency_key=idempotency_key,
            now=self._clock(),
        )
        _LOGGER.info(
            "work_attachment stage=linked work_object_id=%s count=%d replayed=%s",
            work_object_id,
            len(request.attachment_ids),
            result.replayed,
        )
        return result

    async def detach(
        self,
        principal: Principal,
        work_object_id: str,
        attachment_id: UUID,
        *,
        if_match: str,
        idempotency_key: UUID,
    ) -> AttachmentMutationResult:
        result = await self._store.detach(
            await self._dispatch.build_scope(principal),
            work_object_id,
            attachment_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            now=self._clock(),
        )
        _LOGGER.info(
            "work_attachment stage=detached work_object_id=%s attachment_id=%s replayed=%s",
            work_object_id,
            attachment_id,
            result.replayed,
        )
        return result

    async def download(
        self, principal: Principal, work_object_id: str, attachment_id: UUID
    ) -> AttachmentDownload:
        scope = await self._dispatch.build_scope(principal)
        try:
            result = await self._store.download(scope, work_object_id, attachment_id)
        except WorkDispatchError as exc:
            if exc.code not in {"organization_directory_missing", "organization_directory_stale"}:
                raise
            _LOGGER.info(
                "work_attachment stage=read_scope_expired resource=download code=%s", exc.code
            )
            result = await self._store.download(
                compute_visibility_scope(
                    principal_tenant_id=scope.principal_tenant_id,
                    principal_ai_user_id=scope.principal_ai_user_id,
                    principal_directory_user_id=scope.principal_directory_user_id,
                    principal_department_id=None,
                ),
                work_object_id,
                attachment_id,
            )
        _LOGGER.info(
            "work_attachment stage=downloaded work_object_id=%s attachment_id=%s",
            work_object_id,
            attachment_id,
        )
        return result
