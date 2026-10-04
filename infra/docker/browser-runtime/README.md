# Browser v4.2 structured single-run trial

`compose.yaml` is the existing opt-in API/supervisor definition.
`compose.single-run.yaml` is the manual, one-run override. Use both files and
select each service with `docker compose run`; do not use `up` for this trial.
The override disables the base `browser-worker` supervisor entry point. The
`browser-single-run` command checks for exactly one accepted queued Run,
executes one worker pass, and exits. The `browser-client` submits and later
inspects that Run through the authenticated internal API.

This is a prepared operator runbook, not evidence of a live trial. The local
image and task PG listener have been prepared. The authenticated file preflight
and owner task initialization previously completed; paid model dispatch remains
unverified. Use the
commands within the owner's existing authorization, and confirm the required
platform credit cap before paid dispatch. The CLI allows
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

On 2026-10-04, the existing task PG was rebuilt with its original data volume
and authentication mounts. It now listens only on `127.0.0.1,172.30.0.2`, port
`15432`; the internal address is pinned and there are no published host ports.
The active service-only PG configuration is
`C:/Users/Administrator/AppData/Local/Temp/browser-v42-20261002/pg-listener-rebuild-20261004/target-postgres.compose.json`.
Future authorized PG management must use that file, project
`eternalai_browser_v42_test`, the original project directory
`C:/Users/Administrator/AppData/Local/Temp/browser-v42-20261002/docker-test`,
and the repair directory's `empty.env` so relative mounts resolve correctly.
The old `docker-test/compose.yml` still declares loopback-only listening;
recreating PG from it would restore the old connection blocker. The repair
directory also retains the original configuration and before/after evidence.
This PG management file is separate from the two trial Compose files below.

**DB password input has been removed.** The owner approved read-only binding of
the existing task-initializer-generated file
`C:/Users/Administrator/AppData/Local/Temp/browser-v42-20261002/docker-test/private/db-password`
to the code-owned `/run/secrets/task-db-password` path. Only bootstrap,
publication (including deactivation), API, and the once-worker receive this
mount. The Compose bind is read-only with `create_host_path: false`; the client
and disabled supervisor receive no DB credential mount. The fixed-file reader
requires Linux, a regular 64-byte ASCII URLsafe file, and a read-only mount. It
accepts this approved Windows bind's existing `0777`, root-owned metadata.
Missing or invalid files and mounts fail with a fixed suppressed error code,
without TTY, environment, argv, or other-file fallback. The password goes only
into the database driver's in-memory `connect_args["password"]`. The fixed
database remains `eternalai_test` at `postgres:15432`, role `browser_v42_test`.
Default container invocation retains its separate hidden vault passphrase and
OpenRouter key prompts. The explicit owner launcher below supplies the same
inputs through an anonymous stdin pipe.

Use this single read-only preflight to establish authenticated access through
the approved file binding after building the updated image. It uses the
explicit approved DB-file mount. It takes no password input and checks DB
identity, schema, task state, and the still-uninitialized vault; it does not
initialize or call a model.

```powershell
docker run --name browser-v42-db-file-preflight --network eternalai_browser_v42_test_default --user pwuser --read-only --tmpfs /tmp:rw,size=512m,mode=1777 --mount type=bind,source=C:/Users/Administrator/AppData/Local/Temp/browser-v42-20261002/docker-test/private/db-password,target=/run/secrets/task-db-password,readonly --mount type=volume,source=browser-v42-single-run-secrets,target=/run/browser-synthetic-secrets,readonly --entrypoint python eternalai-browser-v42:local -m scripts.bootstrap_browser_synthetic
```

Only exit `0` with `browser_bootstrap_preflight_ready` establishes this
preflight's success. A wrong password or any other failure exits nonzero with a
fixed error code; retain the container and stop. The retained
`browser-v42-db-input-preflight` and `browser-v42-single-init` containers have
older immutable images; do not restart them for this file-binding path. Use
the updated trial services below only after the read-only preflight passes.

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
    & docker compose --env-file C:/Users/Administrator/AppData/Local/Temp/browser-v42-20261002/publication-fixed-classification-20261004/empty.env `
        --project-name browser-v42-single-run `
        --file infra/docker/browser-runtime/compose.yaml `
        --file infra/docker/browser-runtime/compose.single-run.yaml `
        --profile operator-browser-runtime @args
    if ($LASTEXITCODE -ne 0) { throw 'Browser v4.2 Compose step failed; stop and inspect retained state.' }
}
```

The seccomp file above must be the locally reviewed Docker `daa0cb7` profile.
Do not replace it with `unconfined`, a different file, or a permissive fallback.

**Current task:** initialization completed on 2026-10-04; do not repeat
`--init` against its existing database and vault. The original conservative
cutoff `2026-10-04T04:48:46Z` has passed. The first approved refresh completed;
its cutoff `2026-10-04T06:56:46Z` has also passed. These input instructions do not
extend it. Keep the existing ciphertext and task state for recovery.
The bootstrap commands below describe fresh-task preparation only.

For fresh-task preparation in the first terminal, parse the merged Compose model, build the shared local
image, perform the read-only bootstrap preflight with the approved DB-file
mount, and then explicitly initialize the task. The `--init` command uses that
same fixed mount, changes the fixed test database, and creates the encrypted
vault after hidden vault passphrase entry. Stop at any nonzero result.
The issued authority tokens expire after one hour; finish publication, the Run,
and deactivation within that window. Expiration fails closed; these commands do
not refresh tokens or overwrite an existing vault.

```powershell
Invoke-BrowserV42 config --quiet
Invoke-BrowserV42 build browser-synthetic-api
Invoke-BrowserV42 run browser-bootstrap
Invoke-BrowserV42 run browser-bootstrap --enable --init
```

For prepare, activate, API and once-worker, use the explicit owner launcher from
`E:/code/eternalai/.worktrees/browser-runtime-v42`. The explicitly authorized
`--phrase-from-exact-field` option projects only `jev-passport` from the fixed
project `.env`, and sends it over the existing anonymous stdin frame. The
`inspect` and `deactivate` operations send a phrase-only frame, without reading
or supplying `jev-key`; their ciphertext paths remain code-owned. After the candidate image is
reviewed and built, set its approved immutable ID and the owner's approved UTC
cutoff in each private terminal. Replace these two public placeholders; do not
put secrets in them. The cutoff must be in `YYYY-MM-DDTHH:MM:SSZ` form and still
in the future. It does not renew or extend any authority token: container-side
authorization continues to verify the existing tokens and their expiry.

```powershell
$browserImageId = 'sha256:<approved-64-lowercase-hex-image-id>'
$browserDeadlineUtc = '<owner-approved-future-UTC-cutoff>'
function Invoke-BrowserJev {
    param([string]$Operation)
    & E:/code/eternalai/.venv/Scripts/python.exe -m scripts.run_browser_synthetic_jev_launcher `
        --operation $Operation --expected-image-id $browserImageId `
        --approved-deadline-utc $browserDeadlineUtc
    if ($LASTEXITCODE -ne 0) { throw 'Browser Jev launcher failed; stop and retain state.' }
}
```

The launcher streams only the exact case-sensitive `jev-key` field from the
fixed `E:/code/eternalai/.env`; it does not load the file as environment variables,
decode other values, interpolate substitutions, copy or mount it. UTF-8 BOM,
CRLF, surrounding assignment spaces, and matching literal quotes are supported.
Missing, empty, duplicate, multiline, control/non-ASCII or oversized keys fail
with a fixed error. It requires the owner's terminal for hidden entry of the
**same existing vault passphrase**, with no echoing or environment fallback.
It checks the worktree, deadline, fixed empty env file, reviewed seccomp profile,
local image ID/user, absent target container and quiet Compose configuration
before reading inputs; image and deadline are rechecked after hidden input.
The public image ID is also passed through `BROWSER_V42_SINGLE_RUN_IMAGE` to pin
the existing Compose services to that immutable image when dispatching; a later
local tag change cannot select a different recipient image.

One bounded, versioned JSON frame carries the passphrase and Jev key only on
the child process's anonymous stdin. The explicit `--private-stdin` branch
unlocks only the fixed operator bundle and retains the existing vault
owner/mode/link checks. No key/passphrase is placed in argv, environment, a file,
or captured output. Child stdout/stderr remain in the owner's private terminal.
This path uses the two existing Compose files, `--no-deps --pull never -T`, fixed
retained names, and no automatic retry/removal. Deactivation, client and the
disabled supervisor are excluded from this secret-input path.

Prepare and activate the fixed synthetic publication. The launcher prompts
for the vault passphrase and reads the approved exact key field; DB
authentication uses the approved read-only file mount. These commands
construct the provider client but send no model HTTP request.
They do write the publication state. The key and passphrase never belong in
arguments, environment variables, logs, or assistant tools.

```powershell
Invoke-BrowserJev prepare
Invoke-BrowserJev activate
```

Still in the first terminal, start the API in the foreground. Its encrypted
operator bundle uses the same hidden passphrase and exact key field; DB authentication uses
the approved read-only file mount. Keep
this terminal open while running the client; the `--use-aliases` option gives
the one-off API container the service DNS name required by the client and CSRF
origin. There is no host port and no background supervisor.

```powershell
Invoke-BrowserJev api
```

Open a second private PowerShell terminal in the same checkout and repeat the
setup blocks above. After the API is running, submit exactly one Run, execute
the one-off worker once, and inspect the Run once. Each relevant command
prompts for its own hidden vault passphrase; the worker's launcher reads the
exact Jev key field and uses the approved DB-file mount. **Only the worker may
make a paid Jev request.** The
`trial.jev-attempt.json` receipt is burned before that request, so an uncertain
result must not be retried. The durable database queue, vault, and trial
receipts remain in place.

```powershell
Invoke-BrowserV42 run browser-client --enable --operation submit
Invoke-BrowserJev once
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
deactivation bundle. This prompts for the vault passphrase and uses the approved
DB-file mount; it does not require the signer or provider key. Then stop the exact foreground API
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

The explicitly approved second diagnosis uses only `--trial-id diagnostic-2`.
The original Run remains failed and its `v1` publication remains inactive at
revision 2. Preparation appends `v2_diagnostic_2`; it preserves the original
publication, ciphertexts, receipts and backup. After approved validation/build,
use the launcher with the reviewed image ID, approved future cutoff and
`--phrase-from-exact-field` for every second-round operation. Run `refresh` once
with the reviewed public `--refresh-script-sha256`, then `prepare`, `activate`,
foreground `api`, `submit`, `once`, `inspect`, and `deactivate`. Refresh mounts
only `C:/Users/Administrator/AppData/Local/Temp/browser-v42-20261002/identity-refresh-diagnostic-2-20261004/refresh_existing_tokens.py`.
It issues the same three identities for 3600 seconds into a private stage, then
publishes the fixed vault child `diagnostic-2` once with `renameNOREPLACE`.
A failure marker takes precedence over a visible success receipt. Refresh,
submit, inspect and deactivate receive phrase-only frames. The worker has one
Jev attempt at USD 0.01; the platform cap remains USD 0.15. On failure retain
all state and stop; no retry, reset, bootstrap or third round. Stop the exact
API container `browser-v42-diagnostic-2-api` after deactivation or its failure.
These instructions do not imply that any second-round operation has run.
