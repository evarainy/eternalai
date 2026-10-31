"""Value-free receipt for the one explicit finite form capability."""
from typing import Annotated, Literal, Protocol

from pydantic import Field

from app.browser_skill.models import Contract
from app.ports.auth import Principal

DFormReadinessCode = Literal[
    "d_form_identity_missing", "d_form_identity_invalid", "d_form_key_reference_missing",
    "d_form_pool_missing", "d_form_pool_mismatch", "d_form_authorization_missing",
    "d_form_reference_missing", "d_form_binding_unavailable", "d_form_binding_unverified",
    "d_form_subject_proof_unavailable", "d_form_handoff_pending",
    "d_form_publication_pending", "d_form_review_pending", "d_form_authorization_denied",
    "d_form_resource_scope_denied", "d_form_subject_mismatch",
    "d_form_owned_deployment_missing", "d_form_login_contract_missing",
]
DFormManagementOperation = Literal[
    "confirm_handoff", "prepare", "validate", "request_review", "confirm_review", "activate",
    "close_owned_browser",
]


class DFormHandoffView(Contract):
    version_token: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    resource_version: int = Field(ge=1, strict=True)
    task_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")


class DFormManagementState(Contract):
    structure_valid: bool
    execution_ready: Literal[False] = False
    fixed_activation: bool = False
    # Current browser subject is checked only inside the authorized Run fences.
    gaps: tuple[DFormReadinessCode, ...]
    choices: tuple[Literal["installed_binding", "recorded_resource"], ...] = ()
    available_operations: tuple[DFormManagementOperation, ...] = ()
    publication: Literal["unavailable", "missing", "prepared", "active", "inactive"]
    task_id: str | None = None
    run_id: str | None = None
    request_id: str | None = None
    handoff_preview: DFormHandoffView | None = None


class DFormValidationInputs(Contract):
    category: str = Field(min_length=1, max_length=100, repr=False)
    position: str = Field(min_length=1, max_length=500, repr=False)
    description: str = Field(min_length=1, max_length=500, repr=False)


class DFormManagementCommand(Contract):
    operation: DFormManagementOperation
    selection: Literal["installed_binding", "recorded_resource"]
    task_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,96}$")
    run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,96}$")
    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,96}$")
    session_id: str | None = Field(
        default=None, repr=False,
        pattern=r"^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[1-8][a-fA-F0-9]{3}-[89abAB][a-fA-F0-9]{3}-[a-fA-F0-9]{12}$",
    )
    parameters: DFormValidationInputs | None = Field(default=None, repr=False)
    handoff_confirmation: DFormHandoffView | None = Field(default=None, repr=False)


DFormManagementErrorCode = DFormReadinessCode | Literal[
    "browser_publication_version_conflict", "browser_publication_not_found",
    "browser_publication_already_prepared", "browser_publication_head_changed",
    "browser_publication_revision_conflict",
]


class DFormManagementError(RuntimeError):
    def __init__(self, code: DFormManagementErrorCode) -> None:
        super().__init__("D form management unavailable")
        self.code = code


class DFormManagementPort(Protocol):
    async def state(self, principal: Principal) -> DFormManagementState: ...

    async def perform(
        self, principal: Principal, command: DFormManagementCommand,
    ) -> DFormManagementState: ...

FillStopCode = Literal[
    "OK", "CONFIG_FAILED", "SESSION_BUSY", "LOGIN_REQUIRED", "PAGE_CHANGED",
    "UNEXPECTED_DIALOG", "DOWNLOAD_BLOCKED", "EGRESS_BLOCKED", "SOURCE_URL_PARSE",
    "SOURCE_URL_CREDENTIALS", "SOURCE_URL_SCHEME", "SOURCE_URL_HOST", "SOURCE_URL_PORT",
    "SOURCE_ORIGIN_MISMATCH", "SOURCE_SET_CHANGED", "CONTROL_MISSING", "CONTROL_AMBIGUOUS",
    "CONTROL_CHANGED", "PAGE_STATE_MISMATCH", "ACTION_TIMEOUT", "OBSERVE_TIMEOUT",
    "CONNECT_TIMEOUT", "RESULT_MISMATCH", "REPORTER_MISSING", "REPORTER_AMBIGUOUS",
    "REPORTER_INVALID", "CANCELLED", "CLEANUP_FAILED", "REQUEST_GUARD_FAILED", "PREP_FAILED",
    "PAGE_CONTEXT_EVENT", "PAGE_MAIN_FRAME_NAV", "PAGE_CHILD_FRAME_NAV",
    "PAGE_TARGET_CLOSED", "PAGE_CONTEXT_SET_CHANGED", "PAGE_FRAME_SET_CHANGED",
    "WINDOW_PREP_FAILED",
    "WINDOW_LOOKUP_FAILED", "WINDOW_SET_FAILED", "WINDOW_VERIFY_FAILED",
    "AUTHORITY_LOST", "RECEIPT_INVALID", "EXECUTION_TIMEOUT",
    "FORM_SUBMIT_BLOCKED",
]


class FillStopChecks(Contract):
    category: bool | None = None
    position: bool | None = None
    description: bool | None = None


class FillStopReport(Contract):
    mode: Literal["FILL_STOP"] = "FILL_STOP"
    execution_status: Literal["filled_stopped", "failed", "cancelled", "unknown"]
    verification: Literal["verified", "mismatch", "incomplete", "unknown"]
    field_checks: FillStopChecks
    actions_attempted: Annotated[int, Field(ge=0, le=4)]
    actions_completed: Annotated[int, Field(ge=0, le=4)]
    submit_actions: Annotated[int, Field(ge=0)] = 0
    model_requests: Literal[0] = 0
    seconds: Annotated[float, Field(ge=0, le=7200, allow_inf_nan=False)]
    error_code: FillStopCode
    cleanup_confirmed: bool
