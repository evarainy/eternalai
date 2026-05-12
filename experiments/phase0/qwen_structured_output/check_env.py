"""P0-SPIKE-001 environment variable check and API reachability probe.

Reads LLM_BASE_URL, LLM_API_KEY, LLM_MODEL from environment.
Reports set/missing only. Never prints real base_url or API key.
Exits 1 if any variable is missing or API is unreachable.
"""

import os
import sys


def check_env_vars() -> dict[str, str]:
    results: dict[str, str] = {}
    for var in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        val = os.environ.get(var)
        if var == "LLM_MODEL":
            results[var] = val if val else "missing"
        else:
            results[var] = "set" if val else "missing"
    return results


def probe_api() -> tuple[bool, bool]:
    """Return (api_reachable, json_schema_supported)."""
    from openai import OpenAI

    base_url = os.environ.get("LLM_BASE_URL")
    api_key = os.environ.get("LLM_API_KEY")
    model = os.environ.get("LLM_MODEL")

    client = OpenAI(base_url=base_url, api_key=api_key)

    # Probe 1: minimal request to check reachability
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
        )
        if not resp.choices:
            return False, False
    except Exception as e:
        err_str = str(e).lower()
        if "401" in err_str or "403" in err_str or "unauthorized" in err_str:
            print(f"api_auth_error: {type(e).__name__}")
            return False, False
        print(f"api_unreachable: {type(e).__name__}")
        return False, False

    # Probe 2: json_schema support
    json_schema_supported = False
    try:
        schema = {
            "name": "probe_schema",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        }
        resp2 = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": 'Reply with {"answer":"ok"}'}],
            max_tokens=20,
            response_format={"type": "json_schema", "json_schema": schema},
        )
        content = resp2.choices[0].message.content or ""
        if content.strip().startswith("{"):
            json_schema_supported = True
    except Exception:
        json_schema_supported = False

    return True, json_schema_supported


def main() -> None:
    env = check_env_vars()
    for var, status in env.items():
        print(f"{var}: {status}")

    missing = [v for v, s in env.items() if s == "missing"]
    if missing:
        print(f"env_missing: {', '.join(missing)}")
        sys.exit(1)

    print("env_ok")
    print("probing api...")
    reachable, json_schema_ok = probe_api()
    print(f"api_reachable: {str(reachable).lower()}")
    print(f"json_schema_supported: {str(json_schema_ok).lower()}")

    if not reachable:
        print("api_unreachable_or_auth_failed")
        sys.exit(1)

    print("check_env_ok")


if __name__ == "__main__":
    main()
