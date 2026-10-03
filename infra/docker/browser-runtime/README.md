# Browser v42 API and worker (manual, offline)

The API and worker use the same local image and the existing durable browser
queue. Both services have the opt-in `operator-browser-runtime` profile. The
ordinary `docker compose up` selects neither service and creates no new
network. The `browser-synthetic-api` service runs
`scripts.run_browser_synthetic_api --enable`, listening on `0.0.0.0:8000` only
inside the task network. Its CSRF origin is
`http://browser-synthetic-api:8000`. Compose publishes no host
port. The worker runs the existing `BrowserWorkerSupervisor` through
`scripts.run_browser_synthetic_worker --enable` and the packaged
`app.infra.browser.synthetic_operator:worker_components` factory. Neither
service creates a tenant, migrates a database, grants permissions, or enables
a paid call by itself. The API Host and operator factory are repository code;
the caller does not need to author an installation module.

Both services attach only to the existing internal task network
`eternalai_browser_v42_test_default`. Their packaged operator wiring targets
the same queue at `postgres:15432/eternalai_test` with role
`browser_v42_test`; `postgres` is the verified network alias. No database
password is stored here. The existing Host dependency injection and secret
authority must supply real authorized dependencies and keys. Missing
installation, grants, or keys fail closed; no `.env` search, generated
permission, placeholder authority, or automatic fallback is provided. The
host MCP/test DB port `15432` is not a queue substitute.

The default profile has no outbound network. No ordinary bridge, proxy,
firewall rule, or new gateway is created by this Compose file. Real OpenRouter
outbound connectivity needs a separately scoped future override and operator
authorization; attaching an ordinary bridge would not be an egress allowlist.
Both services have stdin and a TTY so the operator can use the existing
`prompt_openrouter_key` helper in a local interactive start. An assistant tool
or unattended background process must not supply that key. This capability
does not authorize a paid model call.

The Playwright base is `mcr.microsoft.com/playwright/python:v1.63.0-noble`.
Python dependencies are installed from the checked-in `uv.lock` with
`uv==0.12.13` and `uv sync --frozen --no-dev --no-install-project`. The
Dockerfile-specific `Dockerfile.dockerignore` excludes the repository build
context by default and re-includes only exact tracked `app/` files, the named
local browser modules and `managed_chromium.js`, the packaged synthetic API
Host and operator modules, both runtime entry scripts, the default-off
`manage_browser_synthetic_publication.py` command, `pyproject.toml`, and `uv.lock`, plus
their parent directories. Any context change needs an audit and an exact
allowlist update. The image copies only `app/`, these three scripts, and
the dependency files. It does not copy `.env`, Git data, raw capture, tests,
or the whole repository.

The publication command only calls existing store and authorization methods;
the publication bootstrap has not been run in this batch. Build and launch
evidence must bind to this exact Dockerfile, context allowlist, and image
digest. The earlier worker snapshot built, but its one real Chromium startup
probe returned generic `unavailable`. That failed probe does not establish a
runnable worker or validate these API and operator entry points. The complete
API and worker assembly has not been started.
