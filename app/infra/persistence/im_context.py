"""Read the verified mapping only; this adapter never provisions or rebinds accounts."""

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ports.auth import Principal
from app.ports.communication import IMAccountBinding
from app.ports.im_context import IMContextError


class PostgreSQLIMAccountBindingStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def current(
        self,
        principal: Principal,
        *,
        provider: str,
        im_instance_id: str,
        realm_id: str,
    ) -> IMAccountBinding | None:
        if principal.org_ctx.tenant_id != "default" or not principal.org_ctx.directory_user_id:
            return None
        try:
            async with self._sessions() as session:
                result = await session.execute(
                    text(
                        "SELECT tenant_id, ai_user_id, directory_user_id, "
                        "provider, im_instance_id, realm_id, im_user_id, login_subject, "
                        "status FROM public.im_account_bindings "
                        "WHERE tenant_id=:tenant AND ai_user_id=:user "
                        "AND directory_user_id=:directory "
                        "AND provider=:provider AND im_instance_id=:instance AND realm_id=:realm"
                    ),
                    {
                        "tenant": principal.org_ctx.tenant_id,
                        "user": principal.ai_user_id,
                        "directory": principal.org_ctx.directory_user_id,
                        "provider": provider,
                        "instance": im_instance_id,
                        "realm": realm_id,
                    },
                )
                row = result.mappings().one_or_none()
                return IMAccountBinding(**dict(row)) if row is not None else None
        except SQLAlchemyError:
            raise IMContextError("im_mapping_unavailable", 503) from None
