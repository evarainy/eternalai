"""P0-SPIKE-002 environment check, API probe, instructor version, and tool calling probe.

Reads LLM_BASE_URL, LLM_API_KEY, LLM_MODEL from environment.
Reports set/missing only. Never prints real base_url or API key.
Probes: API reachability, json_schema support, json_object support, tool calling support.
Checks instructor package importability and version.
Exits 1 if any critical check fails.
"""

import os
import sys
from typing import Dict, Tuple


def _parse_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"ERROR: {name} must be an integer", file=sys.stderr)
        sys.exit(1)


def _parse_env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"", "0", "false", "no", "off"}:
        return False
    if normalized in {"1", "true", "yes", "on"}:
        return True
    print(f"ERROR: {name} must be one of 0/1/false/true/no/yes/off/on", file=sys.stderr)
    sys.exit(1)


REQUEST_TIMEOUT_S = _parse_env_int("LLM_TIMEOUT_S", 120)
ENABLE_THINKING = _parse_env_bool("LLM_ENABLE_THINKING", False)
EXTRA_BODY = {} if ENABLE_THINKING else {"chat_template_kwargs": {"enable_thinking": False}}
THINKING_OFF_INJECTED = not ENABLE_THINKING


def check_env_vars() -> Dict[str, str]:
    results: Dict[str, str] = {}
    for var in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        val = os.environ.get(var)
        if var == "LLM_MODEL":
            results[var] = val if val else "missing"
        else:
            results[var] = "set" if val else "missing"
    return results


def check_instructor() -> Tuple[bool, str]:
    """Return (importable, version_string)."""
    try:
        import instructor
        ver = getattr(instructor, "__version__", "unknown")
        return True, ver
    except ImportError:
        return False, "not_installed"


def probe_api() -> dict:
    """Return probe results dict."""
    from openai import OpenAI

    base_url = os.environ.get("LLM_BASE_URL")
    api_key = os.environ.get("LLM_API_KEY")
    model = os.environ.get("LLM_MODEL")

    client = OpenAI(base_url=base_url, api_key=api_key, timeout=REQUEST_TIMEOUT_S)
    result = {
        "api_reachable": False,
        "json_schema_supported": False,
        "json_object_supported": False,
        "tool_calling_supported": False,
    }

    # Probe 1: minimal request
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
            extra_body=EXTRA_BODY,
        )
        if not resp.choices:
            return result
        result["api_reachable"] = True
    except Exception as e:
        err_str = str(e).lower()
        if "401" in err_str or "403" in err_str or "unauthorized" in err_str:
            print(f"api_auth_error: {type(e).__name__}")
        else:
            print(f"api_unreachable: {type(e).__name__}")
        return result

    # Probe 2: json_schema support
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
            messages=[
                {"role": "system", "content": "You must output only valid JSON. No markdown, no explanation."},
                {"role": "user", "content": 'Output this exact JSON: {"answer":"ok"}'},
            ],
            max_tokens=30,
            response_format={"type": "json_schema", "json_schema": schema},
            extra_body=EXTRA_BODY,
        )
        content = resp2.choices[0].message.content or ""
        cleaned2 = content.strip()
        if cleaned2.startswith("```"):
            lines2 = cleaned2.split("\n")
            lines2 = [ln for ln in lines2 if not ln.strip().startswith("```")]
            cleaned2 = "\n".join(lines2).strip()
        if cleaned2.startswith("{"):
            result["json_schema_supported"] = True
    except Exception:
        result["json_schema_supported"] = False

    # Probe 3: json_object support
    try:
        resp3 = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You must output only valid JSON. No markdown, no explanation."},
                {"role": "user", "content": 'Output this exact JSON: {"answer":"ok"}'},
            ],
            max_tokens=30,
            response_format={"type": "json_object"},
            extra_body=EXTRA_BODY,
        )
        content3 = resp3.choices[0].message.content or ""
        cleaned3 = content3.strip()
        if cleaned3.startswith("```"):
            lines3 = cleaned3.split("\n")
            lines3 = [ln for ln in lines3 if not ln.strip().startswith("```")]
            cleaned3 = "\n".join(lines3).strip()
        if cleaned3.startswith("{"):
            result["json_object_supported"] = True
    except Exception:
        result["json_object_supported"] = False

    # Probe 4: tool calling support
    try:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "probe_tool",
                    "description": "A probe tool for testing tool calling support",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "A test query"}
                        },
                        "required": ["query"],
                    },
                },
            }
        ]
        resp4 = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Call the probe_tool with query='test'"}],
            max_tokens=50,
            tools=tools,
            tool_choice="auto",
            extra_body=EXTRA_BODY,
        )
        choice = resp4.choices[0]
        if choice.message.tool_calls:
            result["tool_calling_supported"] = True
        elif choice.finish_reason == "tool_calls":
            result["tool_calling_supported"] = True
        else:
            # Some providers return tool_calls in a different way
            result["tool_calling_supported"] = False
    except Exception as e:
        err_name = type(e).__name__
        # Some providers return explicit "not supported" errors
        result["tool_calling_supported"] = False
        print(f"tool_calling_probe_error: {err_name}")

    return result


def main() -> None:
    env = check_env_vars()
    for var, status in env.items():
        print(f"{var}: {status}")
    print(f"request_timeout_s: {REQUEST_TIMEOUT_S}")
    print(f"enable_thinking: {str(ENABLE_THINKING).lower()}")
    print(f"thinking_off_injected: {str(THINKING_OFF_INJECTED).lower()}")
    print(f"extra_body: {EXTRA_BODY}")

    missing = [v for v, s in env.items() if s == "missing"]
    if missing:
        print(f"env_missing: {', '.join(missing)}")
        sys.exit(1)

    print("env_ok")

    # Instructor check
    importable, ver = check_instructor()
    print(f"instructor_importable: {str(importable).lower()}")
    print(f"instructor_version: {ver}")
    if not importable:
        print("instructor_not_installed")
        sys.exit(1)

    # API probe
    print("probing api...")
    probe = probe_api()
    print(f"api_reachable: {str(probe['api_reachable']).lower()}")
    print(f"json_schema_supported: {str(probe['json_schema_supported']).lower()}")
    print(f"json_object_supported: {str(probe['json_object_supported']).lower()}")
    print(f"tool_calling_supported: {str(probe['tool_calling_supported']).lower()}")

    if not probe["api_reachable"]:
        print("api_unreachable_or_auth_failed")
        sys.exit(1)

    if not probe["json_object_supported"]:
        print("json_object_not_supported_cannot_proceed")
        sys.exit(1)

    print("check_env_ok")


if __name__ == "__main__":
    main()
