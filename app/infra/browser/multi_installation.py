"""Trusted multi-publication installation over the existing exact-digest lanes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from sqlalchemy import text

from app.browser_skill.registry import BrowserPublicationCatalog, PublicationCatalogError
from app.infra.browser.composition import BrowserVerticalComponents
from app.ports.auth import Principal
from app.ports.browser_chat import BrowserChatError, BrowserRunResponse
from app.ports.browser_run_store import BrowserRunStoreError, checked_run_id
from app.ports.response_envelope import ResponseEnvelope


class BrowserVerticalCollection:
    """Select an installed lane using the durable publication or Run digest.

    The database lookup only selects a lane. Each lane still performs its own
    current publication, binding, grant, Policy and Run authorization checks.
    Tuple order declares the compatible default Skill for /me and older callers.
    """

    def __init__(self, components: tuple[BrowserVerticalComponents, ...]) -> None:
        if not components or any(type(item) is not BrowserVerticalComponents
                                 or item._seed.source_kind != "registered_site"
                                 for item in components):
            raise ValueError("browser_multi_installation_invalid")
        default = components[0]
        if any(item._sessions is not default._sessions
               or item._tenant_id != default._tenant_id for item in components):
            raise ValueError("browser_multi_authority_mismatch")
        catalog = BrowserPublicationCatalog(tuple(item._seed for item in components))
        by_digest = {item._seed.digest: item for item in components}
        if len(by_digest) != len(components):
            raise ValueError("browser_multi_installation_invalid")
        self._components = components
        self._default = default
        self._catalog = catalog
        self._by_digest: Mapping[str, BrowserVerticalComponents] = by_digest
        self._skills = frozenset(item._seed.skill.skill_id for item in components)
        self.chat = self if default.chat is not None else None
        self.submission = self

    @property
    def skill_id(self) -> str:
        return self._default._seed.skill.skill_id

    def owner_cache_scope(self, principal: Principal) -> tuple[str, str]:
        return self._default.submission.owner_cache_scope(principal)

    def can_select_skill(self, skill_id: str) -> bool:
        """Installed-name check only; durable active state and grants follow."""
        return type(skill_id) is str and skill_id in self._skills

    def _owner(self, principal: Principal, bound_session: str) -> tuple[str, str, str]:
        owner = self._default.submission._owner(principal, bound_session)
        if owner.tenant_id != self._default._tenant_id:
            raise BrowserChatError("browser_skill_not_configured", http_status=403)
        return owner.tenant_id, owner.user_id, owner.session_id

    async def _active(
        self, principal: Principal, bound_session: str, skill_id: str,
    ) -> BrowserVerticalComponents:
        if type(skill_id) is not str or skill_id not in self._skills:
            raise BrowserChatError("browser_skill_not_configured", http_status=409)
        tenant, _, _ = self._owner(principal, bound_session)
        async with self._default._sessions() as session:
            rows = (await session.execute(text(
                "SELECT publication_digest FROM browser_publications"
                " WHERE tenant_id=:tenant AND skill_id=:skill AND state='active'"
            ), {"tenant": tenant, "skill": skill_id})).scalars().all()
        if len(rows) != 1:
            raise BrowserChatError("browser_publication_unavailable")
        digest = bytes(rows[0]).hex()
        lane = self._by_digest.get(digest)
        if lane is None:
            raise BrowserChatError("browser_publication_unavailable")
        try:
            self._catalog.resolve_exact(
                skill_id, lane._seed.skill.version, digest,
            )
        except PublicationCatalogError:
            raise BrowserChatError("browser_publication_unavailable") from None
        return lane

    async def _run(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str,
    ) -> BrowserVerticalComponents:
        tenant, user, session_id = self._owner(principal, bound_session)
        try:
            checked_run_id(task_id)
            checked_run_id(run_id)
        except BrowserRunStoreError:
            raise BrowserChatError("browser_run_not_found", http_status=404)
        async with self._default._sessions() as session:
            rows = (await session.execute(text(
                "SELECT publication_digest FROM browser_runs WHERE tenant_id=:tenant"
                " AND ai_user_id=:user AND session_id=:session"
                " AND task_id=:task AND run_id=:run"
            ), {"tenant": tenant, "user": user, "session": session_id,
                "task": task_id, "run": run_id})).scalars().all()
        if len(rows) != 1:
            raise BrowserChatError("browser_run_not_found", http_status=404)
        digest = bytes(rows[0]).hex()
        try:
            self._catalog.by_digest(digest)
        except PublicationCatalogError:
            raise BrowserChatError("browser_publication_unavailable") from None
        return self._by_digest[digest]

    async def start(
        self, *, channel: Literal["web", "cli", "api", "mock"], principal: Principal,
        bound_session: str, message: str, client_capabilities: dict[str, Any],
        client_request_id: str, skill_id: str,
    ) -> ResponseEnvelope:
        lane = await self._active(principal, bound_session, skill_id)
        if lane.chat is None:
            raise BrowserChatError("browser_chat_unavailable")
        return await lane.chat.start(
            channel=channel, principal=principal, bound_session=bound_session,
            message=message, client_capabilities=client_capabilities,
            client_request_id=client_request_id, skill_id=skill_id,
        )

    async def submit(
        self, *, principal: Principal, bound_session: str, skill_id: str,
        skill_version: str, arguments: dict[str, Any], client_request_id: str,
    ) -> ResponseEnvelope:
        lane = await self._active(principal, bound_session, skill_id)
        if skill_version != lane._seed.skill.version:
            raise BrowserChatError("browser_publication_unavailable")
        return await lane.submission.submit(
            principal=principal, bound_session=bound_session, skill_id=skill_id,
            skill_version=skill_version, arguments=arguments,
            client_request_id=client_request_id,
        )

    async def get(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str,
    ) -> BrowserRunResponse:
        lane = await self._run(principal, bound_session, task_id, run_id)
        return await lane.submission.get(principal, bound_session, task_id, run_id)

    async def cancel(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str,
    ) -> BrowserRunResponse:
        lane = await self._run(principal, bound_session, task_id, run_id)
        return await lane.submission.cancel(principal, bound_session, task_id, run_id)

    async def decide_write(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str,
        decision: Literal["confirmed", "rejected"],
    ) -> BrowserRunResponse:
        lane = await self._run(principal, bound_session, task_id, run_id)
        return await lane.submission.decide_write(
            principal, bound_session, task_id, run_id, decision,
        )

    async def read_artifact(
        self, principal: Principal, bound_session: str, task_id: str,
        run_id: str, reference: str,
    ) -> tuple[str, bytes]:
        lane = await self._run(principal, bound_session, task_id, run_id)
        return await lane.submission.read_artifact(
            principal, bound_session, task_id, run_id, reference,
        )

    async def run_business_ready(self) -> tuple[object, ...]:
        completed: list[object] = []
        failed = False
        for lane in self._components:
            try:
                completed.extend(await lane.run_business_ready())
            except Exception:
                failed = True
        if failed:
            raise RuntimeError("browser_worker_pass_failed")
        return tuple(completed)

    async def cleanup_ready(self) -> None:
        failed = False
        for lane in self._components:
            try:
                await lane.cleanup_ready()
            except Exception:
                failed = True
        if failed:
            raise RuntimeError("browser_worker_pass_failed")

    async def start_resources(self) -> None:
        started: list[BrowserVerticalComponents] = []
        try:
            for lane in self._components:
                await lane.start_resources()
                started.append(lane)
        except BaseException:
            for lane in reversed(started):
                await lane.stop_resources()
            raise

    async def stop_resources(self) -> None:
        failed = False
        for lane in reversed(self._components):
            try:
                await lane.stop_resources()
            except Exception:
                failed = True
        if failed:
            raise RuntimeError("browser_worker_shutdown_pending")
