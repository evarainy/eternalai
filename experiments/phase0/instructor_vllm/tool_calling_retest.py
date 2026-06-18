# -*- coding: utf-8 -*-
"""Retest only the Phase 0 tool-calling samples with a normal token budget."""

import json
import os
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_spike as H

from openai import OpenAI
from pydantic import ValidationError


def _parse_float_env(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        print("ERROR: %s must be a float, got %r" % (name, raw), file=sys.stderr)
        sys.exit(1)


def _parse_int_env(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print("ERROR: %s must be an integer, got %r" % (name, raw), file=sys.stderr)
        sys.exit(1)


def _parse_bool_env(name, default_text):
    raw = os.environ.get(name, default_text)
    normalized = raw.strip().lower()
    if normalized in ("", "0", "false", "no", "off"):
        return False
    if normalized in ("1", "true", "yes", "on"):
        return True
    print("ERROR: %s must be one of 0/1/false/true/no/yes/off/on" % name, file=sys.stderr)
    sys.exit(1)


BASE_URL = os.environ.get("LLM_BASE_URL", "").strip()
if not BASE_URL:
    print("ERROR: LLM_BASE_URL is required. Set it to the OpenAI-compatible endpoint base URL.", file=sys.stderr)
    sys.exit(1)

API_KEY = os.environ.get("LLM_API_KEY", "EMPTY")
MODEL = os.environ.get("LLM_MODEL", "qwen3.5-27b")
TIMEOUT = _parse_float_env("LLM_TIMEOUT_S", 120.0)
MAX_TOKENS = _parse_int_env("LLM_TOOL_MAX_TOKENS", 512)
ENABLE_THINKING = _parse_bool_env("LLM_ENABLE_THINKING", "0")
EXTRA_BODY = {} if ENABLE_THINKING else {"chat_template_kwargs": {"enable_thinking": False}}
THRESHOLD = 80.0

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)


def _mask_api_key(value):
    if value == "EMPTY":
        return "EMPTY"
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return value[:1] + "***" + value[-1:]
    return value[:4] + "***" + value[-4:]


def _slugify(value):
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip().lower())
    slug = slug.strip("-._")
    return slug or "model"


def _report_tag(model):
    override = os.environ.get("LLM_REPORT_TAG", "").strip()
    if override:
        return _slugify(override)
    if model.strip().lower().startswith("glm"):
        return "glm"
    return _slugify(model)


def _build_openai_tools():
    openai_tools = []
    for name, schema in H.TOOL_SCHEMAS.items():
        openai_tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": schema.__doc__ or ("Call " + name),
                "parameters": schema.model_json_schema(),
            },
        })
    return openai_tools


def _safe_error(exc):
    sanitizer = getattr(H, "_sanitize_error", None)
    if sanitizer is not None:
        try:
            return sanitizer(exc)
        except Exception:
            pass
    return type(exc).__name__


class ConsoleLog(object):
    def __init__(self):
        self.lines = []

    def write(self, text):
        self.lines.append(text)
        print(text)

    def write_blank(self):
        self.write("")

    def dump(self, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines))
            f.write("\n")


def _rate(count, total):
    if not total:
        return 0.0
    return round(count / total * 100.0, 1)


def _run_sample(sample, index, total, openai_tools, log):
    log.write("[%d/%d] %s expected=%s" % (index, total, sample.id, sample.expected_tool))
    t0 = time.time()

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": sample.system_hint},
                {"role": "user", "content": sample.user_msg},
            ],
            tools=openai_tools,
            tool_choice="auto",
            max_tokens=MAX_TOKENS,
            timeout=TIMEOUT,
            extra_body=EXTRA_BODY,
        )
        latency = (time.time() - t0) * 1000.0
        choice = resp.choices[0]
        message = choice.message

        if not message.tool_calls:
            finish_reason = getattr(choice, "finish_reason", None)
            log.write("  FAIL no_tool_calls finish_reason=%s latency_ms=%.1f" % (finish_reason, latency))
            return {
                "sample_id": sample.id,
                "expected_tool": sample.expected_tool,
                "success": False,
                "tool_selected": "",
                "tool_selection_correct": False,
                "arguments_valid": False,
                "latency_ms": round(latency, 1),
                "error": "no_tool_calls_in_response",
                "finish_reason": finish_reason,
            }

        tc = message.tool_calls[0]
        tool_name = tc.function.name
        selection_correct = tool_name == sample.expected_tool

        try:
            args = json.loads(tc.function.arguments)
            H.TOOL_SCHEMAS.get(tool_name, sample.expected_schema).model_validate(args)
            args_valid = True
        except (json.JSONDecodeError, ValidationError, KeyError):
            args_valid = False

        success = selection_correct and args_valid
        if success:
            log.write("  OK tool=%s latency_ms=%.1f" % (tool_name, latency))
        else:
            log.write(
                "  FAIL tool=%s selection_correct=%s arguments_valid=%s latency_ms=%.1f"
                % (tool_name, str(selection_correct).lower(), str(args_valid).lower(), latency)
            )

        return {
            "sample_id": sample.id,
            "expected_tool": sample.expected_tool,
            "success": success,
            "tool_selected": tool_name,
            "tool_selection_correct": selection_correct,
            "arguments_valid": args_valid,
            "latency_ms": round(latency, 1),
        }

    except Exception as exc:
        latency = (time.time() - t0) * 1000.0
        error = _safe_error(exc)
        log.write("  ERROR %s latency_ms=%.1f" % (error, latency))
        return {
            "sample_id": sample.id,
            "expected_tool": sample.expected_tool,
            "success": False,
            "tool_selected": "",
            "tool_selection_correct": False,
            "arguments_valid": False,
            "latency_ms": round(latency, 1),
            "error": error,
        }


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    tag = _report_tag(MODEL)
    report_path = os.path.join(here, "tool_calling_retest_%s_report.json" % tag)
    log_path = os.path.join(here, "tool_calling_retest_%s_log.txt" % tag)
    log = ConsoleLog()

    log.write("P0 tool-calling retest")
    log.write("model: %s" % MODEL)
    log.write("api_key: %s" % _mask_api_key(API_KEY))
    log.write("max_output_tokens: %d" % MAX_TOKENS)
    log.write("request_timeout_s: %.1f" % TIMEOUT)
    log.write("enable_thinking: %s" % str(ENABLE_THINKING).lower())
    log.write("extra_body: %s" % json.dumps(EXTRA_BODY, ensure_ascii=False, sort_keys=True))
    log.write_blank()

    samples = H.build_tool_calling_samples()
    openai_tools = _build_openai_tools()
    log.write("tool_calling_sample_count: %d" % len(samples))
    log.write("openai_tool_count: %d" % len(openai_tools))
    log.write_blank()

    per_sample = []
    total = len(samples)
    for index, sample in enumerate(samples, start=1):
        per_sample.append(_run_sample(sample, index, total, openai_tools, log))

    passed = sum(1 for item in per_sample if item["success"])
    selection_correct = sum(1 for item in per_sample if item["tool_selection_correct"])
    args_valid = sum(1 for item in per_sample if item["arguments_valid"])
    success_rate = _rate(passed, total)
    selection_rate = _rate(selection_correct, total)
    args_rate = _rate(args_valid, total)
    threshold_met = success_rate >= THRESHOLD

    report = {
        "test": "tool_calling_retest",
        "model": MODEL,
        "max_output_tokens": MAX_TOKENS,
        "enable_thinking": ENABLE_THINKING,
        "extra_body": EXTRA_BODY,
        "request_timeout_s": TIMEOUT,
        "tool_calling_sample_count": total,
        "tool_calling_passed": passed,
        "tool_calling_success_rate": success_rate,
        "tool_selection_accuracy": selection_rate,
        "argument_validation_success_rate": args_rate,
        "tool_calling_threshold_met": threshold_met,
        "per_sample": per_sample,
        "note": (
            "Retests only the 8 tool-calling samples from run_spike.py with "
            "normal max token budget; structured-output samples are not run."
        ),
    }

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")

    log.write_blank()
    log.write("=" * 60)
    log.write("RESULTS")
    log.write("=" * 60)
    log.write("Tool calling samples: %d" % total)
    log.write("  tool_calling_passed: %d/%d" % (passed, total))
    log.write("  tool_calling_success_rate: %.1f%%" % success_rate)
    log.write("  tool_selection_accuracy: %.1f%%" % selection_rate)
    log.write("  argument_validation_success_rate: %.1f%%" % args_rate)
    log.write("  threshold_met_80_percent: %s" % str(threshold_met).lower())
    log.write("Report: %s" % report_path)
    log.write("Log: %s" % log_path)

    log.dump(log_path)


if __name__ == "__main__":
    main()
