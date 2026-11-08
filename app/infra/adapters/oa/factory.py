"""Replay/live OA read adapter construction shared by the API and the worker.

Takes only ``OAReadSettings`` so the worker never loads ``ProductionSettings`` or the
production composition. The mock mode and its safety check stay in the API composition.
"""

from __future__ import annotations

from app.config import OAReadSettings
from app.infra.adapters.oa.adapter import OAReadAdapter
from app.infra.adapters.oa.provider import (
    LiveOAReadProvider,
    ReplayOAReadProvider,
    report_oa_structural_drift,
)
from app.infra.auth.secret_provider import CredentialStoreSecretProvider
from app.ports.adapter import AdapterPort
from app.ports.auth import CredentialStorePort
from app.ports.credential_binding import OASessionRenewalPort


def build_oa_read_provider_adapter(
    settings: OAReadSettings,
    *,
    credential_store: CredentialStorePort,
    session_renewal: OASessionRenewalPort | None = None,
) -> AdapterPort:
    """Build the replay or live OA read adapter without runtime fallback."""

    if settings.oa_read_adapter_mode == "replay":
        contract_pack_dir = settings.oa_read_contract_pack_dir
        if contract_pack_dir is None or not contract_pack_dir.is_dir():
            raise RuntimeError("OA_READ_CONTRACT_PACK_DIR must be an existing directory")
        return OAReadAdapter(ReplayOAReadProvider(contract_pack_dir))
    if settings.oa_read_adapter_mode == "live":
        pending_contract_pack_dir = settings.oa_pending_workflows_contract_pack_dir
        if pending_contract_pack_dir is None or not pending_contract_pack_dir.is_dir():
            raise RuntimeError(
                "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR must be an existing directory"
            )
        system_message_contract_pack_dir = settings.oa_system_messages_contract_pack_dir
        if (
            system_message_contract_pack_dir is None
            or not system_message_contract_pack_dir.is_dir()
        ):
            raise RuntimeError("OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR must be an existing directory")
        message_center_path = settings.oa_message_center_path
        if message_center_path is None:
            raise RuntimeError("OA_MESSAGE_CENTER_PATH is required for live mode")
        pending_split_path = settings.oa_pending_workflows_split_page_key_path
        pending_counts_path = settings.oa_pending_workflows_counts_path
        pending_datas_path = settings.oa_pending_workflows_datas_path
        pending_actiontype = settings.oa_pending_workflows_actiontype
        pending_hide_no_data_tab = settings.oa_pending_workflows_hide_no_data_tab
        pending_method = settings.oa_pending_workflows_method
        pending_offical_type = settings.oa_pending_workflows_offical_type
        pending_view_scope = settings.oa_pending_workflows_view_scope
        pending_sort_params = settings.oa_pending_workflows_sort_params
        system_category_id = settings.oa_system_messages_category_id
        system_bizstate = settings.oa_system_messages_bizstate
        system_select_state = settings.oa_system_messages_select_state
        if any(
            value is None
            for value in (
                pending_split_path,
                pending_counts_path,
                pending_datas_path,
                pending_actiontype,
                pending_hide_no_data_tab,
                pending_method,
                pending_offical_type,
                pending_view_scope,
                pending_sort_params,
                system_category_id,
                system_bizstate,
                system_select_state,
            )
        ):
            raise RuntimeError("OA capability parameters are required for live mode")
        assert pending_split_path is not None
        assert pending_counts_path is not None
        assert pending_datas_path is not None
        assert pending_actiontype is not None
        assert pending_hide_no_data_tab is not None
        assert pending_method is not None
        assert pending_offical_type is not None
        assert pending_view_scope is not None
        assert pending_sort_params is not None
        assert system_category_id is not None
        assert system_bizstate is not None
        assert system_select_state is not None
        return OAReadAdapter(
            LiveOAReadProvider(
                base_url=settings.oa_base_url,
                message_center_endpoint_path=message_center_path,
                pending_workflows_split_page_key_path=pending_split_path,
                pending_workflows_counts_path=pending_counts_path,
                pending_workflows_datas_path=pending_datas_path,
                pending_workflows_actiontype=pending_actiontype,
                pending_workflows_hide_no_data_tab=pending_hide_no_data_tab,
                pending_workflows_method=pending_method,
                pending_workflows_offical_type=pending_offical_type,
                pending_workflows_view_scope=pending_view_scope,
                pending_workflows_sort_params=pending_sort_params,
                system_messages_category_id=system_category_id,
                system_messages_bizstate=system_bizstate,
                system_messages_select_state=system_select_state,
                timeout_seconds=settings.oa_timeout_seconds,
                pending_workflows_contract_pack_dir=pending_contract_pack_dir,
                system_messages_contract_pack_dir=(system_message_contract_pack_dir),
                drift_reporter=report_oa_structural_drift,
                page_size=settings.oa_message_center_page_size,
            ),
            secret_provider=CredentialStoreSecretProvider(
                credential_store=credential_store, tenant_id=settings.source_tenant_id
            ),
            session_renewal=session_renewal,
        )
    raise RuntimeError("OA_READ_ADAPTER_MODE is invalid")


__all__ = ("build_oa_read_provider_adapter",)
