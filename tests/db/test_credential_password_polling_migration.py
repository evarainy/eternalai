"""Database proof for the per-system credential migration."""

from __future__ import annotations

import asyncio
import json
import os
import selectors
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.db.config import normalize_database_url
from app.infra.auth.postgresql import (
    PostgreSQLCredentialStore,
    credential_associated_data,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "20260821_090000"


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if value is None:
        raise AssertionError("DATABASE_URL must be set by the test runner environment")
    url = make_url(normalize_database_url(value))
    if url.host != "127.0.0.1" or url.port != 15432:
        raise AssertionError("credential migration tests require 127.0.0.1:15432")
    return value


def _alembic_config() -> Config:
    _database_url()
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return config


def test_session_columns_are_all_nullable_but_guarded_as_one_record() -> None:
    command.upgrade(_alembic_config(), "head")
    engine = create_engine(normalize_database_url(_database_url()))
    try:
        columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("oa_session_credentials")
        }
        for name in ("cipher_version", "nonce", "encrypted_payload", "expires_at"):
            assert columns[name]["nullable"] is True

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO oa_session_credentials"
                        " (ai_user_id, target_system, cipher_version, updated_at)"
                        " VALUES (:ai_user_id, 'u8', 'aes256gcm-v1', :updated_at)"
                    ),
                    {
                        "ai_user_id": f"usr_v1_{uuid4().hex}",
                        "updated_at": datetime.now(UTC),
                    },
                )
    finally:
        engine.dispose()


def test_primary_key_is_user_and_target_system() -> None:
    command.upgrade(_alembic_config(), "head")
    engine = create_engine(normalize_database_url(_database_url()))
    try:
        primary_key = inspect(engine).get_pk_constraint("oa_session_credentials")
        assert primary_key["constrained_columns"] == ["ai_user_id", "target_system"]
    finally:
        engine.dispose()


def test_existing_oa_session_survives_upgrade_and_remains_loadable() -> None:
    config = _alembic_config()
    database_url = normalize_database_url(_database_url())
    ai_user_id = f"usr_v1_{uuid4().hex}"
    encryption_key = bytes(range(32))
    nonce = os.urandom(12)
    oa_user_id = f"synthetic-oa-user-{uuid4().hex}"
    cookie_value = f"synthetic-cookie-{uuid4().hex}"
    expires_at = datetime(2099, 1, 1, tzinfo=UTC)
    updated_at = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)
    plaintext = json.dumps(
        {
            "oa_user_id": oa_user_id,
            "cookies": {"ecology_JSessionid": cookie_value},
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encrypted_payload = AESGCM(encryption_key).encrypt(
        nonce,
        plaintext,
        credential_associated_data(ai_user_id),
    )
    engine = create_engine(database_url)
    try:
        command.downgrade(config, PREVIOUS_REVISION)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO oa_session_credentials"
                    " (ai_user_id, cipher_version, nonce, encrypted_payload,"
                    " expires_at, updated_at) VALUES"
                    " (:ai_user_id, 'aes256gcm-v1', :nonce, :encrypted_payload,"
                    " :expires_at, :updated_at)"
                ),
                {
                    "ai_user_id": ai_user_id,
                    "nonce": nonce,
                    "encrypted_payload": encrypted_payload,
                    "expires_at": expires_at,
                    "updated_at": updated_at,
                },
            )
            before = dict(
                connection.execute(
                    text(
                        "SELECT ai_user_id, cipher_version, nonce,"
                        " encrypted_payload, expires_at, updated_at, revoked_at"
                        " FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                .mappings()
                .one()
            )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            after = dict(
                connection.execute(
                    text(
                        "SELECT ai_user_id, cipher_version, nonce,"
                        " encrypted_payload, expires_at, updated_at, revoked_at,"
                        " target_system, password_cipher_version, password_nonce,"
                        " encrypted_password_payload, poll_status,"
                        " poll_failure_count FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id AND target_system = 'oa'"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                .mappings()
                .one()
            )
        for name, value in before.items():
            assert after[name] == value
        assert after["target_system"] == "oa"
        assert after["password_cipher_version"] is None
        assert after["password_nonce"] is None
        assert after["encrypted_password_payload"] is None
        assert after["poll_status"] == "unbound"
        assert after["poll_failure_count"] == 0

        from app.db.session import make_async_engine, make_async_session_factory

        async def load_session() -> None:
            async_engine = make_async_engine(_database_url())
            store = PostgreSQLCredentialStore(
                session_factory=make_async_session_factory(async_engine),
                encryption_key=encryption_key,
            )
            try:
                loaded = await store.load(ai_user_id, "oa")
                assert loaded is not None
                assert loaded.oa_user_id.get_secret_value() == oa_user_id
                assert (
                    loaded.cookies["ecology_JSessionid"].get_secret_value()
                    == cookie_value
                )
                assert loaded.expires_at == expires_at
            finally:
                await async_engine.dispose()

        if sys.platform == "win32":
            asyncio.run(
                load_session(),
                loop_factory=lambda: asyncio.SelectorEventLoop(
                    selectors.SelectSelector()
                ),
            )
        else:
            asyncio.run(load_session())
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM oa_session_credentials"
                    " WHERE ai_user_id = :ai_user_id"
                ),
                {"ai_user_id": ai_user_id},
            )
        engine.dispose()
