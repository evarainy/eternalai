"""Profile payload constraints on the approved synthetic PostgreSQL database.

Run with --noconftest and an explicit DATABASE_URL for 127.0.0.1:15432.
All fixture rows remain inside a transaction rolled back after the assertions.
This verifies database constraints, not a real provider's release evidence.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import DBAPIError

from alembic import command
from app.db.config import normalize_database_url

ROOT = Path(__file__).resolve().parents[4]


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    assert value, "explicit synthetic DATABASE_URL required"
    normalized = normalize_database_url(value)
    url = make_url(normalized)
    assert (url.host, url.port, url.database) == (
        "127.0.0.1", 15432, "eternalai_test",
    )
    return normalized


def _seed(connection: Connection, params: dict[str, object]) -> None:
    connection.execute(text(
        "INSERT INTO sessions(tenant_id,session_id) VALUES(:tenant,:session)"
    ), params)
    connection.execute(text(
        "INSERT INTO oa_session_credentials(tenant_id,ai_user_id,target_system,"
        "binding_id,binding_state,binding_subject_digest,"
        "binding_subject_verified_at,updated_at)"
        " VALUES(:tenant,:user,'oa',:binding,'active',:subject,"
        "clock_timestamp(),clock_timestamp())"
    ), params)
    connection.execute(text(
        "INSERT INTO browser_profiles(tenant_id,ai_user_id,session_id,"
        "target_system,binding_id) VALUES(:tenant,:user,:session,'oa',:binding)"
    ), params)
    connection.execute(text(
        "INSERT INTO browser_profile_generations(generation_id,tenant_id,ai_user_id,"
        "session_id,target_system,binding_id,binding_revision,profile_revision,"
        "lease_epoch,provider_key,capture_operation_id,manifest_digest,"
        "generation_ref_digest,subject_digest,origin_digest,projection_digest,"
        "captured_bytes,disposition) VALUES(:generation,:tenant,:user,:session,"
        "'oa',:binding,1,1,1,:provider,:operation,:digest,:ref,:subject,"
        ":digest,:digest,17,'validated')"
    ), params)
    connection.execute(text(
        "INSERT INTO browser_profile_payloads(tenant_id,ai_user_id,session_id,"
        "target_system,binding_id,generation_id,cipher_version,key_id,nonce,"
        "ciphertext) VALUES(:tenant,:user,:session,'oa',:binding,:generation,"
        "'aes256gcm-browser-profile-v1','synthetic',:nonce,:ciphertext)"
    ), params)
    connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


def _expect_database_rejection(connection: Connection, statement: str,
                               params: dict[str, object]) -> None:
    with pytest.raises(DBAPIError):
        with connection.begin_nested():
            connection.execute(text(statement), params)


def test_payload_requires_receipt_and_cannot_reappear_after_confirmation() -> None:
    database_url = _database_url()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "head")
    engine = create_engine(database_url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                suffix = uuid4().hex
                params: dict[str, object] = {
                    "tenant": "profile_" + suffix,
                    "user": "user_" + suffix,
                    "session": "sid_v1." + suffix,
                    "binding": "binding_" + suffix,
                    "generation": "generation_" + suffix,
                    "provider": "provider_" + suffix,
                    "operation": "operation_" + suffix,
                    "digest": b"d" * 32,
                    "ref": b"r" * 32,
                    "subject": b"s" * 32,
                    "nonce": b"n" * 12,
                    "ciphertext": b"c" * 17,
                    "proof": b"p" * 32,
                }
                _seed(connection, params)
                connection.execute(text(
                    "UPDATE browser_profile_generations SET disposition='orphan'"
                    " WHERE generation_id=:generation"
                ), params)
                changed_at = connection.execute(text(
                    "SELECT disposition_changed_at FROM browser_profile_generations"
                    " WHERE generation_id=:generation"
                ), params).scalar_one()
                assert changed_at is not None and changed_at.utcoffset() is not None
                _expect_database_rejection(connection,
                    "DELETE FROM browser_profile_payloads WHERE generation_id=:generation",
                    params)
                with pytest.raises(DBAPIError):
                    with connection.begin_nested():
                        connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
                        connection.execute(text(
                            "UPDATE browser_profile_generations"
                            " SET disposition='cleanup_confirmed',cleanup_proof_digest=:proof"
                            " WHERE generation_id=:generation"
                        ), params)
                        connection.execute(text(
                            "DELETE FROM browser_profile_payloads WHERE generation_id=:generation"
                        ), params)
                        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                connection.execute(text(
                    "INSERT INTO browser_profile_cleanup_receipts(tenant_id,ai_user_id,"
                    "session_id,target_system,binding_id,generation_id,provider_key,"
                    "capture_operation_id,generation_ref_digest,proof_digest)"
                    " VALUES(:tenant,:user,:session,'oa',:binding,:generation,:provider,"
                    ":operation,:ref,:proof)"
                ), params)
                with pytest.raises(DBAPIError):
                    with connection.begin_nested():
                        connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
                        connection.execute(text(
                            "UPDATE browser_profile_generations"
                            " SET disposition='cleanup_confirmed',cleanup_proof_digest=:proof"
                            " WHERE generation_id=:generation"
                        ), params)
                        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
                connection.execute(text(
                    "UPDATE browser_profile_generations"
                    " SET disposition='cleanup_confirmed',cleanup_proof_digest=:proof"
                    " WHERE generation_id=:generation"
                ), params)
                connection.execute(text(
                    "DELETE FROM browser_profile_payloads WHERE generation_id=:generation"
                ), params)
                connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                remaining = connection.execute(text(
                    "SELECT count(*) FROM browser_profile_payloads"
                    " WHERE generation_id=:generation"
                ), params).scalar_one()
                state = connection.execute(text(
                    "SELECT disposition,cleanup_proof_digest"
                    " FROM browser_profile_generations WHERE generation_id=:generation"
                ), params).one()
                assert remaining == 0
                assert state.disposition == "cleanup_confirmed"
                assert state.cleanup_proof_digest == params["proof"]
                _expect_database_rejection(connection,
                    "INSERT INTO browser_profile_payloads(tenant_id,ai_user_id,session_id,"
                    "target_system,binding_id,generation_id,cipher_version,key_id,nonce,"
                    "ciphertext) VALUES(:tenant,:user,:session,'oa',:binding,:generation,"
                    "'aes256gcm-browser-profile-v1','synthetic',:nonce,:ciphertext)",
                    params)
                _expect_database_rejection(connection,
                    "DELETE FROM browser_profile_generations WHERE generation_id=:generation",
                    params)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
