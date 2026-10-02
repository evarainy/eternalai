import asyncio

from app.browser_skill.models import DecisionCallContext, DecisionRequest, DecisionResult
from app.ports.browser import DecisionProvider
from tests.browser_skill.factories import context, request


class AbstainingDecision:
    async def decide(self, req: DecisionRequest, ctx: DecisionCallContext) -> DecisionResult:
        return DecisionResult(request_id=req.request_id, scope=req.scope, status="abstained")


def test_decision_port_substitution_preserves_nonselection() -> None:
    provider: DecisionProvider = AbstainingDecision()
    result = asyncio.run(provider.decide(request(), context()))
    assert result.status == "abstained" and result.selected is None and result.error is None
