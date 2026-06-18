# P0-SPIKE-007 — PydanticAI + Qwen/vLLM Compatibility Spike

Spike-only code. NOT production. Do not copy to `app/`.

## Purpose

Validate PydanticAI compatibility with an OpenAI-compatible provider API (qwen3.6-27b via DashScope).

## Scope

- Structured output via PydanticAI Agent `output_type`
- Tool calling via PydanticAI `@agent.tool` decorator
- Exception handling, retry, observability probe

## Requirements

- Python 3.10+
- Environment variables: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`
- spike-only dependencies in `requirements.txt`

## How to run

```powershell
# Set temp paths to avoid repo contamination
$env:PYTHONPYCACHEPREFIX = "$env:TEMP\p0_spike_007_pycache"

# Install dependencies in temp venv
python -m venv "$env:TEMP\p0_spike_007_venv"
& "$env:TEMP\p0_spike_007_venv\Scripts\activate.ps1"
pip install -r requirements.txt

# Set environment variables
$env:LLM_BASE_URL = "<your-openai-compatible-endpoint>"
$env:LLM_API_KEY = "<your-api-key>"
$env:LLM_MODEL = "qwen3.6-27b"

# Run environment check
python check_env.py

# Run full spike
python run_spike.py --run all
```

## Output

- Environment probe: `$TEMP\p0_spike_007_env_probe.json`
- Spike report: `$TEMP\p0_spike_007_report.json`

## v1.0.11 Evidence Fields

Recorded in ADR and Task Record:

- pydanticai version
- pydantic version
- output_type parameter name (discovered at runtime)
- model name
- structured output mode
- tool calling mode
- success/failure sample counts
- retry strategy
- fallback strategy
- provider API boundary fields
