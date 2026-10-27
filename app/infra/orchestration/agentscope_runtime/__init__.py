"""AgentScope M1 runtime (selected by composition when RUNTIME_ENGINE=agentscope).

Imports are lazy: the executor module imports this package's ledger, so the
package itself must not import the executor or runtime at module level.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infra.orchestration.agentscope_read_tools import AgentScopeRuntimeDependencies
    from app.ports.runtime import RuntimePort


def build_agentscope_runtime(deps: AgentScopeRuntimeDependencies) -> RuntimePort:
    """Build the M1 runtime; ``deps.model_factory`` is the model seam (Golden, eval)."""

    from app.infra.orchestration.agentscope_runtime.runtime import AgentScopeRuntime

    return AgentScopeRuntime(deps)


__all__ = ("build_agentscope_runtime",)
