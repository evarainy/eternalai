# Browser v4.2 structured single-run trial

`compose.yaml` is the existing opt-in API/supervisor definition.
`compose.single-run.yaml` is the manual, one-run override. Use both files and
select each service with `docker compose run`; do not use `up` for this trial.
The override disables the base `browser-worker` supervisor entry point. The
`browser-single-run` command checks for exactly one accepted queued Run,
executes one worker pass, and exits. The `browser-client` submits and later
inspects that Run through the authenticated internal API.

This is a prepared operator runbook, not evidence of a live trial. No Docker
build, container startup, database write, paid request, or network call was
performed while preparing it. The owner must separately authorize those actions
and the `0.01` USD trial budget before using the commands below. The CLI allows
at most one Jev transport attempt for this trial. It cannot guarantee a dollar
billing ceiling; actual cost is reported as unknown.

The image uses the checked-in Dockerfile, `uv.lock`, and exact
`Dockerfile.dockerignore` allowlist. Its explicit script copies are
`bootstrap_browser_synthetic.py`, `manage_browser_synthetic_publication.py`,
`run_browser_synthetic_api.py`, `run_browser_synthetic_worker.py`,
`run_browser_synthetic_client.py`, and `run_browser_synthetic_once.py`; the
application modules come from the audited `app/` context entries. The image
does not copy `.env`, Git data, raw captures, or tests. Every trial service runs
as image user
`pwuser` with a read-only root filesystem, `/tmp` tmpfs, stdin and TTY. The
task-private named volume `browser-v42-single-run-secrets` holds encrypted
operator, business, and deactivation bundles plus private trial receipts. On a
fresh volume, Docker copies the image's `pwuser`-owned, mode `0700` vault parent;
bootstrap creates the task directory and encrypted files. There is no root
chown helper, credential environment variable, host bind mount for the vault,
or automatic reset. Bootstrap, client, and worker need read-write volume access
for creation and once-only receipts; API and publication mount it read-only.

The existing external network `eternalai_browser_v42_test_default` must already
provide the `postgres` alias, port `15432`, database `eternalai_test`, and role
`browser_v42_test`. The bootstrap preflight checks the fixed database identity,
schema revision, initial task state, and uninitialized vault before `--init`
can write. The API, client, bootstrap, and publication services use only that
network. Only `browser-single-run` also joins the ordinary outbound bridge
`browser-model-egress` for its Jev call. That bridge is **not** a destination
allowlist. No service publishes a host port. The API's internal name and CSRF
origin are `browser-synthetic-api:8000` and
`http://browser-synthetic-api:8000`.

The worker alone uses the reviewed Docker seccomp profile from
`BROWSER_V42_SECCOMP_PROFILE`; the other services use Docker's default profile.
The reviewed profile permits the Chromium sandbox's `clone`, `setns`, and
`unshare` calls while keeping `clone3` on `ENOSYS` and `io_uring` denied. The
worker does not use privileged mode, `seccomp:unconfined`, or Chromium
`--no-sandbox`. Stop if the local profile is missing or its SHA-256 differs.

## Owner's private PowerShell console

Run from the repository checkout containing **both** Compose files. Run this
setup in each PowerShell terminal used below. It only sets a process-local,
nonsecret path and checks the local profile before any Compose command.

```powershell
$ErrorActionPreference = 'Stop'
$env:BROWSER_V42_SECCOMP_PROFILE = 'C:/Users/Administrator/AppData/Local/Temp/browser-v42-20261002/sandbox-probe-approved/chromium-seccomp-docker-daa0cb7.json'
$seccompSha256 = (Get-FileHash -LiteralPath $env:BROWSER_V42_SECCOMP_PROFILE -Algorithm SHA256).Hash.ToLowerInvariant()
if ($seccompSha256 -cne 'e67623828ce94bb9f4917d029b1c5b83191f18ecd1c3538da63956b773dd33c5') {
    throw 'Reviewed Chromium seccomp profile hash mismatch; stop.'
}
function Invoke-BrowserV42 {
    & docker compose --project-name browser-v42-single-run `
        --file infra/docker/browser-runtime/compose.yaml `
        --file infra/docker/browser-runtime/compose.single-run.yaml `
        --profile operator-browser-runtime @args
    if ($LASTEXITCODE -ne 0) { throw 'Browser v4.2 Compose step failed; stop and inspect retained state.' }
}
```

The seccomp file above must be the locally reviewed Docker `daa0cb7` profile.
Do not replace it with `unconfined`, a different file, or a permissive fallback.
In the first terminal, parse the merged Compose model, build the shared local
image, perform the read-only bootstrap preflight, and then explicitly initialize
the task. The `--init` command changes the fixed test database and creates the
encrypted vault after hidden passphrase entry. Stop at any nonzero result.
The issued authority tokens expire after one hour; finish publication, the Run,
and deactivation within that window. Expiration fails closed; these commands do
not refresh tokens or overwrite an existing vault.

```powershell
Invoke-BrowserV42 config --quiet
Invoke-BrowserV42 build browser-synthetic-api
Invoke-BrowserV42 run browser-bootstrap
Invoke-BrowserV42 run browser-bootstrap --enable --init
```

Prepare and activate the fixed synthetic publication. These commands prompt
for the vault passphrase and a restricted OpenRouter Jev key in the owner's
terminal. They construct the provider client but send no model HTTP request.
They do write the publication state. The key and passphrase never belong in
arguments, environment variables, logs, or assistant tools.

```powershell
Invoke-BrowserV42 run browser-publication --enable --operation prepare --input-mode structured --operator-vault /run/browser-synthetic-secrets/P2-BROWSER-RUNTIME-V42-001/operator.bundle.enc
Invoke-BrowserV42 run browser-publication --enable --operation activate --input-mode structured --operator-vault /run/browser-synthetic-secrets/P2-BROWSER-RUNTIME-V42-001/operator.bundle.enc
```

Still in the first terminal, start the API in the foreground. Its encrypted
operator bundle and restricted key are entered through hidden prompts. Keep
this terminal open while running the client; the `--use-aliases` option gives
the one-off API container the service DNS name required by the client and CSRF
origin. There is no host port and no background supervisor.

```powershell
Invoke-BrowserV42 run --use-aliases --name browser-v42-single-api browser-synthetic-api
```

Open a second private PowerShell terminal in the same checkout and repeat the
setup block above. After the API is running, submit exactly one Run, execute
the one-off worker once, and inspect the Run once. Each relevant command
prompts for its own hidden vault passphrase; the worker also prompts for the
restricted Jev key. **Only the worker may make a paid Jev request.** The
`trial.jev-attempt.json` receipt is burned before that request, so an uncertain
result must not be retried. The durable database queue, vault, and trial
receipts remain in place.

```powershell
Invoke-BrowserV42 run browser-client --enable --operation submit
Invoke-BrowserV42 run browser-single-run --enable --approved-budget-usd 0.01
Invoke-BrowserV42 run browser-client --enable --operation inspect
```

If any step fails, stop the paid path. An inspect can establish the recorded
state; `Invoke-BrowserV42 run browser-client --enable --operation cancel` is an
explicit option when the Run remains cancellable. Do not re-submit, restart the
worker, reset its receipts, rerun bootstrap, or create a second trial as a
recovery shortcut. Keep the queue, vault, quarantined state, and receipts for
review. A verified result requires the worker's successful exit and the
client's terminal Run view; container startup alone proves neither.

Finally, in the second terminal, deactivate the publication with the separate
deactivation bundle. This prompts only for the vault passphrase and does not
require the signer or provider key. Then stop the exact foreground API
container from the second terminal. If deactivation fails, stop the API and
retain state for explicit recovery; do not report successful deactivation.

```powershell
try {
    Invoke-BrowserV42 run browser-publication --enable --operation deactivate --deactivation-vault /run/browser-synthetic-secrets/P2-BROWSER-RUNTIME-V42-001/deactivation.bundle.enc
}
finally {
    & docker stop browser-v42-single-api
    if ($LASTEXITCODE -ne 0) { throw 'API stop failed; retain state and stop the exact container manually.' }
}
```

Do not run `docker compose down -v`, delete the named volume, remove retained
containers, or modify the fixed database to make a failed attempt green. This
structured path exercises the authenticated browser API and one Jev decision;
it does not exercise chat intent parsing or the default vLLM endpoint.
