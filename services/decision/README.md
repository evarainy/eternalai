# Local browser Decision source checkpoint

This package implements the existing `LocalChoiceCodec` contract at `POST /select`.
It is independent of the main Runtime LLM and does not start a listener, import a
model library, download weights, or create a second serving stack on import.

`checkpoint_inventory.json` is **WAITING_ENV**, not a launch manifest. Its observed
`cklxx/laya-browser` commit and file hashes are candidate source metadata, not an
operator-approved checkpoint or downloaded weight verification. Synthetic tests are explicitly served as
`synthetic-decision-test-v1`; they provide no model-quality or Arbiter evidence.

## Model and serving boundaries

The preferred repository remains [cklxx/laya-browser](https://huggingface.co/cklxx/laya-browser).
Its current card describes both high-level operation selection and a typed-question
interface. This service permits only target choice: no `decide(page, goal, history)`,
raw page text, operation selection, value generation or auxiliary text model is
imported. Compatibility of our restricted input with the eventual pinned checkpoint
requires actual A1 evidence. The specified challenger is
[ichenney/laya-browser-v32b](https://huggingface.co/ichenney/laya-browser-v32b); its
card currently shows a `v32b` subfolder. Neither mutable card freezes our revision,
tokenizer, dtype, input adapter or calibration.

The local `LayaEngine` uses the checkpoint's documented `predict(state, questions)`
choice interface through one pinned `laya` installation. It does not run operation
selection, free-form text generation, Arbiter, a second model or a cloud endpoint.
The [fixed Arbiter engine contract](https://raw.githubusercontent.com/0xBakeer/arbiter/bf71358774c85aeff2051ec7b1dcc4577a87706d/engines/README.md)
is retained as an A0 reference only. A1 checkpoint parity, A2 serving isolation,
A3 mixed batches, A4 offline/resources and A5 business quality remain WAITING_ENV.
Missing GPU or weights is not a technical NO-GO.

## Registration and offline preflight

An operator-approved `PinnedCheckpoint` must include the exact checkpoint/tokenizer
revisions, subfolder, dtype, device, `laya_eager` backend, wire-schema and
projection-policy digests, calibration, Python/runtime versions, and exact paths,
sizes and SHA256 values for weights,
tokenizer, configuration, audited model code, input adapter, calibration, dependency
lock, its exact wheel lock and checkpoint license/notice. Runtime dependency notices
also need an operator-reviewed bundle. `digest()` canonicalizes JSON and excludes only the
object's own digest;
nested digests remain covered. Never use this public hash for private parameter values.

The calibration artifact must parse as `CalibrationEvidence` and equal the manifest's
checkpoint-bound evidence, including the `laya_answer_confidence` semantics and
SHA256 of the exact input-adapter artifact. That artifact must parse as
`decision_laya_input.v1`, pinning the choice instruction and minimal state format.
The dependency-lock artifact must parse as `DependencyLock`, bind the SHA256 of its
`wheel_lock` artifact, and match the exact checkpoint, Python version and runtime
requirements. The wheel lock must contain one hash-pinned wheel for every runtime
package, including transitive dependencies. Registration
alone is insufficient: the offline checker checks installed versions and reads only
listed artifacts under the explicitly authorized absolute root. It rejects links,
junctions, path escapes, forbidden paths, missing files and mismatched bytes. It does
not execute artifact code. The approved artifact root must remain immutable during
subsequent trusted model loading; the preflight receipt is not evidence of loaded weights.

## Reproducible CPU reference, separate from checkpoint approval

`requirements-linux-cpu.in` pins the direct packages; `pylock.linux_cpu.toml` pins
all 35 resolved wheels, exact versions, source URLs and SHA256 values for **CPython
3.12, Linux x86_64, glibc 2.28+, CPU PyTorch**. It was resolved from public PyPI and
PyTorch metadata with uv 0.12.13 using `--torch-backend cpu --only-binary :all:`;
no package, model or weight was installed or run. It does not lock a specific Python
patch release, the company's unconfirmed server, CUDA/GPU drivers or checkpoint
quality. The selected deployment must bind its actual Python patch and device in
`PinnedCheckpoint`, carry the wheel lock as a hashed artifact, enumerate **all**
installed runtime package versions from that lock in `DependencyLock` and
`PinnedCheckpoint.runtime`, and pass the existing offline preflight before loading.
Choosing another hardware target requires a separately resolved and reviewed lock.

`license_sources.json` records each of the 35 exact package versions' PyPI license
metadata source. These declarations do not contain actual wheel notice bytes and
are not a legal approval. The approved air-gap bundle has this layout:

```text
<approved-bundle-root>/
  <35 exact wheel filenames from pylock.linux_cpu.toml>
  notices.json
  notices/<one approved notice file per package>
```

`notices.json` uses `decision_wheel_notices.v1` with `lock_sha256` and exactly one
entry per wheel: `name`, `version`, `wheel_sha256`, `source_url` (the exact PyPI
version metadata URL), `license_id`, `notice_path` under `notices/`, and
`notice_sha256`. An operator must extract and review the actual license and
third-party notices from the exact wheels, then hash the chosen notice files.
`wheelhouse.py` rejects missing, extra or changed wheels, lock/notice mismatches,
unsafe paths and missing notices without importing model packages or contacting an
index. This is a pre-install integrity check; it does not prove installation,
license sufficiency, model loading or network isolation.

```powershell
python -m services.decision.wheelhouse --bundle-root <absolute-approved-bundle-root>
```

The verifier exits `0` only when the local bundle is complete, `2` for missing
materials, and `1` for invalid materials. On the approved **Linux CPU reference**
target, a fresh isolated Python 3.12 environment may then install only the verified
wheel files with a local-only installer (`--no-index`, `--no-deps` and egress deny),
followed by exact installed-version and checkpoint preflight checks. The installer
and actual wheelhouse remain untested in this stage. Do not add these dependencies
to the main Runtime's vLLM environment or treat this CPU lock as a GPU lock.

The preferred candidate snapshot in `checkpoint_inventory.json` records HF commit
`645cf366a2ae35f1086e8c20eff48f909bb49206`, two LFS SHA256 values, and SHA256
of five small public metadata/code files at that commit. The LFS values came from
the repository tree API; neither weights nor tokenizer bytes were downloaded. The
model card declares Apache-2.0, but the observed root tree has no license notice
file. Approval of that exact revision, notice bytes, audited code, dtype, calibration
and real inference still requires separate evidence. Keep `launchable=false` until
the full checkpoint manifest and bundle can actually pass preflight and target
acceptance.

From the approved isolated source export, using its existing Python environment:

```powershell
python -m services.decision.offline --manifest <absolute-approved-manifest.json> --artifact-root <absolute-approved-artifact-root>
python -m pytest -q tests/services/decision/
python -m ruff check services/decision tests/services/decision
python -m mypy --explicit-package-bases app/ services/decision/
```

The offline command exits `0` for preflight READY, `2` for WAITING_ENV, or `1` for an
actual validation failure. It emits fixed codes and no artifact contents. It neither
installs dependencies nor changes environment variables. A real offline cold start,
no-download/egress proof and resource measurements still require the selected runtime,
weights and authorized environment. The existing main application lock is not a
substitute for the selected model runtime's audited offline dependency bundle.

## Trusted composition and lifetime

Trusted composition calls `DecisionService.from_local_checkpoint(...)` with an
operator-pinned manifest, absolute artifact root, explicit `cpu` or `cuda`, approved
projection policy, current authorizer and serving limits. This runs the exact-file
offline preflight, requires pinned `laya`, `torch`, `transformers`, `safetensors` and
`huggingface-hub` versions, and loads only an existing local checkpoint directory.
`HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` must already be effective in the
process; the deployment must also deny external network and mount artifacts read-only.
Those library flags alone are not a network isolation proof. A missing checkpoint,
wrong dependency or fallback device/dtype rejects startup. `DecisionService.checkpoint`
remains the lower-level already-loaded-engine seam. `DecisionService.synthetic(...)`
accepts only a synthetic backend and fixes its deployment identity. Request strings
cannot select a mode or bypass startup checks.

`create_app(service, authorize=...)` requires current HTTP authorization; omission
denies requests. The service separately requires current input authorization and an
approved projection policy. Model input contains only approved criteria and candidate
IDs, roles and labels/context. Scope epochs are echoed by the service but excluded
from model input; owner, credentials, URLs and raw values have no wire fields. The
caller remains responsible for current scope and action authorization at dispatch.

Capacity is process-local and bounds actual active inference, not merely waiting HTTP
requests. Timeout, cancellation or disconnect signals the backend and cancels its task;
a backend that suppresses cancellation retains its slot until it actually finishes.
Late results are discarded. Blocking model work runs off the event loop; a cancelled
await retains quota until the actual model forward finishes. The
owner must await `service.close(...)`; `False` means work remains and must not be
reported as released. There is no retry, fallback or automatic model reload.

HTTP failures retain fixed codes: 401 unauthorized, 412 model mismatch, 422 unsupported input,
429 overloaded, 504 timeout, 499 cancelled, 502 invalid output and 503 unavailable.
No backend exception or rejected request body is echoed. Request/response byte limits,
exact option sets, finite rounded choice probabilities, deployment identity,
checkpoint digest and explicit score semantics are checked. Laya's
`answer_confidence` is used as `certainty` only under checkpoint-bound calibration
evidence; its normalized-entropy `confidence`, selected choice probability, raw
logits and rank values are different quantities and cannot be substituted. A
truncated state or collapsed option set is rejected. Ties become ambiguous and scores
below the registered threshold become abstained. The threshold and Chinese quality
still require an independent held-out calibration set; `WAITING_ENV` inventory cannot
be started as a production model.

The existing browser `DecisionDeployment` registry and composition code still permit
only synthetic dispositions. Formal deployment integration must add a trusted
`local_model` registration pinned to this manifest and a local endpoint with no
remote fallback, plus the externally enforced network and read-only mount. The
main Runtime LLM retains its existing vLLM raw JSON configuration. The deterministic
read path is measured separately from model accuracy.
