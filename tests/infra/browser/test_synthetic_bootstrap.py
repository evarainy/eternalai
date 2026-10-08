"""Isolated bootstrap boundaries; no durable credentials or database are created."""

from __future__ import annotations

import asyncio
import base64
import secrets
import stat
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import SecretStr

from app.browser_skill.models import ModelManifest
from app.infra.auth.crypto import HMACSessionToken, PrincipalSessionBinder
from app.infra.browser import synthetic_bootstrap as bootstrap
from app.infra.browser import synthetic_vault as vault
from app.infra.browser.fixed_synthetic_seed import SYNTHETIC_TENANT, SYNTHETIC_USER
from app.infra.browser.synthetic_configuration import (
    CLEANUP_ACTOR,
    CLEANUP_ROLE,
    PUBLICATION_ACTOR,
    PUBLICATION_ROLE,
    SyntheticOperatorBundle,
    synthetic_jev_manifest,
)


def _manifest() -> ModelManifest:
    return synthetic_jev_manifest()


def test_generated_bundle_has_eight_independent_keys_and_exact_actor_scopes() -> None:
    documents, sessions = bootstrap._material(_manifest())
    operator = SyntheticOperatorBundle.model_validate(documents[vault.OPERATOR_FILE])
    keys = (
        operator.session_signing_key,
        operator.session_binding_key,
        operator.payload_keys[operator.active_payload_key_id],
        operator.resource_keys[operator.active_resource_key_id],
        operator.request_digest_keys[operator.active_request_digest_key_id],
        operator.input_digest_key,
        operator.result_digest_key,
        operator.proof_context_key,
    )
    decoded = [base64.b64decode(key.get_secret_value(), validate=True) for key in keys]
    assert len(decoded) == 8 and all(len(key) == 32 for key in decoded)
    assert len(set(decoded)) == 8
    assert set(documents[vault.BUSINESS_FILE]) == {"business_token"}
    assert set(documents[vault.DEACTIVATION_FILE]) == {
        "session_signing_key", "session_binding_key", "publication_token",
    }
    codec = HMACSessionToken(signing_key=decoded[0], ttl_seconds=3600,
                             tenant_id=SYNTHETIC_TENANT)
    binder = PrincipalSessionBinder(binding_key=decoded[1])
    identities = (
        (operator.business_token.get_secret_value(), SYNTHETIC_USER, ()),
        (operator.publication_token.get_secret_value(), PUBLICATION_ACTOR,
         (PUBLICATION_ROLE,)),
        (operator.cleanup_token.get_secret_value(), CLEANUP_ACTOR, (CLEANUP_ROLE,)),
    )
    assert len(set(sessions)) == 3
    for (token, actor, roles), session in zip(identities, sessions, strict=True):
        verified = codec.inspect(token)
        assert verified.principal.ai_user_id == actor
        assert verified.principal.org_ctx.tenant_id == SYNTHETIC_TENANT
        assert verified.principal.roles == roles
        assert binder.bind(verified.principal, actor) == session
        remaining = (verified.expires_at - datetime.now(UTC)).total_seconds()
        assert 3590 <= remaining <= 3600


def test_vault_ciphertext_rejects_tamper_wrong_name_and_wrong_passphrase() -> None:
    passphrase = secrets.token_urlsafe(24)
    document = {"synthetic_metadata": "fixture"}
    ciphertext = vault.encrypt_document(vault.OPERATOR_FILE, document, passphrase)
    assert vault.decrypt_document(vault.OPERATOR_FILE, ciphertext, passphrase) == document
    changed = bytearray(ciphertext)
    changed[-1] ^= 1
    for name, ciphertext, supplied in (
        (vault.OPERATOR_FILE, bytes(changed), passphrase),
        (vault.DEACTIVATION_FILE, ciphertext, passphrase),
        (vault.OPERATOR_FILE, ciphertext, secrets.token_urlsafe(24)),
    ):
        with pytest.raises(ValueError, match="browser_vault_unlock_failed"):
            vault.decrypt_document(name, ciphertext, supplied)


@pytest.mark.parametrize("phrase", [
    "  synthetic-only phrase \t ", '"synthetic-only quoted phrase"',
    "synthetic-only \u4e00\U0001f31f e\u0301", r"synthetic-only\n\t literal",
], ids=["whitespace", "literal-quotes", "unicode", "literal-escapes"])
def test_vault_diagnostic_round_trip_keeps_phrase_and_source_aad(phrase: str) -> None:
    document = {"synthetic_metadata": "fixture"}
    options = {"trial_id": vault.OBSERVE_TRIAL, "attempt_id": "a" * 32}
    ciphertext = vault.encrypt_document(vault.OPERATOR_FILE, document, phrase, **options)
    assert vault.decrypt_document(vault.OPERATOR_FILE, ciphertext, phrase, **options) == document
    with pytest.raises(vault._VaultUnlockError) as caught:
        vault.decrypt_document(vault.OPERATOR_FILE, ciphertext, phrase,
                               trial_id=vault.OBSERVE_TRIAL, attempt_id="b" * 32)
    assert caught.value.stage == "authentication"
    assert caught.value.args == ("browser_vault_unlock_failed",)


@pytest.mark.parametrize("kind,stage", [
    ("format", "format"), ("kdf", "kdf"), ("backend", "kdf"),
    ("authentication", "authentication"), ("nonce", "authentication"),
    ("json", "json"), ("encoding", "json"), ("contract", "contract"),
])
def test_vault_diagnostic_categories_remain_fixed_and_do_not_render_input(
    kind: str, stage: str, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import traceback

    phrase = secrets.token_urlsafe(24)
    marker = secrets.token_urlsafe(24)
    ciphertext = vault.encrypt_document(vault.OPERATOR_FILE, {"synthetic_metadata": marker}, phrase)
    supplied = phrase
    if kind == "format":
        ciphertext = b"BAD!" + ciphertext[4:]
    elif kind == "kdf":
        supplied = "too-short"
    elif kind == "backend":
        monkeypatch.setattr(vault, "_derive", Mock(side_effect=vault.UnsupportedAlgorithm(marker)))
    elif kind == "authentication":
        supplied = secrets.token_urlsafe(24)
    elif kind == "nonce":
        ciphertext = ciphertext[:20] + bytes([ciphertext[20] ^ 1]) + ciphertext[21:]
    else:
        payload = {"json": b"{bad", "encoding": b"\xff", "contract": b"[]"}[kind]
        salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
        ciphertext = b"BSV1" + salt + nonce + vault.AESGCM(vault._derive(phrase, salt)).encrypt(
            nonce, payload, vault._aad(vault.OPERATOR_FILE),
        )
    with pytest.raises(vault._VaultUnlockError) as caught:
        vault.decrypt_document(vault.OPERATOR_FILE, ciphertext, supplied)
    assert caught.value.stage == stage
    assert caught.value.args == ("browser_vault_unlock_failed",)
    assert caught.value.__suppress_context__
    rendered = "".join(traceback.format_exception(caught.value))
    assert phrase not in rendered and marker not in rendered
    assert capsys.readouterr() == ("", "")


def test_vault_diagnostic_invalid_format_stops_before_kdf(monkeypatch: pytest.MonkeyPatch) -> None:
    derive = Mock(side_effect=AssertionError("KDF must not run"))
    monkeypatch.setattr(vault, "_derive", derive)
    with pytest.raises(vault._VaultUnlockError) as caught:
        vault.decrypt_document(vault.OPERATOR_FILE, b"BSV1", "synthetic-only phrase")
    assert caught.value.stage == "format"
    derive.assert_not_called()


def test_vault_requires_exact_explicit_path_before_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vault, "_private_console", lambda: None)
    directory_check = Mock(side_effect=AssertionError("filesystem reached"))
    monkeypatch.setattr(vault, "_check_directory", directory_check)
    with pytest.raises(ValueError, match="browser_vault_path_invalid"):
        vault.read_encrypted(Path("/tmp/operator.bundle.enc"), expected_name=vault.OPERATOR_FILE)
    directory_check.assert_not_called()


def test_vault_never_overwrites_existing_ciphertext(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vault, "_private_console", lambda: None)
    monkeypatch.setattr(vault, "_check_directory", lambda **kwargs: None)
    directory = Mock()
    directory.iterdir.return_value = iter(())
    monkeypatch.setattr(vault, "VAULT_DIRECTORY", directory)
    existing = Mock()
    existing.exists.return_value = True
    monkeypatch.setattr(vault, "_file_path", lambda name: existing)
    prompt = Mock(side_effect=AssertionError("passphrase prompted"))
    monkeypatch.setattr(vault, "prompt_passphrase", prompt)
    documents = {name: {} for name in (
        vault.OPERATOR_FILE, vault.BUSINESS_FILE, vault.DEACTIVATION_FILE,
    )}
    with pytest.raises(ValueError, match="browser_vault_existing_ciphertext"):
        vault.write_encrypted_files(documents)
    prompt.assert_not_called()


@pytest.mark.parametrize("mode,owner", [
    (stat.S_IFREG | 0o644, 1000),
    (stat.S_IFREG | 0o600, 1001),
    (stat.S_IFLNK | 0o600, 1000),
])
def test_vault_rejects_unsafe_file_metadata(
    monkeypatch: pytest.MonkeyPatch, mode: int, owner: int,
) -> None:
    monkeypatch.setattr(vault.sys, "platform", "linux")
    monkeypatch.setattr(vault.os, "geteuid", lambda: 1000, raising=False)
    path = Mock()
    path.lstat.return_value = SimpleNamespace(
        st_mode=mode, st_uid=owner, st_nlink=1, st_size=64,
    )
    with pytest.raises(ValueError, match="browser_vault_file_invalid"):
        vault._checked_file(path)


@pytest.mark.parametrize("mode,owner", [
    (stat.S_IFDIR | 0o755, 1000),
    (stat.S_IFDIR | 0o700, 1001),
    (stat.S_IFLNK | 0o700, 1000),
])
def test_vault_rejects_unsafe_directory_metadata(
    monkeypatch: pytest.MonkeyPatch, mode: int, owner: int,
) -> None:
    monkeypatch.setattr(vault.sys, "platform", "linux")
    monkeypatch.setattr(vault.os, "geteuid", lambda: 1000, raising=False)
    parent = Mock()
    parent.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=1000)
    directory = Mock()
    directory.parent = parent
    directory.lstat.return_value = SimpleNamespace(st_mode=mode, st_uid=owner)
    monkeypatch.setattr(vault, "VAULT_DIRECTORY", directory)
    with pytest.raises(ValueError, match="browser_vault_directory_invalid"):
        vault._check_directory(create=False)


def test_ciphertext_and_directory_sync_precede_database_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All resources are mocked; no real key, file, or database is created."""
    events: list[str] = []
    monkeypatch.setattr(vault.sys, "platform", "linux")
    monkeypatch.setattr(vault.os, "geteuid", lambda: 1000, raising=False)
    for name, flag in (("O_DIRECTORY", 0x1000), ("O_NOFOLLOW", 0x2000),
                       ("O_CLOEXEC", 0x4000)):
        monkeypatch.setattr(vault.os, name, flag, raising=False)
    private_directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=1000)
    parent = Mock()
    parent.lstat.return_value = private_directory
    directory = Mock()
    directory.parent = parent
    directory.exists.return_value = False
    directory.is_symlink.return_value = False
    directory.lstat.return_value = private_directory
    directory.iterdir.return_value = iter(())
    directory.mkdir.side_effect = lambda **kwargs: events.append("task_mkdir")
    monkeypatch.setattr(vault, "VAULT_DIRECTORY", directory)
    files = {name: Mock() for name in (
        vault.OPERATOR_FILE, vault.BUSINESS_FILE, vault.DEACTIVATION_FILE,
    )}
    for path in files.values():
        path.exists.return_value = False
        path.is_symlink.return_value = False
    monkeypatch.setattr(vault, "_file_path", lambda name: files[name])
    monkeypatch.setattr(vault, "_private_console", lambda: None)
    monkeypatch.setattr(vault, "_checked_file", lambda path: None)
    monkeypatch.setattr(vault, "prompt_passphrase", lambda **kwargs: secrets.token_urlsafe(24))
    monkeypatch.setattr(vault, "encrypt_document", lambda *args: b"synthetic-ciphertext")

    class _Stream:
        def __init__(self, descriptor: int) -> None:
            self.descriptor = descriptor

        def __enter__(self) -> _Stream:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def write(self, contents: bytes) -> None:
            events.append("file_write")

        def flush(self) -> None:
            events.append("file_flush")

        def fileno(self) -> int:
            return self.descriptor

    next_file = iter((11, 12, 13))

    def fake_open(path: object, flags: int, mode: int | None = None) -> int:
        if path is parent:
            assert flags & 0x7000 == 0x7000
            return 98
        if path is directory:
            assert flags & 0x7000 == 0x7000
            return 99
        assert mode == 0o600
        return next(next_file)

    def fake_fsync(descriptor: int) -> None:
        events.append({98: "parent_sync", 99: "directory_sync"}.get(
            descriptor, "file_sync"
        ))

    monkeypatch.setattr(vault.os, "open", fake_open)
    monkeypatch.setattr(vault.os, "fdopen", lambda descriptor, mode: _Stream(descriptor))
    monkeypatch.setattr(vault.os, "fstat", lambda descriptor: private_directory)
    monkeypatch.setattr(vault.os, "fsync", fake_fsync)
    monkeypatch.setattr(vault.os, "close", lambda descriptor: None)

    class _Connection:
        async def execute(self, statement: object, parameters: object = None) -> None:
            pass

    class _Engine:
        @asynccontextmanager
        async def begin(self) -> Any:
            yield _Connection()
            events.append("database_commit")

        async def dispose(self) -> None:
            pass

    async def approved_identity(connection: object) -> tuple[str, str]:
        return bootstrap.EXPECTED_SYSTEM_IDENTIFIER, bootstrap.EXPECTED_REVISION

    async def empty_state(connection: object, *, lock_capability: bool) -> bool:
        return False

    async def register(*args: object, **kwargs: object) -> None:
        events.append("register_pending")

    monkeypatch.setattr(bootstrap, "create_async_engine", lambda *args, **kwargs: _Engine())
    monkeypatch.setattr(bootstrap, "read_database_password",
                        lambda: SecretStr(secrets.token_urlsafe(24)))
    monkeypatch.setattr(bootstrap, "_private_console", lambda: None)
    monkeypatch.setattr(bootstrap, "_identity", approved_identity)
    monkeypatch.setattr(bootstrap, "_empty_initial_state", empty_state)
    monkeypatch.setattr(bootstrap, "assert_vault_uninitialized", lambda: None)
    monkeypatch.setattr(bootstrap, "_material", lambda manifest: (
        {name: {} for name in files}, tuple(secrets.token_urlsafe(24) for _ in range(3)),
    ))
    monkeypatch.setattr(bootstrap, "_register", register)
    asyncio.run(bootstrap.initialize_synthetic_bootstrap(_manifest(), enabled=True))

    assert events.count("file_flush") == events.count("file_sync") == 3
    assert events.index("task_mkdir") < events.index("parent_sync")
    assert events.index("parent_sync") < events.index("file_flush")
    assert events.index("register_pending") < events.index("file_flush")
    flush_indices = [index for index, event in enumerate(events) if event == "file_flush"]
    sync_indices = [index for index, event in enumerate(events) if event == "file_sync"]
    assert all(flush < sync for flush, sync in zip(flush_indices, sync_indices, strict=True))
    assert max(sync_indices) < events.index("directory_sync")
    assert events.index("directory_sync") < events.index("database_commit")


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one(self) -> object:
        return self.value

    def scalar_one_or_none(self) -> object:
        return self.value

    def scalars(self) -> _Result:
        return self

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> object:
        return self.value

    def all(self) -> object:
        return self.value


class _Connection:
    def __init__(
        self, *, system_identifier: str = bootstrap.EXPECTED_SYSTEM_IDENTIFIER,
        capability_row: dict[str, object] | None = None,
    ) -> None:
        self.system_identifier = system_identifier
        self.capability_row = capability_row
        self.statements: list[str] = []

    async def execute(self, statement: object, parameters: object = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if "pg_control_system" in sql:
            return _Result(self.system_identifier)
        if "alembic_version" in sql:
            return _Result([bootstrap.EXPECTED_REVISION])
        if "FROM capabilities" in sql:
            return _Result(self.capability_row)
        return _Result(None)


def test_identity_mismatch_stops_before_key_generation_and_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(system_identifier="unexpected")

    class _Engine:
        @asynccontextmanager
        async def begin(self) -> Any:
            yield connection

        async def dispose(self) -> None:
            pass

    generator = Mock(side_effect=AssertionError("generation reached"))
    writer = Mock(side_effect=AssertionError("vault write reached"))
    monkeypatch.setattr(bootstrap, "create_async_engine", lambda *args, **kwargs: _Engine())
    monkeypatch.setattr(bootstrap, "read_database_password",
                        lambda: SecretStr(secrets.token_urlsafe(24)))
    monkeypatch.setattr(bootstrap, "_private_console", lambda: None)
    monkeypatch.setattr(bootstrap, "_material", generator)
    monkeypatch.setattr(bootstrap, "write_encrypted_files", writer)
    with pytest.raises(ValueError, match="browser_bootstrap_database_identity_invalid"):
        asyncio.run(bootstrap.initialize_synthetic_bootstrap(_manifest(), enabled=True))
    generator.assert_not_called()
    writer.assert_not_called()
    assert not any("INSERT" in sql for sql in connection.statements)


@pytest.mark.parametrize("deployment", [
    "typesafe/jev-1.13", "unapproved/model",
])
def test_invalid_model_pin_stops_before_database_or_generation(
    monkeypatch: pytest.MonkeyPatch, deployment: str,
) -> None:
    database = Mock(side_effect=AssertionError("database reached"))
    generator = Mock(side_effect=AssertionError("generation reached"))
    monkeypatch.setattr(bootstrap, "create_async_engine", database)
    monkeypatch.setattr(bootstrap, "_material", generator)
    with pytest.raises(ValueError, match="browser_bootstrap_manifest_invalid"):
        asyncio.run(bootstrap.initialize_synthetic_bootstrap(
            _manifest().model_copy(update={"deployment_model": deployment}), enabled=True,
        ))
    database.assert_not_called()
    generator.assert_not_called()


def test_matching_models_with_different_digest_stop_before_database_or_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Mock(side_effect=AssertionError("database reached"))
    generator = Mock(side_effect=AssertionError("generation reached"))
    monkeypatch.setattr(bootstrap, "create_async_engine", database)
    monkeypatch.setattr(bootstrap, "_material", generator)
    trusted = _manifest()
    altered_prefix = "0" if trusted.manifest_digest[0] != "0" else "1"
    mismatch = trusted.model_copy(update={
        "manifest_digest": altered_prefix + trusted.manifest_digest[1:],
    })
    with pytest.raises(ValueError, match="browser_bootstrap_manifest_invalid"):
        asyncio.run(bootstrap.initialize_synthetic_bootstrap(mismatch, enabled=True))
    database.assert_not_called()
    generator.assert_not_called()


def test_registration_uses_metadata_only_binding_and_two_capacity_rows() -> None:
    connection = _Connection()
    session_ids = tuple(secrets.token_urlsafe(32) for _ in range(3))
    asyncio.run(bootstrap._register(connection, _manifest(), session_ids))
    statements = connection.statements
    assert sum("INSERT INTO sessions" in sql for sql in statements) == 3
    assert sum("INSERT INTO principal_roles" in sql for sql in statements) == 2
    assert sum("INSERT INTO browser_capacity_limits" in sql for sql in statements) == 2
    binding = next(sql for sql in statements if "INSERT INTO oa_session_credentials" in sql)
    assert "binding_subject_digest" in binding and "'active'" in binding
    assert "encrypted_payload" not in binding and "encrypted_password_payload" not in binding
    assert not any("browser_publications" in sql for sql in statements)


def test_exact_global_capability_is_reused_without_an_insert() -> None:
    existing = bootstrap.synthetic_detail_capability_snapshot().model_dump(mode="python")
    connection = _Connection(capability_row=existing)
    assert asyncio.run(bootstrap._empty_initial_state(connection)) is True
    session_ids = tuple(secrets.token_urlsafe(32) for _ in range(3))
    asyncio.run(bootstrap._register(
        connection, _manifest(), session_ids, capability_reused=True,
    ))
    assert not any("INSERT INTO capabilities" in sql for sql in connection.statements)


def test_conflicting_global_capability_fails_closed() -> None:
    existing = bootstrap.synthetic_detail_capability_snapshot().model_dump(mode="python")
    existing["status"] = "disabled"
    with pytest.raises(ValueError, match="browser_bootstrap_capability_conflict"):
        asyncio.run(bootstrap._empty_initial_state(_Connection(capability_row=existing)))


def test_dry_run_only_reads_identity_and_conflict_state(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Connection()

    class _Engine:
        @asynccontextmanager
        async def connect(self) -> Any:
            yield connection

        async def dispose(self) -> None:
            pass

    generator = Mock(side_effect=AssertionError("generation reached"))
    monkeypatch.setattr(bootstrap, "create_async_engine", lambda *args, **kwargs: _Engine())
    monkeypatch.setattr(bootstrap, "read_database_password",
                        lambda: SecretStr(secrets.token_urlsafe(24)))
    monkeypatch.setattr(bootstrap, "assert_vault_uninitialized", lambda: None)
    monkeypatch.setattr(bootstrap, "_material", generator)
    preflight = asyncio.run(bootstrap.preflight_synthetic_bootstrap())
    assert preflight.initial_state_empty is True
    assert not any("INSERT" in sql or "UPDATE" in sql for sql in connection.statements)
    generator.assert_not_called()
