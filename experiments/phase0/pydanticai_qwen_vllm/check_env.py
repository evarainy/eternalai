"""P0-SPIKE-007 environment check, PydanticAI API probe, and provider probe.

Reads LLM_BASE_URL, LLM_API_KEY, LLM_MODEL from environment.
Reports set/missing only. Never prints real base_url or API key.
Probes: PydanticAI importability, structured output API discovery,
       API reachability, json_schema support, tool calling support.
Exits 1 if any critical check fails.
"""

import os
import sys


def _sanitize_error(e: Exception) -> str:
    """Return safe error detail — class name + status code only.
    Never prints URLs, tokens, headers, or provider details."""
    cls = type(e).__name__
    status = getattr(e, "status_code", None) or getattr(e, "code", None)
    if status:
        return f"{cls} (status={status})"
    return cls


def check_env_vars() -> dict[str, str]:
    results: dict[str, str] = {}
    for var in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        val = os.environ.get(var)
        if var == "LLM_MODEL":
            results[var] = val if val else "missing"
        else:
            results[var] = "set" if val else "missing"
    return results


def check_pydanticai() -> dict:
    """Check PydanticAI importability and version info."""
    info: dict = {
        "importable": False,
        "version": "not_installed",
        "slim": False,
        "openai_extra": False,
    }
    try:
        import pydantic_ai
        info["importable"] = True
        info["version"] = getattr(pydantic_ai, "__version__", "unknown")
    except ImportError:
        return info

    try:
        from pydantic_ai.models.openai import OpenAIChatModel
        info["openai_extra"] = True
    except (ImportError, ModuleNotFoundError):
        try:
            from pydantic_ai.models.openai import OpenAIModel
            info["openai_extra"] = True
        except (ImportError, ModuleNotFoundError):
            info["slim"] = True

    return info


def _probe_pydanticai_api(
    base_url: str | None, api_key: str | None, model_name: str
) -> dict:
    """Probe PydanticAI structured output API.

    Returns probe result dict with working config or failure info.
    """
    from pydantic import BaseModel
    from pydantic_ai import Agent
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.models.openai import OpenAIChatModel

    class ProbeResult(BaseModel):
        answer: str

    result: dict = {
        "pydanticai_ok": False,
        "output_mode": None,
        "output_type_param": "output_type",
        "result_output_accessible": False,
        "probe_model_isinstance": False,
        "probe_output_value": "",
        "working_config": {},
        "error": "",
    }

    if not base_url or not api_key:
        result["error"] = "missing base_url or api_key"
        return result

    # Build provider and model
    try:
        provider = OpenAIProvider(base_url=base_url, api_key=api_key)
        model = OpenAIChatModel(model_name, provider=provider)
        print(f"  model created: OpenAIChatModel({model_name})")
    except Exception as e:
        result["error"] = f"model creation failed: {_sanitize_error(e)}"
        return result

    # Probe 1: PromptedOutput (no tool_choice, uses prompt-based JSON extraction)
    try:
        from pydantic_ai.output import PromptedOutput
        agent = Agent(model, output_type=PromptedOutput(outputs=ProbeResult))
        probe_result = agent.run_sync(
            "Reply with exactly: {\"answer\": \"probe_ok\"}"
        )
        if isinstance(probe_result.output, ProbeResult):
            result["pydanticai_ok"] = True
            result["output_mode"] = "PromptedOutput"
            result["result_output_accessible"] = True
            result["probe_model_isinstance"] = True
            result["probe_output_value"] = probe_result.output.answer
            result["working_config"] = {
                "model_desc": "OpenAIChatModel + PromptedOutput",
                "has_explicit_model": True,
                "model_name": model_name,
            }
            return result
        else:
            print(f"  PromptedOutput: output type {type(probe_result.output).__name__}")
    except Exception as e:
        print(f"  PromptedOutput failed: {_sanitize_error(e)}")

    # Probe 2: NativeOutput (uses model's native structured output / json_schema)
    try:
        from pydantic_ai.output import NativeOutput
        agent = Agent(model, output_type=NativeOutput(outputs=ProbeResult))
        probe_result = agent.run_sync(
            "Reply with exactly: {\"answer\": \"probe_ok\"}"
        )
        if isinstance(probe_result.output, ProbeResult):
            result["pydanticai_ok"] = True
            result["output_mode"] = "NativeOutput"
            result["result_output_accessible"] = True
            result["probe_model_isinstance"] = True
            result["probe_output_value"] = probe_result.output.answer
            result["working_config"] = {
                "model_desc": "OpenAIChatModel + NativeOutput",
                "has_explicit_model": True,
                "model_name": model_name,
            }
            return result
        else:
            print(f"  NativeOutput: output type {type(probe_result.output).__name__}")
    except Exception as e:
        print(f"  NativeOutput failed: {_sanitize_error(e)}")

    # Probe 3: Default output_type (tool calling, may fail with thinking mode)
    try:
        agent = Agent(model, output_type=ProbeResult)
        probe_result = agent.run_sync(
            "Reply with exactly: {\"answer\": \"probe_ok\"}"
        )
        if isinstance(probe_result.output, ProbeResult):
            result["pydanticai_ok"] = True
            result["output_mode"] = "ToolOutput (default)"
            result["result_output_accessible"] = True
            result["probe_model_isinstance"] = True
            result["probe_output_value"] = probe_result.output.answer
            result["working_config"] = {
                "model_desc": "OpenAIChatModel + default output_type",
                "has_explicit_model": True,
                "model_name": model_name,
            }
            return result
        else:
            print(f"  default output_type: output type {type(probe_result.output).__name__}")
    except Exception as e:
        print(f"  default output_type failed: {_sanitize_error(e)}")
        result["error"] = f"all probes failed; last: {_sanitize_error(e)}"

    return result


def probe_openai_api(
    base_url: str, api_key: str, model: str
) -> dict:
    """Probe standard OpenAI API capabilities."""
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key, timeout=30)
    probe: dict = {
        "api_reachable": False,
        "json_schema_supported": False,
        "json_object_supported": False,
        "tool_calling_supported": False,
    }

    try:
        resp = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "hi"}], max_tokens=5,
        )
        if not resp.choices:
            return probe
        probe["api_reachable"] = True
    except Exception as e:
        err_str = str(e).lower()
        if "401" in err_str or "403" in err_str or "unauthorized" in err_str:
            print(f"api_auth_error: {type(e).__name__}")
        else:
            print(f"api_unreachable: {type(e).__name__}")
        return probe

    # Probe json_schema
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
                {"role": "system", "content": "Output only valid JSON."},
                {"role": "user", "content": '{"answer":"ok"}'},
            ],
            max_tokens=30,
            response_format={"type": "json_schema", "json_schema": schema},
        )
        c = (resp2.choices[0].message.content or "").strip()
        if c.startswith("```"):
            c = "\n".join(l for l in c.split("\n") if not l.strip().startswith("```")).strip()
        probe["json_schema_supported"] = c.startswith("{")
    except Exception:
        pass

    # Probe json_object
    try:
        resp3 = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Output only valid JSON."},
                {"role": "user", "content": '{"answer":"ok"}'},
            ],
            max_tokens=30,
            response_format={"type": "json_object"},
        )
        c3 = (resp3.choices[0].message.content or "").strip()
        if c3.startswith("```"):
            c3 = "\n".join(l for l in c3.split("\n") if not l.strip().startswith("```")).strip()
        probe["json_object_supported"] = c3.startswith("{")
    except Exception:
        pass

    # Probe tool calling
    try:
        tools = [{
            "type": "function",
            "function": {
                "name": "probe_tool",
                "description": "Probe tool for testing",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }]
        resp4 = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Call probe_tool with query='test'"}],
            max_tokens=50, tools=tools, tool_choice="auto",
        )
        if resp4.choices[0].message.tool_calls:
            probe["tool_calling_supported"] = True
    except Exception as e:
        print(f"tool_calling_probe_error: {type(e).__name__}")

    return probe


def main() -> None:
    env = check_env_vars()
    for var, status in env.items():
        print(f"{var}: {status}")

    missing = [v for v, s in env.items() if s == "missing"]
    if missing:
        print(f"env_missing: {', '.join(missing)}")
        sys.exit(1)

    print("env_ok")

    # PydanticAI check
    pai_info = check_pydanticai()
    print(f"pydanticai_importable: {str(pai_info['importable']).lower()}")
    print(f"pydanticai_version: {pai_info['version']}")
    if pai_info["importable"]:
        print(f"pydanticai_openai_extra: {str(pai_info['openai_extra']).lower()}")
    if not pai_info["importable"]:
        print("pydanticai_not_installed")
        sys.exit(1)

    base_url = os.environ["LLM_BASE_URL"]
    api_key = os.environ["LLM_API_KEY"]
    model_name = os.environ["LLM_MODEL"]

    # PydanticAI API probe
    print("probing pydanticai api...")
    pai_probe = _probe_pydanticai_api(base_url, api_key, model_name)
    print(f"pydanticai_ok: {str(pai_probe['pydanticai_ok']).lower()}")
    if pai_probe["pydanticai_ok"]:
        print(f"output_type_param: {pai_probe['output_type_param']}")
        print(f"result_output_accessible: {str(pai_probe['result_output_accessible']).lower()}")
        print(f"probe_model_isinstance: {str(pai_probe['probe_model_isinstance']).lower()}")
        wc = pai_probe["working_config"]
        print(f"working_config: model_desc={wc['model_desc']}, "
              f"has_explicit_model={str(wc['has_explicit_model']).lower()}")
    else:
        print(f"pydanticai_api_error: {pai_probe['error']}")

    if not pai_probe["pydanticai_ok"]:
        print("pydanticai_api_probe_failed")
        sys.exit(1)

    # OpenAI API probe
    print("probing openai api...")
    openai_probe = probe_openai_api(base_url, api_key, model_name)
    print(f"api_reachable: {str(openai_probe['api_reachable']).lower()}")
    print(f"json_schema_supported: {str(openai_probe['json_schema_supported']).lower()}")
    print(f"json_object_supported: {str(openai_probe['json_object_supported']).lower()}")
    print(f"tool_calling_supported: {str(openai_probe['tool_calling_supported']).lower()}")

    if not openai_probe["api_reachable"]:
        print("api_unreachable")
        sys.exit(1)

    # Save probe result for run_spike.py to consume
    import json
    combined_probe = {
        **pai_probe,
        "version": pai_info["version"],
        "openai_extra": pai_info["openai_extra"],
        "api_reachable": openai_probe["api_reachable"],
        "json_schema_supported": openai_probe["json_schema_supported"],
        "json_object_supported": openai_probe["json_object_supported"],
        "tool_calling_supported": openai_probe["tool_calling_supported"],
    }
    probe_path = os.path.join(os.environ.get("TEMP", "/tmp"), "p0_spike_007_env_probe.json")
    with open(probe_path, "w", encoding="utf-8") as f:
        json.dump(combined_probe, f, ensure_ascii=False, indent=2)
    print(f"probe_saved: {probe_path}")

    print("check_env_ok")


if __name__ == "__main__":
    main()
