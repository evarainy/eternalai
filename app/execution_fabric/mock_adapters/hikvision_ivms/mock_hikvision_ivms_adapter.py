"""Mock Hikvision iVMS adapter for Phase 0 execution-fabric contract tests."""

from __future__ import annotations

from typing import Any

from app.ports.adapter import MOCK_ERROR_MODE_TO_ERROR_CODE, AdapterResult


class MockHikvisionIVMSAdapter:
    """Deterministic iVMS adapter returning AdapterResult without upstream I/O."""

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        error_mode = execution_context.get("mock_error_mode")
        if error_mode is not None:
            return AdapterResult(
                status="error",
                data=None,
                error_code=MOCK_ERROR_MODE_TO_ERROR_CODE.get(error_mode),
            )

        device_domain_id = str(arguments.get("device_domain_id", "mock-domain-001"))
        cid = capability_id.lower()

        if "alarm_summary" in cid:
            return AdapterResult(
                status="success",
                data={
                    "device_domain_id": device_domain_id,
                    "total_alarms": 0,
                    "active_alarms": 0,
                    "summary_generated_at": "2026-01-01T00:00:00Z",
                },
            )

        return AdapterResult(
            status="success",
            data={
                "device_domain_id": device_domain_id,
                "device_id": str(arguments.get("device_id", "device-mock-001")),
                "online": bool(arguments.get("mock_online", True)),
                "last_seen_at": str(
                    arguments.get("mock_last_seen_at", "2026-01-01T00:00:00Z")
                ),
                "video_frame_included": False,
            },
        )
