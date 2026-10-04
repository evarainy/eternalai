"""Explicit task-only registration for the isolated synthetic browser tenant.

The default operation is a read-only preflight. Only an interactive ``init`` call
can create keys, issue short-lived tokens, register metadata, or write the vault.
Publication remains a separate explicit prepare/activate operation through the
existing publication store and its installed source/grants.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.browser_skill.models import ModelManifest
from app.infra.auth.crypto import HMACSessionToken, PrincipalSessionBinder
from app.infra.browser.fixed_synthetic_seed import (
    SYNTHETIC_TENANT,
    SYNTHETIC_USER,
    build_fixed_synthetic_query_source,
    synthetic_detail_capability_snapshot,
)
from app.infra.browser.local_resource_lifecycle import local_subject_digest
from app.infra.browser.synthetic_configuration import (
    BINDING_ID,
    CLEANUP_ACTOR,
    CLEANUP_ROLE,
    DATABASE_URL,
    PROVIDER_ID,
    PUBLICATION_ACTOR,
    PUBLICATION_ROLE,
    SyntheticOperatorBundle,
    prompt_database_password,
    synthetic_jev_manifest,
)
from app.infra.browser.synthetic_vault import (
    _private_console,
    assert_vault_uninitialized,
    write_encrypted_files,
)
from app.infra.persistence.capability_registry.schema import capabilities
from app.ports.auth import Principal, PrincipalOrgContext
from app.ports.capability_registry import CapabilitySpec

EXPECTED_SYSTEM_IDENTIFIER = "7692249839882264617"
EXPECTED_REVISION = "20261003_212000"
_SKILL_ID = "fixed_synthetic_message_detail"
_KEY_NAMES = (
    "session_signing_key", "session_binding_key", "payload_key", "resource_key",
    "request_digest_key", "input_digest_key", "result_digest_key", "proof_context_key",
)


@dataclass(frozen=True, slots=True)
class BootstrapPreflight:
    system_identifier: str
    revision: str
    initial_state_empty: bool
    capability_reused: bool


async def _identity(connection: AsyncConnection) -> tuple[str, str]:
    """No mutation, secret access, or key generation can precede this check."""
    system_identifier = (await connection.execute(text(
        "SELECT system_identifier::text FROM pg_control_system()"
    ))).scalar_one()
    versions = (
        await connection.execute(text("SELECT version_num FROM alembic_version"))
    ).scalars().all()
    if (str(system_identifier) != EXPECTED_SYSTEM_IDENTIFIER
            or versions != [EXPECTED_REVISION]):
        raise ValueError("browser_bootstrap_database_identity_invalid")
    return str(system_identifier), versions[0]


async def _empty_initial_state(
    connection: AsyncConnection, *, lock_capability: bool = False,
) -> bool:
    """A dedicated tenant must not borrow or overwrite any pre-existing row."""
    statement = select(capabilities).where(
        capabilities.c.capability_id == "browser.synthetic.system_message_detail"
    )
    if lock_capability:
        statement = statement.with_for_update(read=True)
    row = (await connection.execute(statement)).mappings().one_or_none()
    if row is not None:
        try:
            if CapabilitySpec.model_validate(dict(row)) != synthetic_detail_capability_snapshot():
                raise ValueError
        except Exception:
            raise ValueError("browser_bootstrap_capability_conflict") from None
    checks = (
        ("SELECT 1 FROM sessions WHERE tenant_id=:tenant LIMIT 1", {"tenant": SYNTHETIC_TENANT}),
        ("SELECT 1 FROM principal_roles WHERE tenant_id=:tenant LIMIT 1",
         {"tenant": SYNTHETIC_TENANT}),
        ("SELECT 1 FROM oa_session_credentials WHERE tenant_id=:tenant LIMIT 1",
         {"tenant": SYNTHETIC_TENANT}),
        ("SELECT 1 FROM browser_capacity_limits WHERE provider_key=:provider LIMIT 1",
         {"provider": PROVIDER_ID}),
        ("SELECT 1 FROM browser_publications WHERE tenant_id=:tenant LIMIT 1",
         {"tenant": SYNTHETIC_TENANT}),
        ("SELECT 1 FROM browser_runs WHERE tenant_id=:tenant LIMIT 1",
         {"tenant": SYNTHETIC_TENANT}),
        ("SELECT 1 FROM browser_binding_leases WHERE tenant_id=:tenant LIMIT 1",
         {"tenant": SYNTHETIC_TENANT}),
        ("SELECT 1 FROM tasks WHERE tenant_id=:tenant LIMIT 1", {"tenant": SYNTHETIC_TENANT}),
    )
    for query, parameters in checks:
        if (await connection.execute(text(query), parameters)).scalar_one_or_none() is not None:
            raise ValueError("browser_bootstrap_initial_state_conflict")
    return row is not None


async def preflight_synthetic_bootstrap() -> BootstrapPreflight:
    """Default dry run: fixed DB identity and exact initial-state checks only."""
    engine = create_async_engine(
        DATABASE_URL, echo=False, hide_parameters=True,
        connect_args={"connect_timeout": 5, "application_name": "browser_synthetic_bootstrap",
                      "password": prompt_database_password().get_secret_value(),
                      "options": "-c statement_timeout=5000 -c lock_timeout=1000"},
    )
    try:
        async with engine.connect() as connection:
            system_identifier, revision = await _identity(connection)
            capability_reused = await _empty_initial_state(connection)
            assert_vault_uninitialized()
        return BootstrapPreflight(system_identifier, revision, True, capability_reused)
    finally:
        await engine.dispose()


def _material(manifest: ModelManifest) -> tuple[dict[str, dict[str, object]], tuple[str, str, str]]:
    """Called only after DB identity + empty-state preflight in explicit init."""
    if manifest != synthetic_jev_manifest():
        raise ValueError("browser_bootstrap_manifest_invalid")
    generated = [secrets.token_bytes(32) for _ in _KEY_NAMES]
    if any(len(value) != 32 for value in generated) or len(set(generated)) != len(_KEY_NAMES):
        raise ValueError("browser_bootstrap_key_generation_invalid")
    keys = dict(zip(_KEY_NAMES, generated, strict=True))
    token_codec = HMACSessionToken(
        signing_key=keys["session_signing_key"], ttl_seconds=3600, tenant_id=SYNTHETIC_TENANT,
    )
    binder = PrincipalSessionBinder(binding_key=keys["session_binding_key"])
    org = PrincipalOrgContext(tenant_id=SYNTHETIC_TENANT)
    business = Principal(ai_user_id=SYNTHETIC_USER, display_name="Synthetic browser user",
                         roles=(), org_ctx=org)
    publication = Principal(ai_user_id=PUBLICATION_ACTOR,
                            display_name="Synthetic publication operator",
                            roles=(PUBLICATION_ROLE,), org_ctx=org)
    cleanup = Principal(ai_user_id=CLEANUP_ACTOR,
                        display_name="Synthetic cleanup operator",
                        roles=(CLEANUP_ROLE,), org_ctx=org)
    sessions = (
        binder.bind(business, SYNTHETIC_USER),
        binder.bind(publication, PUBLICATION_ACTOR),
        binder.bind(cleanup, CLEANUP_ACTOR),
    )
    tokens = (token_codec.issue(business), token_codec.issue(publication),
              token_codec.issue(cleanup))

    def encoded(name: str) -> str:
        return base64.b64encode(keys[name]).decode("ascii")

    operator: dict[str, object] = {
        "session_signing_key": encoded("session_signing_key"),
        "session_binding_key": encoded("session_binding_key"),
        "payload_keys": {"v1": encoded("payload_key")},
        "active_payload_key_id": "v1",
        "resource_keys": {"v1": encoded("resource_key")},
        "active_resource_key_id": "v1",
        "request_digest_keys": {"v1": encoded("request_digest_key")},
        "active_request_digest_key_id": "v1",
        "input_digest_key": encoded("input_digest_key"),
        "result_digest_key": encoded("result_digest_key"),
        "proof_context_key": encoded("proof_context_key"),
        "business_token": tokens[0], "publication_token": tokens[1],
        "cleanup_token": tokens[2], "jev_manifest": manifest.model_dump(mode="json"),
    }
    SyntheticOperatorBundle.model_validate(operator)
    return (
        {
            "operator.bundle.enc": operator,
            "business.token.enc": {"business_token": tokens[0]},
            "deactivation.bundle.enc": {
                "session_signing_key": encoded("session_signing_key"),
                "session_binding_key": encoded("session_binding_key"),
                "publication_token": tokens[1],
            },
        },
        sessions,
    )


async def _register(
    connection: AsyncConnection, manifest: ModelManifest, sessions: tuple[str, str, str],
    *, capability_reused: bool = False,
) -> None:
    if manifest != synthetic_jev_manifest():
        raise ValueError("browser_bootstrap_manifest_invalid")
    source = build_fixed_synthetic_query_source(manifest)
    expected = source.manifest.capability
    if (source.manifest.skill.skill_id != _SKILL_ID
            or expected.capability_id != "browser.synthetic.system_message_detail"
            or expected.status != "active" or expected.type != "query"):
        raise ValueError("browser_bootstrap_source_invalid")
    if not capability_reused:
        await connection.execute(insert(capabilities).values(**expected.model_dump(mode="python")))
    for session_id in sessions:
        await connection.execute(text(
            "INSERT INTO sessions (tenant_id,session_id) VALUES (:tenant,:session)"
        ), {"tenant": SYNTHETIC_TENANT, "session": session_id})
    for actor, role in ((PUBLICATION_ACTOR, PUBLICATION_ROLE), (CLEANUP_ACTOR, CLEANUP_ROLE)):
        await connection.execute(text(
            "INSERT INTO principal_roles (tenant_id,ai_user_id,role)"
            " VALUES (:tenant,:actor,:role)"
        ), {"tenant": SYNTHETIC_TENANT, "actor": actor, "role": role})
    await connection.execute(text(
        "INSERT INTO oa_session_credentials (tenant_id,ai_user_id,target_system,binding_id,"
        "binding_state,binding_revision,credential_write_revision,refresh_epoch,"
        "binding_subject_digest,binding_subject_verified_at,updated_at) VALUES "
        "(:tenant,:user,'oa',:binding,'active',1,0,0,:subject,clock_timestamp(),"
        "clock_timestamp())"
    ), {"tenant": SYNTHETIC_TENANT, "user": SYNTHETIC_USER,
        "binding": BINDING_ID, "subject": local_subject_digest(SYNTHETIC_TENANT, SYNTHETIC_USER)})
    for quota_id, tenant_id in (("browser_fixture_global", None),
                                ("browser_fixture_tenant", SYNTHETIC_TENANT)):
        await connection.execute(text(
            "INSERT INTO browser_capacity_limits (quota_id,provider_key,tenant_id,max_active)"
            " VALUES (:quota,:provider,:tenant,1)"
        ), {"quota": quota_id, "provider": PROVIDER_ID, "tenant": tenant_id})


async def initialize_synthetic_bootstrap(manifest: ModelManifest, *, enabled: bool = False) -> None:
    """User-only init; no publication, model client, browser, or HTTP is constructed."""
    if enabled is not True or type(manifest) is not ModelManifest:
        raise ValueError("browser_bootstrap_disabled")
    if manifest != synthetic_jev_manifest():
        raise ValueError("browser_bootstrap_manifest_invalid")
    _private_console()
    engine = create_async_engine(
        DATABASE_URL, echo=False, hide_parameters=True,
        connect_args={"connect_timeout": 5, "application_name": "browser_synthetic_bootstrap",
                      "password": prompt_database_password().get_secret_value(),
                      "options": "-c statement_timeout=5000 -c lock_timeout=1000"},
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
            # Match existing writer/downgrade locks and serialize competing init.
            await connection.execute(text("SELECT pg_advisory_xact_lock(746420210000)"))
            await connection.execute(text("SELECT pg_advisory_xact_lock(746420212000)"))
            await _identity(connection)
            capability_reused = await _empty_initial_state(connection, lock_capability=True)
            assert_vault_uninitialized()
            documents, sessions = _material(manifest)
            await _register(connection, manifest, sessions,
                            capability_reused=capability_reused)
            # The transaction commits only after all three new ciphertext files
            # exist. A failure leaves no DB registration; any partial ciphertext
            # is retained for explicit operator recovery, never overwritten.
            write_encrypted_files(documents)
    finally:
        await engine.dispose()
