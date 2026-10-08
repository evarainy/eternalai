# Local browser Decision source checkpoint

This package implements the existing `LocalChoiceCodec` contract at `POST /select`.
It is independent of the main Runtime LLM and does not start a listener, import a
model library, download weights, or create a second serving stack on import.

`checkpoint_inventory.json` is **WAITING_ENV**, not a launch manifest. No immutable
checkpoint revision has been selected. Synthetic tests are explicitly served as
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
lock and checkpoint license/notice. Runtime dependency notices also need an
operator-reviewed bundle. `digest()` canonicalizes JSON and excludes only the
object's own digest;
nested digests remain covered. Never use this public hash for private parameter values.

The calibration artifact must parse as `CalibrationEvidence` and equal the manifest's
checkpoint-bound evidence, including the `laya_answer_confidence` semantics and
SHA256 of the exact input-adapter artifact. That artifact must parse as
`decision_laya_input.v1`, pinning the choice instruction and minimal state format.
The dependency-lock artifact must parse as `DependencyLock`
and match the exact checkpoint, Python version and runtime requirements. Registration
alone is insufficient: the offline checker checks installed versions and reads only
listed artifacts under the explicitly authorized absolute root. It rejects links,
junctions, path escapes, forbidden paths, missing files and mismatched bytes. It does
not execute artifact code. The approved artifact root must remain immutable during
subsequent trusted model loading; the preflight receipt is not evidence of loaded weights.

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
