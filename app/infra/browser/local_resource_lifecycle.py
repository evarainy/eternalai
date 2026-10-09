"""Local Chromium ownership and native exit evidence, never client-supplied proof.

The Node helper uses public BrowserServer.process(); private IPC is not logged.
Unknown processes after a worker restart remain quarantined. This adapter never
guesses a PID, reconnects by a stale endpoint, or claims durable profile capture.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.metadata
import json
import os
import re
import secrets
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, fields, is_dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel

from app.browser_skill.models import BrowserOperationError, BrowserSessionRef
from app.browser_skill.publication_contracts import BrowserPublicationManifest, canonical_json
from app.browser_skill.repair import ExplorationFeedback
from app.browser_skill.site_rules import (
    ExpectedDownload,
    ExpectedPopup,
    FrozenSiteAdapter,
    navigation_allowed,
)
from app.infra.browser.deployment_manifest import (
    LocalOfflineManifest,
    verify_local_offline_assets,
)
from app.infra.browser.local_file_artifacts import LocalFileArtifacts
from app.infra.browser.playwright_actions import LiveBrowser
from app.infra.browser.playwright_dom_rules import RegisteredDOMRules
from app.infra.browser.playwright_observer import RegisteredRegion
from app.infra.browser.profile_codec import checked_playwright_state
from app.infra.browser.read_execution import BrowserOutputProjector
from app.infra.persistence.browser.crypto import BrowserClaimProofContext, resource_identity
from app.infra.persistence.browser.payload_crypto import BrowserPayloadCipher
from app.ports.browser_profile_store import (
    BrowserProfileCaptureFact,
    BrowserProfileCleanupFact,
    BrowserProfileContext,
    BrowserProfileError,
    BrowserProfileGeneration,
)
from app.ports.browser_read_execution import (
    BrowserCaptureResolution,
    BrowserReadExecutionError,
    BrowserWorkerCheckpoint,
)
from app.ports.browser_run_store import RunCleanup, RunSnapshot
from app.ports.browser_store import (
    BrowserBindingKey,
    BrowserCleanupAuthorityPort,
    BrowserLeaseClaim,
    BrowserLeaseError,
    BrowserProviderExpectation,
    BrowserProviderFact,
    ProviderOutcome,
)
from app.ports.credential_vault import BrowserBindingFact

if TYPE_CHECKING:
    from app.infra.browser.fixed_synthetic_seed import FixedSyntheticSource
    from app.infra.browser.local_read_execution import LocalBrowserReadExecutionFactory

Guard = Callable[[], Awaitable[None]]


class LocalLoginSkill(Protocol):
    async def __call__(self, page: Any, binding: BrowserBindingFact, guard: Guard) -> None:
        """Audited, installed login only; guard before each browser send."""
        ...


class RegisteredExplorationSuggester(Protocol):
    """Trusted proposal source; returned IDs are never execution authority."""

    rule_digest: str

    async def __call__(
        self, run_id: str, prior_primitive_ids: tuple[str, ...],
        feedback: ExplorationFeedback,
    ) -> str | None: ...


class DownloadContentVerifier(Protocol):
    """Trusted, Site-specific file-key check; page metadata is not authority."""

    rule_digest: str

    async def matches(
        self, source: Path, expected_key: str, rule: ExpectedDownload,
    ) -> bool: ...

    async def matches_bytes(
        self, payload: bytes, expected_key: str, rule: ExpectedDownload,
    ) -> bool: ...


def _registered_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return {part.name: _registered_value(getattr(value, part.name))
                for part in fields(value)}
    if isinstance(value, (tuple, list)):
        return [_registered_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _registered_value(item) for key, item in value.items()}
    return value


def registered_local_digest(
    *, entry_url: str, region: RegisteredRegion, rules: RegisteredDOMRules,
    projector: BrowserOutputProjector, subject_selector: str,
    subject_frame_selectors: tuple[str, ...] = (), capture_indexed_db: bool = False,
    websocket_origins: tuple[str, ...] = (),
    popup_expectations: tuple[ExpectedPopup, ...] = (),
    download_expectations: tuple[ExpectedDownload, ...] = (),
    download_content_verifier: DownloadContentVerifier | None = None,
    exploration_suggest: RegisteredExplorationSuggester | None = None,
) -> str:
    """Bind browser-specific Site configuration without a publication hash cycle."""
    projector_type = type(projector)
    payload = {
        "entry_url": entry_url,
        "region": _registered_value(region),
        "rules": {
            "steps": _registered_value(rules.steps),
            "read": _registered_value(rules.read),
        },
        "projector": f"{projector_type.__module__}.{projector_type.__qualname__}",
        "subject_selector": subject_selector,
        "subject_frame_selectors": subject_frame_selectors,
        "capture_indexed_db": capture_indexed_db,
        "websocket_origins": websocket_origins,
        "popup_expectations": tuple(
            rule.model_dump(mode="json") for rule in popup_expectations
        ),
    }
    if rules.traversal is not None:
        payload["traversal"] = _registered_value(rules.traversal)
    if rules.repair_targets:
        payload["repair_targets"] = _registered_value(rules.repair_targets)
    if exploration_suggest is not None:
        implementation = type(exploration_suggest)
        payload["exploration_suggest"] = {
            "implementation": f"{implementation.__module__}.{implementation.__qualname__}",
            "rule_digest": exploration_suggest.rule_digest,
        }
    if download_expectations:
        payload["download_expectations"] = tuple(
            rule.model_dump(mode="json") for rule in download_expectations
        )
        if download_content_verifier is not None:
            verifier_type = type(download_content_verifier)
            payload["download_content_verifier"] = {
                "implementation": (
                    f"{verifier_type.__module__}.{verifier_type.__qualname__}"
                ),
                "rule_digest": getattr(download_content_verifier, "rule_digest", None),
            }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class RegisteredLocalSite:
    """Trusted immutable registration for one real Site/ReadSpec publication.

    The registration digest covers the browser-specific source settings. Login
    implementation and deployment assets are installed by the trusted host, not
    supplied in Run input or discovered from the page.
    """

    manifest: BrowserPublicationManifest
    entry_url: str
    region: RegisteredRegion
    rules: RegisteredDOMRules
    site: FrozenSiteAdapter
    projector: BrowserOutputProjector
    subject_selector: str
    subject_frame_selectors: tuple[str, ...] = ()
    login: LocalLoginSkill | None = field(default=None, repr=False)
    capture_indexed_db: bool = False
    websocket_origins: tuple[str, ...] = ()
    popup_expectations: tuple[ExpectedPopup, ...] = ()
    download_expectations: tuple[ExpectedDownload, ...] = ()
    download_content_verifier: DownloadContentVerifier | None = field(
        default=None, repr=False,
    )
    exploration_suggest: RegisteredExplorationSuggester | None = field(
        default=None, repr=False,
    )

    @property
    def origins(self) -> tuple[str, ...]:
        return self.manifest.site.navigation_origins

    @property
    def popup_origins(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(rule.origin for rule in self.popup_expectations))

    def registration_digest(self) -> str:
        return registered_local_digest(
            entry_url=self.entry_url, region=self.region, rules=self.rules,
            projector=self.projector, subject_selector=self.subject_selector,
            subject_frame_selectors=self.subject_frame_selectors,
            capture_indexed_db=self.capture_indexed_db,
            websocket_origins=self.websocket_origins,
            popup_expectations=self.popup_expectations,
            download_expectations=self.download_expectations,
            download_content_verifier=self.download_content_verifier,
            exploration_suggest=self.exploration_suggest,
        )

    def __post_init__(self) -> None:
        if (
            not isinstance(self.manifest, BrowserPublicationManifest)
            or self.manifest.source_kind != "registered_site"
            or not isinstance(self.region, RegisteredRegion)
            or not isinstance(self.rules, RegisteredDOMRules)
            or not isinstance(self.site, FrozenSiteAdapter)
            or not callable(self.projector)
            or type(self.subject_selector) is not str
            or not 1 <= len(self.subject_selector) <= 256
            or type(self.subject_frame_selectors) is not tuple
            or len(self.subject_frame_selectors) > 8
            or any(not selector or len(selector) > 256
                   for selector in self.subject_frame_selectors)
            or type(self.capture_indexed_db) is not bool
            or type(self.popup_expectations) is not tuple
            or any(not isinstance(rule, ExpectedPopup) for rule in self.popup_expectations)
            or type(self.download_expectations) is not tuple
            or any(not isinstance(rule, ExpectedDownload)
                   for rule in self.download_expectations)
            or bool(self.download_expectations) != (self.download_content_verifier is not None)
            or (self.manifest.site.exploration is not None
                and self.exploration_suggest is None)
            or (self.manifest.site.exploration is not None and (
                self.manifest.site.exploration.allowed_regions != (self.region.region_id,)
                or self.popup_expectations or self.download_expectations
            ))
            or (self.exploration_suggest is not None and (
                not callable(self.exploration_suggest)
                or type(getattr(self.exploration_suggest, "rule_digest", None)) is not str
                or re.fullmatch(
                    r"[0-9a-f]{64}", self.exploration_suggest.rule_digest,
                ) is None
            ))
            or (self.download_content_verifier is not None and (
                not callable(getattr(self.download_content_verifier, "matches", None))
                or not callable(getattr(self.download_content_verifier, "matches_bytes", None))
                or type(getattr(self.download_content_verifier, "rule_digest", None)) is not str
                or re.fullmatch(
                    r"[0-9a-f]{64}", self.download_content_verifier.rule_digest,
                ) is None
                or any(
                    rule.content_rule_digest != self.download_content_verifier.rule_digest
                    for rule in self.download_expectations
                )
            ))
            or type(self.websocket_origins) is not tuple
            or (self.login is not None and not callable(self.login))
            or not navigation_allowed(self.entry_url, self.origins)
            or any(origin not in self.origins for origin in self.popup_origins)
            or len({rule.step_id for rule in self.popup_expectations})
            != len(self.popup_expectations)
            or len({rule.step_id for rule in self.download_expectations})
            != len(self.download_expectations)
            or bool({rule.step_id for rule in self.download_expectations}
                    & {rule.step_id for rule in self.popup_expectations})
            or (self.manifest.site.traversal is not None and any(
                rule.step_id == self.manifest.site.traversal.advance_step_id
                for rule in self.download_expectations
            ))
            or any(
                rule.object_type != self.manifest.site.read_rule.object_type
                or not any(
                    step.step.step_id == rule.step_id
                    and step.step.operation == "click"
                    and step.step.effect == "read_only"
                    and step.effect.actual_effect == "read_only"
                    for step in self.manifest.site.steps
                ) for rule in self.download_expectations
            )
            or any(
                rule.object_type != self.manifest.site.read_rule.object_type
                or not any(
                    step.step.step_id == rule.step_id
                    and step.step.operation in {"click", "navigate"}
                    and step.step.effect == "read_only"
                    and step.effect.actual_effect == "read_only"
                    for step in self.manifest.site.steps
                ) for rule in self.popup_expectations
            )
            or len(set(self.websocket_origins)) != len(self.websocket_origins)
            or any(
                urlsplit(origin).scheme not in {"ws", "wss"}
                or ("https" if origin.startswith("wss://") else "http")
                + origin[origin.index("://"):] not in self.origins
                for origin in self.websocket_origins
            )
            or self.rules.site_digest != self.manifest.site.digest
            or self.rules.skill_digest != self.manifest.skill.digest
            or self.rules.verifier_digest != self.manifest.verifier.digest
            or {step.step_id for step in self.rules.steps} != {
                rule.step.step_id for rule in (
                    self.manifest.site.primitive_rules or self.manifest.site.steps
                )
            }
            or ((self.rules.traversal is None)
                != (self.manifest.site.traversal is None))
            or (self.rules.traversal is not None
                and self.manifest.site.traversal is not None
                and (
                    self.manifest.site.traversal.region_id != self.region.region_id
                    or self.rules.traversal.rule_digests() != {
                        "page_key": self.manifest.site.traversal.page_key_rule_digest,
                        "row_key": self.manifest.site.traversal.row_key_rule_digest,
                        "ordinal": self.manifest.site.traversal.ordinal_rule_digest,
                        "total": self.manifest.site.traversal.total_rule_digest,
                        "terminal": self.manifest.site.traversal.terminal_rule_digest,
                    }
                ))
            or self.region.policy_id != self.manifest.site.policy.policy_id
            or self.region.policy_digest != self.manifest.site.policy.digest
            or any(rule.observation.region_id != self.region.region_id
                   for rule in self.manifest.site.steps)
            or self.site.bootstrap(self.manifest.skill) != self.manifest.site_plan()
            or self.registration_digest() != self.manifest.site.source.fixture_digest
        ):
            raise ValueError("browser_local_site_registration_invalid")


def derive_registered_teaching_site(
    source: RegisteredLocalSite, candidate: BrowserPublicationManifest,
) -> RegisteredLocalSite:
    """Bind a reviewed route to the unchanged local primitive implementation.

    The caller still needs the publication store's prepared/active authority,
    current grants and the exact candidate's review before installing a Run.
    """
    candidate = BrowserPublicationManifest.model_validate_json(
        candidate.model_dump_json()
    )
    base = source.manifest
    if (
        candidate.source_kind != "registered_site"
        or candidate.effect != "read_only"
        or any(rule.step.effect != "read_only"
               or rule.effect.actual_effect != "read_only"
               for rule in candidate.site.steps)
        or candidate.site.exploration is not None
        or candidate.site.site_id != base.site.site_id
        or candidate.site.source != base.site.source
        or candidate.site.policy != base.site.policy
        or candidate.site.primitive_rules != base.site.primitive_rules
        or not candidate.site.step_primitives
        or candidate.site.read_rule != base.site.read_rule
        or candidate.site.decision_manifest != base.site.decision_manifest
        or candidate.site.decision_budget != base.site.decision_budget
        or candidate.site.navigation_origins != base.site.navigation_origins
        or candidate.verifier != base.verifier
        or candidate.output != base.output
        or candidate.capability_snapshot_json != base.capability_snapshot_json
        or candidate.capability_bindings != base.capability_bindings
    ):
        raise ValueError("browser_teaching_source_mismatch")
    rules = replace(
        source.rules,
        skill_digest=candidate.skill.digest,
        site_digest=candidate.site.digest,
        verifier_digest=candidate.verifier.digest,
    )
    return replace(
        source, manifest=candidate, rules=rules,
        site=FrozenSiteAdapter((candidate.site_plan(),)),
    )


def _json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def local_subject_digest(
    tenant_id: str,
    user_id: str,
    object_type: str = "system_message_collection",
) -> bytes:
    """Synthetic SUBJECT codec for the trusted binding provisioner and live read."""
    return hashlib.sha256(
        _json(
            [
                "browser.synthetic.subject.v1",
                tenant_id,
                user_id,
                object_type,
            ]
        )
    ).digest()


def registered_subject_digest(target_system: str, subject: str) -> bytes:
    """Binding provisioner and live Site reader must agree on this exact codec."""
    if (type(target_system) is not str or not target_system
            or type(subject) is not str or not subject or len(subject) > 256):
        raise ValueError("browser_local_subject_invalid")
    return hashlib.sha256(_json(["browser.registered.subject.v1", target_system, subject])).digest()


def local_deployment_configuration_digest(
    *, provider_key: str, node_executable: Path, playwright_package: Path,
    browsers_path: Path, assets_root: Path, helper_digest: bytes,
    node_digest: bytes, chromium_digest: bytes, enabled: bool,
    timeout_seconds: float, immutable_assets: bool,
) -> str:
    """Bind an offline lock to this exact installed runtime configuration."""
    payload = [
        "browser.local.configuration.v1", provider_key, str(node_executable),
        str(playwright_package), str(browsers_path), str(assets_root),
        helper_digest.hex(), node_digest.hex(), chromium_digest.hex(),
        enabled, timeout_seconds, immutable_assets,
    ]
    return hashlib.sha256(_json(payload)).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class LocalChromiumDeployment:
    provider_key: str
    manifest_digest: bytes
    node_executable: Path
    playwright_package: Path
    browsers_path: Path
    helper_digest: bytes
    node_digest: bytes
    chromium_digest: bytes
    enabled: bool = False
    timeout_seconds: float = 25.0
    assets_root: Path | None = None
    offline_manifest: LocalOfflineManifest | None = None
    immutable_assets: bool = False

    def __post_init__(self) -> None:
        if (
            re.fullmatch(r"[A-Za-z0-9_-]{1,96}", self.provider_key) is None
            or any(
                type(v) is not bytes or len(v) != 32
                for v in (
                    self.manifest_digest,
                    self.helper_digest,
                    self.node_digest,
                    self.chromium_digest,
                )
            )
            or any(
                not isinstance(p, Path) or not p.is_absolute()
                for p in (self.node_executable, self.playwright_package, self.browsers_path)
            )
            or type(self.enabled) is not bool
            or type(self.immutable_assets) is not bool
            or ((self.assets_root is None) != (self.offline_manifest is None))
            or (self.assets_root is not None and not self.assets_root.is_absolute())
            or (self.offline_manifest is not None and bytes.fromhex(
                self.offline_manifest.manifest_digest,
            ) != self.manifest_digest)
            or (self.offline_manifest is not None and any(
                not any(asset.role == role and asset.sha256 == digest.hex()
                        for asset in self.offline_manifest.assets)
                for role, digest in (
                    ("node", self.node_digest),
                    ("chromium", self.chromium_digest),
                    ("helper", self.helper_digest),
                )
            ))
            or not 1 <= self.timeout_seconds <= 30
        ):
            raise ValueError("browser_local_deployment_invalid")
        if (self.offline_manifest is not None and self.offline_manifest.runtime is not None
                and self.assets_root is not None):
            expected = local_deployment_configuration_digest(
                provider_key=self.provider_key, node_executable=self.node_executable,
                playwright_package=self.playwright_package, browsers_path=self.browsers_path,
                assets_root=self.assets_root, helper_digest=self.helper_digest,
                node_digest=self.node_digest, chromium_digest=self.chromium_digest,
                enabled=self.enabled, timeout_seconds=self.timeout_seconds,
                immutable_assets=self.immutable_assets,
            )
            if self.offline_manifest.runtime.configuration_digest != expected:
                raise ValueError("browser_local_deployment_configuration_mismatch")


class LocalPlaywrightDriver:
    """One installed worker's Python transport, never a Run-owned resource."""

    def __init__(self, deployment: LocalChromiumDeployment) -> None:
        self._deployment = deployment
        self._lock = asyncio.Lock()
        self._driver: Any = None
        self._starting: asyncio.Task[Any] | None = None
        self._stopping: asyncio.Task[None] | None = None
        self._state = "new"
        self._suspect = False
        self._probe_lock = asyncio.Lock()
        self._last_probe = 0.0
        self.last_start_ms: float | None = None

    @property
    def ready(self) -> bool:
        return self._state == "ready" and self._driver is not None

    @property
    def admitting(self) -> bool:
        return self._state == "new" or (self.ready and not self._suspect)

    def fail(self) -> None:
        # No automatic restart or new admission after a transport disconnect.
        if self._state == "ready":
            self._state = "failed"

    def suspect(self) -> None:
        # A single Browser disconnect is ambiguous. Pause new admission until
        # a public Playwright API round trip distinguishes it from driver loss.
        if self.ready:
            self._suspect = True

    async def probe(self) -> bool:
        if not self.ready:
            return False
        async with self._probe_lock:
            if not self.ready:
                return False
            if not self._suspect and time.monotonic() - self._last_probe < 1.5:
                return True
            try:
                # This creates no browser or network request. It checks the
                # shared transport through the documented request API.
                request = await asyncio.wait_for(self._driver.request.new_context(), 3)
                await asyncio.wait_for(request.dispose(), 3)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.fail()
                return False
            self._suspect = False
            self._last_probe = time.monotonic()
            return True

    def chromium(self) -> Any:
        if not self.ready:
            raise BrowserReadExecutionError("unavailable")
        return self._driver.chromium

    async def start(self) -> None:
        await self._start()

    async def ensure_started(self) -> None:
        # Direct one-shot callers start on first execution. Later Runs reuse
        # the same owner without adding another shutdown claim.
        await self._start()
        if not await self.probe():
            raise BrowserReadExecutionError("unavailable")

    async def _start(self) -> None:
        async with self._lock:
            if self.ready:
                return
            if self._state != "new":
                raise BrowserReadExecutionError("unavailable")
            started_at = time.monotonic()
            self._state = "starting"
            try:
                if (not self._deployment.enabled or sys.platform != "linux"
                        or any(os.environ.get(name) for name in (
                            "DEBUG", "PWDEBUG", "SSLKEYLOGFILE", "NODE_OPTIONS",
                        ))
                        or importlib.metadata.version("playwright") != "1.63.0"):
                    raise BrowserReadExecutionError("unsupported")
                from playwright.async_api import async_playwright

                self._starting = asyncio.create_task(async_playwright().start())
                self._driver = await asyncio.wait_for(
                    asyncio.shield(self._starting), self._deployment.timeout_seconds,
                )
                self._starting = None
                self._state = "ready"
                self.last_start_ms = round((time.monotonic() - started_at) * 1000, 3)
            except BaseException as error:
                self._state = "failed"
                if isinstance(error, (BrowserReadExecutionError, asyncio.CancelledError)):
                    raise
                if isinstance(error, TimeoutError):
                    raise BrowserReadExecutionError("timeout") from None
                raise BrowserReadExecutionError("unavailable") from None

    async def stop(self) -> None:
        async with self._lock:
            if self._state == "stopped":
                return
            driver, starting = self._driver, self._starting
            self._state = "stopping"
            if starting is not None:
                try:
                    driver = await asyncio.wait_for(asyncio.shield(starting), 3)
                except TimeoutError:
                    self._state = "failed"
                    raise BrowserReadExecutionError("unavailable") from None
                except asyncio.CancelledError:
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelling():
                        self._state = "failed"
                        raise
                    self._starting = None
                    self._state = "stopped"
                    return
                except Exception:
                    # A failed start task produced no driver handle to own.
                    self._starting = None
                    self._state = "stopped"
                    return
                self._driver = driver
                self._starting = None
            if driver is not None:
                if self._stopping is None or self._stopping.cancelled() or (
                    self._stopping.done() and self._stopping.exception() is not None
                ):
                    self._stopping = asyncio.create_task(driver.stop())
                try:
                    await asyncio.wait_for(asyncio.shield(self._stopping), 3)
                except asyncio.CancelledError:
                    self._state = "failed"
                    raise
                except Exception:
                    self._state = "failed"
                    raise BrowserReadExecutionError("unavailable") from None
            self._driver = self._starting = None
            self._stopping = None
            self._state = "stopped"


@dataclass(slots=True, repr=False)
class _NativeChromium:
    process: asyncio.subprocess.Process
    identity: str
    timeout: float
    node_digest: bytes
    chromium_digest: bytes
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    sequence: int = 0
    original: dict[str, object] | None = None
    exited: bool = False

    async def call(self, operation: str) -> dict[str, Any]:
        async with self.lock:
            try:
                if (
                    operation not in {"launch", "status", "stop"}
                    or self.process.returncode is not None
                ):
                    raise ValueError
                self.sequence += 1
                assert self.process.stdin is not None and self.process.stdout is not None
                self.process.stdin.write(
                    _json(
                        {
                            "id": self.sequence,
                            "op": operation,
                            "identity": self.identity,
                            "node_digest": self.node_digest.hex(),
                            "chromium_digest": self.chromium_digest.hex(),
                        }
                    )
                    + b"\n"
                )
                async with asyncio.timeout(self.timeout):
                    await self.process.stdin.drain()
                    line = await self.process.stdout.readline()
                if not line or len(line) > 8192:
                    raise ValueError
                response = json.loads(line)
                if (
                    type(response) is not dict
                    or type(response.get("id")) is not int
                    or response.get("id") != self.sequence
                    or response.get("ok") is not True
                ):
                    raise ValueError
                result = response["result"]
                if (
                    type(result) is not dict
                    or result.get("identity") != self.identity
                    or type(result.get("pid")) is not int
                    or result["pid"] <= 0
                    or re.fullmatch(r"[0-9]{1,32}", str(result.get("start", ""))) is None
                    or re.fullmatch(r"[a-f0-9-]{36}", str(result.get("boot", ""))) is None
                    or result.get("exited") is not (operation == "stop")
                ):
                    raise ValueError
                original = {k: result[k] for k in ("identity", "pid", "start", "boot")}
                if operation == "launch":
                    if self.original is not None:
                        raise ValueError
                    endpoint = urlsplit(result["endpoint"])
                    if (
                        endpoint.scheme != "ws"
                        or endpoint.hostname != "127.0.0.1"
                        or not endpoint.port
                        or endpoint.username
                        or endpoint.password
                        or endpoint.query
                        or endpoint.fragment
                        or not endpoint.path
                    ):
                        raise ValueError
                    self.original = original
                elif original != self.original:
                    raise ValueError
                if operation == "stop":
                    self.exited = True
                return result
            except TimeoutError:
                raise BrowserReadExecutionError("timeout") from None
            except Exception:
                raise BrowserReadExecutionError("unavailable") from None

    async def dispose_helper(self) -> bool:
        # EOF asks this owned helper to shut down its own browser. It never
        # supplies proof if the helper/native exit response was lost.
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            async with asyncio.timeout(3):
                await self.process.wait()
        except TimeoutError:
            return False
        return True


@dataclass(slots=True, repr=False)
class _Resource:
    claim: BrowserLeaseClaim
    session: BrowserSessionRef
    native: _NativeChromium
    reference: bytes
    live: LiveBrowser | None
    page: Any
    source: FixedSyntheticSource | RegisteredLocalSite
    guard: Guard
    stages_ms: dict[str, float] = field(default_factory=dict)
    connection: Any = None
    context: Any = None
    acquire_token: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    exit_token: bytes | None = None
    terminal_at: float | None = None
    faulted: bool = False
    closing: bool = False
    failure_class: str | None = None
    capture_receipt: tuple[
        BrowserProfileContext, BrowserProfileGeneration, bytes, int,
    ] | None = field(default=None, repr=False)
    profile_cleanup_fact_recorded: bool = False


class LocalBrowserResources:
    """Concrete BrowserProviderProofPort + BrowserResourceSubjectPort."""

    def __init__(
        self,
        deployment: LocalChromiumDeployment,
        proof_context: BrowserClaimProofContext,
        cleanup_authority: BrowserCleanupAuthorityPort,
        driver: LocalPlaywrightDriver,
        file_artifacts: LocalFileArtifacts | None = None,
    ) -> None:
        self.deployment, self._proof, self.cleanup_authority = (
            deployment,
            proof_context,
            cleanup_authority,
        )
        self._resources: dict[str, _Resource] = {}
        self._helpers: dict[int, _NativeChromium] = {}
        self.driver = driver
        self.file_artifacts = file_artifacts
        self._assets_verified = False

    def _verify_assets(self) -> float:
        started_at = time.monotonic()
        root, manifest = self.deployment.assets_root, self.deployment.offline_manifest
        if root is None or manifest is None:
            return 0.0
        try:
            # A configuration flag alone cannot establish immutability. Reuse
            # the previous hash pass only for a read-only installed mount.
            statvfs = getattr(os, "statvfs", None)
            cacheable = (
                self.deployment.immutable_assets
                and os.name == "posix"
                and not root.is_symlink()
                and statvfs is not None
                and bool(statvfs(root).f_flag & getattr(os, "ST_RDONLY", 1))
            )
            if self._assets_verified and cacheable:
                return 0.0
            verify_local_offline_assets(root, manifest)
            resolved = root.resolve(strict=True)
            helper = Path(__file__).with_name("managed_chromium.js").resolve(strict=True)
            for asset in manifest.assets:
                path = (resolved / asset.relative_path).resolve(strict=True)
                if ((asset.role == "node" and path != self.deployment.node_executable.resolve())
                        or (asset.role == "helper" and path != helper)
                        or (asset.role == "chromium" and not path.is_relative_to(
                            self.deployment.browsers_path.resolve(),
                        ))
                        or (asset.role == "playwright" and not path.is_relative_to(
                            self.deployment.playwright_package.resolve(),
                        ))):
                    raise ValueError("browser_local_asset_binding_invalid")
        except (OSError, ValueError):
            raise BrowserReadExecutionError("unsupported") from None
        self._assets_verified = True
        return round((time.monotonic() - started_at) * 1000, 3)

    def stage_timings(self, reference: bytes) -> tuple[tuple[str, float], ...]:
        """Fixed resource stages only; no owner, page data or raw exception text."""
        item = self.resource(reference)
        return tuple(sorted(item.stages_ms.items()))

    def failure_class(self, reference: bytes) -> str | None:
        return self.resource(reference).failure_class

    def worker_start_timing(self) -> tuple[str, float] | None:
        """Cold driver start is worker scope, never added to every Run total."""
        value = self.driver.last_start_ms
        return ("driver_cold_start_ms", value) if value is not None else None

    async def start(self) -> None:
        self._verify_assets()
        await self.driver.start()

    async def ensure_started(self) -> None:
        self._verify_assets()
        await self.driver.ensure_started()

    async def stop(self) -> None:
        # EOF contains an unproven native child; it never manufactures exit proof.
        unsettled = False
        for native in tuple(self._helpers.values()):
            if native.process.returncode is None:
                try:
                    if not await native.dispose_helper():
                        unsettled = True
                except Exception:
                    unsettled = True
        if unsettled:
            raise BrowserReadExecutionError("unavailable")
        try:
            await self.driver.stop()
        except Exception:
            raise BrowserReadExecutionError("unavailable")

    def _prune(self) -> None:
        for key, native in tuple(self._helpers.items()):
            if native.process.returncode is not None:
                del self._helpers[key]
        for identity, item in tuple(self._resources.items()):
            if (item.terminal_at is not None
                    and item.native.process.returncode is not None
                    and (item.capture_receipt is None
                         or item.profile_cleanup_fact_recorded)
                    and time.monotonic() - item.terminal_at > 120):
                del self._resources[identity]

    def resource(self, reference: bytes) -> _Resource:
        for item in self._resources.values():
            if hmac.compare_digest(item.reference, reference):
                return item
        raise BrowserLeaseError("browser_local_resource_unknown")

    def _disconnected(self, item: _Resource) -> None:
        if not item.closing and not item.native.exited:
            item.faulted = True
            self.driver.suspect()

    async def launch(
        self,
        claim: BrowserLeaseClaim,
        session: BrowserSessionRef,
        source: FixedSyntheticSource | RegisteredLocalSite,
        *,
        guard: Guard,
        subject_guard: Guard,
        storage_state: dict[str, object] | None = None,
    ) -> _Resource:
        deployment = self.deployment
        stages_ms = {"asset_verify_ms": self._verify_assets()}
        self._prune()
        helper = Path(__file__).with_name("managed_chromium.js")
        from app.infra.browser.fixed_synthetic_seed import FixedSyntheticSource

        synthetic = type(source) is FixedSyntheticSource
        if not synthetic and type(source) is not RegisteredLocalSite:
            raise BrowserReadExecutionError("unsupported")
        if not synthetic:
            manifest = deployment.offline_manifest
            if manifest is None:
                raise BrowserReadExecutionError("unsupported")
            try:
                manifest.require_registered_site_lock()
                if importlib.metadata.version("playwright") != "1.63.0":
                    raise ValueError("browser_local_playwright_version_invalid")
            except (OSError, ValueError, importlib.metadata.PackageNotFoundError):
                raise BrowserReadExecutionError("unsupported") from None
        if synthetic and storage_state is not None:
            raise BrowserReadExecutionError("denied")
        probe_started_at = time.monotonic()
        if not await self.driver.probe():
            raise BrowserReadExecutionError("unavailable")
        stages_ms["driver_probe_ms"] = round(
            (time.monotonic() - probe_started_at) * 1000, 3,
        )
        if (
            not deployment.enabled
            or sys.platform != "linux"
            or not self.driver.ready
            or sum(item.exit_token is None for item in self._resources.values()) >= 32
            or len(self._helpers) >= 32
            or claim.provider_key != deployment.provider_key
            or any(
                os.environ.get(name)
                for name in ("DEBUG", "PWDEBUG", "SSLKEYLOGFILE", "NODE_OPTIONS")
            )
            or hashlib.sha256(helper.read_bytes()).digest() != deployment.helper_digest
        ):
            raise BrowserReadExecutionError("unsupported")
        with deployment.node_executable.open("rb") as binary:
            if hashlib.file_digest(binary, "sha256").digest() != deployment.node_digest:
                raise BrowserReadExecutionError("unsupported")
        await guard()
        identity = self._proof.digest(
            "browser-local-instance.v1",
            _json(resource_identity(claim)) + secrets.token_bytes(32),
        ).hex()
        # No secrets/debug/preload settings are inherited by the browser helper.
        environment = {
            name: os.environ[name] for name in ("PATH", "HOME", "TMPDIR") if name in os.environ
        }
        environment["PLAYWRIGHT_BROWSERS_PATH"] = str(deployment.browsers_path)
        process = await asyncio.create_subprocess_exec(
            str(deployment.node_executable),
            str(helper),
            str(deployment.playwright_package),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=environment,
            limit=8192,
        )
        native = _NativeChromium(
            process,
            identity,
            deployment.timeout_seconds,
            deployment.node_digest,
            deployment.chromium_digest,
        )
        # Ownership begins at subprocess creation, before launch can return a
        # verified identity or an endpoint. Failed starts remain tracked.
        self._helpers[id(native)] = native
        item: _Resource | None = None
        try:
            await guard()
            phase_started_at = time.monotonic()
            result = await native.call("launch")
            stages_ms["native_launch_ms"] = round(
                (time.monotonic() - phase_started_at) * 1000, 3,
            )
            assert native.original is not None
            reference = _json({"schema": "browser.local.resource.v1", **native.original})
            # Register immediately, before any further authority check can fail.
            item = _Resource(
                claim, session, native, reference, None, None, source, subject_guard,
                stages_ms=stages_ms,
            )
            self._resources[identity] = item
            await guard()
            try:
                phase_started_at = time.monotonic()
                browser = await self.driver.chromium().connect(
                    result["endpoint"], timeout=deployment.timeout_seconds * 1000,
                )
                stages_ms["browser_connect_ms"] = round(
                    (time.monotonic() - phase_started_at) * 1000, 3,
                )
            except Exception:
                self.driver.suspect()
                raise
            item.connection = browser
            browser.on("disconnected", lambda _browser: self._disconnected(item))
            await guard()
            phase_started_at = time.monotonic()
            context = await asyncio.wait_for(
                browser.new_context(
                    accept_downloads=(
                        isinstance(source, RegisteredLocalSite)
                        and bool(source.download_expectations)
                        and self.file_artifacts is not None
                    ),
                    service_workers="block",
                    java_script_enabled=not synthetic,
                    **({"storage_state": storage_state} if storage_state is not None else {}),
                ),
                deployment.timeout_seconds,
            )
            stages_ms["context_create_ms"] = round(
                (time.monotonic() - phase_started_at) * 1000, 3,
            )
            item.context = context
            item.live = LiveBrowser(session, browser, context)
            await guard()

            async def route(request: Any) -> None:
                await subject_guard()
                if synthetic and (
                    request.request.url == source.manifest.site.source.origin + "/"
                    and request.request.method == "GET"
                    and request.request.resource_type == "document"
                ):
                    await request.fulfill(
                        status=200, content_type="text/html; charset=utf-8", body=source.html
                    )
                elif not synthetic and navigation_allowed(
                    request.request.url, source.origins,
                ):
                    await request.continue_()
                else:
                    item.faulted = True
                    await request.abort("blockedbyclient")
                await subject_guard()

            await asyncio.wait_for(context.route("**/*", route), deployment.timeout_seconds)
            if not synthetic:
                async def socket_route(socket: Any) -> None:
                    await subject_guard()
                    origin = urlsplit(socket.url)
                    socket_origin = f"{origin.scheme}://{origin.netloc}"
                    if socket_origin in source.websocket_origins:
                        socket.connect_to_server()
                    else:
                        item.faulted = True
                        await socket.close()

                await asyncio.wait_for(
                    context.route_web_socket("**/*", socket_route), deployment.timeout_seconds,
                )
            await guard()
            phase_started_at = time.monotonic()
            page = await asyncio.wait_for(context.new_page(), deployment.timeout_seconds)
            stages_ms["page_create_ms"] = round(
                (time.monotonic() - phase_started_at) * 1000, 3,
            )
            item.page = page
            await guard()
            # Page routes take precedence over the adapter's later context
            # origin route, so continue_() cannot bypass exact-byte fulfillment.
            page.set_default_timeout(deployment.timeout_seconds * 1000)
            if synthetic:
                await asyncio.wait_for(page.route("**/*", route), deployment.timeout_seconds)
            await guard()
            target = (source.manifest.site.source.origin + "/" if synthetic
                      else source.entry_url)
            phase_started_at = time.monotonic()
            await page.goto(target, wait_until="domcontentloaded")
            stages_ms["navigation_ms"] = round(
                (time.monotonic() - phase_started_at) * 1000, 3,
            )
            await guard()
            if synthetic:
                await self.check_subject(claim, reference)
            await guard()
            return item
        except BaseException as error:
            # Release is still forbidden until cleanup's independent grant and
            # native exit proof run. EOF is best-effort containment, not proof.
            if item is not None:
                item.faulted = True
                item.closing = True
                item.failure_class = (
                    error.code if isinstance(error, BrowserReadExecutionError)
                    else error.failure.code if isinstance(error, BrowserOperationError)
                    else "cancelled" if isinstance(error, asyncio.CancelledError)
                    else "timeout" if isinstance(error, TimeoutError)
                    else "unavailable"
                )
            else:
                await native.dispose_helper()
            if isinstance(
                error, (BrowserReadExecutionError, BrowserOperationError, asyncio.CancelledError)
            ):
                raise
            if isinstance(error, TimeoutError):
                raise BrowserReadExecutionError("timeout") from None
            raise BrowserReadExecutionError("unavailable") from None

    async def verify(
        self,
        expected: BrowserProviderExpectation,
        evidence: bytes,
    ) -> BrowserProviderFact:
        item = next(
            (
                entry
                for entry in self._resources.values()
                if evidence in (entry.acquire_token, entry.exit_token)
            ),
            None,
        )
        if (
            item is None
            or type(evidence) is not bytes
            or resource_identity(expected.claim) != resource_identity(item.claim)
            or expected.manifest_digest != self.deployment.manifest_digest
            or expected.challenge
            != self._proof.challenge(
                expected.claim,
                self.deployment.manifest_digest,
            )
            or (expected.resource_ref is not None and expected.resource_ref != item.reference)
        ):
            raise BrowserLeaseError("browser_local_proof_invalid")
        if evidence == item.acquire_token:
            await item.guard()
            await item.native.call("status")
            await item.guard()
            if (item.faulted or item.native.exited or item.live is None
                    or not self.driver.ready or not item.live.browser.is_connected()):
                raise BrowserLeaseError("browser_local_proof_invalid")
            outcome: ProviderOutcome = "acquired"
        else:
            if not item.native.exited or item.exit_token is None:
                raise BrowserLeaseError("browser_local_exit_unproven")
            outcome = "terminated"
        return BrowserProviderFact(
            expected.claim.provider_key,
            expected.claim.operation_id,
            expected.claim.holder_id,
            expected.claim.lease_epoch,
            expected.challenge,
            expected.manifest_digest,
            item.reference,
            outcome,
        )

    async def check_subject(self, claim: BrowserLeaseClaim, resource_ref: bytes) -> None:
        item = self.resource(resource_ref)
        if (
            resource_identity(item.claim) != resource_identity(claim)
            or not isinstance(claim.binding, BrowserBindingFact)
            or item.live is None
            or item.page is None
            or item.native.exited
            or item.faulted
            or not self.driver.ready
            or not item.live.browser.is_connected()
        ):
            raise BrowserLeaseError("browser_local_subject_unavailable")
        await item.guard()
        if isinstance(item.source, RegisteredLocalSite):
            if await self.registered_subject_status(item) != "matched":
                raise BrowserLeaseError("browser_local_subject_mismatch")
            return
        if (
            item.page.is_closed()
            or item.page.url != item.source.manifest.site.source.origin + "/"
            or len(item.live.context.pages) != 1
            or item.live.context.pages[0] is not item.page
        ):
            raise BrowserLeaseError("browser_local_subject_mismatch")
        dom = item.source.rules.read
        if dom.object_type is None:
            raise BrowserLeaseError("browser_local_subject_mismatch")
        actual = await asyncio.wait_for(
            item.page.evaluate(
                """({tenant,user,objectType}) => {
              const read = s => { const ns=document.querySelectorAll(s);
                if(ns.length!==1 || !ns[0].isConnected) return null;
                const t=ns[0].textContent; return typeof t==='string' && t.length<=96 ? t : null; };
              return [read(tenant),read(user),read(objectType)];
            }""",
                {
                    "tenant": dom.tenant.selector,
                    "user": dom.user.selector,
                    "objectType": dom.object_type.selector,
                },
            ),
            self.deployment.timeout_seconds,
        )
        await item.guard()
        if (
            actual
            != [
                claim.auth.owner.tenant_id,
                claim.auth.owner.user_id,
                item.source.manifest.site.read_rule.object_type,
            ]
            or local_subject_digest(*actual) != claim.binding.subject_digest
        ):
            raise BrowserLeaseError("browser_local_subject_mismatch")

    async def registered_subject_status(self, item: _Resource) -> str:
        """Return only a closed status; actual account text is never retained."""
        source = item.source
        if not isinstance(source, RegisteredLocalSite):
            raise BrowserLeaseError("browser_local_subject_unavailable")
        claim = item.claim
        if not isinstance(claim.binding, BrowserBindingFact):
            raise BrowserLeaseError("browser_local_subject_unavailable")
        page = item.page
        if (page is None or page.is_closed() or item.faulted or item.native.exited
                or not self.driver.ready or item.live is None
                or not item.live.browser.is_connected()
                or not navigation_allowed(page.url, source.origins)):
            raise BrowserLeaseError("browser_local_subject_unavailable")
        popup_counts: dict[str, int] = {}
        for opened in item.live.context.pages:
            if opened is page:
                continue
            if opened.url == "about:blank" and source.popup_expectations:
                deadline = time.monotonic() + max(
                    rule.wait_ms for rule in source.popup_expectations
                ) / 1000
                while opened.url == "about:blank" and time.monotonic() < deadline:
                    await item.guard()
                    await asyncio.sleep(0.05)
            location = urlsplit(opened.url)
            matched = tuple(
                rule for rule in source.popup_expectations
                if navigation_allowed(opened.url, (rule.origin,))
                and location.path.startswith(rule.path_prefix)
            )
            if (opened.is_closed() or await opened.opener() is not page
                    or len(matched) != 1):
                raise BrowserLeaseError("browser_local_subject_mismatch")
            rule = matched[0]
            popup_counts[rule.step_id] = popup_counts.get(rule.step_id, 0) + 1
            if popup_counts[rule.step_id] > rule.maximum_pages:
                raise BrowserLeaseError("browser_local_subject_mismatch")
        await item.guard()
        frame = page
        for selector in source.subject_frame_selectors:
            frame = frame.frame_locator(selector)
        locator = frame.locator(source.subject_selector)
        count = await asyncio.wait_for(locator.count(), self.deployment.timeout_seconds)
        if count == 0:
            await item.guard()
            return "logged_out"
        if count != 1 or not await locator.is_visible():
            raise BrowserLeaseError("browser_local_subject_mismatch")
        subject = await asyncio.wait_for(locator.inner_text(), self.deployment.timeout_seconds)
        await item.guard()
        if type(subject) is not str or not subject or len(subject) > 256:
            raise BrowserLeaseError("browser_local_subject_mismatch")
        return ("matched" if hmac.compare_digest(
            registered_subject_digest(claim.binding.target_system, subject),
            claim.binding.subject_digest,
        ) else "wrong_account")

    async def capture_profile(
        self,
        item: _Resource,
        context: BrowserProfileContext,
        operation_id: str,
        expected_revision: int,
        cipher: BrowserPayloadCipher,
    ) -> tuple[BrowserProfileContext, BrowserProfileGeneration, bytes, int]:
        source = item.source
        binding = context.claim.binding
        item_binding = item.claim.binding
        if (not isinstance(source, RegisteredLocalSite)
                or not isinstance(binding, BrowserBindingFact)
                or not isinstance(item_binding, BrowserBindingFact)
                or item.capture_receipt is not None
                or item.live is None or item.page is None
                or resource_identity(item.claim) != resource_identity(context.claim)
                or binding.subject_digest != item_binding.subject_digest
                or type(expected_revision) is not int
                or not 0 <= expected_revision < 9_007_199_254_740_991
                or not operation_id):
            raise BrowserProfileError("browser_local_capture_invalid")
        await self.check_subject(context.claim, item.reference)
        state = await asyncio.wait_for(
            item.live.context.storage_state(indexed_db=source.capture_indexed_db),
            self.deployment.timeout_seconds,
        )
        checked = checked_playwright_state(state, source)
        payload: dict[str, object] = {"storage_state": checked}
        encoded = _json(payload)
        if len(encoded) > 1_000_000:
            raise BrowserProfileError("browser_local_capture_budget")
        generation_id = secrets.token_hex(16)
        fact = BrowserProfileCaptureFact(
            generation_id=generation_id,
            binding_revision=binding.binding_revision,
            profile_revision=expected_revision + 1,
            lease_epoch=context.claim.lease_epoch,
            provider_key=context.claim.provider_key,
            capture_operation_id=operation_id,
            manifest_digest=self.deployment.manifest_digest,
            generation_ref_digest=self._proof.digest(
                "browser-local-profile-ref.v1",
                item.reference + generation_id.encode() + operation_id.encode(),
            ),
            subject_digest=binding.subject_digest,
            origin_digest=hashlib.sha256(_json(source.origins)).digest(),
            projection_digest=hashlib.sha256(encoded).digest(),
            captured_bytes=len(encoded),
        )
        generation = cipher.encrypt_profile(
            context.claim.auth.owner, binding, fact, payload,
        )
        evidence = secrets.token_bytes(32)
        await self.check_subject(context.claim, item.reference)
        item.capture_receipt = (context, generation, evidence, expected_revision)
        return item.capture_receipt

    async def verify_capture(
        self,
        context: BrowserProfileContext,
        generation: BrowserProfileGeneration,
        evidence: bytes,
    ) -> BrowserProfileCaptureFact:
        for item in self._resources.values():
            receipt = item.capture_receipt
            if (receipt is not None and receipt[0] == context
                    and receipt[1] == generation and type(evidence) is bytes
                    and hmac.compare_digest(receipt[2], evidence)
                    and resource_identity(item.claim) == resource_identity(context.claim)):
                await self.check_subject(context.claim, item.reference)
                return generation.fact
        raise BrowserProfileError("browser_local_capture_proof_invalid")

    async def check_live_subject(
        self,
        context: BrowserProfileContext,
        generation: BrowserProfileGeneration,
    ) -> None:
        for item in self._resources.values():
            receipt = item.capture_receipt
            if (receipt is not None and receipt[0] == context
                    and receipt[1] == generation
                    and resource_identity(item.claim) == resource_identity(context.claim)):
                await self.check_subject(context.claim, item.reference)
                return
        raise BrowserProfileError("browser_local_subject_proof_invalid")

    async def verify_cleanup(
        self,
        context: BrowserProfileContext,
        generation: BrowserProfileGeneration,
        evidence: bytes,
    ) -> BrowserProfileCleanupFact:
        """Witness this generation's captured context after native termination."""
        if type(evidence) is not bytes or len(evidence) != 32:
            raise BrowserProfileError("browser_local_profile_cleanup_proof_invalid")
        await self.cleanup_authority.check_cleanup(context.claim)
        fact = generation.fact
        for item in self._resources.values():
            receipt = item.capture_receipt
            if (receipt is None or receipt[0] != context
                    or receipt[1] != generation
                    or resource_identity(item.claim) != resource_identity(context.claim)
                    or item.exit_token is None
                    or not hmac.compare_digest(item.exit_token, evidence)
                    or not item.native.exited or item.native.process.returncode is None
                    or item.terminal_at is None or item.live is not None
                    or item.context is not None or item.page is not None
                    or fact.provider_key != context.claim.provider_key
                    or fact.lease_epoch != context.claim.lease_epoch
                    or fact.manifest_digest != self.deployment.manifest_digest):
                continue
            expected_ref = self._proof.digest(
                "browser-local-profile-ref.v1",
                item.reference + fact.generation_id.encode()
                + fact.capture_operation_id.encode(),
            )
            if not hmac.compare_digest(fact.generation_ref_digest, expected_ref):
                break
            proof_digest = self._proof.digest(
                "browser-local-profile-cleanup.v1",
                _json({
                    "owner": [context.claim.auth.owner.tenant_id,
                              context.claim.auth.owner.user_id,
                              context.claim.auth.owner.session_id],
                    "run_id": context.run_id,
                    "provider_key": fact.provider_key,
                    "capture_operation_id": fact.capture_operation_id,
                    "generation_id": fact.generation_id,
                    "generation_ref_digest": fact.generation_ref_digest.hex(),
                    "native_exit_token": evidence.hex(),
                }),
            )
            await self.cleanup_authority.check_cleanup(context.claim)
            return BrowserProfileCleanupFact(
                provider_key=fact.provider_key,
                capture_operation_id=fact.capture_operation_id,
                generation_id=fact.generation_id,
                generation_ref_digest=fact.generation_ref_digest,
                proof_digest=proof_digest,
            )
        raise BrowserProfileError("browser_local_profile_cleanup_proof_invalid")

    async def terminate(self, item: _Resource) -> bytes:
        teardown_started_at = time.monotonic()
        await self.cleanup_authority.check_cleanup(item.claim)
        if item.exit_token is None:
            item.closing = True
            if item.connection is not None:
                try:
                    if item.live is not None:
                        await asyncio.wait_for(item.live.close(), 3)
                    else:
                        try:
                            if item.context is not None:
                                await asyncio.wait_for(item.context.close(), 3)
                        finally:
                            await asyncio.wait_for(item.connection.close(), 3)
                except BaseException:
                    # Context/connection closure is not native termination proof.
                    item.faulted = True
            if not item.native.exited:
                try:
                    await item.native.call("stop")
                except BrowserReadExecutionError:
                    await item.native.dispose_helper()
                    raise
            await self.cleanup_authority.check_cleanup(item.claim)
            if not item.native.exited:
                raise BrowserLeaseError("browser_local_exit_unproven")
            item.exit_token = secrets.token_bytes(32)
            item.terminal_at = time.monotonic()
            await item.native.dispose_helper()
            item.live = item.page = item.connection = item.context = None
            item.stages_ms["teardown_ms"] = round(
                (time.monotonic() - teardown_started_at) * 1000, 3,
            )
        return item.exit_token


class LocalBrowserReadLifecycle:
    def __init__(
        self,
        factory: LocalBrowserReadExecutionFactory,
        cleanup_authority: BrowserCleanupAuthorityPort,
        *,
        no_resource: Callable[[RunSnapshot], Awaitable[bool]] | None = None,
        no_resource_in_session: Callable[[object, RunSnapshot], Awaitable[bool]] | None = None,
    ) -> None:
        self._factory, self._authority = factory, cleanup_authority
        self._no_resource, self._no_resource_in_session = no_resource, no_resource_in_session
        self._cleanup: dict[str, tuple[RunSnapshot, RunCleanup, float, bool]] = {}
        self._cancel: dict[str, tuple[RunSnapshot, float, bool]] = {}

    async def lookup_capture(
        self,
        run: RunSnapshot,
        checkpoint: BrowserWorkerCheckpoint,
    ) -> BrowserCaptureResolution:
        fresh = await checkpoint.refresh()
        factory = self._factory
        if not isinstance(factory.source, RegisteredLocalSite):
            return BrowserCaptureResolution("unsupported")
        state = factory.state_for_cleanup(fresh)
        if (state is None or state.claim is None or state.resource is None
                or not factory.claim_matches_run(state.claim, fresh)):
            return BrowserCaptureResolution("unknown")
        await factory._fence(state)
        receipt = state.resource.capture_receipt
        if receipt is None:
            return BrowserCaptureResolution(
                "absent" if fresh.capture_status == "prepared" else "unknown",
            )
        context, generation, evidence, revision = receipt
        if (generation.fact.capture_operation_id != fresh.capture_operation_id
                or context.run_id != fresh.run_id):
            return BrowserCaptureResolution("unknown")
        return BrowserCaptureResolution("found", context, generation, evidence, revision)

    async def send_capture(
        self,
        run: RunSnapshot,
        checkpoint: BrowserWorkerCheckpoint,
    ) -> BrowserCaptureResolution:
        fresh = await checkpoint.refresh()
        factory = self._factory
        if not isinstance(factory.source, RegisteredLocalSite):
            return BrowserCaptureResolution("unsupported")
        state = factory.state_for_cleanup(fresh)
        if (state is None or state.claim is None or state.resource is None
                or state.principal is None or state.profile_revision is None
                or fresh.capture_status != "sent"
                or not factory.claim_matches_run(state.claim, fresh)
                or factory._cipher is None):
            return BrowserCaptureResolution("unknown")
        await factory._fence(state)
        if state.resource.capture_receipt is not None:
            return await self.lookup_capture(fresh, checkpoint)
        operation = state.run.capture_operation_id
        if type(operation) is not str or not operation:
            return BrowserCaptureResolution("unknown")
        context = BrowserProfileContext(
            state.principal, state.claim, fresh.task_id, fresh.run_id,
            state.run.worker_epoch,
        )
        receipt = await factory.resources.capture_profile(
            state.resource, context, operation, state.profile_revision, factory._cipher,
        )
        await factory._fence(state)
        return BrowserCaptureResolution("found", *receipt)

    async def cleanup(self, run: RunSnapshot) -> RunCleanup:
        key = BrowserBindingKey(
            run.owner.tenant_id,
            run.owner.user_id,
            run.admission.target_system,
            run.admission.binding_id,
        )
        await self._authority.check_recovery(key)
        leases = self._factory.authority()[0]
        state = self._factory.state_for_cleanup(run)
        if (self._no_resource is not None
                and (state is None or (state.claim is None and state.resource is None))
                and await self._no_resource(run)):
            # Durable absence, not memory absence or a failed recover_cleanup.
            # Own reservation_only claims still take the original cleanup path.
            self._remember_cleanup(run, "released", no_resource=True)
            if state is not None:
                await self._factory.release_account_gate(state)
            return "released"
        outcome: RunCleanup = "quarantined"
        if state is not None and state.released:
            if state.cleanup_outcome not in {"terminated", "released"}:
                raise BrowserReadExecutionError("unavailable")
            outcome = state.cleanup_outcome
        else:
            claim = state.claim if state is not None else await leases.recover_cleanup(key)
            if claim is None or not self._factory.claim_matches_run(claim, run):
                raise BrowserReadExecutionError("denied")
            await self._authority.check_cleanup(claim)
            if state is not None and state.resource is not None:
                state.cancellation.set()
                async with state.dispatch_lock:
                    try:
                        evidence = await self._factory.resources.terminate(state.resource)
                    except (BrowserReadExecutionError, BrowserLeaseError):
                        await self._authority.check_cleanup(claim)
                        # The store retains quota when acquisition was sent and
                        # no original-process exit proof can be supplied.
                        await leases.cleanup(claim)
                        self._remember_cleanup(run, "quarantined")
                        return "quarantined"
                    receipt = state.resource.capture_receipt
                    if receipt is not None and not state.resource.profile_cleanup_fact_recorded:
                        context, generation, _capture_evidence, _revision = receipt
                        profiles = self._factory._profiles
                        if (profiles is None or state.profile_context != context
                                or context.run_id != run.run_id):
                            raise BrowserReadExecutionError("unavailable")
                        # The native exit token is still independently verifiable.
                        # Persist this generation's durable receipt before release;
                        # failure retains the lease and the local proof for retry.
                        try:
                            await profiles.record_provider_cleanup_fact(
                                context, generation.fact.generation_id, evidence,
                            )
                        except BrowserProfileError as error:
                            # A sent provider capture can precede the atomic
                            # validated generation/Run write. If no generation
                            # was ever durably linked, there is no DB payload
                            # to retain or later delete. Other proof failures
                            # keep the lease and local evidence for recovery.
                            if (error.code not in {
                                    "browser_profile_generation_missing",
                                    "browser_profile_cleanup_run_mismatch",
                                }
                                    or run.profile_generation_id is not None):
                                raise
                        state.resource.profile_cleanup_fact_recorded = True
                    await self._authority.check_cleanup(claim)
                    if await leases.cleanup(claim, evidence):
                        state.released = True
                        state.terminal_at = time.monotonic()
                        state.key = ""
                        state.observer = None
                        state.cleanup_outcome = "terminated"
                        outcome = "terminated"
            else:
                # Store proves never-sent reservations itself. A recovered
                # acquiring/unknown process cannot be rediscovered from a PID.
                if await leases.cleanup(claim):
                    outcome = "released"
                    if state is not None:
                        state.released = True
                        state.terminal_at = time.monotonic()
                        state.cleanup_outcome = "released"
                        state.key = ""
                else:
                    outcome = "quarantined"
        self._remember_cleanup(run, outcome)
        if state is not None and outcome in {"released", "terminated"}:
            await self._factory.release_account_gate(state)
        return outcome

    def _remember_cleanup(
        self, run: RunSnapshot, outcome: RunCleanup, *, no_resource: bool = False,
    ) -> None:
        now = time.monotonic()
        for run_id, (_, _, deadline, _) in tuple(self._cleanup.items()):
            if deadline <= now:
                del self._cleanup[run_id]
        if len(self._cleanup) >= 32 and run.run_id not in self._cleanup:
            raise BrowserReadExecutionError("unavailable")
        # Private proof provenance only; never persisted or accepted as proof.
        self._cleanup[run.run_id] = (run, outcome, now + 120, no_resource)

    async def stop(self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint) -> None:
        fresh = await checkpoint.refresh(allow_cancel=True)
        if (
            not fresh.cancel_requested
            or fresh.admission != run.admission
            or fresh.worker_id != run.worker_id
            or fresh.worker_epoch != run.worker_epoch
        ):
            raise BrowserReadExecutionError("denied")
        if await self.cleanup(fresh) not in {"released", "terminated"}:
            raise BrowserReadExecutionError("unavailable")
        fresh = await checkpoint.refresh(allow_cancel=True)
        for run_id, (_, deadline, _) in tuple(self._cancel.items()):
            if deadline <= time.monotonic():
                del self._cancel[run_id]
        if len(self._cancel) >= 32 and fresh.run_id not in self._cancel:
            raise BrowserReadExecutionError("unavailable")
        self._cancel[fresh.run_id] = (
            fresh, time.monotonic() + 120, self._cleanup[fresh.run_id][3],
        )

    async def check_cancel(self, run: RunSnapshot) -> None:
        emission = self._cancel.get(run.run_id)
        if (
            emission is None
            or emission[1] <= time.monotonic()
            or emission[0].admission != run.admission
            or emission[0].worker_id != run.worker_id
            or emission[0].worker_epoch != run.worker_epoch
            or emission[0].lease_epoch != run.lease_epoch
            or not run.cancel_requested
            or not run.cancel_acknowledged
        ):
            raise BrowserReadExecutionError("denied")

    async def check_cancel_in_session(self, transaction: object, run: RunSnapshot) -> None:
        await self.check_cancel(run)
        if self._cancel[run.run_id][2]:
            if (self._no_resource_in_session is None
                    or not await self._no_resource_in_session(transaction, run)):
                raise BrowserReadExecutionError("denied")

    async def check_cleanup(
        self,
        transaction: object,
        run: RunSnapshot,
        outcome: RunCleanup,
    ) -> None:
        # The separately injected SQL cleanup_authorize callback runs first. This
        # Resource proofs remain local. The no-resource branch additionally
        # rereads durable evidence in this already-locked transaction, with no
        # provider IO or new locks/session.
        emission = self._cleanup.get(run.run_id)
        if (
            emission is None
            or emission[2] <= time.monotonic()
            or emission[1] != outcome
            or emission[0].admission != run.admission
            or emission[0].provider_key != run.provider_key
            or emission[0].lease_epoch != run.lease_epoch
        ):
            raise BrowserReadExecutionError("denied")
        if emission[3]:
            if (self._no_resource_in_session is None
                    or not await self._no_resource_in_session(transaction, run)):
                raise BrowserReadExecutionError("denied")
