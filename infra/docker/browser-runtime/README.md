# Browser worker container (manual operator start)

This image runs the existing durable browser worker supervisor. It does not run
the Runtime API, create a tenant, migrate a database, seed publications, or
grant browser permissions. The CLI is disabled by default. The Compose service
has an opt-in `operator-browser-worker` profile and supplies `--enable` only
when an operator explicitly starts that service. There is no automatic start,
paid model call, or permission fallback.

The operator must install a reviewed, code-only module at the host path supplied
as `BROWSER_OPERATOR_MODULE`. Compose mounts that **existing file** read-only at
`/opt/operator/browser_worker_installation.py`; a missing file makes startup
fail. The module must expose a zero-argument `open_components()` async context
manager that yields `BrowserVerticalComponents`. It should use the existing Host
dependency injection and call
`app.infra.browser.local_installation.build_local_browser_vertical` with complete
`LocalBrowserInstallationDependencies`. That builder returns `None` when disabled and rejects invalid enabled
configuration; the operator manager must fail startup in either case. The
operator must provide real approved keys, grants, and runtime configuration and
close its HTTP decision client and database engine on exit. No placeholder authority or
key is included in this image or Compose file. Supply runtime secrets through
the operator's local hidden console or the existing secret authority, never in
the mounted Python module, image, or Compose configuration.

The service enables stdin and a TTY solely so the operator can enter an
OpenRouter key through the existing `prompt_openrouter_key` helper during a
local, interactive start. The operator must be present at that terminal; an
assistant tool or unattended background launch cannot supply the key. The
helper rejects non-interactive input. Missing keys fail closed without printing
secret text or searching for a `.env` file. This input capability does not
authorize a paid model call; the operator must separately approve that call.

The service joins the existing task PostgreSQL network
`eternalai_browser_v42_test_default`. Inside that network, the verified database
endpoint is `postgres:15432/eternalai_test`. Before starting the worker, the
existing API must also target the same explicitly authorized shared queue, or
the operator must confirm another queue entry point. The service also joins the
ordinary Compose-managed bridge `eternalai_browser_v42_worker_egress` for
OpenRouter outbound connectivity. That bridge would be created only when this
service is started after the operator confirms its scope. It is **not** an
egress allowlist or firewall. The shared API queue endpoint still needs
deployment confirmation; the worker must not point at the host MCP/test DB port
`15432` as a substitute. This Compose file publishes no host port and mounts no
Docker socket.

The Playwright image includes browser system dependencies. Its Python package
dependencies come from the checked-in `uv.lock` with `uv sync --frozen --no-dev
--no-install-project`; the image installs the operator-checked `uv==0.12.13`.
`Dockerfile.dockerignore` excludes the repository build context by default and
re-includes only exact tracked `app/` files, the three named local browser
modules, `managed_chromium.js`, the worker entry script, `pyproject.toml`, and
`uv.lock`, plus their parent directories. Any build-context change needs an
audit and an explicit allowlist update. Only `app/`, the worker entry script,
`pyproject.toml`, and `uv.lock` are copied into the image. The operator module
is a separate read-only runtime mount. No `.env`, Git data, raw capture, test
tree, or whole repository is sent as ordinary build context or copied.

No container build, pull, start, live network call, or database operation is
part of this repository change. A network-none probe would not prove the final
dual-network execution path, so it cannot be treated as a production run.
