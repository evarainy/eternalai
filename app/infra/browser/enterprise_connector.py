"""Enterprise preflight; no speculative launch while resource codecs are unknown."""

from __future__ import annotations

import importlib.metadata
import os
from collections.abc import Callable
from typing import Never

from app.infra.browser.browserless_wire import BrowserProviderError
from app.infra.browser.enterprise_manifest import (
    DeploymentView,
    EnterpriseManifest,
    require_current_view,
)
from app.infra.browser.enterprise_wire import require_verified_codec


class EnterpriseConnector:
    def preflight(
        self, manifest: EnterpriseManifest, view: DeploymentView, now: float,
    ) -> None:
        require_current_view(manifest, view, now, "restore", acceptance=True)
        unsafe_options = ("DEBUG", "PWDEBUG", "SSLKEYLOGFILE", "NODE_OPTIONS")
        if any(os.environ.get(key) for key in unsafe_options):
            raise BrowserProviderError("unsupported", "restore", "transport")
        try:
            installed = importlib.metadata.version("playwright")
        except importlib.metadata.PackageNotFoundError:
            raise BrowserProviderError("unsupported", "restore", "transport") from None
        if ".".join(installed.split(".")[:2]) != manifest.client_playwright_version:
            raise BrowserProviderError("unsupported", "restore", "transport")

    async def connect(
        self,
        manifest: EnterpriseManifest,
        view: DeploymentView,
        now: float,
        credential: Callable[[], str],
    ) -> Never:
        self.preflight(manifest, view, now)
        # No direct native launch: until its resource identity/cleanup protocol is
        # evidenced, disconnect could leak a remote browser without a proof handle.
        require_verified_codec(manifest, "session", "restore")
