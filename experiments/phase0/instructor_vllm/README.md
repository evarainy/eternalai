# P0-SPIKE-002: instructor + vLLM OpenAI-compatible API Stability Spike

## Purpose

Validate instructor library stability for structured output, retry, exception parsing,
and tool calling response parsing via public OpenAI-compatible API (provider API mode).

## Provider API Boundary

```yaml
execution_environment:        public_vendor_api
internal_endpoint_validation: deferred / not_executed
activation_condition:         rerun against internal vLLM/Qwen server when intranet access is available
recommendation_scope:         client-side compatibility only
```

## Files

| File | Purpose |
|---|---|
| `check_env.py` | Environment variable check, API reachability, instructor version, tool calling probe |
| `run_spike.py` | Main spike: 50 structured output samples (6 categories, dual run) + tool calling samples |
| `requirements.txt` | Spike-only dependencies (NOT production) |

## Usage

```powershell
# Set environment variables (do NOT commit or print these)
$env:LLM_BASE_URL = "..."
$env:LLM_API_KEY = "..."
$env:LLM_MODEL = "qwen3.6-27b"

# Create temp venv
python -m venv $env:TEMP\p0-spike-002-venv
& "$env:TEMP\p0-spike-002-venv\Scripts\Activate.ps1"
pip install -r requirements.txt

# Check environment
python check_env.py

# Run spike
python run_spike.py

# Deactivate and cleanup
deactivate
Remove-Item -Recurse -Force $env:TEMP\p0-spike-002-venv
```

## Sample Design

### Structured output samples (>=50, always required)

| Category | Count | Purpose |
|---|---|---|
| success | 15 | Normal requests, expect valid structured output |
| missing_fields | 9 | Adversarial: prompt model to omit required fields |
| type_error | 8 | Adversarial: prompt model to use wrong types |
| refusal | 6 | Trigger model refusal |
| non_json | 6 | Adversarial: prompt non-JSON output |
| timeout | 6 | Short timeout to trigger timeout errors |

### Tool calling samples (>=8, only if provider supports tool calling)

| Tool | Count | Scenario |
|---|---|---|
| query_oa_leave_balance | 3 | OA leave balance queries |
| query_u8_invoice_status | 3 | U8 invoice status queries |
| query_hik_access_log | 2 | Hikvision access log queries |

## Dual Run Design

- **Run A** (`max_retries=0`): Single attempt, no instructor retry. Measures first-pass success rate.
- **Run B** (`max_retries=3`): Instructor auto-retry. Measures final success rate after retry.

Retry analysis compares Run A vs Run B to determine retry_recovered_count and retry_exhausted_count.

## Safety

- Credentials are NEVER printed or written to files
- check_env.py reports set/missing only
- Error messages are sanitized (class name + status code only)
- Temp files written to %TEMP%, not to repo
