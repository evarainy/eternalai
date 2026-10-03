"""One queued synthetic Run, one Jev transport attempt, then exit; no supervisor."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import timedelta
from typing import NoReturn

from sqlalchemy import text

from app.browser_skill.run_contracts import BrowserAcceptedView
from app.infra.browser.openrouter_jev import prompt_openrouter_key
from app.infra.browser.synthetic_operator import (
    SyntheticOperatorComponents,
    open_synthetic_operator,
    prompt_operator_bundle,
)
from app.infra.browser.synthetic_trial import (
    SingleJevAttempt,
    create_trial_file,
    read_trial_file,
)
from app.infra.browser.synthetic_vault import OPERATOR_FILE, VAULT_DIRECTORY


class _SilentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError("browser_trial_arguments_invalid")


async def execute_once(
    components: SyntheticOperatorComponents, expected: BrowserAcceptedView,
) -> bool:
    """Preflight is supplementary; the existing worker retains all current guards."""
    vertical, owner = components.vertical, components.publication_owner
    async with vertical._sessions() as session:
        rows = (await session.execute(text(
            "SELECT task_id,run_id,ai_user_id,session_id,status,phase,cancel_requested,"
            "worker_deadline FROM browser_runs WHERE tenant_id=:tenant LIMIT 2"
        ), {"tenant": owner.tenant_id})).mappings().all()
    if (len(rows) != 1 or rows[0]["task_id"] != expected.task_id
            or rows[0]["run_id"] != expected.run_id or rows[0]["ai_user_id"] != owner.user_id
            or rows[0]["session_id"] != owner.session_id or rows[0]["status"] != "running"
            or rows[0]["phase"] != "queued" or rows[0]["cancel_requested"]
            or rows[0]["worker_deadline"] is not None):
        raise ValueError("browser_trial_single_queued_run_required")
    create_trial_file("trial.worker.json", {"worker_passes": 1})
    # Exact existing claim/CAS transaction: never fall back to another owner's Run.
    claimed = await vertical.runs._claim_candidate(
        owner, expected.task_id, expected.run_id, "browser_fixture_worker", timedelta(seconds=60),
    )
    if (claimed.owner != owner or claimed.task_id != expected.task_id
            or claimed.run_id != expected.run_id or claimed.worker_id != "browser_fixture_worker"):
        raise ValueError("browser_trial_worker_failed")
    result = await vertical.worker._run_claimed(claimed)
    if result.task_id != expected.task_id or result.run_id != expected.run_id:
        raise ValueError("browser_trial_worker_failed")
    # _run_claimed's finally settles cleanup; re-read instead of reporting its old snapshot.
    result = await vertical.runs.get(owner, expected.task_id, expected.run_id)
    return (result.status == "completed" and result.effect == "acknowledged"
            and result.verification == "verified" and result.error_code is None
            and result.cleanup in {"released", "terminated"})


async def _run(approved_budget_usd: str) -> bool:
    budget = SingleJevAttempt(approved_budget_usd)
    reference = read_trial_file("trial.run.json")
    if (set(reference) != {"task_id", "run_id"} or any(
        not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value) is None
        for value in reference.values()
    )):
        raise ValueError("browser_trial_receipt_invalid")
    expected = BrowserAcceptedView.model_validate({**reference, "state_revision": 0})
    bundle = prompt_operator_bundle(encrypted_path=VAULT_DIRECTORY / OPERATOR_FILE)
    key = prompt_openrouter_key()
    try:
        async with open_synthetic_operator(
            bundle, jev_key=key, enabled=True, input_mode="structured",
            attempt_guard=budget.reserve,
        ) as components:
            success = await execute_once(components, expected)
        return success and budget.request_count == 1
    finally:
        print(json.dumps({"jev_request_count": budget.request_count,
                          "max_jev_requests": 1, "actual_cost_usd": None,
                          "cost_status": "unknown"}, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = _SilentParser(allow_abbrev=False, add_help=False)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--approved-budget-usd", required=True)
    try:
        args = parser.parse_args(argv)
        if not args.enable:
            raise ValueError
        success = asyncio.run(_run(args.approved_budget_usd))
    except BaseException:
        print("browser_trial_worker_unavailable", file=sys.stderr)
        return 2
    print("browser_trial_verified" if success else "browser_trial_failed")
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
