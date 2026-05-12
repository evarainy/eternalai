# P0-SPIKE-001 — Qwen Structured Output Spike (Provider API Mode)

## Purpose

Validate Qwen-series model structured output capability via a public OpenAI-compatible API endpoint.
This spike does NOT validate internal vLLM / GPU / Qwen local deployment.

## Provider API Boundary

```
execution_environment:        public_vendor_api
internal_endpoint_validation: deferred / not_executed
activation_condition:         rerun against internal vLLM/Qwen server when intranet access is available
recommendation_scope:         client-side compatibility only
```

## Environment Variables

| Variable | Required | Notes |
|---|---|---|
| `LLM_BASE_URL` | yes | OpenAI-compatible endpoint base URL. Never printed or recorded. |
| `LLM_API_KEY` | yes | API authentication key. Never printed or recorded. |
| `LLM_MODEL` | yes | Model name / identifier. |

## How to Run

```bash
# 1. Create temp venv
python -m venv %TEMP%\eternalai-p0-spike-001-venv
%TEMP%\eternalai-p0-spike-001-venv\Scripts\activate

# 2. Install dependencies
pip install -r experiments/phase0/qwen_structured_output/requirements.txt

# 3. Set PYTHONPYCACHEPREFIX
set PYTHONPYCACHEPREFIX=%TEMP%

# 4. Check environment
python experiments/phase0/qwen_structured_output/check_env.py

# 5. Run spike
python experiments/phase0/qwen_structured_output/run_spike.py
```

## Output

- `check_env.py`: reports set/missing for each env var, API reachability, json_schema support
- `run_spike.py`: per-sample results, per-mode statistics, overall success rate

## Provider Mode Limitations

- Does NOT validate internal vLLM / GPU / Qwen deployment
- Does NOT confirm quantization, max_model_len, or GPU memory behavior
- Public API backend version and configuration are unknown
- Results may differ from internal endpoint behavior
- json_schema support varies by provider

## Scope

This spike validates client-side structured output compatibility only.
Internal endpoint validation must be rerun when intranet access is available.
