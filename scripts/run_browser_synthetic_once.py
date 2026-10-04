"""One queued synthetic Run, one Jev transport attempt, then exit; no supervisor."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import timedelta
from typing import Any, NoReturn

from sqlalchemy import text

from app.browser_skill.run_contracts import BrowserAcceptedView
from app.infra.browser.openrouter_jev import prompt_openrouter_key
from app.infra.browser.synthetic_operator import (
    SyntheticOperatorComponents,
    open_synthetic_operator,
    prompt_operator_bundle,
)
from app.infra.browser.synthetic_private_input import read_private_operator_input
from app.infra.browser.synthetic_trial import (
    DIAGNOSTIC_RUN_ID,
    DIAGNOSTIC_TASK_ID,
    DIAGNOSTIC_TRIAL,
    LEGACY_RUN_ID,
    LEGACY_TASK_ID,
    OBSERVE_TRIAL,
    ORIGINAL_TRIAL,
    VISIBLE_RUN_ID,
    VISIBLE_TASK_ID,
    VISIBLE_TRIAL,
    SingleJevAttempt,
    approved_attempt_id,
    create_trial_file,
    read_trial_file,
    require_diagnostic_terminal,
    require_legacy_terminal,
    require_visible_terminal,
    trial_publication_digest,
    trial_reference,
    trial_request_id,
)
from app.infra.browser.synthetic_trial_client import _receipt
from app.infra.browser.synthetic_vault import OPERATOR_FILE, VAULT_DIRECTORY


class _SilentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError("browser_trial_arguments_invalid")


async def execute_once(
    components: SyntheticOperatorComponents, expected: BrowserAcceptedView,
    *, trial_id: str = ORIGINAL_TRIAL, attempt_id: str | None = None,
) -> bool:
    """Preflight is supplementary; the existing worker retains all current guards."""
    vertical, owner = components.vertical, components.publication_owner
    attempt_id = approved_attempt_id(attempt_id, trial_id=trial_id)
    async with vertical._sessions() as session:
        if attempt_id is not None:
            rows = (await session.execute(text(
                "SELECT r.task_id,r.run_id,r.ai_user_id,r.session_id,r.status,r.phase,"
                "r.cancel_requested,r.worker_deadline,r.worker_epoch,r.effect,r.verification,"
                "r.publication_digest,t.client_request_id FROM browser_runs r"
                " JOIN tasks t ON t.task_id=r.task_id AND t.tenant_id=r.tenant_id"
                " WHERE r.tenant_id=:tenant AND r.task_id=:task AND r.run_id=:run LIMIT 2"
            ), {"tenant": owner.tenant_id, "task": expected.task_id,
                "run": expected.run_id})).mappings().all()
            if len(rows) != 1:
                raise ValueError("browser_trial_receipt_invalid")
            row = rows[0]
            if (row["client_request_id"] != trial_request_id(trial_id, attempt_id=attempt_id)
                    or bytes(row["publication_digest"]) != bytes.fromhex(
                        trial_publication_digest(trial_id, attempt_id=attempt_id))
                    or row["effect"] != "not_sent" or row["verification"] is not None
                    or row["worker_epoch"] != 0):
                raise ValueError("browser_trial_receipt_invalid")
        elif trial_id != ORIGINAL_TRIAL:
            rows = (await session.execute(text(
                "SELECT r.task_id,r.run_id,r.ai_user_id,r.session_id,r.status,r.phase,"
                "r.cancel_requested,r.worker_deadline,r.worker_epoch,r.cleanup,r.effect,"
                "r.verification,r.error_code,r.dispatch_failure_code,r.publication_digest,"
                "t.client_request_id FROM browser_runs r JOIN tasks t ON t.task_id=r.task_id"
                " AND t.tenant_id=r.tenant_id WHERE r.tenant_id=:tenant LIMIT :maximum"
            ), {"tenant": owner.tenant_id,
                "maximum": (5 if trial_id == OBSERVE_TRIAL
                            else 4 if trial_id == VISIBLE_TRIAL else 3)})).mappings().all()
            history = [row for row in rows if row["task_id"] == LEGACY_TASK_ID
                       and row["run_id"] == LEGACY_RUN_ID]
            current = [row for row in rows if row["task_id"] == expected.task_id
                       and row["run_id"] == expected.run_id]
            diagnostic = [row for row in rows if row["task_id"] == DIAGNOSTIC_TASK_ID
                          and row["run_id"] == DIAGNOSTIC_RUN_ID]
            if (len(rows) != (4 if trial_id == OBSERVE_TRIAL
                                     else 3 if trial_id == VISIBLE_TRIAL else 2)
                    or len(history) != 1 or len(current) != 1
                    or expected.task_id == LEGACY_TASK_ID or expected.run_id == LEGACY_RUN_ID):
                raise ValueError("browser_trial_history_invalid")
            require_legacy_terminal(history[0], owner)
            if trial_id in {VISIBLE_TRIAL, OBSERVE_TRIAL}:
                if (len(diagnostic) != 1 or expected.task_id == DIAGNOSTIC_TASK_ID
                        or expected.run_id == DIAGNOSTIC_RUN_ID):
                    raise ValueError("browser_trial_history_invalid")
                require_diagnostic_terminal(diagnostic[0], owner)
            if trial_id == OBSERVE_TRIAL:
                visible = [item for item in rows if item["task_id"] == VISIBLE_TASK_ID
                           and item["run_id"] == VISIBLE_RUN_ID]
                if (len(visible) != 1 or expected.task_id == VISIBLE_TASK_ID
                        or expected.run_id == VISIBLE_RUN_ID):
                    raise ValueError("browser_trial_history_invalid")
                require_visible_terminal(visible[0], owner)
            row = current[0]
            if row["client_request_id"] != trial_request_id(trial_id) or bytes(
                row["publication_digest"]
            ) != bytes.fromhex(trial_publication_digest(trial_id)):
                raise ValueError("browser_trial_receipt_invalid")
        else:
            rows = (await session.execute(text(
                "SELECT task_id,run_id,ai_user_id,session_id,status,phase,cancel_requested,"
                "worker_deadline FROM browser_runs WHERE tenant_id=:tenant LIMIT 2"
            ), {"tenant": owner.tenant_id})).mappings().all()
            if len(rows) != 1:
                raise ValueError("browser_trial_single_queued_run_required")
            row = rows[0]
    if (row["task_id"] != expected.task_id or row["run_id"] != expected.run_id
            or row["ai_user_id"] != owner.user_id or row["session_id"] != owner.session_id
            or row["status"] != "running" or row["phase"] != "queued"
            or row["cancel_requested"] or row["worker_deadline"] is not None):
        raise ValueError("browser_trial_single_queued_run_required")
    if trial_id != ORIGINAL_TRIAL:
        worker_receipt: dict[str, Any] = {"worker_passes": 1, "trial_id": trial_id}
        receipt_options: dict[str, Any] = {"trial_id": trial_id}
        if attempt_id is not None:
            worker_receipt.update(trial_reference(trial_id, attempt_id=attempt_id))
            worker_receipt.update(task_id=expected.task_id, run_id=expected.run_id)
            receipt_options["attempt_id"] = attempt_id
        create_trial_file("trial.worker.json", worker_receipt, **receipt_options)
    else:
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
    if trial_id == OBSERVE_TRIAL:
        elapsed_ms = vertical.execution._consume_observe_only_receipt(result)
        observed = (elapsed_ms is not None and result.status == "failed"
                    and result.phase is None and result.effect == "not_sent"
                    and result.verification is None
                    and result.error_code == "browser_verification_failed"
                    and result.dispatch_failure_code == "cancelled"
                    and result.cleanup in {"released", "terminated"})
        observation_binding = (trial_reference(trial_id, attempt_id=attempt_id)
                               if attempt_id is not None else {})
        create_trial_file("trial.observation.json", {
            **observation_binding,
            "trial_id": trial_id, "task_id": result.task_id, "run_id": result.run_id,
            "observation_completed": observed, "observe_elapsed_ms": elapsed_ms,
            "business_completed": False, "verification": result.verification,
            "run_status": result.status, "effect": result.effect,
            "cleanup": result.cleanup, "jev_request_count": 0,
        }, **receipt_options)
        return observed
    return (result.status == "completed" and result.effect == "acknowledged"
            and result.verification == "verified" and result.error_code is None
            and result.cleanup in {"released", "terminated"})


async def _run(approved_budget_usd: str | None, *, private_stdin: bool = False,
               trial_id: str = ORIGINAL_TRIAL, observe_only: bool = False,
               attempt_id: str | None = None) -> bool:
    attempt_id = approved_attempt_id(attempt_id, trial_id=trial_id)
    if trial_id != ORIGINAL_TRIAL and not private_stdin:
        raise ValueError("browser_trial_arguments_invalid")
    trial_options: dict[str, Any] = {"trial_id": trial_id} if trial_id != ORIGINAL_TRIAL else {}
    if attempt_id is not None:
        trial_options["attempt_id"] = attempt_id
    if observe_only != (trial_id == OBSERVE_TRIAL):
        raise ValueError("browser_trial_arguments_invalid")
    if observe_only and approved_budget_usd is not None:
        raise ValueError("browser_trial_arguments_invalid")
    budget = None if observe_only else SingleJevAttempt(approved_budget_usd, **trial_options)

    def reject_http() -> None:
        raise ValueError("browser_observe_only_http_forbidden")
    if trial_id != ORIGINAL_TRIAL:
        task_id, run_id = _receipt(trial_id, attempt_id=attempt_id)
        reference = {"task_id": task_id, "run_id": run_id}
    else:
        reference = read_trial_file("trial.run.json")
    if (set(reference) != {"task_id", "run_id"} or any(
        not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value) is None
        for value in reference.values()
    )):
        raise ValueError("browser_trial_receipt_invalid")
    expected = BrowserAcceptedView.model_validate({**reference, "state_revision": 0})
    if private_stdin:
        bundle, key = (read_private_operator_input() if trial_id == ORIGINAL_TRIAL
                       else read_private_operator_input(
                           trial_id=trial_id, attempt_id=attempt_id,
                       ))
    else:
        bundle = prompt_operator_bundle(encrypted_path=VAULT_DIRECTORY / OPERATOR_FILE)
        key = prompt_openrouter_key()
    try:
        async with open_synthetic_operator(
            bundle, jev_key=key, enabled=True, input_mode="structured",
            attempt_guard=reject_http if observe_only else budget.reserve,
            **trial_options,
        ) as components:
            success = await execute_once(components, expected, **trial_options)
        return success if observe_only else success and budget.request_count == 1
    finally:
        print(json.dumps({"jev_request_count": 0 if observe_only else budget.request_count,
                          "max_jev_requests": 0 if observe_only else 1,
                          "actual_cost_usd": 0 if observe_only else None,
                          "cost_status": "http_forbidden" if observe_only else "unknown"},
                         sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = _SilentParser(allow_abbrev=False, add_help=False)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--approved-budget-usd")
    parser.add_argument("--observe-only", action="store_true")
    parser.add_argument("--private-stdin", action="store_true")
    parser.add_argument(
        "--trial-id", choices=(ORIGINAL_TRIAL, DIAGNOSTIC_TRIAL, VISIBLE_TRIAL, OBSERVE_TRIAL),
        default=ORIGINAL_TRIAL,
    )
    parser.add_argument("--attempt-id")
    try:
        args = parser.parse_args(argv)
        if not args.enable:
            raise ValueError
        success = asyncio.run(_run(args.approved_budget_usd, private_stdin=args.private_stdin,
                                   trial_id=args.trial_id, observe_only=args.observe_only,
                                   attempt_id=args.attempt_id))
    except BaseException:
        print("browser_trial_worker_unavailable", file=sys.stderr)
        return 2
    print(("browser_trial_observation_completed" if args.observe_only
           else "browser_trial_verified") if success else "browser_trial_failed")
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
