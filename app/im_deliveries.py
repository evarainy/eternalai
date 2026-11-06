"""A person's explicit preview, one IM attempt, and the recipient's receipts."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from app.ports.auth import Principal
from app.ports.communication import CommunicationDeliveryPort, DeliveryResult
from app.ports.file_artifacts import FileArtifactError, FileArtifactSharePort
from app.ports.im_deliveries import (
    DeliveryPrepare,
    DeliveryRecipient,
    DeliveryRecord,
    DeliveryVerification,
    DeliveryView,
    IMDeliveryError,
    IMDeliveryStorePort,
    confirmation_digest,
)
from app.ports.personal_agents import AgentReplyPort

_LOGGER = logging.getLogger("uvicorn.error.im_deliveries")


class IMDeliveryService:
    def __init__(
        self, store: IMDeliveryStorePort, sender: CommunicationDeliveryPort,
        files: FileArtifactSharePort | None = None,
    ) -> None:
        self._store = store
        self._sender = sender
        self._files = files
        self._agent_replies: AgentReplyPort | None = None

    @property
    def sender(self) -> CommunicationDeliveryPort:
        return self._sender

    def set_agent_replies(self, service: AgentReplyPort | None) -> None:
        self._agent_replies = service

    async def recipients(self, principal: Principal) -> list[DeliveryRecipient]:
        return await self._store.recipients(principal)

    @staticmethod
    def _same_request(record: DeliveryRecord, request: DeliveryPrepare) -> None:
        if (record.recipient_ai_user_id, record.note, record.send_expires_at,
                record.artifact_id, record.grant_expires_at) != (
            request.recipient_ai_user_id, request.note, request.send_expires_at,
            request.artifact_id, request.grant_expires_at,
        ):
            raise IMDeliveryError("im_delivery_idempotency_conflict")

    async def prepare(self, principal: Principal, request: DeliveryPrepare) -> DeliveryView:
        file = None
        if request.artifact_id is not None:
            if self._files is None:
                raise IMDeliveryError("file_artifact_unavailable", 503)
            try:
                file = await self._files.share_metadata(principal, request.artifact_id)
            except FileArtifactError as error:
                status = {"file_artifact_not_found": 404, "file_artifact_expired": 410}
                raise IMDeliveryError(error.code, status.get(error.code, 503)) from None
            grant = request.grant_expires_at or file.expires_at
            if not request.send_expires_at <= grant <= file.expires_at:
                raise IMDeliveryError("im_delivery_expiry_invalid", 422)
            request = request.model_copy(update={"grant_expires_at": grant.astimezone(UTC)})
        elif request.grant_expires_at is not None:
            raise IMDeliveryError("im_delivery_expiry_invalid", 422)
        existing = await self._store.find_request(principal, request.idempotency_key)
        if existing is not None:
            self._same_request(existing, request)
            return await self.get(principal, existing.delivery_id)
        now = datetime.now(UTC)
        if request.send_expires_at <= now:
            raise IMDeliveryError("im_delivery_expiry_invalid", 422)
        if request.recipient_ai_user_id == principal.ai_user_id:
            raise IMDeliveryError("im_delivery_recipient_invalid", 422)
        initiator, recipient = await self._store.bindings(principal, request.recipient_ai_user_id)
        delivery_id = uuid4()
        try:
            card = self._sender.prepare_private_card(
                delivery_id=delivery_id, initiator_label=principal.display_name,
                recipient_im_user_id=recipient.im_user_id, note=request.note,
            )
        except ValueError:
            raise IMDeliveryError("im_delivery_mapping_invalid") from None
        if file is not None:
            card = replace(card, card_content=(
                f"{card.card_content}\n\n文件：{file.filename}（{file.size_bytes} 字节）\n"
                f"SHA256：{file.sha256.hex()[:12]}\n"
                f"授权截止：{cast(datetime, request.grant_expires_at).isoformat()}"
            ))
        record = DeliveryRecord(
            delivery_id=delivery_id, tenant_id=principal.org_ctx.tenant_id,
            initiator_ai_user_id=principal.ai_user_id,
            recipient_ai_user_id=recipient.ai_user_id,
            recipient_binding_id=recipient.binding_id, recipient_mapping_version=recipient.version,
            provider=card.provider, im_instance_id=card.im_instance_id, realm_id=card.realm_id,
            sender_bot_id=card.sender_bot_id, target_conversation_ref=card.target_conversation_ref,
            card_content=card.card_content, note=request.note, confirmation_sha256=b"",
            idempotency_key=request.idempotency_key, state="prepared", created_at=now,
            confirmed_at=None, send_expires_at=request.send_expires_at.astimezone(UTC),
            queue_job_id=None, send_attempt_id=None, attempt_started_at=None,
            remote_message_id=None, accepted_at=None, result_code=None, updated_at=now,
            artifact_id=request.artifact_id,
            artifact_sha256=None if file is None else file.sha256,
            grant_expires_at=request.grant_expires_at,
        )
        saved = await self._store.prepare(replace(
            record, confirmation_sha256=confirmation_digest(record, initiator, recipient),
        ))
        self._same_request(saved, request)
        _LOGGER.info("im_delivery_prepared delivery_id=%s artifact_id=%s",
                     str(saved.delivery_id)[:12], str(saved.artifact_id)[:12])
        return await self.get(principal, saved.delivery_id)

    async def get(self, principal: Principal, delivery_id: UUID) -> DeliveryView:
        return await self._store.get(principal, delivery_id)

    async def list(self, principal: Principal) -> list[DeliveryView]:
        return await self._store.list(principal)

    async def confirm(
        self, principal: Principal, delivery_id: UUID, confirmation_sha256: str,
    ) -> DeliveryView:
        fingerprint = bytes.fromhex(confirmation_sha256)
        record = await self._store.confirm(principal, delivery_id, fingerprint)
        _LOGGER.info(
            "im_delivery_confirmed delivery_id=%s",
            str(record.delivery_id)[:12],
        )
        return await self.get(principal, delivery_id)

    async def cancel(self, principal: Principal, delivery_id: UUID) -> DeliveryView:
        await self._store.cancel(principal, delivery_id)
        _LOGGER.info("im_delivery_cancelled_or_revoked delivery_id=%s", str(delivery_id)[:12])
        return await self.get(principal, delivery_id)

    async def verify(
        self, principal: Principal, delivery_id: UUID, request: DeliveryVerification,
    ) -> DeliveryView:
        await self._store.verify(principal, delivery_id, request)
        _LOGGER.info("im_delivery_verified delivery_id=%s result=%s", delivery_id, request.result)
        return await self.get(principal, delivery_id)

    async def send(self, delivery_id: UUID) -> None:
        if await self._store.origin_inbox(delivery_id) is None:
            claimed = await self._store.claim(delivery_id)
        else:
            if self._agent_replies is None:
                raise IMDeliveryError("personal_agent_unavailable", 503)
            claimed = await self._agent_replies.claim_reply(delivery_id)
        if claimed is None:
            return
        record, card = claimed
        attempt_id = cast(UUID, record.send_attempt_id)
        _LOGGER.info(
            "im_delivery_attempt delivery_id=%s queue_job_id=%s attempt_id=%s",
            delivery_id, record.queue_job_id, attempt_id,
        )
        try:
            result = await self._sender.send_message_once(card)
        except asyncio.CancelledError:
            # The committed attempt remains sending. Recovery can only read IM.
            _LOGGER.info("im_delivery_interrupted delivery_id=%s attempt_id=%s",
                         delivery_id, attempt_id)
            raise
        await self._store.finish(delivery_id, attempt_id, result)
        _LOGGER.info(
            "im_delivery_result delivery_id=%s attempt_id=%s code=%s remote_message_id=%s",
            delivery_id, attempt_id, result.result_code, result.remote_message_id,
        )

    async def _reconcile(self, delivery_id: UUID) -> None:
        pending = await self._store.uncertain(delivery_id)
        if pending is None:
            return
        record, card = pending
        result = await self._sender.reconcile_message(card, delivery_id)
        if result.state != "accepted":
            result = DeliveryResult("unknown", None, result.result_code)
        await self._store.finish(delivery_id, cast(UUID, record.send_attempt_id), result)
        _LOGGER.info("im_delivery_reconciled delivery_id=%s code=%s remote_message_id=%s",
                     delivery_id, result.result_code, result.remote_message_id)

    async def reconcile(self, principal: Principal, delivery_id: UUID) -> DeliveryView:
        view = await self.get(principal, delivery_id)
        if view.viewer_role != "initiator":
            raise IMDeliveryError("im_delivery_not_found", 404)
        await self._reconcile(delivery_id)
        return await self.get(principal, delivery_id)

    async def maintain(self) -> None:
        await self._store.recover_ready()
        for delivery_id in await self._store.pending_reconciliation():
            await self._reconcile(delivery_id)
