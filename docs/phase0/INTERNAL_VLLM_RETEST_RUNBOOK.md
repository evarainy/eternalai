# Internal vLLM Retest Runbook

## Purpose

Prepare and run the internal vLLM retest for the three Phase 0 LLM spike harnesses:

- `experiments/phase0/qwen_structured_output`
- `experiments/phase0/instructor_vllm`
- `experiments/phase0/pydanticai_qwen_vllm`

The retest is faithful and env-driven. Sample sets, prompts, Pydantic schemas, enums, scoring, validation, failure categories, and self-checks remain identical to the public run. Only the deferred activation conditions are controlled by shell environment variables: longer normal timeout, Qwen thinking mode disabled by default, public API pacing removed by default, and shorter inter-run pause.

## Prerequisites

- Target host: Windows 7 with Python 3.8.10. The harness files are kept Python 3.8-compatible.
- Install each spike's dependencies from its own `requirements.txt`.
- Air-gapped note: availability of `instructor` and `pydantic-ai` on the internal mirror is unconfirmed. Use the internal PyPI mirror or pre-downloaded wheels approved for the intranet environment.
- PydanticAI caveat: `pydantic-ai` may not support Python 3.8 because its supported Python floor may be 3.9 or newer. If `python check_env.py` in `pydanticai_qwen_vllm` fails while importing `pydantic_ai`, report that result and run only the raw-SDK plus instructor spikes on the Windows 7 host. Defer the PydanticAI retest to a Python 3.9+ host.

Do not install packages on the retest host by writing dependency state into this repository.

## Environment Setup

Set these variables in the current shell only. Never write them to `.env`, never commit them, and never paste secrets into logs or reports.

Required:

- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`

Optional:

- `LLM_TIMEOUT_S` default `120`
- `LLM_ENABLE_THINKING` default `0` means off
- `LLM_REQUEST_DELAY_S` default `0`
- `LLM_INTER_RUN_PAUSE_S` default `2`
- `LLM_RATE_LIMIT_ABORT` default `3`
- `LLM_MAX_TOKENS` default `2048` (instructor structured output only; raise if `IncompleteOutputException` recurs)
- `LLM_REPORT_TAG` default = model slug (`qwen3.5-27b`->`qwen3.5_27b`, `glm-*`->`glm`); sets the `<tag>` in output filenames. Override only to disambiguate (e.g. two glm versions).

> **Re-run required with this harness version.** Two harness fixes landed after the first internal run, so re-pull this branch before re-testing:
> - **PydanticAI Run B retry is now real.** Run B previously passed `tool_retries=3`, which only governs tool-call retries and had **no** effect on structured-output recovery, so the earlier "Run B 80%" was effectively a no-retry number. It now uses `retries={"output": N}` (output-validation retry budget), the knob that actually re-asks the model on schema-invalid output.
> - **instructor `max_tokens` raised 1024 to 2048 (env-tunable).** The 4 qwen Run-B `api_error`s were `IncompleteOutputException` (output truncated at the token cap, not a network fault). The new default gives long structured output room; raise `LLM_MAX_TOKENS` further if it still appears.

PowerShell:

```powershell
$env:LLM_BASE_URL = "<internal-openai-compatible-base-url>"
$env:LLM_API_KEY = "<internal-api-key>"
$env:LLM_MODEL = "<internal-qwen-model-name>"
$env:LLM_TIMEOUT_S = "120"
$env:LLM_ENABLE_THINKING = "0"
$env:LLM_REQUEST_DELAY_S = "0"
$env:LLM_INTER_RUN_PAUSE_S = "2"
$env:LLM_RATE_LIMIT_ABORT = "3"
$env:LLM_MAX_TOKENS = "2048"
```

Bash:

```bash
export LLM_BASE_URL="<internal-openai-compatible-base-url>"
export LLM_API_KEY="<internal-api-key>"
export LLM_MODEL="<internal-qwen-model-name>"
export LLM_TIMEOUT_S="120"
export LLM_ENABLE_THINKING="0"
export LLM_REQUEST_DELAY_S="0"
export LLM_INTER_RUN_PAUSE_S="2"
export LLM_RATE_LIMIT_ABORT="3"
export LLM_MAX_TOKENS="2048"
```

Public-equivalent reference settings for context:

```bash
LLM_TIMEOUT_S=30
LLM_REQUEST_DELAY_S=2
LLM_ENABLE_THINKING=1
```

For the original inter-run pacing context, use `LLM_INTER_RUN_PAUSE_S=10`.

## Thinking-Off Mechanism

When `LLM_ENABLE_THINKING` is false, each harness builds:

```python
{"chat_template_kwargs": {"enable_thinking": False}}
```

and passes it as the request `extra_body`.

Framework mechanism and version assumptions:

- Raw OpenAI SDK: `client.chat.completions.create(..., extra_body=EXTRA_BODY)`. This assumes the installed OpenAI SDK accepts `extra_body` on chat completions and forwards it to the OpenAI-compatible endpoint.
- instructor: `instructor.from_openai(...).chat.completions.create(..., extra_body=EXTRA_BODY)`. This assumes the installed instructor version forwards `extra_body` to the underlying OpenAI request when using `instructor.Mode.JSON`. Verify on intranet.
- PydanticAI: `agent.run_sync(..., model_settings={"timeout": ..., "extra_body": EXTRA_BODY})`. This assumes the installed PydanticAI OpenAI model forwards `extra_body` from `model_settings` to the underlying OpenAI request. Verify on intranet.

Thinking-off caveat: `chat_template_kwargs.enable_thinking` is the common vLLM-Qwen way to disable thinking mode, but the accepted key can differ by vLLM/Qwen version or deployment wrapper. If the server rejects the parameter, set `LLM_ENABLE_THINKING=1` or adjust the deployment-specific request key for the internal environment.

## Raw OpenAI SDK Spike

```powershell
cd experiments/phase0/qwen_structured_output
python check_env.py
python run_spike.py
```

`check_env.py` must end with `check_env_ok` before running the spike.

## instructor Spike

```powershell
cd experiments/phase0/instructor_vllm
python check_env.py
python run_spike.py
```

`check_env.py` must end with `check_env_ok` before running the spike.

Optional phased execution remains available:

```powershell
python run_spike.py --run a
python run_spike.py --run b
python run_spike.py --run tc
python run_spike.py --run report
```

## PydanticAI Spike

```powershell
cd experiments/phase0/pydanticai_qwen_vllm
python check_env.py
python run_spike.py
```

`check_env.py` must end with `check_env_ok` before running the spike.

Optional tool-calling-only execution remains available:

```powershell
python run_spike.py --run tc
```

## Output Files (auto-saved next to each `run_spike.py`)

Each `run_spike.py` now writes BOTH its JSON report and a full console log into the spike directory (no manual `> log.txt` redirect needed). Filenames carry a per-model tag, so qwen and glm runs no longer overwrite each other, and each report's JSON also records the `model` that was called:

- `p0_spike_<id>_<tag>_report.json`
- `p0_spike_<id>_<tag>_log.txt`

`<id>` is `001`/`002`/`007`; `<tag>` defaults to `qwen3.5_27b` for qwen and `glm` for glm (override with `LLM_REPORT_TAG`). Files land next to the `run_spike.py` you launched (i.e. the directory you `cd` into).

Example file set for this round (instructor + pydanticai, one qwen + one glm run each):

- `p0_spike_002_qwen3.5_27b_report.json` + `p0_spike_002_qwen3.5_27b_log.txt`
- `p0_spike_002_glm_report.json` + `p0_spike_002_glm_log.txt`
- `p0_spike_007_qwen3.5_27b_report.json` + `p0_spike_007_qwen3.5_27b_log.txt`
- `p0_spike_007_glm_report.json` + `p0_spike_007_glm_log.txt`

## What To Paste Back

Paste back all the `*_report.json` and `*_log.txt` files above, plus the `check_env.py` console output. Do not redact: the harnesses never print secrets. If a secret ever appears in output, stop and report the leak path instead of continuing.
